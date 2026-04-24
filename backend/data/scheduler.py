"""Background data scheduler using APScheduler v3."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.data.publisher import publish_market_update, publish_news
from backend.data.trading_hours import is_trading_hours
from backend.llm.cost_tracker import flush_to_mongodb

BENCHMARK_INDEX_CODE = "000300"
BENCHMARK_BACKFILL_DAYS = 5

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.database import MongoDBService
    from backend.data.market_data import MarketDataService
    from backend.data.news_crawler import NewsCrawlerService

log = structlog.get_logger(component="scheduler")


class DataScheduler:
    """Background scheduler for periodic market data and news collection.

    Market data is fetched every N seconds during trading hours.
    News is fetched every M seconds around the clock.
    """

    def __init__(
        self,
        market_data: MarketDataService,
        news_crawler: NewsCrawlerService,
        mongodb: MongoDBService,
        redis_client: redis.asyncio.Redis | None,
        market_interval_seconds: int = 30,
        news_interval_seconds: int = 300,
    ) -> None:
        self._market_data = market_data
        self._news_crawler = news_crawler
        self._mongodb = mongodb
        self._redis = redis_client
        self._market_interval = market_interval_seconds
        self._news_interval = news_interval_seconds
        self._scheduler: AsyncIOScheduler | None = None
        self._log = log

    async def start(self) -> None:
        """Start the background scheduler with market and news jobs."""
        self._scheduler = AsyncIOScheduler()

        self._scheduler.add_job(
            self._run_market_job,
            "interval",
            seconds=self._market_interval,
            id="market_data_job",
            name="Market data collection",
        )

        self._scheduler.add_job(
            self._run_news_job,
            "interval",
            seconds=self._news_interval,
            id="news_job",
            name="News collection",
        )

        self._scheduler.add_job(
            self._run_index_job,
            "cron",
            hour=15,
            minute=30,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
            id="index_price_job",
            name="CSI300 daily close collection",
        )

        self._scheduler.add_job(
            self._run_cost_flush_job,
            "cron",
            hour=23,
            minute=0,
            timezone="Asia/Shanghai",
            id="cost_flush_job",
            name="LLM cost Redis->MongoDB flush",
        )

        self._scheduler.start()
        self._log.info(
            "scheduler_started",
            market_interval=self._market_interval,
            news_interval=self._news_interval,
        )

    async def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._log.info("scheduler_stopped")
        self._scheduler = None

    async def _run_market_job(self) -> None:
        """Fetch market data if within trading hours, save and publish."""
        if not is_trading_hours():
            return

        try:
            quotes = await self._market_data.get_index_realtime()
            if quotes:
                await self._mongodb.save_market_snapshot(quotes)
                await publish_market_update(self._redis, quotes)
                self._log.debug("market_job_complete", count=len(quotes))
        except Exception as exc:
            self._log.warning("market_job_failed", error=str(exc))

    async def _run_news_job(self) -> None:
        """Fetch latest news, save to MongoDB, publish to Redis."""
        try:
            articles = await self._news_crawler.fetch_latest_news()
            if articles:
                await self._mongodb.save_news(articles)
                await publish_news(self._redis, articles)
                self._log.debug("news_job_complete", count=len(articles))
        except Exception as exc:
            self._log.warning("news_job_failed", error=str(exc))

    async def _run_index_job(self) -> None:
        """Fetch CSI300 recent daily closes and upsert to MongoDB."""
        try:
            df = await self._market_data.get_index_history(
                BENCHMARK_INDEX_CODE, days=BENCHMARK_BACKFILL_DAYS
            )
            if df is None or df.empty:
                return
            prices: list[dict[str, object]] = [
                {str(k): v for k, v in row.items()}
                for row in df.to_dict(orient="records")
            ]
            count = await self._mongodb.save_index_prices(
                BENCHMARK_INDEX_CODE, prices
            )
            self._log.info(
                "index_job_complete",
                code=BENCHMARK_INDEX_CODE,
                fetched=len(prices),
                persisted=count,
            )
        except Exception as exc:
            self._log.warning("index_job_failed", error=str(exc))

    async def _run_cost_flush_job(self) -> None:
        """Flush today's LLM cost entries from Redis to MongoDB."""
        if self._redis is None:
            self._log.debug("cost_flush_skipped", reason="no_redis")
            return
        try:
            count = await flush_to_mongodb(
                self._redis, self._mongodb, days=1
            )
            self._log.info("cost_flush_complete", entries=count)
        except Exception as exc:
            self._log.warning("cost_flush_failed", error=str(exc))
