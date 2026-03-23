"""LLM fallback logic and token usage / cost tracking."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

import openai
import structlog

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="llm_fallback")

# -- Retryable exceptions that trigger fallback --

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIConnectionError,
)

# -- Cost rates per million tokens (from blueprint section 2.1) --


@dataclass(frozen=True)
class CostRate:
    """Cost rate per million tokens for a provider (in RMB)."""

    input_rmb_per_million: float
    output_rmb_per_million: float


COST_RATES: dict[str, CostRate] = {
    "deepseek": CostRate(input_rmb_per_million=0.2, output_rmb_per_million=0.2),
    "qwen": CostRate(input_rmb_per_million=1.0, output_rmb_per_million=1.0),
    "minimax": CostRate(input_rmb_per_million=2.1, output_rmb_per_million=8.4),
}

_TTL_DAYS = 90


# -- Token usage tracking --


async def track_usage(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Track token usage and cost in Redis.

    Key pattern: llm:usage:{date}:{agent_name}:{provider}
    Fields: prompt_tokens, completion_tokens, requests, cost_rmb

    Silently logs and returns on Redis errors (degrade, not crash).
    """
    if redis_client is None:
        return

    date_str = datetime.date.today().isoformat()
    key = f"llm:usage:{date_str}:{agent_name}:{provider}"

    rate = COST_RATES.get(provider, CostRate(0.0, 0.0))
    cost = (
        prompt_tokens * rate.input_rmb_per_million / 1_000_000
        + completion_tokens * rate.output_rmb_per_million / 1_000_000
    )

    try:
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "prompt_tokens", prompt_tokens)
        pipe.hincrby(key, "completion_tokens", completion_tokens)
        pipe.hincrby(key, "requests", 1)
        pipe.hincrbyfloat(key, "cost_rmb", round(cost, 8))
        pipe.expire(key, _TTL_DAYS * 86400)
        await pipe.execute()
    except Exception as exc:
        log.warning(
            "redis_usage_tracking_failed",
            agent_name=agent_name,
            provider=provider,
            error=str(exc),
        )


async def track_fallback(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    primary_provider: str,
    fallback_provider: str,
) -> None:
    """Increment fallback counter in Redis.

    Key: llm:fallbacks:{date}
    Field: {agent_name}:{primary_provider}->{fallback_provider}
    """
    if redis_client is None:
        return

    date_str = datetime.date.today().isoformat()
    key = f"llm:fallbacks:{date_str}"
    field = f"{agent_name}:{primary_provider}->{fallback_provider}"

    try:
        await redis_client.hincrby(key, field, 1)
        await redis_client.expire(key, _TTL_DAYS * 86400)
    except Exception as exc:
        log.warning(
            "redis_fallback_tracking_failed",
            agent_name=agent_name,
            error=str(exc),
        )
