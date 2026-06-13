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

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
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
from backend.candidate_selector.selector import (
    AdvisorySignal,
    CandidateSelector,
    QuantCandidate,
)
from backend.integrations.feishu.renderer import BuySignalTemplate, MessageRenderer
from backend.integrations.feishu.signal_rationale import BuySignalRationale
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.models.position_thesis import MAX_PILLARS, MIN_PILLARS, PositionThesis
from backend.orchestration.instruction_dispatcher import (
    FeishuSender,
    OutboundSignal,
    OutboxRepository,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.position_thesis.derivation import build_position_thesis
from backend.screening.screener import CandidateRow, Screener
from backend.services.cost_guard import DailyBudgetExceededError
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    BuilderDegrade,
    BuilderPlan,
    InstructionPlanBuilder,
    MandatoryAgentRecords,
)
from backend.services.ledger import DecisionLedgerService
from backend.style import StyleInputs, StyleTag, classify_style

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
    QUOTE_DEGRADED = "quote_degraded"
    """The lead's live quote was unusable (no dual-source-fresh last, divergent /
    stale spot, or missing 卖一) so the price-cage BUY 限价上限 is unprovable
    (U-E2 / 缺口4). A structurally non-actionable notice is rendered (no order,
    no instruction_id); the system NEVER prices a real BUY on the last / T-1
    close. Per-candidate — the basket falls through to the next name."""
    ALLOCATION_SKIPPED = "allocation_skipped"
    """Portfolio allocation's inverse-volatility target floored to 0 lots for
    this name (conservative under-deployment, P0-7-amendment-2026-05-30) — the
    quote was fine, the name is simply not funded today. Per-candidate; the
    basket falls through. No order, no notice (distinct from QUOTE_DEGRADED)."""
    EARLY_RETURN = "early_return"
    """A Builder five-early-return freeze blocked routing (data quality, etc.)."""
    BUDGET_EXHAUSTED = "budget_exhausted"
    """The ¥100 daily reservation or the max_debates_per_day fan-out cap stopped
    the shortlist walk before any (further) candidate could be debated
    (P1-7-amendment-2026-05-26). Fail-closed: already-routed BUYs are kept."""


class Line1SelectionMode(StrEnum):
    """How many routable BUYs Line-1 collects from the shortlist per run.

    P1-7-amendment-2026-05-26 §2.2 (owner AskUserQuestion): default ``BASKET``.
    """

    BASKET = "basket"
    """Walk the whole shortlist, collecting EVERY VALIDATED BUY (a REJECTED /
    HOLD / DEGRADED / non-BUY candidate falls through to the next). Bounded by
    max_debates_per_day + the ¥100 reservation + P0-7 ≤5 orders/day +
    RiskEngine 15%/70% caps (cash threaded across the basket)."""
    SINGLE = "single"
    """Return at the FIRST VALIDATED BUY — debate no further candidates."""


@dataclass(frozen=True)
class CommittedBuy:
    """A basket BUY already routed this run, threaded into later candidates.

    Flow-level only (taken off the routed plan), so the runner stays
    import-clean. The provider folds these into the next candidate's account
    (cash ↓ by notional) + positions (value ↑) so the basket stays collectively
    ≤ available cash and ≤ the 70% total-position cap — RiskEngine then
    re-validates each candidate against the post-commitment state
    (P1-7-amendment-2026-05-26 §2.3)."""

    code: str
    volume: int
    limit_price: float


@dataclass(frozen=True)
class RoutedBuy:
    """One VALIDATED BUY that was rendered + handed to the RouteCoordinator."""

    plan: InstructionPlan
    route_outcome: RouteOutcome


@dataclass(frozen=True)
class Line1QuoteDegrade:
    """The provider could not price a lead — degrade to a non-actionable notice.

    Returned by ``Line1ContextProvider.build_lead_context`` (instead of a
    ``Line1LeadContext``) when the lead's live quote is unusable: no
    dual-source-fresh last, a divergent / stale spot, or a missing 卖一 (U-E2 /
    缺口4). Flow-level only (the runner stays import-clean): the runner renders a
    structurally non-actionable notice + classifies ``QUOTE_DEGRADED`` and never
    prices a BUY on the last / T-1 close. ``reason`` is a deterministic
    provider string (never LLM)."""

    code: str
    name: str
    reason: str


