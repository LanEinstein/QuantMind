"""Daily stock analysis orchestrator."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.agents.graph import run_analysis
from backend.agents.models import TradingSignal

if TYPE_CHECKING:
    import redis.asyncio

    from backend.agents.models import AnalysisServices
    from backend.data.database import MongoDBService
    from backend.data.watchlist import WatchlistService

log = structlog.get_logger(component="analysis_scheduler")

CHANNEL_ANALYSIS = "analysis:signals"


class AnalysisScheduler:
    """Daily stock analysis orchestrator.

    Runs at 09:45 CST on trading days. For each active watchlist stock:
    1. Call run_analysis() (9-agent LangGraph pipeline)
    2. Persist signal to MongoDB
    3. Publish signal to Redis for real-time frontend updates
    Rate-limits 10s between stocks to avoid LLM API throttling.
    """

    def __init__(
        self,
        watchlist: WatchlistService,
        services: AnalysisServices,
        mongodb: MongoDBService,
        redis_client: redis.asyncio.Redis | None,
    ) -> None:
        self._watchlist = watchlist
        self._services = services
        self._mongodb = mongodb
        self._redis = redis_client
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        """Register cron job: 09:45 Asia/Shanghai, Mon-Fri."""
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self.run_daily_analysis,
            "cron",
            hour=9,
            minute=45,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
            id="daily_analysis",
            name="Daily watchlist analysis",
        )
        self._scheduler.start()
        log.info("analysis_scheduler_started", schedule="09:45 CST Mon-Fri")

    async def stop(self) -> None:
        """Shutdown scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("analysis_scheduler_stopped")
        self._scheduler = None

    async def run_daily_analysis(self) -> list[TradingSignal]:
        """Execute analysis for all watchlist stocks."""
        stocks = await self._watchlist.list_stocks()
        if not stocks:
            log.info("daily_analysis_skipped", reason="empty_watchlist")
            return []

        signals: list[TradingSignal] = []
        log.info("daily_analysis_started", stock_count=len(stocks))

        for i, stock in enumerate(stocks):
            code = stock["stock_code"]
            try:
                signal = await run_analysis(code, self._services)
                signal_dict = signal.model_dump(mode="json")
                signal_dict["created_at"] = datetime.now(UTC).isoformat()
                await self._mongodb.save_signal(signal_dict)
                await self._publish_signal(signal_dict)
                signals.append(signal)
                log.info(
                    "stock_analysis_complete", code=code, action=signal.action
                )
            except Exception as exc:
                log.error("stock_analysis_failed", code=code, error=str(exc))

            # Rate limit between stocks (skip after last)
            if i < len(stocks) - 1:
                await asyncio.sleep(10)

        log.info(
            "daily_analysis_complete", total=len(stocks), success=len(signals)
        )
        return signals

    async def run_single_analysis(
        self, stock_code: str
    ) -> TradingSignal | None:
        """Analyze a single stock on demand."""
        try:
            signal = await run_analysis(stock_code, self._services)
            signal_dict = signal.model_dump(mode="json")
            signal_dict["created_at"] = datetime.now(UTC).isoformat()
            await self._mongodb.save_signal(signal_dict)
            await self._publish_signal(signal_dict)
            return signal
        except Exception as exc:
            log.error(
                "single_analysis_failed", code=stock_code, error=str(exc)
            )
            return None

    async def _publish_signal(self, signal_dict: dict[str, Any]) -> None:
        """Publish signal to Redis for WebSocket clients."""
        if self._redis is None:
            return
        try:
            payload = json.dumps(signal_dict, ensure_ascii=False)
            await self._redis.publish(CHANNEL_ANALYSIS, payload)
        except Exception as exc:
            log.warning("signal_publish_failed", error=str(exc))
