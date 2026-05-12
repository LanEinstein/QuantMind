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


__all__ = [
    "HolidayTable",
    "get_holiday_table",
    "reload_holiday_table",
]
