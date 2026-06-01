"""≤5-slot rotation runner (Phase V-004).

The orchestration hub that turns the deterministic ``backend.slot_portfolio``
rotation decision into a **rotation SELL suggestion** (T-day) + an append-only
``RotationIntent`` — never a same-day buy (P0-7-amendment-2026-06-01 §1.4). It is
the sibling of ``Line1Runner`` / ``Line2DailyRunner`` and runs once per trading
day against the SAME T-1 EOD frame the Line-1 screen used.

Flow (only when the portfolio is FULL — held == ``max_total_positions``):

    screen percentiles + entry-rank baseline + Line-2 incumbent health
      → IncumbentState / ChallengerState
      → propose_rotation (weakest weak incumbent × strongest qualified challenger)
      → apply_churn_gates (yield to protective stops, ≤1/day, cooldowns, …)
      → rotation SELL via assemble_monitoring_plan (single construction point)
      → record RotationIntent (append-only)

T+1 is implicit: when the owner executes the SELL and it settles, the held
count drops and the **holdings-aware Line-1 BUY** fills the freed slot the next
day (no in-flight promise; the freed slot is observed from settled positions).
This runner also resolves expiry for open intents (anti "sold-but-never-rebought").

Red lines: orchestration isolation (no ``backend.{risk,broker,data}`` import —
heavy objects come via the provider); never constructs an ``InstructionPlan``
(the rotation SELL goes through ``assemble_monitoring_plan``); the rotation
decision is deterministic + replayable; fail-closed toward inaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

import structlog

from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.instruction import InstructionPlan, InstructionStatus
from backend.orchestration.instruction_dispatcher import OutboundSignal
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.screening.screener import ScreenResult
from backend.services.instruction_plan_builder import (
    InstructionPlanBuilder,
    MonitoringAssemblyContext,
    MonitoringPlan,
)
from backend.services.ledger import DecisionLedgerService
from backend.slot_portfolio.entry_rank import EntryRankStore
from backend.slot_portfolio.policy import (
    RotationPolicyConfig,
    RotationProposal,
    propose_rotation,
)
from backend.slot_portfolio.rotation_intent import (
    ChurnGateInputs,
    ExpiryOutcomeKind,
    RotationIntentStore,
    apply_churn_gates,
    build_rotation_intent,
    is_expired,
    resolve_expiry,
)
from backend.slot_portfolio.scoring import ChallengerState, IncumbentState

log = structlog.get_logger(component="orchestration.rotation_runner")

ROTATION_SIGNAL_PREFIX = "LINE2-MON-"
"""The rotation SELL reuses the deterministic monitoring construction point, so
its signal_id carries the LINE2-MON- prefix (assemble_monitoring_plan gate); a
``-rotation-`` infix lets audit tell a rotation SELL from an anomaly SELL."""


@dataclass(frozen=True)
class IncumbentHealth:
    """Deterministic Line-2 health + sell params for one held position.

    Built by the provider (which has broker/risk/monitoring access). Numeric
    quant fields are sourced over the same PIT frame; the rotation runner only
    folds them into an :class:`IncumbentState`. ``score_median_20d`` /
    ``score_mad_20d`` default to 0 when no score history is available — that
    just disables confirmation 6a (fail-closed), leaving 6b/6c.
    """

    name: str
    available_volume: int        # settled, T+1 (sizes the SELL)
    sell_limit_price: float      # deterministic SELL limit (frame/quote-derived)
    protective_stop_active: bool
    hard_exit_pending: bool
    anomaly_flag_active: bool
    drawdown_from_local_high: float
    suspended: bool
    limit_down_unsellable: bool
    corporate_action_unsafe: bool
    score_median_20d: float = 0.0
    score_mad_20d: float = 0.0


@runtime_checkable
class RotationContextProvider(Protocol):
    """Caller-supplied bridge to the risk/broker/monitoring objects the runner
    must not import (orchestration isolation), mirroring the Line-1/Line-2
    providers. Implemented by the scheduler / ``main.py``; tests inject a fake.
    """

    @property
    def held_codes(self) -> frozenset[str]:
        """Currently held, settled, bare 6-digit codes (ETF included)."""
        ...

    def incumbent_health(self, code: str) -> IncumbentHealth:
        """Deterministic Line-2 health + SELL params for a held code."""
        ...

    @property
    def rotations_today(self) -> int:
        """Rotation SELLs already issued today (churn cap)."""
        ...

    @property
    def daily_new_instruction_budget_remaining(self) -> int:
        """Remaining ≤5/day order slots (rotation subcap fits in here)."""
        ...

    @property
    def protective_action_needs_cap_today(self) -> bool:
        """A protective stop / forced exit needs today's cap → rotation yields."""
        ...

    def trading_days_between(self, earlier: str, later: str) -> int:
        """Trading-day distance between two YYYYMMDD dates (calendar, injected)."""
        ...

    def trading_day_ahead(self, trade_date: str, n: int) -> str:
        """The YYYYMMDD date ``n`` trading days after ``trade_date`` (calendar)."""
        ...

    def build_rotation_sell_context(
        self,
        *,
        code: str,
        name: str,
        available_volume: int,
        limit_price: float,
        reason: str,
        signal_id: str,
        seq: int,
        now: datetime,
    ) -> MonitoringAssemblyContext:
        """Build the rotation SELL's ``MonitoringAssemblyContext`` (single
        construction point input). The provider synthesises the deterministic
        SELL intent + the per-code risk/broker objects."""
        ...


