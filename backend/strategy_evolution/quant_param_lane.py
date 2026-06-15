"""Three-stage quant-parameter promotion funnel (AE-005 / amendment §2.1-2.5).

The 5th evolution lane's decision core — pure, deterministic, zero-LLM. It maps
the deterministic-backtest :class:`BacktestResult` (produced off-isolation in
``backend.backtest``) to the promotion judgement's :class:`PromotionInputs` *at
this seam* — keeping ``evaluate_promotion`` itself pure — and runs the funnel:

* **Stage 1 — historical prefilter (batch + per candidate).** MinBTL admission
  (batch veto: history too short for ``N`` ⇒ reject the whole batch) + the
  locked :func:`evaluate_promotion` gates (DSR is the only statistical veto;
  the bootstrap CI / acceptance / anti-gaming / oracle gates are folded in).
* **Stage 2 — per-candidate verify.** Closed-form invariants (DIVERGENT ⇒
  reject) + golden-vector decision oracle + the mechanism-hypothesis gate
  (no pre-registered economic rationale ⇒ default-overfit ⇒ reject).
* **Stage 3 — declaration, NOT promotion.** A survivor earns a
  :class:`ForwardShadowMandate` to ENTER a 45-day frozen forward shadow; it is
  never auto-activated. The frozen forward shadow + a human pin remain the only
  promotion path (amendment §2.1 red line 1).

Plus the anti-self-deception layer: a null-edge sentinel that clears the
statistical gates breaks the control group — the lane then fail-closed
suppresses ALL mandates and surfaces the breach on the honest dashboard.

This module references ``objective_promotion`` and therefore stays inside
``strategy_evolution`` (redline [AB-008]); it imports ``backend.backtest`` types
at the seam (allowed — backtest is itself import-isolated).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

import structlog

from backend.backtest.harness import BacktestResult, to_acceptance_report
from backend.strategy_evolution.backtest_oracle import OracleVerdict
from backend.strategy_evolution.candidate_batch import CandidateBatch
from backend.strategy_evolution.disclosure_stats import (
    PBOResult,
    SPAResult,
    admit_batch,
    minimum_backtest_length,
    pbo_cscv,
    spa_disclosure,
)
from backend.strategy_evolution.experiment_registry import (
    ExperimentKind,
    compute_experiment_id,
)
from backend.strategy_evolution.forward_shadow_mandate import (
    MIN_FORWARD_SHADOW_CALENDAR_DAYS,
    PREDECLARED_FORWARD_SHADOW_METRICS,
    ForwardShadowMandate,
    HonestDashboard,
)
from backend.strategy_evolution.mechanism_registry import has_valid_mechanism
from backend.strategy_evolution.objective_promotion import (
    AntiGamingStats,
    PromotionDecision,
    PromotionInputs,
    evaluate_promotion,
)
from backend.strategy_evolution.quant_param_search import ParamSet

log = structlog.get_logger(component="strategy_evolution.quant_param_lane")

_PARAM_LANE_KIND = ExperimentKind.THRESHOLD_PARAM
"""Selector / allocation / tier weights are threshold-style parameters."""

# The prefilter/verify veto gates the lane reads off ``evaluate_promotion``
# (amendment §2.1-2.3 — "门做减法"). DSR is the ONLY statistical-significance
# veto; the rest are structural sanity (sample-size floors, anti-gaming, the
# rqalpha oracle). Two gates ``evaluate_promotion`` computes are deliberately
# NOT vetoes here:
#   * ``excess_ci_significant`` — redundant with the stricter DSR gate; keeping
#     both as an AND-stack triggers the small-A-share-sample power collapse the
#     amendment warns against (it stays in the audit record, disclosure only).
#   * ``acceptance_not_degraded`` — the operational 8-gate non-degradation is
#     structurally UNSATISFIABLE for a historical backtest: one of its four
#     strict-better metrics is ``execution_report_accuracy_rate``, which
#     ``to_acceptance_report`` synthesises as a perfect 1.0 for BOTH champion and
#     challenger (a replay has no execution failures), so "strictly better" can
#     never hold (1.0 > 1.0 is False) no matter how the strategy metrics differ.
#     The real strategy-metric non-degradation (drawdown / pnl / excess) is
#     deferred to the live forward shadow (stage 3) by design; DSR — which needs
#     a positive excess Sharpe — already blocks a candidate that loses to the
#     incumbent day over day.
_PREFILTER_VETO_GATES: tuple[str, ...] = (
    "window_trading_days",
    "window_sample_count",
    "deflated_sharpe",
    "anti_gaming_exposure",
    "anti_gaming_signal_count",
    "anti_gaming_turnover_band",
    "oracle_not_divergent",
)


def _all_gates_pass(decision: PromotionDecision, names: Sequence[str]) -> bool:
    by_name = {g.name: g.passed for g in decision.gates}
    return all(by_name.get(name, False) for name in names)


class BacktestDataUnavailableError(RuntimeError):
    """The PIT backtest data needed for a run is not ingested (fail-closed).

    Raised by a runner when the owner-gated historical PIT store is empty; the
    lane records a DATA_UNAVAILABLE outcome rather than fabricating a result.
    """


@runtime_checkable
class BacktestRunnerProtocol(Protocol):
    """Injected backtest runner — production wires the real PIT data + factors.

    The runner lives off ``strategy_evolution`` (it may touch ``backend.data``);
    the lane only consumes the pure :class:`BacktestResult` it returns. A
    sentinel candidate is run with a signal-free (shuffled) score stream so it
    carries no systematic edge.
    """

    def run_champion(self) -> BacktestResult:
        """Replay the pinned incumbent parameters over the window."""
        ...

    def run_candidate(self, candidate: ParamSet, *, sentinel: bool) -> BacktestResult:
        """Replay ``candidate`` (signal-free when ``sentinel``)."""
        ...

    def observation_count(self) -> int:
        """Number of daily observations in the backtest window."""
        ...


@dataclass(frozen=True)
class CandidateEvaluation:
    """One candidate's full, replayable funnel verdict (a complete audit row)."""

    param_hash: str
    is_sentinel: bool
    prefilter_decision: PromotionDecision
    minbtl_admitted: bool
    invariants_consistent: bool
    golden_vector_ok: bool
    mechanism_ok: bool
    excess_sharpe: float | None
    statistical_prefilter_pass: bool
    survived: bool
    mandate: ForwardShadowMandate | None


