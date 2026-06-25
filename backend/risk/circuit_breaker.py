"""Circuit breaker — halts trading after excessive losses.

This class maintains MUTABLE internal state (documented exception to
the project's immutability rule). The circuit breaker must track
consecutive losses and daily P&L across multiple trade events.
"""

from __future__ import annotations

import datetime as dt
import math

import structlog

from backend.broker.models import CircuitBreakerConfig
from backend.utils.trading_hours import SHANGHAI

log = structlog.get_logger(component="risk.circuit_breaker")


class CircuitBreaker:
    """Halts trading when loss thresholds are breached.

    Tracks daily cumulative P&L and consecutive losing trades.
    Enforces a cooldown period after halting.
    Call reset() at the start of each trading day.
    """

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        # NOTE: two writers with DIFFERENT semantics share this field.
        # ``record_trade_result`` ACCUMULATES per-trade pnl (the deferred
        # realized-PnL task / Batch1b); ``observe_daily_drawdown`` OVERWRITES it
        # with the NAV-based daily figure (P0-7-amendment-2026-06-23). Today only
        # ``observe_daily_drawdown`` has production callers; Batch1b MUST
        # reconcile the two (e.g. keep them on separate fields) before wiring
        # ``record_trade_result`` live, or an ``observe`` call would discard an
        # accumulated per-trade sum and vice-versa.
        self._daily_pnl_pct: float = 0.0
        self._consecutive_losses: int = 0
        self._halted_at: dt.datetime | None = None

    def record_trade_result(
        self,
        pnl_pct: float,
        now: dt.datetime | None = None,
    ) -> None:
        """Record a trade's P&L and check halt conditions.

        Args:
            pnl_pct: Trade P&L as pct of portfolio (-0.03 = -3%).
            now: Current time (injectable for testing).
        """
        if not math.isfinite(pnl_pct):
            log.error("invalid_pnl_pct", pnl_pct=pnl_pct)
            return

        self._daily_pnl_pct += pnl_pct

        if pnl_pct < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if self._halted_at is None:
            if self._should_halt():
                self._halted_at = now or dt.datetime.now(tz=SHANGHAI)
                log.error(
                    "circuit_breaker_tripped",
                    daily_pnl_pct=f"{self._daily_pnl_pct:.2%}",
                    consecutive_losses=self._consecutive_losses,
                )

    def observe_daily_drawdown(
        self,
        daily_pnl_pct: float,
        now: dt.datetime | None = None,
    ) -> None:
        """Trip the daily-loss halt from an externally-computed NAV drawdown.

        Used when realized per-trade P&L is unavailable
        (``P0-7-amendment-2026-06-23``): the mark-to-market NAV daily P&L
        (``(current_nav - day_open_nav) / day_open_nav``, computed by the caller
        from the EquityPoint store) is the authoritative daily-loss signal.

        Unlike :meth:`record_trade_result`, this *overwrites* the daily figure
        (NAV is a cumulative day-relative value, not a per-trade delta) and never
        touches the consecutive-loss counter. A trip latches the 60-min cooldown
        via the existing :meth:`is_halted` machinery. Non-finite input is ignored
        (fail-safe — never fabricate a halt or clear a real one).

        Args:
            daily_pnl_pct: day-open-NAV-relative P&L ratio (-0.05 = -5%).
            now: current time (injectable for testing).
        """
        if not math.isfinite(daily_pnl_pct):
            log.error("invalid_daily_pnl_pct", daily_pnl_pct=daily_pnl_pct)
            return

        self._daily_pnl_pct = daily_pnl_pct

        if self._halted_at is None and (
            self._daily_pnl_pct <= -self._config.daily_loss_limit_pct
        ):
            self._halted_at = now or dt.datetime.now(tz=SHANGHAI)
            log.error(
                "circuit_breaker_tripped_daily_drawdown",
                daily_pnl_pct=f"{self._daily_pnl_pct:.2%}",
            )

    def _should_halt(self) -> bool:
        """Check if either halt condition is met."""
        if self._daily_pnl_pct <= -self._config.daily_loss_limit_pct:
            return True
        if self._consecutive_losses >= self._config.consecutive_loss_count:
            return True
        return False

    def is_halted(self, now: dt.datetime | None = None) -> bool:
        """Check if trading is currently halted.

        Returns False if the cooldown period has expired.

        Args:
            now: Current time (injectable for testing).
        """
        if self._halted_at is None:
            return False

        if now is None:
            now = dt.datetime.now(tz=SHANGHAI)

        elapsed = now - self._halted_at
        cooldown = dt.timedelta(minutes=self._config.cooldown_minutes)

        if elapsed >= cooldown:
            self._expire_halt()
            return False
        return True

    def _expire_halt(self) -> None:
        """Reset all state when cooldown expires."""
        self._halted_at = None
        self._daily_pnl_pct = 0.0
        self._consecutive_losses = 0
        log.info("circuit_breaker_cooldown_expired")

    def reset(self) -> None:
        """Reset all counters. Call at the start of each trading day."""
        self._daily_pnl_pct = 0.0
        self._consecutive_losses = 0
        self._halted_at = None
        log.info("circuit_breaker_reset")
