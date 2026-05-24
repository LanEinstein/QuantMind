"""agents_team LangGraph orchestration (Phase M-002).

Builds the two-line MVP debate graph and compiles it with a **local SQLite
checkpointer** (no hosted SaaS — P0-10-amendment-2026-05-24 / agents_team
CLAUDE.md). Topology:

    START → fundamental_analyst ┐
            technical_analyst    ├─→ debate → fund_manager → risk_gate → builder → END
            risk_officer        ┘

The three analysts run in parallel (each writes its own report key, so no
concurrent-write reducer is needed); the debate fans them in. ``fund_manager``
is the sole BUY/SELL/HOLD proposer. ``risk_gate`` + ``builder`` are pure
deterministic tool nodes — there is **no edge by which an LLM/agent node writes
their numeric order / decision output** (R0 §4): the analysts and fund_manager
only write free-text + the direction proposal, and the tool nodes read the
deterministic numeric state set at entry.

M-003: the agent nodes are real LLM calls (``agents.py``) reached through the
injected ``ctx.llm_router``; the debate is a single deterministic fan-in round.
The N-005 end-to-end gate wires ``builder``'s BUILD_OK signal into
``instruction_plan_builder.assemble_plan`` (the single construction point) via
the LLM-only ``FundManagerOutput`` bridge.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.agents_team.agents import (
    debate_node,
    fund_manager_node,
    fundamental_analyst_node,
    risk_officer_node,
    technical_analyst_node,
)
from backend.agents_team.nodes import (
    builder_node,
    risk_gate_node,
)
from backend.agents_team.state import (
    CandidateBrief,
    TeamContext,
    TeamState,
    make_initial_state,
)
from backend.services.cost_guard import (  # noqa: TID251
    reserve_budget,
    reserve_debate_slot,
    settle_budget,
)

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="agents_team.graph")

# Conservative per-debate cost estimate reserved BEFORE any LLM call
# (actual 4-agent debate ≈ ¥0.4-0.8; reserve ¥1.0 of headroom). The
# reservation — not this estimate's accuracy — is what keeps spend under the
# ¥20 hard cap: settle releases it and track_usage records the real cost.
_DEBATE_COST_ESTIMATE_RMB = 1.0


def _bind(node: Any, ctx: TeamContext) -> Any:
    """Inject ``ctx`` into a ``node(state, ctx)`` callable (async or sync).

    ``functools.partial`` preserves coroutine-function-ness for async nodes, so
    LangGraph awaits them and runs sync tool nodes directly.
    """
    return functools.partial(node, ctx=ctx)


def build_team_graph(
    ctx: TeamContext,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Build + compile the agents_team graph with ``ctx`` injected.

    Args:
        ctx: Injected deterministic context (RiskEngine + risk inputs +
            skeleton stub knobs). Never enters the checkpointed state.
        checkpointer: Optional local checkpointer. When provided, ``ainvoke``
            must pass a ``thread_id`` in its config. ``None`` runs without
            persistence (still fully functional).

    Returns:
        A compiled LangGraph app ready for ``ainvoke``.
    """
    graph = StateGraph(TeamState)

    graph.add_node("fundamental_analyst", _bind(fundamental_analyst_node, ctx))
    graph.add_node("technical_analyst", _bind(technical_analyst_node, ctx))
    graph.add_node("risk_officer", _bind(risk_officer_node, ctx))
    graph.add_node("debate", _bind(debate_node, ctx))
    graph.add_node("fund_manager", _bind(fund_manager_node, ctx))
    graph.add_node("risk_gate", _bind(risk_gate_node, ctx))
    graph.add_node("builder", _bind(builder_node, ctx))

    # START → 3 analysts in parallel.
    graph.add_edge(START, "fundamental_analyst")
    graph.add_edge(START, "technical_analyst")
    graph.add_edge(START, "risk_officer")
    # 3 analysts fan into the debate (LangGraph waits for all incoming edges).
    graph.add_edge("fundamental_analyst", "debate")
    graph.add_edge("technical_analyst", "debate")
    graph.add_edge("risk_officer", "debate")
    # Decision → deterministic tool gates → END.
    graph.add_edge("debate", "fund_manager")
    graph.add_edge("fund_manager", "risk_gate")
    graph.add_edge("risk_gate", "builder")
    graph.add_edge("builder", END)

    return graph.compile(checkpointer=checkpointer)


