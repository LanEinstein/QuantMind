"""Holiday-calendar fail-closed staleness guard (P0-6-amendment-2026-06-23)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from backend.utils.holiday_loader import (
    CalendarStaleError,
    HolidayTable,
    assert_calendar_covers,
    calendar_forward_warning,
    calendar_staleness_reason,
)


def _dates_in_year(year: int, n: int) -> set[dt.date]:
    """``n`` distinct dates all inside ``year`` (consecutive from Jan 1)."""
    base = dt.date(year, 1, 1)
    return {base + dt.timedelta(days=i) for i in range(n)}


def _table(
    *, holidays: set[dt.date], last_verified: dt.date | None = None
) -> HolidayTable:
    return HolidayTable(
        holidays=frozenset(holidays),
        makeup_workdays=frozenset(),
        schedule_version=1,
        last_verified=last_verified,
        source_path=Path("test"),
    )


def test_curated_year_passes() -> None:
    tbl = _table(holidays=_dates_in_year(2026, 12))
    today = dt.date(2026, 6, 25)
    assert calendar_staleness_reason(today, table=tbl) is None
    assert_calendar_covers(today, table=tbl)  # must not raise


def test_placeholder_year_is_stale() -> None:
    tbl = _table(holidays=_dates_in_year(2027, 1))  # only 元旦 → placeholder
    today = dt.date(2027, 3, 1)
    reason = calendar_staleness_reason(today, table=tbl)
    assert reason is not None and "holidays_2027" in reason
    with pytest.raises(CalendarStaleError):
        assert_calendar_covers(today, table=tbl)


def test_boundary_min_passes_one_below_fails() -> None:
    today = dt.date(2026, 6, 25)
    ok = _table(holidays=_dates_in_year(2026, 10))  # exactly the floor
    assert calendar_staleness_reason(today, table=ok) is None
    short = _table(holidays=_dates_in_year(2026, 9))  # one below the floor
    assert calendar_staleness_reason(today, table=short) is not None


def test_december_forward_warning_is_soft_not_a_boot_block() -> None:
    holidays = _dates_in_year(2026, 12) | _dates_in_year(2027, 1)
    tbl = _table(holidays=holidays)
    # Current year (2026) is curated → NOT stale, even in December, so boot is
    # never blocked by a missing next-year block (the Dec-2026 test/prod time-bomb
    # the codex review caught).
    assert calendar_staleness_reason(dt.date(2026, 12, 1), table=tbl) is None
    assert_calendar_covers(dt.date(2026, 12, 15), table=tbl)  # must NOT raise
    # December: next-year placeholder is a SOFT warning (ops should backfill).
    assert calendar_forward_warning(dt.date(2026, 12, 1), table=tbl) is not None
    # Mid-year: no forward warning yet (notice not even published).
    assert calendar_forward_warning(dt.date(2026, 6, 25), table=tbl) is None


def test_missing_year_block_is_stale() -> None:
    tbl = _table(holidays=_dates_in_year(2026, 12))  # nothing for 2028
    assert calendar_staleness_reason(dt.date(2028, 4, 1), table=tbl) is not None
