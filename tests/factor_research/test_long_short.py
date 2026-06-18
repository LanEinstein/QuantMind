"""Tests for the market-neutral reference arm (R2-3 / T3, research-only)."""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.benchmark_relative import CARRY_FACTORS
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME
from scripts.factor_research.long_short import (
    RESEARCH_ONLY,
    market_neutral_backtest,
)


def _panel() -> pd.DataFrame:
    rows = []
    for date in ("20240105", "20240112"):
        # 10 names; composite rises with sc; high-sc names get high fwd.
        for i in range(10):
            sc = float(i)
            row: dict[str, object] = {
                "date": date,
                "ts_code": f"{600000 + i}.SH",
                "fwd_ret_5d": 0.01 * i,  # higher rank → higher forward return
            }
            for base in CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
                row[f"{base}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def test_research_only_flag_is_set() -> None:
    assert RESEARCH_ONLY is True


def test_market_neutral_alpha_positive_on_flat_index() -> None:
    panel = _panel()
    index_returns = {"20240105": 0.0, "20240112": 0.0}
    res = market_neutral_backtest(
        panel,
        index_returns,
        weights={f: 1.0 for f in CARRY_FACTORS},
        horizon=5,
        top_quantile=0.3,
    )
    assert res.n_periods == 2
    assert res.research_only is True
    # longing the top-30% composite (= top forward returns) minus a flat index
    # → positive alpha
    assert res.total_alpha > 0


def test_market_neutral_hedges_index_beta() -> None:
    panel = _panel()
    # A strongly positive index should be hedged away (short index leg), so the
    # alpha is the long basket's EXCESS over the index, not its raw return.
    flat = market_neutral_backtest(
        panel,
        {"20240105": 0.0, "20240112": 0.0},
        weights={f: 1.0 for f in CARRY_FACTORS},
        horizon=5,
        top_quantile=0.3,
    )
    bull = market_neutral_backtest(
        panel,
        {"20240105": 0.10, "20240112": 0.10},
        weights={f: 1.0 for f in CARRY_FACTORS},
        horizon=5,
        top_quantile=0.3,
    )
    # alpha = long − index, so a +10% index lowers alpha by ~10% each period
    assert bull.total_alpha < flat.total_alpha


def test_max_drawdown_counts_first_period_loss() -> None:
    # codex P3: a big first-period loss (index +50% vs ~+8% long basket) must
    # show in MDD measured from initial capital, not read 0% while underwater.
    panel = _panel()
    res = market_neutral_backtest(
        panel,
        {"20240105": 0.50, "20240112": 0.0},  # period-1 alpha strongly negative
        weights={f: 1.0 for f in CARRY_FACTORS},
        horizon=5,
        top_quantile=0.3,
    )
    assert res.alpha_returns[0] < 0  # first period is a loss
    assert res.max_drawdown > 0.3  # the drawdown from 1.0 is captured


def test_skips_dates_without_index_return() -> None:
    # The reference arm only runs on dates present in index_returns (the diag
    # filters this to the primary arm's benchmark-covered dates — codex P2).
    panel = _panel()
    res = market_neutral_backtest(
        panel,
        {"20240105": 0.0},  # 20240112 omitted → skipped
        weights={f: 1.0 for f in CARRY_FACTORS},
        horizon=5,
        top_quantile=0.3,
    )
    assert res.n_periods == 1
    assert res.dates == ("20240105",)
