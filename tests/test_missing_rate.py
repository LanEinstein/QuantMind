"""Pure-function tests for ``backend.data.missing_rate`` (C-003 / P0-8)."""

from __future__ import annotations

import pytest

from backend.data.missing_rate import (
    compute_tick_missing_rate,
    compute_window_missing_rate,
)


class TestTickMissingRate:
    """Single-tick coverage view."""

    def test_full_coverage_returns_zero(self) -> None:
        expected = {"600519", "510300", "159949"}
        observed = {"600519", "510300", "159949"}
        assert compute_tick_missing_rate(expected, observed) == 0.0

    def test_all_missing_returns_one(self) -> None:
        expected = {"600519", "510300"}
        observed: set[str] = set()
        assert compute_tick_missing_rate(expected, observed) == 1.0

    def test_partial_missing(self) -> None:
        expected = {"600519", "510300", "159949", "300750"}
        observed = {"600519", "510300"}
        # 2 missing out of 4 = 0.5
        assert compute_tick_missing_rate(expected, observed) == 0.5

    def test_empty_expected_returns_zero(self) -> None:
        assert compute_tick_missing_rate(set(), {"600519"}) == 0.0

    def test_over_observed_does_not_decrease_rate(self) -> None:
        """Observed codes outside the universe are ignored."""
        expected = {"600519", "510300"}
        observed = {"600519", "510300", "stranger", "noise"}
        assert compute_tick_missing_rate(expected, observed) == 0.0

    def test_duplicate_observed_codes_treated_as_set(self) -> None:
        """An iterable with duplicates must not give different result than a set."""
        expected = ["600519", "510300", "159949"]
        observed_dupes = ["600519", "600519", "510300"]
        rate_dupes = compute_tick_missing_rate(expected, observed_dupes)
        rate_set = compute_tick_missing_rate(set(expected), {"600519", "510300"})
        assert rate_dupes == rate_set == pytest.approx(1.0 / 3.0)


class TestWindowMissingRate:
    """Multi-tick window view."""

    def test_full_window_returns_zero(self) -> None:
        # 13 codes × 60 ticks (30s × 30min = 60) = 780 expected
        assert compute_window_missing_rate(13, 60, 780) == 0.0

    def test_no_observations_returns_one(self) -> None:
        assert compute_window_missing_rate(13, 60, 0) == 1.0

    def test_half_window_returns_half(self) -> None:
        # 13 × 60 = 780 expected; 390 observed → 0.5 missing
        assert compute_window_missing_rate(13, 60, 390) == 0.5

    def test_over_observed_floors_at_zero(self) -> None:
        """Retry duplicates / clock drift must not give a negative rate."""
        assert compute_window_missing_rate(13, 60, 800) == 0.0

    def test_zero_expected_returns_zero(self) -> None:
        assert compute_window_missing_rate(0, 60, 0) == 0.0

    def test_zero_ticks_returns_zero(self) -> None:
        assert compute_window_missing_rate(13, 0, 0) == 0.0

    def test_negative_inputs_return_zero(self) -> None:
        assert compute_window_missing_rate(-1, 10, 0) == 0.0
        assert compute_window_missing_rate(10, -1, 0) == 0.0

    def test_p0_8_acceptance_threshold_one_pct(self) -> None:
        """≤1% missing is the P0-8 acceptance gate; verify the boundary."""
        # 13 × 60 = 780; 99% coverage = 772.2 → 772 rows = 1.0257% missing
        # 99.5% coverage = 776.1 → 776 rows = 0.51% missing
        assert compute_window_missing_rate(13, 60, 776) < 0.01
        assert compute_window_missing_rate(13, 60, 772) >= 0.01
