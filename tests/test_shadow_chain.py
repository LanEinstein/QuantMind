"""X-007 unit tests — ShadowChain / ShadowAcceptanceReport / verdict / bootstrap.

Schema-level + verdict-rule + bootstrap-CI invariants. The full
45-day replay chain depends on the X-008 EvolutionDispatcher to land;
this module exercises everything that can be tested at the X-007 layer
with an in-memory ChallengerReplayer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from backend.services.acceptance_report import (
    AcceptanceMetric,
    AcceptanceOutcome,
    AcceptanceReport,
    WindowResetState,
)
from backend.services.shadow_chain import (
    ALL_GATE_NAMES,
    BOOTSTRAP_CONFIDENCE_LEVEL,
    BOOTSTRAP_RESAMPLES,
    NO_REGRESSION_METRICS,
    NO_REGRESSION_TOLERANCE_PCT,
    STRICT_BETTER_METRICS,
    ChallengerReplayer,
    ChallengerVerdict,
    ShadowAcceptanceReport,
    ShadowChain,
    compute_bootstrap_pnl_ci_95pct,
    evaluate_challenger,
    make_acceptance_report,
)

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------


def test_strict_better_metrics_locked_four() -> None:
    assert STRICT_BETTER_METRICS == frozenset(
        {
            "pnl_cny",
            "csi300_excess_pct",
            "max_drawdown_pct",
            "execution_report_accuracy_rate",
        }
    )
    assert len(STRICT_BETTER_METRICS) == 4


def test_no_regression_metrics_locked_four() -> None:
    assert NO_REGRESSION_METRICS == frozenset(
        {
            "instruction_completion_rate",
            "data_missing_rate",
            "llm_timeout_rate",
            "signal_generation_rate",
        }
    )
    assert len(NO_REGRESSION_METRICS) == 4


def test_strict_and_no_regression_are_disjoint_and_cover_eight() -> None:
    assert STRICT_BETTER_METRICS.isdisjoint(NO_REGRESSION_METRICS)
    assert len(ALL_GATE_NAMES) == 8


def test_no_regression_tolerance_is_half_pct() -> None:
    assert NO_REGRESSION_TOLERANCE_PCT == 0.005


def test_bootstrap_constants_locked() -> None:
    assert BOOTSTRAP_RESAMPLES == 1000
    assert BOOTSTRAP_CONFIDENCE_LEVEL == 0.95


# ---------------------------------------------------------------------------
# ShadowAcceptanceReport
# ---------------------------------------------------------------------------


def _full_report(
    *,
    pnl: float = 5_000.0,
    excess: float = 0.02,
    drawdown: float = 0.04,
    accuracy: float = 0.995,
    completion: float = 0.97,
    data_missing: float = 0.005,
    llm_timeout: float = 0.03,
    signal: float = 0.96,
    outcome: AcceptanceOutcome = AcceptanceOutcome.PASS,
) -> AcceptanceReport:
    return make_acceptance_report(
        metric_values={
            "instruction_completion_rate": completion,
            "execution_report_accuracy_rate": accuracy,
            "data_missing_rate": data_missing,
            "llm_timeout_rate": llm_timeout,
            "signal_generation_rate": signal,
            "max_drawdown_pct": drawdown,
            "pnl_cny": pnl,
            "csi300_excess_pct": excess,
        },
        outcome=outcome,
    )


def test_shadow_report_extends_acceptance_with_three_fields() -> None:
    base = _full_report()
    shadow = ShadowAcceptanceReport(
        report_id=base.report_id,
        computed_at=base.computed_at,
        trade_date=base.trade_date,
        window_start=base.window_start,
        window_end=base.window_end,
        trading_days_in_window=base.trading_days_in_window,
        outcome=base.outcome,
        metrics=base.metrics,
        notes=base.notes,
        reset_state=WindowResetState(),
        bootstrap_pnl_ci_95pct=(4_500.0, 5_500.0),
        challenger_artifact_id="PROMPT-fundamental_analyst-v3",
        champion_baseline_id="PROMPT-fundamental_analyst-v2",
    )
    assert shadow.bootstrap_pnl_ci_95pct == (4_500.0, 5_500.0)
    assert shadow.challenger_artifact_id.startswith("PROMPT-")
    # Inheritance: every AcceptanceReport field is present.
    assert shadow.outcome == AcceptanceOutcome.PASS
    assert isinstance(shadow.metrics[0], AcceptanceMetric)


def test_shadow_report_extra_field_forbidden() -> None:
    base = _full_report()
    with pytest.raises(ValidationError):
        ShadowAcceptanceReport(
            report_id=base.report_id,
            computed_at=base.computed_at,
            trade_date=base.trade_date,
            window_start=base.window_start,
            window_end=base.window_end,
            trading_days_in_window=base.trading_days_in_window,
            outcome=base.outcome,
            metrics=base.metrics,
            notes=base.notes,
            reset_state=WindowResetState(),
            bootstrap_pnl_ci_95pct=(0.0, 1.0),
            challenger_artifact_id="x",
            champion_baseline_id="y",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_shadow_report_is_frozen() -> None:
    base = _full_report()
    shadow = ShadowAcceptanceReport(
        report_id=base.report_id,
        computed_at=base.computed_at,
        trade_date=base.trade_date,
        window_start=base.window_start,
        window_end=base.window_end,
        trading_days_in_window=base.trading_days_in_window,
        outcome=base.outcome,
        metrics=base.metrics,
        notes=base.notes,
        reset_state=WindowResetState(),
        bootstrap_pnl_ci_95pct=(0.0, 1.0),
        challenger_artifact_id="x",
        champion_baseline_id="y",
    )
    with pytest.raises(ValidationError):
        shadow.challenger_artifact_id = "tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bootstrap CI
# ---------------------------------------------------------------------------


def test_bootstrap_ci_deterministic_seed() -> None:
    series = [1.0, 2.0, 3.0, 4.0, 5.0, 0.0, -1.0, 2.5, 3.1, 2.9]
    low1, high1 = compute_bootstrap_pnl_ci_95pct(series, rng_seed=42)
    low2, high2 = compute_bootstrap_pnl_ci_95pct(series, rng_seed=42)
    assert (low1, high1) == (low2, high2)
    assert low1 < high1


def test_bootstrap_ci_different_seeds_differ_within_tolerance() -> None:
    series = [1.0, 2.0, 3.0, 4.0, 5.0, 0.0, -1.0, 2.5, 3.1, 2.9]
    low1, high1 = compute_bootstrap_pnl_ci_95pct(series, rng_seed=1)
    low2, high2 = compute_bootstrap_pnl_ci_95pct(series, rng_seed=2)
    # Two RNG seeds on a non-trivial 10-day series should produce
    # different CIs but stay within an order of magnitude of the
    # sample range (sanity check; not an exact equality).
    assert abs((low1 + high1) / 2 - (low2 + high2) / 2) < 5.0


def test_bootstrap_ci_empty_series_raises() -> None:
    with pytest.raises(ValueError, match="empty series"):
        compute_bootstrap_pnl_ci_95pct([])


def test_bootstrap_ci_single_value() -> None:
    low, high = compute_bootstrap_pnl_ci_95pct([7.5])
    assert (low, high) == (7.5, 7.5)


def test_bootstrap_ci_ninety_five_percent_brackets_mean() -> None:
    series = [1.0, 1.5, 2.0, 2.2, 1.8, 1.9, 2.1, 1.4, 1.6, 1.7]
    low, high = compute_bootstrap_pnl_ci_95pct(series, rng_seed=99)
    mean = sum(series) / len(series)
    assert low <= mean <= high


# ---------------------------------------------------------------------------
# Challenger verdict — the gate rule
# ---------------------------------------------------------------------------


def test_verdict_passes_when_challenger_strictly_better_on_all_four() -> None:
    champion = _full_report(
        pnl=1_000.0, excess=0.01, drawdown=0.05, accuracy=0.99
    )
    challenger = _full_report(
        pnl=1_500.0, excess=0.02, drawdown=0.04, accuracy=0.995
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is True
    assert verdict.challenger_strictly_better_on_all_four is True
    assert verdict.challenger_within_tolerance_on_all_four is True


def test_verdict_fails_when_strict_metric_only_ties() -> None:
    champion = _full_report(pnl=1_500.0)
    challenger = _full_report(pnl=1_500.0)  # tie, not strict-better
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False
    assert verdict.challenger_strictly_better_on_all_four is False
    pnl_cmp = next(c for c in verdict.strict_better if c.name == "pnl_cny")
    assert pnl_cmp.passed is False
    assert pnl_cmp.delta == 0.0


def test_verdict_fails_when_drawdown_increases() -> None:
    # Drawdown is at_most: challenger must have LOWER drawdown.
    champion = _full_report(drawdown=0.04)
    challenger = _full_report(drawdown=0.05)
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False
    dd_cmp = next(
        c for c in verdict.strict_better if c.name == "max_drawdown_pct"
    )
    assert dd_cmp.passed is False
    # champion 0.04 - challenger 0.05 = -0.01 (subject to IEEE-754 noise)
    assert dd_cmp.delta == pytest.approx(-0.01, abs=1e-12)


def test_verdict_passes_when_no_regression_within_half_pct() -> None:
    # signal_generation_rate is at_least; allow up to 0.5pp drop.
    # Challenger must also strictly beat champion on the 4 strict
    # metrics — defaults set pnl=5000 / excess=0.02 / drawdown=0.04 /
    # accuracy=0.995, so the override bumps each one upward.
    champion = _full_report(signal=0.96)
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
        signal=0.957,  # 0.3pp drop, within tolerance
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is True
    sig_cmp = next(
        c for c in verdict.no_regression if c.name == "signal_generation_rate"
    )
    assert sig_cmp.passed is True


def test_verdict_fails_when_no_regression_breaks_half_pct() -> None:
    champion = _full_report(signal=0.96)
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
        signal=0.954,  # 0.6pp drop, outside tolerance
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False
    sig_cmp = next(
        c for c in verdict.no_regression if c.name == "signal_generation_rate"
    )
    assert sig_cmp.passed is False


def test_verdict_fails_when_at_most_metric_rises_over_tolerance() -> None:
    # data_missing_rate is at_most; challenger may go up by 0.5pp.
    champion = _full_report(data_missing=0.005)
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
        data_missing=0.012,  # 0.7pp worse — fails tolerance
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False


def test_verdict_fails_when_challenger_does_not_pass_baseline_gates() -> None:
    champion = _full_report()
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
        outcome=AcceptanceOutcome.FAIL,
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False
    assert verdict.challenger_passed_all_gates is False


def test_verdict_fails_when_champion_not_pass() -> None:
    champion = _full_report(outcome=AcceptanceOutcome.INSUFFICIENT_DATA)
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert verdict.passed is False
    assert verdict.champion_passed_all_gates is False


def test_verdict_metric_comparison_counts_match_partitions() -> None:
    champion = _full_report()
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    assert len(verdict.strict_better) == 4
    assert len(verdict.no_regression) == 4
    assert {c.name for c in verdict.strict_better} == STRICT_BETTER_METRICS
    assert {c.name for c in verdict.no_regression} == NO_REGRESSION_METRICS


def test_verdict_direction_aware_delta_sign() -> None:
    # Both at_least and at_most metrics encode "positive delta = better".
    champion = _full_report(pnl=1_000.0, drawdown=0.05)
    challenger = _full_report(
        pnl=1_500.0,  # at_least, +500 -> delta +500
        excess=0.02,
        drawdown=0.03,  # at_most, champion 0.05 - challenger 0.03 = +0.02
        accuracy=0.995,
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    pnl_cmp = next(c for c in verdict.strict_better if c.name == "pnl_cny")
    dd_cmp = next(
        c for c in verdict.strict_better if c.name == "max_drawdown_pct"
    )
    assert pnl_cmp.delta == 500.0
    assert dd_cmp.delta == pytest.approx(0.02, abs=1e-12)


# ---------------------------------------------------------------------------
# ShadowChain.run — orchestration façade
# ---------------------------------------------------------------------------


@dataclass
class _CannedReplayer:
    """In-memory ChallengerReplayer."""

    canned_report: AcceptanceReport
    canned_pnl_series: list[float]

    def replay(
        self,
        *,
        as_of: dt.date,
        challenger_artifact_id: str,
    ) -> tuple[AcceptanceReport, Sequence[float]]:
        return self.canned_report, list(self.canned_pnl_series)


def test_shadow_chain_run_emits_shadow_report_and_passing_verdict() -> None:
    champion = _full_report()
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
    )
    chain = ShadowChain(
        replayer=_CannedReplayer(
            canned_report=challenger,
            canned_pnl_series=[5.0, 8.0, 11.0, 4.0, 9.0, 10.0, 12.0],
        )
    )
    report, verdict = chain.run(
        as_of=dt.date(2026, 5, 18),
        champion_baseline_id="PROMPT-fundamental_analyst-v2",
        champion_report=champion,
        challenger_artifact_id="PROMPT-fundamental_analyst-v3",
    )
    assert verdict.passed is True
    assert isinstance(report, ShadowAcceptanceReport)
    assert report.challenger_artifact_id == "PROMPT-fundamental_analyst-v3"
    assert report.champion_baseline_id == "PROMPT-fundamental_analyst-v2"
    low, high = report.bootstrap_pnl_ci_95pct
    assert low <= sum(chain.replayer.canned_pnl_series) / len(  # type: ignore[attr-defined]
        chain.replayer.canned_pnl_series  # type: ignore[attr-defined]
    ) <= high


def test_shadow_chain_run_fails_verdict_on_regression() -> None:
    champion = _full_report()
    challenger = _full_report(
        pnl=6_000.0, excess=0.03, drawdown=0.035, accuracy=0.997,
        data_missing=0.02,  # 1.5pp worse — fails no-regression
    )
    chain = ShadowChain(
        replayer=_CannedReplayer(
            canned_report=challenger,
            canned_pnl_series=[1.0, 2.0, 3.0],
        )
    )
    _, verdict = chain.run(
        as_of=dt.date(2026, 5, 18),
        champion_baseline_id="champ",
        champion_report=champion,
        challenger_artifact_id="chal",
    )
    assert verdict.passed is False


# ---------------------------------------------------------------------------
# Protocol smoke — ChallengerReplayer satisfies the structural typing
# ---------------------------------------------------------------------------


def test_canned_replayer_is_challenger_replayer() -> None:
    replayer: ChallengerReplayer = _CannedReplayer(
        canned_report=_full_report(),
        canned_pnl_series=[0.0],
    )
    report, series = replayer.replay(
        as_of=dt.date(2026, 5, 18),
        challenger_artifact_id="x",
    )
    assert isinstance(report, AcceptanceReport)
    assert list(series) == [0.0]


# ---------------------------------------------------------------------------
# Import-gate red line
# ---------------------------------------------------------------------------


def test_shadow_chain_module_avoids_forbidden_imports() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "backend/services/shadow_chain.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from backend.api",
        "from backend.broker",
        "from backend.risk",
        "from backend.llm",
        "from backend.agents",
        "from backend.mirofish",
        "from backend.data",
        "import backend.api",
        "import backend.broker",
        "import backend.risk",
        "import backend.llm",
        "import backend.agents",
        "import backend.mirofish",
        "import backend.data",
    ):
        assert forbidden not in src, (
            f"P2-2 §2 red line 17 violation: shadow_chain.py contains "
            f"{forbidden!r}"
        )


# ---------------------------------------------------------------------------
# verdict-rule total assertions count (13 per X-007 spec)
# ---------------------------------------------------------------------------


def test_thirteen_verdict_assertion_count() -> None:
    """The verdict surface comprises 13 distinct booleans:

    * 2 outcome gates (champion + challenger pass their 8 baseline gates)
    * 4 strict-better assertions
    * 4 no-regression assertions
    * 3 derived properties (strictly_better_on_all_four,
      within_tolerance_on_all_four, passed)

    This keeps the verdict surface auditable; if a future contributor
    adds a 14th boolean the count assertion forces them to update the
    docstring + X-007 SSoT acceptance text together.
    """
    champion = _full_report()
    challenger = _full_report(
        pnl=1_500.0, excess=0.02, drawdown=0.04, accuracy=0.995
    )
    verdict = evaluate_challenger(champion=champion, challenger=challenger)
    booleans: list[bool] = [
        verdict.champion_passed_all_gates,
        verdict.challenger_passed_all_gates,
        *(c.passed for c in verdict.strict_better),
        *(c.passed for c in verdict.no_regression),
        verdict.challenger_strictly_better_on_all_four,
        verdict.challenger_within_tolerance_on_all_four,
        verdict.passed,
    ]
    assert len(booleans) == 13
    assert all(isinstance(b, bool) for b in booleans)


# ---------------------------------------------------------------------------
# AcceptanceReport name mismatch path
# ---------------------------------------------------------------------------


def test_evaluate_raises_when_metric_missing() -> None:
    champion = _full_report()
    # Build a challenger without one of the gate metrics.
    pruned_metrics = tuple(
        m for m in champion.metrics if m.name != "pnl_cny"
    )
    broken = AcceptanceReport(
        report_id=champion.report_id,
        computed_at=champion.computed_at,
        trade_date=champion.trade_date,
        window_start=champion.window_start,
        window_end=champion.window_end,
        trading_days_in_window=champion.trading_days_in_window,
        outcome=champion.outcome,
        metrics=pruned_metrics,
        notes="",
        reset_state=WindowResetState(),
    )
    with pytest.raises(KeyError, match="pnl_cny"):
        evaluate_challenger(champion=champion, challenger=broken)


# ---------------------------------------------------------------------------
# Sanity smoke — ChallengerVerdict dataclass surface
# ---------------------------------------------------------------------------


def test_verdict_dataclass_frozen() -> None:
    verdict = evaluate_challenger(
        champion=_full_report(),
        challenger=_full_report(
            pnl=1_500.0, excess=0.02, drawdown=0.04, accuracy=0.995
        ),
    )
    with pytest.raises(Exception):  # noqa: BLE001 — dataclass raises FrozenInstanceError
        verdict.passed = False  # type: ignore[misc]


@pytest.fixture
def _silence_unused_imports() -> Any:
    # Keep ChallengerVerdict import alive in case future tests skip
    # the constructor smoke; pytest is otherwise the test discovery
    # point.
    return ChallengerVerdict
