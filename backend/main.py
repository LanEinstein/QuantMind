"""QuantMind FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from backend.api.analysis import router as analysis_router
from backend.api.health import router as health_router
from backend.api.market import router as market_router
from backend.api.monitoring import router as monitoring_router
from backend.api.performance import router as performance_router
from backend.api.risk import router as risk_router
from backend.api.settings import router as settings_router
from backend.api.simulation import router as simulation_router
from backend.api.trading import router as trading_router
from backend.api.watchlist import router as watchlist_router
from backend.api.websocket import router as websocket_router
from backend.llm.router import LLMRouter
from backend.services.config_service import ConfigService

log = structlog.get_logger()


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

    # MongoDB
    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    mongo_client = motor.AsyncIOMotorClient(mongo_uri)
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

    # Scheduler
    scheduler = DataScheduler(
        market_data=market_data,
        news_crawler=news_crawler,
        mongodb=mongodb_service,
        redis_client=redis_pool,  # type: ignore[arg-type]
        market_interval_seconds=config.market_data.refresh_interval_seconds,
        news_interval_seconds=config.news.refresh_interval_seconds,
    )
    await scheduler.start()

    # Watchlist service
    from backend.data.watchlist import WatchlistService

    watchlist_service = WatchlistService(db)
    try:
        await watchlist_service.initialize()
    except Exception as exc:
        log.warning("watchlist_init_failed", error=str(exc))

    # Store on app state
    application.state.mongo_client = mongo_client
    application.state.mongodb = mongodb_service
    application.state.market_data = market_data
    application.state.history_data = history_data
    application.state.news_crawler = news_crawler
    application.state.scheduler = scheduler
    application.state.watchlist = watchlist_service

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


async def _init_analysis_scheduler(application: FastAPI) -> None:
    """Initialize the daily analysis orchestrator.

    Phase 5B-T02: when ``config/watchlist_policy.yaml`` is present the
    scheduler runs in Fast/Slow mode (two cron jobs). When the file is
    missing or fails to parse we log a warning and fall back to the
    legacy single-cron mode so a typo in the YAML can't bring the
    scheduler down.
    """
    from backend.agents.models import AnalysisServices, PipelineConfig
    from backend.data.analysis_scheduler import AnalysisScheduler
    from backend.services.watchlist_policy import (
        WatchlistPolicyError,
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

    services = AnalysisServices(
        llm_router=application.state.llm_router,
        market_data=application.state.market_data,
        history_data=application.state.history_data,
        news_crawler=application.state.news_crawler,
        mongodb=application.state.mongodb,
        pipeline_config=PipelineConfig(),
    )

    policy_path = os.environ.get(
        "QUANTMIND_WATCHLIST_POLICY_PATH", "config/watchlist_policy.yaml"
    )
    policy = None
    if os.path.exists(policy_path):
        try:
            policy = load_policy(policy_path)
        except (WatchlistPolicyError, OSError) as exc:
            log.warning(
                "watchlist_policy_load_failed",
                path=policy_path,
                error=str(exc),
            )
    else:
        log.info("watchlist_policy_missing", path=policy_path)
    analysis_scheduler = AnalysisScheduler(
        watchlist=application.state.watchlist,
        services=services,
        mongodb=application.state.mongodb,
        redis_client=getattr(application.state, "redis", None),
        policy=policy,
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
    """Shut down the data layer services."""
    if hasattr(application.state, "analysis_scheduler"):
        await application.state.analysis_scheduler.stop()
    if hasattr(application.state, "scheduler"):
        await application.state.scheduler.stop()
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

    # Resolve run mode (P0-1). simulation_auto is always-on; FEISHU_INTERACTIVE_ENABLED
    # toggles the human-in-loop overlay. Replaces the legacy AUTHORIZATION_MODE x
    # QUANTMIND_PHASE matrix; Feishu credential fail-fast lives in the secrets
    # validator (P1-6 / H-001), not here.
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

    # Live agent-debate SSE hub (A2). Lives in-process; jobs lost on restart
    # are acceptable because the full AnalysisRecord is persisted to MongoDB.
    from backend.services.analysis_stream import AnalysisStreamHub

    application.state.analysis_stream_hub = AnalysisStreamHub()

    # Webhook alerter (Session C/D). Falls back to log-only when
    # ALERT_WEBHOOK_URL is unset, so wiring this up is safe.
    from backend.monitoring.alerter import Alerter

    application.state.alerter = Alerter()

    await _init_data_layer(application, redis_pool)

    # Trading subsystem
    await _init_trading_layer(application)

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
app.include_router(websocket_router)


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