@dataclass(frozen=True)
class BatchEvaluation:
    """The batch's funnel result + the honest dashboard + disclosure stats."""

    batch_id: str
    family: str
    cumulative_n: int
    batch_admitted: bool
    evaluations: tuple[CandidateEvaluation, ...]
    mandates: tuple[ForwardShadowMandate, ...]
    sentinel_integrity_breached: bool
    pbo: PBOResult
    spa: SPAResult
    dashboard: HonestDashboard


# ---------------------------------------------------------------------------
# The seam: BacktestResult → PromotionInputs (keeps evaluate_promotion pure)
# ---------------------------------------------------------------------------


def _excess_sharpe(daily_excess: Sequence[float]) -> float | None:
    n = len(daily_excess)
    if n < 2:
        return None
    mean = sum(daily_excess) / n
    var = sum((x - mean) ** 2 for x in daily_excess) / (n - 1)
    if var <= 0.0:
        return None
    return mean / math.sqrt(var)


def daily_excess(
    champion: BacktestResult, challenger: BacktestResult
) -> tuple[float, ...]:
    """Challenger-minus-champion daily PnL over the SAME PIT window (pure).

    ``strict=True``: champion and challenger replay the SAME injected window, so
    a length mismatch is a broken invariant — fail loudly (the family's batch
    aborts and the cron logs it) rather than silently truncating to the shorter
    series while the window gates were checked against the full ``trading_days``.
    """
    return tuple(
        c - h
        for c, h in zip(challenger.daily_returns, champion.daily_returns, strict=True)
    )