@dataclass(frozen=True)
class Line1AllocationSkip:
    """Portfolio allocation chose not to buy this name today (P-003).

    Distinct from :class:`Line1QuoteDegrade`: the lead's quote was perfectly
    usable, but its inverse-volatility cash target floored to 0 lots
    (conservative under-deployment, P0-7-amendment-2026-05-30) — so the order is
    skipped, NOT coerced to 1 lot. The runner classifies ``ALLOCATION_SKIPPED``
    (never the misleading ``QUOTE_DEGRADED``) and emits no non-actionable-quote
    notice. ``reason`` is a deterministic provider string (never LLM)."""

    code: str
    name: str
    reason: str


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

    @property
    def held_codes(self) -> frozenset[str]:
        """Currently held, settled, bare 6-digit codes (holdings-aware Line-1).

        Line-1 excludes these from the BUY candidate set so it only fills
        genuine *empty* slots with NEW names — it never re-buys a holding
        (adding to a position is Line-2's job) and the ≤5-slot rotation
        (Phase V-004) is what frees a slot for a stronger challenger. Optional
        for backward compatibility: a provider without it (older tests /
        offline) yields no exclusion (``getattr`` default ``frozenset()``)."""
        ...

    def per_lot_cost(self, code: str, last_price: float) -> float:
        """One A-share lot cost in ¥ (``last_price × lot_size``)."""
        ...

    def prime_allocation(self, shortlist_rows: Sequence[CandidateRow]) -> None:
        """Compute the shortlist's portfolio-allocation cash targets (P-003).

        Optional: the runner invokes this once (``hasattr``-guarded) after
        selection + before the walk so the provider can pre-size each name to
        its inverse-volatility target (P0-7-amendment-2026-05-30). A provider
        without it (or with no allocation policy) is a no-op — sizing stays at
        the existing max_compliant. Implementations must not construct an
        ``InstructionPlan`` here (single construction point).
        """
        ...

    async def build_lead_context(
        self,
        lead: CandidateRow,
        *,
        concentration_exception: bool = False,
        committed: tuple[CommittedBuy, ...] = (),
        signal_id: str = "",
        seq: int = 0,
    ) -> Line1LeadContext | Line1QuoteDegrade | Line1AllocationSkip:
        """Build the TeamContext + AssemblyContext factory for the lead.

        Async because the provider fetches the lead's live quote (dual-source
        last + 卖一 orderbook) here to derive the price-cage BUY 限价上限 (U-E2 /
        缺口4). When the quote is unusable it returns a :class:`Line1QuoteDegrade`
        instead of a context, so the runner degrades to a non-actionable notice
        rather than pricing a BUY on the stale last / T-1 close.

        ``concentration_exception`` is the lead's budget-tier flag (over-15%
        whitelisted ETF in Micro/Small). The provider threads it into BOTH the
        debate ``TeamContext`` (so the debate's risk-gate node does not record
        a REJECTED decision that contradicts the routed plan) AND the
        ``AssemblyContext`` (the authoritative 14-check). RiskEngine still
        re-derives ETF + whitelist + ≤max_lots, so the flag never bypasses the
        cap on its own (U-C4 / codex P2).

        ``committed`` are the BUYs already routed earlier in THIS basket run
        (P1-7-amendment-2026-05-26 §2.3). The provider folds them into the
        candidate's account (cash ↓ by notional) + positions (value ↑) so this
        candidate is sized + 14-check-validated against the post-commitment
        state — keeping the basket collectively ≤ cash and ≤ the 70% cap. Empty
        for SINGLE mode and the first BASKET candidate (identical to U-D1b).

        ``signal_id`` / ``seq`` thread the run correlation in so the provider can
        tag the PIT spot snapshot's lineage (U-E2 §2.0 replayability).
        """
        ...


@runtime_checkable
class AdvisoryProvider(Protocol):
    """O-003: resolve MiroFish-forecast advisory signals for a candidate set.

    Returns the bounded re-rank inputs the :class:`CandidateSelector`
    consumes, or ``None`` when no usable forecast exists (the selector
    then runs the pure-quant path). Implementations must be fail-open:
    any gap (no forecast, stale, malformed, IO error) returns ``None`` so
    MiroFish can only reorder an already-qualified set, never gate it.
    """

    async def __call__(
        self, codes: Sequence[str], *, trade_date: str
    ) -> Sequence[AdvisorySignal] | None: ...


class ThesisWriter(Protocol):
    """Persists a buy-time :class:`PositionThesis` (W-001).

    Injected so the runner stays import-clean of the broker/store layer — the
    concrete :class:`backend.position_thesis.store.PositionThesisStore` is wired
    by ``main.py``. A thesis write is an audit side-effect that never gates
    routing (the order already went out); the runner swallows any failure.
    """

    def open_thesis(self, thesis: PositionThesis) -> bool:
        """Record the thesis. Returns False on an idempotent no-op."""
        ...


@runtime_checkable
class StyleNameplateSink(Protocol):
    """Registers the deterministic buy-time style for a code (AC-001).

    Injected so the runner stays import-clean of the broker layer — the concrete
    sink (wired by ``main.py``) calls ``MockBroker.set_pending_entry_style`` so
    the NEXT BUY fill stamps the position nameplate's ``entry_style``. Called
    BEFORE the route (the fill stamps at episode open). A no-op sink on the
    offline path leaves ``entry_style`` None, which is the legacy behaviour.
    """

    def set_pending_entry_style(self, code: str, style: str) -> None:
        """Register the style label to stamp on ``code``'s next episode open."""
        ...


