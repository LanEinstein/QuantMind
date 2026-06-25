"""Static holiday-table loader for ``backend.utils.trading_hours``.

Lives under ``backend/utils`` (not ``backend/data``) so the
``backend/risk`` import path can keep importing ``trading_hours``
without violating the P0-10 isolation redline (``backend/risk`` cannot
load ``backend.{llm,agents,mirofish,data}``).

Pure stdlib + ``yaml.safe_load`` against ``config/holidays.yaml``;
no network, no DB, no LLM. The richer trading-calendar surface
(``prev_trading_day`` / ``next_trading_day`` /
``compute_window_back``) lives in
``backend.data.trading_calendar`` for callers outside the risk
isolation boundary; that module re-uses this loader.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "holidays.yaml"
)
"""Resolved as ``<repo>/config/holidays.yaml``."""

_ENV_VAR = "QUANTMIND_HOLIDAYS_PATH"
"""Override for tests / alternate deploys. Production must leave unset."""


def _resolve_path() -> Path:
    raw = os.environ.get(_ENV_VAR)
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_PATH


@dataclass(frozen=True)
class HolidayTable:
    """Immutable view of the loaded holiday + makeup-workday tables."""

    holidays: frozenset[dt.date]
    makeup_workdays: frozenset[dt.date]
    schedule_version: int
    last_verified: dt.date | None
    source_path: Path


_lock = threading.Lock()
_cache: HolidayTable | None = None


def _coerce_date(value: object) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, str):
        return dt.date.fromisoformat(value)
    raise TypeError(f"cannot coerce {value!r} ({type(value).__name__}) to date")


def _load_table(path: Path) -> HolidayTable:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ValueError(
            f"holidays YAML root must be a mapping, got {type(raw).__name__}"
        )

    holidays: set[dt.date] = set()
    makeup: set[dt.date] = set()

    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, list):
            continue
        if key.startswith("holidays_"):
            for entry in value:
                if isinstance(entry, dict) and "date" in entry:
                    holidays.add(_coerce_date(entry["date"]))
                else:
                    holidays.add(_coerce_date(entry))
        elif key.startswith("makeup_workdays_"):
            for entry in value:
                makeup.add(_coerce_date(entry))

    overlap = holidays & makeup
    if overlap:
        raise ValueError(
            f"holidays.yaml has overlapping holiday + makeup dates: {sorted(overlap)}"
        )

    last_verified_raw = raw.get("last_verified")
    last_verified = _coerce_date(last_verified_raw) if last_verified_raw else None

    return HolidayTable(
        holidays=frozenset(holidays),
        makeup_workdays=frozenset(makeup),
        schedule_version=int(raw.get("schedule_version", 0)),
        last_verified=last_verified,
        source_path=path,
    )


def get_holiday_table() -> HolidayTable:
    """Return the cached :class:`HolidayTable`, loading once on first call."""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            _cache = _load_table(_resolve_path())
        return _cache


def reload_holiday_table() -> HolidayTable:
    """Force a re-read of the YAML. Tests only — A-007 forbids hot-reload."""
    global _cache
    with _lock:
        _cache = _load_table(_resolve_path())
        return _cache


# ---------------------------------------------------------------------------
# Fail-closed staleness guard (P0-6-amendment-2026-06-23)
# ---------------------------------------------------------------------------

_MIN_HOLIDAYS_PER_YEAR = 10
"""A fully-curated A-share year lists ~15-20 holiday weekdays (Spring Festival +
National Day alone are ~10); a placeholder / half-curated year has <=3. 10 is a
conservative floor that every complete year clears and every placeholder fails."""


class CalendarStaleError(RuntimeError):
    """The static holiday calendar is not curated for the operating year.

    Raised by :func:`assert_calendar_covers` so an unattended run fails CLOSED
    (refuses to boot / open positions) rather than trading on what
    ``is_trading_day`` would mis-classify as a normal weekday inside an
    un-curated holiday week (P0-6-amendment-2026-06-23).
    """


def _holidays_in_year(table: HolidayTable, year: int) -> int:
    return sum(1 for h in table.holidays if h.year == year)


def calendar_staleness_reason(
    today: dt.date, *, table: HolidayTable | None = None
) -> str | None:
    """Reason the calendar is stale for the CURRENT operating year, else ``None``.

    The HARD, fail-closed condition: the current year's ``holidays_YYYY`` block is
    missing or a placeholder (< ``_MIN_HOLIDAYS_PER_YEAR``). Operating inside an
    un-curated year lets ``is_trading_day`` mis-classify a holiday week as normal
    trading. ``assert_calendar_covers`` raises on this; the Line-1 cron skips +
    alerts on it. ``last_verified`` is folded into the message but the per-year
    count is authoritative. (Next-year forward coverage is a SOFT warning, not a
    stale condition — see :func:`calendar_forward_warning`.)
    """
    tbl = table if table is not None else get_holiday_table()
    this_year = _holidays_in_year(tbl, today.year)
    if this_year < _MIN_HOLIDAYS_PER_YEAR:
        return (
            f"holidays_{today.year} has only {this_year} curated dates "
            f"(< {_MIN_HOLIDAYS_PER_YEAR}); calendar not curated for the current "
            f"operating year (last_verified={tbl.last_verified})"
        )
    return None


def calendar_forward_warning(
    today: dt.date, *, table: HolidayTable | None = None
) -> str | None:
    """Soft early-warning that NEXT year is not yet curated, else ``None``.

    From December on, a long unattended run will cross into next year, so its
    ``holidays_YYYY`` block should already be curated. This is **warn-only**, NOT
    fail-closed: the current year still trades fine, and the State Council 放假安排
    notice for next year only publishes mid-November — a hard boot-block here would
    brick startup weeks before ops can act. Boot logs this; it never raises
    (P0-6-amendment-2026-06-23).
    """
    tbl = table if table is not None else get_holiday_table()
    if today.month == 12:
        next_year = _holidays_in_year(tbl, today.year + 1)
        if next_year < _MIN_HOLIDAYS_PER_YEAR:
            return (
                f"holidays_{today.year + 1} has only {next_year} curated dates "
                f"(< {_MIN_HOLIDAYS_PER_YEAR}); a year-end unattended run will "
                f"cross into an un-curated year — backfill it before year-end"
            )
    return None


def assert_calendar_covers(
    today: dt.date, *, table: HolidayTable | None = None
) -> None:
    """Raise :class:`CalendarStaleError` if the calendar is stale for ``today``.

    Boot/fail-fast form of :func:`calendar_staleness_reason` — called at startup
    (refuse to boot an unattended run on a placeholder calendar) and on the
    position-opening path (P0-6-amendment-2026-06-23).
    """
    reason = calendar_staleness_reason(today, table=table)
    if reason is not None:
        raise CalendarStaleError(reason)


__all__ = [
    "CalendarStaleError",
    "HolidayTable",
    "assert_calendar_covers",
    "calendar_forward_warning",
    "calendar_staleness_reason",
    "get_holiday_table",
    "reload_holiday_table",
]
