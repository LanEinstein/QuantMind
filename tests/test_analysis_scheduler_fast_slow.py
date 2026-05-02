"""Tests for the Phase 5B-T02 Fast/Slow scheduler split."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.models import (
    AnalysisServices,
    PipelineConfig,
    TradingSignal,
)
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.data.analysis_scheduler import AnalysisScheduler
from backend.services.watchlist_policy import (
    WatchlistPolicy,
    load_policy,
)

YAML_TEMPLATE = """
fast:
  cron: "0 9,11,13,15 * * mon-fri"
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480
  default_codes: ["600519"]
slow:
  cron: "0 9 * * mon-fri"
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900
  default_codes: ["000858"]
overrides:
  "601318": slow
default_category: slow
policy_version: 1
"""


def _sample_result(code: str = "600519") -> AnalysisRunResult:
    signal = TradingSignal(
        action="买入",
        target_price=1900.0,
        confidence=0.8,
        risk_score=0.3,
        reasoning="基本面强劲",
        stock_code=code,
        stock_name=code,
        trade_date="2026-05-02",
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
def policy(tmp_path: Path) -> WatchlistPolicy:
    p = tmp_path / "policy.yaml"
    p.write_text(YAML_TEMPLATE, encoding="utf-8")
    return load_policy(p)


def _make_services() -> AnalysisServices:
    return AnalysisServices(
        llm_router=MagicMock(),
        market_data=MagicMock(),
        history_data=MagicMock(),
        news_crawler=MagicMock(),
        mongodb=MagicMock(),
        pipeline_config=PipelineConfig(),
    )


@pytest.fixture()
def watchlist_with_codes() -> AsyncMock:
    wl = AsyncMock()
    wl.list_stocks = AsyncMock(
        return_value=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
            {"stock_code": "000858", "stock_name": "五粮液", "active": True},
            {"stock_code": "601318", "stock_name": "中国平安", "active": True},
        ]
    )
    return wl


@pytest.fixture()
def mongodb() -> AsyncMock:
    m = AsyncMock()
    m.save_signal = AsyncMock(return_value="sig_id")
    m.save_analysis_record = AsyncMock(return_value="rec_id")
    return m


@pytest.fixture()
def scheduler_with_policy(
    watchlist_with_codes: AsyncMock,
    mongodb: AsyncMock,
    policy: WatchlistPolicy,
) -> AnalysisScheduler:
    return AnalysisScheduler(
        watchlist=watchlist_with_codes,
        services=_make_services(),
        mongodb=mongodb,
        redis_client=None,
        policy=policy,
    )


class TestPolicyAccessors:
    @pytest.mark.unit
    def test_policy_property_returns_loaded_policy(
        self,
        scheduler_with_policy: AnalysisScheduler,
        policy: WatchlistPolicy,
    ) -> None:
        assert scheduler_with_policy.policy is policy

    @pytest.mark.unit
    def test_update_policy_swaps_in_memory(
        self, scheduler_with_policy: AnalysisScheduler, policy: WatchlistPolicy
    ) -> None:
        from backend.services.watchlist_policy import update_override

        new = update_override(policy, "600519", "slow")
        scheduler_with_policy.update_policy(new)
        assert scheduler_with_policy.policy is new


class TestRunCategoryAnalysis:
    @pytest.mark.asyncio
    async def test_fast_runs_only_fast_codes(
        self,
        scheduler_with_policy: AnalysisScheduler,
        mongodb: AsyncMock,
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result("600519"),
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            signals = await scheduler_with_policy.run_category_analysis("fast")

        assert mock_run.call_count == 1
        # Assert the only ran code is 600519 (fast bucket)
        called_code = mock_run.await_args_list[0].args[0]
        assert called_code == "600519"
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_slow_runs_default_and_override_codes(
        self,
        scheduler_with_policy: AnalysisScheduler,
    ) -> None:
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result("000858"),
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler_with_policy.run_category_analysis("slow")

        # 000858 (slow default) + 601318 (override slow)
        assert mock_run.call_count == 2
        called_codes = {
            c.args[0] for c in mock_run.await_args_list
        }
        assert called_codes == {"000858", "601318"}

    @pytest.mark.asyncio
    async def test_no_matched_codes_returns_empty(
        self,
        watchlist_with_codes: AsyncMock,
        mongodb: AsyncMock,
        policy: WatchlistPolicy,
    ) -> None:
        # Build a watchlist with only a slow-default code
        watchlist_with_codes.list_stocks.return_value = [
            {"stock_code": "000858", "stock_name": "wuli", "active": True},
        ]
        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=None,
            policy=policy,
        )
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
        ) as mock_run:
            signals = await scheduler.run_category_analysis("fast")
        assert signals == []
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_policy_logs_and_returns_empty(
        self,
        watchlist_with_codes: AsyncMock,
        mongodb: AsyncMock,
    ) -> None:
        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=None,
            policy=None,
        )
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
        ) as mock_run:
            signals = await scheduler.run_category_analysis("fast")
        assert signals == []
        mock_run.assert_not_called()


class TestPerCategoryPipelineConfig:
    """Each cron job must rebuild services with the bucket's PipelineConfig."""

    @pytest.mark.asyncio
    async def test_fast_uses_fast_max_debate_rounds(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler_with_policy.run_category_analysis("fast")

        assert len(captured) == 1
        cfg = captured[0].pipeline_config
        assert cfg.max_debate_rounds == 1
        assert cfg.analysis_timeout_seconds == 480

    @pytest.mark.asyncio
    async def test_slow_uses_slow_max_debate_rounds(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler_with_policy.run_category_analysis("slow")

        assert len(captured) == 2
        for services in captured:
            assert services.pipeline_config.max_debate_rounds == 2
            assert services.pipeline_config.analysis_timeout_seconds == 900

    @pytest.mark.asyncio
    async def test_no_policy_keeps_base_services_unchanged(
        self,
        watchlist_with_codes: AsyncMock,
        mongodb: AsyncMock,
    ) -> None:
        base_services = _make_services()
        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=base_services,
            mongodb=mongodb,
            redis_client=None,
            policy=None,
        )
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        # Same services object — no model_copy when no policy.
        for services in captured:
            assert services is base_services


class TestTimeoutEnforcement:
    @pytest.mark.asyncio
    async def test_fast_pipeline_timeout_persists_failed_record(
        self,
        scheduler_with_policy: AnalysisScheduler,
        mongodb: AsyncMock,
    ) -> None:
        async def hangs(*args, **kwargs):
            await asyncio.sleep(10)
            return _sample_result()

        captured_timeout: list[int] = []

        async def fake_wait_for(coro, timeout):
            # Close the coroutine so pytest doesn't warn about an
            # un-awaited coroutine, then raise to mimic a timeout.
            captured_timeout.append(timeout)
            coro.close()
            raise TimeoutError

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=hangs,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.wait_for",
            new=fake_wait_for,
        ):
            signal = await scheduler_with_policy._run_and_persist(
                "600519", category="fast"
            )

        # R3 MEDIUM: the test must verify the timeout VALUE passed to
        # wait_for, not just the except branch.
        assert captured_timeout == [480]

        assert signal is None
        # A failed record with the pipeline_timeout prefix is written
        mongodb.save_analysis_record.assert_awaited_once()
        record_payload = mongodb.save_analysis_record.await_args[0][0]
        assert record_payload["status"] == "failed"
        assert record_payload["error"].startswith("pipeline_timeout:")
        assert "category=fast" in record_payload["error"]
        assert "timeout=480s" in record_payload["error"]

    @pytest.mark.asyncio
    async def test_no_category_skips_timeout_wrapper(
        self,
        scheduler_with_policy: AnalysisScheduler,
    ) -> None:
        """Legacy code path (category=None) must not invoke wait_for."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result("600519"),
        ), patch(
            "backend.data.analysis_scheduler.asyncio.wait_for",
            new_callable=AsyncMock,
        ) as mock_wait:
            signal = await scheduler_with_policy._run_and_persist(
                "600519", category=None
            )
        assert signal is not None
        mock_wait.assert_not_called()


class TestCronRegistration:
    @pytest.mark.asyncio
    async def test_start_with_policy_registers_two_jobs(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        # Stub catch-up so start() doesn't kick off a background task that
        # could race with stop() and depend on the wall-clock weekday.
        with patch.object(
            scheduler_with_policy,
            "_compute_catch_up_targets",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await scheduler_with_policy.start()
        try:
            jobs = scheduler_with_policy._scheduler.get_jobs()
            ids = {job.id for job in jobs}
            assert "fast_analysis" in ids
            assert "slow_analysis" in ids
            # Legacy job must NOT be registered when policy is set
            assert "daily_analysis" not in ids
        finally:
            await scheduler_with_policy.stop()

    @pytest.mark.asyncio
    async def test_malformed_cron_falls_back_to_legacy(
        self,
        watchlist_with_codes: AsyncMock,
        mongodb: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """A typo in either bucket's cron must NOT bring the scheduler down.

        Codex R1 HIGH: a malformed cron expression in policy was
        crashing start(). Verify the scheduler degrades to the legacy
        single-cron mode instead.
        """
        # Build a policy with garbage cron string by writing through YAML
        bad_yaml = YAML_TEMPLATE.replace(
            '"0 9,11,13,15 * * mon-fri"', '"not-a-cron"'
        )
        p = tmp_path / "bad_cron.yaml"
        p.write_text(bad_yaml, encoding="utf-8")
        bad_policy = load_policy(p)

        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=None,
            policy=bad_policy,
        )
        with patch.object(
            scheduler,
            "_compute_catch_up_targets",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await scheduler.start()
        try:
            jobs = scheduler._scheduler.get_jobs()
            ids = {job.id for job in jobs}
            # Malformed cron forced fallback → legacy job present, no
            # fast/slow registered, policy cleared to None.
            assert ids == {"daily_analysis"}
            assert scheduler.policy is None
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_start_without_policy_keeps_legacy_job(
        self, watchlist_with_codes: AsyncMock, mongodb: AsyncMock
    ) -> None:
        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=None,
            policy=None,
        )
        with patch.object(
            scheduler,
            "_compute_catch_up_targets",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await scheduler.start()
        try:
            jobs = scheduler._scheduler.get_jobs()
            ids = {job.id for job in jobs}
            assert ids == {"daily_analysis"}
        finally:
            await scheduler.stop()


class TestBudgetInteractionWithPolicy:
    """R3 HIGH #3: hard-cap budget breach must short-circuit before
    asyncio.wait_for is even constructed in the policy code path."""

    @pytest.mark.asyncio
    async def test_hard_cap_breach_skips_pipeline_for_fast_category(
        self,
        watchlist_with_codes: AsyncMock,
        mongodb: AsyncMock,
        policy: WatchlistPolicy,
    ) -> None:
        from backend.services.cost_guard import DailyBudgetExceededError

        scheduler = AnalysisScheduler(
            watchlist=watchlist_with_codes,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=AsyncMock(),
            policy=policy,
        )
        with patch(
            "backend.data.analysis_scheduler.assert_budget_allows",
            new_callable=AsyncMock,
            side_effect=DailyBudgetExceededError("budget gone"),
        ), patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.wait_for",
            new_callable=AsyncMock,
        ) as mock_wait:
            signal = await scheduler._run_and_persist(
                "600519", category="fast"
            )

        assert signal is None
        mock_run.assert_not_called()
        mock_wait.assert_not_called()
        # cost_ceiling_breached record was written (existing behaviour)
        mongodb.save_analysis_record.assert_awaited_once()
        record_payload = mongodb.save_analysis_record.await_args[0][0]
        assert record_payload["error"].startswith("cost_ceiling_breached:")


class TestCatchUpWithPolicy:
    """R3 HIGH #4: missed runs must be replayed with the assigned bucket's
    config — slow stocks should still get the deeper pipeline + 900s budget."""

    @pytest.mark.asyncio
    async def test_catchup_uses_slow_config_for_slow_code(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            # 000858 is in slow bucket (default_codes)
            await scheduler_with_policy._run_catch_up(["000858"])

        assert len(captured) == 1
        cfg = captured[0].pipeline_config
        assert cfg.max_debate_rounds == 2
        assert cfg.analysis_timeout_seconds == 900


class TestRunDailyAnalysisWithPolicy:
    """R3 MEDIUM #7: a manual full sweep must still respect per-stock buckets."""

    @pytest.mark.asyncio
    async def test_mixed_buckets_use_their_own_configs(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[tuple[str, AnalysisServices]] = []

        async def capturing_run(code, services):
            captured.append((code, services))
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ), patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler_with_policy.run_daily_analysis()

        # 600519 → fast (1 round, 480s); 000858 + 601318 → slow (2/900)
        configs_by_code = {
            code: services.pipeline_config for code, services in captured
        }
        assert configs_by_code["600519"].max_debate_rounds == 1
        assert configs_by_code["600519"].analysis_timeout_seconds == 480
        assert configs_by_code["000858"].max_debate_rounds == 2
        assert configs_by_code["000858"].analysis_timeout_seconds == 900
        assert configs_by_code["601318"].max_debate_rounds == 2
        assert configs_by_code["601318"].analysis_timeout_seconds == 900


