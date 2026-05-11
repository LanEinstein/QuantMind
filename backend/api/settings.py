"""FastAPI routes for system settings (GET-only per P1-5 §2).

Phase A redline: all POST/PUT/PATCH/DELETE handlers under
``backend/api/settings*.py`` have been destructively deleted. The
runtime is no longer permitted to mutate ``config/agent_models.yaml``,
``config/mirofish.yaml``, or ``config/data_sources.yaml`` over HTTP;
config changes must go through ``git diff`` + amendment + restart per
P0-7 / P0-10 (hot-reload disabled).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

log = structlog.get_logger(component="api_settings")

router = APIRouter()

_LLM_CONFIG_PATH = Path("config/agent_models.yaml")
_MIROFISH_CONFIG_PATH = Path("config/mirofish.yaml")
_DATA_SOURCES_CONFIG_PATH = Path("config/data_sources.yaml")

_VALID_PERIODS = {"daily", "weekly"}


def _ok(data: Any) -> dict[str, Any]:
    """Wrap data in a success response envelope."""
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    """Raise an HTTPException with error envelope."""
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


@router.get("/api/settings/llm-config")
async def get_llm_config(request: Request) -> dict[str, Any]:
    """Read LLM router configuration with API keys masked."""
    try:
        config_service = request.app.state.config_service
        data = await config_service.read_llm_config(_LLM_CONFIG_PATH)
        return _ok(data)
    except FileNotFoundError:
        _err("LLM config file not found", 404)
    except Exception as exc:
        log.error("llm_config_read_failed", error=str(exc))
        _err(f"Failed to read LLM config: {exc}")
    return _ok(None)


@router.get("/api/settings/data-sources")
async def get_data_sources(request: Request) -> dict[str, Any]:
    """Return connectivity status for all data sources (read-only)."""
    try:
        config_service = request.app.state.config_service
        config = await config_service.read_yaml(_DATA_SOURCES_CONFIG_PATH)

        sources = _build_data_source_list(config)

        if hasattr(request.app.state, "mongodb"):
            try:
                mongo_client = request.app.state.mongo_client
                await mongo_client.admin.command("ping")
                sources.append({
                    "name": "MongoDB",
                    "type": "database",
                    "status": "connected",
                    "latency_ms": 0,
                    "error": None,
                })
            except Exception as exc:
                sources.append({
                    "name": "MongoDB",
                    "type": "database",
                    "status": "error",
                    "latency_ms": 0,
                    "error": str(exc),
                })

        if hasattr(request.app.state, "redis"):
            try:
                await request.app.state.redis.ping()
                sources.append({
                    "name": "Redis",
                    "type": "cache",
                    "status": "connected",
                    "latency_ms": 0,
                    "error": None,
                })
            except Exception as exc:
                sources.append({
                    "name": "Redis",
                    "type": "cache",
                    "status": "error",
                    "latency_ms": 0,
                    "error": str(exc),
                })

        return _ok(sources)
    except FileNotFoundError:
        _err("Data sources config file not found", 404)
    except Exception as exc:
        log.error("data_sources_read_failed", error=str(exc))
        _err(f"Failed to read data sources: {exc}")
    return _ok([])


@router.get("/api/settings/mirofish")
async def get_mirofish_config(request: Request) -> dict[str, Any]:
    """Read MiroFish simulation configuration (read-only)."""
    try:
        config_service = request.app.state.config_service
        data = await config_service.read_yaml(_MIROFISH_CONFIG_PATH)
        return _ok(data)
    except FileNotFoundError:
        _err("MiroFish config file not found", 404)
    except Exception as exc:
        log.error("mirofish_config_read_failed", error=str(exc))
        _err(f"Failed to read MiroFish config: {exc}")
    return _ok(None)


@router.get("/api/settings/cost-stats")
async def get_cost_stats(
    request: Request,
    period: str = Query(default="daily"),
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Get LLM cost statistics."""
    if period not in _VALID_PERIODS:
        _err(
            f"Invalid period '{period}'. Valid: {sorted(_VALID_PERIODS)}",
            422,
        )

    try:
        from backend.llm.cost_tracker import aggregate_costs

        redis_client = request.app.state.redis
        summary = await aggregate_costs(redis_client, days=days, period=period)
        return _ok(asdict(summary))
    except Exception as exc:
        log.error("cost_stats_failed", error=str(exc))
        _err(f"Failed to get cost stats: {exc}")
    return _ok(None)


def _build_data_source_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build data source status list from config."""
    sources: list[dict[str, Any]] = []

    market = config.get("market_data", {})
    if market:
        sources.append({
            "name": "adata",
            "type": "market_data",
            "status": "configured",
            "latency_ms": 0,
            "error": None,
            "role": "primary" if market.get("primary") == "adata" else "fallback",
        })
        sources.append({
            "name": "AKShare",
            "type": "market_data",
            "status": "configured",
            "latency_ms": 0,
            "error": None,
            "role": "fallback" if market.get("primary") == "adata" else "primary",
        })

    history = config.get("history_data", {})
    if history:
        sources.append({
            "name": "BaoStock",
            "type": "history_data",
            "status": "configured",
            "latency_ms": 0,
            "error": None,
            "role": "fallback" if history.get("primary") == "adata" else "primary",
        })

    sources.append({
        "name": "新闻爬虫",
        "type": "news",
        "status": "configured",
        "latency_ms": 0,
        "error": None,
    })

    return sources