def map_to_promotion_inputs(
    *,
    candidate: ParamSet,
    champion: BacktestResult,
    challenger: BacktestResult,
    window_start: str,
    window_end: str,
    n_trials: int,
    oracle_verdict: OracleVerdict,
    now: dt.datetime,
    benchmark_total_return: float = 0.0,
) -> PromotionInputs:
    """Map a champion/challenger backtest pair → ``PromotionInputs`` (pure seam).

    ``n_trials`` is the family's HONEST cumulative trial count (the
    append-only registry total including this batch); ``window_*`` are ISO
    dates; ``now`` is injected (no wall-clock). The experiment id is
    content-addressed over the design exactly as the registry would compute it.
    """
    champion_report = to_acceptance_report(
        champion, now=now, benchmark_total_return=benchmark_total_return
    )
    challenger_report = to_acceptance_report(
        challenger, now=now, benchmark_total_return=benchmark_total_return
    )
    experiment_id = compute_experiment_id(
        kind=_PARAM_LANE_KIND,
        family=candidate.family,
        hypothesis=_hypothesis(candidate),
        artifact_hash=candidate.param_hash,
        param_space=candidate.param_space_strings(),
        window_start=window_start,
        window_end=window_end,
    )
    anti_gaming = AntiGamingStats(
        avg_exposure_ratio=challenger.avg_exposure_ratio,
        signal_count=challenger.signal_count,
        monthly_turnover=challenger.monthly_turnover,
    )
    return PromotionInputs(
        kind=_PARAM_LANE_KIND,
        family=candidate.family,
        artifact_hash=candidate.param_hash,
        experiment_id=experiment_id,
        trading_days=challenger.trading_days,
        sample_count=challenger.signal_count,
        daily_excess=daily_excess(champion, challenger),
        champion_report=champion_report,
        challenger_report=challenger_report,
        anti_gaming=anti_gaming,
        n_trials=n_trials,
        oracle_verdict=oracle_verdict,
        evaluated_at=now,
    )


def _hypothesis(candidate: ParamSet) -> str:
    mech = candidate.mechanism.value if candidate.mechanism else "null_edge_sentinel"
    return f"quant param search: {candidate.family} via {mech}"


# ---------------------------------------------------------------------------
# Per-candidate funnel
# ---------------------------------------------------------------------------


def evaluate_candidate(
    *,
    candidate: ParamSet,
    champion: BacktestResult,
    challenger: BacktestResult,
    batch: CandidateBatch,
    n_trials: int,
    oracle_verdict: OracleVerdict,
    batch_admitted: bool,
    now: dt.datetime,
    calendar_start: str,
) -> CandidateEvaluation:
    """Run the full 3-stage funnel for one candidate (pure, replayable).

    A sentinel can NEVER survive (``not is_sentinel`` is an explicit survival
    term) and is judged additionally on whether the *statistical* gates caught
    it — that is the control-group integrity signal.
    """
    inputs = map_to_promotion_inputs(
        candidate=candidate,
        champion=champion,
        challenger=challenger,
        window_start=batch.window_start,
        window_end=batch.window_end,
        n_trials=n_trials,
        oracle_verdict=oracle_verdict,
        now=now,
    )
    decision = evaluate_promotion(inputs)

    invariants_ok = challenger.invariant_report.consistent
    golden_ok = (
        challenger.golden_vector_result is None
        or challenger.golden_vector_result.matched
    )
    mechanism_ok = has_valid_mechanism(candidate.family, candidate.mechanism)
    excess = _excess_sharpe(inputs.daily_excess)

    # The statistical prefilter = MinBTL admission + the reduced veto gate set
    # (DSR + structural sanity) + stage-2 closed invariants + golden-vector
    # oracle. It deliberately EXCLUDES the mechanism gate (a sentinel has no
    # mechanism by design — this is the control-group integrity signal).
    statistical_pass = (
        batch_admitted
        and _all_gates_pass(decision, _PREFILTER_VETO_GATES)
        and invariants_ok
        and golden_ok
    )
    survived = statistical_pass and mechanism_ok and not candidate.is_sentinel

    mandate = (
        ForwardShadowMandate(
            batch_id=batch.batch_id,
            family=candidate.family,
            mechanism=candidate.mechanism,  # non-None for real survivors
            candidate_param_hash=candidate.param_hash,
            frozen_param_values=candidate.values,
            predeclared_metrics=PREDECLARED_FORWARD_SHADOW_METRICS,
            prefilter_excess_sharpe=excess if excess is not None else 0.0,
            calendar_start_date=calendar_start,
            min_calendar_days=MIN_FORWARD_SHADOW_CALENDAR_DAYS,
            created_at=now,
        )
        if survived and candidate.mechanism is not None
        else None
    )
    return CandidateEvaluation(
        param_hash=candidate.param_hash,
        is_sentinel=candidate.is_sentinel,
        prefilter_decision=decision,
        minbtl_admitted=batch_admitted,
        invariants_consistent=invariants_ok,
        golden_vector_ok=golden_ok,
        mechanism_ok=mechanism_ok,
        excess_sharpe=excess,
        statistical_prefilter_pass=statistical_pass,
        survived=survived,
        mandate=mandate,
    )


