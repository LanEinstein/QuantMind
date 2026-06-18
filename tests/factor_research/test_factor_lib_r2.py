"""Tests for the round-2 trend / quality / growth factors (R2-2 / S3).

The round-1 seven-factor set (``FACTORS`` / ``ResearchFactorVector`` /
``compute_factor_vector``) is left untouched; these cover the NEW round-2
registry + factor functions only.
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.factor_lib import (
    FACTOR_NAMES,
    R2_FACTOR_NAMES,
    R2_FACTORS_BY_NAME,
    compute_fundamental_factors,
    compute_trend_factors,
    distance_from_high,
    momentum_skip,
    trend_slope,
)
from scripts.factor_research.fundamentals_pit import FundamentalRecord


def test_round1_registry_unchanged() -> None:
    # Regression guard: round-2 factors must NOT leak into the round-1 set.
    assert FACTOR_NAMES == (
        "ret_5d",
        "ret_20d",
        "vol_20d",
        "max_20d",
        "ep_ttm",
        "turn_20d",
        "amihud_20d",
    )
    assert set(R2_FACTOR_NAMES).isdisjoint(set(FACTOR_NAMES))


def test_r2_registry_shape() -> None:
    assert R2_FACTOR_NAMES == (
        "mom_12_1",
        "dist_high",
        "trend_slope",
        "roe",
        "gpm",
        "np_yoy",
        "rev_yoy",
    )
    # growth is honestly labelled "growth_premium" — intentionally NOT yet a
    # registered EconomicMechanism (promotion gate stays fail-closed until a
    # future amendment adds it).
    assert R2_FACTORS_BY_NAME["np_yoy"].mechanism == "growth_premium"
    assert R2_FACTORS_BY_NAME["roe"].mechanism == "quality_premium"
    assert R2_FACTORS_BY_NAME["mom_12_1"].mechanism == "momentum_continuation"
    assert R2_FACTORS_BY_NAME["roe"].attractive_high is True


def test_momentum_skip_skips_recent_month() -> None:
    # Build a series where the last 21 bars crash but t-252..t-21 rose.
    rising = [100.0 * (1.0 + 0.001 * i) for i in range(260)]  # 260 bars up-trend
    crashed = rising[:-21] + [rising[-22] * 0.5] * 21  # last 21 bars halved
    # 12-1 momentum measures t-252→t-21, so the recent crash is excluded.
    mom = momentum_skip(crashed, lookback=252, skip=21)
    assert mom is not None and mom > 0
    # explicit value: closes[-22]/closes[-253] - 1
    assert mom == pytest.approx(crashed[-22] / crashed[-253] - 1.0)


def test_momentum_skip_insufficient_history() -> None:
    assert momentum_skip([1.0] * 100, lookback=252, skip=21) is None


def test_momentum_skip_fail_closed_on_nonfinite() -> None:
    series = [100.0] * 260
    series[-253] = math.nan  # the base bar is corrupt
    assert momentum_skip(series, lookback=252, skip=21) is None


def test_distance_from_high() -> None:
    closes = [10.0, 12.0, 11.0, 9.0]  # high=12, last=9
    d = distance_from_high(closes, window=4)
    assert d == pytest.approx(9.0 / 12.0 - 1.0)
    assert d < 0
    # at a fresh high → 0
    assert distance_from_high([10.0, 11.0, 12.0], window=3) == pytest.approx(0.0)


def test_distance_from_high_insufficient_or_nonfinite() -> None:
    assert distance_from_high([10.0, 11.0], window=5) is None
    assert distance_from_high([math.inf, 11.0, 12.0], window=3) is None


def test_trend_slope_sign() -> None:
    up = [math.exp(0.01 * i) for i in range(60)]  # +1%/day log-trend
    s = trend_slope(up, window=60)
    assert s is not None and s == pytest.approx(0.01, abs=1e-9)
    down = [math.exp(-0.02 * i) for i in range(60)]
    sd = trend_slope(down, window=60)
    assert sd is not None and sd < 0


def test_trend_slope_fail_closed() -> None:
    assert trend_slope([1.0] * 10, window=60) is None  # too short
    assert trend_slope([1.0, -1.0, 2.0], window=3) is None  # non-positive price


def test_compute_trend_factors_dict() -> None:
    closes = [100.0 + i for i in range(300)]
    out = compute_trend_factors(closes)
    assert set(out) == {"mom_12_1", "dist_high", "trend_slope"}
    assert all(v is not None for v in out.values())


def _record(**vals: float | None) -> FundamentalRecord:
    fields = ("roe", "grossprofit_margin", "netprofit_yoy", "or_yoy")
    return FundamentalRecord(
        ts_code="600519.SH",
        end_date="20240331",
        ann_date="20240430",
        update_flag="1",
        vals=tuple(vals.get(f) for f in fields),
    )


def test_compute_fundamental_factors_maps_fields() -> None:
    rec = _record(roe=20.0, grossprofit_margin=90.0, netprofit_yoy=12.0, or_yoy=8.0)
    out = compute_fundamental_factors(rec)
    assert out == {
        "roe": 20.0,
        "gpm": 90.0,
        "np_yoy": 12.0,
        "rev_yoy": 8.0,
    }


def test_compute_fundamental_factors_none_record_all_none() -> None:
    out = compute_fundamental_factors(None)
    assert out == {"roe": None, "gpm": None, "np_yoy": None, "rev_yoy": None}


def test_compute_fundamental_factors_nonfinite_to_none() -> None:
    rec = _record(roe=math.inf, grossprofit_margin=90.0, netprofit_yoy=None, or_yoy=8.0)
    out = compute_fundamental_factors(rec)
    assert out["roe"] is None  # inf → fail closed
    assert out["np_yoy"] is None
    assert out["gpm"] == 90.0
