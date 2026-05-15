"""BrokerSnapshot — full account state checkpoint (E-002 / P1-2.A).

Snapshots are written by the EOD pipeline (BrokerScheduler 16:00:30
chain) after every delta event for the trading day has been applied.
The snapshot captures cash + frozen_cash + positions + last_sequence
+ a deterministic SHA256 checksum over the canonical state payload;
the recovery loader verifies the checksum before adopting the
snapshot, and fail-closes when it mismatches so a corrupted
checkpoint cannot drive the broker into an inconsistent state.

Snapshots are versioned (``schema_version``) so a structural change
to the on-disk format can be detected without trying to parse a
stale row. The store enforces monotonic versions — downgrades are a
red line.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

BROKER_SNAPSHOT_SCHEMA_VERSION = 1
"""Locked schema version for broker_snapshots rows. See events.py for
the bump procedure (paired with a P1-2.A amendment doc + migration)."""


class BrokerSnapshotPosition(BaseModel):
    """Single position row inside a snapshot.

    Mirrors :class:`backend.broker.models.Position` but is encoded with
    the broker-internal layout (no derived market_value / pnl fields —
    those are recomputed at MTM time by E-006).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    volume: int = Field(ge=0)
    today_bought_volume: int = Field(ge=0)
    cost_price: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_today_le_total(self) -> BrokerSnapshotPosition:
        if self.today_bought_volume > self.volume:
            raise ValueError(
                f"today_bought_volume {self.today_bought_volume} "
                f"exceeds total volume {self.volume} (code {self.code})"
            )
        return self


class BrokerSnapshot(BaseModel):
    """Full MockBroker single-mirror state checkpoint.

    Recovery contract:

    * ``last_event_sequence`` records the sequence number of the last
      ``broker_events`` row applied **into** this snapshot. The
      recovery loader replays events with ``sequence >
      last_event_sequence`` on top of the snapshot to reach current
      state.
    * ``checksum`` is the deterministic SHA256[:16] over the canonical
      state payload (cash, frozen_cash, positions sorted by code). The
      loader recomputes it and refuses the snapshot on mismatch.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    schema_version: int = Field(default=BROKER_SNAPSHOT_SCHEMA_VERSION, ge=1)
    last_event_sequence: int = Field(ge=0)
    """Sequence of the last broker_events row applied into this
    snapshot. ``0`` means "no events yet" (initial account state)."""

    cash: float = Field(ge=0.0)
    frozen_cash: float = Field(ge=0.0)
    initial_capital: float = Field(gt=0.0)
    positions: tuple[BrokerSnapshotPosition, ...] = Field(default_factory=tuple)
    checksum: str = Field(pattern=r"^[0-9a-f]{16}$")
    """SHA256[:16] over canonical_state_payload. Locked to 16 hex chars
    so a typo / truncation immediately fails the regex."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form provenance: scheduler run_id, sourceCommitHash, etc.
    Not part of the checksum so adding metadata after the fact does not
    silently invalidate the snapshot."""

    @model_validator(mode="after")
    def _check_schema_version(self) -> BrokerSnapshot:
        if self.schema_version != BROKER_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                f"broker_snapshot schema_version {self.schema_version} != "
                f"{BROKER_SNAPSHOT_SCHEMA_VERSION}; persistence module "
                "needs upgrade before reading"
            )
        return self

    @model_validator(mode="after")
    def _check_no_duplicate_codes(self) -> BrokerSnapshot:
        seen: set[str] = set()
        for pos in self.positions:
            if pos.code in seen:
                raise ValueError(
                    f"snapshot contains duplicate position code {pos.code}"
                )
            seen.add(pos.code)
        return self


__all__ = [
    "BROKER_SNAPSHOT_SCHEMA_VERSION",
    "BrokerSnapshot",
    "BrokerSnapshotPosition",
]
