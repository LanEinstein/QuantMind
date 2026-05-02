"""FastAPI routes for system settings management."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = structlog.get_logger(component="api_settings")

router = APIRouter()

# -- Config file paths --

_LLM_CONFIG_PATH = Path("config/agent_models.yaml")
_MIROFISH_CONFIG_PATH = Path("config/mirofish.yaml")
_DATA_SOURCES_CONFIG_PATH = Path("config/data_sources.yaml")

_VALID_PROVIDERS = {"deepseek", "qwen", "kimi", "claude", "openai"}
_VALID_PERIODS = {"daily", "weekly"}


# -- Response helpers --


def _ok(data: Any) -> dict[str, Any]:
    """Wrap data in a success response envelope."""
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    """Raise an HTTPException with error envelope."""
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


# -- Request models --


class FallbackInput(BaseModel):
    """Fallback provider input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str


class ThinkingConfigInput(BaseModel):
    """Per-agent thinking-mode update input.

    Mirrors backend.llm.providers.ThinkingConfig. Bounds are enforced
    on the wire so an obviously bad payload does not silently land in
    the YAML.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["enabled", "disabled"] | None = None
    max_tokens: int | None = Field(default=None, ge=0, le=32_000)
    keep: Literal["all", "last_round", "none"] | None = None


class RoutingConfigInput(BaseModel):
    """Triage→escalation routing update input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    triage_provider: str | None = None
    triage_model: str | None = None
    escalation_provider: str | None = None
    escalation_model: str | None = None
    escalation_condition: dict[str, Any] | None = None


class AgentConfigInput(BaseModel):
    """Agent configuration update input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    provider: str | None = None
    model: str | None = None
    fallback: FallbackInput | None = None
    routing: RoutingConfigInput | None = None
    thinking: ThinkingConfigInput | None = None
    frequency: str | None = None
    task: str | None = None


class ProviderConfigInput(BaseModel):
    """Provider configuration update input."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None


class DefaultsInput(BaseModel):
    """Default LLM parameters input."""

    model_config = ConfigDict(frozen=True)

    temperature: float | None = None
    max_tokens: int | None = None


class LLMConfigUpdate(BaseModel):
    """Request body for POST /api/settings/llm-config."""

    model_config = ConfigDict(frozen=True)

    providers: dict[str, ProviderConfigInput] | None = None
    agents: dict[str, AgentConfigInput] | None = None
    defaults: DefaultsInput | None = None


class ProviderTestRequest(BaseModel):
    """Request body for POST /api/settings/llm-config/test."""

    model_config = ConfigDict(frozen=True)

    provider: str


class DataSourceTestRequest(BaseModel):
    """Request body for POST /api/settings/data-sources/test."""

    model_config = ConfigDict(frozen=True)

    source: str


class MiroFishConfigUpdate(BaseModel):
    """Request body for POST /api/settings/mirofish."""

    model_config = ConfigDict(frozen=True)

    agent_count: int | None = Field(default=None, ge=100, le=1000)
    rounds: int | None = Field(default=None, ge=5, le=50)
    trigger_threshold: int | None = Field(default=None, ge=1, le=10)
    model: str | None = None
    enabled: bool | None = None


# -- LLM Config endpoints --


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
    return _ok(None)  # unreachable


@router.post("/api/settings/llm-config")
async def update_llm_config(
    request: Request, body: LLMConfigUpdate
) -> dict[str, Any]:
    """Update LLM router configuration."""
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        _err("No fields to update", 422)

    # Validate provider names if provided
    if "providers" in update_data:
        for name in update_data["providers"]:
            if name not in _VALID_PROVIDERS:
                _err(
                    f"Invalid provider '{name}'. "
                    f"Valid: {sorted(_VALID_PROVIDERS)}",
                    422,
                )

    try:
        config_service = request.app.state.config_service
        result = await config_service.write_llm_config(
            _LLM_CONFIG_PATH, update_data
        )
        return _ok(result)
    except ValidationError as exc:
        # Merged YAML failed RouterConfig validation — file untouched.
        log.warning("llm_config_invalid_merge", errors=exc.errors())
        _err(f"Invalid LLM config after merge: {exc}", 422)
    except Exception as exc:
        log.error("llm_config_write_failed", error=str(exc))
        _err(f"Failed to update LLM config: {exc}")
    return _ok(None)


