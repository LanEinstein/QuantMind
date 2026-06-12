"""Z-004 — backend/api/slot_rotation.py tests (≤5-slot rotation surface)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.slot_rotation import router as slot_rotation_router
from backend.slot_portfolio.rotation_intent import (
    RotationIntent,
    RotationIntentStore,
)


def _intent(
    incumbent: str = "600000",
    challenger: str = "000002",
    *,
    created: str = "20260612",
    expires: str = "20260615",
) -> RotationIntent:
    return RotationIntent(
        intent_id=f"ROT-{created}-{incumbent}-{challenger}",
        created_trade_date=created,
        expires_at_trade_date=expires,
        sell_instruction_id="QM-20260612-093500-000001-SELL-001",
        incumbent_code=incumbent,
        challenger_code=challenger,
        incumbent_score=0.31,
        challenger_score=0.88,
        incumbent_percentile=0.35,
        challenger_percentile=0.92,
        signal_id="LINE1-20260612-0935",
        config_hash="cfg-hash-1",
    )


class _StubRunner:
    """The endpoint only touches ``intent_store`` + ``max_total_positions``."""

    def __init__(self, store: object, *, cap: int = 5) -> None:
        self._store = store
        self.max_total_positions = cap

    @property
    def intent_store(self) -> Any:
        return self._store


def _build_app(runner: object | None) -> FastAPI:
    app = FastAPI()
    app.state.rotation_runner = runner
    app.include_router(slot_rotation_router)
    return app


async def _get(app: FastAPI, params: dict[str, Any] | None = None) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/slot-rotation", params=params or {})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


@pytest.mark.asyncio
async def test_unavailable_when_runner_unwired() -> None:
    data = await _get(_build_app(None))
    assert data["available"] is False
    assert data["open_intents"] == []
    assert data["max_total_positions"] is None


@pytest.mark.asyncio
async def test_unavailable_when_runner_lacks_intent_store() -> None:
    class _Broken:
        pass

    data = await _get(_build_app(_Broken()))
    assert data["available"] is False


@pytest.mark.asyncio
async def test_empty_wired_runner_available(tmp_path: Path) -> None:
    store = RotationIntentStore(tmp_path / "rotation_intents.jsonl")
    data = await _get(_build_app(_StubRunner(store)))
    assert data["available"] is True
    assert data["open_intents"] == []
    assert data["max_total_positions"] == 5
    assert data["underinvested_block_active"] is False


@pytest.mark.asyncio
async def test_serializes_open_intent(tmp_path: Path) -> None:
    store = RotationIntentStore(tmp_path / "rotation_intents.jsonl")
    store.record_proposed(_intent())
    data = await _get(_build_app(_StubRunner(store)))

    assert data["available"] is True
    assert data["open_intent_count"] == 1
    intent = data["open_intents"][0]
    assert intent["incumbent_code"] == "600000"
    assert intent["challenger_code"] == "000002"
    assert intent["challenger_percentile"] == 0.92
    assert intent["expires_at_trade_date"] == "20260615"
    # The PROPOSED event surfaces in the most-recent-first lifecycle list.
    assert data["recent_events"][0]["event_type"] == "proposed"


@pytest.mark.asyncio
async def test_resolved_intent_leaves_open_set_but_stays_in_events(
    tmp_path: Path,
) -> None:
    store = RotationIntentStore(tmp_path / "rotation_intents.jsonl")
    intent = _intent()
    store.record_proposed(intent)
    store.record_resolved(intent.intent_id, trade_date="20260613")
    data = await _get(_build_app(_StubRunner(store)))

    assert data["open_intent_count"] == 0
    # Most-recent-first: RESOLVED is the head, PROPOSED follows.
    kinds = [e["event_type"] for e in data["recent_events"]]
    assert kinds == ["resolved", "proposed"]
    # The terminal RESOLVED event keeps the sell→challenger legs folded from
    # the earlier PROPOSED intent (codex P2 — the store does not re-embed it).
    resolved = data["recent_events"][0]
    assert resolved["incumbent_code"] == "600000"
    assert resolved["challenger_code"] == "000002"


@pytest.mark.asyncio
async def test_event_limit_bounds_recent_events(tmp_path: Path) -> None:
    store = RotationIntentStore(tmp_path / "rotation_intents.jsonl")
    for i in range(5):
        store.record_proposed(
            _intent(incumbent=f"60000{i}", created=f"2026061{i}")
        )
    data = await _get(_build_app(_StubRunner(store)), params={"event_limit": 2})
    assert len(data["recent_events"]) == 2


@pytest.mark.asyncio
async def test_read_failure_fails_closed() -> None:
    class _Exploding:
        def open_intents(self) -> Any:
            raise RuntimeError("corrupt ledger")

    data = await _get(_build_app(_StubRunner(_Exploding())))
    assert data["available"] is False
    assert data["open_intents"] == []


def test_router_is_get_only() -> None:
    source = Path("backend/api/slot_rotation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(
                    deco.func, ast.Attribute
                ):
                    if deco.func.attr in write_verbs:
                        seen.append(node.name)
    assert seen == []


def test_module_imports_no_trading_stack() -> None:
    source = Path("backend/api/slot_rotation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"llm", "agents", "risk", "broker", "data", "mirofish"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in forbidden:
                bad.append(node.module)
    assert bad == []
