"""DailyTradingState — pure data type carrying daily counters for 14-check.

RiskEngine is a pure function with no IO (P0-1 §2 redline 8 / P0-10 §2
redline 9). Check 10/12/13/14 still need per-day counters and the current
quote — InstructionPlanBuilder assembles those externally (from MockBroker,
decision_ledger, quote provider) and hands them in as ``DailyTradingState``.

Lives in ``backend/risk/`` so ``backend/risk/engine.py`` can import it
without touching ``backend/data``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DailyTradingState:
    """Daily counters consumed by RiskEngine 14-check checks 10/12/13/14.

    Why frozen + slots: the engine treats this as a value object (matches
    the broker/risk immutability convention) and slots blocks accidental
    field additions that would silently bypass schema review.
    """

    today_new_instruction_count: int
    """Count of dispatched BUY+SELL InstructionPlans today. HOLD never
    routes (P0-3) so it is not counted; DRAFT/REJECTED are not counted
    either — only confirmed-dispatched consumes the daily 5-slot budget
    (otherwise the 6th candidate would always fail because itself sits in
    the counter)."""

    today_portfolio_pnl_pct: float
    """Cumulative day-open-NAV-relative PnL ratio
    ``(current_nav - day_open_nav) / day_open_nav``. Negative = loss."""

    last_3_trade_pnls: tuple[float, ...]
    """Up-to-N (default 3) most recent FILLED trade PnLs (oldest→newest).
    Initial trading day may have ``len < N``; check 14 PASSes in that case
    (cannot evaluate "last N consecutive losses" without enough data)."""

    current_price: float | None
    """Live spot price for the order's stock — required by check 12 to
    decide whether the stock is at limit-up / limit-down. ``None`` means
    the quote pipeline failed; check 12 must fail-closed (REJECTED) and
    never pass on optimistic-by-default fallback."""

    is_in_halt_cooldown: bool
    """Whether the circuit-breaker singleton currently reports the system
    inside a cooldown window (``now < halt_until``)."""

    halt_until: datetime | None
    """Wall-clock moment at which the halt expires. ``None`` whenever
    ``is_in_halt_cooldown`` is False — the two fields are kept in sync by
    the assembler. Should be timezone-aware (Asia/Shanghai)."""
