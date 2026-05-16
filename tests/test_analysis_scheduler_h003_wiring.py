"""H-003 cycle 2-3 regression tests for analysis_scheduler wiring.

Codex cycle 2 P2: SoftDegradeManager + monthly milestone wiring is
test-only — this file proves the scheduler now invokes them.

Codex cycle 3 P3: monthly milestone dispatch must still fire on the
daily-hard-breach branch (so a 100% milestone is not silently swallowed
when the daily ceiling fires in the same tick).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.models import TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.data.analysis_scheduler import AnalysisScheduler
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    DailyBudgetState,
    MonthlyBudgetState,
)
from backend.services.soft_degrade_manager import MilestoneTransition


def _ok_state() -> DailyBudgetState:
    return DailyBudgetState(
        daily_budget=20.0,
        spent_today=5.0,
        soft_ceiling=14.0,
        hard_ceiling=20.0,
        remaining=15.0,
        status="ok",
    )


def _soft_state() -> DailyBudgetState:
    return DailyBudgetState(
        daily_budget=20.0,
        spent_today=15.0,
        soft_ceiling=14.0,
        hard_ceiling=20.0,
        remaining=5.0,
        status="soft_breach",
    )


def _monthly_state(pct: float | None) -> MonthlyBudgetState:
    return MonthlyBudgetState(
        monthly_budget=440.0,
        spent_month=440.0 * (pct or 0.0),
        fraction=pct or 0.0,
        threshold_reached=pct,
        status=(
            "ok"
            if pct is None
            else f"threshold_{int(pct * 100)}"
        ),
    )


def _sample_result() -> AnalysisRunResult:
    signal = TradingSignal(
        stock_code="600519",
        stock_name="贵州茅台",
        action="买入",
        target_price=1900.0,
        confidence=0.65,
        risk_score=0.3,
        reasoning="ok",
        trade_date="2026-05-16",
    )
    record = AnalysisRecord(
        run_id="run-1",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-05-16",
        status="completed",
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
    alert_dispatcher = AsyncMock()
    return AnalysisScheduler(
        watchlist=watchlist,
        services=services,
        mongodb=mongodb,
        redis_client=redis_client,
        alert_dispatcher=alert_dispatcher,
    )


@pytest.mark.asyncio
async def test_soft_breach_activates_kimi_block(
    scheduler: AnalysisScheduler,
) -> None:
    """Codex cycle 2 P2 — soft_breach must activate Kimi escalation block."""
    activate = AsyncMock(return_value=True)
    fire = AsyncMock(return_value=None)

    with patch(
        "backend.data.analysis_scheduler.assert_budget_allows",
        new_callable=AsyncMock,
        return_value=_soft_state(),
    ), patch(
        "backend.data.analysis_scheduler.get_monthly_budget_state",
        new_callable=AsyncMock,
        return_value=_monthly_state(None),
    ), patch(
        "backend.data.analysis_scheduler.SoftDegradeManager"
    ) as MgrCls, patch(
        "backend.data.analysis_scheduler.run_analysis",
        new_callable=AsyncMock,
        return_value=_sample_result(),
    ):
        mgr_instance = MagicMock()
        mgr_instance.activate_kimi_escalation_block = activate
        mgr_instance.maybe_fire_monthly_milestone = fire
        MgrCls.return_value = mgr_instance
        await scheduler._run_and_persist("600519")

    activate.assert_awaited_once()
    assert activate.await_args.kwargs["reason"] == "daily_soft_breach"


@pytest.mark.asyncio
async def test_monthly_milestone_dispatches_via_alert_dispatcher(
    scheduler: AnalysisScheduler,
) -> None:
    """Codex cycle 2 P2 — milestone must reach alert_dispatcher.fire()."""
    activate = AsyncMock(return_value=False)
    fire = AsyncMock(
        return_value=MilestoneTransition(
            fraction=0.50,
            fired=True,
            alert_type="monthly_budget_50pct_reached",
        )
    )

    with patch(
        "backend.data.analysis_scheduler.assert_budget_allows",
        new_callable=AsyncMock,
        return_value=_ok_state(),
    ), patch(
        "backend.data.analysis_scheduler.get_monthly_budget_state",
        new_callable=AsyncMock,
        return_value=_monthly_state(0.50),
    ), patch(
        "backend.data.analysis_scheduler.SoftDegradeManager"
    ) as MgrCls, patch(
        "backend.data.analysis_scheduler.run_analysis",
        new_callable=AsyncMock,
        return_value=_sample_result(),
    ):
        mgr_instance = MagicMock()
        mgr_instance.activate_kimi_escalation_block = activate
        mgr_instance.maybe_fire_monthly_milestone = fire
        MgrCls.return_value = mgr_instance
        await scheduler._run_and_persist("600519")

    fire.assert_awaited_once()
    dispatcher = scheduler._alert_dispatcher
    dispatcher.fire.assert_awaited_once()
    assert (
        dispatcher.fire.await_args.kwargs["alert_type"]
        == "monthly_budget_50pct_reached"
    )


@pytest.mark.asyncio
async def test_milestone_skipped_when_not_fired(
    scheduler: AnalysisScheduler,
) -> None:
    fire = AsyncMock(
        return_value=MilestoneTransition(
            fraction=0.50,
            fired=False,
            alert_type="monthly_budget_50pct_reached",
        )
    )
    with patch(
        "backend.data.analysis_scheduler.assert_budget_allows",
        new_callable=AsyncMock,
        return_value=_ok_state(),
    ), patch(
        "backend.data.analysis_scheduler.get_monthly_budget_state",
        new_callable=AsyncMock,
        return_value=_monthly_state(0.50),
    ), patch(
        "backend.data.analysis_scheduler.SoftDegradeManager"
    ) as MgrCls, patch(
        "backend.data.analysis_scheduler.run_analysis",
        new_callable=AsyncMock,
        return_value=_sample_result(),
    ):
        mgr_instance = MagicMock()
        mgr_instance.maybe_fire_monthly_milestone = fire
        mgr_instance.activate_kimi_escalation_block = AsyncMock(return_value=False)
        MgrCls.return_value = mgr_instance
        await scheduler._run_and_persist("600519")
    scheduler._alert_dispatcher.fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_hard_breach_still_evaluates_monthly_milestone(
    scheduler: AnalysisScheduler,
) -> None:
    """Codex cycle 3 P3 — hard-breach branch must still dispatch milestones."""
    fire = AsyncMock(
        return_value=MilestoneTransition(
            fraction=1.00,
            fired=True,
            alert_type="monthly_budget_100pct_reached",
        )
    )
    with patch(
        "backend.data.analysis_scheduler.assert_budget_allows",
        new_callable=AsyncMock,
        side_effect=DailyBudgetExceededError("daily 20 CNY exceeded"),
    ), patch(
        "backend.data.analysis_scheduler.get_monthly_budget_state",
        new_callable=AsyncMock,
        return_value=_monthly_state(1.00),
    ), patch(
        "backend.data.analysis_scheduler.SoftDegradeManager"
    ) as MgrCls, patch(
        "backend.data.analysis_scheduler.run_analysis",
        new_callable=AsyncMock,
        return_value=_sample_result(),
    ) as mock_run:
        mgr_instance = MagicMock()
        mgr_instance.maybe_fire_monthly_milestone = fire
        mgr_instance.activate_kimi_escalation_block = AsyncMock(return_value=False)
        MgrCls.return_value = mgr_instance
        signal = await scheduler._run_and_persist("600519")

    # Hard breach: analysis skipped + cost-skip record persisted.
    assert signal is None
    mock_run.assert_not_called()
    scheduler._mongodb.save_analysis_record.assert_awaited_once()

    # H-003 cycle 3 P3 — but the monthly milestone WAS evaluated and fired.
    fire.assert_awaited_once()
    scheduler._alert_dispatcher.fire.assert_awaited_once()
    assert (
        scheduler._alert_dispatcher.fire.await_args.kwargs["alert_type"]
        == "monthly_budget_100pct_reached"
    )


@pytest.mark.asyncio
async def test_no_redis_skips_wiring_paths() -> None:
    """When Redis is missing, wiring paths must be no-ops, not errors."""
    scheduler = AnalysisScheduler(
        watchlist=AsyncMock(),
        services=MagicMock(),
        mongodb=AsyncMock(),
        redis_client=None,
        alert_dispatcher=AsyncMock(),
    )
    # Direct call should silently return.
    await scheduler._activate_kimi_escalation_block_safely(reason="x")
    await scheduler._maybe_emit_monthly_milestone_safely()
    scheduler._alert_dispatcher.fire.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_no_alert_dispatcher_skips_milestone() -> None:
    """When alert_dispatcher is None, milestone path must be a no-op."""
    scheduler = AnalysisScheduler(
        watchlist=AsyncMock(),
        services=MagicMock(),
        mongodb=AsyncMock(),
        redis_client=AsyncMock(),
        alert_dispatcher=None,
    )
    with patch(
        "backend.data.analysis_scheduler.get_monthly_budget_state",
        new_callable=AsyncMock,
        return_value=_monthly_state(0.50),
    ):
        await scheduler._maybe_emit_monthly_milestone_safely()
    # No exception, no crash.
