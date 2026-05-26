"""AppliedReportGuard — durable report-id idempotency (U-D4).

Pins the claim/release contract for both the in-memory and Redis-backed
guards: the first claim of a report_id wins, a duplicate claim loses,
and release undoes a claim so a failed apply can be retried.
"""

from __future__ import annotations

import pytest

from backend.broker.applied_report_guard import (
    AppliedReportGuard,
    InMemoryAppliedReportGuard,
    RedisAppliedReportGuard,
)


class _FakeRedis:
    """Minimal SET NX EX + DEL fake mirroring redis.asyncio semantics."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[dict[str, object]] = []

    async def set(  # noqa: A003 — Redis command name
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool | None = None,
    ) -> bool | None:
        self.set_calls.append({"name": name, "ex": ex, "nx": nx})
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def delete(self, name: str) -> int:
        return 1 if self.store.pop(name, None) is not None else 0


class TestInMemoryAppliedReportGuard:
    async def test_first_claim_wins_duplicate_loses(self) -> None:
        guard = InMemoryAppliedReportGuard()
        assert await guard.claim("erp-1") is True
        assert await guard.claim("erp-1") is False

    async def test_release_allows_reclaim(self) -> None:
        guard = InMemoryAppliedReportGuard()
        assert await guard.claim("erp-1") is True
        await guard.release("erp-1")
        # After release a retry can re-attempt the apply.
        assert await guard.claim("erp-1") is True

    async def test_distinct_ids_independent(self) -> None:
        guard = InMemoryAppliedReportGuard()
        assert await guard.claim("erp-1") is True
        assert await guard.claim("erp-2") is True

    async def test_empty_report_id_raises(self) -> None:
        guard = InMemoryAppliedReportGuard()
        with pytest.raises(ValueError, match="report_id"):
            await guard.claim("")

    async def test_lru_evicts_oldest(self) -> None:
        guard = InMemoryAppliedReportGuard(max_entries=2)
        await guard.claim("a")
        await guard.claim("b")
        await guard.claim("c")  # evicts "a"
        # "a" was evicted → re-claimable; "c" still held.
        assert await guard.claim("a") is True
        assert await guard.claim("c") is False

    async def test_duplicate_claim_does_not_reorder_fixed_window(self) -> None:
        # A duplicate claim must NOT move the entry to the end with its
        # stale timestamp — that would break _purge_expired's ordering
        # assumption and let a stale claim outlive its TTL (Codex U-D4 P3).
        guard = InMemoryAppliedReportGuard()
        await guard.claim("a")
        await guard.claim("b")
        await guard.claim("a")  # duplicate
        assert list(guard._entries.keys()) == ["a", "b"]

    async def test_satisfies_protocol(self) -> None:
        guard: AppliedReportGuard = InMemoryAppliedReportGuard()
        assert await guard.claim("x") is True


class TestRedisAppliedReportGuard:
    async def test_claim_uses_set_nx_ex(self) -> None:
        redis = _FakeRedis()
        guard = RedisAppliedReportGuard(redis, ttl_seconds=3600)
        assert await guard.claim("erp-1") is True
        call = redis.set_calls[0]
        assert call["nx"] is True
        assert call["ex"] == 3600
        assert call["name"] == "broker:applied_report:erp-1"

    async def test_duplicate_claim_returns_false(self) -> None:
        redis = _FakeRedis()
        guard = RedisAppliedReportGuard(redis)
        assert await guard.claim("erp-1") is True
        assert await guard.claim("erp-1") is False

    async def test_release_deletes_key(self) -> None:
        redis = _FakeRedis()
        guard = RedisAppliedReportGuard(redis)
        await guard.claim("erp-1")
        await guard.release("erp-1")
        assert await guard.claim("erp-1") is True

    async def test_rejects_non_positive_ttl(self) -> None:
        redis = _FakeRedis()
        with pytest.raises(ValueError, match="ttl_seconds"):
            RedisAppliedReportGuard(redis, ttl_seconds=0)

    async def test_empty_report_id_raises(self) -> None:
        redis = _FakeRedis()
        guard = RedisAppliedReportGuard(redis)
        with pytest.raises(ValueError, match="report_id"):
            await guard.claim("")
