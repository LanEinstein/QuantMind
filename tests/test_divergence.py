"""Pure-function tests for :mod:`backend.data.divergence` (C-004 / P0-8)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.data.divergence import DivergenceReport, evaluate_divergence


class TestEvaluateDivergence:
    """Locked semantics for the P0-8 divergence signal."""

    def test_identical_prices_not_divergent(self) -> None:
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        assert isinstance(report, DivergenceReport)
        assert report.relative_diff == 0.0
        assert report.is_divergent is False

    def test_within_threshold_not_divergent(self) -> None:
        # 0.2% < 0.3%
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1503.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff == pytest.approx(0.002)
        assert report.is_divergent is False

    def test_boundary_equal_threshold_not_divergent(self) -> None:
        """``diff == threshold`` is not divergent — only strictly greater."""
        # Exact 0.3%
        report = evaluate_divergence(
            code="600519", primary_price=1000.0, fallback_price=1003.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff == pytest.approx(0.003)
        assert report.is_divergent is False

    def test_above_threshold_is_divergent(self) -> None:
        # 0.4% > 0.3%
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1506.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff == pytest.approx(0.004)
        assert report.is_divergent is True

    def test_negative_diff_still_divergent_by_abs(self) -> None:
        """Backup below primary counts the same as backup above."""
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1494.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff == pytest.approx(0.004)
        assert report.is_divergent is True

    def test_fallback_missing_returns_none_and_not_divergent(self) -> None:
        """``fallback_price=None`` defers to staleness/availability gates."""
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=None,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False

    def test_zero_primary_returns_none_and_not_divergent(self) -> None:
        """Zero primary cannot anchor a relative comparison."""
        report = evaluate_divergence(
            code="600519", primary_price=0.0, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False

    def test_negative_primary_returns_none(self) -> None:
        """Negative primary (vendor bug) also short-circuits."""
        report = evaluate_divergence(
            code="600519", primary_price=-1.0, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False

    def test_threshold_propagated(self) -> None:
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1500.5,
            threshold_pct=0.005,
        )
        assert report.threshold_pct == 0.005

    def test_report_is_frozen(self) -> None:
        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        with pytest.raises(FrozenInstanceError):
            report.is_divergent = True  # type: ignore[misc]

    def test_nan_fallback_is_not_divergent_and_diff_none(self) -> None:
        """Regression: NaN fallback used to slip through as ``rel=NaN``
        with ``NaN > threshold = False``, silently returning clean."""
        import math as _m

        report = evaluate_divergence(
            code="600519", primary_price=1500.0, fallback_price=_m.nan,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False

    def test_nan_primary_is_not_divergent_and_diff_none(self) -> None:
        import math as _m

        report = evaluate_divergence(
            code="600519", primary_price=_m.nan, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False

    def test_inf_inputs_short_circuit(self) -> None:
        import math as _m

        report = evaluate_divergence(
            code="600519", primary_price=_m.inf, fallback_price=1500.0,
            threshold_pct=0.003,
        )
        assert report.relative_diff is None
        assert report.is_divergent is False
