"""FastAPI monitoring dashboard endpoint (Session C).

Aggregates health, signal volume, analysis freshness, cost, LLM
availability, and circuit-breaker state into a single ``/api/monitoring/
dashboard`` response so the daily-check script and any future operator
UI have one source of truth.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

log = structlog.get_logger(component="api_monitoring")

router = APIRouter(tags=["monitoring"])


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


async def _signals_summary(mongodb: Any, today: str, cutoff_7d: str) -> dict[str, Any]:
    if mongodb is None:
        return {"today": None, "last_7_days": None}
    try:
        today_count = await mongodb.count_signals_for_date(today)
        week_count = await mongodb.count_signals_since(cutoff_7d)
        return {"today": today_count, "last_7_days": week_count}
    except Exception as exc:
        log.warning("signals_summary_failed", error=str(exc))
        return {"today": None, "last_7_days": None}


async def _analysis_summary(mongodb: Any) -> dict[str, Any]:
    if mongodb is None:
        return {"latest_record_at": None, "lag_seconds": None}
    try:
        latest = await mongodb.get_latest_analysis_record()
    except Exception as exc:
        log.warning("analysis_summary_failed", error=str(exc))
        return {"latest_record_at": None, "lag_seconds": None}
    if latest is None:
        return {"latest_record_at": None, "lag_seconds": None}

    created_at = latest.get("completed_at") or latest.get("created_at")
    latest_iso = _to_iso(created_at)
    lag: float | None = None
    if latest_iso is not None:
        try:
            dt = datetime.fromisoformat(latest_iso.replace("Z", "+00:00"))
            lag = (datetime.now(tz=UTC) - dt).total_seconds()
        except ValueError:
            lag = None
    return {"latest_record_at": latest_iso, "lag_seconds": lag}


async def _cost_summary(mongodb: Any, today: str) -> dict[str, Any]:
    import os

    budget = float(os.environ.get("ALERT_COST_DAILY_CNY", "20") or 0)
    if mongodb is None:
        return {"today_cny": None, "daily_budget_cny": budget, "over_budget": False}
    try:
        spent = await mongodb.sum_cost_for_date(today)
    except Exception as exc:
        log.warning("cost_summary_failed", error=str(exc))
        return {"today_cny": None, "daily_budget_cny": budget, "over_budget": False}
    return {
        "today_cny": round(spent, 2),
        "daily_budget_cny": budget,
        "over_budget": spent > budget > 0,
    }


def _llm_summary(router_obj: Any) -> dict[str, Any]:
    """Best-effort LLM provider availability snapshot.

    Reads from the router's config — does not invoke providers. This is
    a fast dashboard heartbeat, not a preflight probe (§D.1 handles the
    active check at request time).
    """
    import os

    if router_obj is None:
        return {"providers": [], "available_count": 0}
    providers: list[dict[str, Any]] = []
    env_keys = {
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "DASHSCOPE_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
    }
    for name, env in env_keys.items():
        providers.append(
            {
                "name": name,
                "env": env,
                "key_present": bool(os.environ.get(env)),
            }
        )
    available = sum(1 for p in providers if p["key_present"])
    return {"providers": providers, "available_count": available}


def _infra_summary(request: Request) -> dict[str, Any]:
    start_time = getattr(request.app.state, "app_start_time", time.time())
    uptime = round(time.time() - start_time, 1)
    return {
        "backend_uptime_seconds": uptime,
        "mongodb_configured": hasattr(request.app.state, "mongodb"),
        "redis_configured": hasattr(request.app.state, "redis"),
    }


def _risk_summary(request: Request) -> dict[str, Any]:
    """Circuit breaker snapshot.

    The breaker is created in `_init_trading_layer` and stored as
    `app.state.circuit_breaker` (NOT on the broker registry — the
    registry is the broker map only). Reading `registry.circuit_breaker`
    used to silently return ``unknown`` even during a real halt.
    """
    breaker = getattr(request.app.state, "circuit_breaker", None)
    if breaker is None:
        return {"circuit_breaker": "unavailable"}

    halted = False
    try:
        is_halted = getattr(breaker, "is_halted", None)
        halted = bool(is_halted()) if callable(is_halted) else bool(is_halted)
    except Exception:
        halted = False

    state = "unknown"
    state_fn = getattr(breaker, "state", None)
    try:
        state = state_fn() if callable(state_fn) else state_fn
    except Exception:
        state = "unknown"

    consecutive_losses = getattr(breaker, "consecutive_losses", None)
    try:
        consecutive_losses = (
            consecutive_losses()
            if callable(consecutive_losses)
            else consecutive_losses
        )
    except Exception:
        consecutive_losses = None

    return {
        "circuit_breaker": state if state is not None else "unknown",
        "halted": halted,
        "consecutive_losses": consecutive_losses,
    }


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, str):
        return value
    return None


def _overall_status(
    infra: dict[str, Any],
    llm: dict[str, Any],
    cost: dict[str, Any],
    analysis: dict[str, Any],
) -> str:
    """Combine component signals into ok / degraded / critical.

    critical: MongoDB/Redis not wired up OR every LLM provider missing.
    degraded: over budget, stale analysis (>36h), or single provider miss.
    ok: everything green.
    """
    if not infra.get("mongodb_configured") or not infra.get("redis_configured"):
        return "critical"
    if llm.get("available_count", 0) == 0:
        return "critical"

    if cost.get("over_budget"):
        return "degraded"
    lag = analysis.get("lag_seconds")
    if lag is not None and lag > 36 * 3600:
        return "degraded"
    if llm.get("available_count", 0) < len(llm.get("providers", [])):
        return "degraded"
    return "ok"


@router.get("/api/monitoring/dashboard")
async def dashboard(request: Request) -> dict[str, Any]:
    """Aggregated operator dashboard for evaluation-period oversight."""
    mongodb = getattr(request.app.state, "mongodb", None)
    router_obj = getattr(request.app.state, "llm_router", None)

    now = datetime.now(tz=UTC)
    today = now.strftime("%Y-%m-%d")
    cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    signals = await _signals_summary(mongodb, today, cutoff_7d)
    analysis = await _analysis_summary(mongodb)
    cost = await _cost_summary(mongodb, today)
    llm = _llm_summary(router_obj)
    infra = _infra_summary(request)
    risk = _risk_summary(request)
    overall = _overall_status(infra, llm, cost, analysis)

    payload = {
        "overall_status": overall,
        "timestamp": now.isoformat(),
        "signals": signals,
        "analysis": analysis,
        "cost": cost,
        "llm": llm,
        "infra": infra,
        "risk": risk,
    }
    return _ok(payload)
