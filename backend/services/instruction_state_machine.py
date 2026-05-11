"""Instruction state-machine guard (P0-4 §1.3, B-003).

Owns the single source of truth for ``InstructionStatus`` transitions
plus the 16:00 Asia/Shanghai post-close freeze. All non-test callers
**must** route status changes through :func:`transition`; direct
``InstructionPlan.model_copy(update={"status": ...})`` is a red line
(P0-3 §2 红线 12 + P0-4 §1.3.4).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.models.instruction import InstructionPlan, InstructionStatus

_SH = ZoneInfo("Asia/Shanghai")
_POST_CLOSE_HHMM = (16, 0)
"""Cutoff for status changes after the trading day closes (P0-4 §1.6)."""


class InvalidTransitionError(ValueError):
    """The requested status transition is not in the allowlist."""


class PostCloseFreezeError(ValueError):
    """Transition arrived after the 16:00 Asia/Shanghai freeze."""


# P0-3 §1.1.1 baseline plus the P0-4 §1.3.2 extensions. Held as a
# frozenset so accidental in-place mutation raises at import time.
ALLOWED_TRANSITIONS: frozenset[tuple[InstructionStatus, InstructionStatus]] = (
    frozenset(
        {
            # P0-3 baseline (7 transitions)
            (InstructionStatus.DRAFT, InstructionStatus.VALIDATED),
            (InstructionStatus.DRAFT, InstructionStatus.REJECTED),
            (InstructionStatus.VALIDATED, InstructionStatus.DISPATCHED),
            (InstructionStatus.DISPATCHED, InstructionStatus.FILLED),
            (InstructionStatus.DISPATCHED, InstructionStatus.EXPIRED),
            (InstructionStatus.DISPATCHED, InstructionStatus.REJECTED),
            (InstructionStatus.DISPATCHED, InstructionStatus.AMBIGUOUS),
            # P0-4 §1.3.2 extensions
            (InstructionStatus.AMBIGUOUS, InstructionStatus.DISPATCHED),
            (InstructionStatus.AMBIGUOUS, InstructionStatus.FILLED),
            (InstructionStatus.AMBIGUOUS, InstructionStatus.REJECTED),
            (InstructionStatus.AMBIGUOUS, InstructionStatus.EXPIRED),
            (InstructionStatus.FILLED, InstructionStatus.REJECTED),
            (InstructionStatus.REJECTED, InstructionStatus.FILLED),
            (InstructionStatus.EXPIRED, InstructionStatus.FILLED),
            (InstructionStatus.EXPIRED, InstructionStatus.REJECTED),
            # same-state amendments (MockBroker reverse + reapply)
            (InstructionStatus.FILLED, InstructionStatus.FILLED),
            (InstructionStatus.REJECTED, InstructionStatus.REJECTED),
        }
    )
)


def _is_post_close(at: datetime) -> bool:
    """Return True if ``at`` is after 16:00 Asia/Shanghai on the local day."""
    local = at.astimezone(_SH)
    cutoff = local.replace(
        hour=_POST_CLOSE_HHMM[0],
        minute=_POST_CLOSE_HHMM[1],
        second=0,
        microsecond=0,
    )
    return local > cutoff


def transition(
    plan: InstructionPlan,
    target: InstructionStatus,
    *,
    at: datetime,
    reason: str | None = None,
    allow_post_close: bool = False,
) -> InstructionPlan:
    """Move ``plan`` to ``target`` if the transition is allowed.

    Args:
        plan: current plan.
        target: requested next status.
        at: event time; the 16:00 freeze is judged here.
        reason: required when ``target`` is REJECTED or AMBIGUOUS
            (mirrors :class:`InstructionPlan` invariants).
        allow_post_close: scheduler-only escape hatch; default False so
            user-driven Feishu paths cannot bypass the freeze (P0-4
            §1.6).

    Returns:
        A new :class:`InstructionPlan` with the updated status.

    Raises:
        InvalidTransitionError: transition not in allowlist.
        PostCloseFreezeError: ``at`` is after 16:00 Asia/Shanghai and
            ``allow_post_close`` is False.
        ValueError: ``target`` requires a reason and none was supplied.
    """
    pair = (plan.status, target)
    if pair not in ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(
            f"{plan.instruction_id}: {plan.status.value} → {target.value} "
            f"not allowed"
        )
    if not allow_post_close and _is_post_close(at):
        raise PostCloseFreezeError(
            f"{plan.instruction_id}: {plan.status.value} → {target.value} "
            f"after 16:00 Asia/Shanghai is frozen"
        )

    needs_reason = target in {
        InstructionStatus.REJECTED,
        InstructionStatus.AMBIGUOUS,
    }
    update: dict[str, object | None] = {"status": target}
    if needs_reason:
        if not reason:
            raise ValueError(
                f"transition to {target.value} requires reason"
            )
        update["rejection_reason"] = reason
    else:
        # Clearing a stale rejection_reason when leaving REJECTED/AMBIGUOUS
        # keeps the InstructionPlan invariants (rejection_reason only
        # populated in those two statuses).
        update["rejection_reason"] = None
    return plan.model_copy(update=update)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "InvalidTransitionError",
    "PostCloseFreezeError",
    "transition",
]
