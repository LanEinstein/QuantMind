"""Tests for the AE-005 three-stage quant-parameter promotion funnel."""

from __future__ import annotations

import dataclasses
import datetime as dt

from backend.backtest.golden_vector import GoldenVectorResult
from backend.backtest.harness import BacktestResult
from backend.backtest.invariants import InvariantReport, InvariantVerdict
from backend.backtest.portfolio import EquitySnapshot
from backend.strategy_evolution.backtest_oracle import OracleVerdict
from backend.strategy_evolution.candidate_batch import assemble_batch
from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_lane import (
    evaluate_candidate,
    map_to_promotion_inputs,
    run_batch,
)
from backend.strategy_evolution.quant_param_search import (
    SELECTOR_WEIGHTS_FAMILY,
    ParamExperimentProducer,
    ParamSet,
)
from backend.strategy_evolution.sentinel import make_sentinels

NOW = dt.datetime(2026, 6, 2, 22, 0, tzinfo=dt.UTC)
FAMILY = SELECTOR_WEIGHTS_FAMILY
MECH = EconomicMechanism.MOMENTUM_CONTINUATION
_INITIAL = 900_000_00


def _result(
    *,
    daily: list[float],
    pnl_cents: int,
    drawdown: float,
    invariant_ok: bool = True,
    golden_matched: bool | None = None,
    exposure: float = 0.45,
    signal_count: int = 40,
    turnover: float = 2.5,
    trading_days: int = 45,
) -> BacktestResult:
    snap = EquitySnapshot(
        trade_date="20260601",
        cash_cents=_INITIAL,
        market_value_cents=0,
        total_equity_cents=_INITIAL + pnl_cents,
        positions=(),
    )
    golden = (
        None
        if golden_matched is None
        else GoldenVectorResult(matched=golden_matched, divergences=())
    )
    return BacktestResult(
        trading_days=trading_days,
        equity_curve=(snap,),
        daily_returns=tuple(daily),
        fills=(),
        fill_count=signal_count,
        decision_vectors=(),
        invariant_report=InvariantReport(
            verdict=InvariantVerdict.CONSISTENT
            if invariant_ok
            else InvariantVerdict.DIVERGENT,
            violations=() if invariant_ok else ("forced",),
        ),
        avg_exposure_ratio=exposure,
        signal_count=signal_count,
        monthly_turnover=turnover,
        initial_capital_cents=_INITIAL,
        final_equity_cents=_INITIAL + pnl_cents,
        max_drawdown_pct=drawdown,
        pnl_cents=pnl_cents,
        golden_vector_result=golden,
    )


def _champion() -> BacktestResult:
    return _result(daily=[0.001] * 45, pnl_cents=40_000_00, drawdown=0.05)


def _strong_challenger(**overrides: object) -> BacktestResult:
    # Steady, low-noise positive excess over the champion → clears DSR.
    daily = [0.001 + 0.0012 + (i % 5) * 0.00002 for i in range(45)]
    kwargs: dict[str, object] = {
        "daily": daily,
        "pnl_cents": 90_000_00,
        "drawdown": 0.03,
    }
    kwargs.update(overrides)
    return _result(**kwargs)  # type: ignore[arg-type]


def _weak_challenger() -> BacktestResult:
    # No edge over the champion (excess ~0) → fails DSR.
    return _result(daily=[0.001] * 45, pnl_cents=40_000_00, drawdown=0.05)


class _FixtureRunner:
    """Deterministic injected runner returning crafted backtest results."""

    def __init__(
        self,
        *,
        champion: BacktestResult,
        result_by_hash: dict[str, BacktestResult],
        default: BacktestResult,
        n_obs: int = 5_000,
    ) -> None:
        self._champion = champion
        self._by_hash = result_by_hash
        self._default = default
        self._n_obs = n_obs

    def run_champion(self) -> BacktestResult:
        return self._champion

    def run_candidate(self, candidate: ParamSet, *, sentinel: bool) -> BacktestResult:
        return self._by_hash.get(candidate.param_hash, self._default)

    def observation_count(self) -> int:
        return self._n_obs


def _batch(*, n_real: int = 4, n_sentinel: int = 0, seed: int = 1):
    producer = ParamExperimentProducer(family=FAMILY)
    real = producer.produce(seed=seed, n_candidates=n_real, mechanism=MECH)
    sentinels = make_sentinels(family=FAMILY, count=n_sentinel, seed=seed)
    return assemble_batch(
        family=FAMILY,
        seed=seed,
        declared_n=n_real,
        window_start="2015-01-05",
        window_end="2026-06-01",
        cumulative_n_at_creation=n_real,
        mechanism=MECH,
        real_candidates=real,
        sentinels=sentinels,
    )


