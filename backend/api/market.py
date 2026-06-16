"""FastAPI routes for market data and news endpoints."""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from backend.data.market_data import DataFetchError

log = structlog.get_logger(component="api_market")

router = APIRouter()

_CODE_RE = re.compile(r"^\d{6}$")
_VALID_PERIODS = {"daily", "weekly", "monthly"}
_VALID_ADJUSTS = {"qfq", "hfq", "none"}


def _ok(data: Any) -> dict[str, Any]:
    """Wrap data in a success response envelope."""
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    """Raise an HTTPException with error envelope."""
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


def _validate_stock_code(code: str) -> None:
    """Validate stock code is 6 digits."""
    if not _CODE_RE.match(code):
        _err(f"Invalid stock code '{code}': must be 6 digits", 422)


# -- Market endpoints --


@router.get("/api/market/indices")
async def get_indices(request: Request) -> dict[str, Any]:
    """Get real-time quotes for major indices."""
    try:
        service = request.app.state.market_data
        quotes = await service.get_index_realtime()
        return _ok([q.model_dump(mode="json") for q in quotes])
    except DataFetchError as exc:
        _err(str(exc))
    return _ok([])  # unreachable, satisfies type checker


@router.get("/api/market/stock/{code}")
async def get_stock(request: Request, code: str) -> dict[str, Any]:
    """Get real-time quote for a single stock."""
    _validate_stock_code(code)
    try:
        service = request.app.state.market_data
        quote = await service.get_stock_realtime(code)
        return _ok(quote.model_dump(mode="json"))
    except DataFetchError as exc:
        _err(str(exc))
    return _ok(None)


@router.get("/api/market/sectors")
async def get_sectors(request: Request) -> dict[str, Any]:
    """Get sector performance overview.

    Sector data is a best-effort akshare scrape; an upstream fetch failure is
    an infra glitch (not data corruption), so we fail OPEN with an empty list
    instead of a 500 that crashes the dashboard panel (CLAUDE.md §3:
    fail-open for infra glitches, fail-closed for corruption).
    """
    try:
        service = request.app.state.market_data
        sectors = await service.get_sector_overview()
        return _ok([s.model_dump(mode="json") for s in sectors])
    except DataFetchError as exc:
        log.warning("sectors_unavailable", error=str(exc))
        return _ok([])


@router.get("/api/market/capital-flow")
async def get_capital_flow(request: Request) -> dict[str, Any]:
    """Get northbound and main capital flow."""
    try:
        service = request.app.state.market_data
        flow = await service.get_capital_flow()
        return _ok(flow.model_dump(mode="json"))
    except DataFetchError as exc:
        _err(str(exc))
    return _ok(None)


@router.get("/api/market/kline/{code}")
async def get_kline(
    request: Request,
    code: str,
    period: str = Query(default="daily"),
    start: str = Query(default=""),
    end: str = Query(default=""),
    adjust: str = Query(default="qfq"),
) -> dict[str, Any]:
    """Get historical K-line data."""
    _validate_stock_code(code)
    if period not in _VALID_PERIODS:
        _err(
            f"Invalid period '{period}'. Must be one of {_VALID_PERIODS}",
            422,
        )
    if adjust not in _VALID_ADJUSTS:
        _err(
            f"Invalid adjust '{adjust}'. Must be one of {_VALID_ADJUSTS}",
            422,
        )
    try:
        service = request.app.state.history_data
        df = await service.get_kline(
            code, period=period, start_date=start, end_date=end, adjust=adjust
        )
        return _ok(df.to_dict(orient="records"))
    except (DataFetchError, ValueError) as exc:
        _err(str(exc))
    return _ok([])


@router.get("/api/market/financial/{code}")
async def get_financial(request: Request, code: str) -> dict[str, Any]:
    """Get financial indicators for a stock."""
    _validate_stock_code(code)
    try:
        service = request.app.state.history_data
        data = await service.get_financial_data(code)
        return _ok(data.model_dump(mode="json"))
    except DataFetchError as exc:
        _err(str(exc))
    return _ok(None)


# -- News endpoints --


@router.get("/api/news/latest")
async def get_latest_news(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Get latest financial news."""
    try:
        service = request.app.state.mongodb
        articles = await service.query_news(limit=limit)
        return _ok(articles)
    except Exception as exc:
        log.error("news_query_failed", error=str(exc))
        _err(str(exc))
    return _ok([])


@router.get("/api/news/stock/{code}")
async def get_stock_news(
    request: Request,
    code: str,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Get news related to a specific stock."""
    _validate_stock_code(code)
    try:
        service = request.app.state.mongodb
        articles = await service.query_news(limit=limit, stock_code=code)
        return _ok(articles)
    except Exception as exc:
        log.error("stock_news_query_failed", error=str(exc))
        _err(str(exc))
    return _ok([])
