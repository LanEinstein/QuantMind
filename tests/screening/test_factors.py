"""Tests for backend.screening.factors (Alpha158 subset, pure functions)."""

from __future__ import annotations

import pytest

from backend.screening.factors import (
    FactorVector,
    avg_amount,
    compute_factors,
    ma_ratio,
    momentum,
    moving_average,
    rsi,
    volatility,
)


class TestMomentum:
    @pytest.mark.unit
    def test_trailing_20d_return(self) -> None:
        closes = [10.0] * 1 + [float(i) for i in range(1, 22)]  # len 22
        # close[-1]=21, close[-21]=1 → 21/1 - 1 = 20.0
        assert momentum(closes, window=20) == pytest.approx(20.0)

    @pytest.mark.unit
    def test_none_when_too_short(self) -> None:
        assert momentum([1.0] * 20, window=20) is None  # needs > window

    @pytest.mark.unit
    def test_none_on_nonpositive_base(self) -> None:
        closes = [0.0] + [1.0] * 20  # len 21 → base is closes[-21] == 0.0
        assert momentum(closes, window=20) is None


class TestMovingAverageAndRatio:
    @pytest.mark.unit
    def test_moving_average(self) -> None:
        assert moving_average([1.0, 2.0, 3.0, 4.0], 2) == pytest.approx(3.5)

    @pytest.mark.unit
    def test_moving_average_too_short(self) -> None:
        assert moving_average([1.0], 2) is None

    @pytest.mark.unit
    def test_ma_ratio_uptrend_gt_one(self) -> None:
        closes = [float(i) for i in range(1, 21)]  # strictly rising
        r = ma_ratio(closes, short=5, long=20)
        assert r is not None and r > 1.0

    @pytest.mark.unit
    def test_ma_ratio_none_when_short(self) -> None:
        assert ma_ratio([1.0, 2.0], short=5, long=20) is None


class TestVolatility:
    @pytest.mark.unit
    def test_zero_for_flat_series(self) -> None:
        assert volatility([5.0] * 25, window=20) == pytest.approx(0.0)

    @pytest.mark.unit
    def test_none_when_short(self) -> None:
        assert volatility([1.0] * 10, window=20) is None

    @pytest.mark.unit
    def test_positive_for_varying_series(self) -> None:
        closes = [10.0 + (1.0 if i % 2 else -1.0) for i in range(25)]
        v = volatility(closes, window=20)
        assert v is not None and v > 0.0


class TestRSI:
    @pytest.mark.unit
    def test_all_gains_is_100(self) -> None:
        closes = [float(i) for i in range(1, 30)]  # strictly rising
        assert rsi(closes, window=14) == pytest.approx(100.0)

    @pytest.mark.unit
    def test_none_when_short(self) -> None:
        assert rsi([1.0] * 10, window=14) is None

    @pytest.mark.unit
    def test_midrange_for_mixed(self) -> None:
        closes = [10.0 + (0.5 if i % 2 else -0.5) for i in range(30)]
        r = rsi(closes, window=14)
        assert r is not None and 0.0 < r < 100.0


class TestAvgAmount:
    @pytest.mark.unit
    def test_mean_of_trailing_window(self) -> None:
        amounts = [1.0] * 5 + [3.0] * 20  # last 20 are all 3.0
        assert avg_amount(amounts, window=20) == pytest.approx(3.0)

    @pytest.mark.unit
    def test_none_when_fewer_than_window(self) -> None:
        assert avg_amount([1.0] * 19, window=20) is None


class TestComputeFactors:
    @pytest.mark.unit
    def test_full_vector_on_sufficient_history(self) -> None:
        closes = [float(i) for i in range(1, 30)]
        amounts = [3e8] * 29
        fv = compute_factors(closes, amounts)
        assert isinstance(fv, FactorVector)
        assert fv.momentum_20d is not None
        assert fv.ma_ratio_5_20 is not None
        assert fv.volatility_20d is not None
        assert fv.rsi_14 is not None
        assert fv.avg_amount_20d == pytest.approx(3e8)

    @pytest.mark.unit
    def test_reproducible(self) -> None:
        """Identical inputs always yield an identical vector (PIT replay)."""
        closes = [10.0 + i * 0.3 for i in range(40)]
        amounts = [2.5e8 + i for i in range(40)]
        assert compute_factors(closes, amounts) == compute_factors(closes, amounts)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        fv = compute_factors([float(i) for i in range(1, 30)], [3e8] * 29)
        with pytest.raises(FrozenInstanceError):
            fv.momentum_20d = 0.0  # type: ignore[misc]
