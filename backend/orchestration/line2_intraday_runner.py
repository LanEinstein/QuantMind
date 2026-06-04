"""Line-2 30s intraday trigger runner (Phase U-C3).

The Line-2 (held-position monitoring) **30s intraday** production entry point —
the deterministic, **zero-LLM** sibling of the daily anomaly runner (U-C2).
Every 30s during trading hours the U-D1 scheduler invokes one tick:

    held positions → fetch live spots (timeout) → partition_by_suspension →
    filter_fresh_quotes → deterministic intraday triggers (drawdown / ATR
    trailing stop → SELL; oversold-vs-cost → ADD) → [persist quote snapshot +
    intraday manifest BEFORE routing] → assemble_monitoring_plan (single
    construction point, 14-check) → RouteCoordinator

Unlike the daily detector (statistical anomalies over the T-1 EOD frame) this
runner fires **deterministic** triggers against a live quote
(``backend.monitoring.intraday_triggers``); the SELL/ADD direction is derived
deterministically and never passes through the fund_manager / 4-agent debate
(R0 §8 / P0-10-amendment-2026-05-25-line2). The ``LINE2-MON-`` signal_id prefix
marks the no-debate monitoring path.

Seven defensive invariants (§设计4) are enforced here, each tested:

1. **no overlapping tick** — a cooperative in-flight flag skips a tick that
   fires while the previous one is still running;
2. **per-tick timeout** — the live-quote fetch is bounded by
   ``tick_timeout_seconds``; a timeout fails the tick closed (skip), never hangs;
3. **stale / missing price fail-closed** — a code with a stale or missing spot
   does not trigger this tick (``filter_fresh_quotes``);
4. **static holidays.yaml** — a non-trading day skips the whole tick;
5. **trading-hours gating** — outside 09:30–11:30 / 13:00–15:00 skips;
6. **suspension partition** — a halted holding degrades cleanly (no SELL/ADD);
7. **trigger ⇒ durable persistence (before routing)** — on a trigger tick the
   live quotes are persisted as a :class:`MarketDataSnapshot` (raw bytes +
   checksum) + an :class:`IntradayTriggerManifest` (parent snapshot id +
   consumed-quote lineage + rule inputs) BEFORE any signal routes, so a crash
   cannot lose the replay lineage.

A per-day ``(code, trigger_kind)`` dedup stops a still-breached trigger from
re-routing every 30s (it would spam the decision group); distinct trigger kinds
are distinct keys (P-005) so a morning ADD does not suppress an afternoon SELL,
and a drawdown-stop does not suppress a later take-profit on the same code.
Only DELIVERED routes (dispatched / simulation_routed / dry_run_rendered) and
REJECTED plans enter the dedup — a definitive Feishu ``send_failed`` reached
nobody and must retry on the next tick (codex P2, ops hardening §1.1). The
dedup is persisted per day (``FiredTriggerStore``) so a restart cannot re-send
an already-delivered batch, and a code whose SELL fired earlier today gets no
contradicting same-day ADD (one-way mutex — an ADD never suppresses a SELL).

LLM red line (orchestration isolation + Line-2 zero-LLM): imports NO
``backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}``. The
``backend.monitoring`` triggers are pure-quant (themselves import-clean of
llm/agents); the heavy risk/broker objects + the live-quote fetch + the
``SnapshotStore`` / manifest store are supplied by the caller's
:class:`Line2IntradayContextProvider` (the U-D1 scheduler / ``main.py``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import (
    MarketDataSnapshot,
    SnapshotStore,
    build_consumed_row,
)
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.monitoring.add_position import (
    AddConfig,
    AddIntent,
    classify_regime,
    moving_average,
    parse_held_series,
)
from backend.monitoring.degrade import partition_by_suspension
from backend.monitoring.intraday_calibration import (
    FEATURE_CODE_VERSION as CALIBRATION_FEATURE_VERSION,
)
from backend.monitoring.intraday_calibration import (
    DrawdownCalibrationConfig,
    TakeProfitCalibrationConfig,
    TieredTakeProfitConfig,
)
from backend.monitoring.intraday_triggers import (
    FEATURE_CODE_VERSION,
    IntradaySellIntent,
    IntradayTriggerConfig,
    evaluate_intraday_add_intents,
    evaluate_intraday_sell_intents,
    filter_fresh_quotes,
    serialize_intraday_quotes,
)
from backend.monitoring.thesis_break import (
    evaluate_thesis_breaks,
    intraday_intact_codes,
)
from backend.orchestration.fired_trigger_store import FiredTriggerStore
from backend.orchestration.instruction_dispatcher import OutboundSignal
from backend.orchestration.intraday_manifest import (
    IntradayTriggerManifest,
    IntradayTriggerManifestStore,
    IntradayTriggerRecord,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.orchestration.takeprofit_ledger import (
    TakeProfitLedgerError,
    TakeProfitLedgerStore,
)
from backend.services.instruction_plan_builder import (
    MONITORING_SIGNAL_PREFIX,
    InstructionPlanBuilder,
    MonitoringAssemblyContext,
    MonitoringPlan,
)
from backend.services.ledger import DecisionLedgerService
from backend.utils.trading_hours import is_trading_day, is_trading_hours

log = structlog.get_logger(component="orchestration.line2_intraday_runner")

# Bounded retry for undelivered (send_failed) routes: after this many failed
# delivery attempts in one day the (code, kind) key enters the dedup anyway —
# a sustained Feishu outage must not re-build/re-persist/re-send every 30s
# for the whole session (ops hardening §1.1, review angle B).
_MAX_UNDELIVERED_ATTEMPTS_PER_DAY = 5


class IntradayTickOutcome(StrEnum):
    """Terminal outcome of one 30s tick (audit-grade)."""

    SCANNED = "scanned"
    """The tick ran an intraday scan (may or may not have routed a signal)."""
    SKIPPED_OVERLAP = "skipped_overlap"
    """A previous tick was still in flight (no overlapping run)."""
    SKIPPED_TIMEOUT = "skipped_timeout"
    """The live-quote fetch timed out — tick failed closed."""
    SKIPPED_NON_TRADING_DAY = "skipped_non_trading_day"
    """``holidays.yaml`` says today is not a trading day."""
    SKIPPED_OFF_HOURS = "skipped_off_hours"
    """Outside the A-share trading sessions (incl. lunch break)."""
    EMPTY_PORTFOLIO = "empty_portfolio"
    """No held positions — nothing to monitor."""


class TriggerRouteOutcome(StrEnum):
    """Terminal outcome of one fired trigger's routing."""

    ROUTED = "routed"
    REJECTED = "rejected"
    EARLY_RETURN = "early_return"
    DEDUP_SKIPPED = "dedup_skipped"


