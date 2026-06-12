"""ObjectivePromotionEngine — deterministic sim-scope promotion judge
(AB-002 / P2-2-amendment-2026-06-12 §1.1).

Owner decision 2026-06-12: within the simulation_auto domain, promotion
is decided by OBJECTIVE, pre-defined, replayable criteria — never by a
human feel and NEVER by an LLM (every input below is a deterministic
metric; the module imports no LLM surface and takes no text). The
feishu_interactive (real-execution) domain keeps the human gate — that
boundary is enforced downstream at the activation layer (AB-003).

The six gate families (amendment §1.1, all must pass):

1. tiered window + sample-size double floor (owner decision);
2. challenger-vs-incumbent excess significant by bootstrap CI (the
   locked ShadowChain CI — seed 20260518, 1000 resamples);
3. multiple-testing deflation: the excess Sharpe must clear the
   deflated-Sharpe bar given the family's CUMULATIVE registered trial
   count (failures included, AB-001) — Bonferroni-style tightening;
4. acceptance 8-gate non-degradation + drawdown not worse (reuses the
   locked ``evaluate_challenger`` ruling verbatim);
5. anti-gaming: minimum average exposure / minimum signal count /
   turnover band — a do-nothing strategy must not win on drawdown;
6. rqalpha differential oracle (R-002): DIVERGENT is a hard reject;
   ORACLE_UNAVAILABLE passes but is recorded un-cross-checked (the
   harsh fill model, AB-007, is the second engine-overfit defence).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import structlog
from pydantic import BaseModel, ConfigDict, Field

from backend.services.acceptance_report import AcceptanceReport
from backend.services.shadow_chain import (
    compute_bootstrap_pnl_ci_95pct,
    evaluate_challenger,
)
from backend.strategy_evolution.anti_overfit import (
    DSR_CONFIDENCE_FLOOR,
    deflated_sharpe_ratio,
)
from backend.strategy_evolution.backtest_oracle import OracleVerdict
from backend.strategy_evolution.experiment_registry import ExperimentKind

log = structlog.get_logger(
    component="strategy_evolution.objective_promotion"
)


@dataclass(frozen=True)
class WindowGate:
    """Tiered double floor (owner decision 2026-06-12)."""

    min_trading_days: int
    min_samples: int


TIERED_WINDOW_GATES: dict[ExperimentKind, WindowGate] = {
    # Threshold parameters: >=15 trading days AND >=30 trigger-level
    # samples (owner-locked).
    ExperimentKind.THRESHOLD_PARAM: WindowGate(15, 30),
    # Prompt / LLM harness: >=20 trading days AND >=15 independent
    # fills (owner-locked).
    ExperimentKind.PROMPT: WindowGate(20, 15),
    # New strategy code: 45 trading days unchanged (P0-6 verbatim);
    # the amendment locks only the days — 30 samples is the engine
    # default mirroring the threshold tier (a 45-day strategy with
    # fewer fills fails anti-gaming's signal floor anyway).
    ExperimentKind.STRATEGY_CODE: WindowGate(45, 30),
}

MIN_AVG_EXPOSURE_RATIO = 0.10
"""Anti-gaming: a challenger must hold positions ≥10% of equity on
average — an empty-portfolio "strategy" wins every drawdown gate by
construction and must never promote."""

TURNOVER_BAND_MONTHLY = (0.2, 15.0)
"""Anti-gaming: monthly turnover (traded notional / equity) must stay
inside a sane band — near-zero means the shadow never traded the
hypothesis; absurdly high means the result is friction-model noise."""


class AntiGamingStats(BaseModel):
    """Deterministic exposure/turnover observations from the shadow run."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    avg_exposure_ratio: float = Field(ge=0.0)
    signal_count: int = Field(ge=0)
    monthly_turnover: float = Field(ge=0.0)


class GateResult(BaseModel):
    """One named promotion-gate verdict."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    passed: bool
    detail: str = Field(max_length=256)


class PromotionDecision(BaseModel):
    """Deterministic, replayable promotion ruling."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    kind: ExperimentKind
    family: str = Field(min_length=1, max_length=128)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    promoted: bool
    gates: tuple[GateResult, ...]
    oracle_cross_checked: bool
    inputs_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime

    @property
    def failed_gates(self) -> tuple[str, ...]:
        return tuple(g.name for g in self.gates if not g.passed)


