"""ShadowChain — 45-day challenger validation pipeline (P2-2 §1.5 + X-007).

The 22:00 mon-fri ``evolution_shadow_run`` cron (X-005) invokes this
chain to decide whether a freshly-proposed prompt / RAG / risk-proposal
/ exemplar challenger has earned the right to be promoted into
production. The pipeline reuses the P0-6 acceptance window mechanics
verbatim:

* 45 rolling trading days of acceptance metrics for the *challenger*
  run, computed by the same :class:`AcceptanceService` so the metric
  semantics, thresholds, and reset clamps stay byte-identical to
  production.
* The :class:`ShadowAcceptanceReport` extends
  :class:`AcceptanceReport` with three forensic fields locked by
  P2-2 §1.7 Q8: ``bootstrap_pnl_ci_95pct``,
  ``challenger_artifact_id``, ``champion_baseline_id``.
* The challenger-vs-champion verdict is the union of four conditions:
  - All 8 challenger gates pass (P0-6 hard gates — the floor).
  - All 8 champion gates pass (no degenerate comparison).
  - 4 strict-better deltas (P2-2 §1.5 wording: "4 严格优于").
  - 4 no-regression deltas at the 0.5pct tolerance band
    ("4 不差于 0.5pct").

The replay engine that turns a candidate prompt into a 45-day
challenger ``AcceptanceReport`` is the wiring layer landed by X-008
(EvolutionDispatcher). This module ships:

* :class:`ShadowAcceptanceReport` — schema.
* :func:`compute_bootstrap_pnl_ci_95pct` — scipy-based CI helper.
* :func:`evaluate_challenger` — the verdict function tests and the
  X-008 dispatcher both call.
* :class:`ShadowChain` — orchestration façade that combines a
  ``ChallengerReplayer`` Protocol with the verdict logic; the
  replayer interface is intentionally narrow so unit tests can
  inject canned reports without booting the prompt + LLM stack.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports (P2-2 §2 red line 17 + X-018 gate).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field

from backend.services.acceptance_report import (
    AcceptanceMetric,
    AcceptanceOutcome,
    AcceptanceReport,
    WindowResetState,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locked metric partitions — challenger verdict rule (P2-2 §1.5)
# ---------------------------------------------------------------------------

STRICT_BETTER_METRICS: frozenset[str] = frozenset(
    {
        "pnl_cny",
        "csi300_excess_pct",
        "max_drawdown_pct",
        "execution_report_accuracy_rate",
    }
)
"""Four metrics where the challenger MUST be strictly better than the
champion. Mix of strategy outcomes (pnl, csi300 excess, drawdown) and
the single accuracy gate whose precision matters for downstream
audit. Direction-aware comparison: ``max_drawdown_pct`` is
``at_most``, so "strictly better" means challenger value < champion
value; the rest are ``at_least``, so challenger > champion."""

NO_REGRESSION_METRICS: frozenset[str] = frozenset(
    {
        "instruction_completion_rate",
        "data_missing_rate",
        "llm_timeout_rate",
        "signal_generation_rate",
    }
)
"""Four operational stability metrics where the challenger only has
to stay within 0.5 percentage points of the champion (the
:data:`NO_REGRESSION_TOLERANCE_PCT` band). Same direction semantics
as the gates: ``at_least`` metrics may drop at most 0.5pp; ``at_most``
metrics may rise at most 0.5pp."""

NO_REGRESSION_TOLERANCE_PCT = 0.005
"""0.5 percentage points expressed as a ratio. The P2-2 §1.5 wording
"4 不差于 0.5pct" is in percentage points, so the tolerance equals
0.005 when the metric is itself a 0..1 ratio (which all five
stability metrics are)."""

BOOTSTRAP_RESAMPLES = 1000
"""Bootstrap iterations for the daily-PnL confidence interval. Locked
at 1000 by P2-2 §1.7 Q8; scipy.stats.bootstrap with method='percentile'
+ confidence_level=0.95 hits the same numerical envelope across
runs given the same seed."""

BOOTSTRAP_CONFIDENCE_LEVEL = 0.95
"""Two-sided percentile confidence interval coverage."""

ALL_GATE_NAMES: frozenset[str] = STRICT_BETTER_METRICS | NO_REGRESSION_METRICS
"""8 gate names — the union of strict-better + no-regression. The
intersection is empty (asserted at module import); a metric cannot
play in both buckets."""

assert STRICT_BETTER_METRICS.isdisjoint(NO_REGRESSION_METRICS)
assert len(STRICT_BETTER_METRICS) == 4
assert len(NO_REGRESSION_METRICS) == 4
assert len(ALL_GATE_NAMES) == 8


# ---------------------------------------------------------------------------
# ShadowAcceptanceReport — AcceptanceReport + 3 forensic fields
# ---------------------------------------------------------------------------


class ShadowAcceptanceReport(AcceptanceReport):
    """One row of the ``shadow_acceptance_reports`` collection (P2-2 §1.7).

    Inherits the full P0-6 AcceptanceReport surface (9 base fields + a
    nested tuple of 8 :class:`AcceptanceMetric` rows) so production
    consumers can treat a shadow report exactly the same as a
    production one when reading the metric values. Three extra fields
    document the challenger-vs-champion provenance:

    * ``bootstrap_pnl_ci_95pct`` — (low, high) 95% percentile interval
      around the daily-PnL mean, computed by
      :func:`compute_bootstrap_pnl_ci_95pct`.
    * ``challenger_artifact_id`` — e.g. ``PROMPT-fundamental_analyst-v3``
      or ``RISK-PROPOSAL-12345`` or ``EXEMPLAR-SCHEMA-v2`` — the
      thing under test.
    * ``champion_baseline_id`` — the production artifact the challenger
      is compared against.

    The collection name is intentionally separate from the production
    ``acceptance_reports`` (P0-6 §1.1 lock); the X-008 wiring writes
    ShadowAcceptanceReport instances only to ``shadow_acceptance_reports``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    bootstrap_pnl_ci_95pct: tuple[float, float]
    challenger_artifact_id: str = Field(min_length=1, max_length=160)
    champion_baseline_id: str = Field(min_length=1, max_length=160)


