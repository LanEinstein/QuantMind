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
