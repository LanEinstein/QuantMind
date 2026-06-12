"""FastAPI routes for performance analytics."""

from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from backend.services.equity_kpis import compute_equity_kpis

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


# AD-005 (P1-2.A-amendment-2026-06-12) — 3-way performance split. The
# readiness gauge / KPI header read the SYSTEM_SUGGESTED bucket only so a
# user-discretionary trade's alpha never inflates the system's measured
# capability (codex P0-7).
_ORIGIN_BUCKETS: tuple[str, ...] = (
    "system_suggested",
    "user_discretionary",
    "reconciliation_reset",
)


def _trade_origin(trade: Any) -> str:
    """Origin string of a trade, defaulting to system_suggested.

    Legacy trades (and any duck-typed test double) without an ``origin``
    attribute are treated as system-suggested — the conservative default
    that keeps historical fills in the readiness bucket they already were.
    """
    origin = getattr(trade, "origin", None)
    if origin is None:
        return "system_suggested"
    return getattr(origin, "value", str(origin))


def _trade_is_sell(trade: Any) -> bool:
    direction = getattr(trade, "direction", None)
    return getattr(direction, "value", str(direction)) == "SELL"


def compute_performance_split(trades: tuple[Any, ...]) -> dict[str, Any]:
    """Per-origin trade-count + net-cash-flow breakdown for the 3-way split.

    Pure aggregation over the (already date-clamped) trade set — never a
    no-op default: a bucket with zero trades is still reported so the
    front-end shows an explicit empty bucket rather than a missing one.

    ``Trade.net_amount`` is the sign-free settled cash amount (cost for a
    BUY, proceeds for a SELL) — positive for both — so summing it as "PnL"
    would make every BUY look profitable (codex P2). We instead sign it by
    direction to report a coherent **net cash flow** per bucket (SELL inflow
    positive, BUY outflow negative); true realized PnL needs buy/sell lot
    matching, which is out of scope for this attribution view.
    ``reconciliation_reset`` produces no :class:`Trade` rows, so its bucket
    is always zero here (kept for a complete, stable shape).
    """
    split: dict[str, Any] = {}
    for bucket in _ORIGIN_BUCKETS:
        bucket_trades = [t for t in trades if _trade_origin(t) == bucket]
        net_cash_flow = sum(
            t.net_amount if _trade_is_sell(t) else -t.net_amount
            for t in bucket_trades
        )
        split[bucket] = {
            "trade_count": len(bucket_trades),
            "net_cash_flow": round(net_cash_flow, 2),
        }
    return split


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
    segment: str | None = Query(
        None,
        description=(
            "AA-004 policy segmentation: 'current' clamps the range to "
            "the active policy segment's start date"
        ),
    ),
    origin: str | None = Query(
        None,
        description=(
            "AD-005 3-way split: 'system_suggested' / 'user_discretionary' "
            "filters the metrics+curve to that bucket; default (None / 'all') "
            "is the merged view. The readiness gauge requests "
            "'system_suggested' so user alpha never inflates system capability."
        ),
    ),
) -> dict[str, Any]:
    """Return performance analytics for a date range."""
    today = date.today()
    start_date = _parse_date(start, today - timedelta(days=30))
    end_date = _parse_date(end, today)

    # AA-004 (P2-2-amendment-2026-06-12 §1.6) — segment-aware range:
    # 'current' restricts the analytics window to the active policy
    # segment so a promotion cannot blend old-policy performance into
    # the readiness view. Read-only; unknown values are ignored.
    segment_started = getattr(
        request.app.state, "policy_segment_started", None
    )
    segment_clamped = segment == "current" and segment_started is not None
    if segment_clamped:
        start_date = max(start_date, segment_started)

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

    # Clamp the trades to the analytics window for ALL queries (not only
    # segment=current). compute_equity_curve() filters dates internally, so
    # without this the headline win-rate/turnover AND the AD-005 split would
    # include fills outside the requested [start, end] and disagree with the
    # curve (codex P2). When segment=current, start_date was already advanced
    # to the segment start above, so this single clamp covers both cases.
    trades = tuple(
        t for t in trades if start_date <= t.traded_at.date() <= end_date
    )

    # AD-005 — the 3-way split is computed over the (date-clamped) set BEFORE
    # any origin filter so the breakdown always shows every bucket.
    performance_split = compute_performance_split(trades)

    # Origin filter (AD-005): the readiness gauge requests
    # 'system_suggested' so user-discretionary alpha never lands in the
    # capability evidence. Unknown / 'all' / None → merged view.
    origin_filter = (origin or "all").strip().lower()
    if origin_filter in ("system_suggested", "user_discretionary"):
        trades = tuple(t for t in trades if _trade_origin(t) == origin_filter)

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

    # AA-004 — segment transition ledger so the frontend can annotate
    # policy switch points on the curve.
    policy_segments: list[dict[str, Any]] = []
    segment_store = getattr(request.app.state, "policy_segment_store", None)
    if segment_store is not None:
        try:
            policy_segments = [
                {
                    "policy_hash": row.policy_hash,
                    "started_at": row.started_at.isoformat(),
                    "trade_date": row.trade_date,
                }
                for row in await segment_store.list_all()
            ]
        except Exception as exc:
            log.debug("policy_segments_unavailable", error=str(exc))

    return _ok(
        {
            "equity_curve": equity,
            "metrics": metrics,
            "drawdown_curve": drawdown,
            "model_contributions": model_contributions,
            "policy_segments": policy_segments,
            "active_policy_hash": getattr(
                request.app.state, "policy_hash", None
            ),
            "performance_split": performance_split,
            "origin_filter": origin_filter,
        }
    )