@dataclass(frozen=True)
class Line1RunResult:
    """Audit-grade summary of one Line-1 run.

    ``routed_buys`` holds EVERY VALIDATED BUY routed this run (BASKET mode;
    ≤1 in SINGLE mode). ``plan`` / ``route_outcome`` mirror the FIRST routed
    BUY for backward compatibility — when 0 BUYs route they carry the last
    processed candidate's terminal plan (e.g. a discarded SELL) for audit.
    """

    signal_id: str
    outcome: Line1Outcome
    tier: BudgetTier | None = None
    shortlist: tuple[str, ...] = ()
    lead_code: str | None = None
    route_outcome: RouteOutcome | None = None
    plan: InstructionPlan | None = None
    routed_buys: tuple[RoutedBuy, ...] = ()


@dataclass(frozen=True)
class _CandidateResult:
    """One shortlist candidate's terminal after debate + assemble + route."""

    outcome: Line1Outcome
    plan: InstructionPlan | None = None
    route_outcome: RouteOutcome | None = None


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
        selection_mode: Line1SelectionMode = Line1SelectionMode.BASKET,
        digest_sender: FeishuSender | None = None,
        digest_chat_id: str = "",
        digest_outbox: OutboxRepository | None = None,
        thesis_writer: ThesisWriter | None = None,
        style_sink: StyleNameplateSink | None = None,
        advisory_provider: AdvisoryProvider | None = None,
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
        # BASKET (default, owner 2026-05-26): collect every VALIDATED BUY in the
        # shortlist; SINGLE: stop at the first (P1-7-amendment-2026-05-26 §2.2).
        self._selection_mode = selection_mode
        # P-004 basket digest (P0-3-amendment-2026-05-30): a display-only
        # overview sent ONCE per run after the basket routes, INDEPENDENT of the
        # InstructionDispatcher (own idempotency key, own outbox claim). All
        # three must be wired (feishu_interactive) for it to send; otherwise the
        # digest is silently skipped (simulation_auto / offline).
        self._digest_sender = digest_sender
        self._digest_chat_id = digest_chat_id
        self._digest_outbox = digest_outbox
        # W-001: persist the buy-time PositionThesis when a BUY routes. Optional
        # (None on the offline / simulation paths) — never gates routing.
        self._thesis_writer = thesis_writer
        # AC-001: register the deterministic buy-time style so the fill stamps
        # the position nameplate. Optional — None leaves entry_style None.
        self._style_sink = style_sink
        # O-003: MiroFish sector-forecast bounded re-rank input. Optional and
        # fail-open — None (or any resolution gap) leaves the pure-quant order
        # untouched, so removing MiroFish never changes the qualified set.
        self._advisory_provider = advisory_provider

    async def run(
        self,
        *,
        frame: MarketDataSnapshot,
        provider: Line1ContextProvider,
        now: datetime,
        signal_id: str | None = None,
    ) -> Line1RunResult:
        """Run Line-1 once on the T-1 frame; route a BUY basket.

        Walks the deterministic shortlist (P1-7-amendment-2026-05-26): each
        candidate is debated → assembled (single construction point) → routed;
        a RiskEngine REJECT / HOLD / DEGRADE / non-BUY falls through to the next
        name. BASKET mode collects every VALIDATED BUY (SINGLE stops at the
        first). The walk stops fail-closed when the ¥100 reservation or the
        max_debates_per_day cap is exhausted — already-routed BUYs are kept.
        """
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

        # 3. Deterministic selection over the affordable quant set, holdings-aware:
        # exclude already-held codes so Line-1 only fills genuine EMPTY slots with
        # NEW names (P0-7-amendment-2026-06-01 §1.4). It never re-buys a holding
        # (adding is Line-2's job) and the ≤5-slot rotation frees a slot for a
        # stronger challenger. ``getattr`` default keeps older providers / tests
        # (no held_codes) on the prior holdings-blind behaviour (codex-safe).
        held_codes = frozenset(getattr(provider, "held_codes", frozenset()))
        quant = [
            QuantCandidate(code=c.code, score=c.score)
            for c in screen.candidates
            if c.code in affordable_codes and c.code not in held_codes
        ]
        # O-003: MiroFish forecast bounded re-rank input (≤1-percentile, never
        # evicts the top ≥min_quant names). Fail-open — None leaves the
        # pure-quant order bit-identical, so removing MiroFish only changes
        # ordering, never the qualified set (P0-8-amendment-2026-05-24 §2.3).
        # Resolve the forecast boundary DETERMINISTICALLY from the replayable
        # frame date (day after T-1), never wall-clock ``now`` — so the
        # advisory is bit-exact replayable (R0 PIT red line ①). The provider
        # consumes the most recent forecast strictly before it = the T-1 17:00
        # forecast co-dated with this frame (codex O-003 P2 + review panel).
        advisory = await self._resolve_advisory(
            [c.code for c in quant], _selection_day_from_frame(frame.trade_date)
        )
        selection = self._selector.select(quant, advisory=advisory)
        if not selection.shortlist:
            return self._short(sid, Line1Outcome.EMPTY_SHORTLIST, tier=assessment.tier)

        # 3b. Prime the portfolio-allocation cash targets over the shortlist
        # (P-003, P0-7-amendment-2026-05-30): the provider computes each name's
        # inverse-volatility incremental target ONCE here so the per-candidate
        # sizing in build_lead_context clamps to it. ``hasattr``-guarded so a
        # provider without allocation (tests / offline) is unaffected. Targets
        # are walk-start deterministic (no mid-walk reallocation, redline 6).
        if hasattr(provider, "prime_allocation"):
            provider.prime_allocation([by_code[c] for c in selection.shortlist])

        # 4-6. Walk the shortlist: debate → assemble (single construction
        # point) → route each candidate; REJECT/HOLD/DEGRADE/non-BUY falls
        # through to the next. Collect the VALIDATED BUY basket (cash threaded
        # across it so it stays collectively ≤ cash + ≤ 70%).
        routed: list[RoutedBuy] = []
        committed: list[CommittedBuy] = []
        last: _CandidateResult | None = None
        budget_stopped = False
        # PIT replay reference stamped onto every buy-time thesis (W-001).
        snapshot_id = str(frame.snapshot_id)
        for seq, code in enumerate(selection.shortlist, start=1):
            afford = afford_by_code.get(code)
            concentration_exception = bool(
                afford is not None and afford.concentration_exception
            )
            try:
                last = await self._process_candidate(
                    provider,
                    by_code[code],
                    concentration_exception=concentration_exception,
                    committed=tuple(committed),
                    signal_id=sid,
                    seq=seq,
                    now=now,
                    snapshot_id=snapshot_id,
                )
            except DailyBudgetExceededError as exc:
                # ¥100 reservation OR max_debates_per_day cap refused this
                # debate (the crossing call never ran). Stop fail-closed and
                # keep the basket built so far (P1-7-amendment-2026-05-26 §2.3).
                log.warning(
                    "line1_budget_exhausted",
                    signal_id=sid,
                    code=code,
                    routed=len(routed),
                    error=str(exc),
                )
                budget_stopped = True
                break
            if last.outcome is Line1Outcome.EARLY_RETURN:
                # A Builder five-early-return freeze (data quality / mode switch
                # / open reconciliation ticket / circuit-breaker cooldown / EOD
                # pipeline) is RUN-LEVEL: the same inputs gate every candidate,
                # so stop the walk instead of burning debates + budget on names
                # that would all freeze identically, and surface the freeze
                # rather than a misleading BUDGET_EXHAUSTED (codex P2).
                log.info("line1_run_frozen", signal_id=sid, code=code)
                break
            if (
                last.outcome is Line1Outcome.ROUTED
                and last.plan is not None
                and last.route_outcome is not None
            ):
                routed.append(
                    RoutedBuy(plan=last.plan, route_outcome=last.route_outcome)
                )
                committed.append(
                    CommittedBuy(
                        code=last.plan.stock_code,
                        volume=int(last.plan.volume or 0),
                        limit_price=float(last.plan.limit_price or 0.0),
                    )
                )
                if self._selection_mode is Line1SelectionMode.SINGLE:
                    break

        result = self._aggregate(
            signal_id=sid,
            shortlist=selection.shortlist,
            tier=assessment.tier,
            routed=routed,
            last=last,
            budget_stopped=budget_stopped,
        )
        # P-004: a display-only basket overview AFTER the per-name orders, once
        # per run, idempotent. Never gates routing (the orders already went out).
        if result.routed_buys:
            await self._send_basket_digest(sid, result.routed_buys, now)
        return result

    async def _send_basket_digest(
        self, signal_id: str, routed_buys: tuple[RoutedBuy, ...], now: datetime
    ) -> None:
        """Send the display-only basket overview once per run (idempotent).

        Independent of the InstructionDispatcher (P0-3-amendment-2026-05-30):
        its own outbox key ``{signal_id}-basket-digest`` is the at-most-once
        gate, so a same-day rerun (same signal_id) never double-sends. Skipped
        when the digest channel is not wired (simulation_auto / offline). Never
        raises: a digest is an audit/operator convenience, not a tradeability
        gate — the orders already routed.
        """
        if (
            self._digest_sender is None
            or self._digest_outbox is None
            or not self._digest_chat_id
        ):
            return
        # Only summarize orders that actually reached the owner (codex P-004 P2):
        # a send_failed dispatch is still ROUTED in routed_buys, but the digest
        # must not claim a basket the owner never received. dispatched (fresh) +
        # skipped_duplicate (already delivered) are the delivered actions.
        delivered = [
            rb
            for rb in routed_buys
            if rb.route_outcome is not None
            and rb.route_outcome.action in {"dispatched", "skipped_duplicate"}
        ]
        if not delivered:
            log.info("line1_basket_digest_skipped_no_delivered", signal_id=signal_id)
            return
        key = f"{signal_id}-basket-digest"
        try:
            if not await self._digest_outbox.try_claim(key, at=now):
                log.info("line1_basket_digest_skipped_duplicate", signal_id=signal_id)
                return
            text = self._renderer.render_basket_digest(
                [rb.plan for rb in delivered], pilot=self._pilot
            )
            result = await self._digest_sender.send_message(
                self._digest_chat_id, text, uuid=key
            )
            if result.ok:
                await self._digest_outbox.mark_sent(
                    key, message_id=result.message_id, at=now
                )
                log.info(
                    "line1_basket_digest_sent",
                    signal_id=signal_id,
                    count=len(delivered),
                    message_id=result.message_id,
                )
            else:
                # Definitive API rejection → release the claim so a later rerun
                # can re-send (no message reached the owner). A transport
                # EXCEPTION (below) leaves the claim PENDING — never auto-resent.
                await self._digest_outbox.release(key)
                log.warning(
                    "line1_basket_digest_send_failed",
                    signal_id=signal_id,
                    code=result.code,
                )
        except Exception as exc:  # noqa: BLE001 — digest never blocks the run
            log.warning(
                "line1_basket_digest_error", signal_id=signal_id, error=str(exc)
            )

    async def _process_candidate(
        self,
        provider: Line1ContextProvider,
        candidate: CandidateRow,
        *,
        concentration_exception: bool,
        committed: tuple[CommittedBuy, ...],
        signal_id: str,
        seq: int,
        now: datetime,
        snapshot_id: str = "",
    ) -> _CandidateResult:
        """Debate + assemble (single construction point) + route one candidate.

        Raises :class:`DailyBudgetExceededError` (out of ``run_shortlist``) when
        the ¥100 reservation or the debate-slot cap refuses — the caller stops
        the basket walk. The ``concentration_exception`` flag and ``committed``
        basket enter at the single point (``build_lead_context``) so the debate,
        the sizing and the authoritative 14-check all see the same state.
        """
        lead_ctx = await provider.build_lead_context(
            candidate,
            concentration_exception=concentration_exception,
            committed=committed,
            signal_id=signal_id,
            seq=seq,
        )
        if isinstance(lead_ctx, Line1AllocationSkip):
            # P-003: the quote was fine but allocation's inverse-vol target
            # floored to 0 lots — skip this name (no order, no debate, no
            # misleading non-actionable-quote notice). The basket falls through.
            log.info(
                "line1_allocation_skipped",
                signal_id=signal_id,
                code=lead_ctx.code,
                reason=lead_ctx.reason,
            )
            return _CandidateResult(outcome=Line1Outcome.ALLOCATION_SKIPPED)
        if isinstance(lead_ctx, Line1QuoteDegrade):
            # The lead's live quote was unusable (no dual-source-fresh last,
            # divergent / stale spot, or missing 卖一). Render a structurally
            # non-actionable notice + classify QUOTE_DEGRADED, skipping the
            # debate (no LLM burned) — the basket falls through to the next
            # name. We NEVER price a BUY on the last / T-1 close (U-E2 §2.0).
            notice = self._renderer.render_non_actionable_quote(
                stock_code=lead_ctx.code,
                stock_name=lead_ctx.name,
                reason=lead_ctx.reason,
            )
            log.info(
                "line1_quote_degraded",
                signal_id=signal_id,
                code=lead_ctx.code,
                reason=lead_ctx.reason,
                notice_chars=len(notice),
            )
            return _CandidateResult(outcome=Line1Outcome.QUOTE_DEGRADED)
        debate = await run_shortlist(
            lead_ctx.team_context, [lead_ctx.brief], redis_client=self._redis
        )
        fmo = to_fund_manager_output(debate.state)
        records = _mandatory_records_from_state(
            debate.state, f"{signal_id}-{candidate.code}"
        )
        context = lead_ctx.make_assembly_context(
            signal_id=signal_id,
            seq=seq,
            debate_round_count=int(debate.state.get("debate_round_count", 0)),
            analysis_record_id=f"{signal_id}-{candidate.code}-debate",
            risk_validation_id=f"{signal_id}-{candidate.code}-rv",
        )
        built = await self._builder.assemble_plan(
            fund_manager_output=fmo, mandatory_records=records, context=context
        )
        # Display-only BUY 判据 (U-E4 缺口3): the deterministic screener factors
        # + the debate's free text, assembled into a render parameter. It is
        # NEVER written onto the InstructionPlan (single construction point
        # M-004) nor consumed by the parser / idempotency key.
        rationale = _build_buy_rationale(candidate, debate.state)
        # AC-001: classify the deterministic buy-time style and register it on
        # the broker nameplate BEFORE the route — the fill stamps entry_style at
        # episode open. Display-only; it never touches the InstructionPlan or any
        # risk number. Until AC-003 lights up the value score this is uniformly
        # SHORT_TERM (bit-identical to the pre-AC path). Never gates routing.
        style = self._classify_candidate_style(candidate, debate.state)
        if self._style_sink is not None:
            try:
                self._style_sink.set_pending_entry_style(
                    candidate.code, style.value
                )
            except Exception as exc:  # noqa: BLE001 — nameplate never blocks
                log.warning(
                    "line1_style_register_failed",
                    code=candidate.code,
                    error=str(exc),
                )
        result = await self._route_candidate(
            built,
            signal_id=signal_id,
            code=candidate.code,
            now=now,
            rationale=rationale,
        )
        # W-001: persist the buy-time PositionThesis when the BUY actually
        # reached the owner / was filled — NOT merely ROUTED. A send_failed or
        # skipped_in_flight dispatch is still Line1Outcome.ROUTED but never
        # produced a holding, so persisting then would leave a stale open thesis
        # for a non-held position (codex W-001 P2). Only the delivered actions
        # (dispatched / skipped_duplicate / simulation_routed) create a holding.
        # The pillars are the debate's LLM reasoning (P0-10-permitted free text);
        # the invalidation thresholds are derived deterministically from the fill
        # anchor + score (never from the pillar text). A failure here NEVER
        # unwinds the order (audit side-effect, like the digest).
        # A holding exists only when the order actually filled or will fill: a
        # feishu dispatch fills later via report, but a SIMULATION route returns
        # action="simulation_routed" even on a freeze / broker rejection
        # (final_status=REJECTED, no broker mutation) — so a sim route counts as
        # delivered ONLY when the executor confirmed a FILL (codex verify P2).
        # This gates BOTH the W-001 thesis persist and the AC-001 style clear.
        delivered = (
            result.outcome is Line1Outcome.ROUTED
            and result.plan is not None
            and result.route_outcome is not None
            and result.route_outcome.action in _THESIS_DELIVERED_ACTIONS
            and _route_produced_holding(result.route_outcome)
        )
        if delivered and result.plan is not None:
            self._persist_thesis(
                candidate, debate.state, result.plan, snapshot_id, style
            )
        elif self._style_sink is not None:
            # AC-001 (codex P2): a non-delivered route (REJECTED / HOLD /
            # DEGRADED / non-BUY / send_failed) produced no fill to consume the
            # pending style registered before routing. Clear it in BOTH modes so
            # a later Line-2 ADD / recovery / report fill for the same code
            # stamps None instead of inheriting a stale Line-1 style. This is
            # safe across runs: a delivered feishu dispatch is in the ``delivered``
            # branch (never reaches here), THIS run already overwrote any prior
            # registration for the code at the pre-route register, and the broker
            # discards the whole pending map at the daily settlement reset
            # (advance_day) — the concrete same-day-expiry bound. Never gates
            # routing.
            try:
                self._style_sink.set_pending_entry_style(candidate.code, None)
            except Exception as exc:  # noqa: BLE001 — nameplate never blocks
                log.warning(
                    "line1_style_clear_failed",
                    code=candidate.code,
                    error=str(exc),
                )
        return result

    def _classify_candidate_style(
        self, candidate: CandidateRow, state: TeamState
    ) -> StyleTag:
        """Deterministic buy-time style for a candidate (AC-001).

        Pure: built from the screener factor spectrum + whether a deterministic
        thesis can be derived (≥ MIN_PILLARS of LLM reasoning). The three-tier
        ``value_score`` is None until AC-003, so this returns SHORT_TERM for
        every name today — the value path activates with the value-line score.
        """
        thesis_derivable = len(_build_thesis_pillars(state)) >= MIN_PILLARS
        inputs = StyleInputs(
            momentum_20d=candidate.factors.momentum_20d,
            volatility_20d=candidate.factors.volatility_20d,
            ma_ratio_5_20=candidate.factors.ma_ratio_5_20,
            value_score=None,
            thesis_derivable=thesis_derivable,
        )
        return classify_style(inputs).style

    def _persist_thesis(
        self,
        candidate: CandidateRow,
        state: TeamState,
        plan: InstructionPlan,
        snapshot_id: str,
        style: StyleTag | None = None,
    ) -> None:
        """Build + persist the buy-time PositionThesis (W-001). Never raises."""
        if self._thesis_writer is None:
            return
        try:
            pillars = _build_thesis_pillars(state)
            if len(pillars) < MIN_PILLARS:
                log.info(
                    "line1_thesis_skipped_insufficient_pillars",
                    instruction_id=plan.instruction_id,
                    pillars=len(pillars),
                )
                return
            thesis = build_position_thesis(
                instruction_id=plan.instruction_id,
                signal_id=plan.signal_id,
                stock_code=plan.stock_code,
                stock_name=plan.stock_name,
                created_at=plan.created_at,
                trade_date=plan.trade_date,
                pillars=pillars,
                entry_price=float(plan.limit_price or 0.0),
                entry_score=float(candidate.score),
                snapshot_id=snapshot_id or "unknown",
                evidence_ids=tuple(plan.evidence_ids),
                style=style,
            )
            wrote = self._thesis_writer.open_thesis(thesis)
            log.info(
                "line1_thesis_persisted",
                instruction_id=plan.instruction_id,
                wrote=wrote,
            )
        except Exception as exc:  # noqa: BLE001 — thesis never blocks the order
            log.warning(
                "line1_thesis_persist_failed",
                instruction_id=plan.instruction_id,
                error=str(exc),
            )

    async def _route_candidate(
        self,
        built: object,
        *,
        signal_id: str,
        code: str,
        now: datetime,
        rationale: BuySignalRationale | None = None,
    ) -> _CandidateResult:
        """Render + route a VALIDATED BUY; classify every other terminal.

        ``rationale`` (U-E4) is threaded to :meth:`render_buy_signal` for the
        VALIDATED-BUY path as a display-only justification — it never touches
        the plan or any execution-bearing structure.
        """
        if isinstance(built, BuilderDegrade):
            log.info(
                "line1_degraded",
                signal_id=signal_id,
                code=code,
                reason=built.reason_namespace,
            )
            return _CandidateResult(outcome=Line1Outcome.DEGRADED)
        if not isinstance(built, BuilderPlan):
            # A five-early-return freeze (data quality / circuit breaker / etc.).
            log.info("line1_early_return", signal_id=signal_id, code=code)
            return _CandidateResult(outcome=Line1Outcome.EARLY_RETURN)

        # Open the decision-ledger entry for every constructed plan (the
        # production "plan drafted" step — idempotent PLAN_DRAFTED). Both
        # routing targets (SimulationExecutor / InstructionDispatcher) append
        # events onto this entry, so it MUST exist before routing. The runner
        # is the production composition root that owns this lifecycle step
        # (no other production caller opens the ledger today).
        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)

        if plan.side is InstructionSide.HOLD:
            return _CandidateResult(outcome=Line1Outcome.HOLD, plan=plan)
        if plan.status is not InstructionStatus.VALIDATED:
            return _CandidateResult(outcome=Line1Outcome.REJECTED, plan=plan)
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
            return _CandidateResult(
                outcome=Line1Outcome.NON_BUY_DISCARDED, plan=plan
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
            plan, template=template, rationale=rationale, pilot=self._pilot
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
        return _CandidateResult(
            outcome=Line1Outcome.ROUTED, plan=plan, route_outcome=outcome
        )

    def _aggregate(
        self,
        *,
        signal_id: str,
        shortlist: tuple[str, ...],
        tier: BudgetTier,
        routed: list[RoutedBuy],
        last: _CandidateResult | None,
        budget_stopped: bool,
    ) -> Line1RunResult:
        """Fold the per-candidate walk into one audit-grade run result."""
        common: dict[str, Any] = {
            "signal_id": signal_id,
            "tier": tier,
            "shortlist": shortlist,
            "lead_code": shortlist[0] if shortlist else None,
        }
        if routed:
            # ROUTED whenever ≥1 BUY made it (even if the walk later stopped on
            # budget). plan/route_outcome mirror the FIRST routed BUY.
            first = routed[0]
            return Line1RunResult(
                outcome=Line1Outcome.ROUTED,
                plan=first.plan,
                route_outcome=first.route_outcome,
                routed_buys=tuple(routed),
                **common,
            )
        if budget_stopped:
            # 0 BUYs and the budget stopped the walk → surface BUDGET_EXHAUSTED
            # (raise the cap / rerun next day) over a candidate's terminal.
            return Line1RunResult(outcome=Line1Outcome.BUDGET_EXHAUSTED, **common)
        if last is not None:
            # 0 BUYs, walk completed: reflect the last candidate's terminal
            # (all-HOLD → HOLD, all-rejected → REJECTED, …) + its plan for audit.
            return Line1RunResult(
                outcome=last.outcome, plan=last.plan, **common
            )
        # Unreachable (the shortlist is non-empty), kept fail-closed.
        return Line1RunResult(outcome=Line1Outcome.EMPTY_SHORTLIST, **common)

    async def _resolve_advisory(
        self, codes: Sequence[str], trade_date: str
    ) -> Sequence[AdvisorySignal] | None:
        """Best-effort MiroFish advisory; ``None`` on any gap (pure-quant path).

        Never raises and never gates selection — a provider error or absent
        forecast simply yields ``None`` so the selector keeps the pure-quant
        order (red-line: MiroFish can reorder but never change the qualified
        set).
        """
        if self._advisory_provider is None or not codes or not trade_date:
            return None
        try:
            return await self._advisory_provider(codes, trade_date=trade_date)
        except Exception as exc:  # noqa: BLE001 — advisory is never load-bearing
            log.warning("advisory_resolve_failed", error=str(exc))
            return None

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


def _selection_day_from_frame(frame_trade_date: str) -> str:
    """Deterministic forecast-boundary ISO date = the day AFTER the T-1 frame.

    The advisory provider consumes the most recent ``MIROFISH-FORECAST``
    strictly BEFORE this date, so the T-1 forecast (``trade_date`` ==
    ``frame.trade_date``) is the one selected. Deriving the boundary purely
    from the replayable ``frame.trade_date`` (a compact ``YYYYMMDD``) — never
    wall-clock ``now`` — keeps the advisory **bit-exact replayable**: an
    offline ``replay <signal_id>`` re-runs against the same frame and
    re-derives the same forecast + staleness verdict (R0 PIT red line ①).
    A malformed frame date yields ``""`` → no advisory (fail-open).
    """
    try:
        frame_day = datetime.strptime(frame_trade_date, "%Y%m%d").date()
    except ValueError:
        return ""
    return (frame_day + timedelta(days=1)).isoformat()


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


def _build_buy_rationale(
    candidate: CandidateRow, state: TeamState
) -> BuySignalRationale:
    """Assemble the display-only BUY 判据 (U-E4 缺口3).

    ① 量化 — the deterministic screener composite score + each Alpha158 factor
    (``candidate.factors``); ② 推理 — the fund_manager reasoning + the three
    mandatory-analyst reports from the debate ``state``. Returned as a render
    parameter for :meth:`render_buy_signal`; it is NEVER written onto the
    InstructionPlan (single construction point M-004) nor consumed by the
    parser / idempotency key (P0-3-amendment-2026-05-27). The renderer
    single-lines + truncates every free-text field before it reaches the wire.
    """
    # Derive the (name, value) pairs from the FactorVector fields themselves —
    # the single source of the screener's factor set — so a future Alpha158
    # factor surfaces on the wire automatically (the renderer's presentation
    # table has an unknown-name fallback) instead of being silently dropped.
    fv = candidate.factors
    return BuySignalRationale(
        composite_score=candidate.score,
        factors=tuple((f.name, getattr(fv, f.name)) for f in fields(fv)),
        fund_manager_reasoning=str(state.get("fund_manager_reasoning") or ""),
        fundamental_conclusion=str(state.get("fundamental_report") or ""),
        technical_conclusion=str(state.get("technical_report") or ""),
        risk_officer_conclusion=str(state.get("risk_officer_report") or ""),
    )


_THESIS_DELIVERED_ACTIONS: frozenset[str] = frozenset(
    {"dispatched", "skipped_duplicate", "simulation_routed"}
)
"""Route actions that mean the BUY reached the owner / was filled — the only
states under which a holding (and therefore a thesis) exists. send_failed /
skipped_in_flight / dry_run_rendered never produced a holding (codex W-001 P2)."""


def _route_produced_holding(ro: RouteOutcome) -> bool:
    """True iff the route actually created (or will create) a holding.

    A feishu ``dispatched`` / ``skipped_duplicate`` fills later via the owner's
    execution report (a holding will form). A ``simulation_routed`` action,
    however, is returned even when the SimulationExecutor REJECTED the order
    (EOD freeze / broker rejection): ``final_status=REJECTED`` with no trades and
    NO broker mutation. Treating that as delivered would persist a thesis for a
    non-held position (W-001) and skip the AC-001 style cleanup (leaving a stale
    pending entry_style) — so a sim route counts only when the executor
    confirmed a FILL with at least one trade (codex verify P2).
    """
    if ro.action != "simulation_routed":
        return True
    sim = ro.simulation_result
    return (
        ro.final_status is InstructionStatus.FILLED
        and sim is not None
        and len(sim.trade_ids) > 0
    )


_PILLAR_SOURCES: tuple[tuple[str, str], ...] = (
    ("基金经理", "fund_manager_reasoning"),
    ("基本面", "fundamental_report"),
    ("技术面", "technical_report"),
    ("风控", "risk_officer_report"),
)
_PILLAR_MAX_CHARS = 480


def _build_thesis_pillars(state: TeamState) -> tuple[str, ...]:
    """Derive the 3–5 LLM buy-logic pillars from the debate state (W-001).

    Each pillar is the (single-lined, truncated) reasoning of one mandatory
    agent. A routed BUY passed the 4-agent gate, so all four are non-empty in
    practice; empties are dropped + the result is capped at ``MAX_PILLARS``. The
    text is LLM-written (P0-10-permitted) and is opaque to the deterministic
    threshold derivation — it can never influence an invalidation threshold.
    """
    pillars: list[str] = []
    for label, key in _PILLAR_SOURCES:
        text = " ".join(str(state.get(key) or "").split())[:_PILLAR_MAX_CHARS]
        if text:
            pillars.append(f"[{label}] {text}")
    return tuple(pillars[:MAX_PILLARS])


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
    "CommittedBuy",
    "Line1AllocationSkip",
    "Line1ContextProvider",
    "Line1LeadContext",
    "Line1Outcome",
    "Line1QuoteDegrade",
    "Line1RunResult",
    "Line1Runner",
    "Line1SelectionMode",
    "RoutedBuy",
    "ThesisWriter",
]
