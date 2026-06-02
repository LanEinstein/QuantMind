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
from datetime import UTC, date, datetime
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
from backend.monitoring.intraday_triggers import (
    FEATURE_CODE_VERSION,
    IntradaySellIntent,
    IntradayTriggerConfig,
    evaluate_intraday_add_intents,
    evaluate_intraday_sell_intents,
    filter_fresh_quotes,
    serialize_intraday_quotes,
)
from backend.monitoring.thesis_break import evaluate_thesis_breaks
from backend.orchestration.instruction_dispatcher import OutboundSignal
from backend.orchestration.intraday_manifest import (
    IntradayTriggerManifest,
    IntradayTriggerManifestStore,
    IntradayTriggerRecord,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.services.instruction_plan_builder import (
    MONITORING_SIGNAL_PREFIX,
    InstructionPlanBuilder,
    MonitoringAssemblyContext,
    MonitoringPlan,
)
from backend.services.ledger import DecisionLedgerService
from backend.utils.trading_hours import is_trading_day, is_trading_hours

log = structlog.get_logger(component="orchestration.line2_intraday_runner")


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
            add_intents = tuple(
                i for i in add_eval.intents if i.code not in sell_codes
            )

        candidates: list[tuple[Any, InstructionSide]] = [
            (i, InstructionSide.SELL) for i in sell_intents
        ] + [(i, InstructionSide.BUY) for i in add_intents]

        today = now.date()
        fired_today = self._fired.setdefault(today, set())
        self._prune_fired(today)

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
                to_route, spots=spots, account=account,
                held=held, closes_by_code=closes_by_code,
                index_closes=index_closes,
            )
            quote_snapshot_id = self._persist_tick(
                sid=sid, now=now, spots=spots, fired_codes=fired_codes,
                triggers=triggers, daily_frame=daily_frame,
            )
            for seq, (intent, side) in enumerate(to_route, start=1):
                route = await self._route_one(
                    intent, side, provider, sid, seq, now, spots
                )
                routes.append(route)
                if route.outcome in (
                    TriggerRouteOutcome.ROUTED,
                    TriggerRouteOutcome.REJECTED,
                ):
                    fired_today.add((intent.code, self._kind_of(intent, side)))

        log.info(
            "intraday_tick_complete",
            signal_id=sid,
            held=len(held),
            active=len(partition.active_codes),
            degraded=len(partition.degrades),
            stale=len(stale),
            routed=sum(
                1 for r in routes if r.outcome is TriggerRouteOutcome.ROUTED
            ),
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
                        intent, spot,
                        position=pos_by_code.get(intent.code),
                        total_assets=total_assets, regime=regime, ma_long=ma_long,
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
                "drawdown_threshold": self._trigger_cfg.drawdown_threshold,
                "atr_stop_mult": self._trigger_cfg.atr_stop_mult,
                "recent_high_window": float(self._trigger_cfg.recent_high_window),
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
                code=intent.code, side=side, kind=kind,
                outcome=TriggerRouteOutcome.EARLY_RETURN,
            )

        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)
        if plan.status is not InstructionStatus.VALIDATED:
            return TriggerRoute(
                code=intent.code, side=side, kind=kind,
                outcome=TriggerRouteOutcome.REJECTED, plan=plan,
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
            code=intent.code, side=side, kind=kind,
            outcome=TriggerRouteOutcome.ROUTED, route_outcome=outcome, plan=plan,
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
    "TriggerRoute",
    "TriggerRouteOutcome",
]
