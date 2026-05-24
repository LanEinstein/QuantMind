"""Tests for backend.budget_policy.calibration (tier-threshold derivation)."""

from __future__ import annotations

import pytest

from backend.budget_policy.calibration import (
    TierCalibration,
    calibrate_tiers,
)
from backend.budget_policy.policy import BudgetPolicyError

# 11 sorted lot costs so percentile ranks land on exact indices:
# p10 → index 1 (¥300), p50 → index 5 (¥1500).
SAMPLE = [200.0, 300.0, 400.0, 800.0, 1200.0, 1500.0, 2000.0, 3000.0, 4000.0,
          8000.0, 20000.0]


class TestCalibrateTiers:
    @pytest.mark.unit
    def test_reproduces_shipped_thresholds(self) -> None:
        # p10 ¥300 / 0.15 = ¥2,000 ; median ¥1,500 / 0.15 = ¥10,000 —
        # i.e. the shipped budget_tiers values are calibrated, not arbitrary.
        cal = calibrate_tiers(SAMPLE, 0.15)
        assert isinstance(cal, TierCalibration)
        assert cal.micro_max_cash_yuan == pytest.approx(2000.0)
        assert cal.small_max_cash_yuan == pytest.approx(10000.0)
        assert cal.micro_percentile_lot_cost == pytest.approx(300.0)
        assert cal.small_percentile_lot_cost == pytest.approx(1500.0)
        assert cal.sample_size == 11

    @pytest.mark.unit
    def test_micro_below_small(self) -> None:
        cal = calibrate_tiers(SAMPLE, 0.15)
        assert cal.micro_max_cash_yuan < cal.small_max_cash_yuan

    @pytest.mark.unit
    def test_drops_nonfinite_and_nonpositive(self) -> None:
        noisy = [*SAMPLE, float("nan"), float("inf"), 0.0, -100.0]
        cal = calibrate_tiers(noisy, 0.15)
        assert cal.sample_size == 11  # only the valid 11 survive

    @pytest.mark.unit
    def test_scales_with_pct(self) -> None:
        # Halving the pct doubles the cash thresholds (cost/pct).
        cal_15 = calibrate_tiers(SAMPLE, 0.15)
        cal_30 = calibrate_tiers(SAMPLE, 0.30)
        assert cal_30.micro_max_cash_yuan == pytest.approx(
            cal_15.micro_max_cash_yuan / 2
        )

    @pytest.mark.unit
    def test_empty_sample_raises(self) -> None:
        with pytest.raises(BudgetPolicyError, match="no valid"):
            calibrate_tiers([], 0.15)

    @pytest.mark.unit
    def test_all_invalid_raises(self) -> None:
        with pytest.raises(BudgetPolicyError, match="no valid"):
            calibrate_tiers([float("nan"), 0.0, -5.0], 0.15)

    @pytest.mark.unit
    def test_narrow_distribution_raises(self) -> None:
        # All identical lots → p10 == p50 → micro == small → cannot derive
        # distinct tiers.
        with pytest.raises(BudgetPolicyError, match="too narrow"):
            calibrate_tiers([500.0] * 20, 0.15)

    @pytest.mark.unit
    def test_single_value_raises(self) -> None:
        with pytest.raises(BudgetPolicyError, match="too narrow"):
            calibrate_tiers([1234.0], 0.15)

    @pytest.mark.unit
    def test_bad_pct_raises(self) -> None:
        with pytest.raises(BudgetPolicyError, match="max_single_stock_pct"):
            calibrate_tiers(SAMPLE, 1.5)

    @pytest.mark.unit
    def test_micro_pct_must_be_below_small_pct(self) -> None:
        with pytest.raises(BudgetPolicyError, match="micro_percentile must be <"):
            calibrate_tiers(SAMPLE, 0.15, micro_percentile=60.0, small_percentile=50.0)

    @pytest.mark.unit
    def test_calibration_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        cal = calibrate_tiers(SAMPLE, 0.15)
        with pytest.raises(FrozenInstanceError):
            cal.micro_max_cash_yuan = 1.0  # type: ignore[misc]
