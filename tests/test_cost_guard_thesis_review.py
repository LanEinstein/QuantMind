"""W-002 — cost_guard.reserve_thesis_review_slot (Line-2 thesis-review gate).

The 17:30 post-close advisory fires one LLM call per open PositionThesis,
deduped per (code, date), bounded by max_thesis_review_llm_per_day, reserving on
the SAME llm:usage:{utc_date} counter so it cannot bypass the ¥100/day hard cap.
"""

from __future__ import annotations

import datetime

import pytest

from backend.services import cost_guard
from backend.services.cost_guard import (
    BudgetReservation,
    get_max_thesis_review_llm_per_day,
    reserve_thesis_review_slot,
)

_DATE = datetime.date(2026, 6, 2)
_RESERVED_KEY = "llm:usage:2026-06-02:reserved"
_COUNT_KEY = "llm:thesis_review:2026-06-02"
_DEDUP_KEY = "llm:thesis_review:dedup:2026-06-02"


class FakeRedis:
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
async def test_first_review_reserves_on_unified_counter(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    res = await reserve_thesis_review_slot(
        redis, trigger_key="600519:2026-06-02", estimated_rmb=0.05, today=_DATE
    )
    assert isinstance(res, BudgetReservation)
    # Lands on the SAME llm:usage counter (cannot bypass ¥100 cap).
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.05)
    assert int(redis.store[_COUNT_KEY]) == 1
    assert "600519:2026-06-02" in redis.sets[_DEDUP_KEY]


@pytest.mark.asyncio
async def test_duplicate_review_deduped(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    first = await reserve_thesis_review_slot(
        redis, trigger_key="600519:2026-06-02", estimated_rmb=0.05, today=_DATE
    )
    second = await reserve_thesis_review_slot(
        redis, trigger_key="600519:2026-06-02", estimated_rmb=0.05, today=_DATE
    )
    assert first is not None
    assert second is None  # deduped — same (code, date) → no second LLM
    assert int(redis.store[_COUNT_KEY]) == 1


@pytest.mark.asyncio
async def test_daily_cap_exhausted_returns_none(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    cap = get_max_thesis_review_llm_per_day()
    granted = 0
    for i in range(cap + 3):
        res = await reserve_thesis_review_slot(
            redis, trigger_key=f"code{i}:2026-06-02", estimated_rmb=0.01, today=_DATE
        )
        if res is not None:
            granted += 1
    assert granted == cap  # exactly the cap is granted, the rest skipped


@pytest.mark.asyncio
async def test_reset_clears_thesis_review_gate_keys(patch_spent) -> None:
    # codex W-002 P3: reset_daily_gate_counters must clear the thesis-review
    # count + dedup keys too, else a same-day rerun is spuriously deduped out.
    patch_spent(0.0)
    redis = FakeRedis()
    await reserve_thesis_review_slot(
        redis, trigger_key="600519:2026-06-02", estimated_rmb=0.05, today=_DATE
    )
    assert int(redis.store.get(_COUNT_KEY, 0)) == 1
    assert "600519:2026-06-02" in redis.sets.get(_DEDUP_KEY, set())

    class _DelRedis(FakeRedis):
        async def delete(self, *keys: str) -> int:
            n = 0
            for k in keys:
                n += self.store.pop(k, None) is not None
                n += self.sets.pop(k, None) is not None
            return n

    del_redis = _DelRedis()
    del_redis.store = redis.store
    del_redis.sets = redis.sets
    await cost_guard.reset_daily_gate_counters(del_redis, today=_DATE)
    assert _COUNT_KEY not in del_redis.store
    assert _DEDUP_KEY not in del_redis.sets


@pytest.mark.asyncio
async def test_budget_exhausted_skips_and_rolls_back(patch_spent) -> None:
    # Daily spend already at the ¥100 hard cap → the reservation refuses, the
    # advisory is skipped (None), and the count is rolled back (fired-only).
    patch_spent(100.0)
    redis = FakeRedis()
    res = await reserve_thesis_review_slot(
        redis, trigger_key="600519:2026-06-02", estimated_rmb=0.05, today=_DATE
    )
    assert res is None
    assert int(redis.store.get(_COUNT_KEY, 0)) == 0  # rolled back
