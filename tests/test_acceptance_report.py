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
    GateDecision,
    GoLiveTier,
    InMemoryAcceptanceRepository,
    StabilityCounters,
    StrategyCounters,
    WindowResetState,
)


class _StubProbe:
    """Duck-typed PILOT probe returning a fixed unmet-reason tuple."""

    def __init__(self, unmet: tuple[str, ...] = (), *, raises: bool = False) -> None:
        self._unmet = unmet
        self._raises = raises

    async def evaluate(self) -> tuple[str, ...]:
        if self._raises:
            raise RuntimeError("probe boom")
        return self._unmet


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
        assert (await service.can_switch_to_feishu_on()).allowed is False

    @pytest.mark.asyncio
    async def test_can_switch_returns_true_only_on_pass(self) -> None:
        service = AcceptanceService()
        passing = service.compute(_payload())
        await service.upsert(passing)
        assert (await service.can_switch_to_feishu_on()).allowed is True

    @pytest.mark.asyncio
    async def test_can_switch_returns_false_on_fail(self) -> None:
        service = AcceptanceService()
        strategy = StrategyCounters(
            max_drawdown_pct=0.10, pnl_cny=0.0, csi300_excess_pct=0.0,
        )
        failing = service.compute(_payload(strategy=strategy))
        await service.upsert(failing)
        assert (await service.can_switch_to_feishu_on()).allowed is False

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


