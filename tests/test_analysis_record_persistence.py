"""Tests for analysis_records persistence methods on MongoDBService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from backend.data.database import MongoDBService

_OID_1 = str(ObjectId())
_OID_2 = str(ObjectId())


def _make_records_collection(
    docs: list[dict] | None = None,
    upserted_id: ObjectId | None = None,
    find_one_doc: dict | None = None,
) -> AsyncMock:
    coll = AsyncMock()
    coll.create_index = AsyncMock()

    coll.update_one = AsyncMock(
        return_value=MagicMock(upserted_id=upserted_id)
    )
    coll.find_one = AsyncMock(return_value=find_one_doc)

    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=docs or [])
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    coll.find = MagicMock(return_value=cursor)

    return coll


def _make_mock_db(records_coll: AsyncMock) -> MagicMock:
    db = MagicMock()
    default = AsyncMock()
    default.create_index = AsyncMock()

    def getitem(name: str) -> AsyncMock:
        return records_coll if name == "analysis_records" else default

    db.__getitem__ = MagicMock(side_effect=getitem)
    return db


def _sample_record_dict(run_id: str = "run-abc") -> dict:
    now = datetime(2026, 4, 24, 9, 50, tzinfo=UTC).isoformat()
    return {
        "run_id": run_id,
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "trade_date": "2026-04-24",
        "status": "completed",
        "max_rounds": 2,
        "current_round": 2,
        "steps": [],
        "analysts": [],
        "intelligence_officer": None,
        "debates": [],
        "risk_assessment": None,
        "decision": None,
        "signal_id": None,
        "created_at": now,
        "completed_at": now,
        "error": None,
    }


class TestSaveAnalysisRecord:
    """Tests for save_analysis_record method."""

    @pytest.mark.asyncio
    async def test_inserts_and_returns_id_on_upsert(self) -> None:
        coll = _make_records_collection(upserted_id=ObjectId(_OID_1))
        service = MongoDBService(_make_mock_db(coll))

        record = _sample_record_dict()
        result_id = await service.save_analysis_record(record)

        assert isinstance(result_id, str)
        assert result_id == _OID_1
        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        assert call_args[0][0] == {"run_id": record["run_id"]}
        assert call_args[0][1] == {"$set": record}
        assert call_args[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_returns_existing_id_on_update(self) -> None:
        coll = _make_records_collection(
            upserted_id=None, find_one_doc={"_id": ObjectId(_OID_2)}
        )
        service = MongoDBService(_make_mock_db(coll))

        result_id = await service.save_analysis_record(_sample_record_dict())
        assert result_id == _OID_2

    @pytest.mark.asyncio
    async def test_same_run_id_is_idempotent(self) -> None:
        coll = _make_records_collection(upserted_id=ObjectId(_OID_1))
        service = MongoDBService(_make_mock_db(coll))

        record = _sample_record_dict(run_id="same-id")
        await service.save_analysis_record(record)
        await service.save_analysis_record({**record, "status": "failed"})

        assert coll.update_one.call_count == 2
        for call in coll.update_one.call_args_list:
            assert call[0][0] == {"run_id": "same-id"}
            assert call[1]["upsert"] is True


class TestQueryAnalysisRecords:
    """Tests for query_analysis_records method."""

    @pytest.mark.asyncio
    async def test_filters_by_stock_code(self) -> None:
        coll = _make_records_collection()
        service = MongoDBService(_make_mock_db(coll))

        await service.query_analysis_records(stock_code="600519")
        query = coll.find.call_args[0][0]
        assert query == {"stock_code": "600519"}

    @pytest.mark.asyncio
    async def test_filters_by_trade_date(self) -> None:
        coll = _make_records_collection()
        service = MongoDBService(_make_mock_db(coll))

        await service.query_analysis_records(trade_date="2026-04-24")
        query = coll.find.call_args[0][0]
        assert query == {"trade_date": "2026-04-24"}

    @pytest.mark.asyncio
    async def test_combined_filter(self) -> None:
        coll = _make_records_collection()
        service = MongoDBService(_make_mock_db(coll))

        await service.query_analysis_records(
            stock_code="600519", trade_date="2026-04-24"
        )
        query = coll.find.call_args[0][0]
        assert query == {
            "stock_code": "600519",
            "trade_date": "2026-04-24",
        }

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self) -> None:
        coll = _make_records_collection(docs=[])
        service = MongoDBService(_make_mock_db(coll))

        result = await service.query_analysis_records()
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_is_bounded(self) -> None:
        coll = _make_records_collection()
        service = MongoDBService(_make_mock_db(coll))

        # Arbitrary large request must clamp to <=500
        await service.query_analysis_records(limit=100_000)
        assert coll.find.return_value.limit.called
        # Last positional arg of .limit() should be at most 500
        clamp_arg = coll.find.return_value.limit.call_args[0][0]
        assert clamp_arg <= 500

    @pytest.mark.asyncio
    async def test_sorts_by_created_at_desc(self) -> None:
        coll = _make_records_collection()
        service = MongoDBService(_make_mock_db(coll))

        await service.query_analysis_records()
        sort_call = coll.find.return_value.sort.call_args
        assert sort_call[0][0] == "created_at"


class TestGetAnalysisRecordById:
    """Tests for get_analysis_record_by_id method."""

    @pytest.mark.asyncio
    async def test_by_object_id(self) -> None:
        expected = _sample_record_dict()
        expected["_id"] = ObjectId(_OID_1)
        coll = _make_records_collection(find_one_doc=expected)
        service = MongoDBService(_make_mock_db(coll))

        result = await service.get_analysis_record_by_id(_OID_1)
        assert result is not None
        assert result["run_id"] == expected["run_id"]

    @pytest.mark.asyncio
    async def test_by_run_id_when_not_object_id(self) -> None:
        expected = _sample_record_dict(run_id="uuid-run")
        coll = _make_records_collection(find_one_doc=expected)
        service = MongoDBService(_make_mock_db(coll))

        result = await service.get_analysis_record_by_id("uuid-run")
        assert result is not None
        assert result["run_id"] == "uuid-run"
        coll.find_one.assert_called_with({"run_id": "uuid-run"})

    @pytest.mark.asyncio
    async def test_invalid_id_does_not_raise(self) -> None:
        """Garbage id string returns None, never raises ObjectId error."""
        coll = _make_records_collection(find_one_doc=None)
        service = MongoDBService(_make_mock_db(coll))

        result = await service.get_analysis_record_by_id("not-an-id!!!")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_returns_none(self) -> None:
        coll = _make_records_collection(find_one_doc=None)
        service = MongoDBService(_make_mock_db(coll))

        result = await service.get_analysis_record_by_id(str(ObjectId()))
        assert result is None
