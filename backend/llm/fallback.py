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


def _utc_date_str() -> str:
    """Single source of truth for `llm:*:{date}` Redis key date basis.

    Track-time and read-time keys must agree, otherwise the monitoring
    endpoint silently shows zero data while Redis fills under a
    different bucket. Pinning to UTC removes timezone drift on hosts
    deployed in Asia/Shanghai (the default for this project).
    """
    return datetime.datetime.now(tz=datetime.UTC).date().isoformat()


# -- Retryable exceptions that trigger fallback --

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIConnectionError,
)

# -- Cost rates per million tokens (P0-10-amendment-2026-06-11) --


@dataclass(frozen=True)
class CostRate:
    """Cost rate per million tokens for a provider (in RMB)."""

    input_rmb_per_million: float
    output_rmb_per_million: float


# Per-MODEL rates, consulted before the per-provider family table. Every
# model actually routed in config/agent_models.yaml must have a tier here,
# otherwise its spend is computed from the (deliberately expensive) family
# rate below. The Redis usage key stays keyed by provider family (see
# :func:`track_usage`) so cost_guard's aggregation is unchanged; only the
# computed ``cost_rmb`` is model-accurate.
#
# Official LIST prices verified 2026-06-11 (input = cache-miss tier;
# limited-time discounts are deliberately NOT priced in — they lapse
# without notice and the table is restart-gated):
#
# * deepseek-v4-pro   ¥3 / ¥6      (api-docs.deepseek.com zh price list)
# * deepseek-v4-flash ¥1 / ¥2      (same source; cheap high-throughput tier)
# * qwen3.6-plus      ¥2 / ¥12     (Aliyun Model Studio; the 90-day free
#                                    quota is assumed exhausted)
# * qwen3.7-max       ¥12 / ¥36    (list price; the limited-time 50%-off
#                                    ¥6/¥18 is intentionally ignored)
# * kimi-k2.6         ¥7.5 / ¥30   (assumption: USD $0.95/$4.00 × FX 7.5,
#                                    deliberately above the prevailing
#                                    ~7.1-7.3 band so the table can only
#                                    over-count; RMB list pending owner
#                                    console verification —
#                                    P0-10-amendment-2026-06-11 §6)
MODEL_COST_RATES: dict[str, CostRate] = {
    "deepseek-v4-pro": CostRate(input_rmb_per_million=3.0, output_rmb_per_million=6.0),
    "deepseek-v4-flash": CostRate(
        input_rmb_per_million=1.0, output_rmb_per_million=2.0
    ),
    "qwen3.6-plus": CostRate(input_rmb_per_million=2.0, output_rmb_per_million=12.0),
    "qwen3.7-max": CostRate(input_rmb_per_million=12.0, output_rmb_per_million=36.0),
    "kimi-k2.6": CostRate(input_rmb_per_million=7.5, output_rmb_per_million=30.0),
}

# Provider family → member models routed under that family (same
# base_url + API key). Used to DERIVE the family fallback rate as the
# max over members, so "an unmapped model can never bill below any known
# family member" holds by construction instead of by hand-synced copies.
_FAMILY_MEMBERS: dict[str, tuple[str, ...]] = {
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
    "qwen": ("qwen3.6-plus", "qwen3.7-max"),
    "kimi": ("kimi-k2.6",),
}

# Provider-FAMILY fallback rates, consulted only when the model is not in
# ``MODEL_COST_RATES``. Derived as each family's PRICIEST member — the
# conservative direction for a budget guard (a low family rate would
# silently under-count spend against the ¥100/day hard cap).
COST_RATES: dict[str, CostRate] = {
    family: CostRate(
        input_rmb_per_million=max(
            MODEL_COST_RATES[m].input_rmb_per_million for m in members
        ),
        output_rmb_per_million=max(
            MODEL_COST_RATES[m].output_rmb_per_million for m in members
        ),
    )
    for family, members in _FAMILY_MEMBERS.items()
}


def resolve_cost_rate(provider: str, model: str | None = None) -> CostRate:
    """Pick the most specific rate: model override → family → zero.

    A per-model rate (``MODEL_COST_RATES``) wins over the per-provider
    family rate (``COST_RATES``); an unknown provider/model pair returns
    a zero rate so unpriced calls cost ¥0 rather than crashing.
    """
    if model is not None:
        model_rate = MODEL_COST_RATES.get(model)
        if model_rate is not None:
            return model_rate
    return COST_RATES.get(provider, CostRate(0.0, 0.0))


_TTL_DAYS = 90


# -- Token usage tracking --


async def track_usage(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None = None,
) -> None:
    """Track token usage and cost in Redis.

    Key pattern: llm:usage:{date}:{agent_name}:{provider}
    Fields: prompt_tokens, completion_tokens, requests, cost_rmb

    ``model`` selects a per-model rate override when one exists
    (``MODEL_COST_RATES``) — e.g. the premium ``qwen3.7-max`` shares the
    ``qwen`` family yet costs more. The Redis key stays keyed by provider
    family so cost_guard's daily aggregation is unaffected; only the
    ``cost_rmb`` value becomes model-accurate.

    Silently logs and returns on Redis errors (degrade, not crash).
    """
    if redis_client is None:
        return

    date_str = _utc_date_str()
    key = f"llm:usage:{date_str}:{agent_name}:{provider}"

    rate = resolve_cost_rate(provider, model)
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

    date_str = _utc_date_str()
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


