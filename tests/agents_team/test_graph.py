"""agents_team graph tests (Phase M-002): end-to-end run + local checkpointer.

Verifies the graph compiles + runs end-to-end on deterministic stubs (no LLM),
that the local SQLite checkpointer round-trips the terminal state, and that the
end-to-end decision matches the gate matrix.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.agents_team.graph import (
    build_team_graph,
    open_sqlite_checkpointer,
    run_team,
)
from backend.agents_team.state import (
    DECISION_BUILD_OK,
    DECISION_HOLD,
    DECISION_REJECTED,
    CandidateBrief,
    TeamContext,
)
from tests.agents_team.conftest import FakeRouter


@pytest.mark.asyncio
async def test_graph_runs_end_to_end_build_ok(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    result = await run_team(buy_context, candidate)
    assert result["decision"] == DECISION_BUILD_OK
    # All four mandatory agents produced output + debate ran.
    assert result["fundamental_report"]
    assert result["technical_report"]
    assert result["risk_officer_report"]
    assert result["fund_manager_reasoning"]
    assert result["debate_round_count"] >= 1
    assert result["direction"] == "BUY"
    assert result["risk_passed"] is True


@pytest.mark.asyncio
async def test_graph_hold_when_fund_manager_holds(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    hold_ctx = dataclasses.replace(buy_context, llm_router=FakeRouter(action="持有"))
    result = await run_team(hold_ctx, candidate)
    assert result["decision"] == DECISION_HOLD
    # HOLD never routes → risk gate not run as a pass.
    assert result["risk_passed"] is False


@pytest.mark.asyncio
async def test_local_sqlite_checkpointer_persists_terminal_state(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    async with open_sqlite_checkpointer(":memory:") as cp:
        graph = build_team_graph(buy_context, checkpointer=cp)
        config = {"configurable": {"thread_id": candidate.code}}
        out = await graph.ainvoke(
            {
                "candidate_code": candidate.code,
                "candidate_name": candidate.name,
                "proposed_volume": candidate.proposed_volume,
                "proposed_limit_price": candidate.proposed_limit_price,
                "debate_round_count": 0,
                "direction": "",
            },
            config,
        )
        assert out["decision"] == DECISION_BUILD_OK
        # Checkpointer round-trips the terminal state under the same thread.
        snap = await graph.aget_state(config)
        assert snap.values["decision"] == DECISION_BUILD_OK
        assert snap.config["configurable"].get("checkpoint_id")


@pytest.mark.asyncio
async def test_checkpointer_is_local_sqlite(
    buy_context: TeamContext, tmp_path
) -> None:
    """The checkpointer writes a plain local SQLite file — no network/SaaS."""
    db = tmp_path / "team.sqlite"
    async with open_sqlite_checkpointer(db) as cp:
        graph = build_team_graph(buy_context, checkpointer=cp)
        await graph.ainvoke(
            {
                "candidate_code": "510300",
                "candidate_name": "x",
                "proposed_volume": 200,
                "proposed_limit_price": 4.5,
                "debate_round_count": 0,
                "direction": "",
            },
            {"configurable": {"thread_id": "510300"}},
        )
    assert db.exists()
    assert db.read_bytes()[:16].startswith(b"SQLite format 3")


@pytest.mark.asyncio
async def test_graph_rejects_when_risk_context_incomplete(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    """End-to-end: a BUY with incomplete 14-check context is REJECTED, never a
    legacy-7-check BUILD_OK (codex M-002 P1)."""
    ctx = dataclasses.replace(buy_context, daily_state=None, stock_meta=None)
    result = await run_team(ctx, candidate)
    assert result["decision"] == DECISION_REJECTED
    assert result["decision_reason"] == "risk:risk_context_incomplete"


@pytest.mark.asyncio
async def test_run_team_with_checkpointer_uses_candidate_thread(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    async with open_sqlite_checkpointer(":memory:") as cp:
        result = await run_team(buy_context, candidate, checkpointer=cp)
        assert result["decision"] == DECISION_BUILD_OK


@pytest.mark.asyncio
async def test_graph_runs_without_checkpointer(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    # No checkpointer → no thread_id needed, still fully functional.
    graph = build_team_graph(buy_context, checkpointer=None)
    out = await graph.ainvoke(
        {
            "candidate_code": candidate.code,
            "candidate_name": candidate.name,
            "proposed_volume": candidate.proposed_volume,
            "proposed_limit_price": candidate.proposed_limit_price,
            "debate_round_count": 0,
            "direction": "",
        },
        {},
    )
    assert out["decision"] == DECISION_BUILD_OK


@pytest.mark.asyncio
async def test_graph_degrades_hold_when_mandatory_agent_missing(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    """A mandatory agent returning an empty report → HOLD (P0-10 §2.3)."""
    ctx = dataclasses.replace(
        buy_context, llm_router=FakeRouter(empty_agents=frozenset({"risk_officer"}))
    )
    result = await run_team(ctx, candidate)
    assert result["decision"] == DECISION_HOLD
    assert "mandatory_agent_missing" in result["decision_reason"]
    assert "risk_officer" in result["decision_reason"]


@pytest.mark.asyncio
async def test_graph_makes_exactly_four_llm_calls(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    """MVP cost guard: one debate per candidate = 4 LLM calls (3 analysts +
    fund_manager); the debate fan-in is deterministic (P1-7-amendment fan-out
    cap rationale — M-005 enforces the budget reserve)."""
    router = FakeRouter(action="买入")
    ctx = dataclasses.replace(buy_context, llm_router=router)
    await run_team(ctx, candidate)
    assert sorted(router.calls) == [
        "fund_manager",
        "fundamental_analyst",
        "risk_officer",
        "technical_analyst",
    ]


@pytest.mark.asyncio
async def test_graph_fail_closed_hold_without_router(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    """No router → every agent empty → all mandatory missing → HOLD."""
    ctx = dataclasses.replace(buy_context, llm_router=None)
    result = await run_team(ctx, candidate)
    assert result["decision"] == DECISION_HOLD
    assert "mandatory_agent_missing" in result["decision_reason"]


@pytest.mark.asyncio
async def test_graph_whitespace_only_agent_cannot_reach_build_ok(
    buy_context: TeamContext, candidate: CandidateBrief
) -> None:
    """A whitespace-only mandatory report degrades to HOLD, never BUILD_OK
    (codex M-003 P2 — fail closed end-to-end)."""
    ctx = dataclasses.replace(
        buy_context,
        llm_router=FakeRouter(whitespace_agents=frozenset({"technical_analyst"})),
    )
    result = await run_team(ctx, candidate)
    assert result["decision"] == DECISION_HOLD
    assert "technical_analyst" in result["decision_reason"]
