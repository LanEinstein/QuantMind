"""FastAPI routes for watchlist management."""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger(component="api_watchlist")

router = APIRouter(tags=["watchlist"])

_CODE_RE = re.compile(r"^\d{6}$")


class AddStockRequest(BaseModel):
    """Request body for adding a stock to the watchlist."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str


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
