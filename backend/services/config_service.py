"""Read-only YAML config service (P0-7 / P0-10 / P1-2.C / P1-7).

Phase A redline: hot-reload is disabled and runtime mutation of
``config/{risk,broker,agent_models,universe_policy,data_sources,mirofish}.yaml``
is forbidden. The legacy ``write_yaml`` / ``write_llm_config`` /
``_notify_config_change`` paths were destructively removed here; config
changes must go through ``git diff`` + amendment + process restart.

This service is still useful for reading YAML configs into request
handlers (with comment preservation via ruamel.yaml) and for masking
``api_key`` fields before sending them to the frontend.
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from ruamel.yaml import YAML

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="config_service")

_MASKED = "***masked***"


class ConfigService:
    """Read-only YAML config loader with api_key masking."""

    def __init__(self, redis_client: redis.asyncio.Redis | None = None) -> None:
        """Initialize the service.

        The ``redis_client`` parameter is retained for API compatibility
        but is intentionally unused: there is no pub/sub channel because
        hot-reload is disabled.
        """
        self._redis = redis_client
        self._yaml = YAML()
        self._yaml.preserve_quotes = True

    async def read_yaml(self, path: Path) -> dict[str, Any]:
        """Read a YAML file and return its contents as a dict.

        Uses ``asyncio.to_thread`` to avoid blocking the event loop.
        """
        return await asyncio.to_thread(self._read_yaml_sync, path)

    async def read_llm_config(self, path: Path) -> dict[str, Any]:
        """Read LLM config with API keys masked for frontend display."""
        raw = await self.read_yaml(path)
        return _mask_api_keys(raw)

    def _read_yaml_sync(self, path: Path) -> dict[str, Any]:
        """Synchronous YAML read (runs in thread)."""
        with path.open("r", encoding="utf-8") as f:
            result = self._yaml.load(f)
        return dict(result) if result else {}


def _mask_api_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with all 'api_key' fields replaced by mask."""
    result = copy.deepcopy(data)
    _mask_recursive(result)
    return result


def _mask_recursive(obj: Any) -> None:
    """Recursively replace api_key values with mask."""
    if isinstance(obj, dict):
        for key in obj:
            if key == "api_key" and isinstance(obj[key], str):
                obj[key] = _MASKED
            else:
                _mask_recursive(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _mask_recursive(item)
