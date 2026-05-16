"""G-008 — backend/api/feishu.py message-history tests."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.feishu import (
    FEISHU_EVENT_TYPES,
)
from backend.api.feishu import (
    router as feishu_router,
)
from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)


def _make_event(
    *,
    ts: datetime,
    event_type: AuditEventType = AuditEventType.FEISHU_MESSAGE_RECEIVED,
    actor: AuditActor = AuditActor.FEISHU_USER,
) -> AuditEvent:
    return AuditEvent(
        timestamp=ts,
        event_type=event_type,
        actor=actor,
        resource_type="feishu_message",
        payload={"message_id": "mid-1"},
        outcome=AuditOutcome.SUCCESS,
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
    def __init__(self, events: list[AuditEvent], *, fail: bool = False) -> None:
        self._docs = [self._serialize(e) for e in events]
        self._fail = fail

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
        allowed = set(query.get("event_type", {}).get("$in", []))
        docs = [d for d in self._docs if d["event_type"] in allowed]
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
    app.state.audit_jsonl_path = (
        jsonl_path or Path("/tmp/__missing_feishu_audit.jsonl")
    )
    app.include_router(feishu_router)
    return app


@pytest.mark.asyncio
async def test_list_happy_path_returns_feishu_events_only(
    tmp_path: Path,
) -> None:
    base = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    feishu = _make_event(ts=base, event_type=AuditEventType.FEISHU_MESSAGE_SENT)
    received = _make_event(
        ts=base - timedelta(minutes=5),
        event_type=AuditEventType.FEISHU_MESSAGE_RECEIVED,
    )
    # An unrelated audit row that must NOT appear in the response.
    unrelated = _make_event(
        ts=base - timedelta(minutes=10),
        event_type=AuditEventType.MODE_SWITCH_INITIATED,
        actor=AuditActor.SYSTEM,
    )

    collection = _FakeCollection([feishu, received, unrelated])
    app = _build_app(mongodb=_FakeMongoDB(collection))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/feishu/messages")

    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["source"] == "mongo"
    # 2 feishu events, not 3
    assert body["data"]["count"] == 2
    types = {row["event_type"] for row in body["data"]["events"]}
    assert types == {"feishu_message_sent", "feishu_message_received"}


@pytest.mark.asyncio
async def test_list_falls_back_to_jsonl_when_mongo_breaks(
    tmp_path: Path,
) -> None:
    collection = _FakeCollection([], fail=True)
    jsonl_path = tmp_path / "audit.jsonl"
    ev = _make_event(ts=datetime(2026, 5, 16, tzinfo=UTC))
    jsonl_path.write_text(ev.model_dump_json() + "\n", encoding="utf-8")
    app = _build_app(mongodb=_FakeMongoDB(collection), jsonl_path=jsonl_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/feishu/messages")
    body = resp.json()
    assert body["data"]["source"] == "jsonl_fallback"
    assert body["data"]["count"] == 1


@pytest.mark.asyncio
async def test_list_no_mongo_reads_jsonl_directly(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    ev = _make_event(ts=datetime(2026, 5, 16, tzinfo=UTC))
    jsonl_path.write_text(ev.model_dump_json() + "\n", encoding="utf-8")
    app = _build_app(mongodb=None, jsonl_path=jsonl_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/feishu/messages")
    assert resp.json()["data"]["source"] == "jsonl_fallback"


@pytest.mark.asyncio
async def test_limit_bounds_enforced() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        low = await client.get("/api/feishu/messages", params={"limit": 0})
        high = await client.get("/api/feishu/messages", params={"limit": 9999})
    assert low.status_code == 422
    assert high.status_code == 422


@pytest.mark.asyncio
async def test_event_types_vocab() -> None:
    app = _build_app(mongodb=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/feishu/event-types")
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["data"]["event_types"]) == {
        "feishu_message_received",
        "feishu_message_sent",
        "feishu_longconn_connected",
        "feishu_longconn_disconnected",
    }


def test_feishu_event_types_locked() -> None:
    assert FEISHU_EVENT_TYPES == frozenset(
        {
            AuditEventType.FEISHU_MESSAGE_RECEIVED,
            AuditEventType.FEISHU_MESSAGE_SENT,
            AuditEventType.FEISHU_LONGCONN_CONNECTED,
            AuditEventType.FEISHU_LONGCONN_DISCONNECTED,
        }
    )


def test_router_is_get_only() -> None:
    source = Path("backend/api/feishu.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    if deco.func.attr in write_verbs:
                        seen.append(node.name)
    assert seen == []
