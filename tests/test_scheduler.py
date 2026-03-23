"""Tests for DataScheduler (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
