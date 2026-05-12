"""FastAPI routes for risk control center."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from backend.services.run_mode import resolve_run_mode

log = structlog.get_logger(component="api_risk")

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
# Internal helpers
# ---------------------------------------------------------------------------


def _build_risk_status(request: Request) -> dict[str, Any]:
    """Build the RiskStatus response dict from current app state."""
    risk_config = getattr(request.app.state, "risk_config", None)
    circuit_breaker = getattr(request.app.state, "circuit_breaker", None)

    cb_triggered = False
    if circuit_breaker is not None:
        cb_triggered = circuit_breaker.is_halted()

    if cb_triggered:
        system_status = "circuit_breaker"
    elif risk_config is None:
        system_status = "warning"
    else:
        system_status = "normal"

    stop_loss_today = getattr(request.app.state, "_stop_loss_count_today", 0)
    llm_intercepts = getattr(request.app.state, "_llm_intercepts_today", 0)

    run_mode = resolve_run_mode()
    return {
        "system_status": system_status,
        "run_mode": {
            "simulation_auto": run_mode.simulation_auto,
            "feishu_interactive": run_mode.feishu_interactive,
        },
        "stop_loss_triggers_today": stop_loss_today,
        "circuit_breaker_triggered": cb_triggered,
        "llm_intercepts_today": llm_intercepts,
    }


async def _build_radar_data(request: Request) -> dict[str, Any]:
    """Build radar chart data from current positions and risk config."""
    risk_config = getattr(request.app.state, "risk_config", None)
    registry = getattr(request.app.state, "broker_registry", None)

    # Defaults from config or fallbacks. P0-7 locked the conservative
    # trio at 15% / 70% / 50k — values below are the fallbacks for when
    # ``risk_config`` is not yet wired into ``app.state``.
    total_position_limit = 70
    single_stock_limit = 15
    sector_limit = 40
    daily_loss_limit = 5
    stock_count_limit = 10

    if risk_config is not None:
        single_stock_limit = int(
            risk_config.position_limits.max_single_stock_pct * 100
        )
        sector_limit = int(risk_config.position_limits.max_sector_pct * 100)
        daily_loss_limit = int(
            risk_config.circuit_breaker.daily_loss_limit_pct * 100
        )
        stock_count_limit = risk_config.position_limits.max_total_positions
        total_position_limit = int(
            risk_config.position_limits.max_total_position_pct * 100
        )

    # Read actual positions from broker
    total_position_pct = 0.0
    max_single_pct = 0.0
    stock_count = 0

    if registry is not None:
        try:
            broker = registry.get_broker()
            account = await broker.get_account()
            positions = await broker.get_positions()
            stock_count = len(positions)
            if account.total_assets > 0:
                total_position_pct = round(
                    account.market_value / account.total_assets * 100, 1
                )
                for pos in positions:
                    pct = pos.market_value / account.total_assets * 100
                    if pct > max_single_pct:
                        max_single_pct = round(pct, 1)
        except Exception:
            log.debug("radar_data_broker_unavailable")

    # Daily loss from circuit breaker
    circuit_breaker = getattr(request.app.state, "circuit_breaker", None)
    daily_loss_pct = 0.0
    if circuit_breaker is not None:
        daily_loss_pct = round(abs(circuit_breaker._daily_pnl_pct) * 100, 1)

    return {
        "total_position_pct": total_position_pct,
        "total_position_limit": total_position_limit,
        "max_single_stock_pct": max_single_pct,
        "max_single_stock_limit": single_stock_limit,
        "industry_concentration_pct": 0.0,  # Sector tracking not yet wired
        "industry_concentration_limit": sector_limit,
        "daily_loss_pct": daily_loss_pct,
        "daily_loss_limit": daily_loss_limit,
        "stock_count": stock_count,
        "stock_count_limit": stock_count_limit,
    }


def _config_to_response(risk_config: Any) -> dict[str, Any]:
    """Convert backend RiskConfig to frontend-expected shape."""
    return {
        "single_stock_limit": risk_config.position_limits.max_single_stock_pct * 100,
        "total_position_limit": (
            risk_config.position_limits.max_total_position_pct * 100
        ),
        "stop_loss_threshold": -(risk_config.stop_loss.single_stock_pct * 100),
        "circuit_breaker_threshold": -(
            risk_config.circuit_breaker.daily_loss_limit_pct * 100
        ),
        "llm_timeout_seconds": 30,  # Default, not in risk config
        "llm_max_consecutive_failures": (
            risk_config.circuit_breaker.consecutive_loss_count
        ),
        "price_deviation_limit": (
            risk_config.position_limits.price_deviation_limit * 100
        ),
    }


# ---------------------------------------------------------------------------
# Risk events (in-memory store for now, will migrate to MongoDB)
# ---------------------------------------------------------------------------

_risk_events: list[dict[str, Any]] = []


def record_risk_event(
    level: str, description: str, action_taken: str
) -> None:
    """Append a risk event. Called by other modules (risk engine, circuit breaker)."""
    _risk_events.insert(
        0,
        {
            "id": f"evt-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": level,
            "description": description,
            "action_taken": action_taken,
        },
    )
    # Keep at most 500 events in memory
    if len(_risk_events) > 500:
        del _risk_events[500:]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/risk/status")
async def get_risk_status(request: Request) -> dict[str, Any]:
    """Return current risk system status."""
    return _ok(_build_risk_status(request))


@router.get("/api/risk/radar")
async def get_risk_radar(request: Request) -> dict[str, Any]:
    """Return risk radar chart data (position limits vs actuals)."""
    return _ok(await _build_radar_data(request))


@router.get("/api/risk/config")
async def get_risk_config(request: Request) -> dict[str, Any]:
    """Return current risk configuration in frontend-friendly format."""
    risk_config = getattr(request.app.state, "risk_config", None)
    if risk_config is None:
        _err("Risk config not loaded", 503)
    return _ok(_config_to_response(risk_config))


@router.get("/api/risk/events")
async def get_risk_events(
    request: Request,
    level: str | None = Query(None, description="Filter by level"),
    start_date: str | None = Query(None, description="Start date ISO"),
    end_date: str | None = Query(None, description="End date ISO"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Return recent risk events, optionally filtered."""
    filtered = _risk_events

    if level is not None:
        filtered = [e for e in filtered if e["level"] == level]

    if start_date is not None:
        filtered = [e for e in filtered if e["timestamp"] >= start_date]

    if end_date is not None:
        filtered = [e for e in filtered if e["timestamp"] <= end_date]

    return _ok(filtered[:limit])