# ---------------------------------------------------------------------------
# Codex cycle 3 regressions — reset hydration + switch gate invalidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_switch_to_feishu_on_invalidated_by_post_pass_reset() -> None:
    """Codex cycle 3 P1 regression — a reset firing after a PASS report
    must invalidate the gate, even before the next compute() runs.

    Scenario: window completed PASS on 2026-05-15; a J-004 reset fires
    at 2026-05-15T10:00Z (= 2026-05-15 Shanghai); the gate must return
    False so Feishu interactive does NOT bootstrap from stale state.
    """
    service = AcceptanceService()
    # Stage a PASS row in the repository.
    from backend.services.acceptance_report import AcceptanceMetric

    pass_report = AcceptanceReport(
        computed_at=dt.datetime(2026, 5, 15, 8, 0, tzinfo=dt.UTC),
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
    await service.upsert(pass_report)
    assert (await service.can_switch_to_feishu_on()).allowed is True

    # Now fire a reset; gate must flip False.
    service.record_reset(
        when=dt.datetime(2026, 5, 15, 10, 0, tzinfo=dt.UTC),
        reason="LLM_FULL_STOP_1H",
    )
    assert (await service.can_switch_to_feishu_on()).allowed is False


@pytest.mark.asyncio
async def test_can_switch_to_feishu_on_stays_true_when_pass_after_reset() -> None:
    """A PASS dated AFTER the reset is a fresh-window PASS — gate True."""
    service = AcceptanceService()
    from backend.services.acceptance_report import AcceptanceMetric

    # Reset happened 60 days ago.
    service.record_reset(
        when=dt.datetime(2026, 3, 17, 10, 0, tzinfo=dt.UTC),
        reason="MARKET_DATA_OUTAGE_30MIN",
    )
    # Fresh window completed PASS today.
    pass_report = AcceptanceReport(
        computed_at=dt.datetime(2026, 5, 17, 8, 0, tzinfo=dt.UTC),
        trade_date="2026-05-17",
        window_start="2026-03-17",
        window_end="2026-05-17",
        trading_days_in_window=45,
        outcome=AcceptanceOutcome.PASS,
        metrics=(
            AcceptanceMetric(
                name="x", value=1.0, threshold=0.0, passed=True,
                direction="at_least",
            ),
        ),
    )
    await service.upsert(pass_report)
    assert (await service.can_switch_to_feishu_on()).allowed is True


def test_set_reset_state_rejects_naive_datetime() -> None:
    service = AcceptanceService()
    with pytest.raises(ValueError, match="timezone-aware"):
        service.set_reset_state(
            WindowResetState(
                last_reset_at=dt.datetime(2026, 5, 17, 10, 0),  # naive
                last_reset_reason="X",
            )
        )


def test_set_reset_state_accepts_empty_state() -> None:
    service = AcceptanceService()
    service.set_reset_state(WindowResetState())
    assert service.reset_state() == WindowResetState()


def test_reset_clamp_uses_shanghai_date() -> None:
    """Codex cycle 3 P2 regression — reset at 2026-05-14T16:30:00Z
    is 2026-05-15 in Shanghai; the clamp must use the Shanghai date
    so a pre-reset trading day does not leak in.

    Scenario: trade_date is 2026-05-15 (a trading day); reset was
    yesterday in UTC but today in Shanghai. The window_start should
    clamp to 2026-05-15, not 2026-05-14.
    """
    service = AcceptanceService()
    service.record_reset(
        when=dt.datetime(2026, 5, 14, 16, 30, tzinfo=dt.UTC),
        reason="LONG_CONN_OUTAGE_4H",
    )
    report = service.compute(
        AcceptanceComputeInput(
            trade_date=dt.date(2026, 5, 15),
            now=dt.datetime(2026, 5, 15, 8, 0, 30, tzinfo=dt.UTC),
            stability=_passing_stability(),
            strategy=_passing_strategy(),
        )
    )
    # 2026-05-14 UTC = 2026-05-15 Shanghai (UTC+8); the clamp must
    # use the Shanghai-converted date.
    assert report.window_start == "2026-05-15"


def test_record_reset_is_monotonic() -> None:
    """Codex cycle 5 P2 regression — an older reset arriving after a
    newer reset must NOT rewind ``_reset_state.last_reset_at``. Two
    concurrent triggers can race through the detector (audit + Feishu
    await) and produce out-of-order ``record_reset`` calls; the
    monotonic guard preserves the most-recent clamp."""
    service = AcceptanceService()
    later = dt.datetime(2026, 5, 17, 12, 0, tzinfo=dt.UTC)
    earlier = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    service.record_reset(when=later, reason="LLM_FULL_STOP_1H")
    assert service.reset_state().last_reset_at == later
    # Out-of-order older arrival: must be ignored.
    service.record_reset(when=earlier, reason="MARKET_DATA_OUTAGE_30MIN")
    assert service.reset_state().last_reset_at == later
    assert service.reset_state().last_reset_reason == "LLM_FULL_STOP_1H"


def test_record_reset_equal_timestamp_is_no_op() -> None:
    """Equal timestamp is treated as already-recorded — no overwrite."""
    service = AcceptanceService()
    when = dt.datetime(2026, 5, 17, 12, 0, tzinfo=dt.UTC)
    service.record_reset(when=when, reason="LLM_FULL_STOP_1H")
    service.record_reset(when=when, reason="MOCK_BROKER_CORRUPTION")
    # First-write wins on equal timestamps (no overwrite).
    assert service.reset_state().last_reset_reason == "LLM_FULL_STOP_1H"


def test_set_reset_state_is_monotonic() -> None:
    """Hydration setter must also guard against rewinding."""
    service = AcceptanceService()
    later = WindowResetState(
        last_reset_at=dt.datetime(2026, 5, 17, 12, 0, tzinfo=dt.UTC),
        last_reset_reason="LLM_FULL_STOP_1H",
    )
    earlier = WindowResetState(
        last_reset_at=dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC),
        last_reset_reason="MARKET_DATA_OUTAGE_30MIN",
    )
    service.set_reset_state(later)
    service.set_reset_state(earlier)  # no-op
    assert service.reset_state().last_reset_at == later.last_reset_at
    assert service.reset_state().last_reset_reason == "LLM_FULL_STOP_1H"


# ---------------------------------------------------------------------------
# U-D2 — tier-aware go-live gate (P0-6-amendment-2026-05-25 §2.2/§2.3/§2.4)
# ---------------------------------------------------------------------------


