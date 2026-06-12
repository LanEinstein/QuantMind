"""Z-003 — backend/api/position_theses.py tests (thesis tracking surface)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.position_theses import router as theses_router
from backend.models.position_thesis import (
    Comparator,
    InvalidationTemplate,
    PositionThesis,
    ThesisInvalidationCondition,
)
from backend.position_thesis.store import PositionThesisStore


def _thesis(code: str = "600519") -> PositionThesis:
    return PositionThesis(
        instruction_id="QM-20260612-093500-000001-BUY-001",
        signal_id="LINE1-20260612-0935",
        stock_code=code,
        stock_name="贵州茅台",
        created_at=datetime(2026, 6, 12, 9, 35, tzinfo=UTC),
        trade_date="2026-06-12",
        pillars=("龙头护城河", "盈利稳健", "估值合理"),
        invalidation_conditions=(
            ThesisInvalidationCondition(
                template=InvalidationTemplate.ANCHOR_DRAWDOWN,
                metric_name="price",
                comparator=Comparator.LT,
                threshold=1530.0,
                anchor=1700.0,
                feature_code_version="fc-v1",
            ),
        ),
        time_stop_trade_days=20,
        entry_price=1700.0,
        entry_score=0.82,
        snapshot_id="snap-1",
        feature_code_version="fc-v1",
    )


def _build_app(store: object | None) -> FastAPI:
    app = FastAPI()
    app.state.position_thesis_store = store
    app.include_router(theses_router)
    return app


async def _get(app: FastAPI) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/position-theses")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


@pytest.mark.asyncio
async def test_unavailable_when_store_unwired() -> None:
    data = await _get(_build_app(None))
    assert data["available"] is False
    assert data["theses"] == []
    assert data["thesis_count"] == 0
    assert "advisory" in data


@pytest.mark.asyncio
async def test_unavailable_when_store_lacks_method() -> None:
    class _Broken:
        pass

    data = await _get(_build_app(_Broken()))
    assert data["available"] is False


@pytest.mark.asyncio
async def test_empty_wired_store_available(tmp_path: Path) -> None:
    store = PositionThesisStore(tmp_path / "theses.jsonl")
    data = await _get(_build_app(store))
    assert data["available"] is True
    assert data["theses"] == []


@pytest.mark.asyncio
async def test_serializes_open_thesis(tmp_path: Path) -> None:
    store = PositionThesisStore(tmp_path / "theses.jsonl")
    store.open_thesis(_thesis())
    data = await _get(_build_app(store))

    assert data["available"] is True
    assert data["thesis_count"] == 1
    t = data["theses"][0]
    assert t["stock_code"] == "600519"
    assert t["pillars"] == ["龙头护城河", "盈利稳健", "估值合理"]
    assert t["time_stop_trade_days"] == 20
    cond = t["invalidation_conditions"][0]
    assert cond["template"] == "anchor_drawdown"
    assert cond["comparator"] == "lt"
    assert cond["threshold"] == 1530.0
    assert cond["anchor"] == 1700.0


@pytest.mark.asyncio
async def test_read_failure_fails_closed(tmp_path: Path) -> None:
    class _Exploding:
        def open_theses(self) -> dict[str, Any]:
            raise RuntimeError("corrupt ledger")

    data = await _get(_build_app(_Exploding()))
    assert data["available"] is False
    assert data["theses"] == []


@pytest.mark.asyncio
async def test_theses_sorted_by_code(tmp_path: Path) -> None:
    store = PositionThesisStore(tmp_path / "theses.jsonl")
    store.open_thesis(_thesis("600519"))
    store.open_thesis(_thesis("000001"))
    data = await _get(_build_app(store))
    codes = [t["stock_code"] for t in data["theses"]]
    assert codes == ["000001", "600519"]


def test_router_is_get_only() -> None:
    source = Path("backend/api/position_theses.py").read_text(encoding="utf-8")
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
    source = Path("backend/api/position_theses.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"llm", "agents", "risk", "broker", "data", "mirofish"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in forbidden:
                bad.append(node.module)
    assert bad == []
