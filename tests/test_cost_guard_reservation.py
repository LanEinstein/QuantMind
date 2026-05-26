"""M-005 — cost_guard pre-call reservation + fan-out cap (P1-7-amendment).

The ¥100/day hard cap (¥20 before P1-7-amendment-2026-05-26) is enforced as a
*pre-call reservation* (the crossing call never runs), not a post-hoc
trailing-stop, and the multiplicative fan-out is bounded by
``max_debates_per_day``. The 4 P1-7 ceiling constants keep their locked values;
the 2026-05-26 amendment raised ONLY the daily hard cap.
"""

from __future__ import annotations

import datetime

import pytest

from backend.services import cost_guard
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
    get_daily_budget_state,
    get_daily_reserved,
    get_max_anomaly_llm_per_day,
    get_max_debates_per_day,
    reserve_budget,
    reserve_debate_slot,
    reset_daily_gate_counters,
    settle_budget,
)

_DATE = datetime.date(2026, 5, 24)
_RESERVED_KEY = "llm:usage:2026-05-24:reserved"
_DEBATE_KEY = "llm:debates:2026-05-24"


class FakeRedis:
    """Minimal in-memory Redis for the reservation/debate counters."""

    def __init__(self) -> None:
        self.store: dict[str, float] = {}
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

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
            self.expires.pop(key, None)
        return removed


@pytest.fixture
def patch_spent(monkeypatch: pytest.MonkeyPatch):
    """Control get_daily_spent (cost_guard reads actual spend via cost_probe)."""

    def _set(value: float) -> None:
        async def _spent(_redis, *, today=None):  # noqa: ANN001
            return value

        monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)

    return _set


