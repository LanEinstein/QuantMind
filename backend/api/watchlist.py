"""FastAPI routes for watchlist management."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.services.watchlist_policy import (
    WatchlistPolicy,
    WatchlistPolicyError,
    assign_category,
    save_policy,
    update_override,
)

log = structlog.get_logger(component="api_watchlist")

router = APIRouter(tags=["watchlist"])

_CODE_RE = re.compile(r"^\d{6}$")
# Persisted policy lives next to the rest of the YAML configs; the
# tests override this via QUANTMIND_WATCHLIST_POLICY_PATH so the API
# never writes into the repo's version-controlled file under pytest.
_DEFAULT_POLICY_PATH = "config/watchlist_policy.yaml"


class AddStockRequest(BaseModel):
    """Request body for adding a stock to the watchlist."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str


_VALID_CATEGORIES = {"fast", "slow"}


class SetCategoryRequest(BaseModel):
    """Request body for ``POST /api/watchlist/{code}/category``.

    Send ``{"category": "fast"}`` or ``{"category": "slow"}`` to pin
    a code to a bucket; send ``{"category": null}`` (JSON null, NOT
    an omitted key) to remove the override and let the code fall back
    to ``watchlist_policy.yaml`` defaults.

    Validation lives in the handler (not Pydantic ``Literal``) so the
    error response stays in the project's ``status/data/error``
    envelope instead of FastAPI's default 422 ``detail[]`` shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Required (not Optional with default) so an empty `{}` body
    # cannot silently clear an override — the operator has to send
    # an explicit `null` (Codex R6 MEDIUM #4).
    category: str | None = Field(
        ...,
        description=(
            "Bucket override. 'fast' or 'slow' to pin; null clears "
            "the override and lets the code fall back to the YAML "
            "default. Field is required: send explicit null to clear."
        ),
        examples=["fast", "slow", None],
    )


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


def _get_analysis_scheduler(request: Request) -> Any:
    """Extract AnalysisScheduler from app state."""
    svc = getattr(request.app.state, "analysis_scheduler", None)
    if not svc:
        _err("Analysis scheduler not initialized", 503)
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


@router.post("/api/watchlist")
async def add_to_watchlist(
    request: Request, body: AddStockRequest
) -> dict[str, Any]:
    """Add a stock to the watchlist."""
    if not _CODE_RE.match(body.code):
        _err(f"Invalid stock code '{body.code}': must be 6 digits", 422)
    watchlist = _get_watchlist(request)
    await watchlist.add_stock(body.code, body.name)
    return _ok({"code": body.code, "name": body.name})


@router.delete("/api/watchlist/{code}")
async def remove_from_watchlist(
    request: Request, code: str
) -> dict[str, Any]:
    """Remove a stock from the watchlist (soft delete)."""
    if not _CODE_RE.match(code):
        _err(f"Invalid stock code '{code}': must be 6 digits", 422)
    watchlist = _get_watchlist(request)
    await watchlist.remove_stock(code)
    return _ok({"code": code, "removed": True})


@router.post("/api/watchlist/analyze-now")
async def trigger_analysis_now(request: Request) -> dict[str, Any]:
    """Manually trigger analysis for all watchlist stocks."""
    scheduler = _get_analysis_scheduler(request)
    signals = await scheduler.run_daily_analysis()
    return _ok({
        "count": len(signals),
        "signals": [s.model_dump(mode="json") for s in signals],
    })


@router.post("/api/watchlist/analyze/{code}")
async def trigger_single_analysis(
    request: Request, code: str
) -> dict[str, Any]:
    """Manually trigger analysis for a single stock."""
    if not _CODE_RE.match(code):
        _err(f"Invalid stock code '{code}': must be 6 digits", 422)
    scheduler = _get_analysis_scheduler(request)
    signal = await scheduler.run_single_analysis(code)
    if signal is None:
        _err(f"Analysis failed for {code}", 500)
        return _ok(None)  # unreachable
    return _ok(signal.model_dump(mode="json"))


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


@router.post("/api/watchlist/{code}/category")
async def set_watchlist_category(
    request: Request, code: str, body: SetCategoryRequest
) -> dict[str, Any]:
    """Pin ``code`` to ``fast`` / ``slow`` (or clear the override).

    Body shape: ``{"category": "fast" | "slow" | null}``. ``null``
    removes any existing override so the code falls back to the
    bucket-default rules in ``watchlist_policy.yaml``.

    The new policy is persisted to disk so a process restart picks it
    up, and the in-memory copy on both ``app.state`` and the running
    scheduler is swapped immediately so the next cron tick reflects
    the change without needing to reload the file. Note: persistence
    re-emits the YAML canonically — comments in the source file are
    NOT preserved (PyYAML limitation; reserved as a future backlog
    item if operators need round-trip-safe edits).
    """
    if not _CODE_RE.match(code):
        _err(f"Invalid stock code '{code}': must be 6 digits", 422)

    if body.category is not None and body.category not in _VALID_CATEGORIES:
        _err(
            f"category must be one of 'fast', 'slow', or null to clear; "
            f"got {body.category!r}",
            422,
        )

    # Reject overrides for codes that aren't on the active watchlist.
    # Otherwise an operator with a typo would silently configure a
    # bucket for a stock that never runs — exactly the kind of quiet
    # misconfiguration that bites at p95 review time.
    watchlist = _get_watchlist(request)
    stocks = await watchlist.list_stocks()
    active_codes = {s["stock_code"] for s in stocks}
    if code not in active_codes:
        _err(
            f"Stock '{code}' is not in the active watchlist; add it via "
            f"POST /api/watchlist first",
            404,
        )

    policy = _get_policy(request)
    try:
        new_policy = update_override(policy, code, body.category)
    except WatchlistPolicyError as exc:
        _err(str(exc), 422)
        return _ok(None)  # unreachable

    policy_path = os.environ.get(
        "QUANTMIND_WATCHLIST_POLICY_PATH", _DEFAULT_POLICY_PATH
    )
    try:
        # YAML dump + atomic replace are blocking I/O; off-load so the
        # async event loop is not stuck on a slow disk / NFS write.
        await asyncio.to_thread(save_policy, new_policy, policy_path)
    except OSError as exc:
        # Path goes to server log (operator visibility); the client
        # response stays generic so a misconfigured deployment does
        # not leak internal directory layout.
        log.error(
            "watchlist_policy_save_failed",
            code=code,
            path=policy_path,
            error=str(exc),
        )
        _err("Failed to persist watchlist policy", 500)

    request.app.state.watchlist_policy = new_policy
    scheduler = getattr(request.app.state, "analysis_scheduler", None)
    if scheduler is not None and hasattr(scheduler, "update_policy"):
        scheduler.update_policy(new_policy)

    log.info(
        "watchlist_category_updated",
        code=code,
        category=body.category,
    )
    return _ok({
        "code": code,
        "category": assign_category(code, new_policy),
        "override": body.category,
    })
