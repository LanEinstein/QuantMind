"""Tests for ApprovalQueue pending order management."""

from __future__ import annotations

import pytest

from backend.broker.approval_queue import ApprovalQueue
from backend.broker.models import (
    BrokerConfig,
    OrderDirection,
    OrderType,
    ValidationResult,
)
from backend.broker.registry import BrokerRegistry


@pytest.fixture()
def registry() -> BrokerRegistry:
    return BrokerRegistry(BrokerConfig(initial_capital=1_000_000.0))


@pytest.fixture()
def queue(registry: BrokerRegistry) -> ApprovalQueue:
    return ApprovalQueue(registry)


def _submit_sample(queue: ApprovalQueue, code: str = "601318"):
    """Helper to submit a sample approval."""
    return queue.submit(
        account_id="default",
        code=code,
        price=52.30,
        volume=300,
        direction=OrderDirection.BUY,
        order_type=OrderType.LIMIT,
        agent_recommendation=f"基金经理建议买入 {code} 300股@¥52.3",
        reasoning="Fundamental analysis indicates undervalued",
        risk_pre_check=ValidationResult(
            passed=True, rule_name="position_limit", message="OK"
        ),
    )


class TestApprovalQueue:
    def test_submit_creates_pending(self, queue: ApprovalQueue) -> None:
        approval = _submit_sample(queue)
        assert approval.code == "601318"
        assert approval.direction == OrderDirection.BUY
        assert len(queue.list_pending()) == 1

    def test_list_pending_filters_by_account(
        self, queue: ApprovalQueue, registry: BrokerRegistry
    ) -> None:
        registry.create_account("other", "Other")
        _submit_sample(queue, "601318")
        queue.submit(
            account_id="other",
            code="000001",
            price=10.0,
            volume=100,
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            agent_recommendation="Buy 000001",
            reasoning="Test",
            risk_pre_check=ValidationResult(passed=True),
        )
        assert len(queue.list_pending()) == 2
        assert len(queue.list_pending(account_id="default")) == 1
        assert len(queue.list_pending(account_id="other")) == 1

    @pytest.mark.asyncio
    async def test_approve_sends_to_broker(self, queue: ApprovalQueue) -> None:
        approval = _submit_sample(queue)
        # MockBroker will likely reject (trading hours check), but the flow works
        result = await queue.approve(approval.id)
        assert result.order_id  # has an order_id
        assert len(queue.list_pending()) == 0

    @pytest.mark.asyncio
    async def test_approve_nonexistent_raises(
        self, queue: ApprovalQueue
    ) -> None:
        with pytest.raises(KeyError, match="not found"):
            await queue.approve("nonexistent")

    def test_reject_removes_pending(self, queue: ApprovalQueue) -> None:
        approval = _submit_sample(queue)
        assert queue.reject(approval.id) is True
        assert len(queue.list_pending()) == 0

    def test_reject_nonexistent_returns_false(
        self, queue: ApprovalQueue
    ) -> None:
        assert queue.reject("nonexistent") is False
