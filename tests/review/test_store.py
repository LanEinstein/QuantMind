"""AA-002 append-only ReviewRecord store tests (fake Mongo collection)."""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.review.models import DailyReviewRecord
from backend.review.store import MongoReviewRecordStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 18, 0, tzinfo=SHANGHAI)


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs,
            key=lambda d: d.get(field, ""),
            reverse=direction == -1,
        )
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if doc.get("trade_date") == query.get("trade_date"):
                return dict(doc)
        return None

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        rng = query.get("trade_date", {})
        rows = [
            d
            for d in self.docs
            if rng.get("$gte", "") <= d.get("trade_date", "")
            and d.get("trade_date", "") <= rng.get("$lte", "9999")
        ]
        return _FakeCursor(rows)


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeCollection()

    def __getitem__(self, name: str) -> _FakeCollection:
        assert name == MongoReviewRecordStore.COLLECTION
        return self.coll


def _record(trade_date: str) -> DailyReviewRecord:
    return DailyReviewRecord(trade_date=trade_date, created_at=NOW)


class TestMongoReviewRecordStore:
    @pytest.mark.asyncio
    async def test_append_then_get_round_trip(self) -> None:
        store = MongoReviewRecordStore(_FakeDb())
        record = _record("2026-06-12")
        assert await store.append(record) is True
        revived = await store.get("2026-06-12")
        assert revived is not None
        assert revived.record_id == record.record_id
        assert revived.trade_date == "2026-06-12"

    @pytest.mark.asyncio
    async def test_rerun_is_idempotent_skip(self) -> None:
        db = _FakeDb()
        store = MongoReviewRecordStore(db)
        assert await store.append(_record("2026-06-12")) is True
        assert await store.append(_record("2026-06-12")) is False
        assert len(db.coll.docs) == 1

    @pytest.mark.asyncio
    async def test_store_has_no_update_or_delete_surface(self) -> None:
        """Append-only by construction (P1-2.A-amendment §1.3)."""
        forbidden = {
            "update",
            "update_one",
            "replace",
            "delete",
            "delete_one",
            "remove",
        }
        public = {
            name
            for name in dir(MongoReviewRecordStore)
            if not name.startswith("_")
        }
        assert forbidden.isdisjoint(public)

    @pytest.mark.asyncio
    async def test_list_between_is_inclusive_and_sorted(self) -> None:
        store = MongoReviewRecordStore(_FakeDb())
        for day in ("2026-06-10", "2026-06-12", "2026-06-11"):
            await store.append(_record(day))
        rows = await store.list_between("2026-06-10", "2026-06-11")
        assert [r.trade_date for r in rows] == ["2026-06-10", "2026-06-11"]

    @pytest.mark.asyncio
    async def test_corrupt_row_dropped_not_raised(self) -> None:
        db = _FakeDb()
        store = MongoReviewRecordStore(db)
        db.coll.docs.append({"trade_date": "2026-06-12", "garbage": True})
        assert await store.get("2026-06-12") is None
        rows = await store.list_between("2026-06-01", "2026-06-30")
        assert rows == ()
