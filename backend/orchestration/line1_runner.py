"""Line-1 production runner (Phase U-C1).

The Line-1 (full-market stock selection) production entry point. It composes
the proven ``tests/monitoring/test_mvp_e2e.py`` Line-1 chain into one runner
that the U-D1 scheduler invokes once per trading day at 09:00 against the
**T-1 EOD** market frame (assembled by :class:`backend.orchestration.
line1_frame.Line1FrameAssembler`, U-B1):

    screen → budget tier → candidate select → ONE 4-agent debate →
    to_fund_manager_output → assemble_plan (14-check single construction
    point) → RouteCoordinator

Cost (P1-7-amendment-2026-05-24): exactly ONE 4-agent debate runs per daily
shortlist (never per candidate); ``run_shortlist`` reserves the ¥20 hard-cap
budget BEFORE any LLM call (真·预留) + claims the fan-out-cap debate slot.

LLM red line (orchestration isolation): this module imports NO
``backend.{api,broker,risk,llm,agents,mirofish,data}``. The heavy risk /
broker objects (RiskEngine, AccountInfo, positions, …) the debate +
assemble step consume are built by the caller's :class:`Line1ContextProvider`
— the U-D1 scheduler / ``main.py`` legitimately import those packages and
inject the per-run context. The runner orchestrates the *flow* using only
the flow-level types (snapshot, plan, signal). ``agents_team`` IS an allowed
import (it is the decision path's debate orchestrator, not a Phase-X
subpackage), so the single debate edge stays direct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.agents_team.agents import to_fund_manager_output
from backend.agents_team.graph import run_shortlist
from backend.agents_team.state import CandidateBrief, TeamContext, TeamState
from backend.budget_policy.policy import (
    BudgetCandidate,
    BudgetTier,
    BudgetTierPolicy,
)
from backend.candidate_selector.selector import CandidateSelector, QuantCandidate
from backend.integrations.feishu.renderer import BuySignalTemplate, MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.orchestration.instruction_dispatcher import OutboundSignal
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.screening.screener import CandidateRow, Screener
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    BuilderDegrade,
    BuilderPlan,
    InstructionPlanBuilder,
    MandatoryAgentRecords,
)
from backend.services.ledger import DecisionLedgerService

log = structlog.get_logger(component="orchestration.line1_runner")


class Line1Outcome(StrEnum):
    """Terminal outcome of one Line-1 run (audit-grade)."""

    ROUTED = "routed"
    """A VALIDATED BUY was rendered + handed to the RouteCoordinator."""
    NO_COMPLIANT_TRADE = "no_compliant_trade"
    """The budget tier left no affordable candidate (first-class, P0-7-amend)."""
    EMPTY_UNIVERSE = "empty_universe"
    """The screen produced no candidate (all excluded)."""
    EMPTY_SHORTLIST = "empty_shortlist"
    """No candidate survived the affordability + selection filter."""
    HOLD = "hold"
    """fund_manager proposed HOLD (or parse failure) — never routed (§2.7)."""
    REJECTED = "rejected"
    """The RiskEngine 14-check rejected the BUY — no signal sent."""
    NON_BUY_DISCARDED = "non_buy_discarded"
    """fund_manager proposed a VALIDATED non-BUY (SELL) — discarded; Line-1
    only routes BUY (SELL is Line-2's deterministic monitoring job)."""
    DEGRADED = "degraded"
    """4-agent gate degraded (a mandatory agent was silent) — fail-closed HOLD."""
    EARLY_RETURN = "early_return"
    """A Builder five-early-return freeze blocked routing (data quality, etc.)."""


@runtime_checkable
class AssemblyContextFactory(Protocol):
    """Finishes the AssemblyContext after the debate (LLM-derived fields).

    The provider closes over the lead + the heavy risk/broker objects; the
    runner supplies the post-debate ``debate_round_count`` + correlation ids.
    """

    def __call__(
        self,
        *,
        signal_id: str,
        seq: int,
        debate_round_count: int,
        analysis_record_id: str,
        risk_validation_id: str,
    ) -> AssemblyContext: ...


@dataclass(frozen=True)
class Line1LeadContext:
    """Per-lead risk/broker context, built by the caller's provider.

    The runner is forbidden to import backend.{risk,broker,data}; the
    provider (U-D1 scheduler / ``main.py``) supplies the heavy objects
    pre-built. ``make_assembly_context`` is invoked AFTER the debate.
    """

    brief: CandidateBrief
    team_context: TeamContext
    make_assembly_context: AssemblyContextFactory


@runtime_checkable
class Line1ContextProvider(Protocol):
    """Caller-supplied bridge to the risk/broker/data objects the runner must
    not import (orchestration isolation, R0 §4 / kickoff §5).

    Implemented by the U-D1 scheduler (which pulls the live account/positions
    from the MockBroker + builds the RiskEngine). Tests inject a fake.
    """

    @property
    def available_cash(self) -> float:
        """Investable cash for the budget tier (``account.available_cash``)."""
        ...

    def per_lot_cost(self, code: str, last_price: float) -> float:
        """One A-share lot cost in ¥ (``last_price × lot_size``)."""
        ...

    def build_lead_context(
        self, lead: CandidateRow, *, concentration_exception: bool = False
    ) -> Line1LeadContext:
        """Build the TeamContext + AssemblyContext factory for the lead.

        ``concentration_exception`` is the lead's budget-tier flag (over-15%
        whitelisted ETF in Micro/Small). The provider threads it into BOTH the
        debate ``TeamContext`` (so the debate's risk-gate node does not record
        a REJECTED decision that contradicts the routed plan) AND the
        ``AssemblyContext`` (the authoritative 14-check). RiskEngine still
        re-derives ETF + whitelist + ≤max_lots, so the flag never bypasses the
        cap on its own (U-C4 / codex P2).
        """
        ...


@dataclass(frozen=True)
class Line1RunResult:
    """Audit-grade summary of one Line-1 run."""

    signal_id: str
    outcome: Line1Outcome
    tier: BudgetTier | None = None
    shortlist: tuple[str, ...] = ()
    lead_code: str | None = None
    route_outcome: RouteOutcome | None = None
    plan: InstructionPlan | None = None


class Line1Runner:
    """Compose the Line-1 chain into one daily production run."""

    def __init__(
        self,
        *,
        screener: Screener,
        budget_policy: BudgetTierPolicy,
        selector: CandidateSelector,
        builder: InstructionPlanBuilder,
        renderer: MessageRenderer,
        coordinator: RouteCoordinator,
        ledger: DecisionLedgerService,
        redis_client: Any,
        pilot: bool = False,
    ) -> None:
        self._screener = screener
        self._budget = budget_policy
        self._selector = selector
        self._builder = builder
        self._renderer = renderer
        self._coordinator = coordinator
        self._ledger = ledger
        self._redis = redis_client
        # PILOT go-live tier → prepend the "模拟盘·人工·试点" banner to every
        # order-bearing Feishu message (P0-6-amendment-2026-05-25 §2.3).
        self._pilot = pilot

    async def run(
        self,
        *,
        frame: MarketDataSnapshot,
        provider: Line1ContextProvider,
        now: datetime,
        signal_id: str | None = None,
    ) -> Line1RunResult:
        """Run Line-1 once on the T-1 frame; route at most one BUY."""
        sid = signal_id or f"SIG-{frame.trade_date}-line1"

        # 1. Full-market screen (PIT, deterministic, 0 LLM).
        screen = self._screener.screen(frame, sid)
        if not screen.candidates:
            return self._short(sid, Line1Outcome.EMPTY_UNIVERSE)

        # 2. Budget-tier affordability gate (upstream of the LLM + RiskEngine).
        by_code = {c.code: c for c in screen.candidates}
        budget_cands = [
            BudgetCandidate(
                code=c.code, per_lot_cost=provider.per_lot_cost(c.code, c.last_price)
            )
            for c in screen.candidates
        ]
        assessment = self._budget.assess(provider.available_cash, budget_cands)
        if assessment.no_compliant_trade:
            return self._short(
                sid, Line1Outcome.NO_COMPLIANT_TRADE, tier=assessment.tier
            )
        affordable_codes = {a.code for a in assessment.affordable}
        # Per-code affordability so the lead's budget-tier concentration flag
        # (over-15% whitelisted ETF in Micro/Small) threads into the 14-check
        # single construction point (U-C4). RiskEngine still re-validates it.
        afford_by_code = {a.code: a for a in assessment.affordable}

        # 3. Deterministic selection over the affordable quant set.
        quant = [
            QuantCandidate(code=c.code, score=c.score)
            for c in screen.candidates
            if c.code in affordable_codes
        ]
        selection = self._selector.select(quant)
        if not selection.shortlist:
            return self._short(sid, Line1Outcome.EMPTY_SHORTLIST, tier=assessment.tier)

        # 4. ONE 4-agent debate (真·预留 + fan-out cap inside run_shortlist).
        lead_code = selection.shortlist[0]
        lead = by_code[lead_code]
        lead_afford = afford_by_code.get(lead_code)
        lead_concentration_exception = bool(
            lead_afford is not None and lead_afford.concentration_exception
        )
        # The flag enters at the single point (build_lead_context): the
        # provider threads it into BOTH the debate TeamContext and the
        # AssemblyContext so the debate decision cannot contradict the routed
        # plan (codex U-C4 P2).
        lead_ctx = provider.build_lead_context(
            lead, concentration_exception=lead_concentration_exception
        )
        debate = await run_shortlist(
            lead_ctx.team_context, [lead_ctx.brief], redis_client=self._redis
        )

        # 5. Single construction point (14-check) via the LLM-only bridge.
        fmo = to_fund_manager_output(debate.state)
        records = _mandatory_records_from_state(debate.state, sid)
        context = lead_ctx.make_assembly_context(
            signal_id=sid,
            seq=1,
            debate_round_count=int(debate.state.get("debate_round_count", 0)),
            analysis_record_id=f"{sid}-debate",
            risk_validation_id=f"{sid}-rv",
        )
        built = await self._builder.assemble_plan(
            fund_manager_output=fmo, mandatory_records=records, context=context
        )

        # 6. Route at most one VALIDATED BUY.
        return await self._route(
            built,
            signal_id=sid,
            lead_code=lead_code,
            shortlist=selection.shortlist,
            tier=assessment.tier,
            now=now,
        )

    async def _route(
        self,
        built: object,
        *,
        signal_id: str,
        lead_code: str,
        shortlist: tuple[str, ...],
        tier: BudgetTier,
        now: datetime,
    ) -> Line1RunResult:
        """Render + route a VALIDATED BUY; classify every other terminal."""
        common: dict[str, Any] = {
            "signal_id": signal_id,
            "tier": tier,
            "shortlist": shortlist,
            "lead_code": lead_code,
        }
        if isinstance(built, BuilderDegrade):
            log.info(
                "line1_degraded", signal_id=signal_id, reason=built.reason_namespace
            )
            return Line1RunResult(outcome=Line1Outcome.DEGRADED, **common)
        if not isinstance(built, BuilderPlan):
            # A five-early-return freeze (data quality / circuit breaker / etc.).
            log.info("line1_early_return", signal_id=signal_id)
            return Line1RunResult(outcome=Line1Outcome.EARLY_RETURN, **common)

        # Open the decision-ledger entry for every constructed plan (the
        # production "plan drafted" step — idempotent PLAN_DRAFTED). Both
        # routing targets (SimulationExecutor / InstructionDispatcher) append
        # events onto this entry, so it MUST exist before routing. The runner
        # is the production composition root that owns this lifecycle step
        # (no other production caller opens the ledger today).
        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)

        if plan.side is InstructionSide.HOLD:
            return Line1RunResult(outcome=Line1Outcome.HOLD, plan=plan, **common)
        if plan.status is not InstructionStatus.VALIDATED:
            return Line1RunResult(outcome=Line1Outcome.REJECTED, plan=plan, **common)
        # Line-1 is the BUY (selection) line. A VALIDATED non-BUY (a SELL the
        # fund_manager proposed on a held screening name) must NOT reach the
        # BUY-only renderer (it would raise + crash the daily run), and must
        # NOT be auto-sold here — SELL is Line-2's deterministic monitoring
        # job. Discard it fail-closed (Codex U-C1 P2).
        if plan.side is not InstructionSide.BUY:
            log.warning(
                "line1_non_buy_discarded",
                signal_id=signal_id,
                instruction_id=plan.instruction_id,
                side=plan.side.value,
            )
            return Line1RunResult(
                outcome=Line1Outcome.NON_BUY_DISCARDED, plan=plan, **common
            )

        # VALIDATED BUY → pick the template from the ENGINE's authoritative
        # result (U-C4): if RiskEngine check 5 granted an over-15% ETF
        # concentration exception it surfaces a passed=True position_limit row
        # carrying ``concentration_exception_granted`` in ``risk_summary``. We
        # key off that (not a re-derived affordability flag) so the human-
        # confirm template can never diverge from what the engine actually
        # allowed. Otherwise it is a normal compliant order. The flag now
        # threads through assemble_plan's single construction point, so the
        # over-15% whitelisted-ETF buy VALIDATES instead of failing closed.
        template = (
            BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION
            if _concentration_exception_granted(plan)
            else BuySignalTemplate.NORMAL_COMPLIANT
        )
        wire = self._renderer.render_buy_signal(
            plan, template=template, pilot=self._pilot
        )
        outcome = await self._coordinator.route(
            OutboundSignal(plan=plan, wire_text=wire), now=now
        )
        log.info(
            "line1_routed",
            signal_id=signal_id,
            instruction_id=plan.instruction_id,
            action=outcome.action,
            mode=outcome.mode.value,
        )
        return Line1RunResult(
            outcome=Line1Outcome.ROUTED, plan=plan, route_outcome=outcome, **common
        )

    @staticmethod
    def _short(
        signal_id: str, outcome: Line1Outcome, *, tier: BudgetTier | None = None
    ) -> Line1RunResult:
        log.info("line1_short_circuit", signal_id=signal_id, outcome=outcome.value)
        return Line1RunResult(signal_id=signal_id, outcome=outcome, tier=tier)


