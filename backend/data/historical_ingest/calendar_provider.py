"""Trade-day enumeration for the offline bulk ingest (AE-001).

Enumerating only real trading days (instead of iterating every calendar day
and calling the rate-limited full-market endpoints on weekends/holidays) is
both cheaper and safer: a holiday-empty frame can no longer be confused with a
fetch failure. Two providers:

* :class:`TushareTradeCalendar` — the production default; reads the exchange
  calendar via the Tushare official SDK ``trade_cal`` (a calendar lookup, not
  a persisted-snapshot data endpoint, and not the akshare 节假日 API barred by
  P0-6 §1.4). Used offline only — the runtime calendar stays
  ``config/holidays.yaml``.
* :class:`StaticTradeCalendar` — a fixed list (tests, or an owner-supplied
  explicit day list).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

import pandas as pd

_DATE_RE = re.compile(r"^\d{8}$")


@runtime_checkable
class TradeCalendarProvider(Protocol):
    """Async source of A-share trading days (``YYYYMMDD``) in ``[start, end]``."""

    async def trading_days(
        self, start_date: str, end_date: str
    ) -> tuple[str, ...]: ...


@runtime_checkable
class _TradeCalClient(Protocol):
    """Minimal duck type satisfied by :class:`TushareClient`."""

    async def trade_cal(
        self, *, start_date: str, end_date: str, exchange: str = ...
    ) -> pd.DataFrame: ...


def _validate_range(start_date: str, end_date: str) -> None:
    if not _DATE_RE.match(start_date) or not _DATE_RE.match(end_date):
        raise ValueError("start_date / end_date must be YYYYMMDD (8 digits)")
    if start_date > end_date:
        raise ValueError(
            f"start_date {start_date} must be <= end_date {end_date}"
        )


class StaticTradeCalendar:
    """A fixed, in-memory list of trading days (deterministic; offline)."""

    def __init__(self, days: Iterable[str]) -> None:
        cleaned = sorted({str(d).strip() for d in days})
        for day in cleaned:
            if not _DATE_RE.match(day):
                raise ValueError(f"trade day {day!r} must be YYYYMMDD")
        self._days = tuple(cleaned)

    async def trading_days(
        self, start_date: str, end_date: str
    ) -> tuple[str, ...]:
        _validate_range(start_date, end_date)
        return tuple(d for d in self._days if start_date <= d <= end_date)


class TushareTradeCalendar:
    """Production calendar — Tushare ``trade_cal`` (official SDK, offline)."""

    def __init__(self, client: _TradeCalClient, *, exchange: str = "SSE") -> None:
        self._client = client
        self._exchange = exchange

    async def trading_days(
        self, start_date: str, end_date: str
    ) -> tuple[str, ...]:
        _validate_range(start_date, end_date)
        frame = await self._client.trade_cal(
            start_date=start_date, end_date=end_date, exchange=self._exchange
        )
        if frame is None or frame.empty:
            return ()
        # Defensive: keep only open days even though we request is_open=1.
        if "is_open" in frame.columns:
            frame = frame[frame["is_open"].astype(str).isin({"1", "1.0", "True"})]
        days = sorted({str(d).strip() for d in frame["cal_date"]})
        return tuple(d for d in days if _DATE_RE.match(d))


__all__ = [
    "StaticTradeCalendar",
    "TradeCalendarProvider",
    "TushareTradeCalendar",
]