@dataclass(frozen=True)
class PromotionInputs:
    """Everything the judgement consumes — plain deterministic data.

    ``daily_excess`` is the challenger-minus-incumbent daily PnL series
    over the SAME PIT inputs (counterfactual replay). ``n_trials`` is
    the family's cumulative registered experiment count (AB-001,
    failures included).
    """

    kind: ExperimentKind
    family: str
    artifact_hash: str
    experiment_id: str
    trading_days: int
    sample_count: int
    daily_excess: tuple[float, ...]
    champion_report: AcceptanceReport
    challenger_report: AcceptanceReport
    anti_gaming: AntiGamingStats
    n_trials: int
    oracle_verdict: OracleVerdict
    evaluated_at: datetime


def _report_semantics(report: AcceptanceReport) -> dict[str, object]:
    """The decision-relevant content of an acceptance report.

    Row-identity fields (report_id, computed_at) are excluded so two
    evaluations over the same METRIC content share a digest — the
    digest addresses the judgement's inputs, not the storage row.
    """
    return {
        "outcome": report.outcome.value,
        "window_start": report.window_start,
        "window_end": report.window_end,
        "trading_days_in_window": report.trading_days_in_window,
        "metrics": [
            {
                "name": m.name,
                "value": round(m.value, 8),
                "threshold": m.threshold,
                "passed": m.passed,
                "direction": m.direction,
            }
            for m in sorted(report.metrics, key=lambda m: m.name)
        ],
    }


