"""Tests for detailed health monitoring endpoint (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture()
def mock_app_state() -> None:
    """Set up minimal app state for health checks."""
    app.state.redis = AsyncMock()
    app.state.redis.ping = AsyncMock(return_value=True)

    app.state.mongodb = AsyncMock()

    mock_mongo_client = MagicMock()
    mock_admin = MagicMock()
    mock_admin.command = AsyncMock(return_value={"ok": 1})
    mock_mongo_client.admin = mock_admin
    app.state.mongo_client = mock_mongo_client

    app.state.llm_router = MagicMock()

    scheduler_mock = MagicMock()
    scheduler_mock._scheduler = MagicMock()
    scheduler_mock._scheduler.running = True
    app.state.scheduler = scheduler_mock

    analysis_scheduler_mock = MagicMock()
    analysis_scheduler_mock._scheduler = MagicMock()
    analysis_scheduler_mock._scheduler.running = True
    app.state.analysis_scheduler = analysis_scheduler_mock

    app.state.app_start_time = 1000000.0


@pytest.fixture()
async def client(mock_app_state: None) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDetailedHealth:
    """Tests for GET /api/health/detailed."""

    @pytest.mark.asyncio
    async def test_ok_when_all_services_up(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health/detailed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] in ("ok", "degraded")
        assert "components" in body["data"]

    @pytest.mark.asyncio
    async def test_degraded_when_redis_down(self, client: AsyncClient) -> None:
        app.state.redis.ping = AsyncMock(side_effect=ConnectionError("down"))

        resp = await client.get("/api/health/detailed")
        body = resp.json()
        assert body["data"]["status"] in ("degraded", "critical")
        assert body["data"]["components"]["redis"] == "error"

    @pytest.mark.asyncio
    async def test_degraded_when_mongodb_down(self, client: AsyncClient) -> None:
        app.state.mongo_client.admin.command = AsyncMock(
            side_effect=ConnectionError("down")
        )

        resp = await client.get("/api/health/detailed")
        body = resp.json()
        assert body["data"]["status"] in ("degraded", "critical")
        assert body["data"]["components"]["mongodb"] == "error"

    @pytest.mark.asyncio
    async def test_includes_uptime(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health/detailed")
        body = resp.json()
        assert "uptime_seconds" in body["data"]
        assert body["data"]["uptime_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_includes_scheduler_status(self, client: AsyncClient) -> None:
        resp = await client.get("/api/health/detailed")
        body = resp.json()
        components = body["data"]["components"]
        assert "data_scheduler" in components
        assert "analysis_scheduler" in components
