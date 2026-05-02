"""Unit + integration tests for backend.services.shadow_recorder.

Covers:
* ShadowDecisionLeg / ShadowDecisionEntry validation (action, confidence,
  latency, tz-naive datetimes)
* record_shadow_decision happy path + Mongo error fail-soft
* query_shadow_decisions UTC cutoff, _id stripping, error fail-soft
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.shadow_recorder import (
    SHADOW_COLLECTION,
    ShadowDecisionEntry,
    ShadowDecisionLeg,
    query_shadow_decisions,
    record_shadow_decision,
)

# ----------------------------------------------------------------------
# Shared fixtures
# ----------------------------------------------------------------------


def _make_leg(
    *,
    action: str = "买入",
    confidence: float = 0.7,
    model: str = "kimi-k2.6",
    latency_ms: float = 1234.5,
    escalated: bool = False,
    parse_ok: bool = True,
) -> ShadowDecisionLeg:
    return ShadowDecisionLeg(
        action=action,
        confidence=confidence,
        model=model,
        latency_ms=latency_ms,
        escalated=escalated,
        parse_ok=parse_ok,
    )


def _make_entry(
    run_id: str = "run-1",
    stock_code: str = "600519",
    trade_date: str = "2026-05-02",
    created_at: datetime.datetime | None = None,
    baseline: ShadowDecisionLeg | None = None,
    routed: ShadowDecisionLeg | None = None,
) -> ShadowDecisionEntry:
    return ShadowDecisionEntry(
        run_id=run_id,
        stock_code=stock_code,
        trade_date=trade_date,
        created_at=created_at
        or datetime.datetime.now(tz=datetime.UTC),
        baseline=baseline or _make_leg(),
        routed=routed or _make_leg(model="qwen3.6-plus", escalated=False),
    )


def _make_mongo() -> tuple[MagicMock, MagicMock]:
    """Return (service-shaped mock, collection mock).

    The recorder reaches into ``mongodb._db[SHADOW_COLLECTION]`` directly
    so we mirror that shape: the service exposes ``_db`` as a dict-like
    Mongo database object.
    """
    coll = MagicMock()
    coll.update_one = AsyncMock()
    db = MagicMock()
    db.__getitem__.return_value = coll
    service = MagicMock()
    service._db = db
    return service, coll


# ----------------------------------------------------------------------
# Group 1: schema validation
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestShadowDecisionLeg:
    def test_happy_path(self) -> None:
        leg = _make_leg()
        assert leg.action == "买入"
        assert leg.confidence == 0.7

    @pytest.mark.parametrize("bad", ["买", "buy", "", "持仓"])
    def test_invalid_action_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _make_leg(action=bad)

    @pytest.mark.parametrize(
        "bad",
        [-0.1, 1.1, float("nan"), float("inf"), -float("inf")],
    )
    def test_invalid_confidence_rejected(self, bad: float) -> None:
        with pytest.raises(ValueError):
            _make_leg(confidence=bad)

    def test_bool_confidence_rejected(self) -> None:
        # Python bool is an int subclass; make sure we don't silently
        # accept True/False as confidence.
        with pytest.raises(ValueError):
            _make_leg(confidence=True)  # type: ignore[arg-type]

    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_leg(latency_ms=-1.0)

    def test_zero_latency_accepted(self) -> None:
        leg = _make_leg(latency_ms=0.0)
        assert leg.latency_ms == 0.0

    def test_inf_latency_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_leg(latency_ms=float("inf"))


@pytest.mark.unit
class TestShadowDecisionEntry:
    def test_happy_path(self) -> None:
        entry = _make_entry()
        assert entry.run_id == "run-1"

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_entry(run_id="")

    def test_empty_stock_code_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_entry(stock_code="")

    def test_empty_trade_date_rejected(self) -> None:
        with pytest.raises(ValueError):
            _make_entry(trade_date="")

    def test_naive_created_at_rejected(self) -> None:
        # Tests the timezone safety net P5B-T03 R6 surfaced.
        with pytest.raises(ValueError):
            _make_entry(created_at=datetime.datetime(2026, 5, 2, 12, 0, 0))

    def test_to_document_round_trip(self) -> None:
        entry = _make_entry()
        doc = entry.to_document()
        assert doc["run_id"] == entry.run_id
        assert doc["baseline"]["action"] == entry.baseline.action
        assert doc["routed"]["confidence"] == entry.routed.confidence
        assert doc["created_at"] == entry.created_at  # BSON Date, not str


# ----------------------------------------------------------------------
# Group 2: record_shadow_decision
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRecordShadowDecision:
    async def test_happy_path_upserts_by_run_id(self) -> None:
        service, coll = _make_mongo()
        entry = _make_entry()
        ok = await record_shadow_decision(service, entry)
        assert ok is True
        coll.update_one.assert_awaited_once()
        args, kwargs = coll.update_one.call_args
        assert args[0] == {"run_id": "run-1"}
        assert "$set" in args[1]
        # upsert kwarg must be true so re-runs replace, not duplicate.
        assert kwargs["upsert"] is True

    async def test_collection_name(self) -> None:
        service, _ = _make_mongo()
        entry = _make_entry()
        await record_shadow_decision(service, entry)
        # __getitem__ called with the canonical collection name.
        service._db.__getitem__.assert_called_with(SHADOW_COLLECTION)

    async def test_mongo_error_returns_false(self) -> None:
        service, coll = _make_mongo()
        coll.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        ok = await record_shadow_decision(service, _make_entry())
        assert ok is False

    async def test_idempotent_second_call(self) -> None:
        service, coll = _make_mongo()
        entry = _make_entry()
        await record_shadow_decision(service, entry)
        await record_shadow_decision(service, entry)
        # Both calls hit Mongo with the same upsert key — the upsert
        # contract is what guarantees idempotency, not the call count.
        assert coll.update_one.await_count == 2


# ----------------------------------------------------------------------
# Group 3: query_shadow_decisions
# ----------------------------------------------------------------------


class _AsyncIterator:
    """Minimal async iterator for the cursor mock."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIterator:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