@dataclass(frozen=True)
class RotationRunResult:
    """Audit-grade summary of one rotation run."""

    signal_id: str
    held_count: int
    portfolio_full: bool
    proposal: RotationProposal | None = None
    gate_blocked_by: tuple[str, ...] = ()
    sell_routed: bool = False
    sell_plan: InstructionPlan | None = None
    route_outcome: RouteOutcome | None = None
    intent_id: str | None = None
    newly_opened_entries: tuple[str, ...] = ()
    resolved_intents: tuple[str, ...] = ()
    expired_intents: tuple[str, ...] = ()
    reason: str = ""


class RotationRunner:
    """Compose the deterministic ≤5-slot rotation into one daily run."""

    def __init__(
        self,
        *,
        policy_config: RotationPolicyConfig,
        intent_store: RotationIntentStore,
        entry_store: EntryRankStore,
        builder: InstructionPlanBuilder,
        renderer: MessageRenderer,
        coordinator: RouteCoordinator,
        ledger: DecisionLedgerService,
        max_total_positions: int,
        pilot: bool = False,
    ) -> None:
        self._config = policy_config
        self._intents = intent_store
        self._entries = entry_store
        self._builder = builder
        self._renderer = renderer
        self._coordinator = coordinator
        self._ledger = ledger
        self._max_positions = max_total_positions
        self._pilot = pilot

    @property
    def intent_store(self) -> RotationIntentStore:
        """The append-only intent ledger (the scheduler folds rotations-today)."""
        return self._intents

    async def run(
        self,
        *,
        screen: ScreenResult,
        provider: RotationContextProvider,
        qualified_codes: frozenset[str],
        now: datetime,
        trade_date: str,
        signal_id: str,
        next_rebalance_close: str | None = None,
    ) -> RotationRunResult:
        """Run the rotation decision once. Returns a no-rotation result whenever
        the portfolio is not full, no incumbent is weak, no challenger wins, or a
        churn gate blocks — only a fully-gated decision routes a SELL."""
        pct_by_code, score_by_code = _percentile_map(screen)
        held = provider.held_codes

        # 1. Record first-seen entry baselines for newly-held codes + retire
        #    baselines for codes that left the held set (open/close lifecycle).
        newly_opened = self._entries.sync_holdings(
            held, trade_date=trade_date,
            percentile_by_code=pct_by_code, score_by_code=score_by_code,
        )

        # 2. Resolve any open intent that has expired (anti sold-but-never-rebought).
        resolved, expired = self._resolve_open_intents(
            held=held, qualified_codes=qualified_codes,
            pct_by_code=pct_by_code, score_by_code=score_by_code,
            trade_date=trade_date,
        )

        held_count = len(held)
        portfolio_full = held_count >= self._max_positions
        if not portfolio_full:
            # Not full → no rotation; the holdings-aware Line-1 BUY fills the
            # genuine empty slots (T+1, observed from settled positions).
            return RotationRunResult(
                signal_id=signal_id, held_count=held_count, portfolio_full=False,
                newly_opened_entries=newly_opened,
                resolved_intents=resolved, expired_intents=expired,
                reason="portfolio not full — Line-1 fills empty slots, no rotation",
            )

        # 3. Build the deterministic incumbent / challenger states.
        incumbents = [
            self._incumbent_state(
                code, provider, pct_by_code, score_by_code, trade_date
            )
            for code in sorted(held)
        ]
        challengers = [
            ChallengerState(
                code=code, qualified=True,
                line1_percentile=pct_by_code[code],
                composite_score=score_by_code[code],
            )
            for code in sorted(qualified_codes)
            if code in pct_by_code and code not in held
        ]

        # 4. Deterministic rotation proposal (weakest weak × strongest margin).
        proposal = propose_rotation(incumbents, challengers, self._config)

        # 5. Churn gates (yield to protective stops, ≤1/day, cooldowns, block).
        gate = apply_churn_gates(
            proposal,
            self._churn_inputs(proposal, provider, trade_date),
            self._config,
        )
        if not gate.allowed:
            return RotationRunResult(
                signal_id=signal_id, held_count=held_count, portfolio_full=True,
                proposal=proposal, gate_blocked_by=gate.blocked_by,
                newly_opened_entries=newly_opened,
                resolved_intents=resolved, expired_intents=expired,
                reason=gate.reason,
            )

        # 6. Route the rotation SELL through the single construction point +
        #    record the append-only intent (T-day; no buy today).
        return await self._route_rotation_sell(
            proposal=proposal, provider=provider, now=now,
            trade_date=trade_date, signal_id=signal_id,
            next_rebalance_close=next_rebalance_close,
            held_count=held_count, newly_opened=newly_opened,
            resolved=resolved, expired=expired,
        )

    # -- internals ------------------------------------------------------

    def _incumbent_state(
        self,
        code: str,
        provider: RotationContextProvider,
        pct_by_code: Mapping[str, float],
        score_by_code: Mapping[str, float],
        trade_date: str,
    ) -> IncumbentState:
        """Fold screen percentile + entry baseline + Line-2 health into a state.

        A held code outside the screened candidate set scores 0.0 (weaker than
        every top-N name in the comparison pool). The entry baseline + holding
        age come from the first-seen ledger (calendar distance via the provider);
        absent a baseline, ``entry_percentile`` mirrors the current percentile so
        condition 5 (deterioration) fails closed and the age is 0 (too young) —
        no rotation on an unknown baseline.
        """
        health = provider.incumbent_health(code)
        current_pct = pct_by_code.get(code, 0.0)
        entry = self._entries.open_entries().get(code)
        if entry is None:
            entry_pct = current_pct
            holding_age = 0
        else:
            entry_pct = entry.entry_percentile
            holding_age = provider.trading_days_between(
                entry.first_seen_trade_date, trade_date
            )
        return IncumbentState(
            code=code,
            line1_percentile=current_pct,
            composite_score=score_by_code.get(code, 0.0),
            entry_percentile=entry_pct,
            holding_age_trading_days=holding_age,
            protective_stop_active=health.protective_stop_active,
            hard_exit_pending=health.hard_exit_pending,
            score_median_20d=health.score_median_20d,
            score_mad_20d=health.score_mad_20d,
            anomaly_flag_active=health.anomaly_flag_active,
            drawdown_from_local_high=health.drawdown_from_local_high,
            suspended=health.suspended,
            limit_down_unsellable=health.limit_down_unsellable,
            corporate_action_unsafe=health.corporate_action_unsafe,
        )

    def _churn_inputs(
        self,
        proposal: RotationProposal,
        provider: RotationContextProvider,
        trade_date: str,
    ) -> ChurnGateInputs:
        inc = proposal.incumbent_code
        ch = proposal.challenger_code
        last_inc = (
            self._intents.last_rotation_date_for_incumbent(inc)
            if inc is not None else None
        )
        last_pair = (
            self._intents.last_rotation_date_for_pair(ch, inc)
            if (inc is not None and ch is not None) else None
        )
        return ChurnGateInputs(
            rotations_today=provider.rotations_today,
            open_intent_count=len(self._intents.open_intents()),
            daily_new_instruction_budget_remaining=(
                provider.daily_new_instruction_budget_remaining
            ),
            protective_action_needs_cap_today=(
                provider.protective_action_needs_cap_today
            ),
            underinvested_block_active=self._intents.underinvested_block_active(),
            trading_days_since_incumbent_rotation=(
                None if last_inc is None
                else provider.trading_days_between(last_inc, trade_date)
            ),
            trading_days_since_pair_rotation=(
                None if last_pair is None
                else provider.trading_days_between(last_pair, trade_date)
            ),
        )

    async def _route_rotation_sell(
        self,
        *,
        proposal: RotationProposal,
        provider: RotationContextProvider,
        now: datetime,
        trade_date: str,
        signal_id: str,
        next_rebalance_close: str | None,
        held_count: int,
        newly_opened: tuple[str, ...],
        resolved: tuple[str, ...],
        expired: tuple[str, ...],
    ) -> RotationRunResult:
        assert proposal.incumbent_code is not None  # gated by apply_churn_gates
        assert proposal.challenger_code is not None
        inc = proposal.incumbent_code
        health = provider.incumbent_health(inc)
        inc_pct = proposal.incumbent_percentile or 0.0
        ch_pct = proposal.challenger_percentile or 0.0
        reason = (
            f"rotation: weak incumbent {inc} replaced by stronger challenger "
            f"{proposal.challenger_code} (pct {inc_pct:.2f}→{ch_pct:.2f})"
        )
        context = provider.build_rotation_sell_context(
            code=inc, name=health.name, available_volume=health.available_volume,
            limit_price=health.sell_limit_price, reason=reason,
            signal_id=signal_id, seq=1, now=now,
        )
        built = await self._builder.assemble_monitoring_plan(context)

        base = dict(
            signal_id=signal_id, held_count=held_count, portfolio_full=True,
            proposal=proposal, newly_opened_entries=newly_opened,
            resolved_intents=resolved, expired_intents=expired,
        )
        if not isinstance(built, MonitoringPlan):
            log.info("rotation_sell_early_return", signal_id=signal_id, code=inc)
            return RotationRunResult(
                reason="rotation SELL frozen (early return)", **base
            )

        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)
        if plan.status is not InstructionStatus.VALIDATED:
            log.info("rotation_sell_rejected", signal_id=signal_id, code=inc)
            return RotationRunResult(
                sell_plan=plan, reason="rotation SELL rejected by RiskEngine", **base
            )

        wire = self._renderer.render_monitoring_sell(
            plan, anomaly_reason=reason, pilot=self._pilot
        )
        outcome = await self._coordinator.route(
            OutboundSignal(plan=plan, wire_text=wire), now=now
        )

        # Record the append-only intent AFTER routing (the SELL is the rotation's
        # T-day leg; the BUY is the holdings-aware Line-1 fill once the slot settles).
        expires_at = _expires_at(
            trade_date, self._config.expiry.max_trading_days, next_rebalance_close,
            provider,
        )
        intent = build_rotation_intent(
            proposal, created_trade_date=trade_date,
            expires_at_trade_date=expires_at,
            sell_instruction_id=plan.instruction_id,
            signal_id=signal_id, config=self._config,
        )
        self._intents.record_proposed(intent)
        log.info(
            "rotation_sell_routed", signal_id=signal_id,
            instruction_id=plan.instruction_id, intent_id=intent.intent_id,
            action=outcome.action,
        )
        return RotationRunResult(
            sell_routed=True, sell_plan=plan, route_outcome=outcome,
            intent_id=intent.intent_id,
            reason="rotation SELL routed; intent recorded (T-day, no buy today)",
            **base,
        )

    def _resolve_open_intents(
        self,
        *,
        held: frozenset[str],
        qualified_codes: frozenset[str],
        pct_by_code: Mapping[str, float],
        score_by_code: Mapping[str, float],
        trade_date: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Resolve expired open intents; returns (resolved_ids, expired_ids).

        For each open + expired intent (checked in this precedence so a holdings
        drift cannot mis-label a non-executed SELL as a successful rotation —
        codex-review V-004 fix):
        * incumbent STILL held → SELL not executed → ``RESOLVED`` lapse note
          (no slot freed → no under-investment). Checked FIRST: if the owner
          never sold, the rotation simply did not happen, regardless of whether
          the challenger code is coincidentally held for an unrelated reason;
        * incumbent sold AND challenger re-bought → ``RESOLVED`` (slot rotated);
        * incumbent sold but no replacement → 3-path expiry fallback (original
          challenger / best ≥P75 / hold cash + UNDERINVESTED block).
        """
        resolved: list[str] = []
        expired: list[str] = []
        for intent in self._intents.open_intents():
            if not is_expired(intent, trade_date):
                continue
            incumbent_held = intent.incumbent_code in held
            challenger_held = intent.challenger_code in held
            if incumbent_held:
                self._intents.record_resolved(
                    intent.intent_id, trade_date=trade_date,
                    note="rotation SELL not executed within expiry; intent lapsed",
                )
                resolved.append(intent.intent_id)
            elif challenger_held:
                self._intents.record_resolved(intent.intent_id, trade_date=trade_date)
                resolved.append(intent.intent_id)
            else:
                # Sold but not yet rebought → deterministic 3-path fallback.
                original_qualified = intent.challenger_code in qualified_codes
                best = _best_challenger(
                    qualified_codes, held, pct_by_code, score_by_code,
                    min_percentile=self._config.challenger_margin.min_percentile,
                )
                outcome = resolve_expiry(
                    intent, original_challenger_qualified=original_qualified,
                    best_challenger=best, config=self._config,
                )
                self._intents.record_expired(
                    intent.intent_id, trade_date=trade_date, outcome=outcome
                )
                expired.append(intent.intent_id)
                if outcome.kind is ExpiryOutcomeKind.UNDERINVESTED:
                    log.warning(
                        "rotation_underinvested",
                        intent_id=intent.intent_id, incumbent=intent.incumbent_code,
                    )
        return tuple(resolved), tuple(expired)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _percentile_map(screen: ScreenResult) -> tuple[dict[str, float], dict[str, float]]:
    """code → (line1_percentile, composite score) over the screened candidates.

    The screener's ``score`` is already a **full-survivor** cross-sectional
    measure — a fixed-weight blend of each factor's percentile rank over the
    WHOLE qualified universe (``_percentile_ranks`` runs before the top-N cap),
    so it lands in [0, 1] with higher = stronger. We use it directly as
    ``line1_percentile`` rather than re-ranking WITHIN the ≤top-N candidate list:
    a held name at the bottom of the top-N (but top-2% of the full market) keeps
    its high score → it is NOT spuriously flagged weak (codex-review V-004 fix —
    a within-pool rank would mis-calibrate the P40/P75 full-market thresholds the
    slot_portfolio contract expects). For ship-first ``line1_percentile`` equals
    ``composite_score``; a rank-distinct percentile awaits the screener exposing
    full-universe ranks. A held code absent from the pool is scored 0.0 by the
    caller (weaker than every screened name).
    """
    pct: dict[str, float] = {}
    score: dict[str, float] = {}
    for c in screen.candidates:
        pct[c.code] = c.score
        score[c.code] = c.score
    return pct, score


def _best_challenger(
    qualified_codes: frozenset[str],
    held: frozenset[str],
    pct_by_code: Mapping[str, float],
    score_by_code: Mapping[str, float],
    *,
    min_percentile: float,
) -> ChallengerState | None:
    """Strongest qualified, not-held challenger at >= P75 (None if none)."""
    eligible = [
        ChallengerState(
            code=code, qualified=True,
            line1_percentile=pct_by_code[code], composite_score=score_by_code[code],
        )
        for code in sorted(qualified_codes)
        if code in pct_by_code and code not in held
        and pct_by_code[code] >= min_percentile
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.composite_score, _neg_code(c.code)))


def _neg_code(code: str) -> tuple[int, ...]:
    """Order codes ascending under ``max`` (mirror the screener's code-asc tie)."""
    return tuple(-ord(ch) for ch in code)


def _expires_at(
    trade_date: str,
    max_trading_days: int,
    next_rebalance_close: str | None,
    provider: RotationContextProvider,
) -> str:
    """``min(max_td trading days ahead, next rebalance close)`` (YYYYMMDD).

    The forward calendar arithmetic is delegated to the provider; lexical min on
    zero-padded YYYYMMDD == chronological min.
    """
    horizon = provider.trading_day_ahead(trade_date, max_trading_days)
    if next_rebalance_close is None:
        return horizon
    return min(horizon, next_rebalance_close)


__all__ = [
    "ROTATION_SIGNAL_PREFIX",
    "IncumbentHealth",
    "RotationContextProvider",
    "RotationRunResult",
    "RotationRunner",
]
