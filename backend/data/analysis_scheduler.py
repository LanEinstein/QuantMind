"""Daily stock analysis orchestrator.

Phase 5B-T02 added a Fast/Slow split: when a :class:`WatchlistPolicy`
is supplied at construction, ``start()`` registers two cron jobs (one
per category) and each per-stock run rebuilds the agent services with
that category's :class:`PipelineConfig` (debate rounds + timeout). When
no policy is supplied the scheduler falls back to the legacy single
09:45 CST cron with the base config — that path is what every test in
``tests/test_analysis_scheduler*.py`` predating T02 exercises, so the
default remains backwards-compatible.

SLA caveats (Codex R4 perf review):

* The bucket ``pipeline_timeout_seconds`` (480s fast / 900s slow) is
  applied via ``asyncio.wait_for`` around ``run_analysis`` only. It
  does NOT include lock-wait, watchlist scan, the 10s inter-stock
  rate-limit, or Mongo/Redis persistence. Operators measuring p95
  end-to-end SLA should track ``category_analysis_complete`` log
  duration and tighten the per-stock timeout if there is consistent
  head-room.
* ``self._run_lock`` is process-wide on purpose: it serialises the
  budget probe + LLM call so a parallel manual trigger cannot
  double-spend the daily ceiling. The trade-off is that fast and
  slow buckets share the lane — if the YAML schedules them at the
  same minute (the default 09:00 overlap), the first fast tick can
  wait up to 900s for an in-flight slow stock to finish before its
  own 480s budget starts. Phase 5C should consider per-bucket locks
  paired with a Redis-backed budget reservation to keep both fairness
  and the cost ceiling.
* ``QUANTMIND_DAILY_BUDGET`` (cost_guard) — not the bucket timeout —
  is what enforces the ¥1.20 daily ceiling. The timeout protects p95
  latency, not spend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.agents.graph import AnalysisRunError, run_analysis
from backend.agents.models import PipelineConfig, TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
)
from backend.services.watchlist_policy import (
    Category,
    WatchlistPolicy,
    assign_category,
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
        policy: WatchlistPolicy | None = None,
    ) -> None:
        self._watchlist = watchlist
        self._services = services
        self._mongodb = mongodb
        self._redis = redis_client
        self._policy = policy
        self._scheduler: AsyncIOScheduler | None = None
        # Serializes _run_and_persist so a manual API call cannot race
        # against the cron-driven daily loop and double-spend the daily
        # budget by both observing the same under-cap snapshot. Within
        # one process that's enough; cross-process races would require
        # a Redis lock and are out of scope while the eval-period
        # backend runs as a single instance.
        self._run_lock = asyncio.Lock()

    @property
    def policy(self) -> WatchlistPolicy | None:
        """Current Fast/Slow watchlist policy (None ⇒ legacy mode)."""
        return self._policy

    def update_policy(self, policy: WatchlistPolicy) -> None:
        """Swap the in-memory policy.

        Cron strings on the running scheduler are NOT rewritten — only
        per-code overrides take effect immediately because each cron tick
        re-reads ``self._policy`` to partition the live watchlist. Cron
        cadence changes still require a process restart; that is an
        intentional simplification while the eval-period scheduler runs
        as a single instance.

        Single-process assumption: this swap is not synchronised across
        workers. Phase 5B targets ``WEB_CONCURRENCY=1`` so the API +
        cron share one process and one ``app.state``. Multi-worker
        deployment would need a Redis pub/sub broadcast (or leader
        election) to keep all schedulers consistent — out of scope here.
        """
        self._policy = policy

    async def start(self) -> None:
        """Register cron job(s) and run catch-up if today's run was missed.

        Two scheduling modes:

        * **Legacy** (``policy is None``): one job at 09:45 CST Mon-Fri
          calling :meth:`run_daily_analysis` over the full watchlist.
        * **Fast/Slow** (``policy`` set): two jobs from the policy's
          ``fast.cron`` and ``slow.cron``, each calling
          :meth:`run_category_analysis` with its category. The watchlist
          is partitioned at job time, so per-code overrides applied via
          the API take effect on the next tick without restart.

        Catch-up trigger (all must hold):
          1. Today is a weekday (Mon-Fri); A-share trading calendar is
             not loaded here so holidays still trigger a run — the
             watchlist analysis itself is tolerant of empty market data.
          2. Current time is past 09:45 Asia/Shanghai.
          3. At least one active watchlist stock has no trading_signals
             row for today.

        By-stock granularity matters: a previous run may have succeeded
        for 3/5 stocks, and we must only re-run the 2 missing ones to
        stay under the daily cost budget. The catch-up itself is
        category-aware when a policy is loaded.
        """
        self._scheduler = AsyncIOScheduler()
        if self._policy is None:
            self._register_legacy_cron()
        else:
            try:
                self._add_category_cron("fast", self._policy.fast.cron)
                self._add_category_cron("slow", self._policy.slow.cron)
            except ValueError as exc:
                # Malformed cron in either bucket — drop both jobs and
                # fall back to the legacy single-cron mode so the rest
                # of the eval loop keeps running. Operators see the
                # warning in logs and can fix the YAML without an
                # outage.
                log.warning(
                    "watchlist_policy_cron_invalid",
                    fast_cron=self._policy.fast.cron,
                    slow_cron=self._policy.slow.cron,
                    error=str(exc),
                )
                for job_id in ("fast_analysis", "slow_analysis"):
                    if self._scheduler.get_job(job_id) is not None:
                        self._scheduler.remove_job(job_id)
                self._policy = None
                self._register_legacy_cron()
            else:
                log.info(
                    "analysis_scheduler_started",
                    mode="fast_slow",
                    fast_cron=self._policy.fast.cron,
                    slow_cron=self._policy.slow.cron,
                )
        self._scheduler.start()

        try:
            missed = await self._compute_catch_up_targets()
        except Exception as exc:
            log.warning("catch_up_probe_failed", error=str(exc))
            missed = []
        if missed:
            log.info("catch_up_scheduling", missed=missed)
            asyncio.create_task(self._run_catch_up(missed))

    def _add_category_cron(self, category: Category, cron_expr: str) -> None:
        """Register one cron job per category, parsed from the YAML string.

        Raises ``ValueError`` when ``cron_expr`` is malformed — the
        caller (:meth:`start`) catches this and falls back to legacy
        single-cron mode so a typo cannot bring the scheduler down.
        """
        if self._scheduler is None:  # defensive — start() owns scheduler
            return
        trigger = CronTrigger.from_crontab(cron_expr, timezone="Asia/Shanghai")
        self._scheduler.add_job(
            self.run_category_analysis,
            trigger=trigger,
            args=[category],
            id=f"{category}_analysis",
            name=f"{category.capitalize()} watchlist analysis",
        )

    def _register_legacy_cron(self) -> None:
        """Register the original 09:45 CST single-cron job."""
        if self._scheduler is None:
            return
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
        log.info(
            "analysis_scheduler_started",
            mode="legacy_single_cron",
            schedule="09:45 CST Mon-Fri",
        )

    async def stop(self) -> None:
        """Shutdown scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("analysis_scheduler_stopped")
        self._scheduler = None

    async def run_daily_analysis(self) -> list[TradingSignal]:
        """Execute analysis for every active watchlist stock.

        Manual entry point (``POST /api/watchlist/analyze-now``) and the
        legacy 09:45 cron. With a policy loaded, each stock is dispatched
        through :meth:`_run_and_persist` with its assigned category so
        the per-bucket pipeline knobs still apply even on a manual sweep.
        """
        stocks = await self._watchlist.list_stocks()
        if not stocks:
            log.info("daily_analysis_skipped", reason="empty_watchlist")
            return []

        codes = [stock["stock_code"] for stock in stocks]
        log.info("daily_analysis_started", stock_count=len(codes))
        signals = await self._run_codes(codes, category=None)
        log.info(
            "daily_analysis_complete", total=len(codes), success=len(signals)
        )
        return signals

    async def run_category_analysis(
        self, category: Category
    ) -> list[TradingSignal]:
        """Run the pipeline only for stocks assigned to ``category``.

        Cron entry point when a :class:`WatchlistPolicy` is loaded —
        ``fast`` ticks 4x/day for short-horizon names with the fast
        bucket's :class:`PipelineConfig`; ``slow`` ticks once/day for
        long-horizon names with the slow bucket's deeper config.

        We snapshot ``self._policy`` once at the start: a concurrent
        :meth:`update_policy` call (e.g. from the API endpoint) must
        not change the partition mid-run, otherwise different stocks
        in the same tick would see different bucket assignments.
        """
        policy = self._policy
        if policy is None:
            log.warning(
                "category_analysis_no_policy", category=category
            )
            return []

        stocks = await self._watchlist.list_stocks()
        all_codes = [stock["stock_code"] for stock in stocks]
        matched = [
            code
            for code in all_codes
            if assign_category(code, policy) == category
        ]
        bucket = policy.bucket_for(category)
        if not matched:
            log.info(
                "category_analysis_skipped",
                category=category,
                reason="no_matched_codes",
                total_watchlist=len(all_codes),
                policy_version=policy.policy_version,
            )
            return []

        log.info(
            "category_analysis_started",
            category=category,
            matched_codes=matched,
            stock_count=len(matched),
            total_watchlist=len(all_codes),
            policy_version=policy.policy_version,
            cron=bucket.cron,
            timeout_seconds=bucket.pipeline_timeout_seconds,
            max_debate_rounds=bucket.max_debate_rounds,
        )
        signals = await self._run_codes(
            matched, category=category, policy=policy
        )
        log.info(
            "category_analysis_complete",
            category=category,
            total=len(matched),
            success=len(signals),
            failed=len(matched) - len(signals),
            policy_version=policy.policy_version,
        )
        return signals

    async def _run_codes(
        self,
        codes: Iterable[str],
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> list[TradingSignal]:
        """Sequentially dispatch ``codes`` through the per-stock pipeline.

        Shared body for both daily and category-scoped runs; isolates
        rate-limiting and per-stock error handling so the cron-facing
        coroutines stay short.

        ``policy`` is an optional snapshot — when supplied, every stock
        in the loop sees the SAME policy even if a concurrent
        :meth:`update_policy` call swapped ``self._policy`` mid-tick
        (Codex R6 MEDIUM #6).
        """
        ordered = list(codes)
        signals: list[TradingSignal] = []
        for i, code in enumerate(ordered):
            stock_category = self._resolve_category(code, category, policy)
            try:
                signal = await self._run_and_persist(
                    code, stock_category, policy
                )
                if signal is not None:
                    signals.append(signal)
                    log.info(
                        "stock_analysis_complete",
                        code=code,
                        category=stock_category,
                        action=signal.action,
                    )
            except Exception as exc:
                log.error(
                    "stock_analysis_failed",
                    code=code,
                    category=stock_category,
                    error=str(exc),
                )
            if i < len(ordered) - 1:
                await asyncio.sleep(10)
        return signals

    def _resolve_category(
        self,
        stock_code: str,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> Category | None:
        """Return the effective category for ``stock_code``.

        Explicit category wins (used by the per-bucket cron jobs); when
        omitted we fall back to whatever the policy assigns. Returns
        ``None`` when no policy is loaded so the legacy single-cron
        path keeps using the base :class:`PipelineConfig` unchanged.

        ``policy`` is an optional snapshot — pass it through from the
        cron entry so that every stock in one tick sees a consistent
        policy even if a concurrent API mutation swaps ``self._policy``.
        """
        if category is not None:
            return category
        effective_policy = policy if policy is not None else self._policy
        if effective_policy is None:
            return None
        return assign_category(stock_code, effective_policy)

    async def run_single_analysis(
        self, stock_code: str, *, category: Category | None = None
    ) -> TradingSignal | None:
        """Analyze a single stock on demand.

        ``category`` defaults to whatever the loaded policy assigns;
        callers can override (e.g. an operator forcing a deep slow run
        on a fast-bucket stock without mutating the policy).
        """
        effective = self._resolve_category(stock_code, category)
        try:
            return await self._run_and_persist(stock_code, effective)
        except Exception as exc:
            log.error(
                "single_analysis_failed", code=stock_code, error=str(exc)
            )
            return None

    async def _run_and_persist(
        self,
        stock_code: str,
        category: Category | None = None,
        policy: WatchlistPolicy | None = None,
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

        Phase 5B-T02: when ``category`` is provided (and a policy is
        loaded), the pipeline runs against a per-category clone of the
        agent services with that bucket's ``max_debate_rounds`` and a
        hard ``asyncio.wait_for`` matching the bucket's
        ``pipeline_timeout_seconds`` SLA. Without a category we keep
        the legacy code path bit-for-bit (no timeout, base config).
        """
        async with self._run_lock:
            return await self._run_and_persist_locked(
                stock_code, category, policy
            )

    async def _run_and_persist_locked(
        self,
        stock_code: str,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
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

        services, timeout = self._resolve_services_and_timeout(
            category, policy
        )
        try:
            if timeout is None:
                result = await run_analysis(stock_code, services)
            else:
                result = await asyncio.wait_for(
                    run_analysis(stock_code, services), timeout=timeout
                )
        except TimeoutError:
            await self._persist_timeout_skip(stock_code, category, timeout)
            return None
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

    def _resolve_services_and_timeout(
        self,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> tuple[AnalysisServices, int | None]:
        """Build per-category services + timeout, or fall back to base.

        Returning ``(self._services, None)`` for the no-category path
        keeps the legacy single-cron behaviour untouched: no
        ``asyncio.wait_for`` wrapper and the original ``PipelineConfig``
        is reused. With a category and a loaded policy we clone services
        with a fresh ``PipelineConfig`` carrying the bucket's debate
        depth + timeout, and return the timeout for ``wait_for``.

        ``policy`` (when supplied) is the cron-tick snapshot — used so
        every stock in one tick consumes the SAME bucket config even
        if a concurrent :meth:`update_policy` swaps ``self._policy``.
        """
        effective_policy = policy if policy is not None else self._policy
        if category is None or effective_policy is None:
            return self._services, None
        bucket = effective_policy.bucket_for(category)
        new_config = PipelineConfig(
            max_debate_rounds=bucket.max_debate_rounds,
            analysis_timeout_seconds=bucket.pipeline_timeout_seconds,
        )
        services = self._services.model_copy(
            update={"pipeline_config": new_config}
        )
        return services, bucket.pipeline_timeout_seconds

    async def _persist_timeout_skip(
        self,
        stock_code: str,
        category: Category | None,
        timeout: int | None,
    ) -> None:
        """Record a synthetic failed analysis when the SLA timeout fires.

        ``/history`` operators need to see WHICH bucket missed its SLA
        so the structured error prefix names the category. The legacy
        path never times out (timeout=None) so this is only reachable
        from the Fast/Slow code path.
        """
        suffix = (
            f"category={category} timeout={timeout}s"
            if category is not None
            else f"timeout={timeout}s"
        )
        record = AnalysisRecord(
            run_id=str(uuid.uuid4()),
            stock_code=stock_code,
            stock_name=stock_code,
            trade_date=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
            status="failed",
            error=f"pipeline_timeout: {suffix}",
        )
        try:
            await self._mongodb.save_analysis_record(
                record.model_dump(mode="json")
            )
        except Exception as persist_exc:
            log.warning(
                "save_timeout_skip_record_failed",
                code=stock_code,
                error=str(persist_exc),
            )
        log.warning(
            "pipeline_timeout",
            code=stock_code,
            category=category,
            timeout=timeout,
        )

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
        """Sequentially re-run analysis for the given stock codes.

        With a policy loaded each catch-up call uses the stock's
        assigned category, so a slow-bucket name still gets the
        deeper pipeline + 15 min budget on a missed-run replay.
        """
        log.info("catch_up_started", stock_codes=stock_codes)
        for i, code in enumerate(stock_codes):
            category = self._resolve_category(code, None)
            try:
                await self._run_and_persist(code, category)
            except Exception as exc:
                log.error(
                    "catch_up_stock_failed", code=code, error=str(exc)
                )
            if i < len(stock_codes) - 1:
                await asyncio.sleep(10)
        log.info("catch_up_complete", count=len(stock_codes))
