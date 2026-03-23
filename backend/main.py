"""QuantMind FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from backend.api.analysis import router as analysis_router
from backend.api.market import router as market_router
from backend.llm.router import LLMRouter

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

    # Store on app state
    application.state.mongo_client = mongo_client
    application.state.mongodb = mongodb_service
    application.state.market_data = market_data
    application.state.history_data = history_data
    application.state.news_crawler = news_crawler
    application.state.scheduler = scheduler

    log.info("data_layer_initialized")


async def _shutdown_data_layer(application: FastAPI) -> None:
    """Shut down the data layer services."""
    if hasattr(application.state, "scheduler"):
        await application.state.scheduler.stop()
    if hasattr(application.state, "mongo_client"):
        application.state.mongo_client.close()
    log.info("data_layer_stopped")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    import redis.asyncio as aioredis

    # -- Startup --
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    redis_pool = aioredis.from_url(redis_url, decode_responses=True)

    router = LLMRouter(config_path="config/agent_models.yaml")
    await router.initialize(redis_client=redis_pool)

    application.state.redis = redis_pool
    application.state.llm_router = router

    await _init_data_layer(application, redis_pool)

    log.info("application_started")
    yield

    # -- Shutdown --
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


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