class TestSeam:
    def test_promotion_inputs_shape(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        inputs = map_to_promotion_inputs(
            candidate=cand,
            champion=_champion(),
            challenger=_strong_challenger(),
            window_start="2015-01-05",
            window_end="2026-06-01",
            n_trials=8,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
        )
        assert len(inputs.daily_excess) == 45
        assert len(inputs.experiment_id) == 64
        assert inputs.artifact_hash == cand.param_hash
        assert inputs.n_trials == 8


class TestPerCandidate:
    def _eval(
        self,
        candidate,
        challenger,
        *,
        batch_admitted=True,
        oracle=OracleVerdict.CONSISTENT,
    ):
        batch = _batch(n_real=1)
        return evaluate_candidate(
            candidate=candidate,
            champion=_champion(),
            challenger=challenger,
            batch=batch,
            n_trials=8,
            oracle_verdict=oracle,
            batch_admitted=batch_admitted,
            now=NOW,
            calendar_start="2026-06-02",
        )

    def test_real_survivor_gets_pending_mandate(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _strong_challenger())
        assert ev.survived is True
        assert ev.mandate is not None
        # 历史门过但冻结 shadow 未过 → 不晋升: the mandate is PENDING, not complete.
        assert ev.mandate.is_shadow_window_complete(as_of=NOW.date()) is False
        assert ev.mandate.candidate_param_hash == cand.param_hash

    def test_no_mechanism_candidate_rejected(self) -> None:
        # 无机制假设 → 拒晋升: statistically strong but no mechanism → no survive.
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=None,
        )
        ev = self._eval(cand, _strong_challenger())
        assert ev.statistical_prefilter_pass is True
        assert ev.mechanism_ok is False
        assert ev.survived is False
        assert ev.mandate is None

    def test_weak_candidate_fails_dsr(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _weak_challenger())
        assert ev.survived is False
        assert ev.mandate is None

    def test_divergent_invariants_rejected(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _strong_challenger(invariant_ok=False))
        assert ev.invariants_consistent is False
        assert ev.survived is False

    def test_golden_vector_mismatch_rejected(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _strong_challenger(golden_matched=False))
        assert ev.golden_vector_ok is False
        assert ev.survived is False

    def test_divergent_oracle_rejected(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _strong_challenger(), oracle=OracleVerdict.DIVERGENT)
        assert ev.survived is False

    def test_minbtl_rejected_batch_blocks_survival(self) -> None:
        cand = ParamSet(
            family=FAMILY,
            values=(("selector.weight_momentum", 0.4),),
            mechanism=MECH,
        )
        ev = self._eval(cand, _strong_challenger(), batch_admitted=False)
        assert ev.minbtl_admitted is False
        assert ev.survived is False


class TestBatch:
    def test_minbtl_short_window_rejects_whole_batch(self) -> None:
        batch = _batch(n_real=4)
        # Many cumulative trials but a tiny window → admission fails.
        batch = dataclasses.replace(batch, cumulative_n_at_creation=4096)
        runner = _FixtureRunner(
            champion=_champion(),
            result_by_hash={},
            default=_strong_challenger(),
            n_obs=20,
        )
        result = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=None,
        )
        assert result.batch_admitted is False
        assert result.mandates == ()
        assert result.dashboard.batch_admitted is False

    def test_real_candidates_can_survive_into_mandates(self) -> None:
        batch = _batch(n_real=4, seed=3)
        by_hash = {c.param_hash: _strong_challenger() for c in batch.real_candidates}
        runner = _FixtureRunner(
            champion=_champion(),
            result_by_hash=by_hash,
            default=_strong_challenger(),
        )
        result = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=12,
        )
        assert result.batch_admitted is True
        assert len(result.mandates) == 4
        assert result.sentinel_integrity_breached is False
        assert all(
            not m.is_shadow_window_complete(as_of=NOW.date()) for m in result.mandates
        )
        assert result.dashboard.days_since_last_promotion == 12

    def test_sentinel_passing_breaches_integrity_and_suppresses_all_mandates(
        self,
    ) -> None:
        # A broken control group: a sentinel is (wrongly) given a strong-edge
        # result so it clears the statistical gates. The lane must flag the
        # breach AND fail-closed suppress EVERY mandate (incl. real survivors).
        batch = _batch(n_real=2, n_sentinel=1, seed=5)
        by_hash: dict[str, BacktestResult] = {}
        for c in batch.candidates:
            by_hash[c.param_hash] = _strong_challenger()
        runner = _FixtureRunner(
            champion=_champion(),
            result_by_hash=by_hash,
            default=_strong_challenger(),
        )
        result = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=None,
        )
        assert result.sentinel_integrity_breached is True
        assert result.dashboard.sentinels_passed == 1
        assert result.dashboard.sentinel_integrity_ok is False
        assert result.mandates == ()  # fail-closed: trust nothing from this run
        # The scrub is at the SOURCE — no per-candidate survived/mandate leaks
        # through for a downstream consumer iterating .evaluations.
        assert all(not e.survived for e in result.evaluations)
        assert all(e.mandate is None for e in result.evaluations)

    def test_healthy_sentinel_does_not_pass(self) -> None:
        # The intended case: sentinels get a no-edge result → never pass.
        batch = _batch(n_real=2, n_sentinel=2, seed=7)
        by_hash: dict[str, BacktestResult] = {}
        for c in batch.real_candidates:
            by_hash[c.param_hash] = _strong_challenger()
        for c in batch.sentinels:
            by_hash[c.param_hash] = _weak_challenger()
        runner = _FixtureRunner(
            champion=_champion(),
            result_by_hash=by_hash,
            default=_weak_challenger(),
        )
        result = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=None,
        )
        assert result.sentinel_integrity_breached is False
        assert result.dashboard.sentinels_passed == 0
        assert len(result.mandates) == 2

    def test_deterministic(self) -> None:
        batch = _batch(n_real=3, seed=9)
        by_hash = {c.param_hash: _strong_challenger() for c in batch.real_candidates}
        runner = _FixtureRunner(
            champion=_champion(), result_by_hash=by_hash, default=_strong_challenger()
        )
        a = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=None,
        )
        b = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.CONSISTENT,
            now=NOW,
            calendar_start="2026-06-02",
            days_since_last_promotion=None,
        )
        assert a.dashboard == b.dashboard
        assert [m.candidate_param_hash for m in a.mandates] == [
            m.candidate_param_hash for m in b.mandates
        ]


__all__: list[str] = []
