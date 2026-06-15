"""Tests for the AE-005 nightly quant-parameter evolution lane runner."""

from __future__ import annotations

import datetime as dt

import pytest

from backend.backtest.harness import BacktestResult
from backend.backtest.invariants import InvariantReport, InvariantVerdict
from backend.backtest.portfolio import EquitySnapshot
from backend.strategy_evolution.candidate_batch import CandidateBatch
from backend.strategy_evolution.experiment_registry import ExperimentRecord
from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_lane_runner import (
    FamilyShadowConfig,
    QuantParamEvolutionLane,
)
from backend.strategy_evolution.quant_param_lane import (
    BacktestDataUnavailableError,
    BacktestRunnerProtocol,
    ParamSet,
)
from backend.strategy_evolution.quant_param_search import SELECTOR_WEIGHTS_FAMILY

NOW = dt.datetime(2026, 6, 15, 22, 0, tzinfo=dt.UTC)
FAMILY = SELECTOR_WEIGHTS_FAMILY
MECH = EconomicMechanism.MOMENTUM_CONTINUATION
_INITIAL = 900_000_00


def _result(*, daily: list[float], pnl_cents: int, drawdown: float) -> BacktestResult:
    snap = EquitySnapshot(
        trade_date="20260601",
        cash_cents=_INITIAL,
        market_value_cents=0,
        total_equity_cents=_INITIAL + pnl_cents,
        positions=(),
    )
    return BacktestResult(
        trading_days=45,
        equity_curve=(snap,),
        daily_returns=tuple(daily),
        fills=(),
        fill_count=40,
        decision_vectors=(),
        invariant_report=InvariantReport(
            verdict=InvariantVerdict.CONSISTENT, violations=()
        ),
        avg_exposure_ratio=0.45,
        signal_count=40,
        monthly_turnover=2.5,
        initial_capital_cents=_INITIAL,
        final_equity_cents=_INITIAL + pnl_cents,
        max_drawdown_pct=drawdown,
        pnl_cents=pnl_cents,
        golden_vector_result=None,
    )


def _strong() -> BacktestResult:
    return _result(
        daily=[0.001 + 0.0012 + (i % 5) * 0.00002 for i in range(45)],
        pnl_cents=90_000_00,
        drawdown=0.03,
    )


def _champion() -> BacktestResult:
    return _result(daily=[0.001] * 45, pnl_cents=40_000_00, drawdown=0.05)


def _weak() -> BacktestResult:
    return _result(daily=[0.001] * 45, pnl_cents=40_000_00, drawdown=0.05)


class _FakeRegistry:
    def __init__(self) -> None:
        self.records: list[ExperimentRecord] = []

    async def count_trials(self, family: str | None = None) -> int:
        if family is None:
            return len(self.records)
        return sum(1 for r in self.records if r.family == family)

    async def register(self, record: ExperimentRecord) -> bool:
        if any(r.experiment_id == record.experiment_id for r in self.records):
            return False
        self.records.append(record)
        return True

    async def last_registered_at(self, family: str) -> dt.datetime | None:
        for r in reversed(self.records):
            if r.family == family:
                return r.registered_at
        return None


class _StubRunner:
    def __init__(self, *, sentinel_strong: bool, n_obs: int = 5_000) -> None:
        self._sentinel_strong = sentinel_strong
        self._n_obs = n_obs

    def run_champion(self) -> BacktestResult:
        return _champion()

    def run_candidate(self, candidate: ParamSet, *, sentinel: bool) -> BacktestResult:
        if sentinel:
            return _strong() if self._sentinel_strong else _weak()
        return _strong()

    def observation_count(self) -> int:
        return self._n_obs


class _StubFactory:
    def __init__(
        self,
        *,
        available: bool = True,
        sentinel_strong: bool = False,
        n_obs: int = 5_000,
    ) -> None:
        self._available = available
        self._sentinel_strong = sentinel_strong
        self._n_obs = n_obs

    def build(self, *, family: str, batch: CandidateBatch) -> BacktestRunnerProtocol:
        if not self._available:
            raise BacktestDataUnavailableError("historical PIT not ingested")
        return _StubRunner(sentinel_strong=self._sentinel_strong, n_obs=self._n_obs)

    def window(self) -> tuple[str, str]:
        return ("2015-01-05", "2026-06-01")