# ---------------------------------------------------------------------------
# Batch funnel
# ---------------------------------------------------------------------------


def run_batch(
    *,
    batch: CandidateBatch,
    runner: BacktestRunnerProtocol,
    oracle_verdict: OracleVerdict,
    now: dt.datetime,
    calendar_start: str,
    days_since_last_promotion: int | None,
) -> BatchEvaluation:
    """Run the whole batch through the funnel (pure given a fixed runner).

    Admission (MinBTL) is a batch-level veto. Each candidate is then funnelled.
    If ANY sentinel cleared the statistical gates the control group is
    compromised — the lane fail-closed suppresses every mandate (a broken gate
    cannot be trusted to have judged the real candidates either).
    """
    champion = runner.run_champion()
    n_obs = runner.observation_count()
    n_trials = batch.cumulative_n_at_creation
    batch_admitted = admit_batch(n_trials=n_trials, n_observations=n_obs)

    evaluations: list[CandidateEvaluation] = []
    real_returns: list[tuple[float, ...]] = []
    real_excess: list[tuple[float, ...]] = []
    for candidate in batch.candidates:
        challenger = runner.run_candidate(candidate, sentinel=candidate.is_sentinel)
        evaluation = evaluate_candidate(
            candidate=candidate,
            champion=champion,
            challenger=challenger,
            batch=batch,
            n_trials=n_trials,
            oracle_verdict=oracle_verdict,
            batch_admitted=batch_admitted,
            now=now,
            calendar_start=calendar_start,
        )
        evaluations.append(evaluation)
        if not candidate.is_sentinel:
            real_returns.append(challenger.daily_returns)
            real_excess.append(daily_excess(champion, challenger))

    sentinels_passed = sum(
        1 for e in evaluations if e.is_sentinel and e.statistical_prefilter_pass
    )
    integrity_breached = sentinels_passed > 0

    if integrity_breached:
        # The control group failed — do not trust ANY survivor of this run.
        # Scrub survival at the SOURCE (each CandidateEvaluation), not just the
        # batch-level mandate list, so no downstream consumer (the experiment
        # registry's success flag, a future stage-3 dispatcher iterating
        # ``evaluations``) can read a populated mandate / survived=True off a
        # candidate from a compromised batch.
        log.critical(
            "sentinel_integrity_breached",
            batch_id=batch.batch_id[:12],
            family=batch.family,
            sentinels_passed=sentinels_passed,
        )
        evaluations = [replace(e, survived=False, mandate=None) for e in evaluations]
    mandates = tuple(e.mandate for e in evaluations if e.mandate is not None)

    pbo = (
        pbo_cscv(real_returns)
        if len(real_returns) >= 2
        else PBOResult(
            pbo=1.0, n_combinations=0, n_strategies=len(real_returns), median_logit=0.0
        )
    )
    spa = spa_disclosure(real_excess)

    dashboard = HonestDashboard(
        batch_id=batch.batch_id,
        family=batch.family,
        cumulative_n=n_trials,
        real_candidate_count=len(batch.real_candidates),
        sentinel_count=len(batch.sentinels),
        sentinels_passed=sentinels_passed,
        survivors=len(mandates),
        pbo=pbo.pbo,
        spa_p_value=spa.p_value,
        n_observations=n_obs,
        min_observations_required=minimum_backtest_length(n_trials=n_trials),
        batch_admitted=batch_admitted,
        days_since_last_promotion=days_since_last_promotion,
    )
    log.info(
        "quant_param_batch_evaluated",
        batch_id=batch.batch_id[:12],
        family=batch.family,
        survivors=len(mandates),
        admitted=batch_admitted,
        sentinels_passed=sentinels_passed,
    )
    return BatchEvaluation(
        batch_id=batch.batch_id,
        family=batch.family,
        cumulative_n=n_trials,
        batch_admitted=batch_admitted,
        evaluations=tuple(evaluations),
        mandates=mandates,
        sentinel_integrity_breached=integrity_breached,
        pbo=pbo,
        spa=spa,
        dashboard=dashboard,
    )


__all__ = [
    "BacktestDataUnavailableError",
    "BacktestRunnerProtocol",
    "BatchEvaluation",
    "CandidateEvaluation",
    "daily_excess",
    "evaluate_candidate",
    "map_to_promotion_inputs",
    "run_batch",
]
