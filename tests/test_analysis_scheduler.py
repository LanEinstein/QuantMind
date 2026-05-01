"""Tests for AnalysisScheduler (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datetime import UTC, datetime

from backend.agents.models import TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.data.analysis_scheduler import AnalysisScheduler


def _sample_signal(code: str = "600519", name: str = "贵州茅台") -> TradingSignal:
    return TradingSignal(
        action="买入",
        target_price=1900.0,
        confidence=0.8,
        risk_score=0.3,
        reasoning="基本面强劲",
        stock_code=code,
        stock_name=name,
        trade_date="2026-04-13",
    )


def _sample_result(
    code: str = "600519", name: str = "贵州茅台"
) -> AnalysisRunResult:
    signal = _sample_signal(code, name)
    now = datetime.now(tz=UTC)
    record = AnalysisRecord(
        run_id=f"run-{code}",
        stock_code=code,
        stock_name=name,
        trade_date=signal.trade_date,
        status="completed",
        max_rounds=2,
        current_round=2,
        created_at=now,
        completed_at=now,
    )
    return AnalysisRunResult(signal=signal, record=record)


def _make_watchlist_stocks(count: int) -> list[dict]:
    codes = [("600519", "贵州茅台"), ("000858", "五粮液"), ("601318", "中国平安")]
    return [
        {"stock_code": c, "stock_name": n, "active": True}
        for c, n in codes[:count]
    ]


@pytest.fixture()
def mock_watchlist() -> AsyncMock:
    wl = AsyncMock()
    wl.list_stocks = AsyncMock(return_value=_make_watchlist_stocks(3))
    return wl


@pytest.fixture()
def mock_services() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_mongodb() -> AsyncMock:
    mongodb = AsyncMock()
    mongodb.save_signal = AsyncMock(return_value="signal_id")
    mongodb.save_analysis_record = AsyncMock(return_value="record_id")
    return mongodb


@pytest.fixture()
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def scheduler(
    mock_watchlist: AsyncMock,
    mock_services: MagicMock,
    mock_mongodb: AsyncMock,
    mock_redis: AsyncMock,
) -> AnalysisScheduler:
    return AnalysisScheduler(
        watchlist=mock_watchlist,
        services=mock_services,
        mongodb=mock_mongodb,
        redis_client=mock_redis,
    )


class TestRunDailyAnalysis:
    """Tests for run_daily_analysis method."""

    @pytest.mark.asyncio
    async def test_calls_run_analysis_per_stock(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """run_analysis is called once for each watchlist stock."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        assert mock_run.call_count == 3

    @pytest.mark.asyncio
    async def test_persists_each_signal(
        self, scheduler: AnalysisScheduler, mock_mongodb: AsyncMock
    ) -> None:
        """Each successful signal is saved via mongodb.save_signal."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        assert mock_mongodb.save_signal.call_count == 3

    @pytest.mark.asyncio
    async def test_persists_each_analysis_record_with_signal_id(
        self,
        scheduler: AnalysisScheduler,
        mock_mongodb: AsyncMock,
    ) -> None:
        """R3 HIGH #4: record persistence coverage.

        Each successful run must call save_analysis_record with the
        signal_id stamped into the record.model_copy(update={...}). A
        regression that drops the record persist call would silently
        break AgentDebate /history.
        """
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        assert mock_mongodb.save_analysis_record.call_count == 3
        for call in mock_mongodb.save_analysis_record.await_args_list:
            doc = call.args[0]
            assert doc["status"] == "completed"
            assert doc["signal_id"] == "signal_id"

    @pytest.mark.asyncio
    async def test_run_analysis_error_persists_failed_record(
        self,
        scheduler: AnalysisScheduler,
        mock_mongodb: AsyncMock,
    ) -> None:
        """R3 HIGH #4: failed runs still surface in /history.

        When run_analysis raises AnalysisRunError, the scheduler must
        persist exc.record before re-raising so the operator can see
        which agent failed, and the overall daily run continues.
        """
        from backend.agents.graph import AnalysisRunError

        failed_record = AnalysisRecord(
            run_id="run-failed",
            stock_code="600519",
            stock_name="贵州茅台",
            trade_date="2026-04-13",
            status="failed",
            max_rounds=2,
            current_round=1,
            created_at=datetime.now(tz=UTC),
            completed_at=datetime.now(tz=UTC),
            error="fundamental_analyst: empty response",
        )

        side_effects = [
            AnalysisRunError(failed_record),
            _sample_result("000858", "五粮液"),
            _sample_result("601318", "中国平安"),
        ]

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            side_effect=side_effects,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        # The failed record was persisted (first call), plus the two
        # successful records' signal_id-stamped copies.
        assert mock_mongodb.save_analysis_record.call_count == 3
        first_doc = mock_mongodb.save_analysis_record.await_args_list[0].args[0]
        assert first_doc["status"] == "failed"
        assert first_doc["run_id"] == "run-failed"

    @pytest.mark.asyncio
    async def test_continues_on_failure(
        self,
        scheduler: AnalysisScheduler,
        mock_mongodb: AsyncMock,
    ) -> None:
        """If one stock fails, the rest are still analyzed."""
        side_effects = [
            _sample_result("600519"),
            RuntimeError("LLM timeout"),
            _sample_result("601318"),
        ]
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            side_effect=side_effects,
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            signals = await scheduler.run_daily_analysis()

        # All 3 stocks attempted
        assert mock_run.call_count == 3
        # Only 2 succeeded
        assert len(signals) == 2
        # Only 2 persisted
        assert mock_mongodb.save_signal.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_successful_signals(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """Returns list of TradingSignal objects for successful analyses."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            signals = await scheduler.run_daily_analysis()

        assert len(signals) == 3
        for sig in signals:
            assert isinstance(sig, TradingSignal)

    @pytest.mark.asyncio
    async def test_empty_watchlist_returns_empty(
        self,
        scheduler: AnalysisScheduler,
        mock_watchlist: AsyncMock,
    ) -> None:
        """Empty watchlist returns [] without calling run_analysis."""
        mock_watchlist.list_stocks.return_value = []

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
        ) as mock_run:
            signals = await scheduler.run_daily_analysis()

        assert signals == []
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_to_redis(
        self,
        scheduler: AnalysisScheduler,
        mock_redis: AsyncMock,
    ) -> None:
        """Each successful signal is published to Redis."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        assert mock_redis.publish.call_count == 3


class TestRunSingleAnalysis:
    """Tests for run_single_analysis method."""

    @pytest.mark.asyncio
    async def test_analyzes_and_persists(
        self,
        scheduler: AnalysisScheduler,
        mock_mongodb: AsyncMock,
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ):
            signal = await scheduler.run_single_analysis("600519")

        assert signal is not None
        assert isinstance(signal, TradingSignal)
        mock_mongodb.save_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(
        self, scheduler: AnalysisScheduler
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            signal = await scheduler.run_single_analysis("600519")

        assert signal is None


class TestStartStop:
    """Tests for scheduler lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_scheduler(
        self, scheduler: AnalysisScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_scheduler(
        self, scheduler: AnalysisScheduler
    ) -> None:
        await scheduler.start()
        await scheduler.stop()
        assert scheduler._scheduler is None
