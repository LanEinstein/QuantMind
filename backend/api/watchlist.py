"""FastAPI routes for watchlist management (GET-only per P0-9 / P1-5).

P0-9 locks the watchlist universe at 13 codes (4 SH main + 3 SZ main +
3 ChiNext + 3 mandatory ETFs 510300/510500/159949) and forbids runtime
mutation. Phase A redline: every POST/PUT/PATCH/DELETE handler in this
module has been destructively deleted so the universe is immutable
between deploys.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from backend.services.watchlist_policy import (
    WatchlistPolicy,
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


def _serialize_policy(policy: WatchlistPolicy) -> dict[str, Any]:
    return {
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
        "overrides": dict(policy.overrides),
        "default_category": policy.default_category,
        "policy_version": policy.policy_version,
        "last_updated": policy.last_updated,
    }


def _get_policy(request: Request) -> WatchlistPolicy:
    """Read the active WatchlistPolicy from app state."""
    policy = getattr(request.app.state, "watchlist_policy", None)
    if policy is None:
        _err("Watchlist policy not loaded", 503)
    return policy


@router.get("/api/watchlist/policy")
async def get_watchlist_policy(request: Request) -> dict[str, Any]:
    """Return the active Fast/Slow policy + per-code resolution.

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
