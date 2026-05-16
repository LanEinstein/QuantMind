"""H-003 — GET /api/cost/{budget,breakdown,soft-degrade} tests.

Coverage:
- Redis unwired → status='unavailable' (no 500)
- Happy path returns daily+monthly+kimi composite
- Breakdown returns sorted daily totals + provider maps
- Soft-degrade flags reflect manager state
- GET-only invariant
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.cost import router as cost_router
from backend.services.cost_guard import (
    DailyBudgetState,
    FullBudgetState,
    KimiBudgetState,
    MonthlyBudgetState,
)
from backend.services.cost_probe import CostProbeSummary


def _build_app(*, redis: object | None = None) -> FastAPI:
    app = FastAPI()
    app.state.redis = redis
    app.include_router(cost_router)
    return app


@pytest.mark.asyncio
async def test_budget_unavailable_when_redis_missing() -> None:
    app = _build_app(redis=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/cost/budget")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_budget_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(redis=AsyncMock())

    state = FullBudgetState(
        daily=DailyBudgetState(
            daily_budget=20.0,
            spent_today=10.0,
            soft_ceiling=14.0,
            hard_ceiling=20.0,
            remaining=10.0,
            status="ok",
        ),
        monthly=MonthlyBudgetState(
            monthly_budget=440.0,
            spent_month=100.0,
            fraction=0.227,
            threshold_reached=None,
            status="ok",
        ),
        kimi=KimiBudgetState(
            kimi_daily_cap=4.0,
            spent_today=1.0,
            remaining=3.0,
            status="ok",
        ),
    )
    monkeypatch.setattr(
        "backend.api.cost.get_full_budget_state",
        AsyncMock(return_value=state),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/cost/budget")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["data"]["daily"]["status"] == "ok"
    assert body["data"]["monthly"]["status"] == "ok"
    assert body["data"]["kimi"]["status"] == "ok"


@pytest.mark.asyncio
async def test_breakdown_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(redis=AsyncMock())
    summary = CostProbeSummary(
        days=3,
        total_cost_rmb=7.5,
        daily_totals={
            "2026-05-14": 1.0,
            "2026-05-15": 3.5,
            "2026-05-16": 3.0,
        },
        by_provider={"deepseek": 4.0, "kimi": 3.5},
        by_provider_daily={
            "deepseek": {"2026-05-16": 1.0, "2026-05-15": 2.0, "2026-05-14": 1.0},
            "kimi": {"2026-05-16": 2.0, "2026-05-15": 1.5},
        },
    )
    monkeypatch.setattr(
        "backend.api.cost.scan_costs",
        AsyncMock(return_value=summary),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/cost/breakdown", params={"days": 3})
    body = resp.json()
    data = body["data"]
    assert data["status"] == "ok"
    # Most-recent first.
    keys = list(data["daily_totals"].keys())
    assert keys == ["2026-05-16", "2026-05-15", "2026-05-14"]
    assert data["total_cost_rmb"] == 7.5
    assert data["by_provider"] == {"deepseek": 4.0, "kimi": 3.5}


@pytest.mark.asyncio
async def test_breakdown_unavailable_when_redis_missing() -> None:
    app = _build_app(redis=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/cost/breakdown")
    body = resp.json()
    assert body["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_breakdown_days_bounds_enforced() -> None:
    app = _build_app(redis=AsyncMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_low = await client.get("/api/cost/breakdown", params={"days": 0})
        resp_high = await client.get("/api/cost/breakdown", params={"days": 999})
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


@pytest.mark.asyncio
async def test_soft_degrade_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_app(redis=AsyncMock())
    state = FullBudgetState(
        daily=DailyBudgetState(
            daily_budget=20.0,
            spent_today=15.0,
            soft_ceiling=14.0,
            hard_ceiling=20.0,
            remaining=5.0,
            status="soft_breach",
        ),
        monthly=MonthlyBudgetState(
            monthly_budget=440.0,
            spent_month=230.0,
            fraction=0.523,
            threshold_reached=0.50,
            status="threshold_50",
        ),
        kimi=KimiBudgetState(
            kimi_daily_cap=4.0,
            spent_today=4.0,
            remaining=0.0,
            status="hard_breach",
        ),
    )
    monkeypatch.setattr(
        "backend.api.cost.get_full_budget_state",
        AsyncMock(return_value=state),
    )

    class _FakeMgr:
        async def is_kimi_escalation_blocked(self) -> bool:
            return True

    monkeypatch.setattr(
        "backend.api.cost.SoftDegradeManager",
        lambda _redis: _FakeMgr(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/cost/soft-degrade")
    body = resp.json()
    data = body["data"]
    assert data["kimi_escalation_blocked"] is True
    assert data["daily_status"] == "soft_breach"
    assert data["monthly_status"] == "threshold_50"
    assert data["kimi_status"] == "hard_breach"
    assert data["monthly_threshold_reached"] == 0.50


def test_cost_router_is_get_only() -> None:
    source = Path("backend/api/cost.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"post", "put", "patch", "delete"}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call):
                    func = deco.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr in forbidden
                    ):
                        found.append(f"{node.name}:{func.attr}")
    assert not found, f"cost API must be GET-only; found {found}"
