"""Unit tests for the risk control center API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.risk import _risk_events, record_risk_event
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    Position,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
)
from backend.main import app


def _make_risk_config() -> RiskConfig:
    return RiskConfig(
        position_limits=PositionLimitsConfig(
            max_single_stock_pct=0.20,
            max_sector_pct=0.40,
            max_total_positions=10,
            price_deviation_limit=0.05,
            volume_lot_size=100,
        ),
        stop_loss=StopLossConfig(
            single_stock_pct=0.08,
            portfolio_daily_pct=0.05,
            trailing_stop_pct=0.10,
        ),
        circuit_breaker=CircuitBreakerConfig(
            daily_loss_limit_pct=0.05,
            consecutive_loss_count=3,
            cooldown_minutes=60,
        ),
    )


def _make_account(
    total_assets: float = 1_000_000,
    market_value: float = 600_000,
) -> AccountInfo:
    return AccountInfo(
        total_assets=total_assets,
        available_cash=total_assets - market_value,
        frozen_cash=0,
        market_value=market_value,
        total_pnl=0,
        total_pnl_pct=0,
        initial_capital=1_000_000,
    )


def _make_positions() -> tuple[Position, ...]:
    return (
        Position(
            code="600519",
            volume=100,
            available_volume=100,
            cost_price=1800.0,
            market_value=180_000,
            unrealized_pnl=0,
            unrealized_pnl_pct=0,
        ),
        Position(
            code="000001",
            volume=5000,
            available_volume=5000,
            cost_price=12.0,
            market_value=60_000,
            unrealized_pnl=0,
            unrealized_pnl_pct=0,
        ),
    )


@pytest.fixture(autouse=True)
def _clear_risk_events():
    """Clear in-memory risk events before each test."""
    _risk_events.clear()
    yield
    _risk_events.clear()


@pytest.fixture()
def mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.get_account = AsyncMock(return_value=_make_account())
    broker.get_positions = AsyncMock(return_value=_make_positions())
    return broker


@pytest.fixture()
def mock_registry(mock_broker: MagicMock) -> MagicMock:
    registry = MagicMock()
    registry.get_broker.return_value = mock_broker
    return registry


@pytest.fixture()
async def client(mock_registry: MagicMock) -> AsyncClient:
    """Async HTTP test client with mocked app state."""
    app.state.risk_config = _make_risk_config()
    app.state.broker_registry = mock_registry
    app.state.circuit_breaker = None
    app.state.redis = AsyncMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# -- GET /api/risk/status --


class TestGetRiskStatus:
    async def test_returns_status(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["system_status"] == "normal"
        # P0-1: run_mode replaces the legacy authorization_mode tri-state.
        assert data["run_mode"]["simulation_auto"] is True
        assert isinstance(data["run_mode"]["feishu_interactive"], bool)
        assert "authorization_mode" not in data
        assert isinstance(data["circuit_breaker_triggered"], bool)

    async def test_circuit_breaker_status(self, client: AsyncClient) -> None:
        cb = MagicMock()
        cb.is_halted.return_value = True
        app.state.circuit_breaker = cb

        resp = await client.get("/api/risk/status")
        data = resp.json()["data"]
        assert data["system_status"] == "circuit_breaker"
        assert data["circuit_breaker_triggered"] is True

        app.state.circuit_breaker = None


# -- GET /api/risk/radar --


class TestGetRiskRadar:
    async def test_returns_radar_data(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/radar")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_position_pct" in data
        assert "max_single_stock_pct" in data
        assert "stock_count" in data
        assert data["max_single_stock_limit"] == 20
        assert data["stock_count_limit"] == 10


# -- GET /api/risk/config --


class TestGetRiskConfig:
    async def test_returns_config(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["single_stock_limit"] == 20.0
        assert data["stop_loss_threshold"] == -8.0
        assert data["circuit_breaker_threshold"] == -5.0
        assert data["price_deviation_limit"] == 5.0

    async def test_config_not_loaded(self, client: AsyncClient) -> None:
        app.state.risk_config = None
        resp = await client.get("/api/risk/config")
        assert resp.status_code == 503
        app.state.risk_config = _make_risk_config()


# -- POST /api/risk/config (removed in A-001/A-004) --


class TestRiskConfigWriteRemoved:
    """POST /api/risk/config was destructively deleted.

    P1-5: only 2 write endpoints remain.
    """

    async def test_post_config_returns_404_or_405(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/risk/config",
            json={"single_stock_limit": 15},
        )
        assert resp.status_code in {404, 405}


# -- GET /api/risk/events --


class TestGetRiskEvents:
    async def test_returns_empty_events(self, client: AsyncClient) -> None:
        resp = await client.get("/api/risk/events")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_returns_recorded_events(self, client: AsyncClient) -> None:
        record_risk_event("warning", "Test event", "Test action")
        record_risk_event("info", "Info event", "Logged")

        resp = await client.get("/api/risk/events")
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["level"] == "info"  # Most recent first

    async def test_filter_by_level(self, client: AsyncClient) -> None:
        record_risk_event("warning", "Warn 1", "Action")
        record_risk_event("info", "Info 1", "Action")
        record_risk_event("critical", "Crit 1", "Action")

        resp = await client.get("/api/risk/events", params={"level": "warning"})
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["level"] == "warning"

    async def test_limit_events(self, client: AsyncClient) -> None:
        for i in range(10):
            record_risk_event("info", f"Event {i}", "Action")

        resp = await client.get("/api/risk/events", params={"limit": 3})
        assert len(resp.json()["data"]) == 3


# -- POST /api/risk/auth-mode (removed in A-001) --


class TestAuthModeRouteRemoved:
    """POST /api/risk/auth-mode was destructively deleted by P0-1 run_mode redesign.

    Run mode is now driven exclusively by ``FEISHU_INTERACTIVE_ENABLED``
    at process start; runtime mutation is forbidden so there is no API
    surface for it.
    """

    async def test_route_returns_404_or_405(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "suggestion"},
        )
        assert resp.status_code in {404, 405}


# -- record_risk_event --


class TestRecordRiskEvent:
    def test_record_event(self) -> None:
        record_risk_event("warning", "Test", "Action")
        assert len(_risk_events) == 1
        assert _risk_events[0]["level"] == "warning"
        assert _risk_events[0]["description"] == "Test"

    def test_events_capped_at_500(self) -> None:
        for i in range(510):
            record_risk_event("info", f"Event {i}", "Action")
        assert len(_risk_events) == 500
