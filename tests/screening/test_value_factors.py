"""AC-002 — mid-tier value factors (event-study / Amihud / capacity), PIT-safe."""

from __future__ import annotations

import math

import pytest

from backend.screening.value_factors import (
    MidTierInputs,
    amihud_illiquidity,
    compute_mid_tier,
    event_study_abnormal_return,
    free_float_capacity,
)


class TestEventStudy:
    def test_car_is_sum_of_abnormal_returns_in_window(self) -> None:
        # 7 bars; event at index 1; window 3 → bars 2,3,4 abnormal returns.
        stock = (0.0, 0.10, 0.05, 0.03, 0.02, 0.01, 0.00)
        market = (0.0, 0.05, 0.01, 0.01, 0.01, 0.00, 0.00)
        car = event_study_abnormal_return(stock, market, event_offset=1, window=3)
        # (0.05-0.01) + (0.03-0.01) + (0.02-0.01) = 0.07
        assert car == pytest.approx(0.07)

    def test_no_event_returns_none(self) -> None:
        assert event_study_abnormal_return((0.1, 0.2), (0.1, 0.1), None) is None

    def test_window_running_off_end_is_none_pit_guard(self) -> None:
        """The post-event window must be FULLY observed (no peeking ahead)."""
        stock = (0.0, 0.1, 0.05)  # only index 2 observed after event@1
        market = (0.0, 0.05, 0.01)
        # window=3 needs bars 2,3,4 but series ends at 2 → None (PIT).
        assert event_study_abnormal_return(stock, market, 1, window=3) is None

    def test_window_exactly_fits_is_observed(self) -> None:
        stock = (0.0, 0.1, 0.05, 0.04)
        market = (0.0, 0.05, 0.01, 0.01)
        # event@1, window=2 → bars 2,3 (last index 3 == n-1) is observed.
        car = event_study_abnormal_return(stock, market, 1, window=2)
        assert car == pytest.approx((0.05 - 0.01) + (0.04 - 0.01))

    def test_misaligned_series_is_none(self) -> None:
        assert event_study_abnormal_return((0.1, 0.2, 0.3), (0.1, 0.2), 0, 1) is None

    def test_dirty_return_is_none(self) -> None:
        stock = (0.0, 0.1, math.nan, 0.04)
        market = (0.0, 0.05, 0.01, 0.01)
        assert event_study_abnormal_return(stock, market, 1, window=2) is None

    def test_deterministic_replay(self) -> None:
        stock = (0.0, 0.1, 0.05, 0.04, 0.02)
        market = (0.0, 0.05, 0.01, 0.01, 0.00)
        a = event_study_abnormal_return(stock, market, 1, 3)
        b = event_study_abnormal_return(stock, market, 1, 3)
        assert a == b


class TestAmihud:
    def test_mean_of_abs_return_over_amount(self) -> None:
        returns = (0.02, -0.04)
        amounts = (2.0, 4.0)
        # mean(0.02/2, 0.04/4) = mean(0.01, 0.01) = 0.01
        assert amihud_illiquidity(returns, amounts) == pytest.approx(0.01)

    def test_skips_non_positive_amount(self) -> None:
        assert amihud_illiquidity((0.02, -0.04), (0.0, 4.0)) == pytest.approx(0.01)

    def test_all_zero_amounts_is_none(self) -> None:
        assert amihud_illiquidity((0.02, -0.04), (0.0, 0.0)) is None

    def test_empty_is_none(self) -> None:
        assert amihud_illiquidity((), ()) is None

    def test_misaligned_is_none(self) -> None:
        assert amihud_illiquidity((0.1,), (1.0, 2.0)) is None


class TestFreeFloatCapacity:
    def test_shares_times_close(self) -> None:
        assert free_float_capacity(1_000_000.0, 12.5) == pytest.approx(12_500_000.0)

    @pytest.mark.parametrize(
        "shares,close",
        [(None, 10.0), (1.0, None), (0.0, 10.0), (1.0, 0.0), (math.nan, 10.0)],
    )
    def test_dirty_input_is_none(self, shares, close) -> None:  # noqa: ANN001
        assert free_float_capacity(shares, close) is None


class TestComputeMidTier:
    def test_passthrough_and_determinism(self) -> None:
        inputs = MidTierInputs(
            stock_returns=(0.0, 0.1, 0.05, 0.04),
            market_returns=(0.0, 0.05, 0.01, 0.01),
            event_offset=1,
            event_window=2,
            amounts=(1.0, 2.0, 3.0, 4.0),
            free_float_shares=1_000_000.0,
            last_close=12.5,
            turnover_rate=0.03,
            northbound_holding_pct=0.08,
            main_capital_net=1_234.0,
        )
        a = compute_mid_tier(inputs)
        b = compute_mid_tier(inputs)
        assert a == b
        assert a.free_float_capacity == pytest.approx(12_500_000.0)
        assert a.turnover_rate == 0.03
        assert a.northbound_holding_pct == 0.08
        assert a.main_capital_net == 1_234.0

    def test_dirty_scalars_become_none(self) -> None:
        out = compute_mid_tier(
            MidTierInputs(turnover_rate=math.inf, northbound_holding_pct=math.nan)
        )
        assert out.turnover_rate is None
        assert out.northbound_holding_pct is None
