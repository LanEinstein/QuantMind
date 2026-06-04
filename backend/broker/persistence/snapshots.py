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

import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

BROKER_SNAPSHOT_SCHEMA_VERSION = 2
"""Locked schema version for broker_snapshots rows. See events.py for
the bump procedure (paired with a P1-2.A amendment doc + migration).

v2 (P0-4-amendment-2026-06-04): positions gained ``bought_by_date``
(per-trade-date buy volumes, ISO-date keys) so the external-report T+1
guard survives a restart from a checkpoint spanning multi-day buys. The
read path accepts v1 rows (the field defaults empty; the checksum
payload is byte-identical for empty maps, so stored v1 checksums still
validate); the write path always emits the current version."""


_ISO_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


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
    bought_by_date: dict[str, int] = Field(default_factory=dict)
    """Per-trade-date buy volumes (ISO ``YYYY-MM-DD`` keys) consumed by
    the external-report T+1 guard (P0-4-amendment-2026-06-04). Empty on
    v1 rows — recovery then falls back to the today_bought_volume
    reseed for the snapshot's own trade date."""

    @model_validator(mode="after")
    def _check_today_le_total(self) -> BrokerSnapshotPosition:
        if self.today_bought_volume > self.volume:
            raise ValueError(
                f"today_bought_volume {self.today_bought_volume} "
                f"exceeds total volume {self.volume} (code {self.code})"
            )
        return self

    @model_validator(mode="after")
    def _check_bought_by_date(self) -> BrokerSnapshotPosition:
        for key, vol in self.bought_by_date.items():
            # Parse, don't just pattern-match: '2026-02-30' satisfies the
            # regex but would crash recovery's date.fromisoformat — reject
            # the corrupt row at READ time instead (fail-closed here, clean
            # pydantic error rather than a deep ValueError mid-recovery).
            if not re.match(_ISO_DATE_RE, key):
                raise ValueError(
                    f"bought_by_date key {key!r} is not an ISO date "
                    f"(code {self.code})"
                )
            try:
                datetime.strptime(key, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(
                    f"bought_by_date key {key!r} is not a real calendar "
                    f"date (code {self.code})"
                ) from exc
            if vol < 0:
                raise ValueError(
                    f"bought_by_date[{key}] is negative (code {self.code})"
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
        # Read-compat: accept PRIOR versions (their new fields default —
        # v1 rows parse with empty bought_by_date and their stored
        # checksums still validate, byte-identical payload). A FUTURE
        # version means this module is too old to interpret the row —
        # fail-closed exactly as before (P0-4-amendment-2026-06-04).
        if self.schema_version > BROKER_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                f"broker_snapshot schema_version {self.schema_version} > "
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
