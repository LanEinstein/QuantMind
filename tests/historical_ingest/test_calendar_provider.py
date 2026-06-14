"""Tests for AE-001 trade-day enumeration providers."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from backend.data.historical_ingest.calendar_provider import (
    StaticTradeCalendar,
    TushareTradeCalendar,
)


async def test_static_calendar_filters_range() -> None:
    cal = StaticTradeCalendar(["20180103", "20180102", "20180104"])
    days = await cal.trading_days("20180102", "20180103")
    assert days == ("20180102", "20180103")


async def test_static_calendar_rejects_bad_date() -> None:
    with pytest.raises(ValueError, match="YYYYMMDD"):
        StaticTradeCalendar(["2018"])


async def test_static_calendar_rejects_inverted_range() -> None:
    cal = StaticTradeCalendar(["20180102"])
    with pytest.raises(ValueError, match="<="):
        await cal.trading_days("20180103", "20180102")


class _FakeTradeCalClient:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.calls: list[dict[str, Any]] = []

    async def trade_cal(
        self, *, start_date: str, end_date: str, exchange: str = "SSE"
    ) -> pd.DataFrame:
        self.calls.append(
            {"start": start_date, "end": end_date, "exchange": exchange}
        )
        return self._frame


async def test_tushare_calendar_returns_open_days_sorted() -> None:
    frame = pd.DataFrame(
        {
            "cal_date": ["20180104", "20180102", "20180101"],
            "is_open": [1, 1, 0],
        }
    )
    cal = TushareTradeCalendar(_FakeTradeCalClient(frame))
    days = await cal.trading_days("20180101", "20180104")
    assert days == ("20180102", "20180104")  # holiday 0101 dropped, sorted


async def test_tushare_calendar_empty_frame() -> None:
    cal = TushareTradeCalendar(_FakeTradeCalClient(pd.DataFrame()))
    assert await cal.trading_days("20180101", "20180104") == ()
