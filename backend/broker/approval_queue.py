"""ApprovalQueue — pending order queue for confirmation mode."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, ConfigDict

from backend.broker.models import (
    OrderDirection,
    OrderResult,
    OrderType,
    ValidationResult,
)
from backend.broker.registry import BrokerRegistry

log = structlog.get_logger(component="broker.approval_queue")


class PendingApproval(BaseModel):
    """Immutable record of an order awaiting human approval."""

    model_config = ConfigDict(frozen=True)

    id: str
    account_id: str
    code: str
    price: float
    volume: int
    direction: OrderDirection
    order_type: OrderType
    agent_recommendation: str
    reasoning: str
    risk_pre_check: ValidationResult
    created_at: str


class ApprovalQueue:
    """Holds orders pending human approval in confirmation mode.

    When the system runs in 'confirm' authorization mode, agent-generated
    orders are intercepted here instead of being sent directly to the broker.
    """

    def __init__(self, registry: BrokerRegistry) -> None:
        self._registry = registry
        self._pending: dict[str, PendingApproval] = {}

    def submit(
        self,
        account_id: str,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        order_type: OrderType,
        agent_recommendation: str,
        reasoning: str,
        risk_pre_check: ValidationResult,
    ) -> PendingApproval:
        """Submit an order for human approval.

        Args:
            account_id: Target virtual account.
            code: 6-digit stock code.
            price: Order price.
            volume: Number of shares.
            direction: BUY or SELL.
            order_type: LIMIT or MARKET.
            agent_recommendation: Human-readable recommendation text.
            reasoning: Agent's reasoning summary.
            risk_pre_check: Result of risk engine pre-check.

        Returns:
            The created PendingApproval record.
        """
        approval_id = uuid.uuid4().hex[:12]
        now = datetime.now(tz=timezone.utc).isoformat()

        approval = PendingApproval(
            id=approval_id,
            account_id=account_id,
            code=code,
            price=price,
            volume=volume,
            direction=direction,
            order_type=order_type,
            agent_recommendation=agent_recommendation,
            reasoning=reasoning,
            risk_pre_check=risk_pre_check,
            created_at=now,
        )
        self._pending[approval_id] = approval

        log.info(
            "approval_submitted",
            id=approval_id,
            code=code,
            direction=direction,
            account_id=account_id,
        )
        return approval

    async def approve(self, approval_id: str) -> OrderResult:
        """Approve a pending order and send it to the broker.

        Args:
            approval_id: The pending approval ID.

        Returns:
            The OrderResult from the broker.

        Raises:
            KeyError: If the approval ID does not exist.
        """
        approval = self._pending.pop(approval_id, None)
        if approval is None:
            raise KeyError(f"Pending approval '{approval_id}' not found")

        broker = self._registry.get_broker(approval.account_id)
        result = await broker.place_order(
            code=approval.code,
            price=approval.price,
            volume=approval.volume,
            direction=approval.direction,
            order_type=approval.order_type,
        )

        log.info(
            "approval_approved",
            id=approval_id,
            order_id=result.order_id,
            success=result.success,
        )
        return result

    def reject(self, approval_id: str) -> bool:
        """Reject a pending order.

        Args:
            approval_id: The pending approval ID.

        Returns:
            True if the approval was found and rejected.
        """
        removed = self._pending.pop(approval_id, None)
        if removed is None:
            return False

        log.info("approval_rejected", id=approval_id, code=removed.code)
        return True

    def list_pending(
        self, account_id: str | None = None
    ) -> tuple[PendingApproval, ...]:
        """List all pending approvals, optionally filtered by account.

        Args:
            account_id: If provided, only return approvals for this account.

        Returns:
            Tuple of pending approval records.
        """
        items = self._pending.values()
        if account_id is not None:
            items = [a for a in items if a.account_id == account_id]
        return tuple(items)
