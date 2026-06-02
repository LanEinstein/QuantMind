"""Unit tests for the broker_events / broker_snapshots persistence layer.

E-002 acceptance:

* append-only event chain produces monotonic sequences
* snapshot recovery loads latest checkpoint + replays newer events
* checksum mismatch fails-closed (no silent automatic recovery)
* event_type / schema_version validation rejects unknown rows
* canonical checksum is deterministic across runs (no float drift)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.broker.persistence import (
    BROKER_EVENT_SCHEMA_VERSION,
    BROKER_SNAPSHOT_SCHEMA_VERSION,
    BrokerEvent,
    BrokerEventStore,
    BrokerEventType,
    BrokerPersistenceError,
    BrokerSnapshot,
    BrokerSnapshotPosition,
    BrokerSnapshotStore,
    ChecksumMismatchError,
    canonical_state_payload,
    compute_snapshot_checksum,
    recover_state,
)

# ---------------------------------------------------------------------------
# In-memory test doubles for motor collections + sessions
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    """Minimal session that supports start_transaction / end_session.

    The transaction context is a no-op for these tests; the goal is
    only to verify that the store *uses* a session. Concurrency
    guarantees live in the integration test layer.
    """

    aborted: bool = False
    ended: bool = False

    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        try:
            yield
        except Exception:
            self.aborted = True
            raise

    async def commit_transaction(self) -> None:
        return None

    async def abort_transaction(self) -> None:
        self.aborted = True

    async def end_session(self) -> None:
        self.ended = True


@dataclass
class _FakeClient:
    sessions: list[_FakeSession] = field(default_factory=list)

    async def start_session(self) -> _FakeSession:
        s = _FakeSession()
        self.sessions.append(s)
        return s


class _FakeCursor:
    """Async iterable that also supports .sort() / .limit() builder calls."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        reverse = direction == -1
        self._docs = sorted(
            self._docs, key=lambda d: d.get(field, 0), reverse=reverse
        )
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
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
    """Append-only fake collection that records inserts in a list.

    Mirrors the duck-typed motor collection interface used by the
    persistence stores. ``insert_one`` accepts an optional ``session``
    parameter so we can verify the store passes one.
    """

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.last_session: _FakeSession | None = None

    async def insert_one(
        self,
        document: dict[str, Any],
        session: _FakeSession | None = None,
    ) -> None:
        self.last_session = session
        self.docs.append(dict(document))

    def find(
        self,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                threshold = gt["$gt"]
                rows = [r for r in rows if r.get("sequence", 0) > threshold]
        return _FakeCursor(rows)

    async def find_one(
        self, filter: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = list(self.docs)
        if filter:
            rows = [
                r
                for r in rows
                if all(r.get(k) == v for k, v in filter.items())
            ]
        return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(seconds: int) -> datetime:
    """Stable timestamp factory — offsets seconds from a fixed UTC anchor."""
    return datetime(2026, 5, 15, 9, 30, tzinfo=UTC).replace(second=seconds % 60)


def _seed_snapshot(
    *,
    sequence: int = 0,
    cash: float = 1_000_000.0,
    positions: tuple[BrokerSnapshotPosition, ...] = (),
    initial_capital: float = 1_000_000.0,
) -> BrokerSnapshot:
    checksum = compute_snapshot_checksum(
        cash, 0.0, initial_capital, positions
    )
    return BrokerSnapshot(
        created_at=_ts(0),
        trade_date="2026-05-15",
        last_event_sequence=sequence,
        cash=cash,
        frozen_cash=0.0,
        initial_capital=initial_capital,
        positions=positions,
        checksum=checksum,
    )


# ---------------------------------------------------------------------------
# BrokerEvent / BrokerSnapshot schema validation
# ---------------------------------------------------------------------------


class TestBrokerEventSchema:
    def test_event_requires_positive_sequence(self) -> None:
        with pytest.raises(Exception):
            BrokerEvent(
                sequence=0,  # ge=1 — must reject
                occurred_at=_ts(0),
                event_type=BrokerEventType.ACCOUNT_INITIALIZED,
            )

    def test_event_rejects_mismatched_schema_version(self) -> None:
        with pytest.raises(Exception, match="schema_version"):
            BrokerEvent(
                sequence=1,
                occurred_at=_ts(0),
                schema_version=99,
                event_type=BrokerEventType.ACCOUNT_INITIALIZED,
            )

    def test_event_round_trip(self) -> None:
        e = BrokerEvent(
            sequence=42,
            occurred_at=_ts(0),
            event_type=BrokerEventType.ORDER_FILLED,
            order_id="ord-123",
            payload={"code": "600519", "volume": 100, "fill_price": 1800.5},
        )
        dump = e.model_dump(mode="python")
        e2 = BrokerEvent.model_validate(dump)
        assert e2.sequence == 42
        assert e2.event_type is BrokerEventType.ORDER_FILLED
        assert e2.payload["code"] == "600519"


class TestBrokerSnapshotSchema:
    def test_snapshot_rejects_bad_checksum_shape(self) -> None:
        with pytest.raises(Exception, match="checksum"):
            BrokerSnapshot(
                created_at=_ts(0),
                trade_date="2026-05-15",
                last_event_sequence=0,
                cash=1.0,
                frozen_cash=0.0,
                initial_capital=1.0,
                checksum="not-hex",  # regex demands 16 lowercase hex
            )

    def test_snapshot_rejects_duplicate_position_codes(self) -> None:
        positions = (
            BrokerSnapshotPosition(
                code="600519", volume=100, today_bought_volume=0, cost_price=1.0
            ),
            BrokerSnapshotPosition(
                code="600519", volume=200, today_bought_volume=0, cost_price=2.0
            ),
        )
        checksum = compute_snapshot_checksum(1.0, 0.0, 1.0, positions)
        with pytest.raises(Exception, match="duplicate"):
            BrokerSnapshot(
                created_at=_ts(0),
                trade_date="2026-05-15",
                last_event_sequence=0,
                cash=1.0,
                frozen_cash=0.0,
                initial_capital=1.0,
                positions=positions,
                checksum=checksum,
            )

    def test_snapshot_position_rejects_today_volume_exceeding_total(
        self,
    ) -> None:
        with pytest.raises(Exception, match="today_bought_volume"):
            BrokerSnapshotPosition(
                code="600519",
                volume=100,
                today_bought_volume=200,
                cost_price=1.0,
            )


# ---------------------------------------------------------------------------
# Checksum determinism
# ---------------------------------------------------------------------------


class TestChecksum:
    def test_checksum_is_deterministic(self) -> None:
        positions = (
            BrokerSnapshotPosition(
                code="600519", volume=100, today_bought_volume=0, cost_price=1800.5
            ),
            BrokerSnapshotPosition(
                code="000001", volume=200, today_bought_volume=0, cost_price=12.5
            ),
        )
        h1 = compute_snapshot_checksum(123.45, 0.0, 1_000_000.0, positions)
        h2 = compute_snapshot_checksum(123.45, 0.0, 1_000_000.0, positions)
        assert h1 == h2

    def test_checksum_independent_of_position_order(self) -> None:
        positions_a = (
            BrokerSnapshotPosition(
                code="600519", volume=100, today_bought_volume=0, cost_price=1800.5
            ),
            BrokerSnapshotPosition(
                code="000001", volume=200, today_bought_volume=0, cost_price=12.5
            ),
        )
        positions_b = tuple(reversed(positions_a))
        h_a = compute_snapshot_checksum(0.0, 0.0, 1.0, positions_a)
        h_b = compute_snapshot_checksum(0.0, 0.0, 1.0, positions_b)
        assert h_a == h_b

    def test_checksum_changes_when_cash_changes(self) -> None:
        h1 = compute_snapshot_checksum(100.0, 0.0, 1_000_000.0, ())
        h2 = compute_snapshot_checksum(100.01, 0.0, 1_000_000.0, ())
        assert h1 != h2

    def test_canonical_state_payload_rounds_floats(self) -> None:
        payload = canonical_state_payload(
            100.000_001, 0.0, 1_000_000.0, ()
        )
        # cash rounded to 4dp
        assert payload["cash"] == 100.0
        assert payload["frozen_cash"] == 0.0


# ---------------------------------------------------------------------------
# Event store transactional append
# ---------------------------------------------------------------------------


class TestBrokerEventStore:
    @pytest.mark.asyncio
    async def test_append_assigns_monotonic_sequence(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        e1 = await store.append(
            event_type=BrokerEventType.ACCOUNT_INITIALIZED,
            occurred_at=_ts(0),
            payload={"cash": 1_000_000.0},
        )
        e2 = await store.append(
            event_type=BrokerEventType.ORDER_PLACED,
            occurred_at=_ts(1),
            order_id="ord-1",
            payload={"direction": "BUY", "frozen_amount": 1000.0},
        )
        assert e1.sequence == 1
        assert e2.sequence == 2
        assert coll.docs[0]["sequence"] == 1
        assert coll.docs[1]["sequence"] == 2
        # session lifecycle ran for both calls
        assert all(s.ended for s in client.sessions)

    @pytest.mark.asyncio
    async def test_append_many_uses_single_transaction(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        events = await store.append_many(
            [
                (
                    BrokerEventType.ACCOUNT_INITIALIZED,
                    _ts(0),
                    None,
                    None,
                    None,
                    {"cash": 1_000_000.0},
                ),
                (
                    BrokerEventType.ORDER_PLACED,
                    _ts(1),
                    "ord-1",
                    None,
                    None,
                    {"direction": "BUY", "frozen_amount": 1000.0},
                ),
                (
                    BrokerEventType.ORDER_FILLED,
                    _ts(2),
                    "ord-1",
                    "trd-1",
                    None,
                    {
                        "direction": "BUY",
                        "code": "600519",
                        "volume": 100,
                        "fill_price": 1800.0,
                        "frozen_amount": 1000.0,
                        "commission": 5.0,
                        "stamp_tax": 0.0,
                        "transfer_fee": 0.0,
                    },
                ),
            ]
        )
        assert tuple(e.sequence for e in events) == (1, 2, 3)
        assert len(client.sessions) == 1

    @pytest.mark.asyncio
    async def test_append_many_empty_short_circuits(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        out = await store.append_many([])
        assert out == ()
        assert not client.sessions  # no transaction opened

    @pytest.mark.asyncio
    async def test_stream_since_returns_in_sequence_order(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        for s in (3, 1, 2):
            event = BrokerEvent(
                sequence=s,
                occurred_at=_ts(s),
                event_type=BrokerEventType.DAY_ADVANCED,
            )
            doc = event.model_dump(mode="python")
            doc["event_id"] = str(event.event_id)
            coll.docs.append(doc)

        collected: list[int] = []
        async for ev in store.stream_since(0):
            collected.append(ev.sequence)
        assert collected == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_stream_since_filters_threshold(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        for s in (1, 2, 3):
            event = BrokerEvent(
                sequence=s,
                occurred_at=_ts(s),
                event_type=BrokerEventType.DAY_ADVANCED,
            )
            doc = event.model_dump(mode="python")
            doc["event_id"] = str(event.event_id)
            coll.docs.append(doc)

        collected = [ev.sequence async for ev in store.stream_since(2)]
        assert collected == [3]

    @pytest.mark.asyncio
    async def test_stream_since_rehydrates_strenum_event_type(self) -> None:
        # Real Mongo/BSON has no enum type: a StrEnum round-trips as a plain
        # str, which strict-mode BrokerEvent validation rejects. The in-memory
        # fake normally preserves the enum object (hiding the bug), so this test
        # deliberately downgrades event_type to a str to mimic real Mongo.
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        event = BrokerEvent(
            sequence=1,
            occurred_at=_ts(1),
            event_type=BrokerEventType.MODE_SWITCH_RESET,
        )
        doc = event.model_dump(mode="python")
        doc["event_id"] = str(event.event_id)
        doc["event_type"] = "mode_switch_reset"  # Mongo str downgrade
        coll.docs.append(doc)

        out = [ev async for ev in store.stream_since(0)]
        assert len(out) == 1
        assert out[0].event_type is BrokerEventType.MODE_SWITCH_RESET

    @pytest.mark.asyncio
    async def test_stream_since_corrupt_event_type_raises(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerEventStore(client, coll)

        event = BrokerEvent(
            sequence=1,
            occurred_at=_ts(1),
            event_type=BrokerEventType.DAY_ADVANCED,
        )
        doc = event.model_dump(mode="python")
        doc["event_id"] = str(event.event_id)
        doc["event_type"] = "not_a_real_event_type"
        coll.docs.append(doc)

        with pytest.raises(BrokerPersistenceError, match="corrupt event_type"):
            [ev async for ev in store.stream_since(0)]


class TestBrokerSnapshotStore:
    @pytest.mark.asyncio
    async def test_append_writes_snapshot(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerSnapshotStore(client, coll)

        snap = _seed_snapshot()
        out = await store.append(snap)
        assert out.snapshot_id == snap.snapshot_id
        assert len(coll.docs) == 1
        assert coll.docs[0]["schema_version"] == BROKER_SNAPSHOT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_read_latest_returns_highest_sequence(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerSnapshotStore(client, coll)

        snap_a = _seed_snapshot(sequence=10)
        snap_b = _seed_snapshot(sequence=42)
        await store.append(snap_a)
        await store.append(snap_b)

        latest = await store.read_latest()
        assert latest is not None
        assert latest.last_event_sequence == 42

    @pytest.mark.asyncio
    async def test_read_latest_returns_none_for_empty(self) -> None:
        store = BrokerSnapshotStore(_FakeClient(), _FakeCollection())
        assert await store.read_latest() is None

    @pytest.mark.asyncio
    async def test_read_latest_rehydrates_bson_list_positions(self) -> None:
        # Real Mongo/BSON has no tuple type: BrokerSnapshot.positions (a
        # tuple) round-trips through BSON as an array, so on read it comes
        # back a list, which strict-mode validation rejects. The in-memory
        # _FakeCollection preserves the tuple (hiding the bug), so this test
        # deliberately downgrades positions to a list to mimic a real Mongo
        # read — the same failure that refused broker boot (fail-closed
        # recover_state) whenever the latest snapshot held open positions.
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerSnapshotStore(client, coll)

        positions = (
            BrokerSnapshotPosition(
                code="600519", volume=100, today_bought_volume=0, cost_price=1500.0
            ),
            BrokerSnapshotPosition(
                code="000001", volume=200, today_bought_volume=0, cost_price=12.5
            ),
        )
        snap = _seed_snapshot(sequence=7, positions=positions)
        doc = snap.model_dump(mode="python")
        doc["snapshot_id"] = str(snap.snapshot_id)
        doc["positions"] = list(doc["positions"])  # Mongo array downgrade
        coll.docs.append(doc)

        latest = await store.read_latest()
        assert latest is not None
        assert isinstance(latest.positions, tuple)
        assert {p.code for p in latest.positions} == {"600519", "000001"}
        assert latest.last_event_sequence == 7


# ---------------------------------------------------------------------------
# Recovery integration — snapshot + replay
# ---------------------------------------------------------------------------


class TestRecovery:
    @pytest.mark.asyncio
    async def test_recover_returns_initial_state_when_no_snapshot(
        self,
    ) -> None:
        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        assert state.cash == 1_000_000.0
        assert state.frozen_cash == 0.0
        assert state.positions == {}
        assert state.last_sequence == 0
        assert state.events_replayed == 0

    @pytest.mark.asyncio
    async def test_recover_replays_events_on_top_of_snapshot(self) -> None:
        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        # Snapshot: 100 shares of 600519 @ 1800.0 cost; cash 820_000.
        positions = (
            BrokerSnapshotPosition(
                code="600519", volume=100, today_bought_volume=0, cost_price=1800.0
            ),
        )
        await ss.append(
            _seed_snapshot(sequence=5, cash=820_000.0, positions=positions)
        )

        # One newer BUY fill for 200 shares @ 12.0 of 000001.
        await es.append_many(
            [
                (
                    BrokerEventType.ORDER_PLACED,
                    _ts(10),
                    "ord-x",
                    None,
                    None,
                    {"direction": "BUY", "frozen_amount": 2_405.0},
                ),
                (
                    BrokerEventType.ORDER_FILLED,
                    _ts(11),
                    "ord-x",
                    "trd-x",
                    None,
                    {
                        "direction": "BUY",
                        "code": "000001",
                        "volume": 200,
                        "fill_price": 12.0,
                        "frozen_amount": 2_405.0,
                        "commission": 5.0,
                        "stamp_tax": 0.0,
                        "transfer_fee": 0.0,
                    },
                ),
            ]
        )
        # Patch sequences manually: snapshot says last_event_sequence=5
        # but the FakeClient assigns 1/2 for the append_many — replay
        # filter `sequence > 5` would drop them. Re-write the docs with
        # synthesised sequences > 5 so we exercise the replay path.
        for offset, doc in enumerate(event_coll.docs, start=6):
            doc["sequence"] = offset

        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        assert state.last_sequence == 7
        assert state.events_replayed == 2
        # Old 600519 carried through the snapshot
        assert state.positions["600519"].volume == 100
        # New 000001 from the replayed events
        assert state.positions["000001"].volume == 200
        # Cash: 820_000 - frozen 2405 = 817_595 after PLACED; then
        # actual_cost = 200*12 + 5 = 2405 → delta = 0 → cash stays at
        # 817_595. frozen_cash returns to 0.
        assert state.cash == pytest.approx(817_595.0)
        assert state.frozen_cash == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_recover_raises_on_checksum_mismatch(self) -> None:
        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        good = _seed_snapshot(cash=1.0)
        # Replace the stored checksum with garbage.
        doc = good.model_dump(mode="python")
        doc["snapshot_id"] = str(good.snapshot_id)
        doc["checksum"] = "0" * 16
        snap_coll.docs.append(doc)

        with pytest.raises(ChecksumMismatchError, match="checksum"):
            await recover_state(es, ss, initial_capital=1_000_000.0)

    @pytest.mark.asyncio
    async def test_recover_handles_sell_after_buy(self) -> None:
        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        await es.append_many(
            [
                (
                    BrokerEventType.ACCOUNT_INITIALIZED,
                    _ts(0),
                    None,
                    None,
                    None,
                    {"cash": 1_000_000.0, "initial_capital": 1_000_000.0},
                ),
                (
                    BrokerEventType.ORDER_PLACED,
                    _ts(1),
                    "buy-1",
                    None,
                    None,
                    {"direction": "BUY", "frozen_amount": 180_100.0},
                ),
                (
                    BrokerEventType.ORDER_FILLED,
                    _ts(2),
                    "buy-1",
                    "trd-1",
                    None,
                    {
                        "direction": "BUY",
                        "code": "600519",
                        "volume": 100,
                        "fill_price": 1800.0,
                        "frozen_amount": 180_100.0,
                        "commission": 54.0,
                        "stamp_tax": 0.0,
                        "transfer_fee": 0.0,
                    },
                ),
                (
                    BrokerEventType.DAY_ADVANCED,
                    _ts(3),
                    None,
                    None,
                    None,
                    {},
                ),
                (
                    BrokerEventType.ORDER_FILLED,
                    _ts(4),
                    "sell-1",
                    "trd-2",
                    None,
                    {
                        "direction": "SELL",
                        "code": "600519",
                        "volume": 100,
                        "fill_price": 1810.0,
                        "frozen_amount": 0.0,
                        "commission": 54.0,
                        "stamp_tax": 181.0,
                        "transfer_fee": 0.0,
                    },
                ),
            ]
        )

        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        # Round-trip: bought + sold the same 100 shares -> position cleared.
        assert "600519" not in state.positions
        assert state.events_replayed == 5
        assert state.last_sequence == 5

    @pytest.mark.asyncio
    async def test_recover_sell_without_position_fails_closed(self) -> None:
        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        await es.append(
            event_type=BrokerEventType.ORDER_FILLED,
            occurred_at=_ts(0),
            order_id="bad",
            trade_id="bad",
            payload={
                "direction": "SELL",
                "code": "600519",
                "volume": 100,
                "fill_price": 1.0,
                "frozen_amount": 0.0,
                "commission": 0.0,
                "stamp_tax": 0.0,
                "transfer_fee": 0.0,
            },
        )

        from backend.broker.persistence.recovery import RecoveryError

        with pytest.raises(RecoveryError, match="SELL fill"):
            await recover_state(es, ss, initial_capital=1_000_000.0)

    @pytest.mark.asyncio
    async def test_recover_v2_execution_report_uses_fee_inclusive_basis(
        self,
    ) -> None:
        # P0-4-amendment-2026-05-27 §2.4 — a v2 EXECUTION_REPORT_APPLIED
        # event carries the fee-inclusive per-share cost in positions_delta
        # so the rebuilt position cost basis matches the live broker.
        client = _FakeClient()
        es = BrokerEventStore(client, _FakeCollection())
        ss = BrokerSnapshotStore(client, _FakeCollection())
        await es.append(
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            occurred_at=_ts(0),
            order_id="ext-1",
            trade_id="trd-1",
            payload={
                "report_schema_version": 2,
                "commission": 27.0,
                "stamp_tax": 0.0,
                "transfer_fee": 0.0,
                "net": 180_027.0,
                "cash_delta": -180_027.0,
                "positions_delta": [
                    {
                        "code": "600519",
                        "volume_delta": 100,
                        "cost_price": 1800.27,
                    }
                ],
            },
        )
        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        assert state.cash == pytest.approx(1_000_000.0 - 180_027.0)
        assert state.positions["600519"].cost_price == pytest.approx(1800.27)

    @pytest.mark.asyncio
    async def test_recover_v2_event_missing_breakdown_fails_closed(
        self,
    ) -> None:
        client = _FakeClient()
        es = BrokerEventStore(client, _FakeCollection())
        ss = BrokerSnapshotStore(client, _FakeCollection())
        await es.append(
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            occurred_at=_ts(0),
            payload={
                # v2 row corrupted — missing the derived friction breakdown.
                "report_schema_version": 2,
                "cash_delta": -180_027.0,
                "positions_delta": [],
            },
        )
        from backend.broker.persistence.recovery import RecoveryError

        with pytest.raises(RecoveryError, match="v2 EXECUTION_REPORT"):
            await recover_state(es, ss, initial_capital=1_000_000.0)

    @pytest.mark.asyncio
    async def test_recover_v2_buy_missing_cost_price_fails_closed(
        self,
    ) -> None:
        # A v2 BUY leg without cost_price would rebuild the position at
        # cost_price 0.0 — the guard refuses it (fail-closed) so a silently
        # wrong fee-inclusive basis never reaches MTM/PnL.
        client = _FakeClient()
        es = BrokerEventStore(client, _FakeCollection())
        ss = BrokerSnapshotStore(client, _FakeCollection())
        await es.append(
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            occurred_at=_ts(0),
            payload={
                "report_schema_version": 2,
                "commission": 27.0,
                "net": 180_027.0,
                "cash_delta": -180_027.0,
                "positions_delta": [
                    {"code": "600519", "volume_delta": 100},  # no cost_price
                ],
            },
        )
        from backend.broker.persistence.recovery import RecoveryError

        with pytest.raises(RecoveryError, match="BUY leg missing cost_price"):
            await recover_state(es, ss, initial_capital=1_000_000.0)

    @pytest.mark.asyncio
    async def test_recover_legacy_v1_execution_report_without_version(
        self,
    ) -> None:
        # A pre-amendment row has no report_schema_version key → treated
        # as v1 (legacy), replayed from the stored deltas with the raw
        # fill price as the cost basis. Back-compat must not break.
        client = _FakeClient()
        es = BrokerEventStore(client, _FakeCollection())
        ss = BrokerSnapshotStore(client, _FakeCollection())
        await es.append(
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            occurred_at=_ts(0),
            payload={
                "cash_delta": -180_005.0,
                "positions_delta": [
                    {
                        "code": "600519",
                        "volume_delta": 100,
                        "cost_price": 1800.0,
                    }
                ],
            },
        )
        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        assert state.cash == pytest.approx(1_000_000.0 - 180_005.0)
        assert state.positions["600519"].cost_price == pytest.approx(1800.0)


# ---------------------------------------------------------------------------
# Snapshot version monotonicity (red line 5)
# ---------------------------------------------------------------------------


class TestSchemaVersionEnforcement:
    @pytest.mark.asyncio
    async def test_snapshot_store_rejects_non_canonical_version(self) -> None:
        client = _FakeClient()
        coll = _FakeCollection()
        store = BrokerSnapshotStore(client, coll)

        # Construct a snapshot then mutate the (already-frozen) dump
        # before round-tripping; the store path normally goes via
        # ``snapshot.schema_version`` directly, so we simulate the
        # rejection at the store entry.
        snap = _seed_snapshot()
        # frozen model — must use model_copy
        with pytest.raises(BrokerPersistenceError, match="schema_version"):
            await store.append(snap.model_copy(update={"schema_version": 99}))

    def test_event_module_constants_match_models(self) -> None:
        assert BROKER_EVENT_SCHEMA_VERSION == 1
        assert BROKER_SNAPSHOT_SCHEMA_VERSION == 1
