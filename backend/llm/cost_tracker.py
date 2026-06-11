"""LLM cost aggregation from Redis usage data."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.llm.fallback import MODEL_COST_RATES, resolve_cost_rate

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.database import MongoDBService

log = structlog.get_logger(component="cost_tracker")

# Per-model pricing in RMB per 1K tokens (documentation/parity view of
# the live table). DERIVED from MODEL_COST_RATES so the two can never
# drift apart (the hand-maintained copy went 3-30x stale before
# P0-10-amendment-2026-06-11) — the live cost computation flows through
# :func:`backend.llm.fallback.resolve_cost_rate` (per-million).
MODEL_PRICING: dict[str, dict[str, float]] = {
    model: {
        "input": rate.input_rmb_per_million / 1_000,
        "output": rate.output_rmb_per_million / 1_000,
    }
    for model, rate in MODEL_COST_RATES.items()
}


@dataclass(frozen=True)
class DailyCostEntry:
    """A single usage record for one agent-provider pair on one day."""

    date: str
    agent_name: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    requests: int
    cost_rmb: float


@dataclass(frozen=True)
class CostSummary:
    """Aggregated cost statistics over a period."""

    period: str
    days: int
    entries: tuple[DailyCostEntry, ...]
    total_cost_rmb: float
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    by_agent: dict[str, float]
    by_provider: dict[str, float]
    daily_totals: dict[str, float]


def calculate_cost(
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None = None,
) -> float:
    """Calculate cost in RMB for a given token count.

    Uses :func:`backend.llm.fallback.resolve_cost_rate` (per million
    tokens) — a per-model rate (e.g. ``qwen3.7-max``) wins over the
    per-provider family rate; an unknown provider/model pair costs ¥0.
    """
    rate = resolve_cost_rate(provider, model)
    cost = (
        prompt_tokens * rate.input_rmb_per_million / 1_000_000
        + completion_tokens * rate.output_rmb_per_million / 1_000_000
    )
    return round(cost, 8)


async def aggregate_costs(
    redis_client: redis.asyncio.Redis,
    days: int = 30,
    period: str = "daily",
) -> CostSummary:
    """Scan Redis for usage data and aggregate cost statistics.

    Scans keys matching the pattern llm:usage:{date}:{agent}:{provider}
    for the requested number of days.

    Args:
        redis_client: Async Redis client.
        days: Number of days to look back.
        period: Aggregation period ('daily' or 'weekly').

    Returns:
        CostSummary with all aggregated data.
    """
    # Pin to UTC date — must match the writer in
    # backend.llm.fallback._utc_date_str(). Using local time here was a
    # silent timezone-drift bug: in Asia/Shanghai the cost_guard hard
    # ceiling could read zero spend during 00:00-08:00 UTC+8 even
    # though Redis already had today's UTC entries (codex P5B-T03 R6).
    today = datetime.datetime.now(tz=datetime.UTC).date()
    entries: list[DailyCostEntry] = []

    for day_offset in range(days):
        date = today - datetime.timedelta(days=day_offset)
        date_str = date.isoformat()
        pattern = f"llm:usage:{date_str}:*"

        try:
            keys = await _scan_keys(redis_client, pattern)
        except Exception as exc:
            log.warning("cost_scan_failed", date=date_str, error=str(exc))
            continue

        for key in keys:
            entry = await _parse_usage_key(redis_client, key, date_str)
            if entry is not None:
                entries.append(entry)

    return _build_summary(entries, period, days)


async def _scan_keys(redis_client: redis.asyncio.Redis, pattern: str) -> list[str]:
    """Scan Redis for keys matching a pattern."""
    keys: list[str] = []
    cursor: int | bytes = 0
    while True:
        cursor, batch = await redis_client.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(k if isinstance(k, str) else k.decode() for k in batch)
        if cursor == 0:
            break
    return keys


async def _parse_usage_key(
    redis_client: redis.asyncio.Redis,
    key: str,
    date_str: str,
) -> DailyCostEntry | None:
    """Parse a single Redis usage key into a DailyCostEntry."""
    try:
        data = await redis_client.hgetall(key)
        if not data:
            return None

        # Key format: llm:usage:{date}:{agent_name}:{provider}
        parts = key.split(":")
        if len(parts) < 5:
            return None

        agent_name = parts[3]
        provider = parts[4]

        prompt_tokens = int(data.get("prompt_tokens", 0))
        completion_tokens = int(data.get("completion_tokens", 0))
        requests = int(data.get("requests", 0))
        cost_rmb = float(data.get("cost_rmb", 0.0))

        # Drop entries with corrupt cost values: a negative or non-finite
        # cost_rmb would otherwise offset legitimate spend in the daily
        # aggregate and silently undercut the cost_guard hard cap. This
        # is the data-layer defense; cost_guard.get_budget_state has a
        # second fail-closed check on the aggregate.
        if not math.isfinite(cost_rmb) or cost_rmb < 0:
            log.warning(
                "cost_entry_invalid",
                key=key,
                cost_rmb=cost_rmb,
                action="dropped",
            )
            return None

        return DailyCostEntry(
            date=date_str,
            agent_name=agent_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            requests=requests,
            cost_rmb=cost_rmb,
        )
    except Exception as exc:
        log.warning("cost_parse_failed", key=key, error=str(exc))
        return None


def _build_summary(
    entries: list[DailyCostEntry], period: str, days: int
) -> CostSummary:
    """Build a CostSummary from a list of DailyCostEntry records."""
    total_cost = 0.0
    total_requests = 0
    total_prompt = 0
    total_completion = 0
    by_agent: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for entry in entries:
        total_cost += entry.cost_rmb
        total_requests += entry.requests
        total_prompt += entry.prompt_tokens
        total_completion += entry.completion_tokens

        by_agent[entry.agent_name] = (
            by_agent.get(entry.agent_name, 0.0) + entry.cost_rmb
        )
        by_provider[entry.provider] = (
            by_provider.get(entry.provider, 0.0) + entry.cost_rmb
        )
        daily_totals[entry.date] = daily_totals.get(entry.date, 0.0) + entry.cost_rmb

    return CostSummary(
        period=period,
        days=days,
        entries=tuple(entries),
        total_cost_rmb=round(total_cost, 4),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        by_agent={k: round(v, 4) for k, v in by_agent.items()},
        by_provider={k: round(v, 4) for k, v in by_provider.items()},
        daily_totals={k: round(v, 4) for k, v in daily_totals.items()},
    )


async def flush_to_mongodb(
    redis_client: redis.asyncio.Redis,
    mongodb: MongoDBService,
    days: int = 1,
) -> int:
    """Persist cost entries from Redis to MongoDB for durable storage.

    Args:
        redis_client: Async Redis client.
        mongodb: MongoDBService instance.
        days: Number of days to flush (default: today only).

    Returns:
        Count of entries persisted.
    """
    try:
        summary = await aggregate_costs(redis_client, days=days)
    except Exception as exc:
        log.warning("cost_flush_aggregate_failed", error=str(exc))
        return 0

    count = 0
    for entry in summary.entries:
        try:
            await mongodb.save_cost_entry(
                {
                    "date": entry.date,
                    "agent_name": entry.agent_name,
                    "provider": entry.provider,
                    "prompt_tokens": entry.prompt_tokens,
                    "completion_tokens": entry.completion_tokens,
                    "requests": entry.requests,
                    "cost_rmb": entry.cost_rmb,
                }
            )
            count += 1
        except Exception as exc:
            log.warning(
                "cost_flush_entry_failed",
                date=entry.date,
                agent=entry.agent_name,
                error=str(exc),
            )

    log.info("cost_flush_complete", entries=count)
    return count