class TestEmptyWatchlistWithPolicy:
    """R3 LOW #11: empty watchlist with policy must not crash or call pipeline."""

    @pytest.mark.asyncio
    async def test_run_category_analysis_returns_empty(
        self, mongodb: AsyncMock, policy: WatchlistPolicy
    ) -> None:
        wl = AsyncMock()
        wl.list_stocks = AsyncMock(return_value=[])
        scheduler = AnalysisScheduler(
            watchlist=wl,
            services=_make_services(),
            mongodb=mongodb,
            redis_client=None,
            policy=policy,
        )
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
        ) as mock_run:
            signals = await scheduler.run_category_analysis("fast")
        assert signals == []
        mock_run.assert_not_called()


class TestSingleAnalysisCategoryResolution:
    @pytest.mark.asyncio
    async def test_run_single_analysis_uses_assigned_category(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ):
            await scheduler_with_policy.run_single_analysis("600519")

        # 600519 is fast bucket → max_debate_rounds=1
        assert captured[0].pipeline_config.max_debate_rounds == 1

    @pytest.mark.asyncio
    async def test_run_single_analysis_explicit_category_overrides(
        self, scheduler_with_policy: AnalysisScheduler
    ) -> None:
        captured: list[AnalysisServices] = []

        async def capturing_run(code, services):
            captured.append(services)
            return _sample_result(code)

        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new=capturing_run,
        ):
            await scheduler_with_policy.run_single_analysis(
                "600519", category="slow"
            )

        # Explicit slow override → max_debate_rounds=2
        assert captured[0].pipeline_config.max_debate_rounds == 2
