"""AC-003 — surface-tier helpers + three-tier value composite (deterministic)."""

from __future__ import annotations

import math

import pytest

from backend.screening.value_factors import (
    beta,
    percentile_rank,
    pit_fundamentals_value,
    resonance_count,
    resonance_score,
)
from backend.screening.value_score import (
    ValueScoreInputs,
    ValueScoreWeights,
    compute_value_score,
)


class TestResonance:
    def test_distinct_families_counted(self) -> None:
        assert resonance_count(["f1", "f2", "f3"]) == 3

    def test_same_run_repeats_count_once(self) -> None:
        """Repeated references from the same LLM run are one echo (codex P1-4)."""
        assert resonance_count(["run-a", "run-a", "run-b"]) == 2

    def test_blank_ids_ignored(self) -> None:
        assert resonance_count(["", "  ", "f1"]) == 1

    def test_score_saturates_at_target(self) -> None:
        assert resonance_score(2, target=2) == 1.0
        assert resonance_score(5, target=2) == 1.0
        assert resonance_score(1, target=2) == 0.5
        assert resonance_score(0, target=2) == 0.0


class TestBeta:
    def test_beta_of_identical_series_is_one(self) -> None:
        r = tuple(0.01 * i for i in range(-30, 30))
        assert beta(r, r, window=60) == pytest.approx(1.0)

    def test_beta_of_double_amplitude_is_two(self) -> None:
        m = tuple(0.01 * i for i in range(-30, 30))
        s = tuple(2 * x for x in m)
        assert beta(s, m, window=60) == pytest.approx(2.0)

    def test_too_short_is_none(self) -> None:
        assert beta((0.1, 0.2), (0.1, 0.2), window=60) is None

    def test_zero_variance_market_is_none(self) -> None:
        s = tuple(0.01 for _ in range(60))
        m = tuple(0.0 for _ in range(60))
        assert beta(s, m, window=60) is None


class TestPitFundamentals:
    def test_latest_announced_on_or_before_as_of(self) -> None:
        records = [("2026-01-31", 1.0), ("2026-04-30", 2.0), ("2026-07-31", 3.0)]
        # Decision on 2026-06-01: the Q2 (announced 07-31) must NOT leak.
        assert pit_fundamentals_value(records, "2026-06-01") == 2.0

    def test_nothing_announced_yet_is_none(self) -> None:
        records = [("2026-07-31", 3.0)]
        assert pit_fundamentals_value(records, "2026-06-01") is None

    def test_same_day_announcement_is_excluded(self) -> None:
        # M2 (P0-8-amendment-2026-06-25): strict-exclusive cutoff — a report
        # announced ON the decision date must NOT leak in (it may post after the
        # 09:35 decision). Falls back to the prior announced vintage.
        records = [("2026-04-30", 2.0), ("2026-06-01", 9.0)]
        assert pit_fundamentals_value(records, "2026-06-01") == 2.0

    def test_same_day_only_is_none(self) -> None:
        records = [("2026-06-01", 9.0)]
        assert pit_fundamentals_value(records, "2026-06-01") is None

    def test_dirty_value_skipped(self) -> None:
        records = [("2026-01-31", math.nan), ("2026-02-28", 5.0)]
        assert pit_fundamentals_value(records, "2026-06-01") == 5.0


class TestPercentileRank:
    def test_basic_rank(self) -> None:
        assert percentile_rank(5.0, [1, 2, 3, 4, 5]) == pytest.approx(0.9)

    def test_inverted_for_lower_is_better(self) -> None:
        # Amihud: a low illiquidity should rank high when inverted.
        assert percentile_rank(
            1.0, [1, 2, 3, 4, 5], higher_is_better=False
        ) == pytest.approx(0.9)

    def test_empty_population_is_none(self) -> None:
        assert percentile_rank(1.0, []) is None

    def test_dirty_value_is_none(self) -> None:
        assert percentile_rank(math.inf, [1, 2, 3]) is None