_ESCALATION_REASONS: frozenset[str] = frozenset({"low_confidence", "parse_failed"})


async def track_escalation(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    triage_provider: str,
    escalation_provider: str,
    reason: str,
) -> None:
    """Increment per-agent escalation counters in Redis.

    Key pattern: ``llm:escalations:{date}:{agent_name}``
    Fields:
      - ``count``                  total escalations today
      - ``reason_<reason>``        per-reason breakdown
      - ``route_<src>-><dst>``     per-route breakdown for cost analysis

    The reason field is whitelisted (``_ESCALATION_REASONS``) so a
    rogue caller cannot inflate the hash with arbitrary keys; unknown
    reasons fall through to ``reason_other`` and emit a warning. Redis
    failures degrade silently — escalation tracking is observability,
    never a hard dependency for the request path.
    """
    if redis_client is None:
        return

    bucket = reason if reason in _ESCALATION_REASONS else "other"
    if bucket == "other":
        log.warning(
            "escalation_unknown_reason",
            agent_name=agent_name,
            reason=reason,
        )

    date_str = _utc_date_str()
    key = f"llm:escalations:{date_str}:{agent_name}"
    route_field = f"route_{triage_provider}->{escalation_provider}"

    try:
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "count", 1)
        pipe.hincrby(key, f"reason_{bucket}", 1)
        pipe.hincrby(key, route_field, 1)
        pipe.expire(key, _TTL_DAYS * 86400)
        await pipe.execute()
    except Exception as exc:
        log.warning(
            "redis_escalation_tracking_failed",
            agent_name=agent_name,
            reason=reason,
            error=str(exc),
        )


# -- Live daily LLM timeout-rate telemetry (P0-6-amendment-2026-05-29 cond10a) --
#
# The PILOT readiness gate's cond10a reads the *current* daily timeout rate, NOT
# the 45-day acceptance report (which is INSUFFICIENT_DATA before the window
# completes and would wrongly block PILOT — Codex U-D2 P2). These two integer
# counters are incremented from the router on every provider call attempt and on
# every ``openai.APITimeoutError``; the gate computes ``timeouts / max(calls, 1)``
# and a cold-start day (0 calls) reads 0.0 == healthy. Counting is best-effort
# observability — a Redis write failure must never break the LLM request path
# (fail-open infra glitch), and the gate's own ``_safe_await`` fails closed when
# the read is unavailable, so the conservative direction is preserved either way.

_LLM_CALLS_KEY_PREFIX = "llm:calls"
_LLM_TIMEOUTS_KEY_PREFIX = "llm:timeouts"


async def _incr_daily_counter(
    redis_client: redis.asyncio.Redis | None, key: str, log_event: str
) -> None:
    """incr ``key`` and (re)set its TTL in one pipeline; best-effort.

    Mirrors the pipelined incr+expire idiom used by :func:`track_usage` /
    :func:`track_escalation` (single round-trip, TTL set atomically with the
    increment). Redis failures degrade to a warning and never propagate into
    the LLM request path (fail-open infra glitch).
    """
    if redis_client is None:
        return
    try:
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _TTL_DAYS * 86400)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001 — counting is observability only
        log.warning(log_event, error=str(exc))


async def track_llm_call(redis_client: redis.asyncio.Redis | None) -> None:
    """Increment the per-UTC-day total LLM provider-call-attempt counter.

    Key: ``llm:calls:{utc_date}``. Called once per provider ATTEMPT, so primary
    + fallback + escalation each count — cond10a is therefore an attempt-level
    timeout rate (``timeouts / attempts``), consistent with its numerator
    :func:`track_llm_timeout` which is also per-attempt.
    """
    await _incr_daily_counter(
        redis_client,
        f"{_LLM_CALLS_KEY_PREFIX}:{_utc_date_str()}",
        "redis_llm_call_tracking_failed",
    )


async def track_llm_timeout(redis_client: redis.asyncio.Redis | None) -> None:
    """Increment the per-UTC-day LLM timeout counter.

    Key: ``llm:timeouts:{utc_date}``. Called on each ``openai.APITimeoutError``
    before the exception is re-raised. cond10a is timeout-specific by design
    (mirrors the acceptance ``llm_timeout_rate``); other retryable failures
    (rate-limit / connection) are out of its scope.
    """
    await _incr_daily_counter(
        redis_client,
        f"{_LLM_TIMEOUTS_KEY_PREFIX}:{_utc_date_str()}",
        "redis_llm_timeout_tracking_failed",
    )


async def read_llm_timeout_rate(
    redis_client: redis.asyncio.Redis | None,
) -> tuple[int, int]:
    """Return ``(timeouts, calls)`` for the current UTC day.

    Missing keys read as ``0``. The caller (PILOT cond10a) computes
    ``timeouts / max(calls, 1)`` so a zero-call cold start is healthy (0.0).
    Raises on Redis error so the gate's ``_safe_await`` can fail closed — an
    unreadable counter must not silently pass the gate.
    """
    if redis_client is None:
        raise RuntimeError("redis client unavailable for llm timeout-rate read")
    date_str = _utc_date_str()
    raw_timeouts, raw_calls = await redis_client.mget(
        f"{_LLM_TIMEOUTS_KEY_PREFIX}:{date_str}",
        f"{_LLM_CALLS_KEY_PREFIX}:{date_str}",
    )
    timeouts = int(raw_timeouts) if raw_timeouts is not None else 0
    calls = int(raw_calls) if raw_calls is not None else 0
    return timeouts, calls
