"""Thread-safe YAML config read/write with comment preservation."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from filelock import FileLock, Timeout
from ruamel.yaml import YAML

if TYPE_CHECKING:
    import redis.asyncio
    from ruamel.yaml.comments import CommentedMap

log = structlog.get_logger(component="config_service")

_LOCK_TIMEOUT = 5  # seconds
_MASKED = "***masked***"


class ConfigService:
    """Thread-safe YAML config read/write with comment preservation.

    Uses ruamel.yaml to preserve comments and formatting on writes.
    Uses filelock to prevent concurrent write corruption.
    Publishes Redis events on config changes for hot-reload.
    """

    def __init__(self, redis_client: redis.asyncio.Redis | None = None) -> None:
        """Initialize the service with an optional Redis client for pub/sub."""
        self._redis = redis_client
        self._yaml = YAML()
        self._yaml.preserve_quotes = True

    async def read_yaml(self, path: Path) -> dict[str, Any]:
        """Read a YAML file and return its contents as a dict.

        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        return await asyncio.to_thread(self._read_yaml_sync, path)

    async def write_yaml(
        self, path: Path, data: dict[str, Any], config_name: str = ""
    ) -> None:
        """Write data to a YAML file, preserving existing comments.

        Acquires a file lock, reads the existing file to get the CommentedMap
        with comments, merges the new data in-place, and writes back.
        Then publishes a Redis notification for hot-reload.

        Args:
            path: Path to the YAML file.
            data: New data to merge into the existing file.
            config_name: Name for the Redis pub/sub channel notification.
        """
        await asyncio.to_thread(self._write_yaml_sync, path, data)
        if config_name:
            await self._notify_config_change(config_name)

    async def read_llm_config(self, path: Path) -> dict[str, Any]:
        """Read LLM config with API keys masked for frontend display."""
        raw = await self.read_yaml(path)
        return _mask_api_keys(raw)

    async def write_llm_config(
        self, path: Path, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Write LLM config, skipping masked API key values.

        Validates the *merged* result against ``RouterConfig`` before
        committing the YAML so a partial update cannot leave the file
        in an unloadable state. Returns the updated config with keys
        re-masked.

        Raises:
            pydantic.ValidationError: if the merged config is invalid.
        """
        from backend.llm.providers import RouterConfig

        clean = _strip_masked_keys(data)
        existing = await self.read_yaml(path)
        merged = copy.deepcopy(existing)
        _deep_merge(merged, clean)
        # Raises ValidationError on bad routing/thinking shape, unknown
        # provider reference, or any other RouterConfig invariant.
        RouterConfig.model_validate(merged)
        await self.write_yaml(path, clean, config_name="llm")
        return await self.read_llm_config(path)

    def _read_yaml_sync(self, path: Path) -> dict[str, Any]:
        """Synchronous YAML read (runs in thread)."""
        with path.open("r", encoding="utf-8") as f:
            result = self._yaml.load(f)
        return dict(result) if result else {}

    def _write_yaml_sync(self, path: Path, data: dict[str, Any]) -> None:
        """Synchronous YAML write with file locking (runs in thread)."""
        lock_path = str(path) + ".lock"
        lock = FileLock(lock_path, timeout=_LOCK_TIMEOUT)

        try:
            with lock:
                # Read existing file to preserve comments
                existing: CommentedMap | None = None
                if path.exists():
                    with path.open("r", encoding="utf-8") as f:
                        existing = self._yaml.load(f)

                if existing is not None:
                    _deep_merge(existing, data)
                    merged = existing
                else:
                    merged = data

                with path.open("w", encoding="utf-8") as f:
                    self._yaml.dump(merged, f)

                log.info("config_written", path=str(path))
        except Timeout:
            log.error("config_write_lock_timeout", path=str(path))
            raise

    async def _notify_config_change(self, config_name: str) -> None:
        """Publish config change event to Redis pub/sub channel."""
        if self._redis is None:
            return
        channel = f"config:changed:{config_name}"
        try:
            await self._redis.publish(channel, config_name)
            log.info("config_change_published", channel=channel)
        except Exception as exc:
            log.warning(
                "config_change_publish_failed",
                channel=channel,
                error=str(exc),
            )


def _deep_merge(target: Any, source: dict[str, Any]) -> None:
    """Recursively merge source dict into target CommentedMap in-place.

    Preserves target's comments and structure. Only updates leaf values.
    """
    for key, value in source.items():
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(target[key], value)
        else:
            target[key] = value


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


def _strip_masked_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with masked api_key fields removed.

    This prevents overwriting real ${ENV_VAR} values with the mask string.
    """
    result = copy.deepcopy(data)
    _strip_masked_recursive(result)
    return result


def _strip_masked_recursive(obj: Any) -> None:
    """Recursively remove api_key fields that contain the mask value."""
    if isinstance(obj, dict):
        keys_to_remove: list[str] = []
        for key in obj:
            if key == "api_key" and obj[key] == _MASKED:
                keys_to_remove.append(key)
            else:
                _strip_masked_recursive(obj[key])
        for key in keys_to_remove:
            del obj[key]
    elif isinstance(obj, list):
        for item in obj:
            _strip_masked_recursive(item)