@router.get("/api/performance/equity-kpis")
async def get_equity_kpis(
    request: Request,
    start: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    benchmark: str | None = Query(None, description="Benchmark index"),
) -> dict[str, Any]:
    """AD-001 — EquityPoint-sourced KPI header + readiness inputs (read-only).

    The KPI header (total return / annualized / HS300 excess / max drawdown /
    sharpe) is computed from the EquityPoint daily series — the source of
    truth — NOT the trade-net-amount curve. The equity series is returned
    with per-point ``policy_hash`` so the front-end can segment the curve and
    mark policy switch points. Short windows (<45 trading days) flag
    ``annualized_reliable=False``. Degrades to an empty/clean shape with HTTP
    200 when the repository is unwired (never 500), so the panel can render a
    warm-up state.
    """
    today = date.today()
    start_date = _parse_date(start, today - timedelta(days=60))
    end_date = _parse_date(end, today)

    repo = getattr(request.app.state, "equity_point_repository", None)
    if repo is None:
        return _ok(
            {
                "kpis": compute_equity_kpis([]),
                "equity_series": [],
                "policy_segments": [],
                "active_policy_hash": getattr(
                    request.app.state, "policy_hash", None
                ),
                "repository_status": "unavailable",
            }
        )

    try:
        series = await repo.list_eod_series(
            start_date.isoformat(), end_date.isoformat()
        )
    except Exception as exc:
        log.warning("equity_kpis_repo_error", error=str(exc))
        return _ok(
            {
                "kpis": compute_equity_kpis([]),
                "equity_series": [],
                "policy_segments": [],
                "active_policy_hash": getattr(
                    request.app.state, "policy_hash", None
                ),
                "repository_status": "error",
            }
        )

    benchmark_prices: list[dict[str, Any]] | None = None
    mongodb = getattr(request.app.state, "mongodb", None)
    if mongodb and series:
        try:
            benchmark_prices = await mongodb.get_index_prices(
                benchmark or "000300",
                start_date=series[0].trade_date,
                end_date=series[-1].trade_date,
            ) or None
        except Exception as exc:
            log.debug("equity_kpis_benchmark_unavailable", error=str(exc))

    kpis = compute_equity_kpis(series, benchmark_prices=benchmark_prices)
    equity_series = [
        {
            "trade_date": p.trade_date,
            "total_equity": round(float(p.total_equity), 2),
            "pnl_pct": round(float(p.pnl_pct), 6),
            "policy_hash": p.policy_hash,
            "quality": getattr(p.quality, "value", str(p.quality)),
        }
        for p in series
    ]

    policy_segments: list[dict[str, Any]] = []
    segment_store = getattr(request.app.state, "policy_segment_store", None)
    if segment_store is not None:
        try:
            policy_segments = [
                {
                    "policy_hash": row.policy_hash,
                    "started_at": row.started_at.isoformat(),
                    "trade_date": row.trade_date,
                }
                for row in await segment_store.list_all()
            ]
        except Exception as exc:
            log.debug("equity_kpis_segments_unavailable", error=str(exc))

    return _ok(
        {
            "kpis": kpis,
            "equity_series": equity_series,
            "policy_segments": policy_segments,
            "active_policy_hash": getattr(
                request.app.state, "policy_hash", None
            ),
            "repository_status": "ok",
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
