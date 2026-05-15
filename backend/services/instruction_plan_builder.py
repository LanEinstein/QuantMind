"""InstructionPlanBuilder — five-early-return gate before RiskEngine.

Per CLAUDE.md §2.4 + §2.7 + the binding decisions
(P0-1 / P0-5 / P0-7 / P0-8 / P0-9), every BUY/SELL candidate goes
through five freeze-source checks BEFORE :class:`RiskEngine` runs its
14-check. Any check that fires emits a stable
``reason_namespace`` audit event and **no InstructionPlan is built** —
the acceptance line is "任一冻结源为真不产生 BUY/SELL plan, ledger 留
reason" (D-003 task entry in ``docs/plan.html``).

The five checks, in locked first-wins order:

1. ``mode_switch_in_progress`` — D-005 ModeRouter lifecycle freeze
2. ``reconciliation_ticket_open`` — P0-5 OPEN/EXPIRED ticket freezes
   routing
3. ``circuit_breaker_cooldown`` — P0-7 CB halts BUY only
   (``SELL`` not halted, lets the user exit positions)
4. ``data_quality_breach`` — P0-8 DataQualityState fails
   ``is_acceptable_for_buy_sell`` (the four blocking breaches only;
   news / MiroFish / snapshot outage are non-blocking per redline)
5. ``watchlist_exclusion`` — P0-9 13-code universe + 4 thresholds + ST
   + forbidden boards (STAR / 北交 / 可转债 / B-share)

Each early return writes ONE
:class:`backend.audit.models.AuditEvent` of type
``BUILDER_EARLY_RETURN`` with ``reason_namespace`` set to the source
identifier above; ``payload`` carries source-specific structured fields
(ticket_id, primary_quote_age_seconds, exclusion sub-reason, etc.) so
``scripts/query_audit.py`` and the front-end three-tab Reason drawer
(P1-5 §1.5 Builder tab) can grep deterministically.

decision_ledger is **not** opened from this module — it is keyed by
``instruction_id`` and there is no plan to point at when a freeze
fires. Once D-004 wires the proceed-path, the full InstructionPlan
(BUY/SELL or HOLD) will open the ledger entry there.

LLM red line: this module never imports
``backend.{llm,agents,mirofish}`` (P0-10 §2 redline 1). The
``test_builder_import_isolation`` subprocess probe asserts the closure
holds at every commit; the lint redline-check.sh adds an AST scan.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.data.data_quality import DataQualityState
from backend.data.stock_metadata import (
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    is_st_name,
)
from backend.models.instruction import InstructionSide
from backend.models.reconciliation import (
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.risk.circuit_breaker import CircuitBreaker
from backend.services.watchlist_policy import WatchlistPolicy

log = structlog.get_logger(component="services.instruction_plan_builder")

_SH = ZoneInfo("Asia/Shanghai")
_CODE_RE = re.compile(r"^\d{6}$")
"""Same regex InstructionPlan / Order use; mirrored here so the early
return can fail fast on a malformed candidate code (the audit event
still records the raw input under ``stock_code_raw``)."""

# Reason-namespace strings — each one is a stable audit grep key. They
# are imported by tests + scripts/query_audit.py; renaming a value is a
# breaking change to existing audit consumers.
REASON_MODE_SWITCH = "mode_switch_in_progress"
REASON_TICKET_OPEN = "reconciliation_ticket_open"
REASON_CIRCUIT_BREAKER = "circuit_breaker_cooldown"
REASON_DATA_QUALITY = "data_quality_breach"
REASON_WATCHLIST = "watchlist_exclusion"

# Watchlist sub-reasons surface in payload['exclusion_sub_reason'] so
# the front-end Reason drawer can show the precise rejection cause
# without re-deriving it from message text. Locked enum-like set.
WATCHLIST_SUB_REASONS: frozenset[str] = frozenset(
    {
        "non_watchlist_code",
        "forbidden_board",
        "unknown_code",
        "is_st",
        "ipo_too_new",
        "sub_new_too_new",
        "liquidity_too_low",
        "price_too_high",
    }
)


class FreezeSource(StrEnum):
    """Stable identifier for the five Builder early-return sources.

    Order is locked (matches the locked first-wins evaluation order in
    :func:`InstructionPlanBuilder.evaluate_candidate`). The string
    values mirror :data:`REASON_*` constants so audit consumers can use
    either form interchangeably.
    """

    MODE_SWITCH = REASON_MODE_SWITCH
    TICKET_OPEN = REASON_TICKET_OPEN
    CIRCUIT_BREAKER = REASON_CIRCUIT_BREAKER
    DATA_QUALITY = REASON_DATA_QUALITY
    WATCHLIST = REASON_WATCHLIST


# Locked freeze-source → AuditEventType mapping. Category 4 BUILDER_EARLY_RETURN
# is the universal type; the source identification lives in
# ``reason_namespace``. A FREEZE_SOURCE_* event type also exists per
# source, but those are reserved for system-level transitions
# (D-005 ModeRouter / EOD pipeline) — Builder per-candidate evaluations
# stay on BUILDER_EARLY_RETURN to keep the Category 4 row count
# bounded.
_AUDIT_EVENT_TYPE = AuditEventType.BUILDER_EARLY_RETURN

# Mode-switch and Mongo-backed mode-switch lifecycles land with D-005.
# Until then a default probe that always reports inactive lets the rest
# of the builder operate without breaking the contract; tests inject a
# stub that flips to True to exercise the early-return.
_DEFAULT_MODE_SWITCH_ACTIVE = False


# ---------------------------------------------------------------------------
# Probes & inputs
# ---------------------------------------------------------------------------


@runtime_checkable
class ModeSwitchProbe(Protocol):
    """Reports whether a mode-switch lifecycle is currently freezing trades.

    The full implementation (archive + MockBroker reset + Feishu initial
    reconciliation + freeze window) lands in D-005. For D-003 the
    builder only reads ``is_active()``; the probe is dependency-injected
    so the lifecycle module can swap in the real handler without
    touching Builder code.
    """

    def is_active(self) -> bool:
        """Return True iff a switch lifecycle is currently freezing routing."""


class _StaticModeSwitchProbe:
    """Default no-op probe used when D-005 wiring is not yet present."""

    def __init__(self, *, active: bool = _DEFAULT_MODE_SWITCH_ACTIVE) -> None:
        self._active = active

    def is_active(self) -> bool:
        return self._active


@dataclass(frozen=True)
class WatchlistMarketSignal:
    """Market signals required to evaluate the four P0-9 thresholds.

    The InstructionPlanBuilder receives these by-value so the watchlist
    early-return stays a pure function on input data. Production
    callers assemble the snapshot from ``DataQualityProvider`` /
    ``MarketMetaProvider`` outputs (P1-2.B/C); tests construct it
    directly.

    Attributes:
        listed_at_trading_days: trading days since IPO (inclusive of
            today). ``None`` is treated as "unknown" and triggers
            ``ipo_too_new`` conservatively (fail-closed).
        avg_amount_20d_yuan: 20-day average daily turnover in CNY. The
            P0-9 threshold is ``min_avg_amount_20d_yuan`` (default
            200_000_000). ``None`` triggers ``liquidity_too_low``.
        last_price_yuan: latest available unit price in CNY for the
            P0-9 ``max_unit_price_yuan`` cap (default 500.0). ``None``
            triggers ``price_too_high`` (we cannot prove the cap is
            respected).
    """

    listed_at_trading_days: int | None
    avg_amount_20d_yuan: float | None
    last_price_yuan: float | None


@dataclass(frozen=True)
class CandidateInputs:
    """Bundle of every input the five early-returns consume.

    Frozen on purpose: the Builder treats the candidate snapshot as
    immutable once constructed, which makes per-check unit tests
    trivial and keeps the orchestrator from accidentally mutating
    upstream state.

    Attributes:
        stock_code: 6-digit A-share code (regex enforced).
        stock_name: display name; consulted by the watchlist exclusion
            check for the ST/*ST/退/PT marker.
        side: must be BUY or SELL — HOLD candidates skip the Builder
            and route to the dedicated HOLD path in D-004.
        now: tz-aware Asia/Shanghai timestamp at which the evaluation
            is run; circuit-breaker cooldown uses it for the elapsed
            comparison.
        open_tickets: every reconciliation ticket whose status is in
            ``{OPEN, EXPIRED}`` at evaluation time. Resolution (the
            three RESOLVED_* statuses) drops the ticket from this
            iterable.
        circuit_breaker: in-process CB instance owned by the trading
            loop. The Builder calls ``is_halted(now)`` (read-only).
        data_quality: per-stock DataQualityState produced by
            :class:`backend.data.data_quality.DataQualityProvider`.
        watchlist_policy: loaded P0-9 policy.
        watchlist_signal: numeric watchlist exclusion inputs (per
            :class:`WatchlistMarketSignal`).
    """

    stock_code: str
    stock_name: str
    side: InstructionSide
    now: datetime
    open_tickets: tuple[ReconciliationTicket, ...]
    circuit_breaker: CircuitBreaker
    data_quality: DataQualityState
    watchlist_policy: WatchlistPolicy
    watchlist_signal: WatchlistMarketSignal

    def __post_init__(self) -> None:
        if self.side is InstructionSide.HOLD:
            raise ValueError(
                "InstructionPlanBuilder does not handle HOLD candidates — "
                "the HOLD path is owned by D-004 (FundManagerOutput → "
                "InstructionPlan)"
            )
        if self.now.tzinfo is None:
            raise ValueError("CandidateInputs.now must be timezone-aware")
        if not _CODE_RE.fullmatch(self.stock_code):
            raise ValueError(
                f"CandidateInputs.stock_code {self.stock_code!r} must be 6 digits"
            )
        if not self.stock_name:
            raise ValueError("CandidateInputs.stock_name must be non-empty")


# ---------------------------------------------------------------------------
# Result envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuilderEarlyReturn:
    """The Builder rejected the candidate before reaching RiskEngine.

    Attributes:
        source: which freeze-source fired (locked enum).
        reason_namespace: stable audit grep key (matches ``source.value``).
        message: short human-readable reason (≤ 256 chars). Goes into
            ``log.warning`` and the front-end Reason drawer.
        payload: structured key/value detail surfaced in the audit
            event; values must be JSON-serialisable primitives because
            ``AuditEvent.payload`` flows into the audit_events Mongo
            collection / JSONL backup.
    """

    source: FreezeSource
    reason_namespace: str
    message: str
    payload: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class BuilderProceed:
    """All five early returns passed; D-004 may now run RiskEngine + assemble.

    Carries the validated candidate inputs forward unchanged so the
    next stage does not re-derive them.
    """

    candidate: CandidateInputs


BuilderResult = BuilderEarlyReturn | BuilderProceed


# ---------------------------------------------------------------------------
# Pure check functions — module-level for unit-test convenience
# ---------------------------------------------------------------------------


def check_mode_switch(probe: ModeSwitchProbe) -> BuilderEarlyReturn | None:
    """First early return: mode-switch lifecycle is freezing routing.

    Returns ``None`` when the probe reports inactive. The probe contract
    is read-only — the Builder must not toggle the lifecycle from here.
    """
    if probe.is_active():
        return BuilderEarlyReturn(
            source=FreezeSource.MODE_SWITCH,
            reason_namespace=REASON_MODE_SWITCH,
            message=(
                "mode_switch lifecycle is freezing BUY/SELL routing (D-005 ModeRouter)"
            ),
            payload={"probe": "mode_switch", "active": True},
        )
    return None


def check_ticket_freeze(
    open_tickets: Iterable[ReconciliationTicket],
) -> BuilderEarlyReturn | None:
    """Second early return: any reconciliation ticket OPEN/EXPIRED.

    The :data:`open_tickets` argument is the **already-filtered** view
    (callers project the active ticket list). Here we still re-check
    each ticket's status because a passing test that hands in a
    RESOLVED ticket should not silently freeze.
    """
    for ticket in open_tickets:
        if ticket.status in {
            ReconciliationTicketStatus.OPEN,
            ReconciliationTicketStatus.EXPIRED,
        }:
            return BuilderEarlyReturn(
                source=FreezeSource.TICKET_OPEN,
                reason_namespace=REASON_TICKET_OPEN,
                message=(
                    f"reconciliation ticket {ticket.ticket_id} is "
                    f"{ticket.status.value}; routing frozen"
                ),
                payload={
                    "ticket_id": ticket.ticket_id,
                    "ticket_status": ticket.status.value,
                    "trade_date": ticket.trade_date,
                },
            )
    return None


def check_circuit_breaker(
    breaker: CircuitBreaker,
    side: InstructionSide,
    now: datetime,
) -> BuilderEarlyReturn | None:
    """Third early return: circuit breaker is in cooldown.

    P0-7 §1.3 locks SELL as **never halted** (lets the user exit
    positions even after the breaker trips). The check therefore
    short-circuits to ``None`` for SELL before consulting the CB.

    ``breaker.is_halted(now)`` will auto-expire the halt if the
    cooldown window has passed; this side-effect is intentional and
    matches CircuitBreaker's documented contract.
    """
    if side is InstructionSide.SELL:
        return None
    if not breaker.is_halted(now):
        return None
    return BuilderEarlyReturn(
        source=FreezeSource.CIRCUIT_BREAKER,
        reason_namespace=REASON_CIRCUIT_BREAKER,
        message="circuit breaker cooldown active; BUY routing frozen",
        payload={
            "side": side.value,
            "halted": True,
        },
    )


def check_data_quality(
    state: DataQualityState,
) -> BuilderEarlyReturn | None:
    """Fourth early return: DataQualityState fails the BUY/SELL gate.

    Only the four blocking breaches in
    :pyattr:`DataQualityState.is_acceptable_for_buy_sell` participate;
    the three non-blocking breaches (news / MiroFish / snapshot
    outage) never freeze trading per P0-8 §2 redline 11. The
    ``degradation_reason`` string is forwarded into ``payload`` so
    audit grep can isolate the specific breach combination without
    re-deriving it.
    """
    if state.is_acceptable_for_buy_sell:
        return None
    return BuilderEarlyReturn(
        source=FreezeSource.DATA_QUALITY,
        reason_namespace=REASON_DATA_QUALITY,
        message=(
            f"data quality gate blocked: {state.degradation_reason or 'unspecified'}"
        ),
        payload={
            "degradation_reason": state.degradation_reason,
            "primary_quote_age_seconds": state.primary_quote_age_seconds,
            "backup_quote_age_seconds": state.backup_quote_age_seconds,
            "quote_unavailable": state.quote_unavailable,
            "quote_staleness_breach": state.quote_staleness_breach,
            "quote_divergence_breach": state.quote_divergence_breach,
            "minimum_freshness_breach": state.minimum_freshness_breach,
        },
    )


def check_watchlist_exclusion(
    code: str,
    name: str,
    policy: WatchlistPolicy,
    signal: WatchlistMarketSignal,
) -> BuilderEarlyReturn | None:
    """Fifth early return: P0-9 watchlist exclusion (8 sub-reasons).

    Sub-reason precedence (first-match wins inside this check):

    1. ``forbidden_board`` — STAR / 北交 / 可转债 / B-share
       (data-layer ``ForbiddenCodeError``)
    2. ``unknown_code`` — code prefix matches no allowlist
    3. ``non_watchlist_code`` — board is allowed but the code is not
       in the loaded P0-9 13-code universe
    4. ``is_st`` — name carries an ST / *ST / 退 / PT marker
    5. ``ipo_too_new`` — listed-trading-days < 30 (or unknown)
    6. ``sub_new_too_new`` — 30 ≤ listed < 180
    7. ``liquidity_too_low`` — 20-day avg amount below threshold
       (or unknown)
    8. ``price_too_high`` — last price above 500.0 cap (or unknown)

    All eight sub-reason strings are members of
    :data:`WATCHLIST_SUB_REASONS`; an unknown sub-reason on a future
    refactor must update both this function and the frozenset together.
    """
    rules = policy.exclusion_rules
    sub_reason: str | None = None
    payload: dict[str, str | int | float | bool | None] = {"stock_code": code}

    try:
        board = classify_board(code)
    except ForbiddenCodeError as exc:
        sub_reason = "forbidden_board"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "forbidden_reason": exc.reason,
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=f"code {code} is on forbidden board: {exc.reason}",
            payload=payload,
        )
    except UnknownCodeError:
        sub_reason = "unknown_code"
        payload.update({"exclusion_sub_reason": sub_reason})
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=f"code {code} matches no known board prefix",
            payload=payload,
        )

    payload["board"] = board.value

    universe = policy.all_watchlist_codes()
    if code not in universe:
        sub_reason = "non_watchlist_code"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "watchlist_size": len(universe),
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=(
                f"code {code} is not in the P0-9 watchlist (size={len(universe)})"
            ),
            payload=payload,
        )

    if is_st_name(name):
        sub_reason = "is_st"
        payload.update({"exclusion_sub_reason": sub_reason, "name": name})
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=f"stock {code} ({name}) carries ST / *ST / 退 / PT marker",
            payload=payload,
        )

    listed_days = signal.listed_at_trading_days
    if listed_days is None or listed_days < rules.ipo_min_trading_days:
        sub_reason = "ipo_too_new"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "listed_at_trading_days": listed_days,
                "threshold": rules.ipo_min_trading_days,
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=(
                f"code {code} listed only {listed_days} trading days "
                f"(< {rules.ipo_min_trading_days} threshold)"
            ),
            payload=payload,
        )

    if listed_days < rules.sub_new_min_trading_days:
        sub_reason = "sub_new_too_new"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "listed_at_trading_days": listed_days,
                "threshold": rules.sub_new_min_trading_days,
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=(
                f"code {code} is sub-new ({listed_days} trading days "
                f"< {rules.sub_new_min_trading_days})"
            ),
            payload=payload,
        )

    avg_amount = signal.avg_amount_20d_yuan
    if avg_amount is None or avg_amount < rules.min_avg_amount_20d_yuan:
        sub_reason = "liquidity_too_low"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "avg_amount_20d_yuan": avg_amount,
                "threshold": rules.min_avg_amount_20d_yuan,
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=(
                f"code {code} 20-day avg amount {avg_amount} yuan "
                f"below {rules.min_avg_amount_20d_yuan}"
            ),
            payload=payload,
        )

    last_price = signal.last_price_yuan
    if last_price is None or last_price > rules.max_unit_price_yuan:
        sub_reason = "price_too_high"
        payload.update(
            {
                "exclusion_sub_reason": sub_reason,
                "last_price_yuan": last_price,
                "threshold": rules.max_unit_price_yuan,
            }
        )
        return BuilderEarlyReturn(
            source=FreezeSource.WATCHLIST,
            reason_namespace=REASON_WATCHLIST,
            message=(
                f"code {code} last price {last_price} above "
                f"{rules.max_unit_price_yuan} cap"
            ),
            payload=payload,
        )

    # All sub-reasons cleared — the watchlist check is the last in the
    # chain, so reaching here means the candidate progresses to D-004.
    assert sub_reason is None  # noqa: S101 — invariant guard, not a runtime branch
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class InstructionPlanBuilder:
    """Assembles BUY/SELL InstructionPlans gated by the five early returns.

    For D-003 the Builder owns ONLY the early-return chain and its
    audit emissions. The ``proceed`` path (RiskEngine 14-check,
    fund_manager → InstructionPlan, debate enforcement) lands with
    D-004; the orchestrator returns :class:`BuilderProceed` once all
    five gates clear, leaving D-004 to consume it.

    The class is intentionally small: it composes the five pure check
    functions and wraps audit emission. Callers inject the audit store,
    a mode-switch probe, and the five sources of state — making the
    Builder trivially unit-testable without spinning up Mongo.
    """

    def __init__(
        self,
        *,
        audit_store: AuditStore,
        mode_switch_probe: ModeSwitchProbe | None = None,
    ) -> None:
        self._audit = audit_store
        self._mode_switch = mode_switch_probe or _StaticModeSwitchProbe()

    async def evaluate_candidate(self, candidate: CandidateInputs) -> BuilderResult:
        """Run the five early-return chain in locked order.

        Returns :class:`BuilderEarlyReturn` for the first source that
        fires (writing one audit event), or :class:`BuilderProceed`
        when all five clear (no audit event written — the audit trail
        for proceed-path plans starts in D-004 once the InstructionPlan
        is constructed).
        """

        early = check_mode_switch(self._mode_switch)
        if early is not None:
            await self._record_audit(early, candidate)
            return early

        early = check_ticket_freeze(candidate.open_tickets)
        if early is not None:
            await self._record_audit(early, candidate)
            return early

        early = check_circuit_breaker(
            candidate.circuit_breaker,
            candidate.side,
            candidate.now,
        )
        if early is not None:
            await self._record_audit(early, candidate)
            return early

        early = check_data_quality(candidate.data_quality)
        if early is not None:
            await self._record_audit(early, candidate)
            return early

        early = check_watchlist_exclusion(
            candidate.stock_code,
            candidate.stock_name,
            candidate.watchlist_policy,
            candidate.watchlist_signal,
        )
        if early is not None:
            await self._record_audit(early, candidate)
            return early

        return BuilderProceed(candidate=candidate)

    async def _record_audit(
        self,
        early: BuilderEarlyReturn,
        candidate: CandidateInputs,
    ) -> None:
        """Persist a BUILDER_EARLY_RETURN audit event for the early return.

        ``resource_type`` is fixed to ``instruction_plan_candidate`` so
        ``scripts/query_audit.py --resource-type instruction_plan_candidate``
        can list every candidate the Builder bounced. ``resource_id``
        carries the candidate code so the front-end Reason drawer can
        join Builder events with the matching watchlist tile.
        """

        merged_payload: dict[str, str | int | float | bool | None] = {
            "stock_code": candidate.stock_code,
            "stock_name": candidate.stock_name,
            "side": candidate.side.value,
            "evaluated_at": candidate.now.astimezone(_SH).isoformat(),
        }
        merged_payload.update(early.payload)

        log.warning(
            "builder_early_return",
            source=early.source.value,
            reason_namespace=early.reason_namespace,
            stock_code=candidate.stock_code,
            side=candidate.side.value,
            message=early.message,
        )

        await self._audit.write(
            event_type=_AUDIT_EVENT_TYPE,
            actor=AuditActor.SYSTEM,
            resource_type="instruction_plan_candidate",
            resource_id=candidate.stock_code,
            payload=merged_payload,
            outcome=AuditOutcome.BLOCKED,
            reason_namespace=early.reason_namespace,
            timestamp=candidate.now,
        )


__all__ = [
    "REASON_CIRCUIT_BREAKER",
    "REASON_DATA_QUALITY",
    "REASON_MODE_SWITCH",
    "REASON_TICKET_OPEN",
    "REASON_WATCHLIST",
    "WATCHLIST_SUB_REASONS",
    "BuilderEarlyReturn",
    "BuilderProceed",
    "BuilderResult",
    "CandidateInputs",
    "FreezeSource",
    "InstructionPlanBuilder",
    "ModeSwitchProbe",
    "WatchlistMarketSignal",
    "check_circuit_breaker",
    "check_data_quality",
    "check_mode_switch",
    "check_ticket_freeze",
    "check_watchlist_exclusion",
]
