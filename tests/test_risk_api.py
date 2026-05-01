"""Unit tests for the risk control center API endpoints."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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
        assert data["authorization_mode"] in {
            "suggestion",
            "semi_auto",
            "full_auto",
        }
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


# -- POST /api/risk/config --


class TestUpdateRiskConfig:
    @patch("backend.api.risk._persist_risk_config")
    @patch("backend.broker.models.load_risk_config")
    async def test_update_config(
        self,
        mock_load: MagicMock,
        mock_persist: MagicMock,
        client: AsyncClient,
    ) -> None:
        mock_load.return_value = _make_risk_config()
        resp = await client.post(
            "/api/risk/config",
            json={"single_stock_limit": 15},
        )
        assert resp.status_code == 200
        mock_persist.assert_called_once()

    async def test_update_without_config(self, client: AsyncClient) -> None:
        app.state.risk_config = None
        resp = await client.post(
            "/api/risk/config",
            json={"single_stock_limit": 15},
        )
        assert resp.status_code == 503
        app.state.risk_config = _make_risk_config()


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


# -- POST /api/risk/auth-mode --


class TestSwitchAuthMode:
    async def test_switch_mode_to_suggestion_in_eval(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`suggestion` is the only allowed mode in `phase5_eval`."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "suggestion"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["authorization_mode"] == "suggestion"

    async def test_invalid_mode(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "invalid_mode"},
        )
        assert resp.status_code == 422

    async def test_cross_phase_rejected_in_eval(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`semi_auto` / `full_auto` / canonical `confirm` / `auto` must 403 in eval."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        for blocked in ("semi_auto", "full_auto", "confirm", "auto"):
            resp = await client.post(
                "/api/risk/auth-mode",
                json={"mode": blocked},
            )
            assert resp.status_code == 403, blocked
            err = resp.json()["detail"]["error"]
            assert "phase5_eval" in err

    async def test_canonical_short_form_accepted_in_eval(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Canonical `suggest` (short form) must be accepted by the endpoint."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "suggest"},
        )
        assert resp.status_code == 200

    async def test_canonical_confirm_accepted_in_dryrun(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`phase6_dryrun` must accept canonical `confirm`."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase6_dryrun")
        resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "confirm"},
        )
        assert resp.status_code == 200

    async def test_dryrun_rejects_auto(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`phase6_dryrun` must NOT accept `auto` / `full_auto`."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase6_dryrun")
        for blocked in ("auto", "full_auto"):
            resp = await client.post(
                "/api/risk/auth-mode",
                json={"mode": blocked},
            )
            assert resp.status_code == 403, blocked

    async def test_live_phase_accepts_auto(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`phase7_live` must accept canonical `auto` and legacy `full_auto`."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase7_live")
        for accepted in ("auto", "full_auto"):
            resp = await client.post(
                "/api/risk/auth-mode",
                json={"mode": accepted},
            )
            assert resp.status_code == 200, accepted

    async def test_post_then_get_consistency_short_form_input(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST canonical short → GET status must show consistent long form.

        Closes the cycle 2 P2 finding: a `suggest` POST used to land env
        as `suggest` and ``_get_auth_mode``'s ``replace`` produced
        ``suggestion``; a `suggestion` POST landed env as ``suggestion``
        and the same replace produced ``suggestionion``.
        """
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        post_resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "suggest"},
        )
        assert post_resp.status_code == 200
        assert post_resp.json()["data"]["authorization_mode"] == "suggestion"

        # Env should now hold the canonical short, not the raw input.
        assert os.environ.get("AUTHORIZATION_MODE") == "suggest"

        get_resp = await client.get("/api/risk/status")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["authorization_mode"] == "suggestion"

    async def test_post_then_get_consistency_long_form_input(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST legacy long form must canonicalize and not produce 'suggestionion'."""
        monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
        post_resp = await client.post(
            "/api/risk/auth-mode",
            json={"mode": "suggestion"},
        )
        assert post_resp.status_code == 200
        # Env stores canonical short, never the long input.
        assert os.environ.get("AUTHORIZATION_MODE") == "suggest"

        get_resp = await client.get("/api/risk/status")
        # The bug used to produce "suggestionion" here.
        assert get_resp.json()["data"]["authorization_mode"] == "suggestion"


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
