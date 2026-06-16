"""Tests for the offline A-share factor library (Phase 3).

Pins exact values for the pure factor maths, the fail-closed ``None`` on
insufficient history, and the registry/vector consistency that the panel
builder and weight search depend on.
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.factor_lib import (
    FACTOR_NAMES,
    FACTORS,
    FACTORS_BY_NAME,
    ResearchFactorVector,
    amihud_illiquidity,
    compute_factor_vector,
    earnings_yield,
    max_daily_return,
    mean_turnover,
    return_volatility,
    trailing_return,
)

# -- trailing_return ----------------------------------------------------


def test_trailing_return_basic() -> None:
    assert trailing_return([10.0, 11.0, 12.0], window=2) == pytest.approx(0.2)
    # close[-1]/close[-1-1] - 1 = 12/11 - 1
    assert trailing_return([10.0, 11.0, 12.0], window=1) == pytest.approx(12 / 11 - 1)


def test_trailing_return_insufficient_history_is_none() -> None:
    # len(closes) <= window -> None (need a base bar strictly before window)
    assert trailing_return([10.0, 11.0], window=2) is None
    assert trailing_return([], window=1) is None


def test_trailing_return_nonpositive_base_is_none() -> None:
    assert trailing_return([0.0, 11.0, 12.0], window=2) is None
    assert trailing_return([-1.0, 11.0, 12.0], window=2) is None


# -- return_volatility --------------------------------------------------


def test_return_volatility_known() -> None:
    # closes [10,10,20] -> returns [0.0, 1.0] -> pstdev = 0.5
    assert return_volatility([10.0, 10.0, 20.0], window=2) == pytest.approx(0.5)


def test_return_volatility_insufficient_is_none() -> None:
    assert return_volatility([10.0, 11.0], window=20) is None
    assert return_volatility([10.0, 11.0, 12.0], window=1) is None  # window<=1


# -- max_daily_return ---------------------------------------------------


def test_max_daily_return_known() -> None:
    # closes [10,12,11] -> returns [0.2, -0.0833...] -> max over 2 = 0.2
    assert max_daily_return([10.0, 12.0, 11.0], window=2) == pytest.approx(0.2)


def test_max_daily_return_insufficient_is_none() -> None:
    assert max_daily_return([10.0, 11.0], window=20) is None


# -- amihud_illiquidity -------------------------------------------------


def test_amihud_alignment_and_value() -> None:
    # closes [10,20,10], amounts [5,10,20], window 2
    # returns [1.0, -0.5]; |r| paired with the LATER day's amount:
    #   1.0 -> amounts[1]=10 ; 0.5 -> amounts[2]=20
    # mean(1.0/10, 0.5/20) = mean(0.1, 0.025) = 0.0625 ; *1e9 = 6.25e7
    assert amihud_illiquidity(
        [10.0, 20.0, 10.0], [5.0, 10.0, 20.0], window=2
    ) == pytest.approx(62_500_000.0)


def test_amihud_skips_nonpositive_amount_fail_closed() -> None:
    # one of the two paired days has amount 0 -> usable < window -> None
    assert amihud_illiquidity([10.0, 20.0, 10.0], [5.0, 0.0, 20.0], window=2) is None


def test_amihud_insufficient_is_none() -> None:
    assert amihud_illiquidity([10.0, 11.0], [1.0, 1.0], window=20) is None


# -- earnings_yield -----------------------------------------------------


def test_earnings_yield() -> None:
    assert earnings_yield(20.0) == pytest.approx(0.05)
    assert earnings_yield(None) is None
    assert earnings_yield(0.0) is None  # zero/undefined P/E
    assert earnings_yield(-5.0) is None  # loss-making -> fail-closed drop


# -- mean_turnover ------------------------------------------------------


def test_mean_turnover() -> None:
    assert mean_turnover([1.0, 2.0, 3.0], window=2) == pytest.approx(2.5)
    assert mean_turnover([1.0, 2.0, 3.0], window=3) == pytest.approx(2.0)


def test_mean_turnover_insufficient_or_negative_is_none() -> None:
    assert mean_turnover([1.0], window=2) is None
    assert mean_turnover([1.0, -1.0, 2.0], window=2) is None  # malformed negative


# -- non-finite (NaN/inf) fail-closed guards ----------------------------
# Tushare daily_basic hands NaN pe_ttm for loss-makers and NaN turnover on
# halts; the library takes already-parsed floats so it must guard finiteness
# itself or it would crash (pstdev) or emit NaN/inf factors that silently
# poison the cross-sectional rank.


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_trailing_return_nonfinite_is_none(bad: float) -> None:
    assert trailing_return([10.0, 11.0, bad], window=2) is None
    assert trailing_return([bad, 11.0, 12.0], window=2) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_return_volatility_nonfinite_is_none_not_crash(bad: float) -> None:
    # NaN previously raised AttributeError inside statistics.pstdev.
    assert return_volatility([10.0, bad, 12.0], window=2) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_max_daily_return_nonfinite_is_none(bad: float) -> None:
    assert max_daily_return([10.0, bad, 12.0], window=2) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_amihud_nonfinite_close_or_amount_is_none(bad: float) -> None:
    assert amihud_illiquidity([10.0, bad, 10.0], [5.0, 10.0, 20.0], window=2) is None
    assert amihud_illiquidity([10.0, 20.0, 10.0], [5.0, bad, 20.0], window=2) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_earnings_yield_nonfinite_is_none(bad: float) -> None:
    # inf previously yielded a fabricated 0.0; nan slipped past `<= 0`.
    assert earnings_yield(bad) is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_mean_turnover_nonfinite_is_none(bad: float) -> None:
    assert mean_turnover([1.0, bad, 2.0], window=2) is None


def test_compute_factor_vector_nonfinite_inputs_no_crash() -> None:
    # A NaN latest close + NaN pe_ttm must not crash; every price factor that
    # uses the last bar -> None (trailing_return is endpoint-based, so a NaN
    # at closes[-1] poisons it too). The clean turnover series stays defined.
    closes = [10.0 + i * 0.1 for i in range(40)]
    closes[-1] = math.nan
    amounts = [1000.0 + i for i in range(40)]
    turnover = [1.5] * 40
    vec = compute_factor_vector(
        closes=closes, amounts=amounts, turnover_rates=turnover, pe_ttm=math.nan
    )
    assert vec.ep_ttm is None
    assert vec.ret_5d is None
    assert vec.ret_20d is None
    assert vec.vol_20d is None
    assert vec.max_20d is None
    assert vec.amihud_20d is None
    assert vec.turn_20d == pytest.approx(1.5)  # clean turnover -> defined
    # every produced value, where not None, must be finite
    assert all(v is None or math.isfinite(v) for v in vec.as_dict().values())


# -- registry / vector consistency --------------------------------------


def test_registry_matches_vector_fields() -> None:
    vector_fields = tuple(ResearchFactorVector.__dataclass_fields__)
    assert FACTOR_NAMES == vector_fields
    assert tuple(FACTORS_BY_NAME) == FACTOR_NAMES
    assert all(FACTORS_BY_NAME[f.name] is f for f in FACTORS)


def test_registry_directions_match_survey() -> None:
    # The Phase-1 survey priors: value attractive-high, everything else
    # (reversal / vol / max / turnover / illiquidity) attractive-low.
    by_name = FACTORS_BY_NAME
    assert by_name["ep_ttm"].attractive_high is True
    assert by_name["ep_ttm"].expected_ic_sign == 1
    for low in ("ret_5d", "ret_20d", "vol_20d", "max_20d", "turn_20d", "amihud_20d"):
        assert by_name[low].attractive_high is False
        assert by_name[low].expected_ic_sign == -1


def test_compute_factor_vector_full() -> None:
    closes = [10.0 + i * 0.1 for i in range(40)]  # 40 ascending bars
    amounts = [1000.0 + i for i in range(40)]
    turnover = [1.5 for _ in range(40)]
    vec = compute_factor_vector(
        closes=closes, amounts=amounts, turnover_rates=turnover, pe_ttm=25.0
    )
    d = vec.as_dict()
    assert set(d) == set(FACTOR_NAMES)
    assert all(v is not None for v in d.values())  # 40 bars -> all defined
    assert vec.ep_ttm == pytest.approx(0.04)
    assert vec.turn_20d == pytest.approx(1.5)


def test_compute_factor_vector_insufficient_history_partial_none() -> None:
    # 6 bars: ret_5d defined (needs 6), ret_20d/vol/max undefined; ep from PE.
    closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
    amounts = [100.0] * 6
    turnover = [2.0] * 6
    vec = compute_factor_vector(
        closes=closes, amounts=amounts, turnover_rates=turnover, pe_ttm=None
    )
    assert vec.ret_5d is not None
    assert vec.ret_20d is None
    assert vec.vol_20d is None
    assert vec.max_20d is None
    assert vec.amihud_20d is None
    assert vec.ep_ttm is None  # pe None
    assert vec.turn_20d is None  # only 6 < 20 turnover obs