def _inputs_digest(inputs: PromotionInputs) -> str:
    payload = json.dumps(
        {
            "kind": inputs.kind.value,
            "family": inputs.family,
            "artifact_hash": inputs.artifact_hash,
            "experiment_id": inputs.experiment_id,
            "trading_days": inputs.trading_days,
            "sample_count": inputs.sample_count,
            "daily_excess": [round(x, 6) for x in inputs.daily_excess],
            "champion": _report_semantics(inputs.champion_report),
            "challenger": _report_semantics(inputs.challenger_report),
            "anti_gaming": inputs.anti_gaming.model_dump(mode="json"),
            "n_trials": inputs.n_trials,
            "oracle_verdict": inputs.oracle_verdict.value,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _excess_sharpe(daily_excess: Sequence[float]) -> float | None:
    """Daily Sharpe of the excess series (None when undefined)."""
    n = len(daily_excess)
    if n < 2:
        return None
    mean = sum(daily_excess) / n
    variance = sum((x - mean) ** 2 for x in daily_excess) / (n - 1)
    if variance <= 0.0:
        return None
    return mean / math.sqrt(variance)


def evaluate_promotion(inputs: PromotionInputs) -> PromotionDecision:
    """The single deterministic promotion judgement (pure function).

    Same inputs → same decision, bit for bit (the bootstrap CI uses
    the locked fixed seed). Every gate is evaluated and named even
    after a failure so the decision is a complete audit record.
    """
    gates: list[GateResult] = []
    window = TIERED_WINDOW_GATES[inputs.kind]

    gates.append(
        GateResult(
            name="window_trading_days",
            passed=inputs.trading_days >= window.min_trading_days,
            detail=(
                f"{inputs.trading_days}d >= {window.min_trading_days}d"
            ),
        )
    )
    gates.append(
        GateResult(
            name="window_sample_count",
            passed=inputs.sample_count >= window.min_samples,
            detail=f"{inputs.sample_count} >= {window.min_samples}",
        )
    )

    # Gate 2 — bootstrap CI on the excess series (locked ShadowChain
    # implementation: percentile, 1000 resamples, seed 20260518).
    if len(inputs.daily_excess) >= 2:
        ci_low, ci_high = compute_bootstrap_pnl_ci_95pct(
            list(inputs.daily_excess)
        )
        ci_passed = ci_low > 0.0
        ci_detail = f"95% CI [{ci_low:.4f}, {ci_high:.4f}], need low > 0"
    else:
        ci_passed = False
        ci_detail = (
            f"excess series has {len(inputs.daily_excess)} points; "
            f"CI undefined (fail-closed)"
        )
    gates.append(
        GateResult(
            name="excess_ci_significant",
            passed=ci_passed,
            detail=ci_detail,
        )
    )

    # Gate 3 — multiple-testing deflation. Variance of the SR estimator
    # under approximate normality: V[SR] ≈ (1 + SR²/2) / n.
    sharpe = _excess_sharpe(inputs.daily_excess)
    if sharpe is None:
        dsr_passed = False
        dsr_detail = "excess Sharpe undefined (fail-closed)"
    else:
        n = len(inputs.daily_excess)
        variance_of_sr = (1.0 + 0.5 * sharpe**2) / n
        dsr = deflated_sharpe_ratio(
            sharpe,
            n_trials=max(1, inputs.n_trials),
            variance_of_sr=variance_of_sr,
            n_samples=n,
        )
        dsr_passed = dsr >= DSR_CONFIDENCE_FLOOR
        dsr_detail = (
            f"DSR {dsr:.4f} (SR {sharpe:.4f}, trials "
            f"{inputs.n_trials}) >= {DSR_CONFIDENCE_FLOOR}"
        )
    gates.append(
        GateResult(
            name="deflated_sharpe",
            passed=dsr_passed,
            detail=dsr_detail,
        )
    )

    # Gate 4 — the locked 8-gate challenger ruling (strict-better 4 +
    # no-regression 4, drawdown included in the strict set).
    verdict = evaluate_challenger(
        champion=inputs.champion_report,
        challenger=inputs.challenger_report,
    )
    acceptance_ok = (
        verdict.challenger_passed_all_gates
        and verdict.challenger_strictly_better_on_all_four
        and verdict.challenger_within_tolerance_on_all_four
    )
    gates.append(
        GateResult(
            name="acceptance_not_degraded",
            passed=acceptance_ok,
            detail=(
                f"challenger_gates={verdict.challenger_passed_all_gates} "
                f"strict4={verdict.challenger_strictly_better_on_all_four} "
                f"tol4={verdict.challenger_within_tolerance_on_all_four}"
            ),
        )
    )

    # Gate 5 — anti-gaming.
    ag = inputs.anti_gaming
    turnover_lo, turnover_hi = TURNOVER_BAND_MONTHLY
    gates.append(
        GateResult(
            name="anti_gaming_exposure",
            passed=ag.avg_exposure_ratio >= MIN_AVG_EXPOSURE_RATIO,
            detail=(
                f"avg exposure {ag.avg_exposure_ratio:.3f} >= "
                f"{MIN_AVG_EXPOSURE_RATIO}"
            ),
        )
    )
    gates.append(
        GateResult(
            name="anti_gaming_signal_count",
            passed=ag.signal_count >= window.min_samples,
            detail=f"{ag.signal_count} signals >= {window.min_samples}",
        )
    )
    gates.append(
        GateResult(
            name="anti_gaming_turnover_band",
            passed=turnover_lo <= ag.monthly_turnover <= turnover_hi,
            detail=(
                f"monthly turnover {ag.monthly_turnover:.2f} in "
                f"[{turnover_lo}, {turnover_hi}]"
            ),
        )
    )

    # Gate 6 — rqalpha differential oracle (R-002). DIVERGENT = hard
    # reject; UNAVAILABLE passes but the decision records the run as
    # un-cross-checked (the oracle is a defence-in-depth layer, not the
    # primary correctness authority — that stays with the harsh fill
    # model + the locked acceptance gates).
    oracle_cross_checked = (
        inputs.oracle_verdict is OracleVerdict.CONSISTENT
    )
    gates.append(
        GateResult(
            name="oracle_not_divergent",
            passed=inputs.oracle_verdict
            is not OracleVerdict.DIVERGENT,
            detail=f"oracle verdict {inputs.oracle_verdict.value}",
        )
    )

    promoted = all(g.passed for g in gates)
    decision = PromotionDecision(
        kind=inputs.kind,
        family=inputs.family,
        artifact_hash=inputs.artifact_hash,
        experiment_id=inputs.experiment_id,
        promoted=promoted,
        gates=tuple(gates),
        oracle_cross_checked=oracle_cross_checked,
        inputs_digest=_inputs_digest(inputs),
        evaluated_at=inputs.evaluated_at,
    )
    log.info(
        "promotion_evaluated",
        family=inputs.family,
        promoted=promoted,
        failed=list(decision.failed_gates),
        oracle_cross_checked=oracle_cross_checked,
    )
    return decision


__all__ = [
    "MIN_AVG_EXPOSURE_RATIO",
    "TIERED_WINDOW_GATES",
    "TURNOVER_BAND_MONTHLY",
    "AntiGamingStats",
    "GateResult",
    "PromotionDecision",
    "PromotionInputs",
    "WindowGate",
    "evaluate_promotion",
]
