"""MongoDB persistence service via motor async driver."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from pymongo import ASCENDING, DESCENDING, UpdateOne

from backend.models.market import (
    FinancialData,
    IndexQuote,
    NewsArticle,
    StockQuote,
)

if TYPE_CHECKING:
    import pandas as pd

log = structlog.get_logger(component="database")

QuoteType = IndexQuote | StockQuote


class MongoDBService:
    """Async MongoDB persistence for market data, kline, and news.

    Uses motor's AsyncIOMotorDatabase for all operations.
    """

    def __init__(self, db: Any) -> None:
        """Initialize with a motor AsyncIOMotorDatabase instance."""
        self._db = db
        self._log = log

    async def initialize(self) -> None:
        """Create indexes on all collections."""
        market = self._db["market_realtime"]
        await market.create_index(
            [("code", ASCENDING), ("timestamp", DESCENDING)],
            unique=True,
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
        await news.create_index(
            [("url", ASCENDING)],
            unique=True,
            background=True,
        )

        simulations = self._db["simulations"]
        await simulations.create_index(
            [("created_at", DESCENDING)],
            background=True,
        )

        signals = self._db["trading_signals"]
        await signals.create_index(
            [("stock_code", ASCENDING), ("trade_date", DESCENDING)],
            unique=True,
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
        """Bulk upsert news articles by URL. Returns operation count."""
        if not articles:
            return 0

        coll = self._db["news_articles"]
        ops = [
            UpdateOne(
                {"url": a.url},
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
