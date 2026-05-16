"""H-002 — GET /api/audit/events tests.

Coverage:
- Mongo happy path with filters
- Mongo failure → JSONL fallback
- Validation errors (bad iso8601, bad enum, since>until, limit out of range)
- /api/audit/event-types vocabulary endpoint
- GET-only invariant (no POST/PUT/PATCH/DELETE handler in module)
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.audit import router as audit_router
from backend.audit.models import (
    AUDIT_EVENT_TYPES,
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def _event(
    *,
    ts: datetime,
    event_type: AuditEventType = AuditEventType.EXECUTION_REPORT_SUBMITTED,
    actor: AuditActor = AuditActor.FEISHU_USER,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    correlation_id: str | None = None,
    resource_type: str = "execution_report",
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=ts,
        event_type=event_type,
        actor=actor,
        resource_type=resource_type,
        payload=payload or {"k": "v"},
        outcome=outcome,
        correlation_id=correlation_id,
    )


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, _key: str, _direction: int) -> _FakeCursor:
        return self

    def limit(self, n: int) -> _FakeCursor:
        return _FakeCursor(self._docs[:n])

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    def __init__(self, docs: list[AuditEvent], *, fail: bool = False) -> None:
        self._docs = [self._serialize(e) for e in docs]
        self._fail = fail
        self.last_query: dict[str, Any] | None = None

    @staticmethod
    def _serialize(e: AuditEvent) -> dict[str, Any]:
        return {
            "event_id": str(e.event_id),
            "timestamp": e.timestamp,
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

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        if self._fail:
            raise RuntimeError("mongo down")
        self.last_query = query
        # Apply filter manually so tests can verify the query shape too.
        docs = list(self._docs)
        if "event_type" in query:
            docs = [d for d in docs if d["event_type"] == query["event_type"]]
        if "actor" in query:
            docs = [d for d in docs if d["actor"] == query["actor"]]
        if "outcome" in query:
            docs = [d for d in docs if d["outcome"] == query["outcome"]]
        if "correlation_id" in query:
            docs = [
                d for d in docs if d["correlation_id"] == query["correlation_id"]
            ]
        if "resource_type" in query:
            docs = [
                d for d in docs if d["resource_type"] == query["resource_type"]
            ]
        if "timestamp" in query:
            ts_filter = query["timestamp"]
            since = ts_filter.get("$gte")
            until = ts_filter.get("$lte")
            if since is not None:
                docs = [d for d in docs if d["timestamp"] >= since]
            if until is not None:
                docs = [d for d in docs if d["timestamp"] <= until]
        docs.sort(key=lambda d: d["timestamp"], reverse=True)
        return _FakeCursor(docs)


class _FakeMongoDB:
    def __init__(self, collection: _FakeCollection) -> None:
        self._db = {"audit_events": collection}


def _build_app(
    *,
    mongodb: _FakeMongoDB | None,
    jsonl_path: Path | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.mongodb = mongodb
    app.state.audit_jsonl_path = jsonl_path or Path("/tmp/__audit_test_missing.jsonl")
    app.include_router(audit_router)
    return app


@pytest.mark.asyncio
async def test_list_audit_events_mongo_happy_path(now_utc: datetime) -> None:
    events = [
        _event(ts=now_utc - timedelta(minutes=10)),
        _event(
            ts=now_utc - timedelta(minutes=5),
            event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
        ),
        _event(ts=now_utc - timedelta(minutes=1)),
    ]
    collection = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit/events", params={"limit": 50})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["source"] == "mongo"
    assert body["data"]["count"] == 3
    # Most-recent first.
    assert body["data"]["events"][0]["event_type"] == "execution_report_submitted"
    assert (
        body["data"]["events"][1]["event_type"]
        == "reconciliation_ticket_decided"
    )


@pytest.mark.asyncio
async def test_list_audit_events_filters_event_type(now_utc: datetime) -> None:
    events = [
        _event(ts=now_utc - timedelta(minutes=1)),
        _event(
            ts=now_utc - timedelta(minutes=2),
            event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
        ),
    ]
    collection = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events",
            params={"event_type": "reconciliation_ticket_decided"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["count"] == 1
    assert body["data"]["events"][0]["event_type"] == "reconciliation_ticket_decided"
    assert collection.last_query == {"event_type": "reconciliation_ticket_decided"}


@pytest.mark.asyncio
async def test_list_audit_events_filters_actor(now_utc: datetime) -> None:
    events = [
        _event(ts=now_utc, actor=AuditActor.FEISHU_USER),
        _event(ts=now_utc, actor=AuditActor.SYSTEM),
    ]
    collection = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events", params={"actor": "system"}
        )

    assert resp.json()["data"]["count"] == 1
    assert resp.json()["data"]["events"][0]["actor"] == "system"


@pytest.mark.asyncio
async def test_list_audit_events_filters_correlation_id(
    now_utc: datetime,
) -> None:
    events = [
        _event(ts=now_utc, correlation_id="QM-corr-1"),
        _event(ts=now_utc, correlation_id="QM-corr-2"),
    ]
    collection = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events",
            params={"correlation_id": "QM-corr-2"},
        )

    assert resp.json()["data"]["count"] == 1
    assert resp.json()["data"]["events"][0]["correlation_id"] == "QM-corr-2"


@pytest.mark.asyncio
async def test_list_audit_events_filters_since_until(now_utc: datetime) -> None:
    base = now_utc.replace(hour=0)
    events = [
        _event(ts=base + timedelta(hours=1)),
        _event(ts=base + timedelta(hours=5)),
        _event(ts=base + timedelta(hours=10)),
    ]
    collection = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events",
            params={
                "since": (base + timedelta(hours=2)).isoformat(),
                "until": (base + timedelta(hours=8)).isoformat(),
            },
        )

    assert resp.json()["data"]["count"] == 1


@pytest.mark.asyncio
async def test_list_audit_events_mongo_failure_falls_back_to_jsonl(
    now_utc: datetime, tmp_path: Path
) -> None:
    # Mongo broken — fall back to JSONL.
    collection = _FakeCollection([], fail=True)
    jsonl_path = tmp_path / "audit.jsonl"
    ev = _event(ts=now_utc)
    jsonl_path.write_text(ev.model_dump_json() + "\n", encoding="utf-8")
    app = _build_app(mongodb=_FakeMongoDB(collection), jsonl_path=jsonl_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit/events")

    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["source"] == "jsonl_fallback"
    assert body["data"]["count"] == 1


@pytest.mark.asyncio
async def test_list_audit_events_no_mongo_uses_jsonl(
    now_utc: datetime, tmp_path: Path
) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    ev = _event(ts=now_utc, event_type=AuditEventType.MODE_SWITCH_INITIATED)
    jsonl_path.write_text(ev.model_dump_json() + "\n", encoding="utf-8")
    app = _build_app(mongodb=None, jsonl_path=jsonl_path)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit/events")

    body = resp.json()
    assert body["data"]["source"] == "jsonl_fallback"
    assert body["data"]["count"] == 1
    assert body["data"]["events"][0]["event_type"] == "mode_switch_initiated"


@pytest.mark.asyncio
async def test_invalid_iso8601_returns_400() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events", params={"since": "not-a-date"}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_event_type_returns_400() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events", params={"event_type": "does_not_exist"}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_actor_returns_400() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events", params={"actor": "robot"}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_since_after_until_returns_400() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/audit/events",
            params={
                "since": "2026-05-16T10:00:00Z",
                "until": "2026-05-16T09:00:00Z",
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_limit_bounds_enforced() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_low = await client.get("/api/audit/events", params={"limit": 0})
        resp_high = await client.get("/api/audit/events", params={"limit": 9999})
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


@pytest.mark.asyncio
async def test_event_types_vocab_endpoint() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit/event-types")
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["data"]["event_types"]) == len(AUDIT_EVENT_TYPES)
    assert "execution_report_submitted" in body["data"]["event_types"]
    assert body["data"]["actors"] == sorted(a.value for a in AuditActor)
    assert body["data"]["outcomes"] == sorted(o.value for o in AuditOutcome)


@pytest.mark.asyncio
async def test_naive_mongo_timestamp_normalised_to_utc(
    now_utc: datetime,
) -> None:
    """Codex cycle 1 P2 regression.

    Motor returns BSON Dates as naive UTC datetimes; the API must pin
    the tz so ``astimezone(UTC)`` does not interpret the value as local
    time on Asia/Shanghai hosts (would shift rows by 8h).
    """
    naive = now_utc.replace(tzinfo=None)
    events = [_event(ts=now_utc)]  # build a valid event for shape
    collection = _FakeCollection(events)
    # Mutate the serialized doc to use a naive timestamp like Motor would
    collection._docs[0]["timestamp"] = naive  # type: ignore[index]
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/audit/events")

    body = resp.json()
    serialized_ts = body["data"]["events"][0]["timestamp"]
    # Must serialize to the same UTC instant as the original aware datetime.
    parsed = datetime.fromisoformat(serialized_ts.replace("Z", "+00:00"))
    assert parsed.astimezone(UTC) == now_utc


def test_audit_router_is_get_only() -> None:
    """No write handlers in backend/api/audit.py (red line: GET only)."""
    source = Path("backend/api/audit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"post", "put", "patch", "delete"}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                # @router.post(...) → Call(Attribute(...))
                if isinstance(deco, ast.Call):
                    func = deco.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr in forbidden
                    ):
                        found.append(f"{node.name}:{func.attr}")
    assert not found, f"audit API must be GET-only; found {found}"