# ---------------------------------------------------------------------------
# Bootstrap CI helper
# ---------------------------------------------------------------------------


def compute_bootstrap_pnl_ci_95pct(
    daily_pnl_series: Sequence[float],
    *,
    rng_seed: int = 20260518,
) -> tuple[float, float]:
    """Return the 95% percentile CI of the mean daily PnL.

    Uses ``scipy.stats.bootstrap`` with ``method='percentile'`` +
    ``confidence_level=0.95`` + ``n_resamples=1000`` (P2-2 §1.7 Q8
    lock). ``rng_seed`` defaults to a fixed integer so two calls on
    the same input return identical intervals — important when the
    shadow chain is re-run for debugging.

    Degenerate inputs (empty series or all-equal series) return
    ``(value, value)`` for length-1 and ``(min, max)`` for an
    all-identical series; scipy itself raises ``ValueError`` on the
    empty case which we re-raise unchanged.
    """
    if len(daily_pnl_series) == 0:
        raise ValueError(
            "compute_bootstrap_pnl_ci_95pct received an empty series; "
            "the challenger window must produce at least one trading day "
            "of daily PnL before the CI can be computed"
        )
    if len(daily_pnl_series) == 1:
        only = float(daily_pnl_series[0])
        return (only, only)

    # Imported here so the rest of the module does not require scipy
    # for tests that exercise schema-only paths.
    import numpy as np
    from scipy import stats

    rng = np.random.default_rng(rng_seed)
    sample = np.asarray(list(daily_pnl_series), dtype=float)
    result = stats.bootstrap(
        (sample,),
        statistic=np.mean,
        n_resamples=BOOTSTRAP_RESAMPLES,
        confidence_level=BOOTSTRAP_CONFIDENCE_LEVEL,
        method="percentile",
        random_state=rng,
    )
    low = float(result.confidence_interval.low)
    high = float(result.confidence_interval.high)
    return (low, high)


# ---------------------------------------------------------------------------
# Challenger verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricComparison:
    """One champion-vs-challenger metric delta, direction-aware."""

    name: str
    rule: Literal["strict_better", "no_regression"]
    direction: Literal["at_least", "at_most"]
    champion_value: float
    challenger_value: float
    passed: bool
    delta: float
    """Signed delta. For ``at_least`` metrics, ``challenger - champion``;
    positive means challenger is better. For ``at_most`` metrics,
    ``champion - challenger``; positive means challenger is better."""