_CONCENTRATION_EXCEPTION_GRANTED = "concentration_exception_granted"
"""RiskEngine check-5 marker (mirrors ``backend.risk.engine``): a passed=True
``position_limit`` row carrying this string means the over-15% ETF
concentration exception was granted (P0-7-amendment-2026-05-24 §2.3 / U-C4)."""


def _concentration_exception_granted(plan: InstructionPlan) -> bool:
    """True iff RiskEngine granted an over-15% ETF concentration exception.

    Reads the authoritative engine result off ``plan.risk_summary`` (the
    builder preserves the granted marker on the ``position_limit`` row) rather
    than re-deriving the upstream affordability flag — the wire template must
    match what the engine actually allowed.
    """
    return any(
        row.passed is True
        and _CONCENTRATION_EXCEPTION_GRANTED in (row.message or "")
        for row in plan.risk_summary
    )


def _mandatory_records_from_state(
    state: TeamState, signal_id: str
) -> MandatoryAgentRecords:
    """Derive the 4-agent record ids from the debate state.

    Each id is non-empty IFF the agent produced a non-empty report — so a
    silent agent (None router / per-stage failure) yields an empty id and the
    Builder's 4-agent gate degrades to HOLD rather than a false pass
    (P0-10 §2.3). The gate also requires ``debate_round_count >= 1``, set by
    ``debate_node``.
    """

    def rid(name: str, key: str) -> str:
        return f"{signal_id}-{name}" if (state.get(key) or "").strip() else ""

    return MandatoryAgentRecords(
        fundamental_analyst_record_id=rid("fundamental", "fundamental_report"),
        technical_analyst_record_id=rid("technical", "technical_report"),
        risk_officer_record_id=rid("risk_officer", "risk_officer_report"),
        fund_manager_record_id=rid("fund_manager", "fund_manager_reasoning"),
    )


__all__ = [
    "AssemblyContextFactory",
    "Line1ContextProvider",
    "Line1LeadContext",
    "Line1Outcome",
    "Line1RunResult",
    "Line1Runner",
]
