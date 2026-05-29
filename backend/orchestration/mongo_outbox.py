"""Mongo-backed durable OutboxRepository for feishu_interactive dispatch.

Why this exists: the in-memory OutboxRepository does not survive a process
restart. Feishu's server-side ``uuid`` dedup window is only ~1 hour, so a
restart more than one hour after a BUY was dispatched (but before it was
confirmed SENT) would re-send the same signal to the owner — a real double-buy
risk. This module replaces the in-memory store with a Mongo-backed claim table
whose ``_id = instruction_id`` unique index provides the same atomic
at-most-once guarantee across restarts.

Design: **mutable claim table** (NOT the append-only broker_events ledger).
Reclaim-after-definitive-send-failure requires DELETE (release only removes
PENDING claims); SENT rows are never deleted or re-sent — the SENT status is
terminal. The at-most-once invariant is preserved because:
  * ``try_claim`` is an insert_one — DuplicateKeyError means "already claimed".
  * ``release`` deletes only if status == PENDING (SENT rows survive).
  * ``mark_sent`` $sets status=SENT with upsert=True so it is idempotent.

StrEnum rehydration (U-D6b class of bug): real Mongo/BSON has no enum type, so
a StrEnum round-trips through Mongo as a plain str. Strict-mode Pydantic rejects
a plain str where an enum is expected, so the status field MUST be rehydrated
from str → OutboxStatus on every read path. The FakeCollection unit-test double
preserves the enum object in memory, masking this bug in tests — only a real
round-trip through Mongo exposes it (see U-D6b broker event recovery fix).

Naive→UTC datetime coercion: motor returns naive UTC datetimes from Mongo (BSON
stores UTC but strips the tzinfo). OutboxEntry consumers compare/display
datetimes and expect tz-aware values (tz=UTC), so the read path coerces naive
datetimes back to tz-aware UTC with ``.replace(tzinfo=UTC)``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from pymongo.errors import DuplicateKeyError

from backend.orchestration.instruction_dispatcher import (
    OutboxEntry,
    OutboxStatus,
)

log = structlog.get_logger(component="orchestration.mongo_outbox")

_COLLECTION_NAME = "instruction_outbox"
"""Mongo collection dedicated to the durable outbox claim table.

Using a dedicated collection (not broker_events) keeps the outbox mutation
semantics (upsert + delete) cleanly separated from the append-only broker
ledger's 8 red lines (P1-2.A).
"""


def _coerce_utc(dt: datetime | None) -> datetime | None:
    """Return a tz-aware UTC datetime; pass None through unchanged.

    Motor returns naive UTC datetimes from BSON. Any downstream consumer
    that compares or serialises the value expects tz-aware UTC, so coerce
    here rather than in every consumer.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _rehydrate_doc(doc: dict[str, Any]) -> OutboxEntry:
    """Build an OutboxEntry from a Mongo document.

    Two rehydration steps required after a real Mongo round-trip:
    1. **StrEnum**: BSON stores the string value, not the enum object.
       Strict consumers do ``status is OutboxStatus.SENT`` identity checks, so
       we MUST restore the OutboxStatus enum. An unrecognised value raises
       (mirrors _rehydrate_event_doc in broker/persistence/store.py).
    2. **Naive datetime → tz-aware UTC**: motor strips tzinfo on read.

    The ``_id`` field maps back to ``instruction_id``.
    """
    raw_status = doc["status"]
    if not isinstance(raw_status, OutboxStatus):
        try:
            status = OutboxStatus(raw_status)
        except ValueError as exc:
            raise ValueError(
                f"corrupt OutboxStatus {raw_status!r} in instruction_outbox doc: {exc}"
            ) from exc
    else:
        status = raw_status

    return OutboxEntry(
        instruction_id=doc["_id"],
        status=status,
        claimed_at=_coerce_utc(doc["claimed_at"]),  # type: ignore[arg-type]
        sent_at=_coerce_utc(doc.get("sent_at")),
        feishu_message_id=doc.get("feishu_message_id"),
    )