@dataclass(frozen=True)
class TriggerRoute:
    """Per-fired-trigger routing summary."""

    code: str
    side: InstructionSide
    kind: str
    outcome: TriggerRouteOutcome
    route_outcome: RouteOutcome | None = None
    plan: InstructionPlan | None = None


@dataclass(frozen=True)
class Line2IntradayRunResult:
    """Audit-grade summary of one Line-2 intraday tick."""

    signal_id: str
    tick_outcome: IntradayTickOutcome
    held_count: int = 0
    active_count: int = 0
    degraded_codes: tuple[str, ...] = ()
    stale_codes: tuple[str, ...] = ()
    quote_snapshot_id: str | None = None
    routes: tuple[TriggerRoute, ...] = ()


@runtime_checkable
class Line2IntradayContextProvider(Protocol):
    """Caller-supplied bridge to the objects the runner must not import
    (orchestration isolation + Line-2 zero-LLM).

    Implemented by the U-D1 scheduler; tests inject a fake. ``held_positions``
    / ``account`` / the spot values are typed ``Any`` because their concrete
    types (``backend.broker.Position`` / ``AccountInfo`` /
    ``backend.models.market.WatchlistMarketSnapshot``) live in packages the
    runner is forbidden to import — it only passes them through the pure
    monitoring functions + the per-code context builders.
    """

    @property
    def held_positions(self) -> Sequence[Any]:
        """Current held positions (``backend.broker.Position`` objects)."""
        ...

    @property
    def name_by_code(self) -> Mapping[str, str]:
        """code → display name for the intent / rendered message."""
        ...

    @property
    def account(self) -> Any:
        """Current ``AccountInfo`` (ADD sizing reads ``total_assets``)."""
        ...

    @property
    def daily_frame(self) -> MarketDataSnapshot:
        """T-1 EOD CSV market-frame (ATR / recent-high source + lineage)."""
        ...

    @property
    def index_closes(self) -> tuple[float, ...]:
        """Benchmark index daily closes for the bear-regime ADD ban."""
        ...

    async def fetch_spots(self, codes: Sequence[str]) -> Mapping[str, Any]:
        """Fetch the held codes' live spots (batch ``get_watchlist_snapshot``).

        Contract: each spot's ``snapshot_at`` must be strictly before the tick
        ``now`` (the decision time the scheduler passes to :meth:`run`) — i.e.
        capture ``now`` at/after the fetch. A quote tagged at or after ``now``
        fails closed (no trigger), mirroring the InstructionPlan strictly-before
        invariant, so it can never crash the plan build after the tick persisted.
        """
        ...

    def build_sell_context(
        self,
        intent: IntradaySellIntent,
        *,
        signal_id: str,
        seq: int,
        now: datetime,
        snapshot_at: datetime,
    ) -> MonitoringAssemblyContext:
        """Build the SELL context (supplies per-code risk/broker objects)."""
        ...

    def build_add_context(
        self,
        intent: AddIntent,
        *,
        signal_id: str,
        seq: int,
        now: datetime,
        snapshot_at: datetime,
    ) -> MonitoringAssemblyContext:
        """Build the ADD (BUY) context (supplies per-code risk/broker objects)."""
        ...


@runtime_checkable
class RejectAlertHook(Protocol):
    """Caller-supplied async hook fired when a SELL route is REJECTED.

    P0-10-amendment-line2-2026-06-04-intraday-ops-hardening §1.2: a
    RiskEngine-rejected protective SELL used to die silently (audit/log
    only) — the prev_close data gap swallowed every Line-2 exit for two
    trading days before a human noticed. The hook (main.py routes it to
    the AlertDispatcher → Feishu alert chat, dedup 15min) makes the next
    such swallow visible within minutes. Injected so the runner keeps its
    orchestration import isolation (no backend.monitoring.alert_dispatcher
    import); any hook exception is swallowed by the caller (fail-open —
    alerting must never break a tick).
    """

    async def __call__(
        self, *, code: str, kind: str, instruction_id: str
    ) -> None: ...