@dataclass(frozen=True)
class ChallengerVerdict:
    """Aggregate ruling from :func:`evaluate_challenger`."""

    champion_passed_all_gates: bool
    challenger_passed_all_gates: bool
    strict_better: tuple[MetricComparison, ...]
    no_regression: tuple[MetricComparison, ...]

    @property
    def challenger_strictly_better_on_all_four(self) -> bool:
        return all(c.passed for c in self.strict_better)

    @property
    def challenger_within_tolerance_on_all_four(self) -> bool:
        return all(c.passed for c in self.no_regression)

    @property
    def passed(self) -> bool:
        return (
            self.champion_passed_all_gates
            and self.challenger_passed_all_gates
            and self.challenger_strictly_better_on_all_four
            and self.challenger_within_tolerance_on_all_four
        )


def _metric_value_by_name(
    report: AcceptanceReport, name: str
) -> tuple[float, str]:
    """Look up a metric by name; return (value, direction)."""
    for metric in report.metrics:
        if metric.name == name:
            return float(metric.value), metric.direction
    raise KeyError(
        f"metric {name!r} not present in acceptance report; "
        f"present metrics: {[m.name for m in report.metrics]}"
    )


def _compare_metric(
    *,
    name: str,
    rule: Literal["strict_better", "no_regression"],
    champion: AcceptanceReport,
    challenger: AcceptanceReport,
) -> MetricComparison:
    champ_value, direction = _metric_value_by_name(champion, name)
    chal_value, chal_direction = _metric_value_by_name(challenger, name)
    if direction != chal_direction:
        raise ValueError(
            f"direction mismatch on {name!r}: champion={direction!r}, "
            f"challenger={chal_direction!r}"
        )
    if direction == "at_least":
        delta = chal_value - champ_value
    else:
        delta = champ_value - chal_value

    if rule == "strict_better":
        passed = delta > 0
    else:
        passed = delta >= -NO_REGRESSION_TOLERANCE_PCT

    return MetricComparison(
        name=name,
        rule=rule,
        direction=direction,  # type: ignore[arg-type]
        champion_value=champ_value,
        challenger_value=chal_value,
        passed=passed,
        delta=delta,
    )


def evaluate_challenger(
    *,
    champion: AcceptanceReport,
    challenger: AcceptanceReport,
) -> ChallengerVerdict:
    """Apply the P2-2 §1.5 challenger ruling.

    Steps:

    1. Both reports must declare ``outcome=PASS`` — a challenger that
       can't even satisfy the 8 hard gates by itself is never
       eligible, and a champion that has degraded out of PASS means
       the comparison baseline is broken (the upstream P0-6 chain
       should have alerted long before).
    2. Compute the 4 strict-better deltas; each must be > 0.
    3. Compute the 4 no-regression deltas; each must be >= -0.005.
    4. The verdict's ``passed`` property is true only when all four
       conditions above hold.

    The function does not consult ``bootstrap_pnl_ci_95pct`` — the CI
    is captured for forensic display in the Feishu notification, not
    as a gate. The gate-level comparison stays direction-aware and
    deterministic so a re-run cannot flip the verdict via numerical
    noise outside the explicit tolerance bands.
    """
    strict = tuple(
        _compare_metric(
            name=name,
            rule="strict_better",
            champion=champion,
            challenger=challenger,
        )
        for name in sorted(STRICT_BETTER_METRICS)
    )
    no_regress = tuple(
        _compare_metric(
            name=name,
            rule="no_regression",
            champion=champion,
            challenger=challenger,
        )
        for name in sorted(NO_REGRESSION_METRICS)
    )
    return ChallengerVerdict(
        champion_passed_all_gates=champion.outcome == AcceptanceOutcome.PASS,
        challenger_passed_all_gates=challenger.outcome == AcceptanceOutcome.PASS,
        strict_better=strict,
        no_regression=no_regress,
    )


# ---------------------------------------------------------------------------
# ShadowChain orchestration façade
# ---------------------------------------------------------------------------


class ChallengerReplayer(Protocol):
    """Replays the 45-day acceptance window under the candidate artifact.

    Implementations (X-008+) typically:

    1. Load the candidate prompt / RAG document / risk proposal /
       exemplar schema.
    2. Re-feed the 45-day decision trace through a stub LLM router or
       the canonical replay harness so the AcceptanceReport metrics
       are produced under the new artifact.
    3. Return the report along with a daily-PnL series for bootstrap.
    """

    def replay(
        self,
        *,
        as_of: dt.date,
        challenger_artifact_id: str,
    ) -> tuple[AcceptanceReport, Sequence[float]]: ...


