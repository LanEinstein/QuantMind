"""LLM provider connection testing."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from openai import AsyncOpenAI

from backend.llm.providers import resolve_env_var

if TYPE_CHECKING:
    from backend.llm.providers import ProviderConfig

log = structlog.get_logger(component="connection_tester")


@dataclass(frozen=True)
class ConnectionTestResult:
    """Result of a provider connection test."""

    provider: str
    connected: bool
    latency_ms: float
    error: str | None = None


async def test_llm_provider(
    provider_name: str,
    provider_config: ProviderConfig,
    timeout: float = 10.0,
) -> ConnectionTestResult:
    """Send a minimal test prompt to a provider and measure latency.

    Creates a temporary AsyncOpenAI client, sends a single "Hello" message
    with max_tokens=5, and measures the round-trip time.

    Args:
        provider_name: Name of the provider (for logging and result).
        provider_config: Provider connection configuration.
        timeout: Maximum seconds to wait for a response.

    Returns:
        ConnectionTestResult with connected status and latency.
    """
    client: AsyncOpenAI | None = None
    try:
        api_key = resolve_env_var(provider_config.api_key)
        client = AsyncOpenAI(
            base_url=provider_config.base_url,
            api_key=api_key,
            timeout=timeout,
        )

        start = time.monotonic()
        await client.chat.completions.create(
            model=provider_config.default_model,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5,
        )
        elapsed_ms = (time.monotonic() - start) * 1000

        log.info(
            "provider_test_success",
            provider=provider_name,
            latency_ms=round(elapsed_ms, 1),
        )
        return ConnectionTestResult(
            provider=provider_name,
            connected=True,
            latency_ms=round(elapsed_ms, 1),
        )
    except Exception as exc:
        log.warning(
            "provider_test_failed",
            provider=provider_name,
            error=str(exc),
        )
        return ConnectionTestResult(
            provider=provider_name,
            connected=False,
            latency_ms=0.0,
            error=str(exc),
        )
    finally:
        if client is not None:
            await client.close()
