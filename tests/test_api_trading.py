"""Tests for trading API endpoints (GET-only per P1-5 §2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.broker.models import (
    BrokerConfig,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
)
from backend.broker.registry import BrokerRegistry
from backend.main import app
from backend.risk.circuit_breaker import CircuitBreaker


@pytest.fixture()
def registry() -> BrokerRegistry:
    reg = BrokerRegistry(BrokerConfig(initial_capital=1_000_000.0))
    app.state.broker_registry = reg
    return reg


@pytest.fixture()
def risk_config() -> RiskConfig:
    cfg = RiskConfig(
        position_limits=PositionLimitsConfig(),
        stop_loss=StopLossConfig(),
        circuit_breaker=CircuitBreakerConfig(),
    )
    app.state.risk_config = cfg
    return cfg


@pytest.fixture()
async def client(
    registry: BrokerRegistry,
    risk_config: RiskConfig,
) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestGetAccounts:
    @pytest.mark.asyncio
    async def test_lists_default_account(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1
        assert body["data"][0]["account_id"] == "default"

    @pytest.mark.asyncio
    async def test_lists_multiple_accounts(
        self, client: AsyncClient, registry: BrokerRegistry
    ) -> None:
        registry.create_account("conservative", "策略B (保守)")
        resp = await client.get("/api/trading/accounts")
        assert len(resp.json()["data"]) == 2


class TestGetAccount:
    @pytest.mark.asyncio
    async def test_returns_default_account(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/account")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["initial_capital"] == 1_000_000.0
        assert data["total_assets"] == 1_000_000.0

    @pytest.mark.asyncio
    async def test_missing_account_returns_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/trading/account?account_id=missing")
        assert resp.status_code == 404


class TestGetPositions:
    @pytest.mark.asyncio
    async def test_returns_empty_positions(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/positions")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestGetOrders:
    @pytest.mark.asyncio
    async def test_returns_empty_orders(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/orders")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_invalid_status_returns_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/trading/orders?status=INVALID")
        assert resp.status_code == 422


class TestGetTrades:
    @pytest.mark.asyncio
    async def test_returns_empty_trades(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/trades")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_invalid_code_returns_422(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/trades?code=ABC")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_date_returns_422(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/trades?start_date=not-a-date")
        assert resp.status_code == 422


class TestTradingWriteRoutesRemoved:
    """P1-5 §2: trading POST routes (cancel / approve / reject / pending-approvals)
    were destructively deleted with the ApprovalQueue in A-002. Confirm 405."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method, path",
        [
            ("POST", "/api/trading/cancel/abc"),
            ("POST", "/api/trading/approve/abc"),
            ("POST", "/api/trading/reject/abc"),
            ("GET", "/api/trading/pending-approvals"),
        ],
    )
    async def test_route_removed(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        resp = await client.request(method, path)
        assert resp.status_code in {404, 405}


class TestCircuitBreakerStatus:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_breaker(
        self, client: AsyncClient
    ) -> None:
        app.state.circuit_breaker = None
        resp = await client.get("/api/trading/circuit-breaker-status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["halted"] is False
        assert data["daily_pnl_pct"] == 0.0
        assert data["consecutive_losses"] == 0

    @pytest.mark.asyncio
    async def test_returns_not_halted_initially(
        self, client: AsyncClient
    ) -> None:
        cb = CircuitBreaker(CircuitBreakerConfig())
        app.state.circuit_breaker = cb
        resp = await client.get("/api/trading/circuit-breaker-status")
        data = resp.json()["data"]
        assert data["halted"] is False

    @pytest.mark.asyncio
    async def test_returns_halted_after_trigger(
        self, client: AsyncClient
    ) -> None:
        cb = CircuitBreaker(CircuitBreakerConfig(daily_loss_limit_pct=0.05))
        app.state.circuit_breaker = cb
        cb.record_trade_result(-0.06)
        resp = await client.get("/api/trading/circuit-breaker-status")
        data = resp.json()["data"]
        assert data["halted"] is True
        assert data["daily_pnl_pct"] == pytest.approx(-0.06)
        assert data["consecutive_losses"] == 1
