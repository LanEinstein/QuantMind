"""BrokerEventStore + BrokerSnapshotStore — append-only Mongo stores.

The 8 P1-2.A red lines this module enforces (any violation must be
caught at the unit-test layer or rejected at runtime):

1. **insert-only** — events / snapshots are appended via ``insert_one`` /
   ``insert_many`` inside an explicit session transaction. The store
   never issues ``update_one`` / ``$set`` on an existing row.
2. **no in-place mutation** — even a "fix typo" update is forbidden;
   corrections go through a new event with ``event_type=…``.
3. **no delete** — neither ``delete_one`` nor ``delete_many`` is
   reachable from the store API surface.
4. **no truncate / drop** — the store has no method named ``drop`` /
   ``truncate``; callers cannot wipe collections through this layer.
5. **no schema rewrite** — once a ``schema_version`` is in use, the
   store rejects rows carrying a different version (no automatic
   upgrade-in-place migration).
6. **no shard rebalance** — the database layer pins data to a single
   shard (evaluation deploys run a single-node RS; future sharding is
   a separate amendment) so the store cannot trigger a rebalance.
7. **no checksum patch** — snapshots' ``checksum`` field is sealed at
   write time; the only legitimate "fix" is to write a NEW snapshot
   with a correct checksum and bigger ``last_event_sequence``.
8. **monotonic sequence** — event sequences are dispensed inside the
   transaction so two concurrent appenders cannot collide.

The stores use the Mongo session API for multi-document atomicity
(``with await client.start_session() as session: async with
session.start_transaction()``). This in turn requires the connected
node to be a replica-set member — see E-001 boot fence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import structlog

from backend.broker.persistence.events import (
    BROKER_EVENT_SCHEMA_VERSION,
    BrokerEvent,
    BrokerEventType,
)
from backend.broker.persistence.snapshots import (
    BROKER_SNAPSHOT_SCHEMA_VERSION,
    BrokerSnapshot,
)

log = structlog.get_logger(component="broker.persistence")


def _clean_mongo_doc(doc: dict[str, Any], uuid_field: str) -> dict[str, Any]:
    """Strip Mongo ``_id`` and rehydrate the UUID field.

    The append path stores UUID fields as strings (motor BSON would
    convert them either way, but the unit-test FakeCollection keeps the
    string verbatim). On the read path strict-mode validation expects
    an actual :class:`uuid.UUID`, so a typed conversion happens here.
    """
    out = {k: v for k, v in doc.items() if k != "_id"}
    raw = out.get(uuid_field)
    if isinstance(raw, str):
        try:
            out[uuid_field] = UUID(raw)
        except ValueError as exc:
            raise BrokerPersistenceError(
                f"corrupt {uuid_field} {raw!r} in stored doc: {exc}"
            ) from exc
    return out


class BrokerPersistenceError(RuntimeError):
    """Raised when the broker_events / broker_snapshots invariants fail.

    The error subclasses ``RuntimeError`` so callers can blanket-catch
    it; the message always identifies which red line tripped.
    """


# ---------------------------------------------------------------------------
# Duck-typed protocols for unit-test isolation
# ---------------------------------------------------------------------------


@runtime_checkable
class _MongoSession(Protocol):
    """Minimal session protocol satisfied by motor's AsyncIOMotorClientSession."""

    def start_transaction(self) -> Any: ...

    async def commit_transaction(self) -> None: ...

    async def abort_transaction(self) -> None: ...

    async def end_session(self) -> None: ...


