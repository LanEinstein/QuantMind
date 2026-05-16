#!/usr/bin/env python
"""H-002 — Operator CLI for the ``audit_events`` trail.

Usage:

    # Tail the JSONL backup (always available, no Mongo required)
    python scripts/query_audit.py --jsonl logs/audit.jsonl --limit 20

    # Filter by event type + since/until (ISO8601)
    python scripts/query_audit.py \\
        --event-type execution_report_submitted \\
        --since 2026-05-15T00:00:00Z \\
        --until 2026-05-16T23:59:59Z

    # Hit Mongo directly when MONGODB_URI is set
    MONGODB_URI=mongodb://localhost:27017 \\
        python scripts/query_audit.py --source mongo

The CLI mirrors the filters exposed by ``GET /api/audit/events`` so
operators can reproduce a Front-End drawer query from the shell. By
default the CLI reads from JSONL (no Mongo runtime dependency); the
``--source mongo`` flag opts into the durable store.

Red lines:

* CLI uses ``actor=cli`` when writing audit (it does NOT write here —
  read-only).
* Sensitive payload values are NEVER printed in full; payload keys are
  printed but each value is truncated at 200 characters so a stray
  multi-page LLM dump cannot wreck the operator terminal.
* JSON mode (``--json``) emits the raw envelope used by Phase B-finale
  Audit page tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.audit.models import AuditActor, AuditEvent, AuditEventType, AuditOutcome
from backend.audit.store import read_jsonl

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 5_000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="query_audit",
        description=(
            "Query QuantMind audit_events. Defaults to the JSONL backup; "
            "--source mongo opts into the durable store."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["jsonl", "mongo"],
        default="jsonl",
        help="Backend to query (default: jsonl).",
    )
    parser.add_argument(
        "--jsonl",
        default="logs/audit.jsonl",
        help="JSONL path (used when --source=jsonl).",
    )
    parser.add_argument(
        "--mongodb-uri",
        default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"),
        help="MongoDB connection string (used when --source=mongo).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("MONGODB_DATABASE", "quantmind"),
    )
    parser.add_argument(
        "--collection",
        default="audit_events",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO8601 lower bound, inclusive.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="ISO8601 upper bound, inclusive.",
    )
    parser.add_argument(
        "--event-type",
        default=None,
        help=f"One of {sorted(t.value for t in AuditEventType)}",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help=f"One of {sorted(a.value for a in AuditActor)}",
    )
    parser.add_argument(
        "--outcome",
        default=None,
        help=f"One of {sorted(o.value for o in AuditOutcome)}",
    )
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--resource-type", default=None)
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        help=f"Cap on rows returned (max {_MAX_LIMIT}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON envelope (default: human-readable table).",
    )
    return parser.parse_args(argv)


def _validate_enum(
    raw: str | None, enum_cls: type, field_name: str
) -> Any | None:
    if raw is None:
        return None
    try:
        return enum_cls(raw)
    except ValueError:
        valid = sorted(m.value for m in enum_cls)
        raise SystemExit(
            f"invalid --{field_name.replace('_', '-')}: {raw!r}; allowed={valid}"
        )


def _parse_iso(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"invalid iso8601 timestamp: {raw!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _matches(
    event: AuditEvent,
    *,
    since: datetime | None,
    until: datetime | None,
    event_type: AuditEventType | None,
    actor: AuditActor | None,
    outcome: AuditOutcome | None,
    correlation_id: str | None,
    resource_type: str | None,
) -> bool:
    if since is not None and event.timestamp < since:
        return False
    if until is not None and event.timestamp > until:
        return False
    if event_type is not None and event.event_type is not event_type:
        return False
    if actor is not None and event.actor is not actor:
        return False
    if outcome is not None and event.outcome is not outcome:
        return False
    if correlation_id is not None and event.correlation_id != correlation_id:
        return False
    if resource_type is not None and event.resource_type != resource_type:
        return False
    return True


def _hydrate(doc: dict[str, Any]) -> AuditEvent | None:
    """Reconstruct an :class:`AuditEvent` from a Mongo document (loose mode).

    Motor / PyMongo decode BSON Date values as naive UTC datetimes. The
    table + JSON formatters later call ``astimezone(UTC)`` which would
    treat naive values as local time on non-UTC hosts and shift rows
    (codex cycle 1 P2). Pin tz here so the CLI matches the JSONL path.
    """
    payload = dict(doc)
    payload.pop("_id", None)
    ts = payload.get("timestamp")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        payload["timestamp"] = ts.replace(tzinfo=UTC)
    try:
        return AuditEvent.model_validate(payload, strict=False)
    except Exception as exc:  # noqa: BLE001
        eid = doc.get("event_id")
        print(
            f"warning: skipping malformed doc event_id={eid!r} error={exc}",
            file=sys.stderr,
        )
        return None


async def _query_mongo(
    *,
    uri: str,
    database: str,
    collection: str,
    since: datetime | None,
    until: datetime | None,
    event_type: AuditEventType | None,
    actor: AuditActor | None,
    outcome: AuditOutcome | None,
    correlation_id: str | None,
    resource_type: str | None,
    limit: int,
) -> list[AuditEvent]:
    import motor.motor_asyncio as motor  # local import: optional dep at CLI time
    from pymongo import DESCENDING

    client = motor.AsyncIOMotorClient(uri)
    try:
        coll = client[database][collection]
        query: dict[str, Any] = {}
        if since is not None or until is not None:
            ts_filter: dict[str, datetime] = {}
            if since is not None:
                ts_filter["$gte"] = since
            if until is not None:
                ts_filter["$lte"] = until
            query["timestamp"] = ts_filter
        if event_type is not None:
            query["event_type"] = event_type.value
        if actor is not None:
            query["actor"] = actor.value
        if outcome is not None:
            query["outcome"] = outcome.value
        if correlation_id is not None:
            query["correlation_id"] = correlation_id
        if resource_type is not None:
            query["resource_type"] = resource_type

        cursor = coll.find(query).sort("timestamp", DESCENDING).limit(limit)
        out: list[AuditEvent] = []
        async for doc in cursor:
            hydrated = _hydrate(doc)
            if hydrated is not None:
                out.append(hydrated)
        return out
    finally:
        client.close()


def _query_jsonl(
    *,
    path: Path,
    since: datetime | None,
    until: datetime | None,
    event_type: AuditEventType | None,
    actor: AuditActor | None,
    outcome: AuditOutcome | None,
    correlation_id: str | None,
    resource_type: str | None,
    limit: int,
) -> list[AuditEvent]:
    raw = read_jsonl(path)
    filtered = [
        e
        for e in raw
        if _matches(
            e,
            since=since,
            until=until,
            event_type=event_type,
            actor=actor,
            outcome=outcome,
            correlation_id=correlation_id,
            resource_type=resource_type,
        )
    ]
    filtered.sort(key=lambda e: e.timestamp, reverse=True)
    return filtered[:limit]


def _truncate(value: Any, *, max_len: int = 200) -> str:
    text = repr(value) if not isinstance(value, str) else value
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _format_payload(payload: dict[str, Any]) -> str:
    if not payload:
        return "-"
    parts = [f"{k}={_truncate(v)}" for k, v in payload.items()]
    return " ".join(parts)


def _format_table(events: list[AuditEvent]) -> str:
    if not events:
        return "(no rows)"
    lines: list[str] = []
    header = (
        f"{'timestamp':<26}  {'event_type':<40}  {'actor':<14}  "
        f"{'outcome':<9}  payload"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for ev in events:
        lines.append(
            f"{ev.timestamp.astimezone(UTC).isoformat():<26}  "
            f"{ev.event_type.value:<40}  "
            f"{ev.actor.value:<14}  "
            f"{ev.outcome.value:<9}  "
            f"{_format_payload(ev.payload)}"
        )
    return "\n".join(lines)


def _format_json(events: list[AuditEvent], *, source: str, limit: int) -> str:
    envelope = {
        "source": source,
        "count": len(events),
        "limit": limit,
        "events": [
            {
                "event_id": str(e.event_id),
                "timestamp": e.timestamp.astimezone(UTC).isoformat(),
                "event_type": e.event_type.value,
                "actor": e.actor.value,
                "actor_detail": e.actor_detail,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "payload": e.payload,
                "outcome": e.outcome.value,
                "correlation_id": e.correlation_id,
                "reason_namespace": e.reason_namespace,
            }
            for e in events
        ],
    }
    return json.dumps(envelope, indent=2, ensure_ascii=False)


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.limit < 1 or args.limit > _MAX_LIMIT:
        raise SystemExit(f"--limit must be in [1, {_MAX_LIMIT}]")

    since_dt = _parse_iso(args.since)
    until_dt = _parse_iso(args.until)
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise SystemExit("--since must be <= --until")

    event_type = _validate_enum(args.event_type, AuditEventType, "event_type")
    actor = _validate_enum(args.actor, AuditActor, "actor")
    outcome = _validate_enum(args.outcome, AuditOutcome, "outcome")

    common = {
        "since": since_dt,
        "until": until_dt,
        "event_type": event_type,
        "actor": actor,
        "outcome": outcome,
        "correlation_id": args.correlation_id,
        "resource_type": args.resource_type,
        "limit": args.limit,
    }

    if args.source == "mongo":
        events = await _query_mongo(
            uri=args.mongodb_uri,
            database=args.database,
            collection=args.collection,
            **common,
        )
        source = "mongo"
    else:
        events = _query_jsonl(path=Path(args.jsonl), **common)
        source = "jsonl"

    if args.json:
        print(_format_json(events, source=source, limit=args.limit))
    else:
        print(_format_table(events))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":  # pragma: no cover — module is exercised via tests
    raise SystemExit(main())
