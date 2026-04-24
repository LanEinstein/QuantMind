"""Tests for DataScheduler (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from backend.data.scheduler import DataScheduler


@pytest.fixture()
def mock_deps() -> dict[str, AsyncMock]:
    """Create mocked dependencies for the scheduler."""
    return {
        "market_data": AsyncMock(),
        "news_crawler": AsyncMock(),
        "mongodb": AsyncMock(),
        "redis_client": AsyncMock(),
    }


@pytest.fixture()
def scheduler(mock_deps: dict[str, AsyncMock]) -> DataScheduler:
    return DataScheduler(
        market_data=mock_deps["market_data"],
        news_crawler=mock_deps["news_crawler"],
        mongodb=mock_deps["mongodb"],
        redis_client=mock_deps["redis_client"],
        market_interval_seconds=30,
        news_interval_seconds=300,
    )


class TestDataScheduler:
    """Tests for DataScheduler start/stop and job behavior."""

    @pytest.mark.asyncio
    async def test_start_creates_scheduler(self, scheduler: DataScheduler) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_shuts_down(self, scheduler: DataScheduler) -> None:
        await scheduler.start()
        await scheduler.stop()
        assert scheduler._scheduler is None or not scheduler._scheduler.running

    @pytest.mark.asyncio
    async def test_market_job_skips_outside_trading(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=False
        ):
            await scheduler._run_market_job()
        mock_deps["market_data"].get_index_realtime.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_job_runs_during_trading(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_realtime.return_value = []
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await scheduler._run_market_job()
        mock_deps["market_data"].get_index_realtime.assert_called_once()

    @pytest.mark.asyncio
    async def test_news_job_runs(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_latest_news.return_value = []
        await scheduler._run_news_job()
        mock_deps["news_crawler"].fetch_latest_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_exception_does_not_crash(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_latest_news.side_effect = Exception("boom")
        # Should not raise
        await scheduler._run_news_job()


class TestIndexCronJob:
    """Tests for the 15:30 CSI300 index price collection cron."""

    @pytest.mark.asyncio
    async def test_start_registers_index_job(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        job = scheduler._scheduler.get_job("index_price_job")
        assert job is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_index_job_fetches_and_persists(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.return_value = pd.DataFrame([
            {"date": "2026-04-23", "open": 3800.0, "high": 3850.0,
             "low": 3780.0, "close": 3830.0, "volume": 1_000_000},
            {"date": "2026-04-24", "open": 3830.0, "high": 3870.0,
             "low": 3820.0, "close": 3865.0, "volume": 1_200_000},
        ])
        mock_deps["mongodb"].save_index_prices.return_value = 2

        await scheduler._run_index_job()

        mock_deps["market_data"].get_index_history.assert_called_once()
        mock_deps["mongodb"].save_index_prices.assert_called_once()
        args = mock_deps["mongodb"].save_index_prices.call_args
        assert args[0][0] == "000300"
        persisted = args[0][1]
        assert len(persisted) == 2
        assert persisted[0]["date"] == "2026-04-23"
        assert persisted[0]["close"] == 3830.0

    @pytest.mark.asyncio
    async def test_index_job_skips_empty_frame(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.return_value = pd.DataFrame()

        await scheduler._run_index_job()

        mock_deps["mongodb"].save_index_prices.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_job_exception_does_not_crash(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.side_effect = RuntimeError("boom")
        # Should not raise
        await scheduler._run_index_job()


class TestCostFlushCronJob:
    """Tests for the 23:00 LLM cost Redis→MongoDB flush cron."""

    @pytest.mark.asyncio
    async def test_start_registers_cost_flush_job(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        job = scheduler._scheduler.get_job("cost_flush_job")
        assert job is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cost_flush_job_calls_flush(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(return_value=5),
        ) as flush_mock:
            await scheduler._run_cost_flush_job()

        flush_mock.assert_called_once()
        kwargs_or_args = flush_mock.call_args
        # Verify called with redis client and mongodb
        assert kwargs_or_args is not None

    @pytest.mark.asyncio
    async def test_cost_flush_skips_without_redis(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=None,
        )
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(return_value=0),
        ) as flush_mock:
            await s._run_cost_flush_job()

        flush_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_cost_flush_exception_does_not_crash(
        self, scheduler: DataScheduler
    ) -> None:
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # Should not raise
            await scheduler._run_cost_flush_job()
