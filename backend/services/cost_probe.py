"""H-003 — Redis-only cost probe used by ``cost_guard`` and ``cost.py``.

Splits the LLM-tracker's aggregation primitives out of ``backend.llm``
so that ``cost_guard.py`` and ``soft_degrade_manager.py`` can read
spend data without violating CLAUDE.md §2.10 (which forbids
``backend.{llm,agents,mirofish,data}`` imports).

The probe is intentionally minimal: it knows only how to scan
``llm:usage:{date}:{agent}:{provider}`` Redis hashes for a date range
and return raw aggregates. The richer ``CostSummary`` and
``calculate_cost`` helpers that wrap pricing data stay in
``backend.llm.cost_tracker`` (they need ``COST_RATES`` from
``backend.llm.fallback`` which is the LLM-layer's concern).
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="cost_probe")

_KEY_PREFIX = "llm:usage"


@dataclass(frozen=True)
class CostProbeEntry:
    """Single Redis usage hash decoded into a typed record."""

    date: str
    agent_name: str
    provider: str
    cost_rmb: float


@dataclass(frozen=True)
class CostProbeSummary:
    """Result of :func:`scan_costs`.

    Aggregates spend by day + provider so callers can drive both the
    P1-7 daily ¥20 hard cap, the daily ¥14 soft cut, the monthly ¥440
    soft 3-tier nodes, and the Kimi-only ¥4 daily cap from one scan.
    """

    days: int
    total_cost_rmb: float
    daily_totals: dict[str, float]
    by_provider: dict[str, float]
    by_provider_daily: dict[str, dict[str, float]]


def _utc_today() -> datetime.date:
    return datetime.datetime.now(tz=datetime.UTC).date()


def _date_offset(today: datetime.date, days_back: int) -> datetime.date:
    return today - datetime.timedelta(days=days_back)


async def scan_costs(
    redis_client: redis.asyncio.Redis,
    *,
    days: int,
    today: datetime.date | None = None,
) -> CostProbeSummary:
    """Aggregate Redis usage entries for the trailing ``days`` window.

    Drops entries with negative or non-finite ``cost_rmb`` so a corrupt
    HSET cannot offset legitimate spend (mirrors the secondary check in
    :func:`backend.services.cost_guard.get_budget_state`).
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    base_day = today or _utc_today()

    daily_totals: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    by_provider_daily: dict[str, dict[str, float]] = {}
    total = 0.0

    for offset in range(days):
        date_str = _date_offset(base_day, offset).isoformat()
        pattern = f"{_KEY_PREFIX}:{date_str}:*"
        try:
            keys = await _scan_keys(redis_client, pattern)
        except Exception as exc:  # noqa: BLE001 — fail-open for transient Redis errors
            log.warning("cost_scan_failed", date=date_str, error=str(exc))
            continue

        for key in keys:
            entry = await _parse_usage_key(redis_client, key, date_str)
            if entry is None:
                continue
            total += entry.cost_rmb
            daily_totals[entry.date] = (
                daily_totals.get(entry.date, 0.0) + entry.cost_rmb
            )
            by_provider[entry.provider] = (
                by_provider.get(entry.provider, 0.0) + entry.cost_rmb
            )
            provider_day = by_provider_daily.setdefault(entry.provider, {})
            provider_day[entry.date] = (
                provider_day.get(entry.date, 0.0) + entry.cost_rmb
            )

    return CostProbeSummary(
        days=days,
        total_cost_rmb=round(total, 4),
        daily_totals={k: round(v, 4) for k, v in daily_totals.items()},
        by_provider={k: round(v, 4) for k, v in by_provider.items()},
        by_provider_daily={
            provider: {date: round(cost, 4) for date, cost in by_day.items()}
            for provider, by_day in by_provider_daily.items()
        },
    )


async def get_daily_spent(
    redis_client: redis.asyncio.Redis, *, today: datetime.date | None = None
) -> float:
    """Return today's aggregate LLM spend (Asia/Shanghai-safe UTC pin)."""
    summary = await scan_costs(redis_client, days=1, today=today)
    if not summary.daily_totals:
        return 0.0
    return next(iter(summary.daily_totals.values()))


async def get_daily_spent_for_provider(
    redis_client: redis.asyncio.Redis,
    *,
    provider: str,
    today: datetime.date | None = None,
) -> float:
    """Return today's spend for a single provider (e.g. ``kimi``)."""
    summary = await scan_costs(redis_client, days=1, today=today)
    by_provider = summary.by_provider_daily.get(provider, {})
    if not by_provider:
        return 0.0
    return next(iter(by_provider.values()))


async def get_month_spent(
    redis_client: redis.asyncio.Redis,
    *,
    today: datetime.date | None = None,
) -> float:
    """Return aggregate spend for the *current calendar month* (UTC).

    P1-7 §1.7 ties the monthly soft budget to the calendar month — not
    a rolling 30-day window — so a fresh month always resets the
    50/80/100% milestones. We scan the day buckets falling within the
    month-to-date range.
    """
    base = today or _utc_today()
    days_into_month = base.day  # 1..31
    summary = await scan_costs(redis_client, days=days_into_month, today=base)
    month_total = 0.0
    for date_str, cost in summary.daily_totals.items():
        try:
            parsed = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if parsed.year == base.year and parsed.month == base.month:
            month_total += cost
    return round(month_total, 4)


async def _scan_keys(
    redis_client: redis.asyncio.Redis, pattern: str
) -> list[str]:
    """SCAN wrapper that decodes bytes-vs-str transparently."""
    keys: list[str] = []
    cursor: int | bytes = 0
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=pattern, count=100
        )
        keys.extend(
            k if isinstance(k, str) else k.decode() for k in batch
        )
        if cursor == 0:
            break
    return keys


def _normalize_hash(data: object) -> dict[str, str]:
    """Decode bytes-keyed Redis hashes to str-keyed dicts.

    The QuantMind app wires ``aioredis.from_url(decode_responses=True)``
    so production keys/values arrive as str; nonetheless a default
    asyncio Redis client returns bytes from ``hgetall`` (codex cycle 2
    P2). Normalizing here keeps the probe correct under either wiring
    and prevents a silent fail-open where ``data.get("cost_rmb", 0.0)``
    misses the ``b"cost_rmb"`` key entirely.
    """
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        key = k.decode("utf-8") if isinstance(k, bytes | bytearray) else str(k)
        val = (
            v.decode("utf-8")
            if isinstance(v, bytes | bytearray)
            else str(v) if v is not None else ""
        )
        out[key] = val
    return out


async def _parse_usage_key(
    redis_client: redis.asyncio.Redis,
    key: str,
    date_str: str,
) -> CostProbeEntry | None:
    try:
        raw = await redis_client.hgetall(key)
        data = _normalize_hash(raw)
        if not data:
            return None
        parts = key.split(":")
        if len(parts) < 5:
            return None
        agent_name = parts[3]
        provider = parts[4]
        cost_rmb = float(data.get("cost_rmb", 0.0))
        if not math.isfinite(cost_rmb) or cost_rmb < 0:
            log.warning("cost_entry_invalid", key=key, cost_rmb=cost_rmb)
            return None
        return CostProbeEntry(
            date=date_str,
            agent_name=agent_name,
            provider=provider,
            cost_rmb=cost_rmb,
        )
    except Exception as exc:  # noqa: BLE001 — operator-visible only
        log.warning("cost_parse_failed", key=key, error=str(exc))
        return None


__all__ = [
    "CostProbeEntry",
    "CostProbeSummary",
    "get_daily_spent",
    "get_daily_spent_for_provider",
    "get_month_spent",
    "scan_costs",
]
