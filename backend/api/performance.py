"""FastAPI routes for performance analytics."""

from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

log = structlog.get_logger(component="api_performance")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


# ---------------------------------------------------------------------------
# Computation helpers — pure functions
# ---------------------------------------------------------------------------


def compute_equity_curve(
    trades: tuple[Any, ...],
    initial_capital: float,
    start: date,
    end: date,
    benchmark_prices: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a daily equity curve from trade history.

    Walks day-by-day from *start* to *end*, applying realized P&L from
    trades to the running portfolio value. Returns a list of
    {date, portfolio, benchmark} points.

    Args:
        benchmark_prices: Optional list of {date, close} dicts for real
            benchmark data. If None, falls back to flat 100.0.
    """
    # Build a date→pnl map from trades
    daily_pnl: dict[str, float] = {}
    for t in trades:
        trade_date = _extract_date(t.traded_at)
        if trade_date < start or trade_date > end:
            continue
        key = trade_date.isoformat()
        daily_pnl[key] = daily_pnl.get(key, 0.0) + t.net_amount

    # Build benchmark lookup: date → close price
    bm_lookup: dict[str, float] = {}
    if benchmark_prices:
        for bp in benchmark_prices:
            bm_lookup[str(bp["date"])] = float(bp["close"])

    points: list[dict[str, Any]] = []
    portfolio = initial_capital
    current = start
    last_bm = 0.0  # Forward-fill for missing benchmark days

    while current <= end:
        # Skip weekends
        if current.weekday() < 5:
            key = current.isoformat()
            pnl = daily_pnl.get(key, 0.0)
            portfolio += pnl
            if bm_lookup:
                bm_val = bm_lookup.get(key)
                if bm_val is not None:
                    last_bm = bm_val
                benchmark_val = last_bm if last_bm > 0 else 100.0
            else:
                benchmark_val = 100.0
            points.append(
                {
                    "date": key,
                    "portfolio": round(portfolio, 2),
                    "benchmark": round(benchmark_val, 2),
                }
            )
        current += timedelta(days=1)

    # Normalize: portfolio starts at 100
    if points and points[0]["portfolio"] != 0:
        base = points[0]["portfolio"]
        for p in points:
            p["portfolio"] = round(p["portfolio"] / base * 100, 2)

    # Normalize benchmark to start at 100
    if points and bm_lookup:
        first_bm = points[0]["benchmark"]
        if first_bm > 0:
            for p in points:
                if p["benchmark"] > 0:
                    p["benchmark"] = round(p["benchmark"] / first_bm * 100, 2)
                else:
                    p["benchmark"] = 100.0
    elif not bm_lookup:
        # Flat fallback: all benchmarks stay at 100.0
        for p in points:
            p["benchmark"] = 100.0

    return points


def compute_drawdown_curve(
    equity_curve: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compute drawdown at each point from the equity curve."""
    peak = 0.0
    result: list[dict[str, Any]] = []
    for point in equity_curve:
        value = point["portfolio"]
        if value > peak:
            peak = value
        dd = (value - peak) / peak if peak > 0 else 0.0
        result.append({"date": point["date"], "drawdown": round(dd, 4)})
    return result


def compute_core_metrics(
    equity_curve: list[dict[str, Any]],
    trades: tuple[Any, ...],
) -> dict[str, Any]:
    """Compute core performance metrics from equity curve and trades."""
    if len(equity_curve) < 2:
        return _empty_metrics()

    # Returns
    values = [p["portfolio"] for p in equity_curve]
    daily_returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]

    if not daily_returns:
        return _empty_metrics()

    # Annualized return (252 trading days)
    total_return = (values[-1] - values[0]) / values[0] if values[0] != 0 else 0
    n_days = len(daily_returns)
    annualized = (1 + total_return) ** (252 / max(n_days, 1)) - 1

    # Sharpe ratio (risk-free rate ~ 0)
    mean_ret = sum(daily_returns) / len(daily_returns)
    std_ret = _std(daily_returns)
    sharpe = (mean_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0

    # Max drawdown
    peak = 0.0
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd

    # Win rate from trades
    winning = sum(1 for t in trades if t.net_amount > 0)
    losing = sum(1 for t in trades if t.net_amount < 0)
    total_trades = winning + losing
    win_rate = winning / total_trades if total_trades > 0 else 0.0

    # Profit/loss ratio
    avg_win = (
        sum(t.net_amount for t in trades if t.net_amount > 0) / winning
        if winning > 0
        else 0.0
    )
    avg_loss = (
        abs(sum(t.net_amount for t in trades if t.net_amount < 0)) / losing
        if losing > 0
        else 1.0
    )
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    # Monthly turnover (simplified)
    total_volume_value = sum(t.amount for t in trades)
    months = max(n_days / 21, 1)
    first_equity = values[0] if values[0] > 0 else 1.0
    monthly_turnover = (total_volume_value / months) / first_equity

    return {
        "annualized_return": round(annualized, 4),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(win_rate, 3),
        "profit_loss_ratio": round(pl_ratio, 2),
        "monthly_turnover": round(monthly_turnover, 3),
    }


def build_model_contributions(
    cost_by_provider: dict[str, float],
    requests_by_provider: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Build model contribution entries from cost tracker data."""
    model_labels = {
        "deepseek": "DeepSeek",
        "qwen": "Qwen",
        "kimi": "Kimi",
    }
    contributions: list[dict[str, Any]] = []

    for provider, label in model_labels.items():
        cost = cost_by_provider.get(provider, 0.0)
        calls = (requests_by_provider or {}).get(provider, 0)
        contributions.append(
            {
                "model": provider,
                "label": label,
                "accuracy_label": "信号准确率",
                "accuracy_value": 0.0,  # Requires signal tracking (Phase 5)
                "call_label": "日均调用",
                "call_value": calls,
                "call_unit": "次",
                "cost_label": "日均成本",
                "cost_value": round(cost, 2),
                "cost_unit": "¥",
            }
        )

    return contributions


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _empty_metrics() -> dict[str, Any]:
    return {
        "annualized_return": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "monthly_turnover": 0.0,
    }


def _std(values: list[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _extract_date(value: str | datetime) -> date:
    """Extract a date from a datetime or ISO string."""
    if isinstance(value, datetime):
        return value.date()
    return datetime.fromisoformat(str(value)).date()


def _parse_date(value: str | None, default: date) -> date:
    """Parse an ISO date string, falling back to default."""
    if value is None:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/performance")
async def get_performance(
    request: Request,
    start: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    benchmark: str | None = Query(None, description="Benchmark index"),
    account_id: str | None = Query(None, description="Account ID"),
) -> dict[str, Any]:
    """Return performance analytics for a date range."""
    today = date.today()
    start_date = _parse_date(start, today - timedelta(days=30))
    end_date = _parse_date(end, today)

    # Get trading data from broker
    registry = getattr(request.app.state, "broker_registry", None)
    if registry is None:
        _err("Trading system not initialized", 503)

    try:
        broker = registry.get_broker(account_id)
        account = await broker.get_account()
        trades = await broker.get_trades()
    except Exception as exc:
        log.warning("performance_broker_error", error=str(exc))
        _err(f"Failed to read trading data: {exc}", 500)

    # Fetch benchmark data from MongoDB
    benchmark_prices: list[dict[str, Any]] | None = None
    mongodb = getattr(request.app.state, "mongodb", None)
    if mongodb:
        try:
            benchmark_code = benchmark or "000300"
            benchmark_prices = await mongodb.get_index_prices(
                benchmark_code,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            if not benchmark_prices:
                benchmark_prices = None
        except Exception as exc:
            log.debug("benchmark_data_unavailable", error=str(exc))

    # Compute analytics
    equity = compute_equity_curve(
        trades, account.initial_capital, start_date, end_date,
        benchmark_prices=benchmark_prices,
    )
    drawdown = compute_drawdown_curve(equity)
    metrics = compute_core_metrics(equity, trades)

    # Model contributions from cost tracker
    cost_by_provider: dict[str, float] = {}
    requests_by_provider: dict[str, int] = {}
    try:
        from backend.llm.cost_tracker import aggregate_costs

        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is not None:
            days = (end_date - start_date).days + 1
            summary = await aggregate_costs(redis_client, days=days)
            cost_by_provider = summary.by_provider
            for entry in summary.entries:
                requests_by_provider[entry.provider] = (
                    requests_by_provider.get(entry.provider, 0) + entry.requests
                )
    except Exception as exc:
        log.debug("cost_tracker_unavailable", error=str(exc))

    model_contributions = build_model_contributions(
        cost_by_provider, requests_by_provider
    )

    return _ok(
        {
            "equity_curve": equity,
            "metrics": metrics,
            "drawdown_curve": drawdown,
            "model_contributions": model_contributions,
        }
    )


@router.get("/api/performance/export/{report_type}")
async def export_performance(
    request: Request,
    report_type: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    account_id: str | None = Query(None),
) -> StreamingResponse:
    """Export a performance report as CSV.

    Args:
        report_type: One of 'daily', 'weekly', 'monthly'.
    """
    if report_type not in {"daily", "weekly", "monthly"}:
        _err("report_type must be daily, weekly, or monthly", 422)

    today = date.today()
    start_date = _parse_date(start, today - timedelta(days=90))
    end_date = _parse_date(end, today)

    registry = getattr(request.app.state, "broker_registry", None)
    if registry is None:
        _err("Trading system not initialized", 503)

    try:
        broker = registry.get_broker(account_id)
        account = await broker.get_account()
        trades = await broker.get_trades()
    except Exception as exc:
        _err(f"Failed to read trading data: {exc}", 500)

    equity = compute_equity_curve(
        trades, account.initial_capital, start_date, end_date
    )
    metrics = compute_core_metrics(equity, trades)

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["QuantMind Performance Report"])
    writer.writerow([f"Period: {start_date} to {end_date}"])
    writer.writerow([f"Type: {report_type}"])
    writer.writerow([])

    writer.writerow(["Core Metrics"])
    for key, value in metrics.items():
        label = key.replace("_", " ").title()
        writer.writerow([label, value])
    writer.writerow([])

    writer.writerow(["Date", "Portfolio", "Benchmark"])
    for point in equity:
        writer.writerow([point["date"], point["portfolio"], point["benchmark"]])

    content = output.getvalue()
    buf = io.BytesIO(content.encode("utf-8-sig"))

    filename = f"quantmind_{report_type}_{start_date}_{end_date}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
