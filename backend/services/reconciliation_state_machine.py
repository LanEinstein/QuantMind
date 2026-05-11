"""Reconciliation ticket state machine (P0-5 §1.5.3, B-004).

Owns the single source of truth for :class:`ReconciliationTicketStatus`
transitions. Direct ``model_copy(update={"status": ...})`` is a red
line — all callers go through :func:`transition_ticket`.

OPEN / EXPIRED tickets freeze the next trading day's BUY/SELL routing
(CLAUDE.md §2.7). Only the four RESOLVED_* statuses release the freeze.
"""

from __future__ import annotations

from datetime import datetime

from backend.models.reconciliation import (
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)


class InvalidTicketTransitionError(ValueError):
    """Requested ticket transition is not in the allowlist."""


_TS = ReconciliationTicketStatus
ALLOWED_TICKET_TRANSITIONS: frozenset[
    tuple[ReconciliationTicketStatus, ReconciliationTicketStatus]
] = frozenset(
    {
        (_TS.OPEN, _TS.RESOLVED_USER_AS_TRUTH),
        (_TS.OPEN, _TS.RESOLVED_SYSTEM_AS_TRUTH),
        (_TS.OPEN, _TS.RESOLVED_AMENDED),
        (_TS.OPEN, _TS.EXPIRED),
        # Late arbitration: users may still resolve after auto-expiry,
        # capped by the orchestrator at 16:00 of the next trading day.
        (_TS.EXPIRED, _TS.RESOLVED_USER_AS_TRUTH),
        (_TS.EXPIRED, _TS.RESOLVED_SYSTEM_AS_TRUTH),
        (_TS.EXPIRED, _TS.RESOLVED_AMENDED),
    }
)

_RESOLVED = frozenset(
    {
        _TS.RESOLVED_USER_AS_TRUTH,
        _TS.RESOLVED_SYSTEM_AS_TRUTH,
        _TS.RESOLVED_AMENDED,
    }
)


def transition_ticket(
    ticket: ReconciliationTicket,
    target: ReconciliationTicketStatus,
    *,
    at: datetime,
    resolution_message_id: str | None = None,
    amended_snapshot: MockBrokerSnapshot | None = None,
) -> ReconciliationTicket:
    """Move ``ticket`` to ``target`` if the transition is allowed.

    Args:
        ticket: current ticket.
        target: requested next status.
        at: resolution timestamp; recorded on the RESOLVED_* outputs.
        resolution_message_id: Feishu message id of the arbitration
            reply (required for any RESOLVED_*).
        amended_snapshot: required for RESOLVED_AMENDED, forbidden for
            the other RESOLVED_* / EXPIRED transitions.

    Returns:
        A new :class:`ReconciliationTicket` reflecting ``target``.

    Raises:
        InvalidTicketTransitionError: transition not in allowlist.
        ValueError: target requires resolution_message_id and/or
            amended_snapshot and the caller did not provide them.
    """
    pair = (ticket.status, target)
    if pair not in ALLOWED_TICKET_TRANSITIONS:
        raise InvalidTicketTransitionError(
            f"{ticket.ticket_id}: {ticket.status.value} → {target.value} "
            f"not allowed"
        )
    if target in _RESOLVED:
        if at < ticket.created_at:
            raise ValueError("resolution time must be >= ticket.created_at")
        if resolution_message_id is None:
            raise ValueError(
                f"transition to {target.value} requires resolution_message_id"
            )
        if target is _TS.RESOLVED_AMENDED:
            if amended_snapshot is None:
                raise ValueError(
                    "RESOLVED_AMENDED requires amended_snapshot"
                )
        else:
            if amended_snapshot is not None:
                raise ValueError(
                    f"target={target.value} must not carry amended_snapshot"
                )
        return ticket.model_copy(
            update={
                "status": target,
                "resolved_at": at,
                "resolution_message_id": resolution_message_id,
                "amended_snapshot": (
                    amended_snapshot if target is _TS.RESOLVED_AMENDED else None
                ),
            }
        )

    # EXPIRED
    if resolution_message_id is not None or amended_snapshot is not None:
        raise ValueError(
            "EXPIRED transition must not carry resolution fields"
        )
    return ticket.model_copy(update={"status": target})


def is_freeze_active(ticket: ReconciliationTicket) -> bool:
    """Whether this ticket currently freezes BUY/SELL routing.

    OPEN and EXPIRED keep the freeze; RESOLVED_* releases it (the
    next-day broker scheduler reads this on every order route).
    """
    return ticket.status in {_TS.OPEN, _TS.EXPIRED}


__all__ = [
    "ALLOWED_TICKET_TRANSITIONS",
    "InvalidTicketTransitionError",
    "is_freeze_active",
    "transition_ticket",
]