@asynccontextmanager
async def open_sqlite_checkpointer(
    db_path: str | Path,
) -> AsyncIterator[AsyncSqliteSaver]:
    """Open a local SQLite checkpointer (no hosted SaaS).

    ``db_path`` may be ``":memory:"`` for tests or a filesystem path for
    persistent per-agent memory across restarts. Usage::

        async with open_sqlite_checkpointer("data/agents_team.sqlite") as cp:
            graph = build_team_graph(ctx, checkpointer=cp)
            await graph.ainvoke(state, {"configurable": {"thread_id": tid}})
    """
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        yield saver


async def run_team(
    ctx: TeamContext,
    candidate: CandidateBrief,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    thread_id: str | None = None,
) -> TeamState:
    """Run the team graph for one candidate; return the terminal state.

    When ``checkpointer`` is provided a ``thread_id`` is required by LangGraph
    (defaults to the candidate code).
    """
    graph = build_team_graph(ctx, checkpointer=checkpointer)
    config: dict[str, Any] = {}
    if checkpointer is not None:
        config = {"configurable": {"thread_id": thread_id or candidate.code}}
    initial = make_initial_state(candidate)
    log.info("team_run_started", code=candidate.code)
    result = await graph.ainvoke(initial, config)
    log.info(
        "team_run_completed",
        code=candidate.code,
        decision=result.get("decision"),
        reason=result.get("decision_reason"),
    )
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class ShortlistDebateResult:
    """Outcome of one budgeted shortlist debate (``run_shortlist``).

    ``candidate`` is the candidate that was actually debated (MVP: the
    top-ranked one); ``state`` is its terminal :class:`TeamState`;
    ``debate_slot`` is the 1-based debate index claimed for the UTC day.
    """

    candidate: CandidateBrief
    state: TeamState
    debate_slot: int


async def run_shortlist(
    ctx: TeamContext,
    shortlist: Sequence[CandidateBrief],
    *,
    redis_client: redis.asyncio.Redis,
    checkpointer: BaseCheckpointSaver | None = None,
    thread_id: str | None = None,
) -> ShortlistDebateResult:
    """Run exactly ONE budgeted 4-agent debate for a converged shortlist.

    P1-7-amendment-2026-05-24 (fan-out cap): a debate runs **once per daily
    shortlist, never once per candidate** — a 20-candidate shortlist still
    triggers a single debate. Order of guards (all BEFORE any LLM call, so a
    refused budget means the crossing call never happens):

    1. ``reserve_budget`` — pre-call ¥20 hard-cap reservation (raises
       :class:`DailyBudgetExceededError` and runs nothing if it would cross).
    2. ``reserve_debate_slot`` — the ``max_debates_per_day`` fan-out cap
       (raises if the day's debate budget is exhausted).
    3. one ``run_team`` debate on the lead (top-ranked) candidate; the
       reservation is always settled in ``finally`` (no leaked reservation,
       even on a mid-run error). MVP debates the lead candidate only; richer
       multi-candidate deliberation in one debate is a Phase T enhancement.

    Raises:
        ValueError: empty shortlist.
        DailyBudgetExceededError: ¥20 reservation or debate-slot cap refused.
    """
    if not shortlist:
        raise ValueError("run_shortlist requires a non-empty shortlist")

    reservation = await reserve_budget(
        redis_client,
        agent_name="agents_team:debate",
        estimated_rmb=_DEBATE_COST_ESTIMATE_RMB,
    )
    try:
        debate_slot = await reserve_debate_slot(redis_client)
        lead = shortlist[0]
        log.info(
            "shortlist_debate_started",
            shortlist_size=len(shortlist),
            lead_code=lead.code,
            debate_slot=debate_slot,
        )
        state = await run_team(
            ctx, lead, checkpointer=checkpointer, thread_id=thread_id
        )
    finally:
        await settle_budget(redis_client, reservation)
    return ShortlistDebateResult(
        candidate=lead, state=state, debate_slot=debate_slot
    )


__all__ = [
    "ShortlistDebateResult",
    "build_team_graph",
    "open_sqlite_checkpointer",
    "run_shortlist",
    "run_team",
]
