"""Market-neutral reference arm (R2-3 / T3) — RESEARCH ONLY, never deployable.

The long-short / market-neutral arm bounds the *alpha upper bound* of the carry
factors: a long basket of the top-composite names hedged by a short CSI300 index
leg (beta removed), reported as a pure-alpha series. It exists only to interpret
the deployable benchmark-relative arm (T2):

* if market-neutral IR ≫ benchmark-relative IR, the bounded tilt is leaving
  alpha on the table (R2-4 can loosen TE / a_max);
* if both are weak, the factors carry little alpha in-sample (an honest negative
  read).

HARD RED LINE (CLAUDE.md): QuantMind is long-only + 永禁真实下单, and A-share
retail cannot borrow to short. This arm is **paper research only** — it is NEVER
deployed, NEVER a PASS claim, and NEVER enters the R2-6 forward verdict. The
``RESEARCH_ONLY`` flag and ``MarketNeutralResult.research_only`` make that
explicit by construction. Deterministic, train_val only, LLM-zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .benchmark_relative import (
    BUY_COST,
    SELL_COST,
    composite_score,
    drift_weights,
    weight_turnover,
)
from .portfolio_backtest import group_by_date

# Compile-time honesty marker: this module is research-only, never deployable.
RESEARCH_ONLY: bool = True
_PERIODS_PER_YEAR_BASE: int = 252
DEFAULT_TOP_QUANTILE: float = 0.2


@dataclass(frozen=True)
class MarketNeutralResult:
    """Pure-alpha (long basket − short index) outcome. RESEARCH ONLY."""

    n_periods: int
    total_alpha: float
    annual_alpha: float
    alpha_sharpe: float
    max_drawdown: float
    avg_turnover: float
    alpha_returns: tuple[float, ...]
    dates: tuple[str, ...]
    research_only: bool = True  # never a PASS claim, never deployed


def market_neutral_backtest(
    panel: pd.DataFrame,
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    horizon: int = 5,
    top_quantile: float = DEFAULT_TOP_QUANTILE,
    buy_cost: float = BUY_COST,
    sell_cost: float = SELL_COST,
) -> MarketNeutralResult:
    """Long top-quantile composite (equal-weight) − short CSI300 → alpha series.

    ``index_returns[d]`` is the CSI300 return over the same ``horizon`` bars
    (the short hedge). Alpha = long-basket return − index return, net of a
    buy/sell-split cost on the long-leg turnover. RESEARCH ONLY.
    """
    fwd_col = f"fwd_ret_{horizon}d"
    groups = group_by_date(panel)
    prev_w: dict[str, float] = {}

    alphas: list[float] = []
    turnovers: list[float] = []
    used: list[str] = []

    for d in sorted(groups):
        bench_ret = index_returns.get(d)
        if bench_ret is None:
            continue
        g = groups[d].dropna(subset=[fwd_col])
        score = composite_score(g, weights).dropna()
        if len(score) < 5:
            continue
        cutoff = float(score.quantile(1.0 - top_quantile))
        longs = {str(c) for c, s in score.items() if s >= cutoff}
        if not longs:
            continue
        fwd = {
            str(c): float(v)
            for c, v in zip(g["ts_code"].astype(str), g[fwd_col], strict=True)
        }
        long_ret = float(np.mean([fwd[c] for c in longs if c in fwd]))
        # Equal-weight book; turnover on the WEIGHT vector (1/n) so a changing
        # basket size also charges the resize of retained names (codex P2 — a
        # set-diff misses 50%→25% reweights when the basket grows/shrinks).
        new_w = {c: 1.0 / len(longs) for c in longs}
        buy, sell = weight_turnover(prev_w, new_w)
        cost = buy * buy_cost + sell * sell_cost
        alphas.append(long_ret - bench_ret - cost)
        turnovers.append(buy)
        # Next turnover trades back from the DRIFTED end-of-period holdings, not
        # the old equal-weight target (codex P2: target-to-target understates
        # cost when names drift apart over the holding period).
        prev_w = drift_weights(new_w, fwd, long_ret)
        used.append(d)

    return _summarize(alphas, turnovers, used, horizon)


def _summarize(
    alphas: list[float], turnovers: list[float], dates: list[str], horizon: int
) -> MarketNeutralResult:
    n = len(alphas)
    if n == 0:
        return MarketNeutralResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, (), ())
    arr = np.array(alphas, dtype=float)
    curve = np.cumprod(1.0 + arr)
    total = float(curve[-1] - 1.0)
    ppy = _PERIODS_PER_YEAR_BASE / horizon
    annual = float((1.0 + total) ** (ppy / n) - 1.0) if total > -1.0 else -1.0
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(arr.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    # Prepend the initial capital (1.0) so a first-period loss is counted in the
    # peak — else MDD can read 0% while underwater (codex P3).
    equity = np.concatenate([[1.0], curve])
    peak = np.maximum.accumulate(equity)
    mdd = float((1.0 - equity / peak).max())
    return MarketNeutralResult(
        n_periods=n,
        total_alpha=total,
        annual_alpha=annual,
        alpha_sharpe=sharpe,
        max_drawdown=mdd,
        avg_turnover=float(np.mean(turnovers)),
        alpha_returns=tuple(float(x) for x in arr),
        dates=tuple(dates),
    )


__all__ = [
    "DEFAULT_TOP_QUANTILE",
    "RESEARCH_ONLY",
    "MarketNeutralResult",
    "market_neutral_backtest",
]