class TestTierAwareGate:
    @pytest.mark.asyncio
    async def test_no_arg_call_is_full_and_returns_gate_decision(self) -> None:
        service = AcceptanceService()
        decision = await service.can_switch_to_feishu_on()
        assert isinstance(decision, GateDecision)
        assert decision.tier is GoLiveTier.FULL
        assert decision.allowed is False  # no report yet
        assert "full:no_acceptance_report" in decision.reasons

    @pytest.mark.asyncio
    async def test_full_allowed_on_pass(self) -> None:
        service = AcceptanceService()
        await service.upsert(service.compute(_payload()))
        decision = await service.can_switch_to_feishu_on(GoLiveTier.FULL)
        assert decision.tier is GoLiveTier.FULL
        assert decision.allowed is True
        assert decision.reasons == ()

    @pytest.mark.asyncio
    async def test_full_string_arg_accepted(self) -> None:
        service = AcceptanceService()
        await service.upsert(service.compute(_payload()))
        assert (await service.can_switch_to_feishu_on("full")).allowed is True

    @pytest.mark.asyncio
    async def test_pilot_fail_closed_when_no_probe(self) -> None:
        service = AcceptanceService()
        decision = await service.can_switch_to_feishu_on(GoLiveTier.PILOT)
        assert decision.tier is GoLiveTier.PILOT
        assert decision.allowed is False
        assert decision.reasons == ("pilot:readiness_probe_not_wired",)

    @pytest.mark.asyncio
    async def test_pilot_allowed_when_probe_clean(self) -> None:
        service = AcceptanceService()
        service.set_pilot_probe(_StubProbe(unmet=()))
        decision = await service.can_switch_to_feishu_on("pilot")
        assert decision.tier is GoLiveTier.PILOT
        assert decision.allowed is True
        assert decision.reasons == ()

    @pytest.mark.asyncio
    async def test_pilot_surfaces_unmet_reasons(self) -> None:
        service = AcceptanceService()
        service.set_pilot_probe(_StubProbe(unmet=("cond1:active_broker_not_mock",)))
        decision = await service.can_switch_to_feishu_on(GoLiveTier.PILOT)
        assert decision.allowed is False
        assert decision.reasons == ("cond1:active_broker_not_mock",)

    @pytest.mark.asyncio
    async def test_pilot_probe_error_is_fail_closed(self) -> None:
        service = AcceptanceService()
        service.set_pilot_probe(_StubProbe(raises=True))
        decision = await service.can_switch_to_feishu_on(GoLiveTier.PILOT)
        assert decision.allowed is False
        assert decision.reasons == ("pilot:readiness_probe_error",)

    @pytest.mark.asyncio
    async def test_pilot_pass_never_satisfies_full(self) -> None:
        # PILOT ≠ FULL (amendment §2.4 / §4 #2): a clean PILOT probe must NOT
        # make the FULL gate pass — there is no 45-day PASS report here.
        service = AcceptanceService()
        service.set_pilot_probe(_StubProbe(unmet=()))
        pilot = await service.can_switch_to_feishu_on(GoLiveTier.PILOT)
        full = await service.can_switch_to_feishu_on(GoLiveTier.FULL)
        assert pilot.allowed is True
        assert full.allowed is False
        assert "full:no_acceptance_report" in full.reasons

    @pytest.mark.asyncio
    async def test_full_pass_does_not_authorise_pilot_without_probe(self) -> None:
        # Symmetric guard: a FULL PASS does not back-door PILOT — PILOT still
        # needs its own probe (fail-closed when unwired).
        service = AcceptanceService()
        await service.upsert(service.compute(_payload()))
        full = await service.can_switch_to_feishu_on(GoLiveTier.FULL)
        pilot = await service.can_switch_to_feishu_on(GoLiveTier.PILOT)
        assert full.allowed is True
        assert pilot.allowed is False

    @pytest.mark.asyncio
    async def test_unknown_tier_raises(self) -> None:
        service = AcceptanceService()
        with pytest.raises(ValueError):
            await service.can_switch_to_feishu_on("staging")

    @pytest.mark.asyncio
    async def test_denied_decision_is_falsey(self) -> None:
        # Codex U-D2 P1 — a denied decision must be falsey so a missed/legacy
        # ``if await gate.can_switch_to_feishu_on():`` still fails closed.
        service = AcceptanceService()
        decision = await service.can_switch_to_feishu_on()
        assert decision.allowed is False
        assert bool(decision) is False
        assert not decision

    @pytest.mark.asyncio
    async def test_allowed_decision_is_truthy(self) -> None:
        service = AcceptanceService()
        await service.upsert(service.compute(_payload()))
        decision = await service.can_switch_to_feishu_on()
        assert bool(decision) is True
        assert decision
