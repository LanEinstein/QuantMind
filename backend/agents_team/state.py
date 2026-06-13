"""Graph state + injected context for the agents_team LangGraph (Phase M-002).

Two deliberately separated carriers:

* :class:`TeamState` — the **checkpointed** LangGraph state. Every field is a
  JSON-serializable primitive so the local SQLite checkpointer round-trips it
  without custom serde. The LLM agent nodes write ONLY the free-text report /
  reasoning fields + the ``direction`` proposal (``fund_manager`` is the sole
  BUY/SELL/HOLD proposer). The numeric order fields (``proposed_volume`` /
  ``proposed_limit_price``) are set at graph entry from a deterministic
  position-sizer — an LLM node never writes them (R0 §4 red line B).

* :class:`TeamContext` — the **injected, non-serialized** bundle the
  deterministic tool nodes (risk gate, builder) consume: the pure
  :class:`RiskEngine`, the account / positions / price snapshot, etc. It is
  passed into nodes by closure (like the legacy ``AnalysisServices``), so the
  heavy Pydantic / engine objects never enter the checkpointed state and an LLM
  node has no edge that can write them.

M-003: the agent nodes are real LLM calls (4 mandatory agents + a single
deterministic debate fan-in round) reached through the injected
:class:`LLMCompleter` (``ctx.llm_router``). ``fund_manager`` is the sole
BUY/SELL/HOLD proposer and also emits the LLM-only ``FundManagerOutput`` bridge.
The deterministic tool nodes (risk gate, builder) read only the numeric state,
so no LLM edge writes the decision path (R0 §4). Wiring the builder to
``instruction_plan_builder.assemble_plan`` with a full ``AssemblyContext`` is
the N-005 end-to-end gate's job — agents_team never constructs an
``InstructionPlan`` itself (single construction point, M-004).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

from backend.broker.models import AccountInfo, Position  # noqa: TID251
from backend.risk.daily_state import DailyTradingState  # noqa: TID251
from backend.risk.engine import RiskEngine  # noqa: TID251
from backend.risk.stock_meta import StockMetadata  # noqa: TID251


@runtime_checkable
class LLMCompleter(Protocol):
    """Minimal async chat-completion surface the agent nodes depend on.

    The production :class:`backend.llm.router.LLMRouter` satisfies this
    Protocol; tests inject a deterministic fake. Defined here (not in
    ``agents.py``) so :class:`TeamContext` can type the injected router
    without importing the agent module (avoids a circular import).
    """

    async def complete(
        self,
        agent_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any: ...

# The four mandatory agents (P0-10 §2.3). Any missing → degrade HOLD.
MANDATORY_AGENTS: tuple[str, ...] = (
    "fundamental_analyst",
    "technical_analyst",
    "risk_officer",
    "fund_manager",
)

# Terminal builder decisions (skeleton — M-003/M-004 add the constructed plan).
DECISION_BUILD_OK = "BUILD_OK"
DECISION_HOLD = "HOLD"
DECISION_REJECTED = "REJECTED"


class TeamState(TypedDict, total=False):
    """Checkpointed LangGraph state (all JSON-serializable).

    ``total=False`` so nodes read with ``.get`` defaults and the initial state
    need not pre-populate every key.
    """

    # Deterministic inputs (set at entry; never written by an LLM node).
    candidate_code: str
    candidate_name: str
    proposed_volume: int
    proposed_limit_price: float

    # LLM-writable text outputs (real LLM in M-003).
    fundamental_report: str
    technical_report: str
    risk_officer_report: str
    fund_manager_reasoning: str
    debate_history: str
    debate_round_count: int

    # fund_manager's BUY/SELL/HOLD proposal (sole proposer of direction).
    direction: str
    # False iff the fund_manager JSON envelope failed to parse → forced
    # HOLD when bridged to FundManagerOutput (P0-3 §2 redline 6).
    fund_manager_parse_ok: bool

    # Deterministic tool-node outputs.
    risk_passed: bool
    risk_rule: str
    risk_message: str
    decision: str
    decision_reason: str


@dataclass(frozen=True)
class TeamContext:
    """Injected, non-serialized bundle for the agent + tool nodes.

    Carries the pure :class:`RiskEngine` + the risk-check inputs and the
    injected :class:`LLMCompleter` (``llm_router``) the agent nodes call.
    ``now`` is injectable for deterministic tests. The heavy engine /
    account / router objects never enter the checkpointed ``TeamState``,
    so an LLM node has no edge that can write the decision path.

    ``llm_router`` is ``None``-safe: a missing router makes every agent
    emit an empty report, which the builder's mandatory-agent gate turns
    into a fail-closed HOLD (never a silent pass).
    """

    risk_engine: RiskEngine
    account: AccountInfo
    positions: tuple[Position, ...] = ()
    prev_close: float | None = None
    daily_state: DailyTradingState | None = None
    stock_meta: StockMetadata | None = None
    concentration_exception: bool = False
    now: dt.datetime | None = None
    llm_router: LLMCompleter | None = None
    # O-004: off-market evidence (MiroFish sector forecast + multi-domain
    # news/index digest) injected as analyst/fund_manager briefing TEXT.
    # Empty by default (MVP / offline) → the debate is bit-identical to
    # before. It is evidence for deliberation only; the LLM still writes
    # only the four allowed text fields, never a decision/numeric field.
    off_market_context: str = ""


@dataclass(frozen=True)
class CandidateBrief:
    """Run input: one selected candidate + its deterministic order numbers.

    ``proposed_volume`` / ``proposed_limit_price`` come from a deterministic
    position-sizer (non-LLM); the graph carries them through to the risk gate
    unchanged.
    """

    code: str
    name: str
    proposed_volume: int
    proposed_limit_price: float


def make_initial_state(candidate: CandidateBrief) -> TeamState:
    """Build the entry state for one candidate run (text fields start empty)."""
    return TeamState(
        candidate_code=candidate.code,
        candidate_name=candidate.name,
        proposed_volume=candidate.proposed_volume,
        proposed_limit_price=candidate.proposed_limit_price,
        fundamental_report="",
        technical_report="",
        risk_officer_report="",
        fund_manager_reasoning="",
        debate_history="",
        debate_round_count=0,
        direction="",
        fund_manager_parse_ok=False,
        risk_passed=False,
        risk_rule="",
        risk_message="",
        decision="",
        decision_reason="",
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
    "make_initial_state",
]
