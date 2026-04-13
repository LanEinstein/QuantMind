"""MongoDB-backed stock watchlist management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

log = structlog.get_logger(component="watchlist")


class WatchlistService:
    """MongoDB-backed stock watchlist management.

    Stocks are soft-deleted (active=False) rather than removed,
    preserving history of what was tracked and when.
    """

    def __init__(self, db: Any) -> None:
        """Initialize with a motor AsyncIOMotorDatabase instance."""
        self._collection = db["watchlist"]

    async def initialize(self) -> None:
        """Create indexes."""
        await self._collection.create_index("stock_code", unique=True)

    async def add_stock(self, code: str, name: str) -> None:
        """Add or reactivate a stock in the watchlist."""
        await self._collection.update_one(
            {"stock_code": code},
            {
                "$set": {
                    "stock_code": code,
                    "stock_name": name,
                    "active": True,
                    "added_at": datetime.now(UTC),
                },
            },
            upsert=True,
        )
        log.info("watchlist_stock_added", code=code, name=name)

    async def remove_stock(self, code: str) -> None:
        """Soft-delete a stock from the watchlist."""
        await self._collection.update_one(
            {"stock_code": code},
            {"$set": {"active": False}},
        )
        log.info("watchlist_stock_removed", code=code)

    async def list_stocks(self) -> list[dict[str, Any]]:
        """Return all active watchlist stocks."""
        cursor = self._collection.find({"active": True})
        return await cursor.to_list(length=500)

    async def clear(self) -> None:
        """Deactivate all stocks."""
        await self._collection.update_many({}, {"$set": {"active": False}})
        log.info("watchlist_cleared")
