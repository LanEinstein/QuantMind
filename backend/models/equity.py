"""EquityPoint — 30-second intraday MTM checkpoint (E-006 / P1-2.B §1.1).

The MockBroker single-mirror tells us "what we own"; ``BrokerSnapshot``
freezes the position state at EOD. Neither answers "what is our
portfolio worth RIGHT NOW" without consulting live quotes. The
:class:`EquityPoint` model bridges that gap:

* one row per 30-second tick during trading hours (plus an EOD_FALLBACK
  row at end-of-day to guarantee >=1 point per trading day);
* per-position breakdown so the front-end can highlight which holding
  drove the change;
* a ``last_broker_event_id`` reverse index so audit can join the MTM
  point to the broker_events row that last changed the underlying
  position state;
* a ``quality`` enum that records whether the prices came from Redis
  (fresh), Mongo (stale within window), or the last_known_cached
  fallback (degraded — flagged in the UI). ``cost_price`` is never a
  valid quality (P1-2.B §2 red line 6).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EquityPointQuality(StrEnum):
    """Source of the per-position prices that built this point."""

    FRESH = "FRESH"
    """All prices read from Redis within 60s window."""

    STALE = "STALE"
    """At least one price had to fall through to Mongo (60–300s)."""

    DEGRADED = "DEGRADED"
    """At least one price came from the last_known_cached fallback
    (>=300s old; UI must flag). NEVER ``cost_price`` — that is a red
    line (P1-2.B §2 redline 6)."""

    EOD_FALLBACK = "EOD_FALLBACK"
    """End-of-day backstop point written when no intraday tick fired
    (e.g. half-day trading holiday). Guarantees >=1 point per day so
    the acceptance window has a non-empty equity curve."""


class EquityPointPosition(BaseModel):
    """Per-stock detail row inside an :class:`EquityPoint`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    volume: int = Field(ge=0)
    cost_price: float = Field(ge=0.0)
    last_price: float = Field(gt=0.0)
    market_value: float = Field(ge=0.0)
    unrealized_pnl: float
    unrealized_pnl_pct: float
    price_quality: EquityPointQuality
    last_price_at: datetime | None = None
    """When the chosen ``last_price`` was sourced. ``None`` indicates
    the fallback path that has no timestamp (last_known_cached without
    an originating tick)."""


class EquityPoint(BaseModel):
    """One MTM row in the ``equity_points`` collection."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_at: datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    cash: float = Field(ge=0.0)
    frozen_cash: float = Field(ge=0.0)
    market_value: float = Field(ge=0.0)
    total_equity: float = Field(ge=0.0)
    initial_capital: float = Field(gt=0.0)
    pnl: float
    pnl_pct: float
    quality: EquityPointQuality
    positions: tuple[EquityPointPosition, ...] = Field(default_factory=tuple)
    policy_hash: str | None = Field(default=None, max_length=64)
    """AA-004 (P2-2-amendment-2026-06-12 §1.6): the policy-manifest hash
    active when this point was built. ``None`` = legacy segment."""
    last_broker_event_id: int | None = Field(default=None, ge=0)
    """Sequence of the broker_events row whose effect is reflected in
    this point's position state. ``None`` for a fresh deploy before
    any event has been written."""

    @model_validator(mode="after")
    def _check_total_equity_consistency(self) -> EquityPoint:
        derived = round(self.cash + self.frozen_cash + self.market_value, 2)
        if abs(derived - round(self.total_equity, 2)) > 0.05:
            raise ValueError(
                f"total_equity {self.total_equity} != "
                f"cash + frozen_cash + market_value {derived} (≥0.05 drift)"
            )
        return self

    @model_validator(mode="after")
    def _check_no_duplicate_codes(self) -> EquityPoint:
        seen: set[str] = set()
        for pos in self.positions:
            if pos.code in seen:
                raise ValueError(
                    f"duplicate position code {pos.code} in EquityPoint"
                )
            seen.add(pos.code)
        return self


__all__ = [
    "EquityPoint",
    "EquityPointPosition",
    "EquityPointQuality",
]
