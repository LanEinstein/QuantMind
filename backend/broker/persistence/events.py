"""BrokerEvent — append-only delta rows for broker_events (E-002 / P1-2.A).

Each row represents one state transition on the MockBroker single
account mirror. The persistence store enforces insert-only semantics
so events are the authoritative audit + replay trail; the in-memory
broker state can always be rebuilt by ``BrokerSnapshot + replay events``.

The event types fall into four families:

* ``order_*`` — placed / filled / rejected / cancelled (one event per
  order lifecycle step the broker emits).
* ``execution_report_applied`` — user-submitted execution report flowed
  through ExecutionReportApplier (E-004) — carries the parsed kind +
  affected order / trade ids.
* ``manual_trade_applied`` — user-discretionary manual trade flowed through
  ManualTradeApplier (AD-005 / P1-2.A-amendment-2026-06-12). Reuses the
  *same* generic-delta wire format as ``execution_report_applied`` (so
  ``BROKER_EVENT_SCHEMA_VERSION`` is unchanged — no migration); recovery
  replays it through the identical delta path with the trade date parsed
  from the ``UT-`` external id.
* ``reconciliation_reset`` — user-decided reconciliation ticket called
  ReconciliationApplier::reset_to_snapshot (E-004) — carries the
  ticket_id and the snapshot reference id.
* ``account_initialized`` / ``day_advanced`` / ``mode_switch_reset`` —
  lifecycle events emitted by BrokerScheduler (E-005) / ModeRouter
  (D-005); not carrying any order_id.

Schema versioning: the wire format on disk carries a
``schema_version: int`` so a future structural change can be detected
without trying to parse a stale row. The store enforces the version
monotonically — downgrades raise (one of the 8 red lines).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

BROKER_EVENT_SCHEMA_VERSION = 1
"""Locked schema version for broker_events rows.

Bumping requires (a) a paired migration script under ``scripts/`` plus
(b) a P1-2.A amendment doc — the recovery loader rejects mixed-version
event streams to keep replay deterministic.
"""


class BrokerEventType(StrEnum):
    """Locked broker_events event_type set.

    Adding a new value is a schema-version bump; the recovery code path
    must learn to apply the new kind before the value is emitted at
    runtime. The store gives a clear error when an unknown event_type is
    loaded so a stale binary can't silently mis-replay.
    """

    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    EXECUTION_REPORT_APPLIED = "execution_report_applied"
    MANUAL_TRADE_APPLIED = "manual_trade_applied"
    RECONCILIATION_RESET = "reconciliation_reset"
    ACCOUNT_INITIALIZED = "account_initialized"
    DAY_ADVANCED = "day_advanced"
    MODE_SWITCH_RESET = "mode_switch_reset"


_EVENT_TYPES: frozenset[BrokerEventType] = frozenset(BrokerEventType)


class BrokerEvent(BaseModel):
    """Single append-only broker_events row.

    Frozen + strict + ``extra='forbid'`` so a typo in any caller fails
    at schema validation instead of silently writing a misshapen row.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=1)
    """Monotonic per-account sequence number assigned by BrokerEventStore.

    The store reads ``max(sequence) + 1`` under the same transaction
    that inserts the event so concurrent appenders never collide. The
    recovery loader reads events ordered by ``sequence`` (not
    timestamp) to remain deterministic across clock skews.
    """

    occurred_at: datetime
    schema_version: int = Field(default=BROKER_EVENT_SCHEMA_VERSION, ge=1)
    event_type: BrokerEventType
    order_id: str | None = Field(default=None, max_length=64)
    trade_id: str | None = Field(default=None, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=64)
    """Optional handle linking the event to an InstructionPlan or
    ReconciliationTicket — fed into decision_ledger correlation when
    the event applies to a known plan."""

    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_schema_version(self) -> BrokerEvent:
        if self.schema_version != BROKER_EVENT_SCHEMA_VERSION:
            raise ValueError(
                f"broker_event schema_version {self.schema_version} != "
                f"{BROKER_EVENT_SCHEMA_VERSION}; persistence module needs "
                "upgrade before reading"
            )
        if self.event_type not in _EVENT_TYPES:
            raise ValueError(f"unknown broker_event event_type {self.event_type!r}")
        return self


__all__ = [
    "BROKER_EVENT_SCHEMA_VERSION",
    "BrokerEvent",
    "BrokerEventType",
]
