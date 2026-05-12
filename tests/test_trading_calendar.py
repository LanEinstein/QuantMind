"""C-007 — holidays.yaml loader + trading-calendar walks.

Tests cover:
* ``holiday_loader`` reads ``config/holidays.yaml`` once + caches.
* ``QUANTMIND_HOLIDAYS_PATH`` env override drives test fixtures.
* ``is_trading_day`` three-tier rule (makeup overrides weekend, then
  holiday closes, else weekday default).
* ``prev_trading_day`` / ``next_trading_day`` skip weekends + holidays
  + accept makeup workdays.
* ``count_trading_days`` half-open semantics.
* ``compute_window_back`` (P0-6 45-trading-day acceptance window).
* Calendar load detects overlap between holidays + makeup + raises
  on malformed YAML root.
"""

from __future__ import annotations

import datetime as dt
import importlib
import textwrap
from pathlib import Path

import pytest

import backend.utils.holiday_loader as loader_mod
from backend.utils import trading_hours

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_calendar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tiny YAML with two holidays + one makeup workday for deterministic tests.

    The default ``config/holidays.yaml`` 2026 schedule may evolve as ops
    revises it; to keep these tests stable we point the loader at a
    fixture-controlled file.
    """
    fixture = tmp_path / "holidays.yaml"
    fixture.write_text(
        textwrap.dedent(
            """
            schedule_version: 99
            last_verified: "2026-05-12"

            holidays_2026:
              - {date: "2026-05-04", name: "test-holiday"}
              - {date: "2026-05-05", name: "test-holiday"}

            makeup_workdays_2026:
              - "2026-05-09"  # Saturday becomes a trading day
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANTMIND_HOLIDAYS_PATH", str(fixture))
    # Force a fresh load against the new path.
    loader_mod._cache = None  # type: ignore[attr-defined]
    importlib.reload(trading_hours)
    yield fixture
    loader_mod._cache = None  # type: ignore[attr-defined]
    importlib.reload(trading_hours)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_loader_caches(synthetic_calendar: Path) -> None:
    table_a = loader_mod.get_holiday_table()
    table_b = loader_mod.get_holiday_table()
    assert table_a is table_b
    assert table_a.schedule_version == 99
    assert table_a.source_path == Path(str(synthetic_calendar)).expanduser().resolve()


