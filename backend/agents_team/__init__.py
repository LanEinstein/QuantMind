"""agents_team — LangGraph multi-agent orchestration (Phase M / Phase T).

The two-line MVP debate graph: 4 mandatory agents (single-round debate, M-003)
with RiskEngine + InstructionPlanBuilder as pure deterministic tool nodes that
no LLM edge can write (R0 §4). Compiled with a local SQLite checkpointer —
no hosted SaaS. fund_manager is the sole BUY/SELL/HOLD proposer; numeric order
fields are always derived deterministically, never from LLM output.
"""

from backend.agents_team.agents import to_fund_manager_output
from backend.agents_team.graph import (
    build_team_graph,
    open_sqlite_checkpointer,
    run_team,
)
from backend.agents_team.state import (
    DECISION_BUILD_OK,
    DECISION_HOLD,
    DECISION_REJECTED,
    MANDATORY_AGENTS,
    CandidateBrief,
    LLMCompleter,
    TeamContext,
    TeamState,
    make_initial_state,
)

__all__ = [
    "DECISION_BUILD_OK",
    "DECISION_HOLD",
    "DECISION_REJECTED",
    "MANDATORY_AGENTS",
    "CandidateBrief",
    "LLMCompleter",
    "TeamContext",
    "TeamState",
    "build_team_graph",
    "make_initial_state",
    "open_sqlite_checkpointer",
    "run_team",
    "to_fund_manager_output",
]
