"""Append-only ReviewRecord persistence (AA-002).

Mongo adapter over the ``review_records`` collection. Append-only by
construction: the only write is ``insert_one`` and a same-``trade_date``
re-run is an idempotent skip — there is no update / delete surface at
all, so a later "correction" must append a new schema-versioned record
rather than rewrite history (the AB promotion engine depends on this).

The Mongo handle is duck-typed (mirrors ``services/mongo_repositories``)
so the module never imports ``backend.data``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

import structlog

from backend.review.models import DailyReviewRecord, WeeklyReviewRecord

log = structlog.get_logger(component="review.store")


class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> Any: ...


def _strip_id(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "_id"}


def _ensure_utc(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    for key in ("created_at",):
        value = out.get(key)
        if isinstance(value, datetime) and value.tzinfo is None:
            out[key] = value.replace(tzinfo=UTC)
    return out


class MongoReviewRecordStore:
    """Adapter over the append-only ``review_records`` collection."""

    COLLECTION = "review_records"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def exists(self, trade_date: str) -> bool:
        """Whether a record for ``trade_date`` was already appended."""
        raw = await self._db[self.COLLECTION].find_one(
            {"trade_date": trade_date}
        )
        return raw is not None

    async def append(self, record: DailyReviewRecord) -> bool:
        """Insert ``record``; returns False on an idempotent skip.

        The exists-then-insert pair is not transactional, but the only
        writer is the single 18:00 cron (one retry runs strictly after
        the first attempt), so a duplicate insert cannot interleave.
        """
        if await self.exists(record.trade_date):
            log.info(
                "review_record_already_exists",
                trade_date=record.trade_date,
            )
            return False
        doc = record.model_dump(mode="python")
        doc["record_id"] = str(record.record_id)
        await self._db[self.COLLECTION].insert_one(doc)
        return True

    async def get(self, trade_date: str) -> DailyReviewRecord | None:
        raw = await self._db[self.COLLECTION].find_one(
            {"trade_date": trade_date}
        )
        if raw is None:
            return None
        return self._decode(raw)

    async def list_between(
        self, start_date: str, end_date: str
    ) -> tuple[DailyReviewRecord, ...]:
        """All records with ``start_date <= trade_date <= end_date``."""
        cursor = (
            self._db[self.COLLECTION]
            .find({"trade_date": {"$gte": start_date, "$lte": end_date}})
            .sort("trade_date", 1)
        )
        out: list[DailyReviewRecord] = []
        async for raw in cursor:
            decoded = self._decode(raw)
            if decoded is not None:
                out.append(decoded)
        return tuple(out)

    def _decode(self, raw: dict[str, Any]) -> DailyReviewRecord | None:
        doc = _ensure_utc(_strip_id(raw))
        rid = doc.get("record_id")
        if isinstance(rid, str):
            doc["record_id"] = UUID(rid)
        try:
            return DailyReviewRecord.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "review_record_decode_failed",
                trade_date=raw.get("trade_date"),
                error=str(exc),
            )
            return None


class MongoWeeklyReviewStore:
    """Adapter over the append-only ``weekly_review_records`` collection.

    Existence of a row for a ``week_key`` IS the "weekend run succeeded"
    marker the holiday catch-up lane gates on (AA-003) — no separate run
    ledger needed.
    """

    COLLECTION = "weekly_review_records"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def exists(self, week_key: str) -> bool:
        raw = await self._db[self.COLLECTION].find_one(
            {"week_key": week_key}
        )
        return raw is not None

    async def append(self, record: WeeklyReviewRecord) -> bool:
        """Insert ``record``; returns False on an idempotent skip."""
        if await self.exists(record.week_key):
            log.info(
                "weekly_review_already_exists", week_key=record.week_key
            )
            return False
        doc = record.model_dump(mode="python")
        doc["record_id"] = str(record.record_id)
        await self._db[self.COLLECTION].insert_one(doc)
        return True

    async def get(self, week_key: str) -> WeeklyReviewRecord | None:
        raw = await self._db[self.COLLECTION].find_one(
            {"week_key": week_key}
        )
        if raw is None:
            return None
        doc = _ensure_utc(_strip_id(raw))
        rid = doc.get("record_id")
        if isinstance(rid, str):
            doc["record_id"] = UUID(rid)
        try:
            return WeeklyReviewRecord.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "weekly_review_decode_failed",
                week_key=raw.get("week_key"),
                error=str(exc),
            )
            return None


__all__ = ["MongoReviewRecordStore", "MongoWeeklyReviewStore"]