def test_loader_reload_picks_up_edits(
    synthetic_calendar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader_mod.get_holiday_table()
    synthetic_calendar.write_text(
        textwrap.dedent(
            """
            schedule_version: 100
            holidays_2026: []
            makeup_workdays_2026: []
            """
        ).strip(),
        encoding="utf-8",
    )
    refreshed = loader_mod.reload_holiday_table()
    assert refreshed.schedule_version == 100
    assert refreshed.holidays == frozenset()


def test_loader_rejects_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "bad.yaml"
    fixture.write_text(
        textwrap.dedent(
            """
            schedule_version: 1
            holidays_2026:
              - {date: "2026-05-09", name: "x"}
            makeup_workdays_2026:
              - "2026-05-09"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANTMIND_HOLIDAYS_PATH", str(fixture))
    loader_mod._cache = None  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="overlapping"):
        loader_mod.reload_holiday_table()
    loader_mod._cache = None  # type: ignore[attr-defined]


def test_loader_rejects_non_mapping_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "bad.yaml"
    fixture.write_text("- a\n- b\n", encoding="utf-8")
    monkeypatch.setenv("QUANTMIND_HOLIDAYS_PATH", str(fixture))
    loader_mod._cache = None  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="mapping"):
        loader_mod.reload_holiday_table()
    loader_mod._cache = None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# is_trading_day three-tier rule
# ---------------------------------------------------------------------------


def test_makeup_workday_overrides_weekend(synthetic_calendar: Path) -> None:
    # 2026-05-09 is Sat AND in makeup_workdays — should be trading.
    sat = dt.date(2026, 5, 9)
    assert sat.weekday() == 5  # Saturday
    assert trading_hours.is_trading_day(sat) is True


def test_holiday_closes_weekday(synthetic_calendar: Path) -> None:
    # 2026-05-04 is Monday but in holidays — non-trading.
    mon = dt.date(2026, 5, 4)
    assert mon.weekday() == 0
    assert trading_hours.is_trading_day(mon) is False


def test_regular_weekday_is_trading(synthetic_calendar: Path) -> None:
    # 2026-05-12 is a Tuesday, not a holiday — trading.
    assert trading_hours.is_trading_day(dt.date(2026, 5, 12)) is True


def test_regular_weekend_is_non_trading(synthetic_calendar: Path) -> None:
    # 2026-05-16 is Saturday, not in makeup — non-trading.
    sat = dt.date(2026, 5, 16)
    assert sat.weekday() == 5
    assert trading_hours.is_trading_day(sat) is False


# ---------------------------------------------------------------------------
# Walks
# ---------------------------------------------------------------------------


def test_prev_trading_day_skips_weekend(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # Monday 2026-05-11 → previous trading day is Friday 2026-05-08
    # because 5/9 (Sat) is makeup workday — wait, 5/9 IS trading per
    # synthetic, so prev of Mon 5/11 is Sat 5/9.
    assert cal.prev_trading_day(dt.date(2026, 5, 11)) == dt.date(2026, 5, 9)


def test_prev_trading_day_skips_holiday(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # Tue 2026-05-05 is a holiday; prev should hop to Fri 2026-05-01
    # (also a real holiday in default YAML, but synthetic only lists 5/4
    # and 5/5 as holidays). So prev of 5/5 should be 5/1 (Friday).
    assert cal.prev_trading_day(dt.date(2026, 5, 5)) == dt.date(2026, 5, 1)


def test_next_trading_day_skips_weekend(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # Friday 2026-05-08 → next trading day is Sat 2026-05-09 (makeup).
    assert cal.next_trading_day(dt.date(2026, 5, 8)) == dt.date(2026, 5, 9)


def test_next_trading_day_skips_holiday(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # 2026-05-03 (Sun) → next is 5/6 (Wed), since 5/4 + 5/5 are holidays.
    assert cal.next_trading_day(dt.date(2026, 5, 3)) == dt.date(2026, 5, 6)


def test_count_trading_days_half_open(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # 2026-05-04 (Mon-holiday) to 2026-05-12 (Tue):
    # day-by-day: 5/4 H, 5/5 H, 5/6 trade, 5/7 trade, 5/8 trade,
    #             5/9 makeup-trade, 5/10 weekend, 5/11 trade, 5/12 (excluded).
    # Trading days in [5/4, 5/12): 5/6, 5/7, 5/8, 5/9, 5/11 = 5
    assert (
        cal.count_trading_days(dt.date(2026, 5, 4), dt.date(2026, 5, 12)) == 5
    )


def test_count_trading_days_invalid_range(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    assert cal.count_trading_days(dt.date(2026, 5, 12), dt.date(2026, 5, 1)) == 0
    assert cal.count_trading_days(dt.date(2026, 5, 5), dt.date(2026, 5, 5)) == 0


def test_compute_window_back_one_day(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    # 2026-05-12 is trading; window-back 1 = same day.
    assert cal.compute_window_back(dt.date(2026, 5, 12), 1) == dt.date(2026, 5, 12)


def test_compute_window_back_skips_holidays_and_weekends(
    synthetic_calendar: Path,
) -> None:
    from backend.data import trading_calendar as cal

    # End on Tue 2026-05-12, want 5 trading days.
    # Walking back: 5/12, 5/11, (5/10 Sun skip), (5/9 Sat → makeup, trade),
    # 5/8, 5/7  → that's 5 trading days, the 5th being 5/7.
    assert cal.compute_window_back(dt.date(2026, 5, 12), 5) == dt.date(2026, 5, 7)


def test_compute_window_back_validates_n(synthetic_calendar: Path) -> None:
    from backend.data import trading_calendar as cal

    with pytest.raises(ValueError):
        cal.compute_window_back(dt.date(2026, 5, 12), 0)
    with pytest.raises(ValueError):
        cal.compute_window_back(dt.date(2026, 5, 12), -1)


# ---------------------------------------------------------------------------
# Default calendar smoke (current 2026 entries in config/holidays.yaml)
# ---------------------------------------------------------------------------


def test_default_calendar_loads_smoke() -> None:
    """Without env override the default ``config/holidays.yaml`` must load."""
    loader_mod._cache = None  # type: ignore[attr-defined]
    importlib.reload(trading_hours)
    table = loader_mod.get_holiday_table()
    # Sanity: 2026 schedule has at least 元旦 + 国庆.
    assert dt.date(2026, 1, 1) in table.holidays
    assert dt.date(2026, 10, 1) in table.holidays
    # Mon 2026-05-12 is a regular trading day per the schedule.
    assert trading_hours.is_trading_day(dt.date(2026, 5, 12)) is True
    loader_mod._cache = None  # type: ignore[attr-defined]
    importlib.reload(trading_hours)