@dataclass(frozen=True)
class ShadowChain:
    """High-level façade: replay + verdict + ShadowAcceptanceReport build.

    Tests can pass an in-memory replayer that returns canned reports.
    The X-008 dispatcher will wire a real replayer that re-runs the
    decision chain end-to-end.
    """

    replayer: ChallengerReplayer

    def run(
        self,
        *,
        as_of: dt.date,
        champion_baseline_id: str,
        champion_report: AcceptanceReport,
        challenger_artifact_id: str,
    ) -> tuple[ShadowAcceptanceReport, ChallengerVerdict]:
        """Run the shadow chain end-to-end.

        Returns the new ShadowAcceptanceReport (with the three
        forensic fields populated) and the verdict. Callers persist
        both to the ``shadow_acceptance_reports`` collection and let
        the X-013 amendment_drafter / X-014 evolution_feishu_notifier
        decide what to do next.
        """
        challenger_report, pnl_series = self.replayer.replay(
            as_of=as_of,
            challenger_artifact_id=challenger_artifact_id,
        )
        ci = compute_bootstrap_pnl_ci_95pct(pnl_series)
        verdict = evaluate_challenger(
            champion=champion_report, challenger=challenger_report
        )
        report = ShadowAcceptanceReport(
            report_id=uuid4(),
            computed_at=challenger_report.computed_at,
            trade_date=challenger_report.trade_date,
            window_start=challenger_report.window_start,
            window_end=challenger_report.window_end,
            trading_days_in_window=challenger_report.trading_days_in_window,
            outcome=challenger_report.outcome,
            metrics=challenger_report.metrics,
            notes=challenger_report.notes,
            reset_state=challenger_report.reset_state,
            bootstrap_pnl_ci_95pct=ci,
            challenger_artifact_id=challenger_artifact_id,
            champion_baseline_id=champion_baseline_id,
        )
        return report, verdict


# ---------------------------------------------------------------------------
# Convenience constructors for tests + X-008 wiring
# ---------------------------------------------------------------------------


def make_metric(
    name: str,
    value: float,
    *,
    threshold: float | None = None,
    direction: Literal["at_least", "at_most"] | None = None,
    passed: bool | None = None,
) -> AcceptanceMetric:
    """Build an :class:`AcceptanceMetric` with sensible defaults.

    Only here for test code and the X-008 wiring. Production builds
    the metric through :class:`AcceptanceService`, which derives the
    direction + threshold from the locked constants.
    """
    if direction is None:
        direction = "at_most" if name.endswith("_rate") and "missing" in name else (
            "at_most" if name == "max_drawdown_pct" or name == "llm_timeout_rate"
            else "at_least"
        )
    if threshold is None:
        threshold = 0.0
    if passed is None:
        if direction == "at_least":
            passed = math.isfinite(value) and value >= threshold
        else:
            passed = math.isfinite(value) and value <= threshold
    return AcceptanceMetric(
        name=name,
        value=value,
        threshold=threshold,
        direction=direction,
        passed=passed,
    )


def make_acceptance_report(
    *,
    metric_values: dict[str, float],
    outcome: AcceptanceOutcome = AcceptanceOutcome.PASS,
    trade_date: str = "2026-05-18",
    report_id: UUID | None = None,
    computed_at: dt.datetime | None = None,
) -> AcceptanceReport:
    """Convenience wrapper around the AcceptanceReport constructor.

    Tests use this to assemble a champion or challenger report from
    the 8 metric values without restating every gate threshold each
    time.
    """
    metrics = tuple(make_metric(name, value) for name, value in metric_values.items())
    return AcceptanceReport(
        report_id=report_id or uuid4(),
        computed_at=computed_at or dt.datetime(2026, 5, 18, 16, 0, tzinfo=dt.UTC),
        trade_date=trade_date,
        window_start="2026-03-15",
        window_end=trade_date,
        trading_days_in_window=45,
        outcome=outcome,
        metrics=metrics,
        notes="",
        reset_state=WindowResetState(),
    )


__all__ = [
    "ALL_GATE_NAMES",
    "BOOTSTRAP_CONFIDENCE_LEVEL",
    "BOOTSTRAP_RESAMPLES",
    "ChallengerReplayer",
    "ChallengerVerdict",
    "MetricComparison",
    "NO_REGRESSION_METRICS",
    "NO_REGRESSION_TOLERANCE_PCT",
    "STRICT_BETTER_METRICS",
    "ShadowAcceptanceReport",
    "ShadowChain",
    "compute_bootstrap_pnl_ci_95pct",
    "evaluate_challenger",
    "make_acceptance_report",
    "make_metric",
]
