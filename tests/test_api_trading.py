"""Tests for trading API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.broker.approval_queue import ApprovalQueue
from backend.broker.models import (
    BrokerConfig,
    OrderDirection,
    OrderType,
    RiskConfig,
    ValidationResult,
)
from backend.broker.registry import BrokerRegistry
from backend.main import app


@pytest.fixture()
def registry() -> BrokerRegistry:
    reg = BrokerRegistry(BrokerConfig(initial_capital=1_000_000.0))
    app.state.broker_registry = reg
    return reg


@pytest.fixture()
def approval_queue(registry: BrokerRegistry) -> ApprovalQueue:
    q = ApprovalQueue(registry)
    app.state.approval_queue = q
    return q


@pytest.fixture()
def risk_config() -> RiskConfig:
    from backend.broker.models import (
        CircuitBreakerConfig,
        PositionLimitsConfig,
        StopLossConfig,
    )

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
    approval_queue: ApprovalQueue,
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


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_400(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/api/trading/cancel/nonexistent")
        assert resp.status_code == 400


class TestPendingApprovals:
    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, client: AsyncClient) -> None:
        resp = await client.get("/api/trading/pending-approvals")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.asyncio
    async def test_returns_pending_after_submit(
        self, client: AsyncClient, approval_queue: ApprovalQueue
    ) -> None:
        approval_queue.submit(
            account_id="default",
            code="601318",
            price=52.3,
            volume=300,
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            agent_recommendation="Buy 601318",
            reasoning="Undervalued",
            risk_pre_check=ValidationResult(passed=True),
        )
        resp = await client.get("/api/trading/pending-approvals")
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["code"] == "601318"


class TestApproveOrder:
    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/api/trading/approve/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_sends_to_broker(
        self, client: AsyncClient, approval_queue: ApprovalQueue
    ) -> None:
        approval = approval_queue.submit(
            account_id="default",
            code="601318",
            price=52.3,
            volume=300,
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            agent_recommendation="Buy 601318",
            reasoning="Test",
            risk_pre_check=ValidationResult(passed=True),
        )
        resp = await client.post(f"/api/trading/approve/{approval.id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "order_id" in data


class TestRejectOrder:
    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/api/trading/reject/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_removes_pending(
        self, client: AsyncClient, approval_queue: ApprovalQueue
    ) -> None:
        approval = approval_queue.submit(
            account_id="default",
            code="601318",
            price=52.3,
            volume=300,
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            agent_recommendation="Buy 601318",
            reasoning="Test",
            risk_pre_check=ValidationResult(passed=True),
        )
        resp = await client.post(f"/api/trading/reject/{approval.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["success"] is True
        # Verify removed
        resp2 = await client.get("/api/trading/pending-approvals")
        assert len(resp2.json()["data"]) == 0
