"""Core LLM router for multi-provider request routing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from openai import AsyncOpenAI

from backend.llm.fallback import (
    RETRYABLE_EXCEPTIONS,
    track_fallback,
    track_usage,
)
from backend.llm.providers import (
    RouterConfig,
    create_openai_client,
    load_router_config,
)

if TYPE_CHECKING:
    import redis.asyncio
    from openai.types.chat import ChatCompletion


class LLMRouter:
    """Routes agent LLM requests to the appropriate provider.

    Manages AsyncOpenAI client instances, config hot-reload,
    and automatic fallback on provider failure.

    Usage::

        router = LLMRouter(config_path="config/agent_models.yaml")
        await router.initialize(redis_client=redis_pool)
        response = await router.complete("news_crawler", messages=[...])
        await router.close()
    """

    def __init__(self, config_path: str | Path) -> None:
        """Initialize the router with the path to agent_models.yaml.

        Does NOT load config or create clients — call initialize() first.
        """
        self._config_path = Path(config_path)
        self._config: RouterConfig | None = None
        self._config_mtime: float = 0.0
        self._clients: dict[str, AsyncOpenAI] = {}
        self._redis: redis.asyncio.Redis | None = None
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(component="llm_router")

    async def initialize(
        self,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> None:
        """Load config, create clients, store Redis reference.

        Must be called before complete(). Typically called in
        FastAPI lifespan.
        """
        self._redis = redis_client
        await self._reload_config()

    async def close(self) -> None:
        """Close all AsyncOpenAI clients. Call in FastAPI shutdown."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    @property
    def config(self) -> RouterConfig:
        """Return the current (immutable) router configuration."""
        if self._config is None:
            raise RuntimeError(
                "Router not initialized. Call initialize() first."
            )
        return self._config

    def preflight(self) -> dict[str, bool]:
        """Snapshot which providers currently hold a resolvable API key.

        Inspects the config's ``api_key`` entry for each provider. A
        literal key is always present; a ``${ENV}`` reference is present
        only when the environment variable is non-empty at call time.

        Returns a mapping ``{provider_name: True/False}``. Does not make
        any network calls — callers use this for a fast 503 cascade
        decision before booting the pipeline.
        """
        import os
        import re

        env_pattern = re.compile(r"^\$\{(\w+)\}$")
        if self._config is None:
            return {}
        status: dict[str, bool] = {}
        for name, provider_cfg in self._config.providers.items():
            raw = provider_cfg.api_key
            m = env_pattern.match(raw)
            if m is None:
                # Literal key in config — assume valid.
                status[name] = bool(raw)
            else:
                status[name] = bool(os.environ.get(m.group(1)))
        return status

    async def complete(
        self,
        agent_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ChatCompletion:
        """Route a chat completion request for the given agent.

        1. Check for config hot-reload
        2. Resolve agent -> provider -> client
        3. Call chat.completions.create
        4. On retryable failure, try fallback provider
        5. Track token usage in Redis

        Args:
            agent_name: Key from agents section of YAML.
            messages: OpenAI-format message list.
            **kwargs: Override temperature, max_tokens, etc.

        Returns:
            ChatCompletion response from the provider.

        Raises:
            KeyError: If agent_name is not in config.
            openai.APIError: If both primary and fallback fail.
        """
        await self._maybe_reload_config()

        config = self.config

        if agent_name not in config.agents:
            raise KeyError(
                f"Unknown agent '{agent_name}'. "
                f"Available: {sorted(config.agents.keys())}"
            )

        agent_cfg = config.agents[agent_name]

        call_kwargs: dict[str, Any] = {
            "temperature": kwargs.pop("temperature", config.defaults.temperature),
            "max_tokens": kwargs.pop("max_tokens", config.defaults.max_tokens),
            **kwargs,
        }

        # Try primary provider
        try:
            return await self._call_provider(
                provider_name=agent_cfg.provider,
                model=agent_cfg.model,
                messages=messages,
                agent_name=agent_name,
                **call_kwargs,
            )
        except RETRYABLE_EXCEPTIONS as exc:
            self._log.warning(
                "primary_provider_failed",
                agent_name=agent_name,
                provider=agent_cfg.provider,
                model=agent_cfg.model,
                error=str(exc),
            )

            if agent_cfg.fallback is None:
                raise

            await track_fallback(
                self._redis,
                agent_name,
                agent_cfg.provider,
                agent_cfg.fallback.provider,
            )

            self._log.info(
                "trying_fallback_provider",
                agent_name=agent_name,
                fallback_provider=agent_cfg.fallback.provider,
                fallback_model=agent_cfg.fallback.model,
            )

            return await self._call_provider(
                provider_name=agent_cfg.fallback.provider,
                model=agent_cfg.fallback.model,
                messages=messages,
                agent_name=agent_name,
                **call_kwargs,
            )

    async def _call_provider(
        self,
        provider_name: str,
        model: str,
        messages: list[dict[str, str]],
        agent_name: str,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Execute a chat completion call against a specific provider."""
        client = self._get_client(provider_name)

        self._log.debug(
            "llm_call_start",
            agent_name=agent_name,
            provider=provider_name,
            model=model,
        )

        call_kwargs = self._normalize_provider_kwargs(
            provider_name, model, kwargs
        )

        response = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            **call_kwargs,
        )

        if response.usage:
            await track_usage(
                self._redis,
                agent_name,
                provider_name,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            self._log.info(
                "llm_call_complete",
                agent_name=agent_name,
                provider=provider_name,
                model=model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )

        return response

    @staticmethod
    def _normalize_provider_kwargs(
        provider_name: str,
        model: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply provider/model-specific Chat Completions constraints."""
        normalized = dict(kwargs)

        if provider_name == "kimi" and model == "kimi-k2.6":
            # Kimi K2.6 rejects arbitrary temperature values in its
            # default thinking mode; keep thinking enabled and use the
            # provider-supported default. The thinking response can be
            # large, so use the provider-recommended minimum cap to avoid
            # spending the whole output budget on reasoning_content.
            normalized["temperature"] = 1
            max_tokens = normalized.get("max_tokens")
            if not isinstance(max_tokens, int) or max_tokens < 16_000:
                normalized["max_tokens"] = 16_000

        return normalized

    def _get_client(self, provider_name: str) -> AsyncOpenAI:
        """Get a pre-initialized client for the given provider.

        Clients are eagerly created during config reload, so this is
        a pure read with no race condition.
        """
        if provider_name not in self._clients:
            raise KeyError(
                f"Unknown provider '{provider_name}'. "
                f"Available: {sorted(self._clients.keys())}"
            )
        return self._clients[provider_name]

    async def _maybe_reload_config(self) -> None:
        """Check file mtime and reload config if changed."""
        try:
            current_mtime = self._config_path.stat().st_mtime
        except OSError:
            return

        if current_mtime <= self._config_mtime:
            return

        async with self._lock:
            # Double-check inside lock
            try:
                current_mtime = self._config_path.stat().st_mtime
            except OSError:
                return
            if current_mtime <= self._config_mtime:
                return
            await self._reload_config()

    async def _reload_config(self) -> None:
        """Reload configuration from YAML file.

        Uses asyncio.to_thread for blocking file I/O to avoid
        stalling the event loop. Eagerly creates clients for all
        providers so the read path (_get_client) is pure lookup.
        """
        new_config = await asyncio.to_thread(
            load_router_config, self._config_path
        )
        old_config = self._config

        # Close clients for providers whose config changed or were removed
        if old_config is not None:
            for name in list(self._clients.keys()):
                if name not in new_config.providers:
                    await self._clients.pop(name).close()
                elif new_config.providers[name] != old_config.providers.get(name):
                    await self._clients.pop(name).close()

        # Eagerly create clients for all providers not already cached
        for name, provider_cfg in new_config.providers.items():
            if name not in self._clients:
                self._clients[name] = create_openai_client(provider_cfg)

        self._config = new_config
        self._config_mtime = self._config_path.stat().st_mtime

        self._log.info(
            "config_reloaded",
            providers=sorted(new_config.providers.keys()),
            agents=sorted(new_config.agents.keys()),
        )
