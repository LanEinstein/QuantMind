"""Reconciliation models (P0-5 §1.1.2 / §1.5.1, B-004).

Captures the daily reconciliation message flow:

* :class:`DailyReconciliation` — user's end-of-day mirror, the
  authoritative *reference* for the deviation check (P0-5 §1.1.2).
* :class:`DeviationReport` — output of the threshold check, embedded in
  the ticket so the front-end can render "what went wrong" without
  re-running the comparison.
* :class:`ReconciliationTicket` — fail-closed lifecycle row stored in
  the ``reconciliation_tickets`` collection. ``OPEN`` / ``EXPIRED``
  tickets are the fifth Buy/Sell freeze source (CLAUDE.md §2.7).

All models frozen + strict + extra='forbid' (CLAUDE.md §2.7).
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

TICKET_ID_PATTERN = r"^RECON-\d{8}-\d{3}$"


class ReconciliationTicketStatus(StrEnum):
    """Ticket lifecycle (P0-5 §1.5.1).

    OPEN tickets freeze the next trading day's BUY/SELL routing
    (CLAUDE.md §2.7). EXPIRED tickets keep the freeze until the user
    files a late arbitration; only RESOLVED_* clears it.
    """

    OPEN = "OPEN"
    RESOLVED_USER_AS_TRUTH = "RESOLVED_USER_AS_TRUTH"
    RESOLVED_SYSTEM_AS_TRUTH = "RESOLVED_SYSTEM_AS_TRUTH"
    RESOLVED_AMENDED = "RESOLVED_AMENDED"
    EXPIRED = "EXPIRED"


class ReportedPosition(BaseModel):
    """User-reported single-stock position (by-value)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    volume: int = Field(ge=0)
    cost_price: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_volume_lot(self) -> ReportedPosition:
        if self.volume % 100 != 0:
            raise ValueError(
                f"reported volume {self.volume} for {self.code} "
                f"must be a multiple of 100"
            )
        return self


class MockBrokerSnapshot(BaseModel):
    """Reference snapshot of the MockBroker mirror at 16:00 cutoff.

    This shape is the contract between Phase E (broker) and this Phase
    B-004 reconciliation surface. The full persistence story (event log
    + delta + EOD snapshot) is owned by P1-2.A and lands in Phase E.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    cash: float = Field(ge=0.0)
    positions: tuple[ReportedPosition, ...] = Field(default_factory=tuple)
    snapshot_at: datetime


class DailyReconciliation(BaseModel):
    """User-reported end-of-day account mirror (P0-5 §1.1.2)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    ticket_id: str = Field(pattern=TICKET_ID_PATTERN)
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_at: datetime

    reported_cash: float = Field(ge=0.0, le=1e10)
    reported_positions: tuple[ReportedPosition, ...] = Field(default_factory=tuple)

    raw_text: str = Field(min_length=1, max_length=4096)
    parse_ok: bool = True

    @model_validator(mode="after")
    def _check_unique_codes(self) -> DailyReconciliation:
        seen: set[str] = set()
        for p in self.reported_positions:
            if p.code in seen:
                raise ValueError(
                    f"duplicate code {p.code} in reported_positions"
                )
            seen.add(p.code)
        return self


class FieldDeviation(BaseModel):
    """Per-field deviation entry inside a :class:`DeviationReport`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    expected: str = Field(max_length=64)
    actual: str = Field(max_length=64)
    abs_diff: float
    threshold: float = Field(ge=0.0)
    passed: bool


class DeviationReport(BaseModel):
    """Output of :func:`detect_deviations`; embedded in the ticket."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    ticket_id: str = Field(pattern=TICKET_ID_PATTERN)
    overall_passed: bool
    deviations: tuple[FieldDeviation, ...]


class ReconciliationTicket(BaseModel):
    """fail-closed reconciliation ticket (P0-5 §1.5.1)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    ticket_id: str = Field(pattern=TICKET_ID_PATTERN)
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    created_at: datetime
    deviation_report: DeviationReport
    expected_snapshot_id: str = Field(min_length=1, max_length=64)
    actual_reconciliation_id: str = Field(min_length=1, max_length=64)

    status: ReconciliationTicketStatus = ReconciliationTicketStatus.OPEN
    resolved_at: datetime | None = None
    resolution_message_id: str | None = Field(default=None, max_length=128)
    amended_snapshot: MockBrokerSnapshot | None = None

    @model_validator(mode="after")
    def _check_ticket_consistency(self) -> ReconciliationTicket:
        terminal = {
            ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_AMENDED,
        }
        if self.status in terminal:
            if self.resolved_at is None:
                raise ValueError(
                    f"status={self.status.value} requires resolved_at"
                )
            if self.resolved_at < self.created_at:
                raise ValueError("resolved_at must be >= created_at")
        else:
            if self.resolved_at is not None:
                raise ValueError(
                    f"status={self.status.value} must not set resolved_at"
                )
        if self.status is ReconciliationTicketStatus.RESOLVED_AMENDED:
            if self.amended_snapshot is None:
                raise ValueError(
                    "RESOLVED_AMENDED requires amended_snapshot"
                )
        else:
            if self.amended_snapshot is not None:
                raise ValueError(
                    f"status={self.status.value} must not set amended_snapshot"
                )
        if self.deviation_report.ticket_id != self.ticket_id:
            raise ValueError(
                "deviation_report.ticket_id must match ticket.ticket_id"
            )
        return self


# Thresholds locked by P0-5 §1.4.1 (must stay positive + finite).
CASH_TOLERANCE_CNY: float = 1.0
COST_PRICE_TOLERANCE_CNY: float = 0.01
assert math.isfinite(CASH_TOLERANCE_CNY) and CASH_TOLERANCE_CNY > 0
assert math.isfinite(COST_PRICE_TOLERANCE_CNY) and COST_PRICE_TOLERANCE_CNY > 0


__all__ = [
    "CASH_TOLERANCE_CNY",
    "COST_PRICE_TOLERANCE_CNY",
    "DailyReconciliation",
    "DeviationReport",
    "FieldDeviation",
    "MockBrokerSnapshot",
    "ReconciliationTicket",
    "ReconciliationTicketStatus",
    "ReportedPosition",
    "TICKET_ID_PATTERN",
]
