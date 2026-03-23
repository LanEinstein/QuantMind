"""FastAPI routes for multi-agent stock analysis."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.agents.graph import run_analysis
from backend.agents.models import AnalysisServices, PipelineConfig

log = structlog.get_logger(component="api_analysis")

router = APIRouter()

_CODE_RE = re.compile(r"^\d{6}$")


class AnalysisRequest(BaseModel):
    """Request body for stock analysis endpoint."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    max_debate_rounds: int = Field(default=2, ge=1, le=5)


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


@router.post("/api/analysis/stock")
async def analyze_stock(request: Request, body: AnalysisRequest) -> dict[str, Any]:
    """Run the full multi-agent analysis pipeline for a stock.

    Triggers 9 LLM agents: 5 analysts, 2 debaters, risk officer, fund manager.
    Returns a TradingSignal with action/target_price/confidence/risk_score.
    """
    if not _CODE_RE.match(body.stock_code):
        _err(f"Invalid stock code '{body.stock_code}': must be 6 digits", 422)

    try:
        services = AnalysisServices(
            llm_router=request.app.state.llm_router,
            market_data=request.app.state.market_data,
            history_data=request.app.state.history_data,
            news_crawler=request.app.state.news_crawler,
            pipeline_config=PipelineConfig(
                max_debate_rounds=body.max_debate_rounds
            ),
        )
    except AttributeError as exc:
        log.error("services_not_initialized", error=str(exc))
        _err("Analysis services not initialized", 503)
        return _ok(None)  # unreachable

    timeout = services.pipeline_config.analysis_timeout_seconds
    try:
        signal = await asyncio.wait_for(
            run_analysis(body.stock_code, services),
            timeout=timeout,
        )
        return _ok(signal.model_dump(mode="json"))
    except TimeoutError:
        _err(f"Analysis timed out after {timeout}s", 504)
    except Exception as exc:
        log.error("analysis_failed", error=str(exc))
        _err(f"Analysis failed: {exc}", 500)
    return _ok(None)  # unreachable
