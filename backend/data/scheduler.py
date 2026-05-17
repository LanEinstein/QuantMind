"""Background data scheduler using APScheduler v3.

C-003 / P0-8 §1.1: the 30s market-data job now snapshots the full
watchlist (13 codes under P0-9) as :class:`WatchlistMarketSnapshot` rows
*in addition to* the legacy benchmark-index pull, so
``DataQualityProvider`` (C-004) has a per-stock per-tick history to
compute staleness / divergence / missing-rate against. Redis caches the
latest quote per code with TTL=120s (P1-2.B §1.2 first fallback tier)
so the MockBroker MTM path and at-fill price-limit recheck have a
fast-path read before falling back to MongoDB.

The scheduler keeps fail-open semantics for transient infra glitches
(MongoDB / Redis / vendor brown-out): each job swallows exceptions
and logs them rather than crashing the AsyncIOScheduler loop, since
the 30s cadence will retry on the next tick.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.data.publisher import publish_market_update, publish_news
from backend.llm.cost_tracker import flush_to_mongodb
from backend.utils.trading_hours import is_trading_hours

BENCHMARK_INDEX_CODE = "000300"
BENCHMARK_BACKFILL_DAYS = 5

# Redis quote-cache TTL — P1-2.B §1.2 first fallback tier (≤60s freshness
# requirement on read, doubled for headroom against scheduler missfires).
QUOTE_CACHE_TTL_SECONDS = 120
QUOTE_CACHE_KEY_PREFIX = "quote:"

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.database import MongoDBService
    from backend.data.market_data import MarketDataService
    from backend.data.news_crawler import NewsCrawlerService
    from backend.data.watchlist import WatchlistService
    from backend.mirofish.output_writer import MiroFishEvidenceWriter

log = structlog.get_logger(component="scheduler")


class DataScheduler:
    """Background scheduler for periodic market data and news collection.

    During trading hours every ``market_interval_seconds`` (default 30s)
    the scheduler:

    1. Fetches a fresh index batch (benchmark + others) and persists to
       the legacy ``market_realtime`` collection.
    2. Reads the active watchlist from :class:`WatchlistService` and
       fetches a per-stock snapshot; persists to
       ``watchlist_market_snapshots`` (C-003) and caches each quote in
       Redis as ``quote:{code}`` with TTL=120s.

    News runs on its own slower cadence (default 5min) regardless of
    trading hours so post-close / overnight events still flow into
    Mongo. Cost-flush is a once-daily cron at 23:00 Asia/Shanghai.
    """

    def __init__(
        self,
        market_data: MarketDataService,
        news_crawler: NewsCrawlerService,
        mongodb: MongoDBService,
        redis_client: redis.asyncio.Redis | None,
        watchlist: WatchlistService | None = None,
        market_interval_seconds: int = 30,
        news_interval_seconds: int = 300,
        mirofish_writer: MiroFishEvidenceWriter | None = None,
    ) -> None:
        self._market_data = market_data
        self._news_crawler = news_crawler
        self._mongodb = mongodb
        self._redis = redis_client
        self._watchlist = watchlist
        self._market_interval = market_interval_seconds
        self._news_interval = news_interval_seconds
        # C-006: 17:00 mon-fri Asia/Shanghai cron uses this writer to
        # land the EOD MiroFish review into evidence_collection. ``None``
        # is permitted so dev environments without the MiroFish stack
        # still boot — the EOD job becomes a no-op in that case.
        self._mirofish_writer = mirofish_writer
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

        # C-005: CCTV ingestion is locked to three Asia/Shanghai
        # checkpoints — 09:00 (pre-open), 15:30 (post-close), 20:00
        # (after 新闻联播's 19:30 broadcast which is the primary daily
        # update window). P0-8 §2 redline 13 / §1.3.1 lock this cadence;
        # using a free-running 6h ``interval`` lets restarts shift the
        # window away from the broadcast (codex cycle 4 P3). APScheduler
        # cron with ``hour=H minute=M`` strings cross-product, so we
        # register three discrete jobs to land at *exactly* the locked
        # timestamps.
        for hour, minute, suffix in (
            (9, 0, "0900"),
            (15, 30, "1530"),
            (20, 0, "2000"),
        ):
            self._scheduler.add_job(
                self._run_cctv_news_job,
                "cron",
                hour=hour,
                minute=minute,
                timezone="Asia/Shanghai",
                id=f"cctv_news_job_{suffix}",
                name=f"CCTV political-domain news ({hour:02d}:{minute:02d} SH)",
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

        # C-006: 17:00 mon-fri Asia/Shanghai — MiroFish EOD review writes
        # one ``MIROFISH-EOD-{YYYYMMDD}`` row into evidence_collection.
        # Wired conditionally because dev envs may not have the writer.
        if self._mirofish_writer is not None:
            self._scheduler.add_job(
                self._run_mirofish_eod_job,
                "cron",
                hour=17,
                minute=0,
                day_of_week="mon-fri",
                timezone="Asia/Shanghai",
                id="mirofish_eod_review_job",
                name="MiroFish EOD review (17:00 mon-fri)",
            )

        self._scheduler.start()
        self._log.info(
            "scheduler_started",
            market_interval=self._market_interval,
            news_interval=self._news_interval,
            watchlist_wired=self._watchlist is not None,
        )

    async def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._log.info("scheduler_stopped")
        self._scheduler = None

    async def _run_market_job(self) -> None:
        """30s tick: index + full watchlist snapshot during trading hours."""
        if not is_trading_hours():
            return

        # Single snapshot timestamp so every row in this tick groups
        # together for downstream missing-rate / divergence checks.
        snapshot_at = datetime.now(tz=UTC)

        await self._collect_index_snapshot()
        await self._collect_watchlist_snapshot(snapshot_at)

    async def _collect_index_snapshot(self) -> None:
        """Fetch the benchmark index batch and persist + publish."""
        try:
            quotes = await self._market_data.get_index_realtime()
        except Exception as exc:
            self._log.warning("index_snapshot_failed", error=str(exc))
            return
        if not quotes:
            return
        try:
            await self._mongodb.save_market_snapshot(quotes)
        except Exception as exc:
            self._log.warning("index_snapshot_persist_failed", error=str(exc))
        try:
            await publish_market_update(self._redis, quotes)
        except Exception as exc:
            self._log.warning("index_snapshot_publish_failed", error=str(exc))

    async def _collect_watchlist_snapshot(self, snapshot_at: datetime) -> None:
        """Fetch per-stock watchlist snapshot, persist, cache to Redis."""
        codes = await self._active_watchlist_codes()
        if not codes:
            return

        try:
            snapshots = await self._market_data.get_watchlist_snapshot(
                codes, snapshot_at
            )
        except Exception as exc:
            self._log.warning("watchlist_snapshot_failed", error=str(exc))
            return

        if not snapshots:
            self._log.warning(
                "watchlist_snapshot_empty_frame",
                expected=len(codes),
            )
            return

        try:
            persisted = await self._mongodb.save_watchlist_snapshot(snapshots)
        except Exception as exc:
            self._log.warning(
                "watchlist_snapshot_persist_failed", error=str(exc)
            )
            persisted = 0

        await self._cache_quotes_to_redis(snapshots)

        self._log.debug(
            "watchlist_snapshot_complete",
            expected=len(codes),
            persisted=persisted,
            observed=len(snapshots),
        )

    async def _active_watchlist_codes(self) -> list[str]:
        """Return the list of active watchlist codes, or [] if unwired."""
        if self._watchlist is None:
            return []
        try:
            rows = await self._watchlist.list_stocks()
        except Exception as exc:
            self._log.warning("watchlist_list_failed", error=str(exc))
            return []
        codes: list[str] = []
        for row in rows:
            code = row.get("stock_code") if isinstance(row, dict) else None
            if isinstance(code, str):
                codes.append(code)
        return codes

    async def _cache_quotes_to_redis(
        self, snapshots: list[object]
    ) -> None:
        """Cache each snapshot under ``quote:{code}`` with TTL=120s.

        ``snapshots`` is typed as ``list[object]`` here to keep the
        scheduler's TYPE_CHECKING-only import of ``WatchlistMarketSnapshot``
        purely cosmetic — the body only relies on ``.code`` and
        ``.model_dump(mode='json')`` which the model contract guarantees.
        Cache misses (no Redis client) are a silent no-op so the dev
        env can run without Redis.
        """
        if self._redis is None:
            return
        try:
            for snap in snapshots:
                key = f"{QUOTE_CACHE_KEY_PREFIX}{snap.code}"  # type: ignore[attr-defined]
                payload = json.dumps(
                    snap.model_dump(mode="json"),  # type: ignore[attr-defined]
                    ensure_ascii=False,
                )
                await self._redis.set(
                    key, payload, ex=QUOTE_CACHE_TTL_SECONDS
                )
        except Exception as exc:
            self._log.warning("quote_cache_failed", error=str(exc))

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

    async def _run_cctv_news_job(self) -> None:
        """C-005 political-domain ingestion at the upstream's 6h cadence.

        CCTV refreshes every ~6 hours, so the regular 5-min news_job
        opts out (``include_cctv=False``) and this dedicated cron is
        responsible for the political domain.
        """
        try:
            articles = await self._news_crawler.fetch_cctv()
            if articles:
                await self._mongodb.save_news(articles)
                await publish_news(self._redis, articles)
                self._log.info("cctv_news_job_complete", count=len(articles))
        except Exception as exc:
            self._log.warning("cctv_news_job_failed", error=str(exc))

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

    async def _run_mirofish_eod_job(self) -> None:
        """C-006 EOD review cron: write MIROFISH-EOD-{YYYYMMDD} row.

        Empty event roll-up is fine — the row itself is the audit trail
        that the cron fired. The writer enforces the locked ``MIROFISH-``
        prefix; uncapped EOD path means consecutive 17:00 reruns would
        collide on the unique ``evidence_id`` index, which is the
        intended fail-closed branch (one EOD row per trade_date).
        """
        if self._mirofish_writer is None:
            return
        # Lazy import to keep the runtime-level dependency thin —
        # scheduler.py stays parseable without backend.mirofish at all
        # in deployments that disable MiroFish.
        from backend.mirofish.output_writer import build_eod_evidence

        trade_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        evidence = build_eod_evidence(events=(), trade_date=trade_date)
        try:
            ok = await self._mirofish_writer.write(evidence)
            self._log.info(
                "mirofish_eod_review_complete",
                trade_date=trade_date,
                inserted=ok,
            )
        except Exception as exc:
            self._log.warning(
                "mirofish_eod_review_failed",
                trade_date=trade_date,
                error=str(exc),
            )