@pytest.mark.unit
class TestQueryShadowDecisions:
    async def test_invalid_days_rejected(self) -> None:
        service, _ = _make_mongo()
        with pytest.raises(ValueError):
            await query_shadow_decisions(service, days=0)

    async def test_drops_object_id(self) -> None:
        service, coll = _make_mongo()
        coll.find = MagicMock(
            return_value=_AsyncIterator(
                [
                    {
                        "_id": "abc123",
                        "run_id": "r1",
                        "stock_code": "600519",
                        "trade_date": "2026-05-02",
                    },
                ]
            )
        )
        docs = await query_shadow_decisions(service, days=7)
        assert len(docs) == 1
        assert "_id" not in docs[0]
        assert docs[0]["run_id"] == "r1"

    async def test_uses_utc_cutoff(self) -> None:
        service, coll = _make_mongo()
        coll.find = MagicMock(return_value=_AsyncIterator([]))
        now = datetime.datetime(2026, 5, 9, 12, 0, tzinfo=datetime.UTC)
        await query_shadow_decisions(service, days=7, now=now)
        coll.find.assert_called_once()
        query = coll.find.call_args[0][0]
        assert "created_at" in query
        cutoff = query["created_at"]["$gte"]
        # 7 days back from a UTC ``now`` is exactly 2026-05-02
        assert cutoff == datetime.datetime(
            2026, 5, 2, 12, 0, tzinfo=datetime.UTC
        )

    async def test_naive_now_normalised_to_utc(self) -> None:
        service, coll = _make_mongo()
        coll.find = MagicMock(return_value=_AsyncIterator([]))
        # An aware now in a non-UTC tz must still be normalised.
        tz_shanghai = datetime.timezone(datetime.timedelta(hours=8))
        now = datetime.datetime(2026, 5, 9, 20, 0, tzinfo=tz_shanghai)
        await query_shadow_decisions(service, days=7, now=now)
        cutoff = coll.find.call_args[0][0]["created_at"]["$gte"]
        # 2026-05-09 20:00+08 == 2026-05-09 12:00Z, minus 7 days
        assert cutoff == datetime.datetime(
            2026, 5, 2, 12, 0, tzinfo=datetime.UTC
        )

    async def test_mongo_error_returns_empty(self) -> None:
        service, coll = _make_mongo()
        coll.find = MagicMock(side_effect=RuntimeError("mongo down"))
        docs = await query_shadow_decisions(service, days=1)
        assert docs == []
