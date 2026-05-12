"""A-share trading calendar walks built on ``backend.utils.holiday_loader`` (C-007).

Provides the prev/next walk + window helpers consumed by
``compute_acceptance_window`` (P0-6 45-trading-day window) and the
``BrokerScheduler`` ``advance_day`` cron. Lives under ``backend/data``
so callers outside the risk-isolation boundary can import the
forward-looking helpers; the predicate-only surface stays in
``backend/utils/trading_hours`` so ``backend/risk`` can keep its
import set free of ``backend.data``.
"""

from __future__ import annotations

import datetime as dt

from backend.utils.holiday_loader import (
    HolidayTable,
    get_holiday_table,
    reload_holiday_table,
)
from backend.utils.trading_hours import is_trading_day

# Re-export for callers that want a single import surface.
TradingCalendar = HolidayTable
get_calendar = get_holiday_table
reload_calendar = reload_holiday_table


_MAX_STEPS = 366
"""Hard upper bound on a single-walk loop. Two consecutive years of all
non-trading days would still terminate well within this bound; if the
loop ever hits it, the calendar YAML is malformed."""


def is_holiday(date: dt.date) -> bool:
    return date in get_holiday_table().holidays


def is_makeup_workday(date: dt.date) -> bool:
    return date in get_holiday_table().makeup_workdays


def prev_trading_day(date: dt.date) -> dt.date:
    """Return the most recent trading day strictly before ``date``."""
    candidate = date - dt.timedelta(days=1)
    for _ in range(_MAX_STEPS):
        if is_trading_day(candidate):
            return candidate
        candidate -= dt.timedelta(days=1)
    raise RuntimeError(
        f"no trading day found in {_MAX_STEPS}d walking back from {date}"
    )


def next_trading_day(date: dt.date) -> dt.date:
    """Return the next trading day strictly after ``date``."""
    candidate = date + dt.timedelta(days=1)
    for _ in range(_MAX_STEPS):
        if is_trading_day(candidate):
            return candidate
        candidate += dt.timedelta(days=1)
    raise RuntimeError(
        f"no trading day found in {_MAX_STEPS}d walking forward from {date}"
    )


def count_trading_days(start: dt.date, end: dt.date) -> int:
    """Count trading days in the half-open interval ``[start, end)``."""
    if start >= end:
        return 0
    count = 0
    cursor = start
    while cursor < end:
        if is_trading_day(cursor):
            count += 1
        cursor += dt.timedelta(days=1)
    return count


def compute_window_back(end: dt.date, n_trading_days: int) -> dt.date:
    """Return the start date covering ``n_trading_days`` ending on ``end``.

    Used by the P0-6 45-trading-day acceptance window:
    ``compute_window_back(today, 45)`` returns the earliest of the 45
    trading days such that ``[start, end + 1d)`` contains exactly
    ``n_trading_days`` trading days — provided ``end`` is itself a
    trading day.
    """
    if n_trading_days < 1:
        raise ValueError("n_trading_days must be >= 1")
    remaining = n_trading_days
    cursor = end
    last_trading: dt.date | None = None
    for _ in range(_MAX_STEPS * 2):
        if is_trading_day(cursor):
            last_trading = cursor
            remaining -= 1
            if remaining == 0:
                return cursor
        cursor -= dt.timedelta(days=1)
    raise RuntimeError(
        f"cannot find {n_trading_days} trading days walking back from {end} "
        f"(last hit: {last_trading})"
    )


__all__ = [
    "TradingCalendar",
    "compute_window_back",
    "count_trading_days",
    "get_calendar",
    "is_holiday",
    "is_makeup_workday",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "reload_calendar",
]
