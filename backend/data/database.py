"""MongoDB persistence service via motor async driver."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from pymongo import ASCENDING, DESCENDING, UpdateOne
from pymongo.errors import OperationFailure

from backend.models.market import (
    FinancialData,
    IndexQuote,
    NewsArticle,
    StockQuote,
    WatchlistMarketSnapshot,
)

if TYPE_CHECKING:
    import pandas as pd

log = structlog.get_logger(component="database")

QuoteType = IndexQuote | StockQuote


class ReplicaSetUnavailableError(RuntimeError):
    """Raised when Mongo is not configured as a replica-set member.

    Multi-document transactions used by broker_events / broker_snapshots
    (E-002) require the connected Mongo node to be part of a replica
    set. Starting the BrokerScheduler against a standalone deployment
    would silently break the delta+snapshot recovery invariant, so the
    boot probe surfaces this as a fail-closed startup error
    (P1-2.A §2 red line 4).
    """

_LEDGER_CORRELATION_FIELDS: frozenset[str] = frozenset(
    {
        "instruction_id",
        "analysis_record_id",
        "signal_id",
        "risk_validation_id",
        "broker_order_id",
        "feishu_message_id",
        "execution_report_id",
        "reconciliation_ticket_id",
        "acceptance_report_id",
    }
)


class MongoDBService:
    """Async MongoDB persistence for market data, kline, and news.

    Uses motor's AsyncIOMotorDatabase for all operations.
    """

    # codex cycle 3 P2: when the partial unique index on
    # ``evidence_collection`` cannot be created (rare; e.g. permission /
    # version regression), the event-driven daily cap drops back to
    # ``count_documents + insert_one``. The writer reads this flag at
    # ``write()`` time and refuses event-driven writes outright so the
    # P0-9 §2 redline 1 cap is fail-closed instead of silently
    # downgraded.
    EVIDENCE_EVENT_CAP_INDEX_NAME = "evidence_event_driven_daily_cap"

    def __init__(self, db: Any) -> None:
        """Initialize with a motor AsyncIOMotorDatabase instance."""
        self.evidence_event_cap_index_ok: bool = False
        self._db = db
        self._log = log

    # ------------------------------------------------------------------
    # Replica-set boot probe (E-001 / P1-2.A)
    # ------------------------------------------------------------------

    async def is_replica_set(self) -> bool:
        """Return ``True`` when the connected Mongo node is in a replica set.

        Runs the ``hello`` admin command and inspects ``setName``. We
        avoid raising here because callers (BrokerScheduler boot,
        readiness probes) want a boolean answer. The fail-closed
        wrapper is :meth:`assert_replica_set`.
        """
        try:
            info = await self._db.client.admin.command("hello")
        except Exception as exc:  # noqa: BLE001 — boot probe must classify any error
            self._log.warning("mongo_hello_failed", error=str(exc))
            return False
        set_name = info.get("setName") if isinstance(info, dict) else None
        return bool(set_name)

    async def assert_replica_set(self) -> str:
        """Fail-closed boot fence: require a replica-set member.

        Returns the ``setName`` on success; raises
        :class:`ReplicaSetUnavailableError` when the node is standalone
        or unreachable. The BrokerScheduler / EOD pipeline use this on
        startup so a misconfigured deployment cannot silently lose the
        transactional guarantees promised by P1-2.A.
        """
        try:
            info = await self._db.client.admin.command("hello")
        except Exception as exc:  # noqa: BLE001 — boot fence escalates *all* errors
            raise ReplicaSetUnavailableError(
                f"mongo hello probe failed: {exc!r} — broker persistence "
                "requires a single-node replica set; see deploy/README.md "
                "§1.1 for rs.initiate() instructions"
            ) from exc
        set_name = info.get("setName") if isinstance(info, dict) else None
        if not set_name:
            raise ReplicaSetUnavailableError(
                "mongo node is not a replica-set member — broker_events / "
                "broker_snapshots require multi-document transactions. "
                "Run `mongosh --eval 'rs.initiate()'` once per "
                "deploy/README.md §1.1 then restart the backend."
            )
        return str(set_name)

    async def initialize(self) -> None:
        """Create indexes on all collections."""
        market = self._db["market_realtime"]
        await market.create_index(
            [("code", ASCENDING), ("timestamp", DESCENDING)],
            unique=True,
            background=True,
        )

        # watchlist_market_snapshots — per-stock 30s tick (C-003 / P0-8 §1.1).
        # Unique key (code, snapshot_at) makes the bulk-upsert idempotent
        # against retried 30s ticks. The (snapshot_at DESC) standalone
        # index backs the latest-tick / windowed missing-rate reads from
        # DataQualityProvider without a collection scan.
        watchlist_snapshots = self._db["watchlist_market_snapshots"]
        await watchlist_snapshots.create_index(
            [("code", ASCENDING), ("snapshot_at", DESCENDING)],
            unique=True,
            background=True,
        )
        await watchlist_snapshots.create_index(
            [("snapshot_at", DESCENDING)],
            background=True,
        )

        kline = self._db["kline_daily"]
        await kline.create_index(
            [("code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            background=True,
        )

        financial = self._db["financial_data"]
        await financial.create_index(
            [("code", ASCENDING), ("report_date", ASCENDING)],
            unique=True,
            background=True,
        )

        news = self._db["news_articles"]
        await news.create_index(
            [("publish_time", DESCENDING)],
            background=True,
        )
        # C-005 (codex cycle 1 P2 + cycle 2 P2): the unique constraint
        # must include ``domain`` so the same URL surfaced in two
        # domains keeps two rows. Existing deployments may still carry
        # the legacy unique ``url_1`` index — drop it so re-running
        # initialise on an old DB lets the compound unique index land
        # cleanly. We also pre-populate ``domain`` on any legacy row
        # lacking one so the unique build does not collide.
        try:
            await news.drop_index("url_1")
        except OperationFailure as exc:
            # codex cycle 3 P2: only swallow the "index not found" /
            # "namespace not found" Mongo error codes (27 / 26 / 28).
            # Any other OperationFailure means the legacy unique index
            # is still alive and would still collapse cross-domain
            # duplicates — those must surface, not silently degrade.
            if exc.code in (26, 27, 28):
                self._log.debug(
                    "news_legacy_url_index_drop_skipped",
                    code=exc.code,
                    error=str(exc),
                )
            else:
                raise
        try:
            await news.update_many(
                {"domain": {"$exists": False}},
                {"$set": {"domain": "financial"}},
            )
        except Exception as exc:
            self._log.warning("news_legacy_domain_backfill_failed", error=str(exc))
        await news.create_index(
            [("url", ASCENDING), ("domain", ASCENDING)],
            unique=True,
            background=True,
        )

        simulations = self._db["simulations"]
        await simulations.create_index(
            [("created_at", DESCENDING)],
            background=True,
        )

        # C-006: evidence_collection holds MIROFISH- prefixed rows
        # (P0-8 §1.6.2 / §2 redline 14). Unique evidence_id + descending
        # trade_date so the event-driven daily-cap query
        # (count where path='event_driven' and trade_date=today) stays
        # O(log n).
        evidence_collection = self._db["evidence_collection"]
        await evidence_collection.create_index(
            [("evidence_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await evidence_collection.create_index(
            [("trade_date", DESCENDING), ("path", ASCENDING)],
            background=True,
        )
        # codex cycle 2 P2: turn the event-driven daily cap (P0-9 §2
        # redline 1) into an atomic DB constraint instead of relying on
        # ``count_documents + insert_one``. A unique *partial* index on
        # ``(trade_date, path)`` filtered to ``path='event_driven'``
        # blocks the second insert with DuplicateKeyError so two
        # concurrent writers cannot both slip past the cap check.
        #
        # codex cycle 3 P2: when this index cannot be created the cap
        # falls back to ``count_documents + insert_one`` (racy under
        # concurrent writes). Track that explicitly so the writer can
        # fail-closed instead of silently degrading.
        try:
            await evidence_collection.create_index(
                [("trade_date", ASCENDING), ("path", ASCENDING)],
                unique=True,
                partialFilterExpression={"path": "event_driven"},
                name=self.EVIDENCE_EVENT_CAP_INDEX_NAME,
                background=True,
            )
            self.evidence_event_cap_index_ok = True
        except Exception as exc:
            self.evidence_event_cap_index_ok = False
            self._log.warning(
                "evidence_event_cap_index_create_failed",
                index=self.EVIDENCE_EVENT_CAP_INDEX_NAME,
                error=str(exc),
            )

        signals = self._db["trading_signals"]
        await signals.create_index(
            [("stock_code", ASCENDING), ("trade_date", DESCENDING)],
            unique=True,
            background=True,
        )
        # Monitoring dashboard aggregates by date without stock_code, so
        # the compound index above cannot cover the trade_date-only scan.
        # A standalone descending index keeps count_signals_for_date /
        # count_signals_since O(log n) as evaluation data grows.
        await signals.create_index(
            [("trade_date", DESCENDING)],
            background=True,
        )

        index_prices = self._db["index_prices"]
        await index_prices.create_index(
            [("index_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            background=True,
        )

        cost_tracking = self._db["cost_tracking"]
        await cost_tracking.create_index(
            [
                ("date", DESCENDING),
                ("agent_name", ASCENDING),
                ("provider", ASCENDING),
            ],
            unique=True,
            background=True,
        )

        analysis_records = self._db["analysis_records"]
        await analysis_records.create_index(
            [("run_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await analysis_records.create_index(
            [
                ("stock_code", ASCENDING),
                ("trade_date", DESCENDING),
                ("created_at", DESCENDING),
            ],
            background=True,
        )
        # Not unique: trading_signals upserts by (stock_code, trade_date),
        # so multiple analysis_records for the same trading day legitimately
        # share a signal_id pointing at the latest signal row.
        await analysis_records.create_index(
            [("signal_id", ASCENDING)],
            sparse=True,
            background=True,
        )
        await analysis_records.create_index(
            [("created_at", DESCENDING)],
            background=True,
        )
        # History-list filter shapes: {stock_code}, {trade_date}, and
        # {stock_code, trade_date}. The main (stock_code, trade_date,
        # created_at) index covers the last two, but stock_code-only
        # queries benefit from a (stock_code, created_at DESC) index
        # that also covers the sort, and trade_date-only queries
        # benefit from (trade_date DESC, created_at DESC).
        await analysis_records.create_index(
            [("stock_code", ASCENDING), ("created_at", DESCENDING)],
            background=True,
        )
        await analysis_records.create_index(
            [("trade_date", DESCENDING), ("created_at", DESCENDING)],
            background=True,
        )

        # Phase 5B-T03 shadow-test harness reads/writes here; the
        # recorder upserts by run_id and the consumer scans by
        # created_at over a 7-30 day window. Without these the harness
        # devolves to a collection scan once retention grows past a
        # few weeks (codex P5B-exit R3 P3).
        shadow_decisions = self._db["shadow_decisions"]
        await shadow_decisions.create_index(
            [("run_id", ASCENDING)],
            unique=True,
            background=True,
        )
        # TTL on created_at — shadow_decisions hold per-stock action /
        # confidence telemetry that is sensitive (it leaks our routing
        # behaviour). Auto-expire after 30 days so the store can't
        # accumulate forever (codex P5B-exit R5 MED on retention).
        # ``_TTL_DAYS_DEFAULT`` in shadow_recorder mirrors this number
        # for consistency.
        from backend.services.shadow_recorder import _TTL_DAYS_DEFAULT
        await shadow_decisions.create_index(
            [("created_at", DESCENDING)],
            expireAfterSeconds=_TTL_DAYS_DEFAULT * 86400,
            background=True,
        )

        # decision_ledger — single correlation graph keyed by instruction_id
        # (B-002 / P0-3 §3.1). The unique key drives upsert idempotence;
        # the remaining indexes back the front-end three-tab Reason
        # drawer and audit-by-correlation queries.
        decision_ledger = self._db["decision_ledger"]
        await decision_ledger.create_index(
            [("instruction_id", ASCENDING)],
            unique=True,
            background=True,
        )
        for handle in (
            "analysis_record_id",
            "signal_id",
            "risk_validation_id",
            "broker_order_id",
            "feishu_message_id",
            "execution_report_id",
            "reconciliation_ticket_id",
            "acceptance_report_id",
        ):
            await decision_ledger.create_index(
                [(handle, ASCENDING)],
                sparse=True,
                background=True,
            )
        await decision_ledger.create_index(
            [("trade_ids", ASCENDING)],
            sparse=True,
            background=True,
        )
        await decision_ledger.create_index(
            [("updated_at", DESCENDING)],
            background=True,
        )

        # audit_events — append-only insert-only (B-005 / P1-6 §1.7).
        # TTL=180 days on `timestamp` (P0-6 45 trading-day window × 4
        # safety multiplier). Indexes back the 4 query shapes used by
        # scripts/query_audit.py + GET /api/audit/events.
        audit_events = self._db["audit_events"]
        await audit_events.create_index(
            [("timestamp", DESCENDING)], background=True
        )
        await audit_events.create_index(
            [("event_type", ASCENDING), ("timestamp", DESCENDING)],
            background=True,
        )
        await audit_events.create_index(
            [("actor", ASCENDING), ("timestamp", DESCENDING)],
            background=True,
        )
        await audit_events.create_index(
            [("correlation_id", ASCENDING)],
            sparse=True,
            background=True,
        )
        await audit_events.create_index(
            [("resource_type", ASCENDING), ("resource_id", ASCENDING)],
            sparse=True,
            background=True,
        )
        await audit_events.create_index(
            [("timestamp", ASCENDING)],
            expireAfterSeconds=180 * 86400,
            background=True,
        )

        # broker_events — append-only delta rows for the MockBroker
        # single mirror (E-002 / P1-2.A). The recovery loader reads by
        # sequence and tails the (sequence > snapshot.last_event_sequence)
        # query; the unique index on ``sequence`` prevents duplicate
        # writes from racing transactions even with the per-transaction
        # max() guard in BrokerEventStore.append.
        broker_events = self._db["broker_events"]
        await broker_events.create_index(
            [("sequence", ASCENDING)],
            unique=True,
            background=True,
        )
        await broker_events.create_index(
            [("event_type", ASCENDING), ("sequence", ASCENDING)],
            background=True,
        )
        await broker_events.create_index(
            [("order_id", ASCENDING)],
            sparse=True,
            background=True,
        )
        await broker_events.create_index(
            [("correlation_id", ASCENDING)],
            sparse=True,
            background=True,
        )

        # equity_points — 30s intraday MTM rows (E-006 / P1-2.B §1.1).
        # Acceptance + Performance UIs scan by trade_date / snapshot_at;
        # unique (snapshot_at) keeps a retried 30s tick idempotent.
        equity_points = self._db["equity_points"]
        await equity_points.create_index(
            [("snapshot_at", DESCENDING)],
            unique=True,
            background=True,
        )
        await equity_points.create_index(
            [("trade_date", DESCENDING), ("snapshot_at", DESCENDING)],
            background=True,
        )

        # broker_snapshots — EOD checkpoint rows. Recovery reads by
        # last_event_sequence DESC; snapshot_id is unique so a botched
        # double-write at EOD cannot create a phantom row.
        broker_snapshots = self._db["broker_snapshots"]
        await broker_snapshots.create_index(
            [("last_event_sequence", DESCENDING)],
            background=True,
        )
        await broker_snapshots.create_index(
            [("snapshot_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await broker_snapshots.create_index(
            [("trade_date", DESCENDING)],
            background=True,
        )

        # instruction_plans — single source of truth for the executable
        # plan timeline (P0-3 / I-001). Unique instruction_id backs the
        # upsert path used by the SignalToPlan orchestrator + the
        # ExecutionReportOrchestrator's InstructionPlanLookup.get().
        instruction_plans = self._db["instruction_plans"]
        await instruction_plans.create_index(
            [("instruction_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await instruction_plans.create_index(
            [("trade_date", DESCENDING), ("created_at", DESCENDING)],
            background=True,
        )
        await instruction_plans.create_index(
            [("status", ASCENDING), ("created_at", DESCENDING)],
            background=True,
        )

        # instruction_plan_builder_early_returns — sibling collection that
        # records D-003 builder early-return rows tied to plans that did
        # get persisted. Drawer reads sorted by ``at`` so the operator
        # sees the chronological gating chain.
        builder_rows = self._db["instruction_plan_builder_early_returns"]
        await builder_rows.create_index(
            [("instruction_id", ASCENDING), ("at", ASCENDING)],
            background=True,
        )

        # reconciliation_tickets — F-005 / P0-5 §1.5.1 lifecycle store.
        # Unique ticket_id makes ReconciliationOrchestrator.decide_ticket
        # idempotent against a retried POST decide. Compound
        # (trade_date, status) backs the open-ticket triage list.
        reconciliation_tickets = self._db["reconciliation_tickets"]
        await reconciliation_tickets.create_index(
            [("ticket_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await reconciliation_tickets.create_index(
            [("trade_date", DESCENDING), ("status", ASCENDING)],
            background=True,
        )

        # daily_reconciliations — user-reported EOD mirror (P0-5 §1.1.2).
        # Unique ticket_id keeps a retried Feishu delivery of the same
        # reply idempotent. Standalone trade_date index backs the
        # reconciliation-by-day reads.
        daily_reconciliations = self._db["daily_reconciliations"]
        await daily_reconciliations.create_index(
            [("ticket_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await daily_reconciliations.create_index(
            [("trade_date", DESCENDING)],
            background=True,
        )

        # acceptance_reports — 16:00:30 daily roll-up (P0-6). Unique
        # trade_date so a same-day re-run overwrites the prior point
        # (mirrors ``InMemoryAcceptanceRepository.upsert`` semantics).
        acceptance_reports = self._db["acceptance_reports"]
        await acceptance_reports.create_index(
            [("trade_date", ASCENDING)],
            unique=True,
            background=True,
        )
        await acceptance_reports.create_index(
            [("computed_at", DESCENDING)],
            background=True,
        )

        self._log.info("mongodb_indexes_created")

    async def save_market_snapshot(
        self, quotes: list[QuoteType]
    ) -> int:
        """Bulk upsert market quotes. Returns count of operations."""
        if not quotes:
            return 0

        coll = self._db["market_realtime"]
        ops = [
            UpdateOne(
                {"code": q.code, "timestamp": q.timestamp},
                {"$set": q.model_dump()},
                upsert=True,
            )
            for q in quotes
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_market_snapshot_failed", error=str(exc))
            return 0

    async def save_watchlist_snapshot(
        self, snapshots: list[WatchlistMarketSnapshot]
    ) -> int:
        """Bulk upsert watchlist 30s snapshots (C-003 / P0-8 §1.1).

        Keyed by ``(code, snapshot_at)`` so a retried scheduler tick
        (e.g. APScheduler missfire grace re-fire) is idempotent. Returns
        the combined upserted + modified count. Failures are logged and
        swallowed — the scheduler's outer ``try`` already absorbs the
        return value, and the 30s cadence will retry on the next tick;
        re-raising would risk crashing the AsyncIOScheduler loop
        (P0-8 §2 mandates fail-open for transient infra glitches).
        """
        if not snapshots:
            return 0

        coll = self._db["watchlist_market_snapshots"]
        ops = [
            UpdateOne(
                {"code": s.code, "snapshot_at": s.snapshot_at},
                {"$set": s.model_dump()},
                upsert=True,
            )
            for s in snapshots
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_watchlist_snapshot_failed", error=str(exc))
            return 0

    async def count_watchlist_snapshots_in_window(
        self, start: datetime, end: datetime
    ) -> int:
        """Count rows persisted to ``watchlist_market_snapshots`` in [start, end).

        Backs :func:`backend.data.missing_rate.compute_window_missing_rate`
        without forcing callers to write their own aggregation pipeline.
        Returns 0 on any driver error so the acceptance scorer can
        fall through to the fail-closed degraded branch instead of
        crashing the report.
        """
        coll = self._db["watchlist_market_snapshots"]
        try:
            return await coll.count_documents(
                {"snapshot_at": {"$gte": start, "$lt": end}}
            )
        except Exception as exc:
            self._log.warning(
                "count_watchlist_snapshots_failed", error=str(exc)
            )
            return 0

    async def save_kline(self, code: str, df: pd.DataFrame) -> int:
        """Bulk upsert K-line DataFrame rows. Returns operation count."""
        if df is None or df.empty:
            return 0

        coll = self._db["kline_daily"]
        ops = []
        for _, row in df.iterrows():
            doc = row.to_dict()
            doc["code"] = code
            ops.append(
                UpdateOne(
                    {"code": code, "date": doc.get("date", "")},
                    {"$set": doc},
                    upsert=True,
                )
            )

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_kline_failed", error=str(exc))
            return 0

    async def save_financial_data(self, data: FinancialData) -> None:
        """Upsert a single financial data document."""
        coll = self._db["financial_data"]
        try:
            await coll.update_one(
                {"code": data.code, "report_date": data.report_date},
                {"$set": data.model_dump()},
                upsert=True,
            )
        except Exception as exc:
            self._log.warning("save_financial_failed", error=str(exc))

    async def save_news(self, articles: list[NewsArticle]) -> int:
        """Bulk upsert news articles by ``(url, domain)``.

        C-005: cross-domain duplicates of the same URL are preserved as
        separate rows so the multi-domain echo of a story (P0-8 §1.2)
        survives Mongo persistence — within-domain dedupe stays
        single-row because the upsert key includes ``domain``.
        """
        if not articles:
            return 0

        coll = self._db["news_articles"]
        ops = [
            UpdateOne(
                {"url": a.url, "domain": a.domain},
                {"$set": a.model_dump()},
                upsert=True,
            )
            for a in articles
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_news_failed", error=str(exc))
            return 0

    async def query_latest_quotes(
        self, codes: list[str]
    ) -> list[dict[str, Any]]:
        """Find latest quote per code."""
        coll = self._db["market_realtime"]
        cursor = coll.find({"code": {"$in": codes}}).sort(
            "timestamp", DESCENDING
        )
        return await cursor.to_list(length=len(codes) * 2)

    async def query_kline(
        self,
        code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """Query K-line data for a code within a date range."""
        query: dict[str, Any] = {"code": code}
        if start_date or end_date:
            date_filter: dict[str, str] = {}
            if start_date:
                date_filter["$gte"] = start_date
            if end_date:
                date_filter["$lte"] = end_date
            query["date"] = date_filter

        coll = self._db["kline_daily"]
        cursor = coll.find(query).sort("date", ASCENDING)
        return await cursor.to_list(length=10000)

    async def query_news(
        self,
        limit: int = 50,
        stock_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query news articles, optionally filtered by stock code."""
        query: dict[str, Any] = {}
        if stock_code:
            query["stock_codes"] = stock_code

        coll = self._db["news_articles"]
        cursor = coll.find(query).sort("publish_time", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    # -- Trading signal persistence --

    async def save_signal(self, signal: dict[str, Any]) -> str:
        """Save a TradingSignal dict to 'trading_signals' collection.

        Uses upsert on (stock_code, trade_date) to prevent duplicates.
        Returns the document _id as string.
        """
        coll = self._db["trading_signals"]
        key = {
            "stock_code": signal["stock_code"],
            "trade_date": signal["trade_date"],
        }
        result = await coll.update_one(key, {"$set": signal}, upsert=True)
        if result.upserted_id is not None:
            return str(result.upserted_id)
        doc = await coll.find_one(key, {"_id": 1})
        return str(doc["_id"])

    async def query_signals(
        self, stock_code: str | None = None, days: int = 30
    ) -> list[dict[str, Any]]:
        """Query recent trading signals.

        Args:
            stock_code: Filter by stock code. None = all stocks.
            days: Lookback window in days.

        Returns:
            Signals sorted by trade_date DESC, then stock_code ASC.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        query: dict[str, Any] = {"trade_date": {"$gte": cutoff}}
        if stock_code:
            query["stock_code"] = stock_code

        coll = self._db["trading_signals"]
        cursor = coll.find(query).sort(
            [("trade_date", DESCENDING), ("stock_code", ASCENDING)]
        )
        return await cursor.to_list(length=1000)

    async def query_signals_for_trade_date(
        self, trade_date: str, stock_codes: list[str]
    ) -> list[dict[str, Any]]:
        """Query signals for a specific trading day and stock set."""
        if not stock_codes:
            return []

        coll = self._db["trading_signals"]
        cursor = coll.find(
            {
                "trade_date": trade_date,
                "stock_code": {"$in": stock_codes},
            }
        ).sort("stock_code", ASCENDING)
        return await cursor.to_list(length=len(stock_codes))

    async def get_signal_by_id(self, signal_id: str) -> dict[str, Any] | None:
        """Retrieve a single signal by MongoDB ObjectId string."""
        from bson import ObjectId

        coll = self._db["trading_signals"]
        return await coll.find_one({"_id": ObjectId(signal_id)})

    # -- Index price persistence --

    async def save_index_prices(
        self, index_code: str, prices: list[dict[str, Any]]
    ) -> int:
        """Bulk upsert index prices to 'index_prices' collection."""
        if not prices:
            return 0

        coll = self._db["index_prices"]
        ops = [
            UpdateOne(
                {"index_code": index_code, "date": p["date"]},
                {"$set": {**p, "index_code": index_code}},
                upsert=True,
            )
            for p in prices
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_index_prices_failed", error=str(exc))
            return 0

    async def get_index_prices(
        self,
        index_code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """Query index prices for a code within a date range, sorted by date ASC."""
        query: dict[str, Any] = {"index_code": index_code}
        if start_date or end_date:
            date_filter: dict[str, str] = {}
            if start_date:
                date_filter["$gte"] = start_date
            if end_date:
                date_filter["$lte"] = end_date
            query["date"] = date_filter

        coll = self._db["index_prices"]
        cursor = coll.find(query).sort("date", ASCENDING)
        return await cursor.to_list(length=10000)

    # -- Cost tracking persistence --

    async def save_cost_entry(self, entry: dict[str, Any]) -> None:
        """Upsert daily cost entry to 'cost_tracking' collection."""
        coll = self._db["cost_tracking"]
        key = {
            "date": entry["date"],
            "agent_name": entry["agent_name"],
            "provider": entry["provider"],
        }
        try:
            await coll.update_one(key, {"$set": entry}, upsert=True)
        except Exception as exc:
            self._log.warning("save_cost_entry_failed", error=str(exc))

    async def get_cost_history(self, days: int = 30) -> list[dict[str, Any]]:
        """Query cost history from MongoDB."""
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        coll = self._db["cost_tracking"]
        cursor = coll.find({"date": {"$gte": cutoff}}).sort("date", DESCENDING)
        return await cursor.to_list(length=10000)

    # -- Analysis record persistence --

    async def save_analysis_record(
        self, record: dict[str, Any]
    ) -> str:
        """Upsert a full AnalysisRecord to `analysis_records` by run_id.

        Same run_id replays overwrite; distinct run_ids accumulate so
        re-runs on the same stock/date preserve every trail. Returns the
        document _id as string.
        """
        coll = self._db["analysis_records"]
        key = {"run_id": record["run_id"]}
        result = await coll.update_one(key, {"$set": record}, upsert=True)
        if result.upserted_id is not None:
            return str(result.upserted_id)
        doc = await coll.find_one(key, {"_id": 1})
        return str(doc["_id"])

    async def query_analysis_records(
        self,
        stock_code: str | None = None,
        trade_date: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query recent analysis records.

        Sorted by created_at DESC so the most recent runs are first. When
        multiple runs share a (stock_code, trade_date) pair, all are
        returned (we never collapse runs into a single row).
        """
        query: dict[str, Any] = {}
        if stock_code:
            query["stock_code"] = stock_code
        if trade_date:
            query["trade_date"] = trade_date

        bounded = max(1, min(limit, 500))
        coll = self._db["analysis_records"]
        cursor = coll.find(query).sort("created_at", DESCENDING).limit(bounded)
        return await cursor.to_list(length=bounded)

    async def get_analysis_record_by_id(
        self, record_id: str
    ) -> dict[str, Any] | None:
        """Retrieve a single analysis record.

        Matches either the MongoDB ObjectId string or the run_id UUID.
        Returns None for any invalid id; never raises ObjectId errors.
        """
        from bson import ObjectId
        from bson.errors import InvalidId

        coll = self._db["analysis_records"]
        try:
            oid = ObjectId(record_id)
        except (InvalidId, TypeError, ValueError):
            return await coll.find_one({"run_id": record_id})
        doc = await coll.find_one({"_id": oid})
        if doc is None:
            doc = await coll.find_one({"run_id": record_id})
        return doc

    # -- decision_ledger persistence (B-002 / P0-3 §3.1) --

    async def upsert_decision_ledger_entry(
        self, entry: dict[str, Any]
    ) -> None:
        """Upsert a decision_ledger entry keyed by instruction_id.

        The repository layer (DecisionLedgerService) serializes the
        :class:`DecisionLedgerEntry` to dict; this method only does the
        Mongo round-trip so the service stays storage-agnostic.
        """
        coll = self._db["decision_ledger"]
        await coll.update_one(
            {"instruction_id": entry["instruction_id"]},
            {"$set": entry},
            upsert=True,
        )

    async def get_decision_ledger_by_instruction(
        self, instruction_id: str
    ) -> dict[str, Any] | None:
        """Return the ledger entry for ``instruction_id`` or None."""
        coll = self._db["decision_ledger"]
        return await coll.find_one({"instruction_id": instruction_id})

    async def find_decision_ledger_by_correlation(
        self, field: str, value: str
    ) -> dict[str, Any] | None:
        """Resolve any one correlation handle back to its ledger entry.

        Supported fields: ``instruction_id``, ``analysis_record_id``,
        ``signal_id``, ``risk_validation_id``, ``broker_order_id``,
        ``feishu_message_id``, ``execution_report_id``,
        ``reconciliation_ticket_id``, ``acceptance_report_id``, plus the
        virtual ``trade_id`` which queries the ``trade_ids`` array.
        Unknown fields raise ``ValueError`` so typos at the call site
        do not silently return ``None``.
        """
        if field == "trade_id":
            query: dict[str, Any] = {"trade_ids": value}
        elif field in _LEDGER_CORRELATION_FIELDS:
            query = {field: value}
        else:
            raise ValueError(f"unknown ledger correlation field {field!r}")
        coll = self._db["decision_ledger"]
        return await coll.find_one(query)

    # -- Monitoring helpers (Session C) --

    async def count_signals_for_date(self, date: str) -> int:
        """Number of trading_signals rows with trade_date == date."""
        coll = self._db["trading_signals"]
        return await coll.count_documents({"trade_date": date})

    async def count_signals_since(self, cutoff: str) -> int:
        """Number of trading_signals rows with trade_date >= cutoff."""
        coll = self._db["trading_signals"]
        return await coll.count_documents({"trade_date": {"$gte": cutoff}})

    async def sum_cost_for_date(self, date: str) -> float:
        """Total CNY cost recorded in cost_tracking on a given date."""
        coll = self._db["cost_tracking"]
        pipeline = [
            {"$match": {"date": date}},
            {"$group": {"_id": None, "total": {"$sum": "$cost_cny"}}},
        ]
        async for doc in coll.aggregate(pipeline):
            total = doc.get("total")
            return float(total) if total is not None else 0.0
        return 0.0

    async def get_latest_analysis_record(
        self,
    ) -> dict[str, Any] | None:
        """Most recent analysis_records row, or None if empty."""
        coll = self._db["analysis_records"]
        cursor = coll.find({}).sort("created_at", DESCENDING).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None