class MongoOutboxRepository:
    """Durable Mongo-backed implementation of the OutboxRepository Protocol.

    One document per instruction_id. The Mongo ``_id`` field stores the
    instruction_id directly, giving a free unique-key constraint that makes
    ``try_claim`` atomic without an extra index.

    All async methods mirror the async style of BrokerEventStore /
    BrokerSnapshotStore (motor ``await`` calls; no explicit transactions needed
    because each operation is a single-document atomic write).
    """

    COLLECTION_NAME = _COLLECTION_NAME

    def __init__(self, collection: Any) -> None:
        """Accept the motor (or Protocol-compatible) collection.

        Why we don't require the full Mongo client here: the outbox operations
        are all single-document inserts/updates/deletes — no multi-doc
        transactions are needed, so there is no need for a session. Simpler
        injection surface than BrokerEventStore.
        """
        self._coll = collection

    async def get(self, instruction_id: str) -> OutboxEntry | None:
        """Return the claim row for instruction_id, or None if unclaimed.

        Rehydrates StrEnum + naive datetime on the read path (see module
        docstring — the FakeCollection hides both bugs, real Mongo exposes them).
        """
        doc: dict[str, Any] | None = await self._coll.find_one(
            {"_id": instruction_id}
        )
        if doc is None:
            return None
        return _rehydrate_doc(doc)

    async def try_claim(self, instruction_id: str, *, at: datetime) -> bool:
        """Atomic at-most-once gate: insert a PENDING claim, return True iff new.

        Uses insert_one with ``_id = instruction_id``. Mongo's unique ``_id``
        index serialises concurrent inserts — the first succeeds and returns True;
        any duplicate raises DuplicateKeyError, which we catch and return False.
        The claim status is never changed by this path; a pre-existing SENT row
        is correctly detected as a duplicate (return False), preventing any
        attempt to re-send a delivered signal.
        """
        doc: dict[str, Any] = {
            "_id": instruction_id,
            "status": OutboxStatus.PENDING.value,
            "claimed_at": at,
            "sent_at": None,
            "feishu_message_id": None,
        }
        try:
            await self._coll.insert_one(doc)
            log.debug("outbox_claimed", instruction_id=instruction_id)
            return True
        except DuplicateKeyError:
            log.debug("outbox_claim_duplicate", instruction_id=instruction_id)
            return False

    async def release(self, instruction_id: str) -> None:
        """Delete a PENDING claim so a later dispatch can re-claim and re-send.

        Only deletes if status == PENDING. A SENT row is never deleted —
        failing closed against double-send (the SENT terminal invariant).
        Releasing an unknown id is also a no-op (delete_one matched_count == 0).

        Called only after a **definitive** API rejection (send.ok is False) — the
        message was never delivered. A transport exception (unknown delivery) is
        NOT released; the claim stays PENDING, forcing manual recovery rather than
        risking a blind re-send.
        """
        result = await self._coll.delete_one(
            {"_id": instruction_id, "status": OutboxStatus.PENDING.value}
        )
        deleted = getattr(result, "deleted_count", 0)
        log.debug(
            "outbox_released",
            instruction_id=instruction_id,
            deleted=deleted,
        )

    async def mark_sent(
        self, instruction_id: str, *, message_id: str | None, at: datetime
    ) -> None:
        """Transition the claim to SENT (terminal). Idempotent via upsert.

        $set updates status/sent_at/feishu_message_id.
        $setOnInsert sets claimed_at only when the document is first created by
        this upsert (mirrors InMemoryOutboxRepository.mark_sent's fallback that
        uses ``at`` when no prior claim exists). This avoids the MongoDB error
        "Mod on _id not allowed" — claimed_at is in $setOnInsert, not $set.

        Because SENT is terminal and never deleted, a second call with the same
        instruction_id is a safe no-op ($set of the same values).
        """
        await self._coll.update_one(
            {"_id": instruction_id},
            {
                "$set": {
                    "status": OutboxStatus.SENT.value,
                    "sent_at": at,
                    "feishu_message_id": message_id,
                },
                "$setOnInsert": {
                    "claimed_at": at,
                },
            },
            upsert=True,
        )
        log.debug(
            "outbox_marked_sent",
            instruction_id=instruction_id,
            message_id=message_id,
        )


__all__ = [
    "MongoOutboxRepository",
]
