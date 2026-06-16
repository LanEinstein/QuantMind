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
    """Net-of-cost portfolio-sort backtest outcome.

    ``net_returns`` is the per-rebalance-period net (post-cost) return series,
    time-aligned to ``dates``. It is the raw input the multiple-testing
    disclosure (DSR / PBO-CSCV / SPA in :mod:`stats_disclosure`) consumes — the
    summary scalars are all derived from it, so exposing it lets the weight
    search assemble per-candidate return matrices without re-running backtests.
    """

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
    net_returns: tuple[float, ...]


def oriented_rank(
    group: pd.DataFrame,
    weights: dict[str, float],
    *,
    orient: dict[str, bool] | None = None,
) -> pd.Series:
    """Weighted composite of oriented (attractive-high) percentile ranks.

    ``orient`` optionally overrides a factor's registry orientation
    (``{factor: attractive_high}``). Its only use is constructing the live
    *momentum* incumbent — scoring ``ret_20d`` as attractive-HIGH (the live
    ``screener.FACTOR_WEIGHTS`` bet) rather than the registry's reversal
    orientation — so the SPA disclosure can test candidates against it. With
    ``orient=None`` every factor uses its literature-prior registry orientation.
    """
    score = pd.Series(0.0, index=group.index)
    for factor, w in weights.items():
        if w == 0 or factor not in group.columns:
            continue
        r = group[factor].rank(pct=True)
        attractive_high = (
            orient[factor]
            if orient is not None and factor in orient
            else FACTORS_BY_NAME[factor].attractive_high
        )
        if not attractive_high:
            r = 1.0 - r  # attractive-low → invert
        score = score + w * r
    return score


def load_benchmark(path: str) -> dict[str, float]:
    """CSI300 close keyed by YYYYMMDD trade date."""
    df = pd.read_csv(path, dtype={"trade_date": str})
    return dict(
        zip(df["trade_date"].astype(str), df["close"].astype(float), strict=False)
    )


def group_by_date(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per-date sub-frames keyed by YYYYMMDD trade date.

    Grouping is weighting-independent, so a search that backtests many
    weightings over the same panel should group once and pass the result into
    :func:`backtest` (``groups=``) rather than re-grouping per candidate.
    """
    return {str(d): sub for d, sub in panel.groupby(panel["date"].astype(str))}


def _benchmark_leg(
    benchmark: dict[str, float],
    bench_dates: list[str],
    bench_pos: dict[str, int],
    d: str,
    horizon: int,
) -> float:
    """CSI300 return over the same ``horizon`` trading bars as the strategy leg.

    Measured on the benchmark's own calendar (``d`` → the bar ``horizon`` trading
    days later), so it matches the strategy's ``fwd_ret_{horizon}d`` holding
    window exactly for *every* rebalance — including the last — instead of
    spanning rebalance-to-rebalance (which equals the horizon only when no period
    is skipped, and left the final period uncompensated). Returns 0.0 when ``d``
    or its ``+horizon`` bar falls outside the benchmark calendar.
    """
    i = bench_pos.get(d)
    if i is None or i + horizon >= len(bench_dates):
        return 0.0
    b0 = benchmark[bench_dates[i]]
    b1 = benchmark[bench_dates[i + horizon]]
    return (b1 / b0 - 1.0) if b0 > 0 and b1 > 0 else 0.0


def backtest(
    panel: pd.DataFrame,
    weights: dict[str, float],
    *,
    benchmark: dict[str, float] | None = None,
    horizon: int = DEFAULT_HORIZON,
    top_n: int = DEFAULT_TOP_N,
    cost: float = ROUND_TRIP_COST,
    orient: dict[str, bool] | None = None,
    groups: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    """Backtest ``weights`` over the panel; return net-of-cost statistics.

    ``orient`` is forwarded to :func:`oriented_rank` to override factor
    orientation (used only for the momentum incumbent — see that function).
    ``groups`` optionally supplies the date→sub-frame mapping from
    :func:`group_by_date`; when a search reuses one panel across many
    weightings, grouping once and passing it in avoids re-grouping per call.
    """
    fwd_col = f"fwd_ret_{horizon}d"
    weighted = [f for f, w in weights.items() if w > 0 and f in panel.columns]
    # Group by date once (not a full-column scan per period); a search reusing
    # the panel across candidates passes a precomputed mapping (groups=).
    if groups is None:
        groups = group_by_date(panel)
    dates = sorted(groups)
    # Benchmark calendar (for horizon-exact CSI300 legs — see _benchmark_leg).
    bench_dates = sorted(benchmark) if benchmark is not None else []
    bench_pos = {dt: i for i, dt in enumerate(bench_dates)}

    prev_basket: set[str] = set()
    net_rets: list[float] = []
    turnovers: list[float] = []
    used_dates: list[str] = []
    bench_rets: list[float] = []

    for d in dates:
        g = groups[d].dropna(subset=[fwd_col, *weighted])
        if len(g) < top_n:
            continue
        g = g.assign(_score=oriented_rank(g, weights, orient=orient))
        g = g.sort_values(["_score", "code"], ascending=[False, True])
        picks = g.head(top_n)
        basket = set(picks["code"].astype(str))
        gross = float(picks[fwd_col].mean())
        turnover = len(basket - prev_basket) / top_n if prev_basket else 1.0
        net_rets.append(gross - turnover * cost)
        turnovers.append(turnover)
        used_dates.append(d)
        prev_basket = basket
        if benchmark is not None:
            bench_rets.append(
                _benchmark_leg(benchmark, bench_dates, bench_pos, d, horizon)
            )

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
        return BacktestResult(
            n_periods=0,
            total_return=0.0,
            annual_return=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
            bench_total_return=0.0,
            excess_vs_bench=0.0,
            avg_turnover=0.0,
            win_rate=0.0,
            equity=(1.0,),
            bench_equity=(1.0,),
            dates=(),
            net_returns=(),
        )
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
        net_returns=tuple(float(x) for x in arr),
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
