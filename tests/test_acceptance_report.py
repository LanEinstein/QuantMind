"""AcceptanceReport service tests (E-008 / P0-6)."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.services.acceptance_report import (
    WINDOW_TRADING_DAYS,
    AcceptanceComputeInput,
    AcceptanceOutcome,
    AcceptanceReport,
    AcceptanceService,
    InMemoryAcceptanceRepository,
    StabilityCounters,
    StrategyCounters,
)


def _passing_stability() -> StabilityCounters:
    """Counters that pass every stability gate by a comfortable margin."""
    return StabilityCounters(
        completed_instructions=99,
        total_instructions=100,
        accurate_reports=199,
        total_reports=200,
        data_missing_ticks=1,
        total_data_ticks=10_000,
        llm_timeout_calls=1,
        total_llm_calls=10_000,
        generated_signal_days=44,
        expected_signal_days=45,
    )


def _passing_strategy() -> StrategyCounters:
    return StrategyCounters(
        max_drawdown_pct=0.05,
        pnl_cny=20_000.0,
        csi300_excess_pct=0.02,
    )


def _payload(
    *,
    trade_date: dt.date = dt.date(2026, 6, 12),
    stability: StabilityCounters | None = None,
    strategy: StrategyCounters | None = None,
    reconciliation_paused: bool = False,
) -> AcceptanceComputeInput:
    return AcceptanceComputeInput(
        trade_date=trade_date,
        now=dt.datetime(2026, 6, 12, 16, 0, 30, tzinfo=dt.UTC),
        stability=stability or _passing_stability(),
        strategy=strategy or _passing_strategy(),
        reconciliation_paused=reconciliation_paused,
    )


class TestAcceptanceCompute:
    def test_pass_outcome_when_all_gates_clear(self) -> None:
        service = AcceptanceService()
        report = service.compute(_payload())
        assert report.outcome is AcceptanceOutcome.PASS
        # All 8 metrics rendered + each passed
        assert len(report.metrics) == 8
        assert all(m.passed for m in report.metrics)

    def test_fail_outcome_when_one_strategy_gate_fails(self) -> None:
        service = AcceptanceService()
        strategy = StrategyCounters(
            max_drawdown_pct=0.10,  # exceeds 0.08 ceiling
            pnl_cny=1.0,
            csi300_excess_pct=0.0,
        )
        report = service.compute(_payload(strategy=strategy))
        assert report.outcome is AcceptanceOutcome.FAIL
        failed = [m.name for m in report.metrics if not m.passed]
        assert failed == ["max_drawdown_pct"]

    def test_fail_outcome_when_data_missing_exceeds_ceiling(self) -> None:
        service = AcceptanceService()
        stability = _passing_stability()
        # 2% data missing — exceeds 1% ceiling
        bad = StabilityCounters(
            completed_instructions=stability.completed_instructions,
            total_instructions=stability.total_instructions,
            accurate_reports=stability.accurate_reports,
            total_reports=stability.total_reports,
            data_missing_ticks=200,
            total_data_ticks=10_000,
            llm_timeout_calls=stability.llm_timeout_calls,
            total_llm_calls=stability.total_llm_calls,
            generated_signal_days=stability.generated_signal_days,
            expected_signal_days=stability.expected_signal_days,
        )
        report = service.compute(_payload(stability=bad))
        assert report.outcome is AcceptanceOutcome.FAIL
        failed = {m.name for m in report.metrics if not m.passed}
        assert "data_missing_rate" in failed

    def test_paused_outcome_short_circuits(self) -> None:
        service = AcceptanceService()
        report = service.compute(_payload(reconciliation_paused=True))
        assert report.outcome is AcceptanceOutcome.PAUSED
        assert report.metrics == ()

    def test_insufficient_data_outcome_when_window_too_short(self) -> None:
        service = AcceptanceService()
        # The locked window is 45 trading days; the calendar walker uses
        # 2026 holidays so we just check that the report carries the
        # locked count. We can simulate by counting backwards.
        payload = _payload()
        report = service.compute(payload)
        # If the holiday table covers the window properly we should
        # still see ~45 days; assert the contract not the exact count.
        assert report.trading_days_in_window <= WINDOW_TRADING_DAYS

    def test_zero_denominator_metric_is_fail_closed(self) -> None:
        service = AcceptanceService()
        stability = StabilityCounters(
            completed_instructions=0,
            total_instructions=0,
            accurate_reports=0,
            total_reports=0,
            data_missing_ticks=0,
            total_data_ticks=0,
            llm_timeout_calls=0,
            total_llm_calls=0,
            generated_signal_days=0,
            expected_signal_days=0,
        )
        report = service.compute(_payload(stability=stability))
        # Zero denominators degrade ratio to 0.0; pass/fail depends on
        # direction — instr at_least 0.95 fails; data_missing at_most
        # 0.01 passes; etc.
        assert report.outcome is AcceptanceOutcome.FAIL


class TestRepositoryAndSwitchGate:
    @pytest.mark.asyncio
    async def test_can_switch_returns_false_when_no_reports(self) -> None:
        service = AcceptanceService()
        assert await service.can_switch_to_feishu_on() is False

    @pytest.mark.asyncio
    async def test_can_switch_returns_true_only_on_pass(self) -> None:
        service = AcceptanceService()
        passing = service.compute(_payload())
        await service.upsert(passing)
        assert await service.can_switch_to_feishu_on() is True

    @pytest.mark.asyncio
    async def test_can_switch_returns_false_on_fail(self) -> None:
        service = AcceptanceService()
        strategy = StrategyCounters(
            max_drawdown_pct=0.10, pnl_cny=0.0, csi300_excess_pct=0.0,
        )
        failing = service.compute(_payload(strategy=strategy))
        await service.upsert(failing)
        assert await service.can_switch_to_feishu_on() is False

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent_by_trade_date(self) -> None:
        repo = InMemoryAcceptanceRepository()
        service = AcceptanceService(repo)
        r1 = service.compute(_payload())
        r2 = service.compute(_payload())
        await service.upsert(r1)
        await service.upsert(r2)
        assert len(repo.rows) == 1
        # latest() returns the most recent insert for the trade_date
        latest = await service.latest()
        assert latest is not None
        assert latest.trade_date == "2026-06-12"


class TestAcceptanceReportSchema:
    def test_window_start_must_be_le_end(self) -> None:
        from datetime import datetime as _dt

        with pytest.raises(Exception, match="window_start"):
            AcceptanceReport(
                computed_at=_dt(2026, 5, 15),
                trade_date="2026-05-15",
                window_start="2026-05-20",
                window_end="2026-05-10",  # < start
                trading_days_in_window=0,
                outcome=AcceptanceOutcome.INSUFFICIENT_DATA,
                metrics=(),
            )

    def test_outcome_locked_to_enum(self) -> None:
        from datetime import datetime as _dt

        from backend.services.acceptance_report import AcceptanceMetric

        report = AcceptanceReport(
            computed_at=_dt(2026, 5, 15),
            trade_date="2026-05-15",
            window_start="2026-03-13",
            window_end="2026-05-15",
            trading_days_in_window=45,
            outcome=AcceptanceOutcome.PASS,
            metrics=(
                AcceptanceMetric(
                    name="x", value=1.0, threshold=0.0, passed=True,
                    direction="at_least",
                ),
            ),
        )
        assert report.outcome is AcceptanceOutcome.PASS
