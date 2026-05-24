"""M-005 — run_shortlist budgeted fan-out cap (P1-7-amendment-2026-05-24).

A converged shortlist triggers exactly ONE 4-agent debate regardless of its
size (no per-candidate fan-out), gated by a pre-call ¥20 reservation and the
max_debates_per_day cap — both checked BEFORE any LLM call, so a refused
budget means the crossing debate never runs.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.agents_team.graph import run_shortlist
from backend.agents_team.state import DECISION_BUILD_OK, CandidateBrief, TeamContext
from backend.services import cost_guard
from backend.services.cost_guard import DailyBudgetExceededError
from tests.agents_team.conftest import FakeRouter
from tests.test_cost_guard_reservation import FakeRedis

_RESERVED_KEY_PREFIX = "llm:usage"


def _shortlist(n: int) -> list[CandidateBrief]:
    return [
        CandidateBrief(
            code=f"{510300 + i:06d}", name=f"c{i}",
            proposed_volume=200, proposed_limit_price=4.5,
        )
        for i in range(n)
    ]


@pytest.fixture
def patch_spent(monkeypatch: pytest.MonkeyPatch):
    def _set(value: float) -> None:
        async def _spent(_redis, *, today=None):  # noqa: ANN001
            return value

        monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)

    return _set


@pytest.mark.asyncio
async def test_twenty_candidates_run_one_debate(
    buy_context: TeamContext, patch_spent
) -> None:
    """20 candidates → ONE debate (4 LLM calls), not 20 (fan-out cap)."""
    patch_spent(0.0)
    router = FakeRouter(action="买入")
    ctx = dataclasses.replace(buy_context, llm_router=router)
    redis = FakeRedis()
    result = await run_shortlist(ctx, _shortlist(20), redis_client=redis)
    # Exactly one debate = 4 LLM calls (3 analysts + fund_manager).
    assert sorted(router.calls) == [
        "fund_manager", "fundamental_analyst", "risk_officer", "technical_analyst",
    ]
    assert result.debate_slot == 1
    assert result.candidate.code == "510300"  # lead (top-ranked)
    assert result.state["decision"] == DECISION_BUILD_OK


@pytest.mark.asyncio
async def test_reservation_settled_after_debate(
    buy_context: TeamContext, patch_spent
) -> None:
    patch_spent(0.0)
    redis = FakeRedis()
    await run_shortlist(buy_context, _shortlist(3), redis_client=redis)
    # The reservation is released in finally — no residue on the counter.
    reserved_keys = [k for k in redis.store if k.endswith(":reserved")]
    assert reserved_keys, "reservation key should exist in llm:usage namespace"
    assert all(k.startswith(_RESERVED_KEY_PREFIX) for k in reserved_keys)
    assert redis.store[reserved_keys[0]] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_over_budget_refuses_before_any_llm_call(
    buy_context: TeamContext, patch_spent
) -> None:
    """spent 19.5 + estimate 1.0 > ¥20 → refused; no LLM call happens."""
    patch_spent(19.5)
    router = FakeRouter(action="买入")
    ctx = dataclasses.replace(buy_context, llm_router=router)
    redis = FakeRedis()
    with pytest.raises(DailyBudgetExceededError):
        await run_shortlist(ctx, _shortlist(5), redis_client=redis)
    assert router.calls == []  # crossing debate never ran


@pytest.mark.asyncio
async def test_debate_cap_refuses_extra_runs_without_leaking_reservation(
    buy_context: TeamContext, patch_spent, monkeypatch
) -> None:
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "1")
    patch_spent(0.0)
    redis = FakeRedis()
    # First shortlist run consumes the only debate slot.
    await run_shortlist(buy_context, _shortlist(2), redis_client=redis)
    # Second run: budget reserved, then debate-slot cap refuses → no LLM call,
    # and the budget reservation must be settled (no leak).
    router2 = FakeRouter(action="买入")
    ctx2 = dataclasses.replace(buy_context, llm_router=router2)
    with pytest.raises(DailyBudgetExceededError):
        await run_shortlist(ctx2, _shortlist(2), redis_client=redis)
    assert router2.calls == []
    reserved_keys = [k for k in redis.store if k.endswith(":reserved")]
    assert redis.store[reserved_keys[0]] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_empty_shortlist_raises(
    buy_context: TeamContext, patch_spent
) -> None:
    patch_spent(0.0)
    with pytest.raises(ValueError, match="non-empty shortlist"):
        await run_shortlist(buy_context, [], redis_client=FakeRedis())
