"""H-004 — GET /api/monitoring/alert-matrix tests."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.monitoring import router as monitoring_router
from backend.monitoring.alert_dispatcher import ALERT_MATRIX


@pytest.mark.asyncio
async def test_alert_matrix_endpoint_returns_locked_rows() -> None:
    app = FastAPI()
    app.include_router(monitoring_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/monitoring/alert-matrix")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert len(body["data"]["alerts"]) == len(ALERT_MATRIX) == 13
    types = {row["alert_type"] for row in body["data"]["alerts"]}
    # Spot-check a few across categories
    assert "daily_cost_ceiling_20cny_breached" in types
    assert "monthly_budget_50pct_reached" in types
    assert "circuit_breaker_open" in types
    assert "feishu_longconn_disconnected" in types
    assert "evolution_amendment_drafted" in types


@pytest.mark.asyncio
async def test_alert_matrix_monthly_milestones_audit_only() -> None:
    app = FastAPI()
    app.include_router(monitoring_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/monitoring/alert-matrix")
    rows = {row["alert_type"]: row for row in resp.json()["data"]["alerts"]}
    for pct in (50, 80, 100):
        assert rows[f"monthly_budget_{pct}pct_reached"]["fire_to_feishu"] is False
