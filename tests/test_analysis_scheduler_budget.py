"""Integration tests: AnalysisScheduler honors cost_guard hard-cap.

These tests stub run_analysis and the cost-guard probe to drive each
branch of ``_run_and_persist`` deterministically. They are the only
assertion that the scheduler and the cost guard speak to each other
correctly — unit tests on ``cost_guard`` alone cannot catch a regression
where the scheduler forgets to call the guard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.models import TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.data.analysis_scheduler import AnalysisScheduler
from backend.services.cost_guard import (
    BudgetState,
    DailyBudgetExceededError,
)


def _ok_state(spent: float = 1.0) -> BudgetState:
    return BudgetState(
        daily_budget=20.0,
        spent_today=spent,
        soft_ceiling=14.0,
        hard_ceiling=20.0,
        remaining=20.0 - spent,
        status="ok",
    )


def _soft_state(spent: float = 15.0) -> BudgetState:
    return BudgetState(
        daily_budget=20.0,
        spent_today=spent,
        soft_ceiling=14.0,
        hard_ceiling=20.0,
        remaining=20.0 - spent,
        status="soft_breach",
    )


def _sample_result(code: str = "600519") -> AnalysisRunResult:
    signal = TradingSignal(
        action="买入",
        target_price=1900.0,
        confidence=0.8,
        risk_score=0.3,
        reasoning="基本面强劲",
        stock_code=code,
        stock_name=code,
        trade_date="2026-05-01",
    )
    now = datetime.now(tz=UTC)
    record = AnalysisRecord(
        run_id=f"run-{code}",
        stock_code=code,
        stock_name=code,
        trade_date=signal.trade_date,
        status="completed",
        max_rounds=2,
        current_round=2,
        created_at=now,
        completed_at=now,
    )
    return AnalysisRunResult(signal=signal, record=record)


@pytest.fixture()
def scheduler() -> AnalysisScheduler:
    watchlist = AsyncMock()
    services = MagicMock()
    mongodb = AsyncMock()
    mongodb.save_signal = AsyncMock(return_value="sig_id")
    mongodb.save_analysis_record = AsyncMock(return_value="rec_id")
    redis_client = AsyncMock()
    return AnalysisScheduler(
        watchlist=watchlist,
        services=services,
        mongodb=mongodb,
        redis_client=redis_client,
    )


class TestCostGuardIntegration:
    """Verify _run_and_persist branches around the cost ceiling."""

    @pytest.mark.asyncio
    async def test_hard_breach_skips_pipeline_and_records_failure(
        self, scheduler: AnalysisScheduler
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            side_effect=DailyBudgetExceededError(
                "Daily budget 20.00 CNY exceeded (spent 25.00); skipping pipeline"
            ),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run:
            signal = await scheduler._run_and_persist("600519")

        assert signal is None
        mock_run.assert_not_called()

        # A failed AnalysisRecord with cost_ceiling_breached prefix is
        # the only thing /history can show — the assertion must be loud.
        scheduler._mongodb.save_analysis_record.assert_awaited_once()
        record_payload = scheduler._mongodb.save_analysis_record.await_args[0][0]
        assert record_payload["status"] == "failed"
        assert record_payload["error"].startswith("cost_ceiling_breached:")
        assert record_payload["stock_code"] == "600519"

    @pytest.mark.asyncio
    async def test_soft_breach_proceeds_but_logs(
        self, scheduler: AnalysisScheduler
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            return_value=_soft_state(),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run:
            signal = await scheduler._run_and_persist("600519")

        assert signal is not None
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ok_state_proceeds_normally(
        self, scheduler: AnalysisScheduler
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            return_value=_ok_state(),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run:
            signal = await scheduler._run_and_persist("600519")

        assert signal is not None
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_redis_probe_failure_does_not_block_pipeline(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """A transient Redis hiccup must NOT wedge analysis."""
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            side_effect=ConnectionError("redis down"),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run:
            signal = await scheduler._run_and_persist("600519")

        assert signal is not None
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_redis_skips_check_and_proceeds(self) -> None:
        """When redis_client is None, scheduler proceeds without guard.

        This matches the existing "publish_signal early-return when
        redis is None" behavior; treating absence of Redis as a hard
        failure would block all evaluation in development.
        """
        watchlist = AsyncMock()
        services = MagicMock()
        mongodb = AsyncMock()
        mongodb.save_signal = AsyncMock(return_value="sig_id")
        mongodb.save_analysis_record = AsyncMock(return_value="rec_id")
        scheduler = AnalysisScheduler(
            watchlist=watchlist,
            services=services,
            mongodb=mongodb,
            redis_client=None,
        )
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
        ) as mock_guard, patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run:
            signal = await scheduler._run_and_persist("600519")

        assert signal is not None
        mock_run.assert_awaited_once()
        mock_guard.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_record_failure_does_not_swallow_skip(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """If MongoDB write fails, we still return None (skip the run)."""
        scheduler._mongodb.save_analysis_record = AsyncMock(
            side_effect=RuntimeError("mongo down")
        )
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            side_effect=DailyBudgetExceededError("budget gone"),
        ):
            signal = await scheduler._run_and_persist("600519")

        assert signal is None

    @pytest.mark.asyncio
    async def test_concurrent_runs_are_serialized_by_lock(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """Manual + cron calls must not double-spend by racing the budget check.

        Without ``self._run_lock``, two concurrent calls could both see
        the same under-cap snapshot and both proceed — exactly the
        race Codex flagged in cycle 4.
        """
        import asyncio as _asyncio
        active = 0
        max_active = 0

        async def slow_run(*args: object, **kwargs: object) -> AnalysisRunResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await _asyncio.sleep(0.05)
            active -= 1
            return _sample_result()

        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            return_value=_ok_state(),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=slow_run,
        ):
            await _asyncio.gather(
                scheduler._run_and_persist("600519"),
                scheduler._run_and_persist("000858"),
                scheduler._run_and_persist("601318"),
            )

        # Lock must keep concurrency at 1; even if 3 calls fire
        # simultaneously, only one ever sits inside run_analysis.
        assert max_active == 1