@router.post("/api/settings/llm-config/test")
async def test_llm_provider_endpoint(
    request: Request, body: ProviderTestRequest
) -> dict[str, Any]:
    """Test connection to a specific LLM provider."""
    from backend.llm.connection_tester import test_llm_provider

    provider_name = body.provider
    if provider_name not in _VALID_PROVIDERS:
        _err(
            f"Invalid provider '{provider_name}'. "
            f"Valid: {sorted(_VALID_PROVIDERS)}",
            422,
        )

    try:
        config_service = request.app.state.config_service
        raw_config = await config_service.read_yaml(_LLM_CONFIG_PATH)
        providers = raw_config.get("providers", {})

        if provider_name not in providers:
            _err(f"Provider '{provider_name}' not configured", 404)

        from backend.llm.providers import ProviderConfig

        provider_cfg = ProviderConfig(**providers[provider_name])
        result = await test_llm_provider(provider_name, provider_cfg)
        return _ok(asdict(result))
    except HTTPException:
        raise
    except Exception as exc:
        log.error("provider_test_failed", error=str(exc))
        _err(f"Provider test failed: {exc}")
    return _ok(None)


# -- Data Source endpoints --


@router.get("/api/settings/data-sources")
async def get_data_sources(request: Request) -> dict[str, Any]:
    """Get connectivity status for all data sources."""
    try:
        config_service = request.app.state.config_service
        config = await config_service.read_yaml(_DATA_SOURCES_CONFIG_PATH)

        sources = _build_data_source_list(config)

        # Check MongoDB connectivity
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

        # Check Redis connectivity
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


@router.post("/api/settings/data-sources/test")
async def test_data_source(
    request: Request, body: DataSourceTestRequest
) -> dict[str, Any]:
    """Test a specific data source connectivity."""
    source = body.source.lower()

    if source == "mongodb":
        try:
            mongo_client = request.app.state.mongo_client
            await mongo_client.admin.command("ping")
            return _ok({
                "name": "MongoDB",
                "type": "database",
                "status": "connected",
                "latency_ms": 0,
                "error": None,
            })
        except Exception as exc:
            return _ok({
                "name": "MongoDB",
                "type": "database",
                "status": "error",
                "latency_ms": 0,
                "error": str(exc),
            })

    if source == "redis":
        try:
            await request.app.state.redis.ping()
            return _ok({
                "name": "Redis",
                "type": "cache",
                "status": "connected",
                "latency_ms": 0,
                "error": None,
            })
        except Exception as exc:
            return _ok({
                "name": "Redis",
                "type": "cache",
                "status": "error",
                "latency_ms": 0,
                "error": str(exc),
            })

    # For market data sources, return config info
    return _ok({
        "name": source,
        "type": "market_data",
        "status": "unknown",
        "latency_ms": 0,
        "error": None,
    })


# -- MiroFish Config endpoints --


@router.get("/api/settings/mirofish")
async def get_mirofish_config(request: Request) -> dict[str, Any]:
    """Read MiroFish simulation configuration."""
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


@router.post("/api/settings/mirofish")
async def update_mirofish_config(
    request: Request, body: MiroFishConfigUpdate
) -> dict[str, Any]:
    """Update MiroFish simulation configuration."""
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        _err("No fields to update", 422)

    try:
        config_service = request.app.state.config_service
        # Nest under 'simulation' key to match YAML structure
        await config_service.write_yaml(
            _MIROFISH_CONFIG_PATH,
            {"simulation": update_data},
            config_name="mirofish",
        )
        result = await config_service.read_yaml(_MIROFISH_CONFIG_PATH)
        return _ok(result)
    except Exception as exc:
        log.error("mirofish_config_write_failed", error=str(exc))
        _err(f"Failed to update MiroFish config: {exc}")
    return _ok(None)


# -- Cost Stats endpoint --


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


# -- Helpers --


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
