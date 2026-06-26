"""Tests for the batch-A crowding / blow-off EXIT factor family (main-force-intent).

Pins the deterministic value + fail-closed behaviour of ``price_bias`` /
``ideal_amplitude`` / ``blowoff`` and the orientation metadata (all attractive-LOW,
expected_ic_sign −1) — the sign itself is verified from zero in the diagnostic, not
asserted here.
"""

from __future__ import annotations

import math

from scripts.factor_research.factor_lib import (
    CROWDING_FACTOR_NAMES,
    CROWDING_FACTORS,
    blowoff,
    compute_crowding_factors,
    ideal_amplitude,
    price_bias,
)


# --------------------------------------------------------------------------- bias
def test_price_bias_positive_when_above_mean() -> None:
    closes = [10.0] * 19 + [12.0]  # last well above the 20d mean
    val = price_bias(closes)
    assert val is not None and val > 0.0


def test_price_bias_zero_on_flat_series() -> None:
    assert price_bias([10.0] * 20) == 0.0


def test_price_bias_none_when_short_or_nonpositive() -> None:
    assert price_bias([10.0] * 19) is None  # < window
    assert price_bias([0.0] * 20) is None  # non-positive mean
    assert price_bias([float("nan")] + [10.0] * 19) is None


# ---------------------------------------------------------------- ideal amplitude
def test_ideal_amplitude_positive_when_highstate_thrashes_more() -> None:
    # Low-state days (closes 10) calm; high-state days (closes 20) wide amplitude.
    n = 20
    adj = [10.0] * (n // 2) + [20.0] * (n // 2)
    pre = [10.0] * n
    # amplitude = (high-low)/pre_close: low-state ~0.1, high-state ~1.0
    highs = [10.5] * (n // 2) + [21.0] * (n // 2)
    lows = [9.5] * (n // 2) + [11.0] * (n // 2)
    val = ideal_amplitude(adj, highs, lows, pre)
    assert val is not None and val > 0.0


def test_ideal_amplitude_none_on_degenerate_split() -> None:
    n = 20
    adj = [10.0] * n  # all equal → median split leaves low-state empty
    highs = [10.5] * n
    lows = [9.5] * n
    pre = [10.0] * n
    assert ideal_amplitude(adj, highs, lows, pre) is None


def test_ideal_amplitude_fail_closed_inputs() -> None:
    n = 20
    adj = list(range(1, n + 1))
    highs = [float(x) + 0.5 for x in adj]
    lows = [float(x) - 0.5 for x in adj]
    pre = [float(x) for x in adj]
    # high < low on one day → None
    bad_high = highs[:]
    bad_high[5] = lows[5] - 1.0
    assert ideal_amplitude(adj, bad_high, lows, pre) is None
    # non-positive pre_close → None
    bad_pre = pre[:]
    bad_pre[3] = 0.0
    assert ideal_amplitude(adj, highs, lows, bad_pre) is None
    # too short → None
    assert ideal_amplitude(adj[:10], highs[:10], lows[:10], pre[:10]) is None


# ------------------------------------------------------------------------ blowoff
def test_blowoff_positive_on_runup_and_turnover_surge() -> None:
    closes = [10.0 + 0.5 * i for i in range(21)]  # steady run-up over 20d
    # turnover: low baseline (prior 15) then a recent surge (last 5)
    turnover = [1.0] * 15 + [5.0] * 5
    val = blowoff(closes, turnover)
    assert val is not None and val > 0.0


def test_blowoff_zero_when_no_runup() -> None:
    closes = [20.0 - 0.5 * i for i in range(21)]  # falling → ret_20d < 0 → clipped
    turnover = [1.0] * 15 + [5.0] * 5
    assert blowoff(closes, turnover) == 0.0


def test_blowoff_none_when_short() -> None:
    assert blowoff([10.0] * 5, [1.0] * 5) is None


# ----------------------------------------------------------------- compute vector
def test_compute_crowding_factors_keys_and_fail_closed() -> None:
    out = compute_crowding_factors(
        adj_closes=[10.0] * 3,
        highs=[10.5] * 3,
        lows=[9.5] * 3,
        pre_closes=[10.0] * 3,
        turnover_rates=[1.0] * 3,
    )
    assert set(out) == {"bias_20d", "ideal_amplitude_20d", "blowoff_20d"}
    assert all(v is None for v in out.values())  # 3 bars << window → all None


def test_crowding_registry_is_all_attractive_low_exit() -> None:
    assert CROWDING_FACTOR_NAMES == ("bias_20d", "ideal_amplitude_20d", "blowoff_20d")
    for f in CROWDING_FACTORS:
        assert f.attractive_high is False
        assert f.expected_ic_sign == -1


def test_compute_crowding_factors_defined_on_full_history() -> None:
    n = 30
    adj = [10.0 + 0.1 * i for i in range(n)]
    highs = [c + 0.3 for c in adj]
    lows = [c - 0.3 for c in adj]
    pre = [adj[0]] + adj[:-1]
    turnover = [1.0] * (n - 5) + [3.0] * 5
    out = compute_crowding_factors(
        adj_closes=adj, highs=highs, lows=lows, pre_closes=pre, turnover_rates=turnover
    )
    assert all(v is not None and math.isfinite(v) for v in out.values())
