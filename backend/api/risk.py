"""FastAPI routes for risk control center."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from backend.data.publisher import publish_portfolio_event
from backend.services.authorization import (
    CrossPhaseAuthorizationError,
    assert_mode_allowed_for_phase,
    normalize_mode,
)


# Inverse of authorization._LONG_TO_SHORT, kept here because it is a
# pure presentation concern: the env / policy layer canonicalizes to
# the short form, the API response layer continues to emit the legacy
# long form so existing frontend / clients (including the playwright
# E2E suite) keep working without a coordinated migration.
_SHORT_TO_LONG: dict[str, str] = {
    "suggest": "suggestion",
    "confirm": "semi_auto",
    "auto": "full_auto",
}


def _to_legacy_long(canonical: str) -> str:
    """Project canonical short modes back onto the legacy display form."""
    return _SHORT_TO_LONG.get(canonical, canonical)

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
# Request / response schemas
# ---------------------------------------------------------------------------


class AuthModeRequest(BaseModel):
    """Body for POST /api/risk/auth-mode."""

    model_config = ConfigDict(frozen=True)

    mode: str  # "suggestion" | "semi_auto" | "full_auto"


class RiskConfigUpdate(BaseModel):
    """Body for POST /api/risk/config — all fields optional."""

    model_config = ConfigDict(frozen=True)

    single_stock_limit: float | None = None
    total_position_limit: float | None = None
    stop_loss_threshold: float | None = None
    circuit_breaker_threshold: float | None = None
    llm_timeout_seconds: float | None = None
    llm_max_consecutive_failures: int | None = None
    price_deviation_limit: float | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_AUTH_MODES = {
    # Canonical short forms (master plan §2.9 vocabulary).
    "suggest",
    "confirm",
    "auto",
    # Legacy long forms accepted for back-compat with older clients.
    "suggestion",
    "semi_auto",
    "full_auto",
}


def _get_auth_mode() -> str:
    """Read current authorization mode in legacy long form for API responses.

    The env now stores the canonical short form (P5A-T03 redline), so
    we normalize first, then project to the long form the existing
    frontend expects. This kills the prior ``replace("suggest",
    "suggestion")`` bug that produced ``"suggestionion"`` when env was
    already the long form.
    """
    canonical = normalize_mode(os.environ.get("AUTHORIZATION_MODE", "suggest"))
    return _to_legacy_long(canonical)


def _build_risk_status(
    request: Request,
    auth_mode: str | None = None,
) -> dict[str, Any]:
    """Build the RiskStatus response dict from current app state."""
    risk_config = getattr(request.app.state, "risk_config", None)
    circuit_breaker = getattr(request.app.state, "circuit_breaker", None)

    cb_triggered = False
    if circuit_breaker is not None:
        cb_triggered = circuit_breaker.is_halted()

    # Determine system status
    if cb_triggered:
        system_status = "circuit_breaker"
    elif risk_config is None:
        system_status = "warning"
    else:
        system_status = "normal"

    # Count today's events from Redis (best-effort)
    stop_loss_today = getattr(request.app.state, "_stop_loss_count_today", 0)
    llm_intercepts = getattr(request.app.state, "_llm_intercepts_today", 0)

    return {
        "system_status": system_status,
        "authorization_mode": auth_mode or _get_auth_mode(),
        "stop_loss_triggers_today": stop_loss_today,
        "circuit_breaker_triggered": cb_triggered,
        "llm_intercepts_today": llm_intercepts,
    }


async def _build_radar_data(request: Request) -> dict[str, Any]:
    """Build radar chart data from current positions and risk config."""
    risk_config = getattr(request.app.state, "risk_config", None)
    registry = getattr(request.app.state, "broker_registry", None)

    # Defaults from config or fallbacks
    total_position_limit = 80
    single_stock_limit = 20
    sector_limit = 40
    daily_loss_limit = 3
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
        total_position_limit = 80  # Not in config, use default

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
        "total_position_limit": 80,  # Not in yaml, hardcoded default
        "stop_loss_threshold": -(risk_config.stop_loss.single_stock_pct * 100),
        "circuit_breaker_threshold": -(
            risk_config.circuit_breaker.daily_loss_limit_pct * 100
        ),
        "llm_timeout_seconds": 30,  # Default, not in risk config
        "llm_max_consecutive_failures": risk_config.circuit_breaker.consecutive_loss_count,
        "price_deviation_limit": risk_config.position_limits.price_deviation_limit * 100,
    }


def _apply_config_updates(
    current: dict[str, Any], updates: RiskConfigUpdate
) -> dict[str, Any]:
    """Apply partial updates to the config response dict (immutable)."""
    result = {**current}
    for field, value in updates.model_dump(exclude_none=True).items():
        if field in result:
            result[field] = value
    return result


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
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
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


@router.post("/api/risk/config")
async def update_risk_config(
    request: Request, body: RiskConfigUpdate
) -> dict[str, Any]:
    """Update risk configuration parameters.

    Applies partial updates and persists to config/risk.yaml.
    """
    risk_config = getattr(request.app.state, "risk_config", None)
    if risk_config is None:
        _err("Risk config not loaded", 503)

    current = _config_to_response(risk_config)
    updated = _apply_config_updates(current, body)

    # Persist back to YAML
    try:
        _persist_risk_config(updated)
        # Reload the config from disk
        from backend.broker.models import load_risk_config

        request.app.state.risk_config = load_risk_config("config/risk.yaml")
    except Exception as exc:
        log.error("risk_config_persist_failed", error=str(exc))
        _err(f"Failed to save config: {exc}", 500)

    record_risk_event(
        "info",
        f"Risk config updated: {body.model_dump(exclude_none=True)}",
        "Config saved to risk.yaml",
    )

    return _ok(_config_to_response(request.app.state.risk_config))


def _persist_risk_config(frontend_config: dict[str, Any]) -> None:
    """Write the frontend config back to config/risk.yaml."""
    yaml_data = {
        "position_limits": {
            "max_single_stock_pct": frontend_config["single_stock_limit"] / 100,
            "max_sector_pct": 0.40,
            "max_total_positions": 10,
            "price_deviation_limit": frontend_config["price_deviation_limit"] / 100,
            "volume_lot_size": 100,
        },
        "stop_loss": {
            "single_stock_pct": abs(frontend_config["stop_loss_threshold"]) / 100,
            "portfolio_daily_pct": 0.05,
            "trailing_stop_pct": 0.10,
        },
        "circuit_breaker": {
            "daily_loss_limit_pct": abs(
                frontend_config["circuit_breaker_threshold"]
            )
            / 100,
            "consecutive_loss_count": frontend_config[
                "llm_max_consecutive_failures"
            ],
            "cooldown_minutes": 60,
        },
    }
    from pathlib import Path

    path = Path("config/risk.yaml")
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(
            yaml_data,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )


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


@router.post("/api/risk/auth-mode")
async def switch_auth_mode(
    request: Request, body: AuthModeRequest
) -> dict[str, Any]:
    """Switch the authorization mode (suggestion/semi_auto/full_auto).

    Beyond format validation, the request is rejected with 403 when
    the requested mode is not allowed in the active QUANTMIND_PHASE
    (P5A-T03 redline). This blocks accidental cross-phase escalation
    such as enabling ``full_auto`` while the system is still in
    ``phase5_eval``.
    """
    if body.mode not in _VALID_AUTH_MODES:
        _err(
            f"Invalid mode: {body.mode}. Must be one of {_VALID_AUTH_MODES}",
            422,
        )

    try:
        canonical = assert_mode_allowed_for_phase(body.mode)
    except CrossPhaseAuthorizationError as exc:
        _err(str(exc), 403)

    # Persist the canonical short form so audit trail / startup
    # assertion / cost_guard etc. see one normalized vocabulary, no
    # matter what alias the API client used.
    os.environ["AUTHORIZATION_MODE"] = canonical
    log.info(
        "auth_mode_switched",
        canonical_mode=canonical,
        requested_mode=body.mode,
    )

    redis_client = getattr(request.app.state, "redis", None)
    await publish_portfolio_event(
        redis_client,
        "auth_mode_change",
        {"mode": canonical, "system_status": "normal"},
    )

    record_risk_event(
        "info",
        f"Authorization mode switched to {canonical}",
        "Mode updated",
    )

    # API response stays in the legacy long form so existing frontend
    # consumers (AuthorizationMode = 'suggestion'|'semi_auto'|'full_auto')
    # keep rendering correctly. A coordinated frontend migration to the
    # canonical vocabulary is a separate change.
    display_mode = _to_legacy_long(canonical)
    return _ok(_build_risk_status(request, auth_mode=display_mode))
