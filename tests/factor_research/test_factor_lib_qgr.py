"""Tests for the QGR-3 short-term factor computations ⑦ (fast leg).

Reversal (1d/3d) + forced lottery-removal overlay (short-window MAX, abnormal
turnover spike, limit-censored MAX count). Pure functions — no store / network.
Cover the value math, the fail-closed (``None``) paths, the limit-up censored
count, and the QGR registry wiring (mechanisms map to EXISTING EconomicMechanism
values — no governance enum change).
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.factor_lib import (
    ALL_FACTORS_BY_NAME,
    QGR_FACTOR_NAMES,
    QGR_FACTORS,
    QGR_FACTORS_BY_NAME,
    compute_qgr_factors,
    limit_up_count,
    trailing_return,
    turnover_spike,
)


class TestReversalShortHorizons:
    def test_rev_1d_is_one_day_return(self) -> None:
        closes = [10.0, 11.0]
        assert trailing_return(closes, 1) == pytest.approx(0.10)

    def test_rev_3d_is_three_day_return(self) -> None:
        closes = [10.0, 9.0, 8.0, 12.0]
        assert trailing_return(closes, 3) == pytest.approx(0.20)

    def test_too_short_history_returns_none(self) -> None:
        assert trailing_return([10.0], 1) is None
        assert trailing_return([10.0, 11.0, 12.0], 3) is None


class TestTurnoverSpike:
    def test_recent_surge_is_positive(self) -> None:
        # prior 20-day baseline = 1.0; last 5 days all 3.0 → spike = 3/1 - 1 = 2.0.
        rates = [1.0] * 20 + [3.0] * 5
        assert turnover_spike(rates) == pytest.approx(2.0)

    def test_quiet_recent_is_negative(self) -> None:
        # prior baseline 3.0; recent 1.0 → 1/3 - 1 = -0.667.
        rates = [3.0] * 20 + [1.0] * 5
        assert turnover_spike(rates) == pytest.approx(-2.0 / 3.0)

    def test_insufficient_history_returns_none(self) -> None:
        # Needs short + base = 25 observations.
        assert turnover_spike([1.0] * 24) is None

    def test_nonfinite_or_negative_fails_closed(self) -> None:
        assert turnover_spike([1.0] * 24 + [math.nan]) is None
        assert turnover_spike([1.0] * 24 + [-1.0]) is None

    def test_zero_base_fails_closed(self) -> None:
        assert turnover_spike([0.0] * 25) is None


class TestLimitUpCount:
    def test_counts_limit_up_closes(self) -> None:
        # Day at/above its up-limit counts; a normal close does not.
        raw = [10.0, 11.0, 10.5, 12.1, 13.0]
        up = [11.0, 11.0, 11.55, 12.1, 14.3]
        # day0 10<11 no; day1 11>=11 yes; day2 10.5<11.55 no; day3 12.1>=12.1 yes;
        # day4 13<14.3 no → 2.
        assert limit_up_count(raw, up, window=5) == pytest.approx(2.0)

    def test_window_respected(self) -> None:
        raw = [50.0, 11.0, 11.0]
        up = [11.0, 11.0, 11.0]
        # window=2 → only last two days (both at limit) → 2.
        assert limit_up_count(raw, up, window=2) == pytest.approx(2.0)

    def test_missing_limit_in_window_fails_closed(self) -> None:
        raw = [10.0, 11.0, 12.0, 13.0, 14.0]
        up = [11.0, 11.0, math.nan, 13.0, 14.0]
        assert limit_up_count(raw, up, window=5) is None

    def test_short_history_returns_none(self) -> None:
        assert limit_up_count([10.0], [11.0], window=5) is None

    def test_length_mismatch_fails_closed(self) -> None:
        # Misaligned series would compare different days → fail closed.
        raw = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        up = [11.0, 11.0, 11.0, 11.0, 11.0]
        assert limit_up_count(raw, up, window=5) is None


class TestComputeQgrFactors:
    def test_full_vector(self) -> None:
        closes = [10.0 + 0.1 * i for i in range(25)]  # gentle uptrend
        turnover = [2.0] * 25
        raw = [10.0] * 25
        up = [11.0] * 25
        vec = compute_qgr_factors(
            closes=closes,
            turnover_rates=turnover,
            raw_closes=raw,
            up_limits=up,
        )
        assert set(vec) == set(QGR_FACTOR_NAMES)
        assert vec["rev_1d"] is not None
        assert vec["rev_3d"] is not None
        assert vec["max_5d"] is not None
        assert vec["turn_spike"] == pytest.approx(0.0)  # flat turnover → no spike
        assert vec["n_limit_up_5d"] == pytest.approx(0.0)  # never at limit

    def test_insufficient_history_yields_none_fields(self) -> None:
        vec = compute_qgr_factors(
            closes=[10.0, 10.1],
            turnover_rates=[2.0, 2.0],
            raw_closes=[10.0, 10.0],
            up_limits=[11.0, 11.0],
        )
        assert vec["rev_1d"] is not None  # 1d needs only 2 closes
        assert vec["rev_3d"] is None
        assert vec["turn_spike"] is None
        assert vec["max_5d"] is None


class TestRegistryWiring:
    def test_all_qgr_factors_attractive_low(self) -> None:
        # Reversal + lottery-removal overlay: every factor is "high = avoid".
        for fdef in QGR_FACTORS:
            assert fdef.attractive_high is False
            assert fdef.expected_ic_sign == -1

    def test_mechanisms_are_registered_enum_values(self) -> None:
        from backend.strategy_evolution.mechanism_registry import EconomicMechanism

        registered = {m.value for m in EconomicMechanism}
        for fdef in QGR_FACTORS:
            assert fdef.mechanism in registered

    def test_names_unique_and_in_all_registry(self) -> None:
        assert len(QGR_FACTOR_NAMES) == len(set(QGR_FACTOR_NAMES))
        for name in QGR_FACTOR_NAMES:
            assert name in ALL_FACTORS_BY_NAME
            assert QGR_FACTORS_BY_NAME[name].name == name

    def test_disjoint_from_round1_names(self) -> None:
        from scripts.factor_research.factor_lib import FACTOR_NAMES

        assert set(QGR_FACTOR_NAMES).isdisjoint(FACTOR_NAMES)