def _lane(
    factory: _StubFactory, registry: _FakeRegistry, **kwargs: object
) -> QuantParamEvolutionLane:
    families = (
        FamilyShadowConfig(
            family=FAMILY, mechanism=MECH, n_candidates=8, sentinel_count=2
        ),
    )
    return QuantParamEvolutionLane(
        registry=registry,
        runner_factory=factory,
        families=families,
        seed=20260615,
        **kwargs,  # type: ignore[arg-type]
    )


class TestNightlyRun:
    @pytest.mark.asyncio
    async def test_survivors_become_mandates(self) -> None:
        registry = _FakeRegistry()
        lane = _lane(_StubFactory(), registry)
        report = await lane.run_nightly(now=NOW)
        assert len(report.batch_evaluations) == 1
        assert report.total_mandates == 8
        assert report.integrity_breached is False
        # None promoted — every mandate is a PENDING forward-shadow declaration.
        for batch_eval in report.batch_evaluations:
            for mandate in batch_eval.mandates:
                assert mandate.is_shadow_window_complete(as_of=NOW.date()) is False

    @pytest.mark.asyncio
    async def test_experiments_registered_grows_cumulative_n(self) -> None:
        registry = _FakeRegistry()
        lane = _lane(_StubFactory(), registry)
        await lane.run_nightly(now=NOW)
        # 8 real candidates registered (sentinels are NOT trials).
        assert await registry.count_trials(FAMILY) == 8
        # Night 2 advances the seed by the cumulative count → 8 NEW candidates
        # (exploration, not a re-draw of the same batch) → the registry grows.
        await lane.run_nightly(now=NOW + dt.timedelta(days=1))
        assert await registry.count_trials(FAMILY) == 16

    @pytest.mark.asyncio
    async def test_data_unavailable_skips_family_not_crash(self) -> None:
        registry = _FakeRegistry()
        lane = _lane(_StubFactory(available=False), registry)
        report = await lane.run_nightly(now=NOW)
        assert report.batch_evaluations == ()
        assert len(report.skipped) == 1
        assert "data_unavailable" in report.skipped[0][1]

    @pytest.mark.asyncio
    async def test_sentinel_breach_flags_integrity(self) -> None:
        registry = _FakeRegistry()
        lane = _lane(_StubFactory(sentinel_strong=True), registry)
        report = await lane.run_nightly(now=NOW)
        assert report.integrity_breached is True
        assert report.total_mandates == 0  # fail-closed suppression

    @pytest.mark.asyncio
    async def test_minbtl_short_window_admits_nothing(self) -> None:
        registry = _FakeRegistry()
        lane = _lane(_StubFactory(n_obs=20), registry)
        report = await lane.run_nightly(now=NOW)
        assert report.total_mandates == 0
        assert report.batch_evaluations[0].batch_admitted is False

    @pytest.mark.asyncio
    async def test_night_candidate_budget_caps_and_logs_drops(self) -> None:
        registry = _FakeRegistry()
        lane = QuantParamEvolutionLane(
            registry=registry,
            runner_factory=_StubFactory(),
            families=(
                FamilyShadowConfig(family=FAMILY, mechanism=MECH, n_candidates=8),
            ),
            seed=1,
            max_candidates_per_night=3,
        )
        report = await lane.run_nightly(now=NOW)
        assert report.dropped_candidates == 5
        # Only the truncated 3 real candidates ran/registered.
        assert await registry.count_trials(FAMILY) == 3

    @pytest.mark.asyncio
    async def test_summary_is_a_string(self) -> None:
        registry = _FakeRegistry()
        report = await _lane(_StubFactory(), registry).run_nightly(now=NOW)
        assert isinstance(report.summary(), str)


__all__: list[str] = []