# --------------------------------------------------------------------------
# reserve_budget / settle_budget
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reserve_under_cap_succeeds(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    res = await reserve_budget(
        redis, agent_name="x", estimated_rmb=1.0, today=_DATE
    )
    assert res.amount_rmb == 1.0
    assert redis.store[_RESERVED_KEY] == pytest.approx(1.0)
    # Reservation lives in the unified llm:usage namespace (amendment §2.4).
    assert res.key == _RESERVED_KEY
    assert _RESERVED_KEY in redis.expires


@pytest.mark.asyncio
async def test_reserve_crossing_cap_is_refused_and_rolled_back(patch_spent) -> None:
    """The call that would cross ¥100 never happens — and the reservation is
    rolled back so it does not wedge the counter (真·预留, not trailing-stop)."""
    patch_spent(99.5)  # ¥100 daily hard cap (P1-7-amendment-2026-05-26)
    redis = FakeRedis()
    with pytest.raises(DailyBudgetExceededError):
        await reserve_budget(redis, agent_name="x", estimated_rmb=1.0, today=_DATE)
    # Rolled back to 0 — a refused reservation leaves no residue.
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_reserve_exactly_at_cap_allowed(patch_spent) -> None:
    patch_spent(99.0)
    redis = FakeRedis()
    res = await reserve_budget(
        redis, agent_name="x", estimated_rmb=1.0, today=_DATE
    )
    assert res.amount_rmb == 1.0  # 99 + 1 == 100, not over


@pytest.mark.asyncio
async def test_settle_releases_reservation(patch_spent) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    res = await reserve_budget(
        redis, agent_name="x", estimated_rmb=1.0, today=_DATE
    )
    await settle_budget(redis, res)
    assert redis.store[_RESERVED_KEY] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_reserve_fail_closed_on_invalid_spent(monkeypatch, patch_spent) -> None:
    async def _bad_spent(_redis, *, today=None):  # noqa: ANN001
        return float("nan")

    monkeypatch.setattr(cost_guard, "get_daily_spent", _bad_spent)
    redis = FakeRedis()
    with pytest.raises(DailyBudgetExceededError):
        await reserve_budget(redis, agent_name="x", estimated_rmb=1.0, today=_DATE)


@pytest.mark.asyncio
async def test_reserve_rejects_negative_estimate(patch_spent) -> None:
    patch_spent(0.0)
    with pytest.raises(ValueError, match="estimated_rmb"):
        await reserve_budget(
            FakeRedis(), agent_name="x", estimated_rmb=-1.0, today=_DATE
        )


@pytest.mark.asyncio
async def test_concurrent_reservations_sum_against_cap(patch_spent) -> None:
    """Two in-flight reservations sum: 0 spent + 99 reserved + 2 → refused."""
    patch_spent(0.0)
    redis = FakeRedis()
    await reserve_budget(redis, agent_name="a", estimated_rmb=99.0, today=_DATE)
    with pytest.raises(DailyBudgetExceededError):
        await reserve_budget(redis, agent_name="b", estimated_rmb=2.0, today=_DATE)
    # The refused second reservation rolled back; the first remains.
    assert redis.store[_RESERVED_KEY] == pytest.approx(99.0)


# --------------------------------------------------------------------------
# reserve_debate_slot — fan-out cap
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debate_slot_caps_at_max(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "2")
    redis = FakeRedis()
    assert await reserve_debate_slot(redis, today=_DATE) == 1
    assert await reserve_debate_slot(redis, today=_DATE) == 2
    with pytest.raises(DailyBudgetExceededError):
        await reserve_debate_slot(redis, today=_DATE)
    # Counter rolled back to the cap (not left at 3).
    assert redis.store[_DEBATE_KEY] == 2


@pytest.mark.asyncio
async def test_reset_daily_gate_counters_clears_gates_not_spend() -> None:
    # The dry-run "fresh trading day" reset clears the transient gate counters
    # (debate-slot, reservation, anomaly) but never the audited spend hash.
    redis = FakeRedis()
    redis.store[_DEBATE_KEY] = 7  # day's debate slots already used
    redis.store[_RESERVED_KEY] = 3.5  # in-flight reservation residue
    redis.store["llm:anomaly:2026-05-24"] = 4
    redis.store["llm:anomaly:dedup:2026-05-24"] = 1
    spend_key = "llm:usage:2026-05-24"  # audited spend — must NOT be cleared
    redis.store[spend_key] = 12.0

    await reset_daily_gate_counters(redis, today=_DATE)

    assert _DEBATE_KEY not in redis.store  # fresh day → can debate again
    assert _RESERVED_KEY not in redis.store
    assert "llm:anomaly:2026-05-24" not in redis.store
    assert "llm:anomaly:dedup:2026-05-24" not in redis.store
    assert redis.store[spend_key] == 12.0  # audited spend preserved


@pytest.mark.asyncio
async def test_reset_daily_gate_counters_fail_open() -> None:
    # A Redis hiccup during reset is logged, never raised (the dry-run runs on).
    class _DeleteRaises(FakeRedis):
        async def delete(self, *keys: str) -> int:
            raise RuntimeError("delete boom")

    await reset_daily_gate_counters(_DeleteRaises(), today=_DATE)  # no raise


@pytest.mark.asyncio
async def test_debate_slot_sets_ttl(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "5")
    redis = FakeRedis()
    await reserve_debate_slot(redis, today=_DATE)
    assert _DEBATE_KEY in redis.expires


# --------------------------------------------------------------------------
# Runtime-read caps (immutable at boot; env override, hot-reload forbidden)
# --------------------------------------------------------------------------


def test_default_caps() -> None:
    assert get_max_debates_per_day() == 8
    assert get_max_anomaly_llm_per_day() == 10


def test_caps_env_override(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "3")
    monkeypatch.setenv("QUANTMIND_MAX_ANOMALY_LLM_PER_DAY", "4")
    assert get_max_debates_per_day() == 3
    assert get_max_anomaly_llm_per_day() == 4


def test_max_debates_floored_at_one(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "0")
    assert get_max_debates_per_day() == 1  # _read_env_float minimum=1.0


# --------------------------------------------------------------------------
# Defensive branches — Redis hiccups must not crash budget-critical paths
# --------------------------------------------------------------------------


class _ExpireRaisesRedis(FakeRedis):
    async def expire(self, key: str, ttl: int) -> bool:
        raise RuntimeError("expire boom")


class _SettleRaisesRedis(FakeRedis):
    async def incrbyfloat(self, key: str, amount: float) -> float:
        if amount < 0:  # the settle (release) path
            raise RuntimeError("settle boom")
        return await super().incrbyfloat(key, amount)


class _NonNumericIncrRedis(FakeRedis):
    async def incrbyfloat(self, key: str, amount: float) -> object:
        await super().incrbyfloat(key, amount)
        return object()  # not float()-able → reserve falls back to estimate


class _NonIntIncrRedis(FakeRedis):
    async def incr(self, key: str) -> object:
        return object()  # not int()-able → debate slot fails closed


@pytest.mark.asyncio
async def test_reserve_tolerates_expire_failure(patch_spent) -> None:
    patch_spent(0.0)
    res = await reserve_budget(
        _ExpireRaisesRedis(), agent_name="x", estimated_rmb=1.0, today=_DATE
    )
    assert res.amount_rmb == 1.0  # TTL is hygiene, not correctness


@pytest.mark.asyncio
async def test_settle_never_raises_on_redis_error(patch_spent) -> None:
    patch_spent(0.0)
    redis = _SettleRaisesRedis()
    res = await reserve_budget(redis, agent_name="x", estimated_rmb=1.0, today=_DATE)
    await settle_budget(redis, res)  # must not raise — TTL backstops the release


@pytest.mark.asyncio
async def test_reserve_falls_back_on_nonnumeric_incr(patch_spent) -> None:
    patch_spent(0.0)
    res = await reserve_budget(
        _NonNumericIncrRedis(), agent_name="x", estimated_rmb=1.0, today=_DATE
    )
    assert res.amount_rmb == 1.0


@pytest.mark.asyncio
async def test_debate_slot_tolerates_expire_failure(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "3")
    assert await reserve_debate_slot(_ExpireRaisesRedis(), today=_DATE) == 1


@pytest.mark.asyncio
async def test_debate_slot_fails_closed_on_nonint_incr(monkeypatch) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "3")
    with pytest.raises(DailyBudgetExceededError):
        await reserve_debate_slot(_NonIntIncrRedis(), today=_DATE)


