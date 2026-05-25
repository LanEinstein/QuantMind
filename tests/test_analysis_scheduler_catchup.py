"""Tests for AnalysisScheduler catch-up logic (Session D.2)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.data.analysis_scheduler import AnalysisScheduler

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _watchlist(codes: list[str]) -> AsyncMock:
    wl = AsyncMock()
    wl.list_stocks = AsyncMock(
        return_value=[
            {"stock_code": c, "stock_name": c, "active": True}
            for c in codes
        ]
    )
    return wl


def _mongodb_with_signals(signals_by_code: dict[str, list[dict]]) -> AsyncMock:
    async def query_signals_for_trade_date(
        trade_date: str, stock_codes: list[str]
    ):
        docs = []
        for code in stock_codes:
            docs.extend(
                {"stock_code": code, **signal}
                for signal in signals_by_code.get(code, [])
                if signal.get("trade_date") == trade_date
            )
        return docs

    m = AsyncMock()
    m.query_signals_for_trade_date = AsyncMock(
        side_effect=query_signals_for_trade_date
    )
    m.query_signals = AsyncMock(return_value=[])
    return m


def _fixed_now(hour: int, weekday: int = 3) -> datetime:
    """Return a datetime at given hour with target weekday (default Thursday)."""
    # April 23 2026 is a Thursday (weekday=3)
    # Adjust by weekday delta
    base = datetime(2026, 4, 23, hour, 45, tzinfo=SHANGHAI)
    delta = weekday - base.weekday()
    return base.replace(day=base.day + delta)


class TestCatchUpTargets:
    @pytest.mark.asyncio
    async def test_before_cutoff_returns_empty(self) -> None:
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519"]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals({}),
            redis_client=None,
        )
        # 08:00 — before 09:45 cutoff
        fake_now = _fixed_now(hour=8, weekday=3)
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == []

    @pytest.mark.asyncio
    async def test_weekend_returns_empty(self) -> None:
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519"]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals({}),
            redis_client=None,
        )
        fake_now = _fixed_now(hour=10, weekday=5)  # Saturday
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_watchlist_returns_empty(self) -> None:
        scheduler = AnalysisScheduler(
            watchlist=_watchlist([]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals({}),
            redis_client=None,
        )
        fake_now = _fixed_now(hour=10, weekday=3)
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == []

    @pytest.mark.asyncio
    async def test_all_stocks_covered_returns_empty(self) -> None:
        fake_now = _fixed_now(hour=10, weekday=3)
        trade_date = fake_now.strftime("%Y-%m-%d")
        signals = {
            "600519": [{"trade_date": trade_date}],
            "000858": [{"trade_date": trade_date}],
        }
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519", "000858"]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals(signals),
            redis_client=None,
        )
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == []

    @pytest.mark.asyncio
    async def test_only_missing_stocks_returned(self) -> None:
        """Stock-level granularity: 2 of 3 done → only the missing one catches up."""
        fake_now = _fixed_now(hour=10, weekday=3)
        trade_date = fake_now.strftime("%Y-%m-%d")
        signals = {
            "600519": [{"trade_date": trade_date}],
            "000858": [{"trade_date": trade_date}],
            # 601318 missing
        }
        mongodb = _mongodb_with_signals(signals)
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519", "000858", "601318"]),
            services=MagicMock(),
            mongodb=mongodb,
            redis_client=None,
        )
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == ["601318"]
        mongodb.query_signals_for_trade_date.assert_awaited_once_with(
            trade_date=trade_date,
            stock_codes=["600519", "000858", "601318"],
        )
        mongodb.query_signals.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_missing_stocks_returned(self) -> None:
        fake_now = _fixed_now(hour=10, weekday=3)
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519", "000858"]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals({}),
            redis_client=None,
        )
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert set(result) == {"600519", "000858"}

    @pytest.mark.asyncio
    async def test_stale_signal_does_not_count(self) -> None:
        """Yesterday's signal does NOT satisfy today's requirement."""
        fake_now = _fixed_now(hour=10, weekday=3)
        # Signals exist but for a different date
        signals = {
            "600519": [{"trade_date": "2026-01-01"}],
        }
        scheduler = AnalysisScheduler(
            watchlist=_watchlist(["600519"]),
            services=MagicMock(),
            mongodb=_mongodb_with_signals(signals),
            redis_client=None,
        )
        with patch(
            "backend.data.analysis_scheduler.datetime"
        ) as dt_mock:
            dt_mock.now.return_value = fake_now
            result = await scheduler._compute_catch_up_targets()
        assert result == ["600519"]
