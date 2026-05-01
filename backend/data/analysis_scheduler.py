"""Daily stock analysis orchestrator."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.agents.graph import AnalysisRunError, run_analysis
from backend.agents.models import TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
)

if TYPE_CHECKING:
    import redis.asyncio

    from backend.agents.models import AnalysisServices
    from backend.data.database import MongoDBService
    from backend.data.watchlist import WatchlistService

log = structlog.get_logger(component="analysis_scheduler")

CHANNEL_ANALYSIS = "analysis:signals"

SHANGHAI = ZoneInfo("Asia/Shanghai")
CATCH_UP_CUTOFF = dt_time(hour=9, minute=45)


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
        # Serializes _run_and_persist so a manual API call cannot race
        # against the cron-driven daily loop and double-spend the daily
        # budget by both observing the same under-cap snapshot. Within
        # one process that's enough; cross-process races would require
        # a Redis lock and are out of scope while the eval-period
        # backend runs as a single instance.
        self._run_lock = asyncio.Lock()

    async def start(self) -> None:
        """Register cron job and run catch-up if today's run was missed.

        Catch-up trigger (all must hold):
          1. Today is a weekday (Mon-Fri); A-share trading calendar is
             not loaded here so holidays still trigger a run — the
             watchlist analysis itself is tolerant of empty market data.
          2. Current time is past 09:45 Asia/Shanghai.
          3. At least one active watchlist stock has no trading_signals
             row for today.

        By-stock granularity matters: a previous run may have succeeded
        for 3/5 stocks, and we must only re-run the 2 missing ones to
        stay under the daily cost budget.
        """
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

        try:
            missed = await self._compute_catch_up_targets()
        except Exception as exc:
            log.warning("catch_up_probe_failed", error=str(exc))
            missed = []
        if missed:
            log.info("catch_up_scheduling", missed=missed)
            asyncio.create_task(self._run_catch_up(missed))

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
                signal = await self._run_and_persist(code)
                if signal is not None:
                    signals.append(signal)
                    log.info(
                        "stock_analysis_complete",
                        code=code,
                        action=signal.action,
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
            return await self._run_and_persist(stock_code)
        except Exception as exc:
            log.error(
                "single_analysis_failed", code=stock_code, error=str(exc)
            )
            return None

    async def _run_and_persist(
        self, stock_code: str
    ) -> TradingSignal | None:
        """Run pipeline and persist both signal and full record.

        Returns the TradingSignal on success, None when run_analysis
        raises (caller logs). Record is saved even when signal persist
        later fails so the analysis trail is preserved.

        AnalysisRunError carries the failed AnalysisRecord; it is
        persisted before re-raising so /history shows the failure.

        Cost ceiling enforcement (P5A-T02): before kicking off a new
        run we check the daily LLM budget against ``assert_budget_allows``.
        On ``hard_breach`` we record a synthetic failed analysis (so
        ``/history`` reflects the skip) and return ``None`` instead of
        paying for another LLM call. The Redis-less / probe failure
        paths fall through to the normal pipeline so we never
        accidentally wedge runs on transient infra glitches.

        ``self._run_lock`` serializes concurrent calls so the cron loop
        and a manual API call cannot both observe the same under-cap
        snapshot and double-spend.
        """
        async with self._run_lock:
            return await self._run_and_persist_locked(stock_code)

    async def _run_and_persist_locked(
        self, stock_code: str
    ) -> TradingSignal | None:
        if self._redis is not None:
            try:
                state = await assert_budget_allows(
                    self._redis, agent_name="pipeline"
                )
            except DailyBudgetExceededError as exc:
                await self._persist_cost_skip(stock_code, exc)
                return None
            except Exception as probe_exc:
                # A Redis hiccup must NOT block analysis — log and proceed.
                log.warning(
                    "cost_guard_probe_failed",
                    code=stock_code,
                    error=str(probe_exc),
                )
            else:
                if state.status == "soft_breach":
                    # Phase 5B will degrade thinking; for now we just
                    # record the warning so operators can see it.
                    log.warning(
                        "cost_soft_breach_observed",
                        code=stock_code,
                        spent=state.spent_today,
                        soft_ceiling=state.soft_ceiling,
                    )
        try:
            result = await run_analysis(stock_code, self._services)
        except AnalysisRunError as exc:
            try:
                await self._mongodb.save_analysis_record(
                    exc.record.model_dump(mode="json")
                )
            except Exception as persist_exc:
                log.warning(
                    "save_failed_record_failed",
                    code=stock_code,
                    error=str(persist_exc),
                )
            raise

        if not isinstance(result, AnalysisRunResult):  # safety guard
            raise TypeError(
                f"run_analysis must return AnalysisRunResult, got {type(result)!r}"
            )

        signal = result.signal
        record = result.record

        signal_dict = signal.model_dump(mode="json")
        signal_dict["created_at"] = datetime.now(UTC).isoformat()
        signal_id = await self._mongodb.save_signal(signal_dict)

        record_with_signal = record.model_copy(update={"signal_id": signal_id})
        try:
            await self._mongodb.save_analysis_record(
                record_with_signal.model_dump(mode="json")
            )
        except AttributeError:
            # MongoDB service predates A1.4 — skip with warning.
            log.warning(
                "save_analysis_record_unavailable", code=stock_code
            )
        except Exception as exc:
            log.warning(
                "save_analysis_record_failed",
                code=stock_code,
                error=str(exc),
            )

        await self._publish_signal(signal_dict)
        return signal

    async def _persist_cost_skip(
        self, stock_code: str, exc: DailyBudgetExceededError
    ) -> None:
        """Record a synthetic failed analysis when the budget hard-caps us.

        We do not have a TradingSignal in this branch; only the record
        is written so ``/history`` reflects the skip with a structured
        ``error`` prefix that downstream tooling can grep for.
        """
        record = AnalysisRecord(
            run_id=str(uuid.uuid4()),
            stock_code=stock_code,
            stock_name=stock_code,
            trade_date=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
            status="failed",
            error=f"cost_ceiling_breached: {exc}",
        )
        try:
            await self._mongodb.save_analysis_record(
                record.model_dump(mode="json")
            )
        except Exception as persist_exc:
            log.warning(
                "save_cost_skip_record_failed",
                code=stock_code,
                error=str(persist_exc),
            )

    async def _publish_signal(self, signal_dict: dict[str, Any]) -> None:
        """Publish signal to Redis for WebSocket clients."""
        if self._redis is None:
            return
        try:
            payload = json.dumps(signal_dict, ensure_ascii=False)
            await self._redis.publish(CHANNEL_ANALYSIS, payload)
        except Exception as exc:
            log.warning("signal_publish_failed", error=str(exc))

    async def _compute_catch_up_targets(self) -> list[str]:
        """Return watchlist stock codes that still need today's analysis.

        Returns empty list when the catch-up preconditions aren't met
        (too early, weekend, empty watchlist, all stocks covered).
        """
        now_sh = datetime.now(tz=SHANGHAI)
        if now_sh.weekday() > 4:  # 5=Sat, 6=Sun
            return []
        if now_sh.time() < CATCH_UP_CUTOFF:
            return []

        stocks = await self._watchlist.list_stocks()
        if not stocks:
            return []

        trade_date = now_sh.strftime("%Y-%m-%d")
        codes = [stock["stock_code"] for stock in stocks]
        try:
            signals = await self._mongodb.query_signals_for_trade_date(
                trade_date=trade_date,
                stock_codes=codes,
            )
        except Exception as exc:
            log.warning(
                "catch_up_query_failed", trade_date=trade_date, error=str(exc)
            )
            return []

        covered_codes = {
            signal["stock_code"]
            for signal in signals
            if signal.get("trade_date") == trade_date
        }
        return [code for code in codes if code not in covered_codes]

    async def _run_catch_up(self, stock_codes: list[str]) -> None:
        """Sequentially re-run analysis for the given stock codes."""
        log.info("catch_up_started", stock_codes=stock_codes)
        for i, code in enumerate(stock_codes):
            try:
                await self._run_and_persist(code)
            except Exception as exc:
                log.error(
                    "catch_up_stock_failed", code=code, error=str(exc)
                )
            if i < len(stock_codes) - 1:
                await asyncio.sleep(10)
        log.info("catch_up_complete", count=len(stock_codes))