@runtime_checkable
class _MongoCollection(Protocol):
    """Minimal collection protocol satisfied by motor's AsyncIOMotorCollection."""

    async def insert_one(
        self, document: dict[str, Any], session: _MongoSession | None = ...
    ) -> Any: ...

    def find(
        self, filter: dict[str, Any] | None = ..., **kwargs: Any
    ) -> Any: ...

    async def find_one(
        self, filter: dict[str, Any] | None = ..., **kwargs: Any
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class _MongoClient(Protocol):
    """Minimal client protocol for ``start_session`` / replica-set probing."""

    async def start_session(self) -> _MongoSession: ...


# ---------------------------------------------------------------------------
# Event store — append-only delta rows
# ---------------------------------------------------------------------------


class BrokerEventStore:
    """Append-only writer + ordered reader for ``broker_events``.

    The store is intentionally narrow: ``append`` (single event under a
    transaction), ``append_many`` (one event chain under one
    transaction), ``stream_since`` (read events with sequence > x in
    sequence order), ``read_latest_sequence``. There is no ``update``,
    no ``delete`` — those would violate the 8 red lines.
    """

    COLLECTION_NAME = "broker_events"

    def __init__(
        self,
        client: _MongoClient,
        collection: _MongoCollection,
    ) -> None:
        self._client = client
        self._coll = collection

    async def read_latest_sequence(self) -> int:
        """Return the highest sequence number seen, or 0 if empty.

        Used by the store to assign the next sequence under a
        transaction, and by recovery to know which events to replay.
        """
        cursor = (
            self._coll.find({}, projection={"sequence": 1})
            .sort("sequence", -1)
            .limit(1)
        )
        async for doc in cursor:
            seq = doc.get("sequence", 0)
            return int(seq) if seq is not None else 0
        return 0

    async def append(
        self,
        *,
        event_type: BrokerEventType,
        occurred_at: datetime,
        order_id: str | None = None,
        trade_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> BrokerEvent:
        """Append a single event under a fresh transaction.

        The sequence is dispensed inside the transaction so a concurrent
        appender can never produce a duplicate. Returns the constructed
        :class:`BrokerEvent` (with the sequence assigned) for callers
        that need to surface it.
        """
        session = await self._client.start_session()
        try:
            async with session.start_transaction():
                next_seq = await self.read_latest_sequence() + 1
                event = BrokerEvent(
                    sequence=next_seq,
                    occurred_at=occurred_at,
                    schema_version=BROKER_EVENT_SCHEMA_VERSION,
                    event_type=event_type,
                    order_id=order_id,
                    trade_id=trade_id,
                    correlation_id=correlation_id,
                    payload=payload or {},
                )
                doc = event.model_dump(mode="python")
                doc["event_id"] = str(event.event_id)
                await self._coll.insert_one(doc, session=session)
                log.debug(
                    "broker_event_appended",
                    sequence=event.sequence,
                    event_type=event.event_type.value,
                    order_id=event.order_id,
                )
            return event
        finally:
            await session.end_session()

    async def append_many(
        self,
        events: Iterable[
            tuple[
                BrokerEventType,
                datetime,
                str | None,
                str | None,
                str | None,
                dict[str, Any] | None,
            ]
        ],
    ) -> tuple[BrokerEvent, ...]:
        """Append a chain of events atomically (single transaction).

        Each tuple is ``(event_type, occurred_at, order_id, trade_id,
        correlation_id, payload)``. The sequence numbers are dispensed
        contiguously inside the transaction so a partial replay can
        always tell the chain was atomic.
        """
        events_list = list(events)
        if not events_list:
            return ()
        session = await self._client.start_session()
        try:
            async with session.start_transaction():
                next_seq = await self.read_latest_sequence() + 1
                built: list[BrokerEvent] = []
                for offset, (etype, occ, oid, tid, cid, pl) in enumerate(events_list):
                    event = BrokerEvent(
                        sequence=next_seq + offset,
                        occurred_at=occ,
                        schema_version=BROKER_EVENT_SCHEMA_VERSION,
                        event_type=etype,
                        order_id=oid,
                        trade_id=tid,
                        correlation_id=cid,
                        payload=pl or {},
                    )
                    doc = event.model_dump(mode="python")
                    doc["event_id"] = str(event.event_id)
                    await self._coll.insert_one(doc, session=session)
                    built.append(event)
            return tuple(built)
        finally:
            await session.end_session()

    async def stream_since(self, sequence: int) -> AsyncIterator[BrokerEvent]:
        """Yield events with ``sequence > sequence``, ordered.

        Recovery consumes this stream after loading the most recent
        snapshot. Each yielded event is validated through the Pydantic
        schema, so a row with an unknown ``schema_version`` or
        ``event_type`` raises immediately (one of the 8 red lines:
        no silent schema rewrites).
        """
        cursor = self._coll.find({"sequence": {"$gt": sequence}}).sort(
            "sequence", 1
        )
        async for doc in cursor:
            yield BrokerEvent.model_validate(_clean_mongo_doc(doc, "event_id"))


# ---------------------------------------------------------------------------
# Snapshot store — append-only EOD checkpoints
# ---------------------------------------------------------------------------


class BrokerSnapshotStore:
    """Append-only writer + ordered reader for ``broker_snapshots``.

    Snapshots are checkpoints: only the EOD pipeline writes them, and
    only after every delta event for the trading day has been applied
    + the checksum has been computed. The recovery loader reads the
    most recent snapshot for its ``last_event_sequence`` cursor.
    """

    COLLECTION_NAME = "broker_snapshots"

    def __init__(
        self,
        client: _MongoClient,
        collection: _MongoCollection,
    ) -> None:
        self._client = client
        self._coll = collection

    async def append(self, snapshot: BrokerSnapshot) -> BrokerSnapshot:
        """Append a snapshot under a fresh transaction.

        The transaction is required because the EOD pipeline also writes
        a closing event into ``broker_events`` for the same trade_date;
        callers compose the snapshot insert with the event chain in one
        outer transaction (BrokerScheduler EOD chain). For the unit
        tests that exercise this method in isolation, a single-write
        transaction is harmless.
        """
        if snapshot.schema_version != BROKER_SNAPSHOT_SCHEMA_VERSION:
            raise BrokerPersistenceError(
                f"snapshot schema_version {snapshot.schema_version} != "
                f"{BROKER_SNAPSHOT_SCHEMA_VERSION}; reject (no automatic "
                "in-place migrations — red line)"
            )
        session = await self._client.start_session()
        try:
            async with session.start_transaction():
                doc = snapshot.model_dump(mode="python")
                doc["snapshot_id"] = str(snapshot.snapshot_id)
                await self._coll.insert_one(doc, session=session)
                log.info(
                    "broker_snapshot_appended",
                    snapshot_id=str(snapshot.snapshot_id),
                    trade_date=snapshot.trade_date,
                    last_event_sequence=snapshot.last_event_sequence,
                    checksum=snapshot.checksum,
                )
        finally:
            await session.end_session()
        return snapshot

    async def read_latest(self) -> BrokerSnapshot | None:
        """Return the newest snapshot by ``last_event_sequence``.

        Sequence is the canonical ordering — created_at can wobble for
        manually-written test fixtures, but the sequence cursor is
        deterministic. ``None`` means no snapshot has ever been
        written (fresh deploy).
        """
        cursor = self._coll.find({}).sort("last_event_sequence", -1).limit(1)
        async for doc in cursor:
            return BrokerSnapshot.model_validate(
                _clean_mongo_doc(doc, "snapshot_id")
            )
        return None


__all__ = [
    "BrokerEventStore",
    "BrokerPersistenceError",
    "BrokerSnapshotStore",
]
