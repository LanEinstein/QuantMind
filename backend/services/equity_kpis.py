"""EquityPoint-sourced KPI computation (AD-001).

Pure functions over a daily :class:`backend.models.equity.EquityPoint`
series — the **source of truth** for the front-end KPI header, replacing the
trade-net-amount-derived curve. owner: "收益率/年化/跑赢指数高 = 具备实盘能力 —
这个证据必须干净". So the maths is deterministic, isolated, and unit-tested.

Short-window handling (P2-2-amendment-2026-06-12 §1.6): a window under
``ANNUALIZED_WINDOW_FLOOR`` trading days makes the annualised figure
statistically meaningless — it is still computed but flagged
``annualized_reliable=False`` so the UI can down-weight / caveat it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

TRADING_DAYS_PER_YEAR = 252
ANNUALIZED_WINDOW_FLOOR = 45
"""Below this many trading days the annualised return is not reliable."""


def _equity_series(points: Sequence[Any]) -> list[float]:
    return [float(p.total_equity) for p in points]


def compute_max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough fractional drawdown (<= 0)."""
    peak = float("-inf")
    worst = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (value - peak) / peak
            if dd < worst:
                worst = dd
    return worst


def compute_sharpe(equity: Sequence[float]) -> float:
    """Annualised Sharpe from daily simple returns (risk-free = 0)."""
    if len(equity) < 2:
        return 0.0
    returns: list[float] = []
    for prev, cur in zip(equity, equity[1:], strict=False):
        if prev > 0:
            returns.append(cur / prev - 1.0)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def count_policy_segments(points: Sequence[Any]) -> int:
    """Number of contiguous policy_hash runs in the series."""
    segments = 0
    last: object = object()
    for p in points:
        h = getattr(p, "policy_hash", None)
        if h != last:
            segments += 1
            last = h
    return segments


def _benchmark_total_return(
    benchmark_prices: Sequence[dict[str, Any]] | None,
) -> float | None:
    """Total return of the benchmark over its first→last close, or None."""
    if not benchmark_prices:
        return None
    closes = [float(bp["close"]) for bp in benchmark_prices if bp.get("close")]
    closes = [c for c in closes if c > 0]
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return closes[-1] / closes[0] - 1.0


def compute_equity_kpis(
    points: Sequence[Any],
    *,
    benchmark_prices: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """KPI header bundle from a daily EquityPoint series.

    Returns a stable shape even for an empty / single-point series so the
    front-end never has to special-case missing keys. ``hs300_excess`` is
    None when no benchmark series is available (the UI shows "—").
    """
    sample_days = len(points)
    equity = _equity_series(points)

    if sample_days == 0:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "annualized_reliable": False,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "hs300_excess": None,
            "sample_trading_days": 0,
            "policy_segment_count": 0,
            "data_quality": {},
            "latest_total_equity": 0.0,
        }

    last = points[-1]
    # EquityPoint.pnl_pct is already (pnl / initial_capital).
    total_return = float(getattr(last, "pnl_pct", 0.0))

    reliable = sample_days >= ANNUALIZED_WINDOW_FLOOR
    if total_return <= -1.0:
        annualized = -1.0
    else:
        exponent = TRADING_DAYS_PER_YEAR / max(sample_days, 1)
        annualized = (1.0 + total_return) ** exponent - 1.0

    bench_return = _benchmark_total_return(benchmark_prices)
    hs300_excess = (
        round(total_return - bench_return, 6)
        if bench_return is not None
        else None
    )

    data_quality: dict[str, int] = {}
    for p in points:
        q = getattr(p, "quality", None)
        key = getattr(q, "value", str(q)) if q is not None else "unknown"
        data_quality[key] = data_quality.get(key, 0) + 1

    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "annualized_reliable": reliable,
        "max_drawdown": round(compute_max_drawdown(equity), 6),
        "sharpe_ratio": round(compute_sharpe(equity), 4),
        "hs300_excess": hs300_excess,
        "sample_trading_days": sample_days,
        "policy_segment_count": count_policy_segments(points),
        "data_quality": data_quality,
        "latest_total_equity": round(float(last.total_equity), 2),
    }


__all__ = [
    "ANNUALIZED_WINDOW_FLOOR",
    "compute_equity_kpis",
    "compute_max_drawdown",
    "compute_sharpe",
    "count_policy_segments",
]
