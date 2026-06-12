"""AB-007 — cost_guard.reserve_evolution_run (evolution lane sub-budget).

The 22:00 evolution lane reserves through BOTH layers: the dedicated
QUANTMIND_EVOLUTION_DAILY_SUBBUDGET ceiling AND the unified
``llm:usage:{utc_date}`` ¥100 reservation (no cap bypass — R-004
mandate absorbed by AB-007).
"""

from __future__ import annotations

import datetime

import pytest

from backend.services import cost_guard
from backend.services.cost_guard import (
    BudgetReservation,
    get_evolution_spent,
    reserve_evolution_run,
    settle_budget,
)

_DATE = datetime.date(2026, 6, 12)
_EVOLUTION_KEY = "llm:evolution:2026-06-12"


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.expires: dict[str, int] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def get(self, key: str):  # noqa: ANN201
        value = self.store.get(key)
        return None if value is None else str(value)

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()

    async def _spent(client, *, today=None):  # noqa: ANN001, ANN202
        return float(redis.store.get("llm:usage:spent", 0.0))

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)
    return redis


class TestReserveEvolutionRun:
    @pytest.mark.asyncio
    async def test_reserves_through_both_layers(
        self, fake_redis: FakeRedis
    ) -> None:
        reservation = await reserve_evolution_run(
            fake_redis, estimated_rmb=5.0, today=_DATE
        )
        assert isinstance(reservation, BudgetReservation)
        # Sub-counter incremented + TTL set.
        assert fake_redis.store[_EVOLUTION_KEY] == 5.0
        assert _EVOLUTION_KEY in fake_redis.expires
        # Unified reservation held until settled.
        assert await get_evolution_spent(
            fake_redis, today=_DATE
        ) == 5.0
        await settle_budget(fake_redis, reservation)

    @pytest.mark.asyncio
    async def test_subbudget_exhaustion_skips(
        self, fake_redis: FakeRedis
    ) -> None:
        fake_redis.store[_EVOLUTION_KEY] = 8.0  # default ceiling ¥10
        reservation = await reserve_evolution_run(
            fake_redis, estimated_rmb=5.0, today=_DATE
        )
        assert reservation is None
        # The sub-counter did NOT move (no partial reservation).
        assert fake_redis.store[_EVOLUTION_KEY] == 8.0

    @pytest.mark.asyncio
    async def test_unified_hard_cap_blocks_even_with_sub_headroom(
        self, fake_redis: FakeRedis
    ) -> None:
        fake_redis.store["llm:usage:spent"] = 99.0  # ¥100 hard cap
        reservation = await reserve_evolution_run(
            fake_redis, estimated_rmb=5.0, today=_DATE
        )
        assert reservation is None
        assert _EVOLUTION_KEY not in fake_redis.store

    @pytest.mark.asyncio
    async def test_env_override_ceiling(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QUANTMIND_EVOLUTION_DAILY_SUBBUDGET", "2.0")
        reservation = await reserve_evolution_run(
            fake_redis, estimated_rmb=5.0, today=_DATE
        )
        assert reservation is None

    @pytest.mark.asyncio
    async def test_invalid_estimate_raises(
        self, fake_redis: FakeRedis
    ) -> None:
        with pytest.raises(ValueError):
            await reserve_evolution_run(
                fake_redis, estimated_rmb=float("nan"), today=_DATE
            )

    @pytest.mark.asyncio
    async def test_cold_key_reads_zero(self, fake_redis: FakeRedis) -> None:
        assert await get_evolution_spent(fake_redis, today=_DATE) == 0.0
