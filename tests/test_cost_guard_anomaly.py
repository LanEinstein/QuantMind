"""N-004 — cost_guard.reserve_anomaly_llm_slot (Line-2 anomaly-LLM gate).

Line-2 polling is zero-LLM; an LLM fires only on a deduplicated trigger,
bounded by max_anomaly_llm_per_day, reserving on the SAME llm:usage:{utc_date}
counter so it cannot bypass the ¥20/day hard cap (P1-7-amendment §2.4).
"""

from __future__ import annotations

import datetime

import pytest

from backend.services import cost_guard
from backend.services.cost_guard import (
    BudgetReservation,
    get_max_anomaly_llm_per_day,
    reserve_anomaly_llm_slot,
)

_DATE = datetime.date(2026, 5, 24)
_RESERVED_KEY = "llm:usage:2026-05-24:reserved"
_COUNT_KEY = "llm:anomaly:2026-05-24"
_DEDUP_KEY = "llm:anomaly:dedup:2026-05-24"


class FakeRedis:
    """In-memory Redis with the scalar + set ops the anomaly gate needs."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}
        self.expires: dict[str, int] = {}

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
        self.expires[key] = ttl
        return True


@pytest.fixture
def patch_spent(monkeypatch: pytest.MonkeyPatch):
    def _set(value: float) -> None:
        async def _spent(_redis, *, today=None):  # noqa: ANN001
            return value

        monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)

    return _set


@pytest.mark.asyncio
async def test_first_trigger_reserves_on_unified_counter(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    res = await reserve_anomaly_llm_slot(
        redis, trigger_key="600519:price_zscore", estimated_rmb=0.1, today=_DATE
    )
    assert isinstance(res, BudgetReservation)
    # Reservation lands on the SAME llm:usage counter (not a separate budget).
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.1)
    assert int(redis.store[_COUNT_KEY]) == 1
    assert "600519:price_zscore" in redis.sets[_DEDUP_KEY]


@pytest.mark.asyncio
async def test_duplicate_trigger_skipped(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    first = await reserve_anomaly_llm_slot(
        redis, trigger_key="600519:price_zscore", estimated_rmb=0.1, today=_DATE
    )
    second = await reserve_anomaly_llm_slot(
        redis, trigger_key="600519:price_zscore", estimated_rmb=0.1, today=_DATE
    )
    assert first is not None
    assert second is None  # deduped — no second LLM
    assert int(redis.store[_COUNT_KEY]) == 1  # count not double-incremented
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.1)  # no second reservation


@pytest.mark.asyncio
async def test_daily_cap_exhausted_returns_none(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    cap = get_max_anomaly_llm_per_day()
    granted = 0
    for i in range(cap + 3):
        res = await reserve_anomaly_llm_slot(
            redis, trigger_key=f"600519:kind{i}", estimated_rmb=0.05, today=_DATE
        )
        if res is not None:
            granted += 1
    assert granted == cap  # never exceeds the daily anomaly-LLM cap
    assert int(redis.store[_COUNT_KEY]) == cap  # rolled back over-cap attempts


@pytest.mark.asyncio
async def test_budget_exhausted_skips_and_rolls_back(patch_spent) -> None:
    # Daily spend already at the ¥100 hard cap (P1-7-amendment-2026-05-26) →
    # the reservation refuses → the optional anomaly LLM is skipped (None) and
    # the count is rolled back.
    patch_spent(100.0)
    redis = FakeRedis()
    res = await reserve_anomaly_llm_slot(
        redis, trigger_key="600519:price_zscore", estimated_rmb=0.1, today=_DATE
    )
    assert res is None
    assert int(redis.store.get(_COUNT_KEY, 0)) == 0  # rolled back, no fire
    # The transient reservation was rolled back by reserve_budget itself.
    assert float(redis.store.get(_RESERVED_KEY, 0.0)) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_never_raises_on_redis_failure() -> None:
    class BrokenRedis(FakeRedis):
        async def sadd(self, key: str, member: str) -> int:
            raise RuntimeError("redis down")

    res = await reserve_anomaly_llm_slot(
        BrokenRedis(), trigger_key="x:y", estimated_rmb=0.1, today=_DATE
    )
    assert res is None  # fail-closed: skip the optional LLM, never raise


@pytest.mark.asyncio
async def test_reservation_layer_failure_rolls_back_and_skips(patch_spent) -> None:
    # sadd/incr succeed but the unified reservation incrbyfloat raises a raw
    # (non-budget) Redis error → the gate must NOT propagate; it rolls back the
    # count + dedup and returns None (codex N-004 P2).
    patch_spent(0.0)

    class ReserveBroken(FakeRedis):
        async def incrbyfloat(self, key: str, amount: float) -> float:
            raise RuntimeError("reservation key write failed")

    redis = ReserveBroken()
    res = await reserve_anomaly_llm_slot(
        redis, trigger_key="600519:price_zscore", estimated_rmb=0.1, today=_DATE
    )
    assert res is None  # fail-closed, never raises
    assert int(redis.store.get(_COUNT_KEY, 0)) == 0  # count rolled back
    assert "600519:price_zscore" not in redis.sets.get(_DEDUP_KEY, set())  # retry-able
