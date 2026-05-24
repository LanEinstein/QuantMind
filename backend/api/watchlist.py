"""FastAPI routes for watchlist management (GET-only per P0-9 / P1-5).

P0-9-amendment-2026-05-24 replaced the fixed 13-code universe with a
full-market *ruleset* (board whitelist + the four exclusion rules +
long-only). Runtime mutation stays forbidden: Phase A destructively
deleted every POST/PUT/PATCH/DELETE handler in this module so the
universe ruleset is immutable between deploys.

The serialiser exposes every locked section (the universe ruleset,
exclusion rules, cap allocation, direction policy) so the frontend /
audit tooling can verify the active configuration without re-reading the
YAML.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from backend.services.universe_policy import (
    UniversePolicy,
    assign_category,
)

log = structlog.get_logger(component="api_watchlist")

router = APIRouter(tags=["watchlist"])


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


def _get_watchlist(request: Request) -> Any:
    """Extract WatchlistService from app state."""
    svc = getattr(request.app.state, "watchlist", None)
    if not svc:
        _err("Watchlist service not initialized", 503)
    return svc


@router.get("/api/watchlist")
async def list_watchlist(request: Request) -> dict[str, Any]:
    """List all active watchlist stocks."""
    watchlist = _get_watchlist(request)
    stocks = await watchlist.list_stocks()
    for s in stocks:
        if "_id" in s:
            s["_id"] = str(s["_id"])
    return _ok(stocks)


def _serialize_policy(policy: UniversePolicy) -> dict[str, Any]:
    return {
        "policy_version": policy.policy_version,
        "locked_decision": policy.locked_decision,
        "last_updated": policy.last_updated,
        "fast": {
            "cron": policy.fast.cron,
            "pipeline": policy.fast.pipeline,
            "max_debate_rounds": policy.fast.max_debate_rounds,
            "pipeline_timeout_seconds": policy.fast.pipeline_timeout_seconds,
            "default_codes": list(policy.fast.default_codes),
        },
        "slow": {
            "cron": policy.slow.cron,
            "pipeline": policy.slow.pipeline,
            "max_debate_rounds": policy.slow.max_debate_rounds,
            "pipeline_timeout_seconds": policy.slow.pipeline_timeout_seconds,
            "default_codes": list(policy.slow.default_codes),
        },
        "universe": {
            "board_whitelist": sorted(policy.universe.board_whitelist),
            "forbidden_boards": sorted(policy.universe.forbidden_boards),
        },
        "exclusion_rules": {
            "ipo_min_trading_days": policy.exclusion_rules.ipo_min_trading_days,
            "sub_new_min_trading_days": (
                policy.exclusion_rules.sub_new_min_trading_days
            ),
            "min_avg_amount_20d_yuan": (
                policy.exclusion_rules.min_avg_amount_20d_yuan
            ),
            "max_unit_price_yuan": policy.exclusion_rules.max_unit_price_yuan,
        },
        "cap_allocation": {
            "total_daily_cap": policy.cap_allocation.total_daily_cap,
            "traditional_path_default_cap": (
                policy.cap_allocation.traditional_path_default_cap
            ),
            "event_path_reserved_cap": (
                policy.cap_allocation.event_path_reserved_cap
            ),
            "reserved_cap_release_time": (
                policy.cap_allocation.reserved_cap_release_time
            ),
        },
        "direction_policy": {
            "long_only": policy.direction_policy.long_only,
            "forbidden_sides": sorted(policy.direction_policy.forbidden_sides),
            "etf_arbitrage_enabled": (
                policy.direction_policy.etf_arbitrage_enabled
            ),
        },
        "overrides": dict(policy.overrides),
        "default_category": policy.default_category,
    }


def _get_policy(request: Request) -> UniversePolicy:
    """Read the active UniversePolicy from app state."""
    policy = getattr(request.app.state, "watchlist_policy", None)
    if policy is None:
        _err("Watchlist policy not loaded", 503)
    return policy


@router.get("/api/watchlist/policy")
async def get_watchlist_policy(request: Request) -> dict[str, Any]:
    """Return the active P0-9 policy + per-code resolution.

    The ``assignments`` map is computed live so callers can see the
    effective bucket for each watchlist stock without re-implementing
    :func:`assign_category`.
    """
    policy = _get_policy(request)
    watchlist = _get_watchlist(request)
    stocks = await watchlist.list_stocks()
    assignments = {
        s["stock_code"]: assign_category(s["stock_code"], policy)
        for s in stocks
    }
    return _ok({
        "policy": _serialize_policy(policy),
        "assignments": assignments,
    })
