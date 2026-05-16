"""G-008 — read-only Feishu message history.

Surfaces ``FEISHU_MESSAGE_RECEIVED`` + ``FEISHU_MESSAGE_SENT`` audit
rows so the Phase B-finale page can render the recent inbound /
outbound traffic without parsing JSONL manually.

Read-only by design — the only way to send a message is through the
F-002 renderer + F-006 alerter or the F-005 reconciliation flow;
adding a write handler here is a P1-5 §2 红线 1 violation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pymongo import DESCENDING

from backend.audit.models import AuditEvent, AuditEventType
from backend.audit.store import read_jsonl

log = logging.getLogger("backend.api.feishu")

router = APIRouter(tags=["feishu"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50
_DEFAULT_JSONL_PATH = Path("logs/audit.jsonl")

FEISHU_EVENT_TYPES: frozenset[AuditEventType] = frozenset(
    {
        AuditEventType.FEISHU_MESSAGE_RECEIVED,
        AuditEventType.FEISHU_MESSAGE_SENT,
        AuditEventType.FEISHU_LONGCONN_CONNECTED,
        AuditEventType.FEISHU_LONGCONN_DISCONNECTED,
    }
)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_jsonl_path(request: Request) -> Path:
    override = getattr(request.app.state, "audit_jsonl_path", None)
    if isinstance(override, Path):
        return override
    if isinstance(override, str) and override:
        return Path(override)
    return _DEFAULT_JSONL_PATH


def _get_collection(request: Request) -> Any | None:
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
    payload = dict(doc)
    payload.pop("_id", None)
    ts = payload.get("timestamp")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        payload["timestamp"] = ts.replace(tzinfo=UTC)
    try:
        return AuditEvent.model_validate(payload, strict=False)
    except Exception as exc:  # noqa: BLE001 — operator visibility
        log.warning(
            "feishu_history_invalid_doc event_id=%s error=%s",
            doc.get("event_id"),
            exc,
        )
        return None


def _serialize(event: AuditEvent) -> dict[str, Any]:
    return {
        "event_id": str(event.event_id),
        "timestamp": event.timestamp.astimezone(UTC).isoformat(),
        "event_type": event.event_type.value,
        "actor": event.actor.value,
        "actor_detail": event.actor_detail,
        "outcome": event.outcome.value,
        "resource_id": event.resource_id,
        "correlation_id": event.correlation_id,
        "payload": event.payload,
    }


async def _query_mongo(
    collection: Any, *, limit: int
) -> list[AuditEvent]:
    query = {
        "event_type": {"$in": [t.value for t in FEISHU_EVENT_TYPES]},
    }
    cursor = collection.find(query).sort("timestamp", DESCENDING).limit(limit)
    out: list[AuditEvent] = []
    async for doc in cursor:
        hydrated = _hydrate(doc)
        if hydrated is not None:
            out.append(hydrated)
    return out


def _query_jsonl(path: Path, *, limit: int) -> list[AuditEvent]:
    raw = read_jsonl(path)
    filtered = [e for e in raw if e.event_type in FEISHU_EVENT_TYPES]
    filtered.sort(key=lambda e: e.timestamp, reverse=True)
    return filtered[:limit]


@router.get("/api/feishu/messages")
async def list_feishu_messages(
    request: Request,
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> dict[str, Any]:
    """Return the most recent Feishu inbound / outbound audit rows.

    Falls back to the JSONL tail when Mongo is unreachable so the page
    always shows *something* during a Mongo outage. The endpoint is
    GET-only — the locked write surface stays at 2 entries.
    """
    collection = _get_collection(request)
    source = "mongo"
    events: list[AuditEvent] = []
    if collection is not None:
        try:
            events = await _query_mongo(collection, limit=limit)
        except Exception as exc:  # noqa: BLE001 — fallback to JSONL
            log.warning("feishu_history_mongo_failed error=%s", exc)
            source = "jsonl_fallback"
            events = _query_jsonl(_get_jsonl_path(request), limit=limit)
    else:
        source = "jsonl_fallback"
        events = _query_jsonl(_get_jsonl_path(request), limit=limit)

    return _ok(
        {
            "source": source,
            "events": [_serialize(e) for e in events],
            "count": len(events),
            "limit": limit,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )


@router.get("/api/feishu/event-types")
async def list_feishu_event_types() -> dict[str, Any]:
    """Return the locked 4-type vocabulary surfaced by this endpoint."""
    return _ok({"event_types": sorted(t.value for t in FEISHU_EVENT_TYPES)})


# Convenience for tests + redline-check.sh AST scan.
_GET_ONLY_PATHS = frozenset(
    {"/api/feishu/messages", "/api/feishu/event-types"}
)

# Guard: any extension of FEISHU_EVENT_TYPES must use AuditEventType
# constants (no plain strings) so a typo cannot slip past
# audit-event-type validation.
for _ev in FEISHU_EVENT_TYPES:
    if not isinstance(_ev, AuditEventType):  # pragma: no cover — boot guard
        raise RuntimeError(
            f"FEISHU_EVENT_TYPES must hold AuditEventType members; got {_ev!r}"
        )


def _raise_when_limit_oob(limit: int) -> None:
    """Helper kept here so the test file can assert the bounds."""
    if limit < 1 or limit > _MAX_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be in [1, {_MAX_LIMIT}]; got {limit}",
        )


__all__ = ["router"]
