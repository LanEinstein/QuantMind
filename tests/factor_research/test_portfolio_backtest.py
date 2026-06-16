"""Tests for the portfolio-sort net-of-cost backtest."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.factor_lib import FACTOR_NAMES
from scripts.factor_research.portfolio_backtest import (
    _benchmark_leg,
    backtest,
    equal_weights,
    group_by_date,
    oriented_rank,
)


def _panel(n_dates: int = 4, n_codes: int = 25) -> pd.DataFrame:
    # ep_ttm (attractive-high) predicts fwd: high i -> high ep_ttm -> high return.
    rows = []
    for di in range(n_dates):
        for i in range(n_codes):
            row = {"date": f"202001{di:02d}", "code": f"6000{i:02d}"}
            for f in FACTOR_NAMES:
                row[f] = float(i)
            row["ep_ttm"] = float(i)
            row["fwd_ret_5d"] = i * 0.001
            row["fwd_ret_10d"] = i * 0.001
            row["fwd_ret_20d"] = i * 0.001
            rows.append(row)
    return pd.DataFrame(rows)


def test_oriented_rank_inverts_attractive_low() -> None:
    g = _panel(n_dates=1)
    g = g[g["date"] == "20200100"]
    # ret_5d is attractive-LOW: the highest raw value must get the LOWEST rank.
    score_low = oriented_rank(g, {"ret_5d": 1.0})
    top_code = g.loc[score_low.idxmax(), "code"]
    assert top_code == "600000"  # smallest raw ret_5d -> highest oriented rank
    # ep_ttm is attractive-HIGH: highest raw -> highest rank.
    score_high = oriented_rank(g, {"ep_ttm": 1.0})
    assert g.loc[score_high.idxmax(), "code"] == "600024"


def test_predictive_value_factor_makes_money_net_of_cost() -> None:
    panel = _panel()
    res = backtest(panel, {"ep_ttm": 1.0}, horizon=5, top_n=5)
    # top-5 ep_ttm = codes 20..24, fwd ~0.020..0.024 -> positive net of cost
    assert res.total_return > 0
    assert res.win_rate == pytest.approx(1.0)
    assert res.n_periods == 4


def test_benchmark_excess_computed() -> None:
    panel = _panel()
    # flat benchmark (no move) -> excess == portfolio total return
    bench = {f"202001{di:02d}": 1000.0 for di in range(4)}
    res = backtest(panel, {"ep_ttm": 1.0}, benchmark=bench, horizon=5, top_n=5)
    assert res.bench_total_return == pytest.approx(0.0)
    assert res.excess_vs_bench == pytest.approx(res.total_return)


def test_cost_drags_return() -> None:
    panel = _panel()
    free = backtest(panel, {"ep_ttm": 1.0}, horizon=5, top_n=5, cost=0.0)
    costed = backtest(panel, {"ep_ttm": 1.0}, horizon=5, top_n=5, cost=0.01)
    assert costed.total_return < free.total_return


def test_equal_weights_sum_to_one() -> None:
    w = equal_weights()
    assert set(w) == set(FACTOR_NAMES)
    assert sum(w.values()) == pytest.approx(1.0)


def test_benchmark_leg_is_horizon_exact_including_last_period() -> None:
    # The benchmark leg must span exactly `horizon` trading bars on the
    # benchmark calendar (matching fwd_ret_{h}d) — not rebalance-to-rebalance.
    bench = {f"2020010{i}": 100.0 * 1.01**i for i in range(8)}
    bdates = sorted(bench)
    bpos = {d: i for i, d in enumerate(bdates)}
    for d in bdates[:6]:  # i + 2 in range -> exact 2-bar return
        assert _benchmark_leg(bench, bdates, bpos, d, 2) == pytest.approx(1.01**2 - 1)
    assert _benchmark_leg(bench, bdates, bpos, bdates[6], 2) == 0.0  # +2 out of range
    assert _benchmark_leg(bench, bdates, bpos, "19990101", 2) == 0.0  # unknown date


def test_empty_panel_is_safe() -> None:
    empty = pd.DataFrame(
        columns=[
            "date",
            "code",
            *FACTOR_NAMES,
            "fwd_ret_5d",
            "fwd_ret_10d",
            "fwd_ret_20d",
        ]
    )
    res = backtest(empty, {"ep_ttm": 1.0})
    assert res.n_periods == 0
    assert res.total_return == 0
    assert res.net_returns == ()


def test_net_returns_aligned_and_reconstruct_equity() -> None:
    # net_returns must be per-period, time-aligned to dates, and compound to
    # the reported equity / total_return (so disclosure can consume it raw).
    panel = _panel()
    res = backtest(panel, {"ep_ttm": 1.0}, horizon=5, top_n=5)
    assert len(res.net_returns) == res.n_periods
    assert len(res.dates) == res.n_periods
    equity = 1.0
    for r in res.net_returns:
        equity *= 1.0 + r
    assert equity == pytest.approx(1.0 + res.total_return)
    assert res.equity[-1] == pytest.approx(1.0 + res.total_return)


def test_precomputed_groups_match_internal_grouping() -> None:
    # Passing groups=group_by_date(panel) (the search hot path) must give a
    # bit-identical result to letting backtest group internally.
    panel = _panel()
    internal = backtest(panel, {"ep_ttm": 1.0}, horizon=5, top_n=5)
    shared = backtest(
        panel, {"ep_ttm": 1.0}, horizon=5, top_n=5, groups=group_by_date(panel)
    )
    assert internal == shared


def test_orient_override_flips_momentum_incumbent() -> None:
    # ret_20d is attractive-LOW in the registry (reversal). The momentum
    # incumbent override scores it attractive-HIGH → opposite top pick.
    g = _panel(n_dates=1)
    g = g[g["date"] == "20200100"]
    reversal = oriented_rank(g, {"ret_20d": 1.0})
    momentum = oriented_rank(g, {"ret_20d": 1.0}, orient={"ret_20d": True})
    assert g.loc[reversal.idxmax(), "code"] == "600000"  # lowest raw ret_20d
    assert g.loc[momentum.idxmax(), "code"] == "600024"  # highest raw ret_20d
