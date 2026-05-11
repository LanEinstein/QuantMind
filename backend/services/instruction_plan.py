"""Pure helpers around :class:`InstructionPlan` (P0-3 §1.1).

Three responsibilities:

1. :func:`make_instruction_id` — generate the QM-... id from a
   timezone-aware datetime, code, side and 1-999 sequence number. The
   InstructionPlanBuilder (Phase D) maintains the seq counter; this
   function is pure and trivially testable.
2. :func:`derive_order_from_plan` — bridge an InstructionPlan into the
   :class:`backend.broker.models.Order` shape so RiskEngine can run its
   14-check chain without changes to its signature (P0-1 §2 red line 8
   / P0-7 §2 red line 9).
3. :func:`is_routable` / :func:`validate_valid_until` — small predicate
   helpers for ModeRouter and timing audits; they intentionally do not
   touch IO so they can be unit-tested in milliseconds.

This module is **not** the InstructionPlanBuilder — wiring TradingSignal
→ InstructionPlan with PositionSizer + RiskEngine lives in Phase D. For
B-001 we only ship the schema and the pure helpers.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.broker.models import (
    Order,
    OrderDirection,
    OrderStatus,
    OrderType,
)
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)

_SH = ZoneInfo("Asia/Shanghai")
_CODE_RE = re.compile(r"^\d{6}$")


def make_instruction_id(
    created_at: datetime,
    stock_code: str,
    side: InstructionSide,
    seq: int,
) -> str:
    """Build a P0-3 §1.2 conformant ``instruction_id``.

    Args:
        created_at: zone-aware datetime; we project to Asia/Shanghai so
            the date/time components in the id match the trading day.
        stock_code: 6-digit A-share code; non-conforming codes raise.
        side: :class:`InstructionSide`.
        seq: 1-999 same-second sequence counter; outside that range raises
            (P0-3 §2 red line 11 — same-second/same-code/same-side ≥ 1000
            implies a runaway LLM and must surface as a builder error).

    Returns:
        ``QM-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ`` exactly.
    """
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    if not _CODE_RE.fullmatch(stock_code):
        raise ValueError(f"stock_code {stock_code!r} must be 6 digits")
    if not 1 <= seq <= 999:
        raise ValueError(f"seq {seq} out of range [1, 999]")

    local = created_at.astimezone(_SH)
    return (
        f"QM-{local.strftime('%Y%m%d')}-{local.strftime('%H%M%S')}"
        f"-{stock_code}-{side.value}-{seq:03d}"
    )


def derive_order_from_plan(plan: InstructionPlan) -> Order:
    """Map an InstructionPlan into the broker :class:`Order` shape.

    HOLD plans never reach the broker, so we refuse to derive them (the
    caller must route HOLD into the ledger via a different path). For
    BUY / SELL we always emit a LIMIT order; market orders are out of
    scope for the first phase (P0-3 §1.3.2).
    """
    if plan.side is InstructionSide.HOLD:
        raise ValueError(
            "HOLD InstructionPlan must not derive to Order (use ledger path)"
        )
    if plan.volume is None or plan.limit_price is None:
        raise ValueError(
            f"BUY/SELL plan {plan.instruction_id} missing volume/limit_price"
        )

    direction = (
        OrderDirection.BUY
        if plan.side is InstructionSide.BUY
        else OrderDirection.SELL
    )
    return Order(
        order_id=plan.instruction_id,
        code=plan.stock_code,
        price=plan.limit_price,
        volume=plan.volume,
        direction=direction,
        order_type=OrderType.LIMIT,
        status=OrderStatus.PENDING,
        created_at=plan.created_at,
        updated_at=plan.created_at,
    )


def is_routable(plan: InstructionPlan) -> bool:
    """ModeRouter gate (P0-3 §1.3.1, §2 red line 2).

    HOLD plans never route. Non-VALIDATED plans never route (drafts must
    pass RiskEngine first, rejected plans must stay in the ledger).
    """
    return (
        plan.side is not InstructionSide.HOLD
        and plan.status is InstructionStatus.VALIDATED
    )


def validate_valid_until(plan: InstructionPlan) -> None:
    """Standalone re-validation of the valid_until 3-way constraint.

    The model validator runs at construction time, but callers that
    mutate the plan via :meth:`InstructionPlan.model_copy` skip the
    cross-field validators by design. Use this helper any time you have
    a plan in hand and want to confirm the cutoff is still satisfied
    (e.g., before re-dispatching an EXPIRED plan).
    """
    created_local = plan.created_at.astimezone(_SH)
    valid_local = plan.valid_until.astimezone(_SH)
    if valid_local <= created_local:
        raise ValueError("valid_until must be strictly after created_at")
    if valid_local.date() != created_local.date():
        raise ValueError(
            "valid_until must share the Asia/Shanghai trading date with created_at"
        )
    cutoff = created_local.replace(hour=14, minute=55, second=0, microsecond=0)
    if valid_local > cutoff:
        raise ValueError(
            f"valid_until {valid_local.isoformat()} exceeds the 14:55 cutoff"
        )


__all__ = [
    "derive_order_from_plan",
    "is_routable",
    "make_instruction_id",
    "validate_valid_until",
]
