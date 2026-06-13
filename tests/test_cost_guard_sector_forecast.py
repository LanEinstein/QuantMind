"""O-002 — cost_guard.reserve_sector_forecast_slot (MiroFish forecast gate).

The 17:00 EOD pipeline fires at most one sector-forecast LLM call per trade
date, deduped per trade_date, bounded by max_sector_forecast_llm_per_day,
reserving on the SAME llm:usage:{utc_date} counter so it cannot bypass the
¥100/day hard cap. Mirrors the W-002 thesis-review gate tests.
"""

from __future__ import annotations

import datetime

import pytest

from backend.services import cost_guard
from backend.services.cost_guard import (
    BudgetReservation,
    get_max_sector_forecast_llm_per_day,
    reserve_sector_forecast_slot,
)

_DATE = datetime.date(2026, 6, 12)
_TRADE_DATE = "2026-06-12"
_RESERVED_KEY = "llm:usage:2026-06-12:reserved"
_COUNT_KEY = "llm:sector_forecast:2026-06-12"
# Dedup is keyed by TRADE DATE (codex O-002 P2), not the rerun's UTC day.
_DEDUP_KEY = "llm:sector_forecast:dedup:2026-06-12"


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def decr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def sadd(self, key: str, member: str) -> int:
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        s = self.sets.get(key, set())
        if member in s:
            s.discard(member)
            return 1
        return 0

    async def get(self, key: str):  # noqa: ANN201
        v = self.store.get(key)
        return None if v is None else str(v)

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            n += self.store.pop(k, None) is not None
            n += self.sets.pop(k, None) is not None
        return n


@pytest.fixture
def patch_spent(monkeypatch: pytest.MonkeyPatch):
    def _set(value: float) -> None:
        async def _spent(_redis, *, today=None):  # noqa: ANN001
            return value

        monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)

    return _set


@pytest.mark.asyncio
async def test_first_forecast_reserves_on_unified_counter(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    res = await reserve_sector_forecast_slot(
        redis, trigger_key="2026-06-12", estimated_rmb=0.5, today=_DATE
    )
    assert isinstance(res, BudgetReservation)
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.5)
    assert int(redis.store[_COUNT_KEY]) == 1
    assert _TRADE_DATE in redis.sets[_DEDUP_KEY]


@pytest.mark.asyncio
async def test_same_trade_date_deduped(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    first = await reserve_sector_forecast_slot(
        redis, trigger_key="2026-06-12", estimated_rmb=0.5, today=_DATE
    )
    second = await reserve_sector_forecast_slot(
        redis, trigger_key="2026-06-12", estimated_rmb=0.5, today=_DATE
    )
    assert first is not None
    assert second is None
    assert int(redis.store[_COUNT_KEY]) == 1


@pytest.mark.asyncio
async def test_daily_cap_exhausted_returns_none(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    cap = get_max_sector_forecast_llm_per_day()
    granted = 0
    for i in range(cap + 3):
        res = await reserve_sector_forecast_slot(
            redis, trigger_key=f"2026-06-{i:02d}", estimated_rmb=0.1, today=_DATE
        )
        if res is not None:
            granted += 1
    assert granted == cap


@pytest.mark.asyncio
async def test_budget_exhausted_skips_and_rolls_back(patch_spent) -> None:
    patch_spent(100.0)
    redis = FakeRedis()
    res = await reserve_sector_forecast_slot(
        redis, trigger_key="2026-06-12", estimated_rmb=0.5, today=_DATE
    )
    assert res is None
    assert int(redis.store.get(_COUNT_KEY, 0)) == 0
    # codex O-002 verify: a non-paid skip must NOT leave the durable dedup
    # marker, else the same trade_date is blocked for the 14-day TTL.
    assert _TRADE_DATE not in redis.sets.get(_DEDUP_KEY, set())


@pytest.mark.asyncio
async def test_budget_skip_then_retry_succeeds(patch_spent) -> None:
    # Budget exhausted today → skip (no marker left). Tomorrow budget frees
    # and the SAME trade_date (holiday fallback) must be re-attemptable.
    redis = FakeRedis()
    patch_spent(100.0)
    first = await reserve_sector_forecast_slot(
        redis,
        trigger_key=_TRADE_DATE,
        estimated_rmb=0.5,
        today=datetime.date(2026, 6, 12),
    )
    assert first is None
    patch_spent(0.0)
    second = await reserve_sector_forecast_slot(
        redis,
        trigger_key=_TRADE_DATE,
        estimated_rmb=0.5,
        today=datetime.date(2026, 6, 13),
    )
    assert second is not None  # retry allowed once budget freed


@pytest.mark.asyncio
async def test_cap_exhausted_releases_marker(patch_spent) -> None:
    # When the per-UTC-day cap is hit, the trade_date's durable marker must
    # be released so the forecast is not permanently blocked.
    patch_spent(0.0)
    redis = FakeRedis()
    cap = get_max_sector_forecast_llm_per_day()
    # Exhaust the cap with OTHER trade dates.
    for i in range(cap):
        await reserve_sector_forecast_slot(
            redis,
            trigger_key=f"2026-05-{i + 1:02d}",
            estimated_rmb=0.1,
            today=_DATE,
        )
    blocked = await reserve_sector_forecast_slot(
        redis, trigger_key=_TRADE_DATE, estimated_rmb=0.1, today=_DATE
    )
    assert blocked is None  # cap reached
    assert _TRADE_DATE not in redis.sets.get(_DEDUP_KEY, set())


@pytest.mark.asyncio
async def test_holiday_fallback_rerun_deduped_across_utc_days(
    patch_spent,
) -> None:
    # codex O-002 P2: a later UTC-day rerun that falls back to the SAME
    # prior trade_date must NOT re-pay for the forecast. The dedup marker
    # is keyed by trade_date, so the second attempt is skipped even though
    # the count counter (per-UTC-day) is fresh.
    patch_spent(0.0)
    redis = FakeRedis()
    first = await reserve_sector_forecast_slot(
        redis,
        trigger_key=_TRADE_DATE,
        estimated_rmb=0.5,
        today=datetime.date(2026, 6, 12),
    )
    # Next calendar day is a holiday → cron falls back to 2026-06-12.
    second = await reserve_sector_forecast_slot(
        redis,
        trigger_key=_TRADE_DATE,
        estimated_rmb=0.5,
        today=datetime.date(2026, 6, 13),
    )
    assert first is not None
    assert second is None  # deduped by trade_date, no second paid call
    # The fresh-UTC-day count counter must not have been bumped by the
    # deduped attempt.
    assert int(redis.store.get("llm:sector_forecast:2026-06-13", 0)) == 0


@pytest.mark.asyncio
async def test_reset_clears_count_but_not_durable_dedup(patch_spent) -> None:
    # The per-UTC-day cap counter is a transient gate (cleared on reset);
    # the per-trade-date dedup marker is durable idempotency (preserved).
    patch_spent(0.0)
    redis = FakeRedis()
    await reserve_sector_forecast_slot(
        redis, trigger_key=_TRADE_DATE, estimated_rmb=0.5, today=_DATE
    )
    assert int(redis.store.get(_COUNT_KEY, 0)) == 1
    await cost_guard.reset_daily_gate_counters(redis, today=_DATE)
    assert _COUNT_KEY not in redis.store
    # Dedup marker survives — re-forecasting the same trade_date stays barred.
    assert _TRADE_DATE in redis.sets.get(_DEDUP_KEY, set())
