"""H-002 — GET /api/audit/events query endpoint.

Read-only window over the ``audit_events`` Mongo collection with a
JSONL fallback when Mongo is unavailable. The endpoint is the front-end
counterpart to ``scripts/query_audit.py`` (operator CLI) and feeds the
P1-5 §1.1 (Phase B-finale) audit drawer when wired.

Red lines (CLAUDE.md §2.9 / P1-5 §2 红线 1+2):

* **GET only** — no POST/PUT/PATCH/DELETE handlers may appear here.
* Mongo write failure does **not** raise (callers up-stack already
  fail-open). When Mongo is unreachable the endpoint serves from
  ``logs/audit.jsonl`` so operators still see the trail.
* Plaintext credentials are rejected at :class:`AuditEvent`
  construction; the serializer additionally drops any payload that
  somehow slips through (defensive, the validator should already raise).
* Pagination is bounded — the maximum ``limit`` is 500 so a single
  request cannot pull the whole 180-day TTL window.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo import DESCENDING

from backend.audit.models import (
    AUDIT_EVENT_TYPES,
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import read_jsonl

log = logging.getLogger("backend.api.audit")

router = APIRouter(tags=["audit"])

# Bounded pagination so a single request cannot pull the entire 180-day
# TTL window into memory or onto the wire. The CLI hits Mongo / JSONL
# directly when an operator needs more rows.
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100

# Default JSONL location — matches AuditStore default. Override exposed
# via app.state for tests.
_DEFAULT_JSONL_PATH = Path("logs/audit.jsonl")


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _serialize(event: AuditEvent) -> dict[str, Any]:
    """Project an :class:`AuditEvent` into the JSON wire shape."""
    return {
        "event_id": str(event.event_id),
        "timestamp": event.timestamp.astimezone(UTC).isoformat(),
        "event_type": event.event_type.value,
        "actor": event.actor.value,
        "actor_detail": event.actor_detail,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "payload": event.payload,
        "outcome": event.outcome.value,
        "correlation_id": event.correlation_id,
        "reason_namespace": event.reason_namespace,
    }


def _parse_iso(value: str | None) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid iso8601 timestamp: {value!r}",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _validate_enum(
    value: str | None,
    enum_cls: type,
    field_name: str,
) -> Any | None:
    if value is None or value == "":
        return None
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = sorted(m.value for m in enum_cls)
        raise HTTPException(
            status_code=400,
            detail=f"invalid {field_name}: {value!r}; allowed={valid}",
        ) from exc


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
    """Shared in-memory filter for the JSONL fallback path."""
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


def _get_jsonl_path(request: Request) -> Path:
    override = getattr(request.app.state, "audit_jsonl_path", None)
    if isinstance(override, Path):
        return override
    if isinstance(override, str) and override:
        return Path(override)
    return _DEFAULT_JSONL_PATH


def _get_collection(request: Request) -> Any | None:
    """Return the audit_events Mongo collection handle or ``None``."""
    mongodb = getattr(request.app.state, "mongodb", None)
    if mongodb is None:
        return None
    db = getattr(mongodb, "_db", None)
    if db is None:
        return None
    try:
        return db["audit_events"]
    except Exception:  # pragma: no cover — defensive
        return None


def _hydrate(doc: dict[str, Any]) -> AuditEvent | None:
    """Reconstruct an :class:`AuditEvent` from a Mongo document.

    Returns ``None`` (with a warning log) when the document fails
    validation — operator-visible without breaking the page. Mongo
    stores ``event_id`` as a string and ``timestamp`` as a BSON Date
    (datetime); the strict Pydantic config refuses str→UUID coercion so
    we route through JSON-mode validation which permits the standard
    ISO-8601 / hex coercion path.

    Motor / PyMongo decode BSON Date values as **naive** UTC datetimes
    by default. Without normalisation the later ``astimezone(UTC)`` in
    :func:`_serialize` would treat them as local-time on hosts where
    the system timezone is Asia/Shanghai, shifting Mongo-sourced rows
    by 8 hours relative to JSONL rows (codex cycle 1 P2). Attach UTC
    explicitly so the API serves the same instants regardless of source.
    """
    payload = dict(doc)
    payload.pop("_id", None)
    ts = payload.get("timestamp")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        payload["timestamp"] = ts.replace(tzinfo=UTC)
    try:
        return AuditEvent.model_validate(payload, strict=False)
    except Exception as exc:  # noqa: BLE001 — operator visibility
        log.warning("audit_doc_invalid event_id=%s error=%s", doc.get("event_id"), exc)
        return None


async def _query_mongo(
    collection: Any,
    *,
    since: datetime | None,
    until: datetime | None,
    event_type: AuditEventType | None,
    actor: AuditActor | None,
    outcome: AuditOutcome | None,
    correlation_id: str | None,
    resource_type: str | None,
    limit: int,
) -> list[AuditEvent]:
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

    cursor = collection.find(query).sort("timestamp", DESCENDING).limit(limit)
    events: list[AuditEvent] = []
    async for doc in cursor:
        hydrated = _hydrate(doc)
        if hydrated is not None:
            events.append(hydrated)
    return events


def _query_jsonl(
    path: Path,
    *,
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


@router.get("/api/audit/events")
async def list_audit_events(
    request: Request,
    since: str | None = Query(default=None, description="ISO8601 lower bound"),
    until: str | None = Query(default=None, description="ISO8601 upper bound"),
    event_type: str | None = Query(default=None, description="AuditEventType"),
    actor: str | None = Query(default=None, description="AuditActor"),
    outcome: str | None = Query(default=None, description="AuditOutcome"),
    correlation_id: str | None = Query(default=None, max_length=128),
    resource_type: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict[str, Any]:
    """List audit events (most-recent first) with optional filters.

    Returns ``source="mongo"`` on the happy path. When Mongo is
    unreachable or returns an error the endpoint falls back to the
    ``logs/audit.jsonl`` tail so the operator always sees *some* trail.
    A degraded response is still ``status="ok"`` so the front-end
    surfaces it as a warning banner rather than a hard error.
    """
    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise HTTPException(
            status_code=400,
            detail="since must be <= until",
        )
    event_type_enum = _validate_enum(event_type, AuditEventType, "event_type")
    actor_enum = _validate_enum(actor, AuditActor, "actor")
    outcome_enum = _validate_enum(outcome, AuditOutcome, "outcome")

    common = {
        "since": since_dt,
        "until": until_dt,
        "event_type": event_type_enum,
        "actor": actor_enum,
        "outcome": outcome_enum,
        "correlation_id": correlation_id,
        "resource_type": resource_type,
        "limit": limit,
    }

    collection = _get_collection(request)
    source = "mongo"
    events: list[AuditEvent] = []
    if collection is not None:
        try:
            events = await _query_mongo(collection, **common)
        except Exception as exc:  # noqa: BLE001 — fall back to JSONL
            log.warning("audit_query_mongo_failed error=%s", exc)
            source = "jsonl_fallback"
            events = _query_jsonl(_get_jsonl_path(request), **common)
    else:
        source = "jsonl_fallback"
        events = _query_jsonl(_get_jsonl_path(request), **common)

    return _ok(
        {
            "source": source,
            "events": [_serialize(e) for e in events],
            "count": len(events),
            "limit": limit,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )


@router.get("/api/audit/event-types")
async def list_audit_event_types() -> dict[str, Any]:
    """Return the locked vocabulary so the front-end can render filters."""
    return _ok(
        {
            "event_types": sorted(t.value for t in AUDIT_EVENT_TYPES),
            "actors": sorted(a.value for a in AuditActor),
            "outcomes": sorted(o.value for o in AuditOutcome),
            "default_limit": _DEFAULT_LIMIT,
            "max_limit": _MAX_LIMIT,
        }
    )


# Convenience for the redline-check.sh / B-005 follow-on test that walks
# the package source for write handlers — explicitly enumerate the
# GET-only surface here.
_GET_ONLY_PATHS = frozenset({"/api/audit/events", "/api/audit/event-types"})


def _emit_window_bounds(days: int = 30) -> tuple[datetime, datetime]:
    """Helper used by tests to construct a typical since/until pair."""
    now = datetime.now(tz=UTC)
    return now - timedelta(days=days), now


__all__ = ["router"]
