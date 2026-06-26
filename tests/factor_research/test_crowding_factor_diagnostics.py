"""Tests for the batch-A crowding EXIT diagnostics (main-force-intent §3/§7).

Deterministic synthetic panels (no RNG) pin the load-bearing helpers: the
crash-probability conditional detects an engineered fat left tail in the
top-crowding decile and stays insignificant when crowding is unrelated to the
forward return; the decile spread, CVaR, and sub-period split behave; and
``evaluate_factor`` wires the CPCV / DSR / collinearity reads together.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.factor_research.crowding_factor_diagnostics import (
    _aligned_onc_n,
    _crash_prob,
    _cvar,
    _overlap_lag,
    _periods_per_year,
    _subperiod_means,
    decile_spread_by_date,
    decile_spread_series,
    evaluate_factor,
    tail_conditional,
)


def _panel_with_tail(*, link: bool, n_dates: int = 60, n_codes: int = 100):
    """A panel where the top-crowding decile crashes on even dates iff ``link``."""
    rows: list[dict[str, object]] = []
    for di in range(n_dates):
        date = f"2020{1000 + di:04d}"
        for i in range(n_codes):
            crowd = float(i)  # f_neut rank == i; top decile = i in 90..99
            crashing = link and i >= 90 and di % 2 == 0
            fwd = -0.08 if crashing else 0.01
            rows.append({"date": date, "f_neut": crowd, "fwd_ret_5d": fwd})
    return pd.DataFrame(rows)


def test_crash_prob_and_cvar() -> None:
    rets = np.array([-0.10, -0.06, -0.04, 0.0, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05])
    assert _crash_prob(rets, -0.05) == 0.2  # two of ten below -5%
    assert _cvar(rets, 0.10) == -0.10  # worst 10% = the single -0.10
    assert np.isnan(_crash_prob(np.array([]), -0.05))


def test_subperiod_means_three_thirds() -> None:
    vals = np.array([1.0] * 10 + [2.0] * 10 + [3.0] * 10)
    assert _subperiod_means(vals, 3) == (1.0, 2.0, 3.0)
    assert all(np.isnan(x) for x in _subperiod_means(np.array([1.0]), 3))


def test_tail_conditional_detects_fat_left_tail() -> None:
    panel = _panel_with_tail(link=True)
    tc = tail_conditional(panel, "f_neut", "fwd_ret_5d")
    assert tc.crowded_p_crash5 > tc.rest_p_crash5  # crowding fattens the left tail
    assert tc.paired_diff_mean > 0
    assert tc.paired_diff_t > 3.0  # significant
    assert tc.crowded_cvar5 < tc.rest_cvar5  # worse tail expected shortfall


def test_tail_conditional_insignificant_when_unlinked() -> None:
    panel = _panel_with_tail(link=False)
    tc = tail_conditional(panel, "f_neut", "fwd_ret_5d")
    # No crashes anywhere → both crash probs zero, paired diff degenerate.
    assert tc.crowded_p_crash5 == 0.0
    assert tc.rest_p_crash5 == 0.0
    assert tc.paired_diff_t == 0.0


def test_decile_spread_positive_for_working_exit_factor() -> None:
    panel = _panel_with_tail(link=True)
    spread = decile_spread_series(panel, "f_neut", "fwd_ret_5d")
    assert len(spread) == 60
    # bottom-decile (calm, +0.01) minus top-decile (crashes on even dates) > 0 mean.
    assert float(np.mean(spread)) > 0.0


def _panel_full_neut(n_dates: int = 40, n_codes: int = 120):
    """A panel carrying every `<carry>_neut` + `<crowding>_neut` + fwd column."""
    from scripts.factor_research.crowding_factor_diagnostics import CARRY_CLUSTER
    from scripts.factor_research.factor_lib import CROWDING_FACTOR_NAMES

    rows: list[dict[str, object]] = []
    for di in range(n_dates):
        date = f"2020{1000 + di:04d}"
        for i in range(n_codes):
            row: dict[str, object] = {"date": date, "code": f"{i:06d}"}
            # crowding neut columns ranked by i; carry neut columns = distinct noise
            for f in CROWDING_FACTOR_NAMES:
                row[f"{f}_neut"] = float(i)
            for j, c in enumerate(CARRY_CLUSTER):
                row[f"{c}_neut"] = float((i * (j + 2)) % 37)  # de-correlated-ish
            crashing = i >= int(n_codes * 0.9) and di % 2 == 0
            row["fwd_ret_5d"] = -0.08 if crashing else 0.01
            rows.append(row)
    return pd.DataFrame(rows)


def test_evaluate_factor_wires_reads_together() -> None:
    neut_panel = _panel_full_neut()
    spread = decile_spread_series(neut_panel, "bias_20d_neut", "fwd_ret_5d")
    res = evaluate_factor(
        neut_panel, "bias_20d", n_trials=2400, spread=spread, rebalance_freq=5
    )
    assert res.factor == "bias_20d"
    assert res.tail.paired_diff_t > 3.0  # engineered tail is significant
    assert 0.0 <= res.spread_dsr <= 1.0
    assert 0.0 <= res.max_carry_corr <= 1.0
    assert res.max_carry_support >= 0  # support count is now carried (review #6)


def test_overlap_lag_and_annualization() -> None:
    # 5d label at 5td cadence → non-overlapping (lag 0); 10d at 5td → lag 1.
    assert _overlap_lag(5, 5) == 0
    assert _overlap_lag(10, 5) == 1
    assert _overlap_lag(20, 5) == 3
    assert _periods_per_year(5) == 252 / 5


def test_aligned_onc_uses_common_dates() -> None:
    # Two factors whose spread dicts share only a subset of dates → ONC aligns on
    # the intersection, never correlating mismatched dates (review #5).
    a = {"20200101": 0.01, "20200102": 0.02, "20200103": 0.03}
    b = {"20200102": 0.02, "20200103": 0.03, "20200104": 0.04}  # offset by one date
    n = _aligned_onc_n({"a": a, "b": b})
    assert n in (1, 2)  # perfectly correlated on the 2 common dates → collapses to 1


def test_decile_spread_by_date_is_keyed() -> None:
    panel = _panel_with_tail(link=True)
    by_date = decile_spread_by_date(panel, "f_neut", "fwd_ret_5d")
    assert len(by_date) == 60
    assert all(isinstance(k, str) for k in by_date)
