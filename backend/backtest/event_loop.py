"""Deterministic event-loop primitives (clean-room, AE-004 §2.2).

Clean-room re-implementation of the look-ahead-free ideas QuantMind borrows
from the top OSS engines (no vendor code copied):

* **nautilus monotonic ``ts_init`` clock** — the simulation clock only ever
  advances. The exchange cannot serve a bar dated after the clock, so reading
  the future is *physically* impossible, not merely discouraged
  (:class:`BacktestClock` raises :class:`ClockViolationError` on a forward read).
* **zipline "pending order → next bar" barrier** — orders a strategy decides on
  day *T*'s close fill on day *T+1*'s open, by construction. The loop in
  :mod:`backend.backtest.harness` enforces the barrier; this module supplies the
  ordered, monotonic day cursor it walks.

A :class:`DayBar` is an integer-分 OHLC bar plus the metadata the fill path
needs: average daily volume (capacity), the pre-computed ±limit prices (the
qlib 涨跌停方向门 gate compares against these — the limit *computation* lives
with the PIT/board data in the bar source), the board (slippage tier) and the
SZ transfer-fee toggle. No floats on the decision path; no wall-clock, no RNG.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_DATE_LEN = 8
"""Trade dates are ``YYYYMMDD`` (lexicographic order == chronological)."""


class ClockViolationError(RuntimeError):
    """Raised on a non-monotonic advance or a forward (look-ahead) read."""


@dataclass(frozen=True)
class DayBar:
    """One code's trading-day bar, integer 分 (immutable, PIT as-of).

    ``limit_up_cents`` / ``limit_down_cents`` are pre-computed by the bar source
    (production: ``get_price_limits`` over the prev close; tests: synthetic) so
    the engine's direction gate is a pure integer comparison. ``adv_volume`` is
    the average daily volume in shares (the harsh-fill capacity reference).
    """

    code: str
    trade_date: str
    open_cents: int
    high_cents: int
    low_cents: int
    close_cents: int
    adv_volume: float
    limit_up_cents: int
    limit_down_cents: int
    board: str
    transfer_fee_applies: bool

    def __post_init__(self) -> None:
        if len(self.trade_date) != _DATE_LEN or not self.trade_date.isdigit():
            raise ValueError(f"trade_date must be YYYYMMDD, got {self.trade_date!r}")
        for name in ("open_cents", "high_cents", "low_cents", "close_cents"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0 for {self.code}")
        if self.adv_volume < 0:
            raise ValueError(f"adv_volume must be >= 0 for {self.code}")

    @property
    def at_limit_up(self) -> bool:
        """Open is at/above the upper limit — a BUY cannot fill (no sellers)."""
        return self.open_cents >= self.limit_up_cents

    @property
    def at_limit_down(self) -> bool:
        """Open is at/below the lower limit — a SELL cannot fill (no buyers)."""
        return self.open_cents <= self.limit_down_cents


@runtime_checkable
class BarSource(Protocol):
    """As-of bar provider (injected). Look-ahead-free by contract.

    ``bars_on(day)`` returns only that day's bars — never a future day's — so
    the engine cannot read ahead through it. Production wires a PIT-snapshot
    reader; tests feed synthetic bars.
    """

    def trading_days(self) -> tuple[str, ...]:
        """Ascending, de-duplicated ``YYYYMMDD`` days covered."""
        ...

    def bars_on(self, day: str) -> Mapping[str, DayBar]: ...


class BacktestClock:
    """Monotonic simulation clock over an ordered set of trading days.

    Advances strictly forward (nautilus ``ts_init``). A read of a day after the
    current cursor is a look-ahead bug → :class:`ClockViolationError`.
    """

    def __init__(self, trading_days: tuple[str, ...]) -> None:
        ordered = sorted(set(trading_days))
        if not ordered:
            raise ClockViolationError("clock needs at least one trading day")
        for day in ordered:
            if len(day) != _DATE_LEN or not day.isdigit():
                raise ClockViolationError(f"bad trade date {day!r}")
        self._days: tuple[str, ...] = tuple(ordered)
        self._idx = 0

    @property
    def days(self) -> tuple[str, ...]:
        return self._days

    @property
    def current_day(self) -> str:
        return self._days[self._idx]

    @property
    def exhausted(self) -> bool:
        return self._idx >= len(self._days) - 1

    def advance(self) -> str | None:
        """Move to the next day; return it, or ``None`` past the last day."""
        if self._idx >= len(self._days) - 1:
            return None
        self._idx += 1
        return self._days[self._idx]

    def assert_readable(self, day: str) -> None:
        """Guard: ``day`` must not be after the current cursor (no look-ahead).

        Raises:
            ClockViolationError: ``day`` is later than the current clock day.
        """
        if day > self.current_day:
            raise ClockViolationError(
                f"look-ahead read: {day} is after clock at {self.current_day}"
            )


__all__ = [
    "BacktestClock",
    "BarSource",
    "ClockViolationError",
    "DayBar",
]
