"""Tests for circuit-breaker gating in ApprovalQueue (Session D.4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.broker.approval_queue import (
    ApprovalQueue,
    CircuitBreakerHaltedError,
)
from backend.broker.models import (
    OrderDirection,
    OrderResult,
    OrderType,
    ValidationResult,
)


def _registry_with_successful_broker() -> MagicMock:
    registry = MagicMock()
    broker = MagicMock()
    broker.place_order = AsyncMock(
        return_value=OrderResult(
            success=True,
            order_id="ord-001",
            status="FILLED",
            message="",
        )
    )
    registry.get_broker.return_value = broker
    return registry


def _submit(queue: ApprovalQueue) -> str:
    approval = queue.submit(
        account_id="acct-1",
        code="600519",
        price=1800.0,
        volume=100,
        direction=OrderDirection.BUY,
        order_type=OrderType.LIMIT,
        agent_recommendation="BUY",
        reasoning="bull",
        risk_pre_check=ValidationResult(passed=True, rule_name="stub", message=""),
    )
    return approval.id


class TestApprovalWithoutCircuitBreaker:
    @pytest.mark.asyncio
    async def test_approve_dispatches_order(self) -> None:
        registry = _registry_with_successful_broker()
        queue = ApprovalQueue(registry)
        approval_id = _submit(queue)

        result = await queue.approve(approval_id)
        assert result.success is True
        registry.get_broker.return_value.place_order.assert_awaited_once()


class TestApprovalGatedByCircuitBreaker:
    @pytest.mark.asyncio
    async def test_approve_rejected_when_halted(self) -> None:
        registry = _registry_with_successful_broker()
        queue = ApprovalQueue(
            registry, halt_check=lambda: True
        )
        approval_id = _submit(queue)

        with pytest.raises(CircuitBreakerHaltedError):
            await queue.approve(approval_id)

        # Order did not reach the broker
        registry.get_broker.return_value.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_halted_approval_stays_in_queue(self) -> None:
        """A rejected approval must remain pending, so the operator can
        retry once the circuit breaker cools down."""
        registry = _registry_with_successful_broker()
        queue = ApprovalQueue(
            registry, halt_check=lambda: True
        )
        approval_id = _submit(queue)

        with pytest.raises(CircuitBreakerHaltedError):
            await queue.approve(approval_id)

        # Still listed as pending
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].id == approval_id

    @pytest.mark.asyncio
    async def test_approve_succeeds_after_cooldown(self) -> None:
        """Halt flips off → same approval id can be approved."""
        registry = _registry_with_successful_broker()
        halted = {"value": True}
        queue = ApprovalQueue(
            registry, halt_check=lambda: halted["value"]
        )
        approval_id = _submit(queue)

        with pytest.raises(CircuitBreakerHaltedError):
            await queue.approve(approval_id)

        halted["value"] = False
        result = await queue.approve(approval_id)
        assert result.success is True
        registry.get_broker.return_value.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_approve_missing_id_still_raises_key_error(self) -> None:
        queue = ApprovalQueue(
            _registry_with_successful_broker(), halt_check=lambda: False
        )
        with pytest.raises(KeyError):
            await queue.approve("does-not-exist")
