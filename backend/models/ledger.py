"""decision_ledger model — single correlation graph keyed by instruction_id.

P0-3 §3.1 + P0-1 §3.2 require every executable plan to be traceable from
multi-Agent analysis through risk validation, broker simulation, Feishu
human execution, user execution report, daily reconciliation, and the
45-trading-day acceptance window. The decision_ledger is that join
table — one document per instruction_id, mutated only by appending
events (never in-place) so the audit trail stays complete.

Event types and field names are locked here so audit queries, Phase B
ledger service, Phase E broker, Phase F Feishu, Phase I acceptance can
all read the same shape. New event kinds need a `B-002-amendment-*.md`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LedgerEventKind(StrEnum):
    """Append-only event types in the decision_ledger.

    Each kind corresponds to one observable lifecycle step. The set is
    closed under P0-3 §3.1; adding a new kind requires an amendment so
    the front-end timeline view and audit queries stay deterministic.
    """

    PLAN_DRAFTED = "PLAN_DRAFTED"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    PLAN_REJECTED = "PLAN_REJECTED"
    PLAN_DISPATCHED = "PLAN_DISPATCHED"
    BROKER_FILLED = "BROKER_FILLED"
    BROKER_EXPIRED = "BROKER_EXPIRED"
    FEISHU_SENT = "FEISHU_SENT"
    EXECUTION_REPORT_PARSED = "EXECUTION_REPORT_PARSED"
    EXECUTION_REPORT_AMBIGUOUS = "EXECUTION_REPORT_AMBIGUOUS"
    RECONCILIATION_OPENED = "RECONCILIATION_OPENED"
    RECONCILIATION_DECIDED = "RECONCILIATION_DECIDED"
    ACCEPTANCE_INCLUDED = "ACCEPTANCE_INCLUDED"


class LedgerEvent(BaseModel):
    """One append-only event in a decision_ledger entry."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    kind: LedgerEventKind
    at: datetime
    actor: str = Field(min_length=1, max_length=32)
    """Who emitted the event: ``SYSTEM`` / ``SCHEDULER`` / ``FEISHU_USER`` /
    ``FRONTEND_USER``. LLM is never an allowed actor (P0-10 + P1-6 audit
    write rules); the service layer enforces the allowlist."""

    payload: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    """Structured event data — restricted to JSON-scalar values so the
    ledger never embeds nested LLM-written objects. Free-text reasoning
    belongs in ``evidence`` collections, referenced by ``evidence_id``."""


class DecisionLedgerEntry(BaseModel):
    """One row of the decision_ledger collection.

    The document is keyed on ``instruction_id`` (unique). ``events`` is
    the append-only history; the top-level reference fields are
    convenience pointers so a front-end detail page can resolve the
    related collections without scanning the event list.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # === primary key + correlation IDs ===
    instruction_id: str = Field(
        pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$"
    )
    """Unique correlation_id across the lifecycle (P0-3 §1.2)."""

    analysis_record_id: str = Field(min_length=1, max_length=64)
    signal_id: str = Field(min_length=1, max_length=64)
    risk_validation_id: str | None = Field(default=None, max_length=64)
    broker_order_id: str | None = Field(default=None, max_length=64)
    trade_ids: tuple[str, ...] = Field(default_factory=tuple)
    feishu_message_id: str | None = Field(default=None, max_length=128)
    execution_report_id: str | None = Field(default=None, max_length=64)
    reconciliation_ticket_id: str | None = Field(default=None, max_length=64)
    acceptance_report_id: str | None = Field(default=None, max_length=64)

    # === audit trail ===
    events: tuple[LedgerEvent, ...]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _check_events_non_empty_and_ordered(self) -> DecisionLedgerEntry:
        if not self.events:
            raise ValueError("decision_ledger entry requires at least one event")
        # Events must be chronologically monotonic — out-of-order events
        # imply concurrent writes without serialization.
        prev = self.events[0].at
        for ev in self.events[1:]:
            if ev.at < prev:
                raise ValueError(
                    "events must be non-decreasing in event time"
                )
            prev = ev.at
        return self

    @model_validator(mode="after")
    def _check_timestamps(self) -> DecisionLedgerEntry:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be >= created_at")
        if self.events[-1].at > self.updated_at:
            raise ValueError("updated_at must be >= last event time")
        return self


__all__ = [
    "DecisionLedgerEntry",
    "LedgerEvent",
    "LedgerEventKind",
]