class Line2IntradayRunner:
    """Compose the Line-2 30s intraday trigger chain into one production tick."""

    def __init__(
        self,
        *,
        builder: InstructionPlanBuilder,
        renderer: MessageRenderer,
        coordinator: RouteCoordinator,
        ledger: DecisionLedgerService,
        snapshot_store: SnapshotStore,
        manifest_store: IntradayTriggerManifestStore,
        trigger_config: IntradayTriggerConfig | None = None,
        add_config: AddConfig | None = None,
        drawdown_calibration: DrawdownCalibrationConfig | None = None,
        regime_drawdown_enabled: bool = False,
        takeprofit_calibration: TakeProfitCalibrationConfig | None = None,
        tiered_takeprofit: TieredTakeProfitConfig | None = None,
        takeprofit_ledger: TakeProfitLedgerStore | None = None,
        fired_store: FiredTriggerStore | None = None,
        reject_alert_hook: RejectAlertHook | None = None,
        tick_timeout_seconds: float = 10.0,
        pilot: bool = False,
    ) -> None:
        self._builder = builder
        self._renderer = renderer
        self._coordinator = coordinator
        self._ledger = ledger
        self._snapshot_store = snapshot_store
        self._manifest_store = manifest_store
        self._trigger_cfg = trigger_config or IntradayTriggerConfig()
        self._add_cfg = add_config or AddConfig()
        # Per-stock adaptive DRAWDOWN_STOP threshold (D1-a). ``None`` keeps the
        # static fixed threshold (default-OFF; env-gated in main.py). Folded into
        # the config hash below so the manifest pins the calibration for replay.
        self._drawdown_calib = drawdown_calibration
        # D1-b regime conditioning: when on, a BEAR regime (derived from the
        # benchmark index) tightens the adaptive drawdown stop. Refines the
        # adaptive threshold, so it only has effect when ``_drawdown_calib`` is
        # also set. Folded into the config hash below (PIT).
        self._regime_dd_enabled = regime_drawdown_enabled
        # D1-c regime-conditioned take-profit multiple. ``None`` keeps the
        # static r_multiple (default-OFF; env-gated in main.py). The config IS
        # the switch (the tiers are its whole content). Independent of the
        # D1-b flag above — each feature conditions only its own maths.
        # Folded into the config hash below (PIT).
        self._takeprofit_calib = takeprofit_calibration
        # D1-d tiered take-profit ladder (+1R half → +2R another tranche →
        # residual rides the trailing stop). ``None`` keeps the single-target
        # v7 semantics (default-OFF; env-gated in main.py). The ladder needs
        # its per-episode tiers-taken ledger; both come together — a ladder
        # without a ledger fails closed at tick time (take-profit suppressed,
        # never double-taken). Folded into the config hash below (PIT).
        self._tiered_takeprofit = tiered_takeprofit
        self._takeprofit_ledger = takeprofit_ledger
        # Ops hardening (P0-10-amendment-line2-2026-06-04-intraday-ops-
        # hardening): durable per-day dedup (survives restarts — §1.1) + the
        # REJECTED-SELL alert hook (§1.2). Both optional and fail-open: a
        # missing/broken store degrades to the in-memory dedup (worst case a
        # duplicate Feishu message), and a failing hook never breaks a tick.
        self._fired_store = fired_store
        self._reject_alert_hook = reject_alert_hook
        # Per-day undelivered-send attempt counter (reset on day rollover):
        # bounds the send_failed retry loop (§1.1, review angle B).
        self._send_failures: dict[tuple[str, str], int] = {}
        self._tick_timeout = tick_timeout_seconds
        # PILOT go-live tier → prepend the "模拟盘·人工·试点" banner to every
        # order-bearing Feishu message (P0-6-amendment-2026-05-25 §2.3).
        self._pilot = pilot
        # Invariant 1: a single cooperative in-flight flag. The scheduler runs
        # ticks on one event loop, so a boolean check-then-set is race-free for
        # this cooperative-scheduling use (no thread preemption between the
        # guard and the set).
        self._in_flight = False
        # Per-day (code, side) dedup so a still-breached trigger does not
        # re-route every 30s; only today's keys are retained.
        # Per-day dedup keyed by (code, trigger_kind) — distinct kinds dedup
        # independently (P-005): a drawdown-stop firing does not suppress a
        # later take-profit / weight-trim on the same code, and ADD ("add") is
        # its own key. The "kind" is _kind_of(intent, side).
        self._fired: dict[date, set[tuple[str, str]]] = {}
        self._config_hash = self._compute_config_hash()

    def _compute_config_hash(self) -> str:
        payload = {
            "feature_code_version": FEATURE_CODE_VERSION,
            "trigger": dataclasses.asdict(self._trigger_cfg),
            "add": dataclasses.asdict(self._add_cfg),
            # Pin the per-stock drawdown calibration (incl. its absence) so a
            # replay reproduces the exact thresholds; a recalibration changes the
            # hash and fails a stale manifest closed (PIT, R0 §3).
            "drawdown_calibration": (
                {
                    # Pin the derivation maths VERSION too, not just the param
                    # values: a maths change that bumps only the calibration
                    # module version must shift this hash so stale manifests fail
                    # closed (codex P2 — PIT, R0 §3).
                    "version": CALIBRATION_FEATURE_VERSION,
                    "config": dataclasses.asdict(self._drawdown_calib),
                }
                if self._drawdown_calib is not None
                else None
            ),
            # Whether a BEAR regime tightens the adaptive drawdown stop (D1-b):
            # the bear_multiplier lives in the calibration config above, but the
            # decision to APPLY it is a separate flag → pin it so a replay with
            # the feature off does not reproduce a tightened threshold.
            "regime_drawdown_enabled": self._regime_dd_enabled,
            # D1-c regime-conditioned take-profit tiers (incl. their absence) —
            # same {version, config} pinning as the drawdown calibration: a
            # tier recalibration or a derivation-maths bump shifts the hash so
            # a stale manifest fails closed (PIT, R0 §3).
            "takeprofit_calibration": (
                {
                    "version": CALIBRATION_FEATURE_VERSION,
                    "config": dataclasses.asdict(self._takeprofit_calib),
                }
                if self._takeprofit_calib is not None
                else None
            ),
            # D1-d tiered take-profit ladder (incl. its absence) — pinned so a
            # replay with the ladder off never reproduces a tier-gated target
            # (PIT, P0-10-amendment-line2-2026-06-04).
            "tiered_takeprofit": (
                {
                    "version": CALIBRATION_FEATURE_VERSION,
                    "config": dataclasses.asdict(self._tiered_takeprofit),
                }
                if self._tiered_takeprofit is not None
                else None
            ),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    async def run(
        self,
        *,
        provider: Line2IntradayContextProvider,
        now: datetime,
        signal_id: str | None = None,
    ) -> Line2IntradayRunResult:
        """Run one 30s intraday tick; route every fresh trigger it finds."""
        # Invariant 1: skip if a previous tick is still in flight.
        if self._in_flight:
            log.info("intraday_tick_overlap_skipped")
            return Line2IntradayRunResult(
                signal_id="", tick_outcome=IntradayTickOutcome.SKIPPED_OVERLAP
            )
        self._in_flight = True
        try:
            return await self._run_locked(
                provider=provider, now=now, signal_id=signal_id
            )
        finally:
            self._in_flight = False

    async def _run_locked(
        self,
        *,
        provider: Line2IntradayContextProvider,
        now: datetime,
        signal_id: str | None,
    ) -> Line2IntradayRunResult:
        # Only None derives the per-tick default; an explicit "" (or any
        # non-prefixed caller id) must fail the prefix check, not be silently
        # replaced by a ``signal_id or default`` falsy coercion (codex U-C2 P2).
        sid = (
            f"{MONITORING_SIGNAL_PREFIX}{now:%Y%m%d}-intraday-{now:%H%M%S}"
            if signal_id is None
            else signal_id
        )
        if not sid.startswith(MONITORING_SIGNAL_PREFIX):
            raise ValueError(
                f"Line-2 signal_id {sid!r} must start with {MONITORING_SIGNAL_PREFIX!r}"
            )

        # Invariants 4 + 5: static-calendar + trading-hours gating (no fetch).
        if not is_trading_day(now.date()):
            return Line2IntradayRunResult(
                signal_id=sid, tick_outcome=IntradayTickOutcome.SKIPPED_NON_TRADING_DAY
            )
        if not is_trading_hours(now):
            return Line2IntradayRunResult(
                signal_id=sid, tick_outcome=IntradayTickOutcome.SKIPPED_OFF_HOURS
            )

        held = tuple(provider.held_positions)
        if not held:
            return Line2IntradayRunResult(
                signal_id=sid, tick_outcome=IntradayTickOutcome.EMPTY_PORTFOLIO
            )
        held_codes = [p.code for p in held]

        # Invariant 2: the live-quote fetch is bounded; a timeout fails closed.
        try:
            spots = dict(
                await asyncio.wait_for(
                    provider.fetch_spots(held_codes), timeout=self._tick_timeout
                )
            )
        except TimeoutError:
            log.warning("intraday_tick_fetch_timeout", signal_id=sid)
            return Line2IntradayRunResult(
                signal_id=sid, tick_outcome=IntradayTickOutcome.SKIPPED_TIMEOUT
            )

        # Read each provider input ONCE per tick into a local, so the trigger
        # gate and the manifest record provably consume the SAME values even if
        # the provider's properties are not immutable across reads (codex U-C3
        # P2). The persisted daily_frame is the exact one the closes parse from.
        daily_frame = provider.daily_frame
        account = provider.account
        index_closes = provider.index_closes
        name_by_code = dict(provider.name_by_code)

        # Invariant 6: suspended holdings degrade cleanly (no SELL/ADD).
        partition = partition_by_suspension(held_codes, spots)
        # Invariant 3: stale / missing-price codes fail closed (no trigger).
        fresh, stale = filter_fresh_quotes(
            spots,
            partition.active_codes,
            now=now,
            max_staleness_seconds=self._trigger_cfg.max_quote_staleness_seconds,
        )

        # Per-day (code, kind) dedup — resolved BEFORE intent evaluation so
        # the same-day SELL→ADD mutex below can read it. On the first tick of
        # a day the persisted keys are loaded once (ops hardening §1.1) so a
        # restart cannot re-route an already-fired trigger; the set lives
        # in-memory afterwards (the store is append-through on every fire).
        today = now.date()
        fired_today = self._fired.get(today)
        if fired_today is None:
            fired_today = set(
                self._fired_store.load_fired(today.isoformat())
                if self._fired_store is not None
                else ()
            )
            self._fired[today] = fired_today
            # Day rollover housekeeping: reset the undelivered-send counters
            # and prune store rows older than the retention window (the
            # append-only file must not grow + be re-scanned unboundedly —
            # review angle A). Both fail-open.
            self._send_failures = {}
            if self._fired_store is not None:
                self._fired_store.prune_before(
                    (today - timedelta(days=7)).isoformat()
                )
        self._prune_fired(today)

        closes_by_code: dict[str, tuple[float, ...]] = {}
        sell_intents: tuple[IntradaySellIntent, ...] = ()
        add_intents: tuple[AddIntent, ...] = ()
        if fresh:
            fresh_spots = {c: spots[c] for c in fresh}
            series = parse_held_series(daily_frame, sorted(fresh))
            closes_by_code = {c: closes for c, (closes, _amounts) in series.items()}
            # W-004: deterministic THESIS_QUANT_BREAK over the fresh prices. The
            # theses + holding days come from the provider (absent → empty map →
            # no thesis exits, behaviour unchanged). Zero LLM: the break is a
            # pure quant evaluation of the buy-time whitelist thresholds.
            thesis_break_by_code = self._thesis_breaks(provider, fresh_spots)
            long_term_hold_codes = self._intact_thesis_codes(provider, fresh_spots)
            # D1-b: classify the market regime (deterministic, from the benchmark
            # index) only when the feature is on — a BEAR regime tightens the
            # adaptive drawdown stop. None when off → no conditioning.
            sell_regime = (
                classify_regime(index_closes) if self._regime_dd_enabled else None
            )
            # D1-c: the take-profit tiers ride their OWN regime channel so each
            # env-gated feature conditions only its own maths (enabling one
            # never activates the other). Same deterministic benchmark-index
            # derivation; None when off.
            tp_regime = (
                classify_regime(index_closes)
                if self._takeprofit_calib is not None
                else None
            )
            # D1-d: fold the per-episode tiers-taken state (closing episodes
            # for codes that fully exited). Any ledger problem fails CLOSED
            # for the take-profit ONLY — suppress TP this tick via the
            # already-taken gate (missing a profit-take is safe; double-
            # taking a tranche is not). Protective stops are untouched.
            tp_tiers_taken: dict[str, int] = {}
            tp_suppress: frozenset[str] = frozenset()
            if self._tiered_takeprofit is not None:
                bare_held = frozenset(
                    p.code.split(".")[0].strip() for p in held if p.volume > 0
                )
                trade_date_iso = now.date().isoformat()
                if self._takeprofit_ledger is None:
                    log.error("tiered_takeprofit_without_ledger_suppressed")
                    tp_suppress = bare_held
                else:
                    try:
                        self._takeprofit_ledger.sync_episodes(
                            bare_held, trade_date=trade_date_iso
                        )
                        tp_tiers_taken = self._takeprofit_ledger.tiers_taken()
                    except (TakeProfitLedgerError, OSError) as exc:
                        log.error(
                            "takeprofit_ledger_read_failed_tp_suppressed",
                            error=str(exc),
                        )
                        tp_tiers_taken = {}
                        tp_suppress = bare_held
            sell_intents = evaluate_intraday_sell_intents(
                fresh_spots,
                closes_by_code,
                held,
                name_by_code=name_by_code,
                config=self._trigger_cfg,
                # P-005: account enables the TAKE_PROFIT + WEIGHT_TRIM triggers;
                # the single-stock cap is the runner's one max_single_stock_pct
                # (shared with the ADD headroom — no new constant). The
                # ledger-derived take_profit_already_taken gate is wired in P-006
                # (defaults empty here → a fresh episode can take profit once).
                account=account,
                max_single_stock_pct=self._add_cfg.max_single_stock_pct,
                thesis_break_by_code=thesis_break_by_code,
                long_term_hold_codes=long_term_hold_codes,
                drawdown_calibration=self._drawdown_calib,
                regime=sell_regime,
                takeprofit_calibration=self._takeprofit_calib,
                takeprofit_regime=tp_regime,
                tiered_takeprofit=self._tiered_takeprofit,
                take_profit_tiers_taken=tp_tiers_taken,
                take_profit_already_taken=tp_suppress,
            )
            add_eval = evaluate_intraday_add_intents(
                fresh_spots,
                closes_by_code,
                held,
                account,
                index_closes=index_closes,
                name_by_code=name_by_code,
                config=self._add_cfg,
            )
            # A risk-exit SELL suppresses an ADD on the SAME code this tick: a
            # holding cannot be both scaled into and exited at once (a sharp
            # drawdown can satisfy both the SELL trigger and the dip-vs-cost ADD
            # gate). The exit wins (codex U-C3 P1).
            sell_codes = {i.code for i in sell_intents}
            # Same-day SELL→ADD one-way mutex (ops hardening §1.3): a code
            # whose SELL trigger fired earlier TODAY (any sell kind, ROUTED or
            # REJECTED — either way the quant said "exit") gets no
            # contradicting ADD for the rest of the day (2026-06-04: SELL
            # 605020 at 14:27, ADD 605020 at 14:50). One-way only: an earlier
            # ADD never suppresses a protective SELL.
            sell_fired_codes = {c for (c, k) in fired_today if k != "add"}
            suppressed = sorted(
                {i.code for i in add_eval.intents} & sell_fired_codes
            )
            if suppressed:
                log.info(
                    "intraday_add_suppressed_same_day_sell", codes=suppressed
                )
            add_intents = tuple(
                i
                for i in add_eval.intents
                if i.code not in sell_codes and i.code not in sell_fired_codes
            )

        candidates: list[tuple[Any, InstructionSide]] = [
            (i, InstructionSide.SELL) for i in sell_intents
        ] + [(i, InstructionSide.BUY) for i in add_intents]

        routes: list[TriggerRoute] = []
        to_route: list[tuple[Any, InstructionSide]] = []
        for intent, side in candidates:
            if (intent.code, self._kind_of(intent, side)) in fired_today:
                routes.append(
                    TriggerRoute(
                        code=intent.code,
                        side=side,
                        kind=self._kind_of(intent, side),
                        outcome=TriggerRouteOutcome.DEDUP_SKIPPED,
                    )
                )
            else:
                to_route.append((intent, side))

        quote_snapshot_id: str | None = None
        if to_route:
            # Invariant 7: persist the consumed quotes + lineage BEFORE routing,
            # for every tick that produces ≥1 NEW (non-deduped) signal. A
            # dedup-skipped repeat is NOT re-persisted — it routes no new signal
            # and its originating signal's lineage is already durable from the
            # first fire (re-persisting the same quote every 30s would just
            # bloat storage with redundant snapshots).
            fired_codes = sorted({intent.code for intent, _ in to_route})
            triggers = self._build_trigger_records(
                to_route,
                spots=spots,
                account=account,
                held=held,
                closes_by_code=closes_by_code,
                index_closes=index_closes,
            )
            quote_snapshot_id = self._persist_tick(
                sid=sid,
                now=now,
                spots=spots,
                fired_codes=fired_codes,
                triggers=triggers,
                daily_frame=daily_frame,
            )
            for seq, (intent, side) in enumerate(to_route, start=1):
                route = await self._route_one(
                    intent, side, provider, sid, seq, now, spots
                )
                routes.append(route)
                # Dedup only what the owner can actually act on: a REJECTED
                # plan (re-offering the same rejected order every 30s is
                # spam) or a route that REACHED its sink (dispatched /
                # simulation_routed / dry_run_rendered / skipped_duplicate —
                # the last means the outbox already SENT this instruction, so
                # the owner has the card). A definitive Feishu ``send_failed``
                # reaches nobody and the dispatcher releases the outbox
                # precisely so it can retry — marking it fired (in-memory or
                # durable) would hide the protective SELL for the rest of the
                # day (codex P2, ops hardening §1.1). ``skipped_in_flight``
                # is unreachable here (per-tick ids never share an outbox
                # claim) and stays retryable by the same conservative logic.
                fired_kind = self._kind_of(intent, side)
                delivered = (
                    route.outcome is TriggerRouteOutcome.ROUTED
                    and route.route_outcome is not None
                    and route.route_outcome.action
                    in (
                        "dispatched",
                        "simulation_routed",
                        "dry_run_rendered",
                        "skipped_duplicate",
                    )
                )
                # Bounded retry for undelivered sends: a sustained Feishu
                # outage must not re-build + re-persist + re-send every 30s
                # for the rest of the session (review angle B). After the cap
                # the key enters the dedup with a loud error — the owner is
                # unreachable on this channel anyway; the audit trail +
                # alert channel carry the evidence.
                send_capped = False
                if (
                    route.outcome is TriggerRouteOutcome.ROUTED
                    and not delivered
                ):
                    key = (intent.code, fired_kind)
                    attempts = self._send_failures.get(key, 0) + 1
                    self._send_failures[key] = attempts
                    if attempts >= _MAX_UNDELIVERED_ATTEMPTS_PER_DAY:
                        send_capped = True
                        log.error(
                            "intraday_send_retries_exhausted",
                            code=intent.code,
                            kind=fired_kind,
                            attempts=attempts,
                        )
                if (
                    route.outcome is TriggerRouteOutcome.REJECTED
                    or delivered
                    or send_capped
                ):
                    fired_today.add((intent.code, fired_kind))
                    # Durable = DELIVERED only. A REJECTED or retry-capped key
                    # dedups in-memory (no 30s spam this process) but is NOT
                    # persisted: a deliberate operator restart — the recovery
                    # playbook after fixing the rejecting data gap or a Feishu
                    # outage — re-attempts the protective exit instead of
                    # finding it durably suppressed (review angle A: a
                    # rejection whose cause clears intra-day must stay
                    # recoverable). Fail-open inside the store.
                    if delivered and self._fired_store is not None:
                        self._fired_store.record_fired(
                            today.isoformat(),
                            intent.code,
                            fired_kind,
                            signal_id=sid,
                        )
                # D1-d: a DELIVERED tiered take-profit advances the episode's
                # ladder (REJECTED does not — the same tier retries next
                # day). ROUTED alone is not delivery: a Feishu send_failed or
                # a DRY_RUN render reaches no owner/order, and advancing the
                # ladder then would silently skip a tier (codex P2). An
                # owner-unexecuted DELIVERED dispatch still advances the
                # ladder (under-sell direction, visible in this ledger +
                # audit — P0-10-amendment-line2-2026-06-04 §1.2 caveat).
                delivered = (
                    route.outcome is TriggerRouteOutcome.ROUTED
                    and route.route_outcome is not None
                    and route.route_outcome.action
                    in ("simulation_routed", "dispatched")
                )
                if (
                    delivered
                    and getattr(intent, "take_profit_tier", None) is not None
                    and self._takeprofit_ledger is not None
                ):
                    try:
                        self._takeprofit_ledger.record_tier(
                            intent.code,
                            tier=intent.take_profit_tier,
                            trade_date=now.date().isoformat(),
                            signal_id=sid,
                        )
                    except (TakeProfitLedgerError, OSError) as exc:
                        # Loud but non-fatal: the tick already routed. A
                        # missed record can re-offer the same tier next day
                        # (human gate reviews every order).
                        log.error(
                            "takeprofit_tier_record_failed",
                            code=intent.code,
                            error=str(exc),
                        )

        log.info(
            "intraday_tick_complete",
            signal_id=sid,
            held=len(held),
            active=len(partition.active_codes),
            degraded=len(partition.degrades),
            stale=len(stale),
            routed=sum(1 for r in routes if r.outcome is TriggerRouteOutcome.ROUTED),
        )
        return Line2IntradayRunResult(
            signal_id=sid,
            tick_outcome=IntradayTickOutcome.SCANNED,
            held_count=len(held),
            active_count=len(partition.active_codes),
            degraded_codes=tuple(d.code for d in partition.degrades),
            stale_codes=stale,
            quote_snapshot_id=quote_snapshot_id,
            routes=tuple(routes),
        )

    def _persist_tick(
        self,
        *,
        sid: str,
        now: datetime,
        spots: Mapping[str, Any],
        fired_codes: Sequence[str],
        triggers: tuple[IntradayTriggerRecord, ...],
        daily_frame: MarketDataSnapshot,
    ) -> str:
        """Persist the fired quotes as a snapshot + write the intraday manifest.

        Runs BEFORE any routing (invariant 7) so a crash mid-route cannot lose
        the replay lineage. Returns the persisted quote snapshot_id (str).
        """
        raw, rows_by_code = serialize_intraday_quotes(spots, fired_codes)
        # Each 30s tick is a DISTINCT point-in-time fetch, not a restatement of
        # an earlier one, so the tick time goes into the endpoint: SnapshotStore
        # rejects a duplicate (vendor, endpoint, trade_date, version) and the
        # intraday version stays 1, so two triggered ticks on the same day would
        # otherwise collide on (quantmind, line2_intraday_quotes, YYYYMMDD, v1)
        # and raise before routing (codex U-C3 P1). The in-flight guard + 30s
        # cadence make the HH:MM:SS tick label unique within a trade date.
        endpoint = f"line2_intraday_quotes-{now:%H%M%S}"
        snap = MarketDataSnapshot.create(
            vendor="quantmind",
            endpoint=endpoint,
            params={"signal_id": sid, "tick_at": now.isoformat()},
            trade_date=f"{now:%Y%m%d}",
            raw_payload=raw,
            encoding="csv",
            compression="none",
            fetch_time_utc=now.astimezone(UTC),
        )
        stored = self._snapshot_store.put(snap)
        consumed = tuple(
            build_consumed_row(stored.snapshot_id, code, rows_by_code[code])
            for code in fired_codes
        )
        manifest = IntradayTriggerManifest(
            signal_id=sid,
            created_at=datetime.now(tz=UTC),
            tick_at=now,
            quote_snapshot_id=stored.snapshot_id,
            daily_frame_snapshot_ids=(daily_frame.snapshot_id,),
            consumed_quotes=consumed,
            triggers=triggers,
            feature_code_version=FEATURE_CODE_VERSION,
            config_hash=self._config_hash,
        )
        self._manifest_store.put(manifest)
        return str(stored.snapshot_id)

    def _build_trigger_records(
        self,
        to_route: Sequence[tuple[Any, InstructionSide]],
        *,
        spots: Mapping[str, Any],
        account: Any,
        held: Sequence[Any],
        closes_by_code: Mapping[str, tuple[float, ...]],
        index_closes: tuple[float, ...],
    ) -> tuple[IntradayTriggerRecord, ...]:
        """Build the manifest trigger records (rule inputs + outputs) for the tick.

        The ADD records carry the dip-vs-cost + sizing inputs the BUY gate used
        beyond the quote (cost / position / equity / regime / long-MA) so the
        verdict is auditable/recomputable (codex U-C3 P2). ``regime`` /
        ``total_assets`` are tick-wide, computed once.
        """
        pos_by_code = {p.code.split(".")[0].strip(): p for p in held}
        has_add = any(side is InstructionSide.BUY for _, side in to_route)
        regime = classify_regime(index_closes).value if has_add else None
        total_assets = float(account.total_assets) if has_add else None
        records: list[IntradayTriggerRecord] = []
        for intent, side in to_route:
            spot = spots[intent.code]
            if side is InstructionSide.BUY:
                ma_long = moving_average(
                    closes_by_code.get(intent.code, ()), self._add_cfg.ma_long_window
                )
                records.append(
                    self._add_record(
                        intent,
                        spot,
                        position=pos_by_code.get(intent.code),
                        total_assets=total_assets,
                        regime=regime,
                        ma_long=ma_long,
                    )
                )
            else:
                records.append(self._sell_record(intent, spot))
        return tuple(records)

    def _sell_record(self, intent: Any, spot: Any) -> IntradayTriggerRecord:
        return IntradayTriggerRecord(
            code=intent.code,
            side="sell",
            kind=intent.trigger_kind.value,
            live_price=intent.limit_price,
            prev_close=float(spot.prev_close) if spot.prev_close else None,
            drawdown_pct=intent.drawdown_pct,
            atr=intent.atr or None,
            recent_high=intent.recent_high or None,
            stop_level=intent.stop_level or None,
            available_volume=intent.available_volume,
            threshold_params={
                # Record the threshold that ACTUALLY fired: the per-stock adaptive
                # value when calibration tightened/widened it, else the static
                # config — so audit / offline replay reproduce the decision
                # (P0-7-amendment-2026-06-03, codex P2).
                "drawdown_threshold": (
                    intent.effective_drawdown_threshold
                    if getattr(intent, "effective_drawdown_threshold", None) is not None
                    else self._trigger_cfg.drawdown_threshold
                ),
                "atr_stop_mult": self._trigger_cfg.atr_stop_mult,
                "recent_high_window": float(self._trigger_cfg.recent_high_window),
                # The take-profit multiple in force when this intent fired (the
                # regime-conditioned tier when D1-c is on, else the static
                # config) — recorded on every SELL so replay reproduces why
                # TAKE_PROFIT did or did not fire first
                # (P0-7-amendment-2026-06-04).
                "r_multiple": (
                    intent.effective_r_multiple
                    if getattr(intent, "effective_r_multiple", None) is not None
                    else self._trigger_cfg.r_multiple
                ),
                # D1-d: the 1-based ladder tier this take-profit fired (absent
                # on non-tiered / non-TP intents) — replay reproduces which
                # tier gated the target (P0-10-amendment-line2-2026-06-04).
                **(
                    {"take_profit_tier": float(intent.take_profit_tier)}
                    if getattr(intent, "take_profit_tier", None) is not None
                    else {}
                ),
                # D1-d: the episode's tiers-taken count in force — carried on
                # EVERY sell record when the ladder is on, so a lower-priority
                # trigger's manifest reproduces WHY take-profit was gated at a
                # higher tier (codex P2).
                **(
                    {
                        "take_profit_tiers_taken": float(
                            intent.take_profit_tiers_taken
                        )
                    }
                    if getattr(intent, "take_profit_tiers_taken", None)
                    is not None
                    else {}
                ),
            },
        )

    def _add_record(
        self,
        intent: Any,
        spot: Any,
        *,
        position: Any,
        total_assets: float | None,
        regime: str | None,
        ma_long: float | None,
    ) -> IntradayTriggerRecord:
        return IntradayTriggerRecord(
            code=intent.code,
            side="buy",
            kind="add",
            live_price=intent.limit_price,
            prev_close=float(spot.prev_close) if spot.prev_close else None,
            atr=intent.atr or None,
            stop_level=intent.stop_price or None,
            available_volume=intent.add_volume,
            cost_price=float(position.cost_price) if position else None,
            position_volume=int(position.volume) if position else None,
            total_assets=total_assets,
            regime=regime,
            ma_long=round(ma_long, 4) if ma_long is not None else None,
            threshold_params={
                "risk_fraction": self._add_cfg.risk_fraction,
                "atr_stop_mult": self._add_cfg.atr_stop_mult,
                "max_add_drawdown_pct": self._add_cfg.max_add_drawdown_pct,
                "max_single_stock_pct": self._add_cfg.max_single_stock_pct,
                "breakdown_tolerance": self._add_cfg.breakdown_tolerance,
                "ma_long_window": float(self._add_cfg.ma_long_window),
            },
        )

    async def _route_one(
        self,
        intent: Any,
        side: InstructionSide,
        provider: Line2IntradayContextProvider,
        signal_id: str,
        seq: int,
        now: datetime,
        spots: Mapping[str, Any],
    ) -> TriggerRoute:
        """Assemble + route one trigger through the single construction point."""
        snapshot_at = spots[intent.code].snapshot_at
        if side is InstructionSide.SELL:
            context = provider.build_sell_context(
                intent, signal_id=signal_id, seq=seq, now=now, snapshot_at=snapshot_at
            )
        else:
            context = provider.build_add_context(
                intent, signal_id=signal_id, seq=seq, now=now, snapshot_at=snapshot_at
            )
        built = await self._builder.assemble_monitoring_plan(context)

        kind = self._kind_of(intent, side)
        if not isinstance(built, MonitoringPlan):
            # A freeze early-return (mode switch / ticket / data quality).
            log.info("intraday_early_return", signal_id=signal_id, code=intent.code)
            return TriggerRoute(
                code=intent.code,
                side=side,
                kind=kind,
                outcome=TriggerRouteOutcome.EARLY_RETURN,
            )

        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)
        if plan.status is not InstructionStatus.VALIDATED:
            # A REJECTED SELL is a swallowed exit — surface it to the alert
            # channel (ops hardening §1.2; the prev_close gap of 2026-06-03
            # silently killed every Line-2 exit for two trading days). BUY
            # rejections stay quiet: a rejected ADD is normal fallthrough.
            if (
                side is InstructionSide.SELL
                and self._reject_alert_hook is not None
            ):
                try:
                    await self._reject_alert_hook(
                        code=intent.code,
                        kind=kind,
                        instruction_id=plan.instruction_id,
                    )
                except Exception as exc:  # noqa: BLE001 — alert never breaks a tick
                    log.warning(
                        "line2_reject_alert_failed",
                        code=intent.code,
                        error=str(exc),
                    )
            return TriggerRoute(
                code=intent.code,
                side=side,
                kind=kind,
                outcome=TriggerRouteOutcome.REJECTED,
                plan=plan,
            )

        if side is InstructionSide.SELL:
            wire = self._renderer.render_monitoring_sell(
                plan, anomaly_reason=intent.anomaly_reason, pilot=self._pilot
            )
        else:
            wire = self._renderer.render_add_position(
                plan,
                add_rationale=intent.rationale,
                stop_price=intent.stop_price,
                pilot=self._pilot,
            )
        outcome = await self._coordinator.route(
            OutboundSignal(plan=plan, wire_text=wire), now=now
        )
        log.info(
            "intraday_routed",
            signal_id=signal_id,
            instruction_id=plan.instruction_id,
            side=side.value,
            action=outcome.action,
            mode=outcome.mode.value,
        )
        return TriggerRoute(
            code=intent.code,
            side=side,
            kind=kind,
            outcome=TriggerRouteOutcome.ROUTED,
            route_outcome=outcome,
            plan=plan,
        )

    @staticmethod
    def _thesis_breaks(
        provider: Line2IntradayContextProvider,
        fresh_spots: Mapping[str, Any],
    ) -> dict[str, str]:
        """Deterministic THESIS_QUANT_BREAK reasons keyed by code (W-004).

        Reads the provider's optional ``theses_by_code`` + ``holding_trade_days_
        by_code`` (a provider without them — older deploys / tests — yields no
        breaks, so the trigger is purely additive). Pure quant over the fresh
        prices; zero LLM.
        """
        theses_attr = getattr(provider, "theses_by_code", None)
        if theses_attr is None:
            return {}
        # FAIL-OPEN (codex W-004 P2): a provider / store error here must NEVER
        # break the tick — the existing drawdown / ATR / ADD monitoring keeps
        # running. The thesis-break feature is optional + add-only, so any
        # failure degrades to an empty break map (prior behaviour).
        try:
            theses = theses_attr() if callable(theses_attr) else theses_attr
            if not theses:
                return {}
            price_by_code = {c: s.price for c, s in fresh_spots.items()}
            days_attr = getattr(provider, "holding_trade_days_by_code", None)
            days = (days_attr() if callable(days_attr) else days_attr) or {}
            breaks = evaluate_thesis_breaks(
                theses,
                price_by_code=price_by_code,
                holding_trade_days_by_code=days,
            )
            return {code: b.reason for code, b in breaks.items()}
        except Exception as exc:  # noqa: BLE001 — thesis break never breaks a tick
            log.warning("thesis_break_eval_failed", error=str(exc))
            return {}

    @staticmethod
    def _intact_thesis_codes(
        provider: Line2IntradayContextProvider,
        fresh_spots: Mapping[str, Any],
    ) -> frozenset[str]:
        """Codes whose PositionThesis is present AND intact = long-term holds.

        P0-10-amendment-line2-2026-06-03 (take-profit exemption). Reads the
        provider's SEPARATE ``exempt_theses_by_code`` (own env gate; empty unless
        the exemption is enabled — keeps THESIS_QUANT_BREAK wiring untouched).
        A code is exempt only when it has a fresh spot this tick AND no
        invalidation template is broken over that PIT price. Pure quant, zero
        LLM. **Fail-open**: any read / eval error degrades to the empty set so
        take-profit fires normally (the conservative default = lock gains),
        never crashing the tick.
        """
        theses_attr = getattr(provider, "exempt_theses_by_code", None)
        if theses_attr is None:
            return frozenset()
        try:
            theses = theses_attr() if callable(theses_attr) else theses_attr
            if not theses:
                return frozenset()
            price_by_code = {c: s.price for c, s in fresh_spots.items()}
            days_attr = getattr(provider, "holding_trade_days_by_code", None)
            days = (days_attr() if callable(days_attr) else days_attr) or {}
            # CONFIRMED intact on every intraday-observable condition (price +
            # time), not merely "not in the break map" — a silently-skipped
            # condition must NOT grant the exemption (codex P2 cycle-3).
            return intraday_intact_codes(
                theses,
                price_by_code=price_by_code,
                holding_trade_days_by_code=days,
            )
        except Exception as exc:  # noqa: BLE001 — exemption never breaks a tick
            log.warning("thesis_exempt_eval_failed", error=str(exc))
            return frozenset()

    @staticmethod
    def _kind_of(intent: Any, side: InstructionSide) -> str:
        if side is InstructionSide.SELL:
            return intent.trigger_kind.value
        return "add"

    def _prune_fired(self, today: date) -> None:
        """Drop dedup keys from prior days (only today's set is retained)."""
        for day in [d for d in self._fired if d != today]:
            del self._fired[day]


__all__ = [
    "IntradayTickOutcome",
    "Line2IntradayContextProvider",
    "Line2IntradayRunResult",
    "Line2IntradayRunner",
    "RejectAlertHook",
    "TriggerRoute",
    "TriggerRouteOutcome",
]