class TestComputeValueScore:
    def test_all_max_is_one(self) -> None:
        inputs = ValueScoreInputs(
            theme_coverage=1.0,
            sector_momentum_pct=1.0,
            regime_score=1.0,
            abnormal_return_pct=1.0,
            capacity_pct=1.0,
            liquidity_pct=1.0,
            turnover_pct=1.0,
            capital_flow_pct=1.0,
            resonance_score=1.0,
            fundamentals_score=1.0,
            elasticity_score=1.0,
        )
        out = compute_value_score(inputs)
        assert out.value_score == pytest.approx(1.0)
        assert out.bottom == pytest.approx(1.0)
        assert len(out.components_present) == 11

    def test_no_components_is_zero_conservative(self) -> None:
        """No pinned theme / no data → score 0 (never clears the value gate)."""
        out = compute_value_score(ValueScoreInputs())
        assert out.value_score == 0.0
        assert out.components_present == ()

    def test_tier_is_mean_of_present_components(self) -> None:
        out = compute_value_score(
            ValueScoreInputs(theme_coverage=1.0, sector_momentum_pct=0.0)
        )
        # regime_score absent → bottom = mean(1.0, 0.0) = 0.5
        assert out.bottom == pytest.approx(0.5)

    def test_clamps_out_of_range_components(self) -> None:
        out = compute_value_score(
            ValueScoreInputs(theme_coverage=5.0, sector_momentum_pct=-3.0)
        )
        assert out.bottom == pytest.approx(0.5)  # clamp(5)->1, clamp(-3)->0

    def test_deterministic_replay(self) -> None:
        inputs = ValueScoreInputs(theme_coverage=0.7, resonance_score=0.5)
        assert compute_value_score(inputs) == compute_value_score(inputs)

    def test_weights_normalised(self) -> None:
        # All weight on the bottom tier; only the bottom component is present.
        out = compute_value_score(
            ValueScoreInputs(theme_coverage=1.0),
            ValueScoreWeights(bottom=2.0, mid=0.0, surface=0.0),
        )
        assert out.value_score == pytest.approx(1.0)

    def test_invalid_weights_rejected(self) -> None:
        with pytest.raises(ValueError):
            ValueScoreWeights(bottom=-1.0)
        with pytest.raises(ValueError):
            ValueScoreWeights(bottom=0.0, mid=0.0, surface=0.0)


class TestValuationFactorField:
    """AF-002: the surface-tier ``valuation_score`` is additive (None-default)."""

    def test_none_valuation_is_bit_identical(self) -> None:
        # A pre-AF-002 input (valuation_score defaults None) is dropped from the
        # surface mean → the composite is unchanged from the v1 behaviour.
        legacy = ValueScoreInputs(
            theme_coverage=0.8, fundamentals_score=0.6, resonance_score=0.4
        )
        out = compute_value_score(legacy)
        assert "valuation_score" not in out.components_present
        # surface = mean(resonance 0.4, fundamentals 0.6) = 0.5 (valuation dropped)
        assert out.surface == pytest.approx(0.5)

    def test_present_valuation_enters_surface_mean(self) -> None:
        out = compute_value_score(
            ValueScoreInputs(fundamentals_score=0.6, valuation_score=1.0)
        )
        # surface = mean(fundamentals 0.6, valuation 1.0) = 0.8
        assert out.surface == pytest.approx(0.8)
        assert "valuation_score" in out.components_present

    def test_full_twelve_components(self) -> None:
        inputs = ValueScoreInputs(
            theme_coverage=1.0,
            sector_momentum_pct=1.0,
            regime_score=1.0,
            abnormal_return_pct=1.0,
            capacity_pct=1.0,
            liquidity_pct=1.0,
            turnover_pct=1.0,
            capital_flow_pct=1.0,
            resonance_score=1.0,
            fundamentals_score=1.0,
            elasticity_score=1.0,
            valuation_score=1.0,
        )
        out = compute_value_score(inputs)
        assert out.value_score == pytest.approx(1.0)
        assert len(out.components_present) == 12
