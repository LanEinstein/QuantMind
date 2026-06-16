"""Portfolio-sort net-of-cost backtest over the factor panel (Phase 3).

Turns a factor weighting into a realistic long-only ≤N-slot equity curve vs
CSI300. At each rebalance date the factors are oriented (percentile rank,
inverted where the registry prior is attractive-low), combined into a weighted
composite, the top-N are held equal-weight for one rebalance period, and the
holding-period forward return is realised **net of A-share round-trip cost**
applied to the traded (turned-over) fraction.

Cost model (aligned to ``config/broker.yaml``): per fully-turned-over name,
buy = commission 0.015% + ~1.5bp slippage, sell = commission 0.015% +
stamp 0.1% + ~1.5bp slippage ≈ 0.16% round-trip. The panel's rebalance
spacing equals the forward horizon, so holding periods are non-overlapping
and the equity curve compounds cleanly.

Limitation (documented): this is a portfolio-sort backtest — it does NOT model
T+1 same-day constraints or limit-up/down at-fill rejection (the panel already
excludes by board/liquidity/price, and entries are next-period). The single
selected strategy should additionally be cross-checked through the full
``backend.backtest`` event-loop engine before the Phase-4 verdict. Reads only
the train_val panel (sacred split enforced upstream); deterministic.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .factor_lib import FACTOR_NAMES, FACTORS_BY_NAME

# A-share round-trip cost on the turned-over fraction (≈ broker.yaml rates).
ROUND_TRIP_COST: float = 0.0016
DEFAULT_TOP_N: int = 5
DEFAULT_HORIZON: int = 5  # must equal the panel's rebalance spacing
_PERIODS_PER_YEAR_BASE: int = 252


@dataclass(frozen=True)
class BacktestResult:
    """Net-of-cost portfolio-sort backtest outcome."""

    n_periods: int
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    bench_total_return: float
    excess_vs_bench: float
    avg_turnover: float
    win_rate: float
    equity: tuple[float, ...]
    bench_equity: tuple[float, ...]
    dates: tuple[str, ...]


def oriented_rank(group: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted composite of oriented (attractive-high) percentile ranks."""
    score = pd.Series(0.0, index=group.index)
    for factor, w in weights.items():
        if w == 0 or factor not in group.columns:
            continue
        r = group[factor].rank(pct=True)
        if not FACTORS_BY_NAME[factor].attractive_high:
            r = 1.0 - r  # attractive-low → invert
        score = score + w * r
    return score


def load_benchmark(path: str) -> dict[str, float]:
    """CSI300 close keyed by YYYYMMDD trade date."""
    df = pd.read_csv(path, dtype={"trade_date": str})
    return dict(
        zip(df["trade_date"].astype(str), df["close"].astype(float), strict=False)
    )


def backtest(
    panel: pd.DataFrame,
    weights: dict[str, float],
    *,
    benchmark: dict[str, float] | None = None,
    horizon: int = DEFAULT_HORIZON,
    top_n: int = DEFAULT_TOP_N,
    cost: float = ROUND_TRIP_COST,
) -> BacktestResult:
    """Backtest ``weights`` over the panel; return net-of-cost statistics."""
    fwd_col = f"fwd_ret_{horizon}d"
    weighted = [f for f, w in weights.items() if w > 0 and f in panel.columns]
    dates = sorted(panel["date"].astype(str).unique())

    prev_basket: set[str] = set()
    net_rets: list[float] = []
    turnovers: list[float] = []
    used_dates: list[str] = []
    bench_rets: list[float] = []

    for i, d in enumerate(dates):
        g = panel[panel["date"].astype(str) == d].dropna(subset=[fwd_col, *weighted])
        if len(g) < top_n:
            continue
        g = g.assign(_score=oriented_rank(g, weights))
        g = g.sort_values(["_score", "code"], ascending=[False, True])
        picks = g.head(top_n)
        basket = set(picks["code"].astype(str))
        gross = float(picks[fwd_col].mean())
        turnover = len(basket - prev_basket) / top_n if prev_basket else 1.0
        net_rets.append(gross - turnover * cost)
        turnovers.append(turnover)
        used_dates.append(d)
        prev_basket = basket
        # Benchmark over the same holding period (this rebalance → the next).
        if benchmark is not None and i + 1 < len(dates):
            b0, b1 = benchmark.get(d), benchmark.get(dates[i + 1])
            bench_rets.append((b1 / b0 - 1.0) if b0 and b1 and b0 > 0 else 0.0)
        elif benchmark is not None:
            bench_rets.append(0.0)

    return _summarize(net_rets, bench_rets, turnovers, used_dates, horizon)


def _summarize(
    net_rets: list[float],
    bench_rets: list[float],
    turnovers: list[float],
    dates: list[str],
    horizon: int,
) -> BacktestResult:
    n = len(net_rets)
    if n == 0:
        return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, 0, (1.0,), (1.0,), ())
    arr = np.array(net_rets, dtype=float)
    equity = np.cumprod(1.0 + arr)
    total = float(equity[-1] - 1.0)
    ppy = _PERIODS_PER_YEAR_BASE / horizon
    annual = float((1.0 + total) ** (ppy / n) - 1.0) if total > -1.0 else -1.0
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(arr.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    mdd = float((1.0 - equity / peak).max())
    bench_eq = (
        np.cumprod(1.0 + np.array(bench_rets, dtype=float))
        if bench_rets
        else np.array([1.0])
    )
    bench_total = float(bench_eq[-1] - 1.0) if bench_rets else 0.0
    return BacktestResult(
        n_periods=n,
        total_return=total,
        annual_return=annual,
        sharpe=sharpe,
        max_drawdown=mdd,
        bench_total_return=bench_total,
        excess_vs_bench=total - bench_total,
        avg_turnover=float(np.mean(turnovers)),
        win_rate=float((arr > 0).mean()),
        equity=tuple(float(x) for x in equity),
        bench_equity=tuple(float(x) for x in bench_eq),
        dates=tuple(dates),
    )


def equal_weights() -> dict[str, float]:
    """Equal weight across all factors (a neutral starting point)."""
    w = 1.0 / len(FACTOR_NAMES)
    return {f: w for f in FACTOR_NAMES}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="data/factor_research/panel_train_val.csv")
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"date": str, "code": str})
    bench = load_benchmark(args.benchmark)
    res = backtest(
        panel, equal_weights(), benchmark=bench, horizon=args.horizon, top_n=args.top_n
    )
    print(
        f"equal-weight baseline: periods={res.n_periods} "
        f"total={res.total_return:+.2%} annual={res.annual_return:+.2%} "
        f"sharpe={res.sharpe:+.2f} mdd={res.max_drawdown:.2%} "
        f"bench={res.bench_total_return:+.2%} excess={res.excess_vs_bench:+.2%} "
        f"turnover={res.avg_turnover:.2f} win={res.win_rate:.2%}"
    )


if __name__ == "__main__":
    main()
