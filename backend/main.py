"""QuantMind FastAPI application entry point."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI, Request

if TYPE_CHECKING:
    from backend.data.watchlist import WatchlistService
    from backend.services.acceptance_report import GoLiveTier
    from backend.services.pilot_readiness import PilotReadinessProbe
    from backend.services.universe_policy import UniversePolicy

from backend.api.acceptance import router as acceptance_router
from backend.api.analysis import router as analysis_router
from backend.api.audit import router as audit_router
from backend.api.cost import router as cost_router
from backend.api.data_quality import router as data_quality_router
from backend.api.equity_points import router as equity_points_router
from backend.api.evolution import router as evolution_router
from backend.api.execution_reports import router as execution_reports_router
from backend.api.feishu import router as feishu_router
from backend.api.health import router as health_router
from backend.api.instruction_plans import router as instruction_plans_router
from backend.api.market import router as market_router
from backend.api.monitoring import router as monitoring_router
from backend.api.performance import router as performance_router
from backend.api.reconciliation import router as reconciliation_router
from backend.api.risk import router as risk_router
from backend.api.settings import router as settings_router
from backend.api.simulation import router as simulation_router
from backend.api.system_status import router as system_status_router
from backend.api.trading import router as trading_router
from backend.api.watchlist import router as watchlist_router
from backend.api.websocket import router as websocket_router
from backend.llm.router import LLMRouter
from backend.services.config_service import ConfigService

log = structlog.get_logger()

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# P0-6-amendment-2026-05-25 §2.2 — which go-live tier's acceptance gate the
# startup + switch paths evaluate. This env var ONLY selects which tier's gate
# to evaluate; it NEVER bypasses the gate's ``allowed`` verdict (amendment §4
# #1 — the acceptance gate stays the single switch authority). Default FULL is
# the fail-safe: a missing / typo value never silently runs PILOT.
FEISHU_TIER_ENV = "QUANTMIND_FEISHU_TIER"


def _resolve_feishu_tier() -> GoLiveTier:
    """Resolve the configured go-live tier (default FULL, fail-safe).

    An unrecognised value aborts startup loudly rather than defaulting
    silently — a typo must never downgrade the gate.
    """
    from backend.services.acceptance_report import GoLiveTier

    raw = os.environ.get(FEISHU_TIER_ENV, "").strip().lower()
    if not raw:
        return GoLiveTier.FULL
    try:
        return GoLiveTier(raw)
    except ValueError:
        raise SystemExit(
            f"Refusing to start: {FEISHU_TIER_ENV}={raw!r} is not a valid "
            "go-live tier. Use 'pilot' or 'full' (default full). This env var "
            "only selects which tier's acceptance gate to evaluate — it can "
            "never bypass the gate's verdict (P0-6-amendment §4 #1)."
        ) from None


# cond10a — PILOT timeout ceiling (P0-6 §2 stability gate, mirrors the
# acceptance llm_timeout_rate ≤ 0.05 threshold but on the live daily counter
# rather than the 45-day report; P0-6-amendment-2026-05-29 §2).
_PILOT_LLM_TIMEOUT_CEILING = 0.05


def _build_pilot_probe(
    application: FastAPI, broker: object
) -> PilotReadinessProbe:
    """Wire the live PILOT readiness probe from the running app's surfaces.

    Each live check is fail-closed: a missing ``app.state`` dependency or a
    raising call counts as the condition UNMET (the probe's ``_safe_*``
    wrappers enforce this). cond 9 (data-quality) is wired fail-closed until
    U-D3 connects the per-code ``data_quality_provider`` against the real
    Tushare frame (the carry-forward note on U-D1/U-D1b) — a global all-clear
    aggregate does not exist yet, and fail-closed is the correct default until
    it does.
    """
    from backend.broker.mock_broker import MockBroker
    from backend.llm.fallback import read_llm_timeout_rate
    from backend.services.cost_guard import get_daily_budget_state
    from backend.services.pilot_data_probe import (
        MANDATORY_ETF_CANARIES,
        canary_quotes_reachable,
    )
    from backend.services.pilot_readiness import PilotReadinessProbe

    def _is_sim_broker() -> bool:
        return isinstance(broker, MockBroker)

    async def _reconciliation_clear() -> bool:
        repo = getattr(
            application.state, "reconciliation_ticket_repository", None
        )
        if repo is None:
            return False
        return not await repo.list_all_open()

    async def _data_quality_clear() -> bool:
        # cond9 — infra reachability of the three mandatory ETF canaries
        # (P0-6-amendment-2026-05-29 §1). Fail-closed when market_data is
        # unwired or any canary's both quote legs are down.
        market_data = getattr(application.state, "market_data", None)
        return await canary_quotes_reachable(
            market_data, MANDATORY_ETF_CANARIES
        )

    async def _llm_timeout_ok() -> bool:
        # cond10a — live daily timeout rate ≤ ceiling
        # (P0-6-amendment-2026-05-29 §2). Cold-start (0 calls) reads 0.0 ==
        # healthy. Fail-closed when Redis is unwired (same convention as the
        # cost_guard probe below); read_llm_timeout_rate still raises defensively
        # so the gate's _safe_await stays a backstop.
        redis = getattr(application.state, "redis", None)
        if redis is None:
            return False
        timeouts, calls = await read_llm_timeout_rate(redis)
        return (timeouts / max(calls, 1)) <= _PILOT_LLM_TIMEOUT_CEILING

    async def _cost_guard_hard_reserve_active() -> bool:
        redis = getattr(application.state, "redis", None)
        if redis is None:
            return False
        state = await get_daily_budget_state(redis)
        return state.status != "hard_breach"

    return PilotReadinessProbe(
        is_sim_broker=_is_sim_broker,
        reconciliation_clear=_reconciliation_clear,
        data_quality_clear=_data_quality_clear,
        llm_timeout_within_ceiling=_llm_timeout_ok,
        cost_guard_hard_reserve_active=_cost_guard_hard_reserve_active,
        env=os.environ,
    )


class _LazyAuditCollection:
    """Audit_events handle that resolves Mongo at call time.

    AuditStore is built before ``_init_data_layer`` runs (so it is
    available as soon as the lifespan ``yield`` happens), but the Mongo
    client is created inside ``_init_data_layer``. This wrapper resolves
    the collection lazily — when Mongo is unwired it raises so AuditStore
    falls back to its JSONL-only path (per the fail-open invariant).
    """

    def __init__(self, application: FastAPI) -> None:
        self._app = application

    async def insert_one(self, document: dict[str, object]) -> object:
        mongodb = getattr(self._app.state, "mongodb", None)
        if mongodb is None:
            raise RuntimeError("audit_events Mongo collection not yet available")
        db = getattr(mongodb, "_db", None)
        if db is None:
            raise RuntimeError("audit_events Mongo db not yet available")
        return await db["audit_events"].insert_one(document)


async def _init_data_layer(application: FastAPI, redis_pool: object) -> None:
    """Initialize the data layer: MongoDB, services, scheduler."""
    import motor.motor_asyncio as motor

    from backend.data.config import load_data_sources_config
    from backend.data.database import MongoDBService
    from backend.data.history_data import HistoryDataService
    from backend.data.market_data import MarketDataService
    from backend.data.news_crawler import NewsCrawlerService
    from backend.data.scheduler import DataScheduler

    try:
        config = load_data_sources_config("config/data_sources.yaml")
    except Exception as exc:
        log.warning("data_sources_config_load_failed", error=str(exc))
        return

    # MongoDB — uuidRepresentation="standard" is required for the
    # AcceptanceReport.report_id (uuid.UUID) round-trip; without it
    # PyMongo's default UuidRepresentation.UNSPECIFIED refuses to encode
    # native UUID values and the 16:00 acceptance upsert silently fails,
    # which means the P0-6 §2 红线 5 gate never gets persisted data
    # (Codex Cycle 1 P1 — found by running review against the I-001
    # orchestration wiring).
    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    mongo_client = motor.AsyncIOMotorClient(
        mongo_uri, uuidRepresentation="standard"
    )
    db = mongo_client["quantmind"]
    mongodb_service = MongoDBService(db)
    try:
        await mongodb_service.initialize()
    except Exception as exc:
        log.warning("mongodb_init_failed", error=str(exc))

    # Services
    market_data = MarketDataService(config)
    history_data = HistoryDataService(config)
    news_crawler = NewsCrawlerService(config)

    # Watchlist service — constructed before the scheduler so the 30s
    # watchlist snapshot job (C-003) can read the active universe from
    # the same instance the API + AnalysisScheduler use.
    from backend.data.watchlist import WatchlistService

    watchlist_service = WatchlistService(db)
    try:
        await watchlist_service.initialize()
    except Exception as exc:
        log.warning("watchlist_init_failed", error=str(exc))

    # C-006: MiroFish evidence writer wired up so the EOD review cron
    # and the intelligence_officer event-driven path both have a real
    # writer in the default startup (codex cycle 1 P1). Constructed on
    # the same MongoDBService that owns ``evidence_collection`` so the
    # unique evidence_id index and the cap-count query see one source
    # of truth.
    from backend.mirofish.output_writer import MiroFishEvidenceWriter

    mirofish_writer = MiroFishEvidenceWriter(mongodb_service)

    # Scheduler
    scheduler = DataScheduler(
        market_data=market_data,
        news_crawler=news_crawler,
        mongodb=mongodb_service,
        redis_client=redis_pool,  # type: ignore[arg-type]
        watchlist=watchlist_service,
        market_interval_seconds=config.market_data.refresh_interval_seconds,
        news_interval_seconds=config.news.refresh_interval_seconds,
        mirofish_writer=mirofish_writer,
    )
    await scheduler.start()

    # Store on app state
    application.state.mongo_client = mongo_client
    application.state.mongodb = mongodb_service
    application.state.market_data = market_data
    application.state.history_data = history_data
    application.state.news_crawler = news_crawler
    application.state.scheduler = scheduler
    application.state.watchlist = watchlist_service
    application.state.mirofish_writer = mirofish_writer

    log.info("data_layer_initialized")


async def _init_trading_layer(application: FastAPI) -> None:
    """Initialize the trading subsystem: broker registry + risk config.

    Note: ApprovalQueue was destructively removed in Phase A (P0-1).
    simulation_auto routes orders directly via the SimulationExecutor
    (Phase E), and the feishu_interactive overlay sends messages via
    FeishuMessenger (Phase F); neither requires an in-process approval
    holding queue.
    """
    from backend.broker.models import BrokerConfig, load_broker_config, load_risk_config
    from backend.broker.registry import BrokerRegistry

    try:
        broker_config = load_broker_config("config/broker.yaml")
    except Exception as exc:
        log.warning("broker_config_load_failed", error=str(exc))
        broker_config = BrokerConfig()

    registry = BrokerRegistry(broker_config)
    application.state.broker_registry = registry

    try:
        risk_config = load_risk_config("config/risk.yaml")
        application.state.risk_config = risk_config
    except Exception as exc:
        log.warning("risk_config_load_failed", error=str(exc))
        application.state.risk_config = None

    circuit_breaker = None
    try:
        risk_cfg = application.state.risk_config
        cb_cfg = getattr(risk_cfg, "circuit_breaker", None) if risk_cfg else None
        if cb_cfg is not None:
            from backend.risk.circuit_breaker import CircuitBreaker

            circuit_breaker = CircuitBreaker(cb_cfg)
    except Exception as exc:
        log.warning("circuit_breaker_init_failed", error=str(exc))
    application.state.circuit_breaker = circuit_breaker

    log.info("trading_layer_initialized")


async def _seed_watchlist_from_policy(
    watchlist_service: WatchlistService, policy: UniversePolicy
) -> None:
    """Reconcile Mongo watchlist with the policy (add missing + soft-delete stale).

    Reads the union of ``policy.{fast,slow}.default_codes`` and
    ``policy.overrides`` as the set of manually-pinned codes. Codes
    present in Mongo but absent from the policy are soft-deleted
    (``active=False``) so a policy rotation cannot leave stale rows that
    :func:`assign_category` would silently route to the default bucket.

    Since the 2026-05-24 amendment removed the fixed 13-code universe,
    ``all_watchlist_codes()`` is empty by default — the analysis universe
    is produced by ``backend/screening`` rather than seeded here. This
    reconciliation only matters when the owner pins codes into a bucket.
    Display names fall back to the code itself until a later phase wires
    in a stock_metadata registry.
    """
    canonical = policy.all_watchlist_codes()

    # Full-market mode (no manually-pinned codes): the analysis universe
    # is produced by ``backend/screening`` (Phase L-002/M), NOT by this
    # reconciliation. Skip entirely so an empty pin set neither seeds a
    # fixed list nor destructively soft-deletes pre-existing rows during
    # the migration window (codex L-001 P1: empty canonical must not wipe
    # the whole watchlist collection out from under the scheduler).
    if not canonical:
        log.info("watchlist_seed_skipped_full_market")
        return

    # Reactivate / upsert every code the policy pins.
    for code in sorted(canonical):
        await watchlist_service.add_stock(code, code)

    # Soft-delete any currently-active code that is no longer in the
    # policy — protects against post-rotation drift (codex C-002 P1).
    active_rows = await watchlist_service.list_stocks()
    active_codes = {
        row["stock_code"]
        for row in active_rows
        if isinstance(row.get("stock_code"), str)
    }
    stale = active_codes - canonical
    for code in sorted(stale):
        await watchlist_service.remove_stock(code)

    if canonical or stale:
        log.info(
            "watchlist_seeded",
            count=len(canonical),
            codes=sorted(canonical),
            deactivated=sorted(stale),
        )


async def _ensure_daily_frame(
    application: FastAPI, frame_lock: asyncio.Lock, now: datetime
) -> None:
    """Lazily assemble the production T-1 EOD frame and cache it on app.state.

    U-D6c — both lines read the SAME ``app.state.line2_daily_frame`` (a
    :class:`MarketDataSnapshot`). Before this, that attribute was never
    assigned in production, so the 09:35 line1 + line2 crons were silent
    no-ops (zero BUY). This assembles the real Tushare T-1 EOD frame on the
    first daily/intraday cron fire of the day and caches it for the rest of
    the day.

    Contracts:

    * **Idempotent per trade date** — re-entry with a cached frame whose
      ``trade_date`` matches the expected T-1 date returns immediately.
    * **Race-free** — ``frame_lock`` serialises the 09:35 line1 + line2 crons
      so only one assembles; the other awaits then hits the cache.
    * **T-1 EOD anchor** — ``fetch_time_utc`` is anchored to the T-1 15:00
      close (``t_minus_1_eod_utc``), hours before the run-day ~09:35
      ``created_at``, so the InstructionPlan ``snapshot_at < created_at``
      invariant holds deterministically (no wall-clock race) and a same-day
      re-assembly reusing the append-only store keeps the identical anchor.
    * **Fail-open** — any assembly error logs and leaves the frame unset, so
      callers skip cleanly (no crash, no trade routed) exactly as the
      pre-U-D6c seam did. Data corruption is never silently traded on.
    """
    from backend.data.trading_calendar import prev_trading_day
    from backend.marketdata_snapshot import SnapshotStore
    from backend.utils.trading_hours import t_minus_1_eod_utc

    as_of = prev_trading_day(now.astimezone(SHANGHAI_TZ).date())
    as_of_compact = as_of.strftime("%Y%m%d")
    cached = getattr(application.state, "line2_daily_frame", None)
    if cached is not None and cached.trade_date == as_of_compact:
        return
    async with frame_lock:
        cached = getattr(application.state, "line2_daily_frame", None)
        if cached is not None and cached.trade_date == as_of_compact:
            return
        try:
            from backend.data.tushare_client import TushareClient
            from backend.orchestration.line1_frame import Line1FrameAssembler

            frame_root = os.environ.get(
                "QUANTMIND_LINE1_FRAME_ROOT", "data/line1_frames"
            )
            assembler = Line1FrameAssembler(
                client=TushareClient(),
                store=SnapshotStore(root=frame_root),
                now_utc=lambda: t_minus_1_eod_utc(as_of),
            )
            result = await assembler.assemble(
                as_of_date=as_of,
                signal_id=f"LINE1-FRAME-{as_of_compact}",
            )
            application.state.line2_daily_frame = result.frame_snapshot
            log.info(
                "daily_frame_assembled",
                trade_date=as_of_compact,
                snapshot_id=str(result.frame_snapshot.snapshot_id),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open per the seam
            # Fail-open keeps the day available (no crash, no trade routed),
            # but a persistent failure — including the assembler's fail-closed
            # Line1FrameError on a corrupt/partial pull — would otherwise be a
            # SILENT multi-day zero-trade condition. Surface it to the owner via
            # the wired Feishu alerter (ALERT chat, dedup-15min so it never
            # storms) so a quiet pipeline is never mistaken for a quiet market.
            log.warning(
                "daily_frame_assembly_failed",
                as_of=as_of_compact,
                error=str(exc),
            )
            alerter = getattr(application.state, "feishu_alerter", None)
            if alerter is not None:
                try:
                    await alerter.fire(
                        alert_type="health_critical",
                        severity="critical",
                        message=(
                            f"T-1 EOD frame assembly failed for {as_of_compact}"
                            f": {exc}. Line-1/Line-2 will route NO signals until"
                            " resolved."
                        ),
                        dedup_key=f"frame_assembly_failed:{as_of_compact}",
                    )
                except Exception as alert_exc:  # noqa: BLE001 — alert best-effort
                    log.warning(
                        "daily_frame_assembly_alert_failed",
                        error=str(alert_exc),
                    )


async def _init_line2_runners(
    application: FastAPI,
    *,
    broker: object,
    audit_store: object,
    ledger: object,
    simulation_executor: object,
    market_meta: object,
    pilot: bool = False,
) -> tuple[
    Callable[[datetime], Awaitable[None]],
    Callable[[datetime], Awaitable[None]],
    Callable[[datetime], Awaitable[None]],
]:
    """U-D1 / U-D1b — construct the double-line production runners.

    Builds the import-isolated runners (Line-2 daily + 30s intraday SELL/ADD,
    U-D1; Line-1 4-agent debate BUY selection, U-D1b) + their single
    mutually-exclusive :class:`RouteCoordinator`, attaches them to ``app.state``
    (cold-start smoke slots), and returns the three scheduler cron callbacks
    (line2_daily, line2_intraday, line1). The heavy per-code/per-lead
    risk/broker context is supplied by the production providers in
    ``backend.services.line2_context_providers`` / ``line1_context_provider``.
    All three runners share one ``builder`` + one ``RouteCoordinator`` so the
    single construction point + the mode-switch / freeze gates stay identical
    across both lines.

    The callbacks read live state from ``app.state`` lazily at cron-fire time
    (so the analysis-layer ``watchlist_policy`` + the lifespan ``llm_router``,
    wired before/after this function, are present by then) and degrade to a
    clean skip until the T-1 EOD frame source is wired (real Tushare frame =
    U-D3) — so simulation_auto boots with the loop structurally complete, not
    hollow.
    """
    from backend.broker.models import CircuitBreakerConfig
    from backend.integrations.feishu.renderer import MessageRenderer
    from backend.marketdata_snapshot import SnapshotStore
    from backend.monitoring.anomaly import AnomalyDetector
    from backend.orchestration.instruction_dispatcher import (
        InMemoryOutboxRepository,
        InstructionDispatcher,
    )
    from backend.orchestration.intraday_manifest import IntradayTriggerManifestStore
    from backend.orchestration.line2_daily_runner import Line2DailyRunner
    from backend.orchestration.line2_intraday_runner import Line2IntradayRunner
    from backend.orchestration.route_coordinator import RouteCoordinator
    from backend.risk.circuit_breaker import CircuitBreaker
    from backend.risk.engine import RiskEngine
    from backend.services.instruction_plan_builder import InstructionPlanBuilder
    from backend.services.line2_context_providers import (
        Line2DailyProvider,
        Line2IntradayProvider,
        build_line2_code_contexts,
        build_line2_run_state,
    )
    from backend.services.run_mode import RouteMode, resolve_route_mode

    renderer = MessageRenderer()
    # Inject the live mode-switch probe so the builder's mode_switch early
    # return fires during a D-005 ModeRouter switch window. Without it the
    # feishu_interactive dispatch path (which does not re-check the switch
    # freeze the way SimulationExecutor does) could send a Line-2 instruction
    # mid-switch (Codex U-D1 P1). mode_router is already on app.state by now.
    mode_router = getattr(application.state, "mode_router", None)
    builder = InstructionPlanBuilder(
        audit_store=audit_store,
        mode_switch_probe=(
            mode_router.mode_state if mode_router is not None else None
        ),
    )

    route_mode = resolve_route_mode(getattr(application.state, "run_mode", None))
    decision_chat = os.environ.get("FEISHU_DECISION_CHAT_ID", "").strip()

    # Fail-closed by construction (Codex U-D1 review): when the route mode is
    # FEISHU_INTERACTIVE the RouteCoordinator WILL call the dispatcher, so a
    # placeholder decision chat must never be wired — otherwise a Line-2 cron
    # firing in the window before the lifespan feishu gate SystemExits could
    # send to a fake chat id. The lifespan gate also enforces this, but it runs
    # AFTER broker_scheduler.start(); enforce it here (before the scheduler
    # starts) so the invariant holds by construction, not by ordering.
    if route_mode is RouteMode.FEISHU_INTERACTIVE and not decision_chat:
        raise SystemExit(
            "Refusing to wire Line-2 runners: route mode is feishu_interactive "
            "but FEISHU_DECISION_CHAT_ID is unset. Set it to the decision "
            "group's open_chat_id (P0-2-amendment-2026-05-16 §4) and restart."
        )

    # Dispatcher (feishu_interactive path only). In the simulation_auto / dry_run
    # baseline the RouteCoordinator routes to the SimulationExecutor (or renders
    # only) and NEVER calls the dispatcher, so a placeholder decision chat keeps
    # it constructible without any risk of a real send (the guard above proves
    # decision_chat is non-empty whenever the mode actually uses the dispatcher).
    # NOTE (U-D2/U-D4 follow-on): the outbox is in-memory here — a durable Mongo
    # outbox is required before feishu go-live so the at-most-once claim survives
    # a restart.
    dispatcher = InstructionDispatcher(
        feishu_client=getattr(application.state, "feishu_client", None),
        decision_chat_id=decision_chat or "SIMULATION_AUTO_NO_DECISION_CHAT",
        outbox=InMemoryOutboxRepository(),
        ledger=ledger,
        audit_store=audit_store,
    )

    coordinator = RouteCoordinator(
        mode=route_mode,
        simulation_executor=simulation_executor,
        dispatcher=dispatcher,
    )

    snapshot_root = os.environ.get(
        "QUANTMIND_LINE2_SNAPSHOT_ROOT", "data/line2_intraday_snapshots"
    )
    manifest_root = os.environ.get(
        "QUANTMIND_INTRADAY_MANIFEST_ROOT", "data/intraday_manifests"
    )
    snapshot_store = SnapshotStore(root=snapshot_root)
    manifest_store = IntradayTriggerManifestStore(manifest_root)

    daily_runner = Line2DailyRunner(
        anomaly_detector=AnomalyDetector(),
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        pilot=pilot,
    )
    intraday_runner = Line2IntradayRunner(
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        snapshot_store=snapshot_store,
        manifest_store=manifest_store,
        pilot=pilot,
    )

    application.state.instruction_dispatcher = dispatcher
    application.state.route_coordinator = coordinator
    application.state.line2_daily_runner = daily_runner
    application.state.line2_intraday_runner = intraday_runner
    application.state.intraday_trigger_manifest_store = manifest_store

    # U-D1b — Line-1 full-market BUY-selection runner. Shares the builder +
    # RouteCoordinator above so the single construction point + mode-switch /
    # freeze gates are identical across both lines. The screen / budget /
    # selector configs are the runtime-immutable git-versioned files the
    # screening + selector modules already validate (load failures fail loud at
    # boot — they are as essential as risk.yaml).
    from backend.budget_policy.policy import (
        BudgetTierPolicy,
        load_budget_tier_config,
    )
    from backend.candidate_selector.selector import (
        CandidateSelector,
        load_selector_config,
    )
    from backend.orchestration.line1_runner import Line1Runner
    from backend.screening.screener import Screener
    from backend.services.line1_context_provider import (
        Line1ContextProvider,
        build_line1_run_state,
    )
    from backend.services.universe_policy import ExclusionRules, load_policy

    risk_yaml = os.environ.get("QUANTMIND_RISK_CONFIG_PATH", "config/risk.yaml")
    selector_yaml = os.environ.get(
        "QUANTMIND_SELECTOR_CONFIG_PATH", "config/candidate_weights/v1.yaml"
    )
    policy_path = os.environ.get(
        "QUANTMIND_UNIVERSE_POLICY_PATH", "config/universe_policy.yaml"
    )
    # The screener exclusion is the LAST-line defense (real exclusion is in the
    # screen per P0-9-amendment); ExclusionRules() defaults == the locked P0-9
    # values, so a missing policy file degrades to the locked rules rather than
    # aborting the daily BUY line.
    try:
        exclusion_rules = load_policy(policy_path).exclusion_rules
    except Exception as exc:  # noqa: BLE001 — defaults are the locked values
        log.warning("line1_exclusion_rules_fallback", error=str(exc))
        exclusion_rules = ExclusionRules()
    line1_runner = Line1Runner(
        screener=Screener(exclusion_rules),
        budget_policy=BudgetTierPolicy(load_budget_tier_config(risk_yaml)),
        selector=CandidateSelector(load_selector_config(selector_yaml)),
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        redis_client=getattr(application.state, "redis", None),
        pilot=pilot,
    )
    application.state.line1_runner = line1_runner

    def _risk_engine_or_none() -> RiskEngine | None:
        cfg = getattr(application.state, "risk_config", None)
        return RiskEngine(cfg) if cfg is not None else None

    def _circuit_breaker() -> CircuitBreaker:
        cb = getattr(application.state, "circuit_breaker", None)
        return cb if cb is not None else CircuitBreaker(CircuitBreakerConfig())

    def _names(positions: object) -> dict[str, str]:
        # Key by BARE code (Codex U-D1 review): the monitoring detectors +
        # build_line2_code_contexts look names up by the suffix-stripped code,
        # so a full-code key ("600000.SH") would never match and the map would
        # be dead. TODO(U-D3): real display names from a stock_metadata
        # registry; the bare code is a safe fallback (renderer + risk-meta
        # tolerate it).
        out: dict[str, str] = {}
        for p in positions:  # type: ignore[attr-defined]
            bare = p.code.split(".")[0].strip()
            out[bare] = bare
        return out

    async def _open_tickets_or_skip() -> tuple[object, ...] | None:
        """Load OPEN/EXPIRED reconciliation tickets so the builder's
        ``ticket_freeze`` early return can fire (P0-5). Returns ``None`` to
        tell the caller to SKIP the whole run.

        Fail-closed in BOTH directions because the simulation_auto
        SimulationExecutor does not itself check tickets, so a missed freeze
        here auto-fills during a reconciliation hold (Codex U-D1 P1 + verify):
        a missing repo (the orchestration layer wires it before the scheduler
        starts, but guard the startup window anyway) AND a lookup failure both
        skip the run rather than proceeding with an empty (no-freeze) view.
        """
        repo = getattr(application.state, "reconciliation_ticket_repository", None)
        if repo is None:
            log.warning("line2_skipped_ticket_repo_unavailable")
            return None
        try:
            return tuple(await repo.list_all_open())
        except Exception as exc:  # noqa: BLE001 — fail-closed on unknown freeze
            log.warning("line2_skipped_ticket_lookup_failed", error=str(exc))
            return None

    # U-D6c — production T-1 EOD frame source (see module-level
    # _ensure_daily_frame). The lock is per-construction so the 09:35 line1 +
    # line2 daily crons serialise on the first-of-day assembly.
    _frame_lock = asyncio.Lock()

    async def _line2_daily_callback(now: datetime) -> None:
        await _ensure_daily_frame(application, _frame_lock, now)
        frame = getattr(application.state, "line2_daily_frame", None)
        if frame is None:
            # Frame assembly failed (logged in _ensure_daily_frame) — skip
            # cleanly (fail-open, not hollow): no frame means no trade routed.
            log.info("line2_daily_skipped_no_frame")
            return
        risk_engine = _risk_engine_or_none()
        policy = getattr(application.state, "watchlist_policy", None)
        if risk_engine is None or policy is None:
            log.info(
                "line2_daily_skipped_missing_config",
                has_risk=risk_engine is not None,
                has_policy=policy is not None,
            )
            return
        open_tickets = await _open_tickets_or_skip()
        if open_tickets is None:
            return
        run_state = await build_line2_run_state(
            broker=broker,
            risk_engine=risk_engine,
            circuit_breaker=_circuit_breaker(),
            watchlist_policy=policy,
            now=now,
            open_tickets=open_tickets,
        )
        if not run_state.positions:
            return
        names = _names(run_state.positions)
        contexts = await build_line2_code_contexts(
            codes=[p.code for p in run_state.positions],
            name_by_code=names,
            market_meta=market_meta,
            frame=frame,
            data_quality_provider=getattr(
                application.state, "data_quality_provider", None
            ),
            now=now,
        )
        provider = Line2DailyProvider(
            run_state=run_state,
            code_contexts=contexts,
            name_by_code=names,
            snapshot_at=frame.fetch_time_utc,
        )
        await daily_runner.run(frame=frame, provider=provider, now=now)

    async def _line2_intraday_callback(now: datetime) -> None:
        await _ensure_daily_frame(application, _frame_lock, now)
        frame = getattr(application.state, "line2_daily_frame", None)
        if frame is None:
            return  # frame assembly failed (logged); skip cleanly
        risk_engine = _risk_engine_or_none()
        policy = getattr(application.state, "watchlist_policy", None)
        if risk_engine is None or policy is None:
            return
        open_tickets = await _open_tickets_or_skip()
        if open_tickets is None:
            return
        run_state = await build_line2_run_state(
            broker=broker,
            risk_engine=risk_engine,
            circuit_breaker=_circuit_breaker(),
            watchlist_policy=policy,
            now=now,
            open_tickets=open_tickets,
        )
        if not run_state.positions:
            return
        names = _names(run_state.positions)
        contexts = await build_line2_code_contexts(
            codes=[p.code for p in run_state.positions],
            name_by_code=names,
            market_meta=market_meta,
            frame=frame,
            data_quality_provider=getattr(
                application.state, "data_quality_provider", None
            ),
            now=now,
        )
        market_data = getattr(application.state, "market_data", None)
        # Benchmark index closes (bear-regime ADD ban) source = U-D3; empty →
        # classify_regime returns NEUTRAL (no bear ban) conservatively.
        index_closes: tuple[float, ...] = ()

        async def _fetch_spots(codes: object) -> dict[str, object]:
            if market_data is None:
                return {}
            # Tag the batch strictly before the tick ``now`` so the runner's
            # filter_fresh_quotes (snapshot_at < now) accepts a fresh quote.
            snap_at = now - timedelta(seconds=1)
            rows = await market_data.get_watchlist_snapshot(list(codes), snap_at)
            return {row.code: row for row in rows}

        provider = Line2IntradayProvider(
            run_state=run_state,
            code_contexts=contexts,
            name_by_code=names,
            daily_frame=frame,
            index_closes=index_closes,
            fetch_spots_fn=_fetch_spots,
        )
        await intraday_runner.run(provider=provider, now=now)

    async def _line1_daily_callback(now: datetime) -> None:
        # Line-1 reads the SAME T-1 EOD frame as Line-2 daily, assembled
        # lazily + cached by _ensure_daily_frame (U-D6c). Fail-open: a frame
        # assembly failure leaves it unset and Line-1 skips (no BUY routed).
        await _ensure_daily_frame(application, _frame_lock, now)
        frame = getattr(application.state, "line2_daily_frame", None)
        if frame is None:
            log.info("line1_daily_skipped_no_frame")
            return
        risk_engine = _risk_engine_or_none()
        risk_config = getattr(application.state, "risk_config", None)
        policy = getattr(application.state, "watchlist_policy", None)
        llm_router = getattr(application.state, "llm_router", None)
        if (
            risk_engine is None
            or risk_config is None
            or policy is None
            or llm_router is None
        ):
            log.info(
                "line1_daily_skipped_missing_config",
                has_risk=risk_engine is not None,
                has_risk_config=risk_config is not None,
                has_policy=policy is not None,
                has_llm=llm_router is not None,
            )
            return
        open_tickets = await _open_tickets_or_skip()
        if open_tickets is None:
            return
        run_state = await build_line1_run_state(
            broker=broker,
            risk_engine=risk_engine,
            circuit_breaker=_circuit_breaker(),
            watchlist_policy=policy,
            risk_config=risk_config,
            now=now,
            open_tickets=open_tickets,
            # Real today_instruction_count (broker_events) wired in U-D3.
        )
        provider = Line1ContextProvider(
            run_state=run_state,
            frame=frame,
            llm_router=llm_router,
            now=now,
            # U-E2 / 缺口4: the live dual-source spot + 卖一 orderbook layer the
            # cage limit is derived from. ``None`` (no live layer) degrades every
            # lead to a non-actionable notice — never a BUY on the T-1 close.
            market_data=getattr(application.state, "market_data", None),
            snapshot_store=snapshot_store,  # PIT sink for the live cage inputs
        )
        await line1_runner.run(frame=frame, provider=provider, now=now)

    log.info("double_line_runners_initialized", route_mode=route_mode.value)
    return _line2_daily_callback, _line2_intraday_callback, _line1_daily_callback


async def _init_orchestration_layer(application: FastAPI) -> None:
    """I-001 — connect the trading subsystem to durable Mongo persistence.

    Eight components land on ``app.state`` here so the GET endpoints
    stop reporting ``repository_status="unavailable"`` and the
    simulation_auto pipeline has a real broker / appliers /
    scheduler / acceptance loop:

    1. ``BrokerEventStore`` + ``BrokerSnapshotStore`` (Phase E
       persistence) bound to Mongo.
    2. The default :class:`MockBroker` from
       :class:`BrokerRegistry` gets a Mongo-backed
       :class:`MarketMetaProvider` attached so the at-fill price-limit
       recheck (P1-2.C §1.2) runs live.
    3. ``MongoAcceptanceRepository`` + :class:`AcceptanceService`
       (P0-6 §2 红线 5 gate).
    4. ``ExecutionReportApplier`` + ``ReconciliationApplier``
       (E-004 single write-entry points to the broker mirror).
    5. ``ModeRouter`` with the acceptance gate wired in (env-var bypass
       forbidden).
    6. ``SimulationExecutor`` (E-007 — closes the audit Phase C gap
       where RiskEngine PASSED orders never reached the broker).
    7. ``BrokerScheduler`` with the intraday MTM, acceptance, and
       (optional) MiroFish post-close callbacks. simulation_auto path
       keeps the EOD pipeline running every weekday at 16:00.
    8. Six Mongo-backed repositories (instruction plans, equity points,
       reconciliation tickets, daily reconciliations, broker snapshots,
       acceptance reports) attached so the read endpoints can render
       live state.

    Failure is fail-open: if Mongo is missing (dev loop without a
    replica set) the orchestration stays None and the API endpoints
    keep returning ``repository_status="unavailable"`` instead of
    crashing the lifespan.
    """
    mongodb = getattr(application.state, "mongodb", None)
    if mongodb is None:
        log.warning("orchestration_layer_skip", missing="mongodb")
        return

    db = getattr(mongodb, "_db", None)
    if db is None:
        log.warning("orchestration_layer_skip", missing="mongodb._db")
        return

    audit_store = getattr(application.state, "audit_store", None)
    if audit_store is None:
        log.warning("orchestration_layer_skip", missing="audit_store")
        return

    registry = getattr(application.state, "broker_registry", None)
    if registry is None:
        log.warning("orchestration_layer_skip", missing="broker_registry")
        return

    # 1. Phase E persistence stores bound to the same Mongo client used
    # for everything else. Both are append-only; the transactional
    # invariants live inside the stores themselves (BrokerEventStore +
    # BrokerSnapshotStore each open a fresh session per append).
    from backend.broker.persistence.store import (
        BrokerEventStore,
        BrokerSnapshotStore,
    )

    mongo_client = application.state.mongo_client
    event_store = BrokerEventStore(
        mongo_client,
        db[BrokerEventStore.COLLECTION_NAME],
    )
    snapshot_store = BrokerSnapshotStore(
        mongo_client,
        db[BrokerSnapshotStore.COLLECTION_NAME],
    )
    application.state.broker_event_store = event_store
    application.state.broker_snapshot_store = snapshot_store

    # 2. Default broker + Mongo-backed market_meta for at-fill recheck.
    broker = registry.get_broker("default")
    from backend.data.market_meta_provider import MongoBackedMarketMetaProvider

    market_meta = MongoBackedMarketMetaProvider(
        mongodb=mongodb,
        redis_client=getattr(application.state, "redis", None),
    )
    broker.attach_market_meta(market_meta)
    application.state.market_meta_provider = market_meta

    # 2b. Recover broker state from broker_snapshots + broker_events
    # BEFORE exposing the broker to SimulationExecutor / MTM / EOD
    # (Codex Cycle 6 P1 fix). Without this seed the registry's fresh
    # MockBroker would route + price-MTM against an empty account while
    # the durable mirror tracked positions worth millions of CNY; the
    # first routed order silently diverges the two views. Recovery is
    # fail-closed: ChecksumMismatchError / RecoveryError refuse boot so
    # the operator sees the corruption immediately rather than at the
    # first trade. ``recover_state`` returns a baseline (initial cash,
    # no positions) on a fresh deploy with no snapshot, which is the
    # right behaviour.
    from backend.broker.persistence import recover_state

    recovered = await recover_state(
        event_store=event_store,
        snapshot_store=snapshot_store,
        initial_capital=broker._initial_capital,  # noqa: SLF001
    )
    await broker.seed_from_recovery(
        cash=recovered.cash,
        frozen_cash=recovered.frozen_cash,
        initial_capital=recovered.initial_capital,
        positions=recovered.to_snapshot_positions(),
    )
    log.info(
        "broker_recovered_from_persistence",
        cash=recovered.cash,
        frozen_cash=recovered.frozen_cash,
        positions=len(recovered.positions),
        last_sequence=recovered.last_sequence,
        events_replayed=recovered.events_replayed,
    )

    # 3. Acceptance gate (P0-6) — Mongo-backed so the 16:00 cron's
    # upsert and the mode-router's lookup share the same source of truth.
    from backend.services.acceptance_report import AcceptanceService
    from backend.services.mongo_repositories import (
        MongoAcceptanceRepository,
        MongoDailyReconciliationStore,
        MongoEquityPointRepository,
        MongoInstructionPlanRepository,
        MongoSnapshotLookup,
        MongoTicketRepository,
    )

    acceptance_repo = MongoAcceptanceRepository(db)
    acceptance_service = AcceptanceService(repository=acceptance_repo)
    application.state.acceptance_repository = acceptance_repo
    application.state.acceptance_service = acceptance_service

    # Codex cycle 2 P1 fix — hydrate the in-memory reset state from the
    # JSONL audit trail so a restart between a J-004 reset trigger and
    # 45 fresh trading days does not silently drop the window clamp.
    # JSONL is the dependable layer per P1-6 §1.7.4; Mongo may not be
    # consulted here because the AuditStore Mongo collection only
    # becomes queryable after _init_data_layer finishes. The latest
    # ``SYSTEM_INTERRUPTED`` event tagged
    # ``reason_namespace='acceptance_reset_trigger'`` carries
    # ``trigger_type`` in the payload — that's the reason identifier.
    from backend.audit.models import AuditEventType
    from backend.audit.store import read_jsonl
    from backend.services.acceptance_report import WindowResetState

    try:
        _historical_events = read_jsonl(
            application.state.audit_jsonl_path
        )
    except Exception as exc:  # noqa: BLE001 — never block startup on read
        log.warning("acceptance_reset_hydrate_read_failed", error=str(exc))
        _historical_events = []
    # Codex cycle 5 P2 fix — pick the max-timestamp matching event
    # rather than the last in append order; JSONL appends may be
    # out-of-order across multiple processes / batched flushes.
    _reset_candidates = [
        _e
        for _e in _historical_events
        if _e.event_type is AuditEventType.SYSTEM_INTERRUPTED
        and _e.reason_namespace == "acceptance_reset_trigger"
    ]
    _latest_reset = (
        max(_reset_candidates, key=lambda e: e.timestamp)
        if _reset_candidates
        else None
    )
    if _latest_reset is not None:
        _payload = _latest_reset.payload or {}
        _trigger_type = str(_payload.get("trigger_type", "UNKNOWN"))[:64]
        _state = WindowResetState(
            last_reset_at=_latest_reset.timestamp,
            last_reset_reason=_trigger_type,
        )
        acceptance_service.set_reset_state(_state)
        log.info(
            "acceptance_reset_state_hydrated",
            trigger_type=_trigger_type,
            last_reset_at=_latest_reset.timestamp.isoformat(),
        )
    else:
        log.info("acceptance_reset_state_clean", reason="no_prior_reset_event")

    # 4. Appliers — the single legitimate entry points for external
    # writes to the broker mirror (P1-2.A red line).
    # U-D4: a Redis-backed report_id guard makes the applier idempotent
    # across uvicorn workers + process restarts (within its TTL) — the
    # last line of defence against a Feishu redelivery / frontend
    # double-submit double-mutating the broker. Falls back to in-process
    # when Redis is unavailable (dev / degraded).
    from backend.broker.applied_report_guard import (
        InMemoryAppliedReportGuard,
        RedisAppliedReportGuard,
    )
    from backend.broker.appliers import (
        ExecutionReportApplier,
        ReconciliationApplier,
    )

    _applier_redis = getattr(application.state, "redis", None)
    applied_guard = (
        RedisAppliedReportGuard(_applier_redis)
        if _applier_redis is not None
        else InMemoryAppliedReportGuard()
    )
    execution_applier = ExecutionReportApplier(
        broker, event_store, audit_store, applied_guard=applied_guard
    )
    # Codex Cycle 1 P2 fix: ReconciliationApplier reads the
    # daily-reconciliation entry by sync ``dict.get`` on the trade_date
    # key. The orchestrator persists each MISMATCH reply through the
    # Mongo store; without a dual-write the applier would later raise
    # ``ValueError`` on RESOLVED_USER_AS_TRUTH because the in-memory map
    # stays empty for the process lifetime. The shared
    # ``daily_cache`` below is the bridge — the orchestrator writes
    # through the ``_DualWriteDailyStore`` wrapper which mirrors every
    # save into the cache (and also warms the cache from Mongo on
    # demand via ``get``).
    daily_cache: dict[str, object] = {}
    reconciliation_applier = ReconciliationApplier(
        broker, event_store, audit_store, daily_reconciliations=daily_cache
    )
    application.state.execution_report_applier = execution_applier
    application.state.reconciliation_applier = reconciliation_applier
    application.state.daily_reconciliation_cache = daily_cache

    # 5. ModeRouter with the acceptance gate plumbed in. The mode-state
    # also exposes the :class:`ModeSwitchProbe` consumed by the Builder
    # + SimulationExecutor (5th freeze source).
    from backend.services.mode_router import SIMULATION_AUTO, ModeRouter

    mode_router = ModeRouter(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        initial_mode=SIMULATION_AUTO,
        acceptance_gate=acceptance_service,
    )
    application.state.mode_router = mode_router

    # 6. SimulationExecutor — the bridge that closes the audit Phase C
    # gap. The freeze_state plumbing arrives from the BrokerScheduler
    # below (5th freeze source = eod_pipeline_freeze).
    from backend.broker.scheduler import EodPipelineFreezeState
    from backend.services.ledger import (
        DecisionLedgerService,
        MongoLedgerRepository,
    )
    from backend.services.simulation_executor import SimulationExecutor

    freeze_state = EodPipelineFreezeState()
    # System-status probe (G-002) reads ``app.state.eod_pipeline_freeze_state``
    # OR ``app.state.broker_scheduler.eod_pipeline_freeze_state`` —
    # ``BrokerScheduler`` exposes it as ``.freeze_state`` so we attach
    # the canonical name on app.state directly (Codex Cycle 4 P2 fix —
    # without this the EOD freeze never surfaces in the StatusBar even
    # after a real eod_pipeline_failed_twice).
    application.state.eod_pipeline_freeze_state = freeze_state

    ledger_service = DecisionLedgerService(MongoLedgerRepository(mongodb))
    application.state.decision_ledger = ledger_service

    simulation_executor = SimulationExecutor(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        ledger=ledger_service,
        freeze_state=freeze_state,
        mode_switch_probe=mode_router.mode_state,
    )
    application.state.simulation_executor = simulation_executor

    # 7. EquityPointBuilder + Mongo equity point repository for the
    # 30s intraday MTM cron callback.
    from backend.broker.equity import EquityPointBuilder

    equity_repo = MongoEquityPointRepository(db)
    equity_builder = EquityPointBuilder(broker, market_meta)
    application.state.equity_point_repository = equity_repo
    application.state.equity_point_builder = equity_builder

    async def _build_and_upsert_equity_point(now: datetime) -> None:
        """Shared helper — build EquityPoint, upsert to Mongo, log + skip
        on builder/upsert errors. No trading-hours guard: callers gate.
        """
        try:
            last_seq = await event_store.read_latest_sequence()
        except Exception as exc:  # noqa: BLE001
            log.warning("intraday_mtm_seq_lookup_failed", error=str(exc))
            last_seq = None
        try:
            point = await equity_builder.build(
                now=now, last_broker_event_id=last_seq
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("intraday_mtm_build_failed", error=str(exc))
            return
        try:
            await equity_repo.upsert(point)
        except Exception as exc:  # noqa: BLE001
            log.warning("intraday_mtm_upsert_failed", error=str(exc))

    async def _intraday_mtm_callback(now: datetime) -> None:
        """30s cron — build the EquityPoint and persist it to Mongo.

        Strict trading-hours guard (Codex Cycle 3+7 P2 fix): the
        ``BrokerScheduler`` runs this on a flat 30-second
        :class:`IntervalTrigger`, which would otherwise fire 24/7. Off
        market hours the EquityPointBuilder either emits a zero-position
        ``FRESH`` point (empty account) or a degraded stale-cache point
        — both pollute the Portfolio page's "latest" view and grow the
        ``equity_points`` collection unbounded.

        The 16:00 EOD chain bypasses this guard via a separate
        ``eod_close_callback`` wired on :class:`BrokerScheduler` (see
        :func:`_eod_close_callback` below). The 30s interval tick stays
        strictly inside the morning + afternoon trading sessions —
        post-close interval ticks would still pollute equity_points
        if 15:00–16:30 were treated as "allowed window" for the 30s
        cron (Codex Cycle 7 P2 follow-up).
        """
        from backend.utils.trading_hours import is_trading_hours

        if not is_trading_hours(now):
            return
        await _build_and_upsert_equity_point(now)

    async def _eod_close_callback(now: datetime) -> None:
        """16:00 EOD chain — write the single closing EquityPoint.

        Bypasses the trading-hours guard so the post-close MTM tick
        lands even though afternoon close is 15:00. Called exactly
        once per EOD pipeline run by
        :meth:`BrokerScheduler.run_eod_pipeline`; the 30s interval
        tick stays gated by :func:`_intraday_mtm_callback`'s strict
        ``is_trading_hours`` check.
        """
        await _build_and_upsert_equity_point(now)

    # Codex cycle 5 P3 fix — bind ``ticket_repo`` BEFORE the
    # ``_has_open_reconciliation_ticket`` closure (and therefore
    # before ``broker_scheduler.start()`` below). Previously the
    # repository was assigned after ``await broker_scheduler.start()``;
    # an APScheduler misfire firing the EOD job during the start
    # window would raise NameError inside the closure and fail the
    # whole EOD chain. The repository is otherwise inert when no
    # tickets exist, so binding it earlier has no behavioural cost.
    ticket_repo = MongoTicketRepository(db)
    mongo_daily_store = MongoDailyReconciliationStore(db)
    snapshot_lookup = MongoSnapshotLookup(db)
    # Attach the ticket repo to app.state BEFORE _init_line2_runners +
    # broker_scheduler.start() below (Codex U-D1 verify P1). The Line-2 cron
    # callbacks read reconciliation_ticket_repository to feed the builder's
    # ticket_freeze early return; if the repo were attached only after start()
    # a cron firing in the startup window would see None, fail-open to no
    # tickets, and bypass the reconciliation freeze.
    application.state.reconciliation_ticket_repository = ticket_repo

    async def _has_open_reconciliation_ticket() -> bool:
        """Return True iff Mongo holds ANY OPEN / EXPIRED ticket.

        Codex Cycle 7 P2 fix — ``AcceptanceComputeInput.reconciliation_paused``
        was previously hardcoded False, so the PAUSED outcome
        :meth:`AcceptanceService.compute` defines for unresolved
        reconciliation state was never produced.

        Codex Cycle 9 P2 fix — queries ALL trade_dates, not just today.
        A prior day's unresolved ticket still freezes BUY/SELL routing
        per P0-5; filtering only today let stale freezes leak through
        as PASS/FAIL.
        """
        try:
            tickets = await ticket_repo.list_all_open()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "acceptance_paused_probe_failed",
                error=str(exc),
            )
            return False
        return bool(tickets)

    async def _acceptance_callback(now: datetime) -> None:
        """16:00 EOD pipeline — recompute + upsert today's acceptance.

        The acceptance service falls back to ``INSUFFICIENT_DATA`` when
        the rolling 45-day window has not filled — we still want to
        write that row so the API surface always has a "latest" point
        and operators can watch the warm-up.
        """
        from backend.services.acceptance_report import (
            AcceptanceComputeInput,
            StabilityCounters,
            StrategyCounters,
        )

        trade_date = now.astimezone(SHANGHAI_TZ).date()
        # Reconciliation freeze probe (Codex Cycle 7 P2 fix) — if there
        # is an OPEN / EXPIRED ticket for today,
        # ``AcceptanceService.compute`` returns ``PAUSED`` instead of
        # FAIL so a transient unresolved reconciliation does not
        # falsely cycle the gate's outcome.
        paused = await _has_open_reconciliation_ticket()
        # Counters are filled in by downstream telemetry pipes in later
        # phases; today we emit an empty stability/strategy state so
        # the resulting AcceptanceReport has trading_days_in_window /
        # outcome metadata and the rolling window window-start can
        # advance. The MongoAcceptanceRepository upsert is keyed on
        # ``trade_date`` so a same-day re-run overwrites cleanly.
        payload = AcceptanceComputeInput(
            now=now,
            trade_date=trade_date,
            stability=StabilityCounters(
                completed_instructions=0,
                total_instructions=0,
                accurate_reports=0,
                total_reports=0,
                data_missing_ticks=0,
                total_data_ticks=0,
                llm_timeout_calls=0,
                total_llm_calls=0,
                generated_signal_days=0,
                expected_signal_days=0,
            ),
            strategy=StrategyCounters(
                max_drawdown_pct=0.0,
                pnl_cny=0.0,
                csi300_excess_pct=0.0,
            ),
            reconciliation_paused=paused,
        )
        report = acceptance_service.compute(payload)
        # Codex Cycle 5 P2 fix — DO NOT swallow upsert failures here.
        # ``BrokerScheduler.run_eod_pipeline`` treats a clean callback
        # return as success and skips its retry / freeze path; a
        # silently-dropped acceptance row would leave the P0-6 §2 红线 5
        # gate without a "latest" point AND mask the EOD failure from
        # the operator. Propagate the exception so the scheduler can
        # retry-once + activate the freeze + write
        # FREEZE_SOURCE_EOD_PIPELINE_FREEZE audit on the second failure.
        await acceptance_service.upsert(report)

    # 7b. BrokerScheduler — the 4-cron broker lifecycle. Pass the
    # MongoDBService as the replica-set gate (Codex Cycle 5 P2 fix):
    # broker_events + broker_snapshots run under multi-document
    # transactions which require a replica set; a standalone Mongo
    # would silently boot and then fail at the first order-routing
    # transaction. The gate runs once in :meth:`BrokerScheduler.start`
    # and surfaces ``ReplicaSetUnavailableError`` so the operator sees
    # the misconfiguration at startup, not at the first trade. Dev
    # environments can opt out via ``QUANTMIND_BROKER_SKIP_RS_GATE=1``
    # — the helper below resolves the gate object once.
    from backend.broker.scheduler import BrokerScheduler
    from backend.services.acceptance_report import GoLiveTier

    # U-D2 — resolve the go-live tier once (default FULL). PILOT prepends the
    # "模拟盘·人工·试点" banner to every order-bearing Feishu message; it is
    # threaded into all three runners at construction so the renderer single
    # source (P0-6-amendment-2026-05-25 §2.3) is the only place the banner is
    # composed. Stored on app.state so the startup feishu gate + the
    # mode-switch lifecycle below require the SAME tier's acceptance gate.
    feishu_tier = _resolve_feishu_tier()
    application.state.feishu_go_live_tier = feishu_tier
    pilot_banner = feishu_tier is GoLiveTier.PILOT

    # U-D1 / U-D1b — build the double-line production runners + their shared
    # RouteCoordinator and get the three cron callbacks before constructing the
    # scheduler. simulation_executor / ledger_service / market_meta are all live
    # by now (llm_router / risk_config / watchlist_policy are read lazily in the
    # Line-1 callback at cron-fire time).
    line2_daily_cb, line2_intraday_cb, line1_cb = await _init_line2_runners(
        application,
        broker=broker,
        audit_store=audit_store,
        ledger=ledger_service,
        pilot=pilot_banner,
        simulation_executor=simulation_executor,
        market_meta=market_meta,
    )

    # U-D2 — wire the live PILOT readiness probe onto the acceptance service.
    # The reconciliation ticket repo is already on app.state (attached before
    # the runners); broker / redis / acceptance_service are live too. The probe
    # is consulted only on the PILOT branch of can_switch_to_feishu_on; in the
    # FULL / simulation_auto default it is dormant. Each check is fail-closed.
    acceptance_service.set_pilot_probe(_build_pilot_probe(application, broker))

    replica_set_gate = (
        None
        if os.environ.get("QUANTMIND_BROKER_SKIP_RS_GATE", "").strip() == "1"
        else mongodb
    )

    broker_scheduler = BrokerScheduler(
        broker=broker,
        event_store=event_store,
        snapshot_store=snapshot_store,
        audit_store=audit_store,
        replica_set_gate=replica_set_gate,
        freeze_state=freeze_state,
        intraday_mtm_callback=_intraday_mtm_callback,
        eod_close_callback=_eod_close_callback,
        acceptance_callback=_acceptance_callback,
        line2_daily_runner_callback=line2_daily_cb,
        line2_intraday_runner_callback=line2_intraday_cb,
        line1_runner_callback=line1_cb,
        # U-A2 / U-D1 — align the scheduler's NAV fallback to the ¥100k
        # 同花顺模拟盘 the broker config loads (recovery still wins when a
        # snapshot exists).
        initial_capital=broker._initial_capital,  # noqa: SLF001
    )
    # Codex Cycle 6 P1 fix: DO NOT swallow start() failures. The
    # replica_set_gate is the only Mongo-multi-document-transaction
    # pre-flight; if it raises (single-node Mongo, gate failure) and we
    # log + continue, ``SimulationExecutor`` is live and the FIRST
    # routed order mutates the broker but ``BrokerEventStore.append_many``
    # hits the same transaction failure — leaving an unpersisted fill
    # that diverges the durable mirror. The
    # QUANTMIND_BROKER_SKIP_RS_GATE=1 env var is the only sanctioned
    # opt-out, applied above by leaving replica_set_gate=None.
    await broker_scheduler.start()
    application.state.broker_scheduler = broker_scheduler

    # 8. Repositories for the GET surfaces (G-003 / G-004 / G-006 /
    # G-007 endpoints). Each repo is a thin Mongo adapter that
    # accepts already-validated Pydantic DTOs.
    application.state.instruction_plan_repository = (
        MongoInstructionPlanRepository(db)
    )

    # ``ticket_repo`` / ``mongo_daily_store`` / ``snapshot_lookup`` were
    # bound earlier (before the BrokerScheduler closures + start()) per
    # Codex cycle 5 P3 fix. Construct the dual-write daily store now
    # that the constructor + cache are both available.

    # Dual-write daily store — mirrors every save into the shared
    # ``daily_cache`` so the sync-API ReconciliationApplier sees the
    # same row the orchestrator just persisted to Mongo (Codex Cycle 1
    # P2 fix). ``get`` also warms the cache from Mongo on first read,
    # which is the only path that survives a process restart for the
    # feishu_interactive overlay.
    class _DualWriteDailyStore:
        """DailyReconciliationStore wrapper that mirrors saves into the
        applier-readable cache. Keyed by ``ticket_id`` (Codex Cycle 7
        P2 fix — keying by ``trade_date`` silently overwrote earlier
        tickets on multi-ticket days).
        """

        def __init__(
            self,
            mongo_store: MongoDailyReconciliationStore,
            cache: dict[str, object],
        ) -> None:
            self._mongo = mongo_store
            self._cache = cache

        async def save(self, daily: object) -> None:
            await self._mongo.save(daily)  # type: ignore[arg-type]
            ticket_id = getattr(daily, "ticket_id", None)
            if isinstance(ticket_id, str):
                self._cache[ticket_id] = daily

        async def get(self, key: str) -> object | None:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            loaded = await self._mongo.get(key)
            if loaded is not None:
                ticket_id = getattr(loaded, "ticket_id", None)
                if isinstance(ticket_id, str):
                    self._cache[ticket_id] = loaded
            return loaded

    daily_store = _DualWriteDailyStore(mongo_daily_store, daily_cache)

    # reconciliation_ticket_repository was attached earlier (before start()
    # per Codex U-D1 verify P1); the remaining two repos are read-only GET
    # surfaces with no startup-window race.
    application.state.daily_reconciliation_store = daily_store
    application.state.broker_snapshot_lookup = snapshot_lookup

    # 9. ExecutionReport + Reconciliation orchestrators. Constructed
    # even in simulation_auto so the GET endpoints can answer ``status=
    # "available_but_no_data"`` instead of ``"unavailable"`` — the
    # feishu client is None so outbound clarifications degrade to
    # log-only, which is the documented contract for both classes.
    feishu_client = getattr(application.state, "feishu_client", None)
    decision_chat = os.environ.get("FEISHU_DECISION_CHAT_ID", "").strip()

    from backend.integrations.feishu.parser import ExecutionReportOrchestrator
    from backend.integrations.feishu.reconciliation import (
        ReconciliationOrchestrator,
    )
    from backend.integrations.feishu.renderer import MessageRenderer

    renderer = MessageRenderer()

    application.state.execution_report_orchestrator = (
        ExecutionReportOrchestrator(
            applier=execution_applier,
            plan_lookup=application.state.instruction_plan_repository,
            feishu=feishu_client,
            renderer=renderer,
            audit=audit_store,
        )
    )

    if decision_chat:
        application.state.reconciliation_orchestrator = (
            ReconciliationOrchestrator(
                feishu=feishu_client,
                renderer=renderer,
                ticket_repo=ticket_repo,
                daily_store=daily_store,
                applier=reconciliation_applier,
                decision_chat_id=decision_chat,
                snapshot_lookup=snapshot_lookup,
            )
        )
    else:
        # No decision chat configured — the ReconciliationOrchestrator
        # requires a non-empty chat id, so leave it None until owner
        # wires FEISHU_DECISION_CHAT_ID in ~/.bashrc (P0-2-amendment-
        # 2026-05-16 keeps the decision/alert chats strictly separate).
        application.state.reconciliation_orchestrator = None

    log.info(
        "orchestration_layer_initialized",
        feishu_client=feishu_client is not None,
        decision_chat_wired=bool(decision_chat),
    )


async def _init_analysis_scheduler(application: FastAPI) -> None:
    """Initialize the daily analysis orchestrator.

    Phase 5B-T02: when ``config/universe_policy.yaml`` is present the
    scheduler runs in Fast/Slow mode (two cron jobs). When the file is
    missing or fails to parse we log a warning and fall back to the
    legacy single-cron mode so a typo in the YAML can't bring the
    scheduler down.
    """
    from backend.agents.models import AnalysisServices, PipelineConfig
    from backend.data.analysis_scheduler import AnalysisScheduler
    from backend.services.universe_policy import (
        UniversePolicyError,
        load_policy,
    )

    required = [
        "llm_router",
        "market_data",
        "history_data",
        "news_crawler",
        "mongodb",
        "watchlist",
    ]
    for attr in required:
        if not hasattr(application.state, attr):
            log.warning("analysis_scheduler_skip", missing=attr)
            return

    # C-006 (codex cycle 2 P1): construct MiroFishSimulator with the
    # live LLMRouter so intelligence_officer_node actually enters the
    # event-driven branch (it is gated on ``mirofish_simulator is not
    # None``). Without this, the C-006 evidence write only triggers in
    # tests where a mock simulator is injected. Failure is fail-open
    # (None) so the rest of analysis still runs.
    mirofish_simulator = None
    try:
        from backend.mirofish.simulator import MiroFishSimulator

        mirofish_simulator = MiroFishSimulator(application.state.llm_router)
    except Exception as exc:
        log.warning("mirofish_simulator_init_failed", error=str(exc))

    services = AnalysisServices(
        llm_router=application.state.llm_router,
        market_data=application.state.market_data,
        history_data=application.state.history_data,
        news_crawler=application.state.news_crawler,
        mongodb=application.state.mongodb,
        mirofish_simulator=mirofish_simulator,
        # C-006 (codex cycle 1 P1): hand the MiroFishEvidenceWriter into
        # AnalysisServices so intelligence_officer's event-driven path
        # writes MIROFISH- evidence to evidence_collection in the
        # default startup, not just in tests.
        mirofish_writer=getattr(
            application.state, "mirofish_writer", None
        ),
        pipeline_config=PipelineConfig(),
    )
    application.state.mirofish_simulator = mirofish_simulator

    policy_path = os.environ.get(
        "QUANTMIND_UNIVERSE_POLICY_PATH", "config/universe_policy.yaml"
    )
    policy = None
    if os.path.exists(policy_path):
        try:
            policy = load_policy(policy_path)
        except (UniversePolicyError, OSError) as exc:
            log.warning(
                "universe_policy_load_failed",
                path=policy_path,
                error=str(exc),
            )
    else:
        log.info("universe_policy_missing", path=policy_path)

    # Idempotent watchlist seed (A-002/A-004 follow-up per codex review).
    # The POST/DELETE handlers were destructively removed, so without
    # this step a fresh Mongo deployment leaves WatchlistService empty
    # and AnalysisScheduler skips every Fast/Slow tick. Seed the codes
    # declared in the policy file at boot — runtime mutation is still
    # forbidden per P0-9.
    if policy is not None:
        try:
            await _seed_watchlist_from_policy(
                application.state.watchlist, policy
            )
        except Exception as exc:
            log.warning("watchlist_seed_failed", error=str(exc))

    analysis_scheduler = AnalysisScheduler(
        watchlist=application.state.watchlist,
        services=services,
        mongodb=application.state.mongodb,
        redis_client=getattr(application.state, "redis", None),
        policy=policy,
        alert_dispatcher=getattr(application.state, "alert_dispatcher", None),
    )
    await analysis_scheduler.start()
    # Re-read the scheduler's policy AFTER start(): a malformed cron in
    # the YAML triggers a runtime fallback that clears the policy, and
    # app.state must reflect that so the API doesn't keep accepting
    # /category mutations against a policy whose cron jobs were never
    # registered (Codex R6 HIGH #2).
    application.state.watchlist_policy = analysis_scheduler.policy
    application.state.analysis_scheduler = analysis_scheduler
    log.info(
        "analysis_scheduler_initialized",
        fast_slow_mode=analysis_scheduler.policy is not None,
    )


async def _shutdown_data_layer(application: FastAPI) -> None:
    """Shut down the data + orchestration layer services."""
    if hasattr(application.state, "analysis_scheduler"):
        await application.state.analysis_scheduler.stop()
    if hasattr(application.state, "scheduler"):
        await application.state.scheduler.stop()
    if hasattr(application.state, "broker_scheduler") and (
        application.state.broker_scheduler is not None
    ):
        try:
            await application.state.broker_scheduler.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("broker_scheduler_stop_failed", error=str(exc))
    if hasattr(application.state, "mongo_client"):
        application.state.mongo_client.close()
    log.info("data_layer_stopped")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # -- Startup --
    import time as _time

    import redis.asyncio as aioredis

    from backend.logging_config import configure_logging

    configure_logging(
        log_dir="logs", level=os.environ.get("LOG_LEVEL", "INFO")
    )

    # Resolve LOG_AUDIT_PATH once at the very top of lifespan so every
    # audit-write path lands in the same file. Codex cycle 2 P2 fix —
    # previously the secrets_validator soft-warning JSONL dispatch used
    # the default ``logs/audit.jsonl`` while the AuditStore + owner
    # auth used the LOG_AUDIT_PATH-derived path, silently splitting the
    # audit trail in any deployment that overrode LOG_AUDIT_PATH.
    from pathlib import Path as _Path

    _resolved_audit_jsonl_path = _Path(
        os.environ.get("LOG_AUDIT_PATH", "logs/audit.jsonl")
    )

    # Secrets fail-fast gate (P1-6 / H-001). Must run before any module
    # that reads credentials (LLMRouter / Feishu client). Errors raise
    # SecretsValidationError (SystemExit) so uvicorn / docker-compose
    # exits non-zero with a clear message. Warnings are dispatched to
    # the audit store once it is wired below.
    from backend.services.secrets_validator import assert_secrets_or_exit

    application.state.secrets = assert_secrets_or_exit(
        audit_jsonl_path=_resolved_audit_jsonl_path,
    )

    # J-007 owner production-run authorization gate (P0-6 §2 红线 5
    # 前置二级门). When ``QUANTMIND_PROD_RUN=1`` the operator must
    # additionally export ``QUANTMIND_OWNER_PROD_AUTHORIZATION=<owner>:
    # YYYYMMDD`` no older than 7 days. A successful authorization writes
    # one ``OWNER_PROD_AUTHORIZATION_GRANTED`` audit event directly to
    # the JSONL backup (the AuditStore is constructed below). Outside
    # production mode this is a no-op so J-002 / J-005 dev harnesses
    # keep working. Errors raise ``OwnerProdAuthorizationError``
    # (SystemExit) so uvicorn exits non-zero with a clear message.
    #
    # Owner authorization gate reuses the single ``_resolved_audit_jsonl_path``
    # resolved at the top of lifespan so the OWNER_PROD_AUTHORIZATION_GRANTED
    # audit lands in the same JSONL file the operator queries via the
    # H-002 audit endpoint / scripts/query_audit.py CLI (Codex cycle 2 P2 fix
    # also covered the secrets_validator soft warnings now that the path is
    # resolved before either call).
    from backend.services.owner_authorization import (
        assert_owner_authorization_or_exit,
    )

    application.state.owner_authorization = assert_owner_authorization_or_exit(
        fingerprints=application.state.secrets.fingerprints,
        audit_jsonl_path=_resolved_audit_jsonl_path,
    )

    # Resolve run mode (P0-1). simulation_auto is always-on; FEISHU_INTERACTIVE_ENABLED
    # toggles the human-in-loop overlay. Replaces the legacy AUTHORIZATION_MODE x
    # QUANTMIND_PHASE matrix; Feishu credential fail-fast already happened in
    # the secrets validator above.
    from backend.services.run_mode import assert_run_mode_env

    application.state.run_mode = assert_run_mode_env()

    application.state.app_start_time = _time.time()

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_pool = aioredis.from_url(redis_url, decode_responses=True)

    router = LLMRouter(config_path="config/agent_models.yaml")
    await router.initialize(redis_client=redis_pool)

    # A-007 startup assertion: fund_manager_shadow_baseline must stay on
    # frequency=shadow_only so it can never enter the decision path. The
    # check is fail-fast (SystemExit) — uvicorn exits non-zero so systemd
    # / docker-compose surfaces the misconfiguration immediately.
    _shadow_agent = router.config.agents.get("fund_manager_shadow_baseline")
    if _shadow_agent is not None and _shadow_agent.frequency != "shadow_only":
        raise SystemExit(
            "Refusing to start: fund_manager_shadow_baseline.frequency must be "
            f"'shadow_only' but is {_shadow_agent.frequency!r}. P0-10 forbids "
            "shadow_baseline from entering the decision path; see A-007."
        )

    application.state.redis = redis_pool
    application.state.llm_router = router
    application.state.config_service = ConfigService(redis_client=redis_pool)

    # H-002 — single AuditStore singleton (JSONL primary + Mongo
    # fail-open). Built before the data layer so anything constructed in
    # `_init_data_layer` or `_init_trading_layer` can pull the same
    # instance off app.state. JSONL path is the one resolved at the top
    # of lifespan so secrets validator + owner auth + AuditStore all
    # share a single source (Codex cycle 1 + cycle 2 P2 fix).
    from backend.audit.store import AuditStore

    audit_jsonl_path = _resolved_audit_jsonl_path
    application.state.audit_jsonl_path = audit_jsonl_path
    # The Mongo handle is attached once the data layer initializes; until
    # then audit writes still land in JSONL via the lazy collection.
    application.state.audit_store = AuditStore(
        _LazyAuditCollection(application),
        jsonl_path=audit_jsonl_path,
    )

    # Live agent-debate SSE hub (A2). Lives in-process; jobs lost on restart
    # are acceptable because the full AnalysisRecord is persisted to MongoDB.
    from backend.services.analysis_stream import AnalysisStreamHub

    application.state.analysis_stream_hub = AnalysisStreamHub()

    # Webhook alerter (Session C/D). Falls back to log-only when
    # ALERT_WEBHOOK_URL is unset, so wiring this up is safe.
    from backend.monitoring.alerter import Alerter

    application.state.alerter = Alerter()

    # Feishu OpenAPI client (F-001). ``from_env`` returns ``None`` when
    # FEISHU_INTERACTIVE_ENABLED is falsy, which is the simulation_auto
    # baseline — downstream code (Alerter F-006, renderer F-002, long
    # connection F-003) must tolerate ``None`` until the overlay is
    # turned on.
    from backend.integrations.feishu.client import FeishuClient

    application.state.feishu_client = FeishuClient.from_env()

    # Feishu OpenAPI alerter (F-006 / P0-2-amendment-2026-05-16). Routes
    # every system alert to FEISHU_ALERT_CHAT_ID via the self-built app
    # API. Falls back to no_client mode when the overlay is off; in
    # that case alerts log + audit but never reach Feishu (the legacy
    # ALERT_WEBHOOK_URL path was retired by the amendment).
    application.state.feishu_alerter = None
    if application.state.feishu_client is not None:
        from backend.integrations.feishu.alerter import FeishuAlerter
        from backend.integrations.feishu.renderer import MessageRenderer

        alert_chat = os.environ.get("FEISHU_ALERT_CHAT_ID", "").strip()
        if alert_chat:
            application.state.feishu_alerter = FeishuAlerter(
                feishu=application.state.feishu_client,
                renderer=MessageRenderer(),
                alert_chat_id=alert_chat,
            )

    # H-004 — central AlertDispatcher composes AuditStore + FeishuAlerter.
    # Used by cost_guard (P1-7), scheduler, llm router, and the long
    # connection monitor; the locked ALERT_MATRIX decides per type
    # whether the alert flows to Feishu in addition to the audit trail.
    # Simulation_auto path keeps the dispatcher with feishu_alerter=None
    # so every alert degrades cleanly to audit-only.
    from backend.monitoring.alert_dispatcher import AlertDispatcher

    application.state.alert_dispatcher = AlertDispatcher(
        audit=application.state.audit_store,
        feishu_alerter=application.state.feishu_alerter,
    )

    # Feishu long-connection receiver placeholders. The
    # orchestrators are constructed in :func:`_init_orchestration_layer`
    # (after the data + trading layers are up) so that even
    # simulation_auto has a populated GET surface backed by Mongo
    # repositories. The receiver itself only starts once the
    # acceptance gate sanctions the switch (P0-6 §2 红线 5).
    application.state.feishu_event_receiver = None
    application.state.execution_report_orchestrator = None
    application.state.reconciliation_orchestrator = None

    await _init_data_layer(application, redis_pool)

    # Trading subsystem
    await _init_trading_layer(application)

    # I-001 — durable orchestration on top of trading + data layers.
    # Constructs BrokerScheduler / SimulationExecutor / Appliers /
    # ModeRouter / 6 Mongo-backed repositories / orchestrators.
    await _init_orchestration_layer(application)

    # Feishu long-connection acceptance gate (P0-6 §2 红线 5). The
    # overlay can only start once the AcceptanceService reports PASS;
    # the env var alone is not a valid sanction. Fail-closed on the
    # interactive path so a mis-set env var cannot silently bypass
    # 45 trading days of stability + strategy checks.
    if application.state.feishu_client is not None:
        gate = getattr(application.state, "acceptance_service", None)
        startup_tier = getattr(
            application.state, "feishu_go_live_tier", None
        ) or _resolve_feishu_tier()
        gate_decision = (
            await gate.can_switch_to_feishu_on(startup_tier)
            if gate is not None
            else None
        )
        if gate is None or gate_decision is None or not gate_decision.allowed:
            unmet = (
                ", ".join(gate_decision.reasons)
                if gate_decision is not None
                else "acceptance_service_unwired"
            )
            raise SystemExit(
                "Refusing to start: FEISHU_INTERACTIVE_ENABLED=true but the "
                f"{startup_tier.value} acceptance gate did not pass "
                f"(unmet: {unmet}). For FULL the 45-trading-day window (P0-6 "
                "§2 红线 5) must clear; for PILOT the 11-condition minimal set "
                "(P0-6-amendment-2026-05-25 §2.3) must all be met. This env "
                "var only selects which tier's gate to evaluate — it can never "
                "bypass the verdict. Fix the unmet conditions, then restart."
            )

        # Codex Cycle 5 P2 fix: starting the receiver without a
        # ReconciliationOrchestrator (FEISHU_DECISION_CHAT_ID unset)
        # would silently route reconciliation replies to the execution
        # parser — open tickets would never resolve and the overlay
        # would appear enabled while half-wired. Fail-closed on the
        # interactive path so a missing env var cannot bypass this
        # contract.
        if application.state.reconciliation_orchestrator is None:
            raise SystemExit(
                "Refusing to start: FEISHU_INTERACTIVE_ENABLED=true but "
                "FEISHU_DECISION_CHAT_ID is unset, so the "
                "ReconciliationOrchestrator could not be constructed. "
                "Set the env var to the open_chat_id of the decision "
                "chat (must differ from FEISHU_ALERT_CHAT_ID per "
                "P0-2-amendment-2026-05-16 §4 red line 7) and restart."
            )

        # Codex Cycle 9 P1 fix — run the mode-switch lifecycle BEFORE
        # starting the receiver. ``ModeRouter`` was initialised at
        # ``simulation_auto``; without ``switch_mode(FEISHU_INTERACTIVE,
        # ...)`` the MODE_SWITCH_RESET broker event + MockBroker reset
        # + audit lifecycle pair are all skipped, and any recovered
        # simulation positions stay live in the account that Feishu
        # execution / reconciliation reports will now mutate. CLAUDE.md
        # §2.1 locks this transition as an account lifecycle event.
        mode_router = application.state.mode_router
        if mode_router.current_mode != "feishu_interactive":
            await mode_router.switch_mode(
                to_mode="feishu_interactive",
                reason="acceptance_gate_passed_at_startup",
                initiated_by="lifespan",
                when=datetime.now(tz=SHANGHAI_TZ),
                feishu_tier=startup_tier,
            )

        # Gate passed: build + start the long-connection receiver with a
        # dispatcher that routes inbound messages to the matching
        # orchestrator. Reconciliation replies match a narrow regex set
        # first (per :func:`parse_reconciliation_reply`); anything that
        # does not parse as a reconciliation reply falls through to the
        # execution-report handler. Both orchestrators dedupe upstream
        # (broker_event_id / report_id) so a borderline message hitting
        # both branches is still idempotent.
        # Codex Cycle 1 P1 fix: previously the gate fell through with
        # ``application.state.feishu_event_receiver = None`` so the
        # process appeared to enable the overlay while no consumer
        # existed for execution reports / reconciliation replies.
        from backend.audit.models import (
            AuditActor,
            AuditEventType,
            AuditOutcome,
        )
        from backend.integrations.feishu.dedupe import RedisEventDedupe
        from backend.integrations.feishu.events import (
            FeishuEventReceiver,
            ReceivedMessage,
        )
        from backend.integrations.feishu.inbound_gate import (
            InboundGate,
            InboundVerdict,
        )
        from backend.services.secrets_validator import compute_fingerprint

        execution_orch = application.state.execution_report_orchestrator
        reconciliation_orch = application.state.reconciliation_orchestrator
        audit_store_ref = application.state.audit_store

        # Codex Cycle 8 P1 fix — chat-id gate (CLAUDE.md §2.6 + P0-2
        # amendment-2026-05-16 §4 红线 7 require alert and decision
        # chats to be strictly separated; without this filter a stray
        # message in the alert chat or a 1-on-1 DM matching a
        # reconciliation/execution regex would reach the appliers and
        # mutate the broker). The decision chat env was already
        # validated non-empty when the orchestrator was constructed;
        # the alert chat is also re-validated here so a misconfigured
        # alert==decision chat aborts startup before any inbound
        # message can mutate state.
        decision_chat_env = os.environ.get(
            "FEISHU_DECISION_CHAT_ID", ""
        ).strip()
        alert_chat_env = os.environ.get(
            "FEISHU_ALERT_CHAT_ID", ""
        ).strip()
        if not decision_chat_env:
            raise SystemExit(
                "Refusing to start: FEISHU_INTERACTIVE_ENABLED=true but "
                "FEISHU_DECISION_CHAT_ID is unset (caught in the receiver "
                "dispatcher gate). The reconciliation orchestrator was "
                "constructed above, but without a known decision chat "
                "the dispatcher cannot enforce P0-2-amendment-2026-05-16 "
                "§4 红线 7."
            )
        if alert_chat_env and alert_chat_env == decision_chat_env:
            raise SystemExit(
                "Refusing to start: FEISHU_ALERT_CHAT_ID equals "
                "FEISHU_DECISION_CHAT_ID. The alert and decision chats "
                "must be strictly separated per P0-2-amendment-2026-05-16 "
                "§4 红线 7 so alert text cannot be parsed as a "
                "reconciliation/execution reply and mutate the broker."
            )

        # P0-2-amendment-2026-05-27 — owner open_id allowlist. The
        # chat-id gate above only proves the message is on the decision
        # group; without a sender allowlist ANY decision-group member's
        # text that matches a report/reconciliation regex would mutate
        # the broker. ``from_env`` is fail-closed: an unset/empty
        # FEISHU_OWNER_OPEN_ID aborts startup here (same severity as the
        # decision-chat / alert!=decision gates) rather than shipping a
        # path that authorizes nobody silently.
        try:
            inbound_gate = InboundGate.from_env(os.environ)
        except ValueError as exc:
            raise SystemExit(
                "Refusing to start: FEISHU_INTERACTIVE_ENABLED=true but the "
                "inbound owner allowlist is unusable "
                f"({exc}). Set FEISHU_OWNER_OPEN_ID to the owner's open_id(s) "
                "(ou_..., comma-separated) per P0-2-amendment-2026-05-27 so a "
                "non-owner reply in the decision chat can never mutate the "
                "broker mirror."
            ) from exc

        async def _feishu_dispatch(message: ReceivedMessage) -> None:
            verdict = inbound_gate.classify(
                chat_id=message.chat_id, sender_id=message.sender_id
            )
            if verdict is InboundVerdict.DROP_WRONG_CHAT:
                log.warning(
                    "feishu_message_dropped_wrong_chat",
                    expected_decision_chat_fp=(
                        decision_chat_env[:6] + "***"
                        if decision_chat_env
                        else ""
                    ),
                    received_chat_fp=(
                        message.chat_id[:6] + "***"
                        if message.chat_id
                        else ""
                    ),
                )
                return
            if verdict is InboundVerdict.DROP_NOT_OWNER:
                # Decision chat, but a non-allowlisted sender — fail-closed:
                # never reaches the parser/applier/broker mirror. Audit with
                # a sender FINGERPRINT only (never the raw open_id / message
                # text — sender ids are user-controlled and would leak into
                # every audit row).
                log.warning(
                    "feishu_message_dropped_not_owner",
                    sender_fingerprint=compute_fingerprint(message.sender_id),
                )
                await audit_store_ref.write(
                    event_type=AuditEventType.FEISHU_MESSAGE_RECEIVED,
                    actor=AuditActor.FEISHU_USER,
                    resource_type="feishu_inbound_message",
                    resource_id=message.message_id,
                    payload={
                        "sender_fingerprint": compute_fingerprint(
                            message.sender_id
                        ),
                        "reason": "sender_not_in_owner_allowlist",
                    },
                    outcome=AuditOutcome.BLOCKED,
                    reason_namespace="inbound_sender_not_allowlisted",
                    timestamp=datetime.now(tz=SHANGHAI_TZ),
                )
                return
            if reconciliation_orch is not None:
                reply_outcome = await reconciliation_orch.handle_reply(
                    message.text
                )
                if reply_outcome.handled:
                    return
            if execution_orch is not None:
                await execution_orch.handle_feishu(message)

        dedupe = RedisEventDedupe(redis_pool)
        receiver = FeishuEventReceiver(
            app_id=os.environ.get("FEISHU_APP_ID", ""),
            app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
            verify_token=os.environ.get("FEISHU_VERIFY_TOKEN", ""),
            encrypt_key=os.environ.get("FEISHU_ENCRYPT_KEY", ""),
            dedupe=dedupe,
            handler=_feishu_dispatch,
        )
        await receiver.start()
        application.state.feishu_event_receiver = receiver
        log.info("feishu_event_receiver_wired")

    # Daily analysis orchestrator
    await _init_analysis_scheduler(application)

    # WebSocket Redis subscriber (bridges Redis pub/sub → WebSocket clients)
    import asyncio

    from backend.api.websocket import _subscribe_and_forward

    ws_subscriber_task = asyncio.create_task(_subscribe_and_forward(redis_pool))

    log.info("application_started")
    yield

    # -- Shutdown --
    ws_subscriber_task.cancel()
    try:
        await ws_subscriber_task
    except asyncio.CancelledError:
        pass

    if hasattr(application.state, "analysis_stream_hub"):
        await application.state.analysis_stream_hub.shutdown()

    if (
        hasattr(application.state, "feishu_event_receiver")
        and application.state.feishu_event_receiver is not None
    ):
        await application.state.feishu_event_receiver.stop()

    await _shutdown_data_layer(application)
    await router.close()
    await redis_pool.aclose()
    log.info("application_stopped")


app = FastAPI(
    title="QuantMind",
    description="AI-powered A-share quantitative trading system",
    version="0.1.0",
    lifespan=lifespan,
)


def get_llm_router(request: Request) -> LLMRouter:
    """FastAPI dependency to access the LLM router singleton."""
    return request.app.state.llm_router


app.include_router(market_router)
app.include_router(analysis_router)
app.include_router(simulation_router)
app.include_router(trading_router)
app.include_router(settings_router)
app.include_router(risk_router)
app.include_router(performance_router)
app.include_router(watchlist_router)
app.include_router(health_router)
app.include_router(monitoring_router)
app.include_router(system_status_router)
app.include_router(instruction_plans_router)
app.include_router(equity_points_router)
app.include_router(acceptance_router)
app.include_router(audit_router)
app.include_router(cost_router)
app.include_router(execution_reports_router)
app.include_router(reconciliation_router)
app.include_router(data_quality_router)
app.include_router(evolution_router)
app.include_router(feishu_router)
app.include_router(websocket_router)


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