# --------------------------------------------------------------------------
# codex M-005 P1 — the legacy budget path must also see in-flight reservations
# --------------------------------------------------------------------------


class _GetRedis(FakeRedis):
    """FakeRedis whose GET returns the stored reservation counter (so the
    legacy daily-budget path can read it)."""

    async def get(self, key: str):  # noqa: ANN201
        val = self.store.get(key)
        return None if val is None else str(val)


@pytest.mark.asyncio
async def test_daily_state_includes_in_flight_reservation(patch_spent) -> None:
    """¥99 actual spent + ¥1 in-flight reservation → the shared daily state
    reads ¥100 = hard_breach, so a legacy caller cannot start another call
    while a debate reservation is in flight (codex M-005 P1)."""
    patch_spent(99.0)
    redis = _GetRedis()
    # A debate reserves ¥1 (in flight, not yet settled).
    await reserve_budget(redis, agent_name="debate", estimated_rmb=1.0, today=_DATE)
    # Legacy path now sees 99 + 1 = 100 = hard_breach. Pin ``today`` so the
    # spend + reservation reads stay on the same UTC day as the reservation
    # above (otherwise the test is date-brittle across a midnight rollover).
    state = await get_daily_budget_state(redis, today=_DATE)
    assert state.spent_today == pytest.approx(100.0)
    assert state.status == "hard_breach"
    with pytest.raises(DailyBudgetExceededError):
        await assert_budget_allows(
            redis, agent_name="legacy_scheduler", today=_DATE
        )


@pytest.mark.asyncio
async def test_daily_state_reserved_released_after_settle(patch_spent) -> None:
    patch_spent(99.0)
    redis = _GetRedis()
    res = await reserve_budget(
        redis, agent_name="debate", estimated_rmb=1.0, today=_DATE
    )
    await settle_budget(redis, res)
    # Reservation released → legacy path back to actual ¥99 (soft ≥¥70, not hard).
    state = await get_daily_budget_state(redis)
    assert state.spent_today == pytest.approx(99.0)
    assert state.status == "soft_breach"


@pytest.mark.asyncio
async def test_daily_reserved_fail_open_on_unreadable(monkeypatch, patch_spent) -> None:
    """A GET error on the reserved counter must not crash the budget state —
    it falls back to actual spend only (reserve_budget is the primary guard)."""
    patch_spent(5.0)

    class _GetRaises(FakeRedis):
        async def get(self, key: str):  # noqa: ANN201
            raise RuntimeError("get boom")

    state = await get_daily_budget_state(_GetRaises())
    assert state.spent_today == pytest.approx(5.0)
    assert state.status == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"2.5", 2.5),          # bytes decode path
        ("3.0", 3.0),           # str path
        (None, 0.0),            # absent key
        ("not-a-number", 0.0),  # unparseable → 0
        ("-1.0", 0.0),          # negative → 0
        ("inf", 0.0),           # non-finite → 0
        (True, 0.0),            # bool rejected (would float to 1.0 otherwise)
        (object(), 0.0),        # non-scalar rejected
    ],
)
async def test_get_daily_reserved_parsing(raw, expected) -> None:
    class _R(FakeRedis):
        async def get(self, key: str):  # noqa: ANN201
            return raw

    assert await get_daily_reserved(_R(), today=_DATE) == pytest.approx(expected)
