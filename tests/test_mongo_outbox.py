"""Real-Mongo integration tests for MongoOutboxRepository.

Why real Mongo (not FakeCollection): the U-D6b bug class is that
FakeCollection preserves Python enum objects in memory, masking the fact that
real Mongo/BSON stores StrEnum as a plain str. A FakeCollection test of
OutboxStatus rehydration would ALWAYS pass because the str→enum conversion
never fires. Only a round-trip through a real Mongo instance proves that
_rehydrate_doc correctly restores OutboxStatus from a bare string, and that
the identity check ``entry.status is OutboxStatus.SENT`` (used by the
dispatcher's SENT short-circuit) works after a real read.

The Mongo container must be a replica-set member (rs0) — the same
constraint as BrokerEventStore (P1-2.A E-001 boot fence). Tests are skipped
cleanly when the real Mongo is unavailable so the main CI suite does not fail
in environments without Mongo.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Availability probe — skip all tests cleanly when Mongo/rs0 is not up
# ---------------------------------------------------------------------------

try:
    import motor.motor_asyncio as motor  # type: ignore[import]

    _MOTOR_AVAILABLE = True
except ImportError:
    _MOTOR_AVAILABLE = False

_MONGO_URI = "mongodb://localhost:27017"
_DB_NAME = "quantmind_test_outbox"


async def _check_mongo() -> bool:
    """Return True iff a real Mongo replica-set is reachable."""
    if not _MOTOR_AVAILABLE:
        return False
    try:
        client = motor.AsyncIOMotorClient(
            _MONGO_URI, serverSelectionTimeoutMS=2000
        )
        await client.admin.command("replSetGetStatus")
        client.close()
        return True
    except Exception:  # noqa: BLE001
        return False


# Evaluate availability synchronously at collection time (pytest runs this
# before the event loop starts). We use a helper that boots its own loop.
_MONGO_AVAILABLE: bool = (
    asyncio.run(_check_mongo())
    if _MOTOR_AVAILABLE
    else False
)

_skip_if_no_mongo = pytest.mark.skipif(
    not _MONGO_AVAILABLE,
    reason="Real MongoDB replica-set (rs0 @ localhost:27017) not available",
)

# ---------------------------------------------------------------------------
# Imports from backend (after the availability probe so E402 noise is minimal)
# ---------------------------------------------------------------------------

from backend.orchestration.instruction_dispatcher import (  # noqa: E402
    OutboxStatus,
)
from backend.orchestration.mongo_outbox import (  # noqa: E402
    MongoOutboxRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_id() -> str:
    """Return a unique instruction_id to avoid cross-test collisions."""
    return f"QM-20260529-093500-{uuid.uuid4().hex[:6].upper()}-BUY-001"


# ---------------------------------------------------------------------------
# Fixture: per-test collection (unique name → idempotent reruns)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def mongo_outbox() -> AsyncIterator[MongoOutboxRepository]:
    """Provide a MongoOutboxRepository backed by a real Mongo collection.

    Uses a fresh collection name per test to guarantee isolation. Drops the
    collection on teardown so reruns start from a clean slate.
    """
    if not _MONGO_AVAILABLE:
        pytest.skip("Real MongoDB not available")
    client = motor.AsyncIOMotorClient(
        _MONGO_URI, uuidRepresentation="standard"
    )
    coll_name = f"test_outbox_{uuid.uuid4().hex[:8]}"
    db = client[_DB_NAME]
    coll = db[coll_name]
    repo = MongoOutboxRepository(coll)
    yield repo
    # Teardown: drop collection so reruns are idempotent
    await db.drop_collection(coll_name)
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_if_no_mongo
class TestTryClaim:
    """try_claim is the atomic at-most-once gate."""

    async def test_first_claim_returns_true(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        result = await mongo_outbox.try_claim(iid, at=datetime.now(UTC))
        assert result is True

    async def test_duplicate_claim_returns_false(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        first = await mongo_outbox.try_claim(iid, at=at)
        second = await mongo_outbox.try_claim(iid, at=at)
        assert first is True
        assert second is False

    async def test_claim_on_sent_row_returns_false(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        # A SENT row must be detected as "already claimed" so no new send fires.
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-001", at=at)
        # try_claim must return False — the SENT row blocks re-entry.
        result = await mongo_outbox.try_claim(iid, at=datetime.now(UTC))
        assert result is False


@_skip_if_no_mongo
class TestUDSixBRegressionGuard:
    """U-D6b regression guard: StrEnum identity after a real Mongo round-trip.

    The dispatcher's SENT short-circuit uses ``status is OutboxStatus.SENT``
    (identity, not equality). If status is returned as a plain str instead of
    an OutboxStatus enum, the ``is`` check fails silently and the signal is
    re-sent — a double-buy. This guard catches that exact failure mode.
    """

    async def test_status_is_enum_after_restart(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-ud6b", at=at)

        # Simulate a restart: build a FRESH repository instance pointing at
        # the SAME collection. On a real Mongo round-trip, status is a plain
        # str which is rehydrated back to the enum by _rehydrate_doc.
        fresh_repo = MongoOutboxRepository(mongo_outbox._coll)
        entry = await fresh_repo.get(iid)
        assert entry is not None, "SENT claim should survive across instances"
        # Identity check — mirrors the dispatcher's SENT short-circuit guard.
        assert entry.status is OutboxStatus.SENT, (
            f"status is {entry.status!r} (type={type(entry.status).__name__}), "
            "expected OutboxStatus.SENT enum identity — U-D6b class of bug"
        )

    async def test_pending_status_is_enum_after_real_mongo(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        # Fresh instance to force a real read from Mongo.
        fresh_repo = MongoOutboxRepository(mongo_outbox._coll)
        entry = await fresh_repo.get(iid)
        assert entry is not None
        assert entry.status is OutboxStatus.PENDING


@_skip_if_no_mongo
class TestRelease:
    """release deletes only PENDING claims; SENT rows are never deleted."""

    async def test_release_pending_removes_row(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.release(iid)
        entry = await mongo_outbox.get(iid)
        assert entry is None, "PENDING claim should be gone after release"

    async def test_release_sent_is_noop(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-sent", at=at)
        # release on a SENT row must be a no-op (fail-closed against re-send).
        await mongo_outbox.release(iid)
        entry = await mongo_outbox.get(iid)
        assert entry is not None, "SENT claim must survive release"
        assert entry.status is OutboxStatus.SENT

    async def test_release_unknown_id_is_noop(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        # Should not raise for a non-existent id.
        await mongo_outbox.release(_unique_id())


@_skip_if_no_mongo
class TestMarkSent:
    """mark_sent transitions PENDING → SENT (terminal), idempotent."""

    async def test_mark_sent_sets_fields(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        sent_at = at + timedelta(seconds=1)
        await mongo_outbox.mark_sent(iid, message_id="msg-abc123", at=sent_at)

        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.status is OutboxStatus.SENT
        assert entry.feishu_message_id == "msg-abc123"
        assert entry.sent_at is not None
        assert entry.sent_at.tzinfo is not None  # tz-aware

    async def test_mark_sent_without_prior_claim(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        # mark_sent uses upsert=True so it also works without a prior claim
        # (mirrors InMemoryOutboxRepository's fallback — $setOnInsert provides
        # claimed_at in that case).
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.mark_sent(iid, message_id="msg-upsert", at=at)
        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.status is OutboxStatus.SENT

    async def test_mark_sent_idempotent(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-idem", at=at)
        # Second call must not raise.
        await mongo_outbox.mark_sent(iid, message_id="msg-idem", at=at)
        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.status is OutboxStatus.SENT


@_skip_if_no_mongo
class TestCrossRestartIdempotency:
    """A fresh repository instance sees the SENT claim (durability)."""

    async def test_sent_claim_survives_new_instance(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-cross", at=at)

        # Simulate restart: new repository instance pointing at same collection.
        restarted = MongoOutboxRepository(mongo_outbox._coll)
        entry = await restarted.get(iid)
        assert entry is not None, "SENT claim must survive across instances"
        assert entry.status is OutboxStatus.SENT
        assert entry.feishu_message_id == "msg-cross"


@_skip_if_no_mongo
class TestDatetimeRoundtrip:
    """Datetimes must round-trip tz-aware UTC (motor strips tzinfo on read)."""

    async def test_claimed_at_tz_aware_after_roundtrip(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime(2026, 5, 29, 9, 35, 0, tzinfo=UTC)
        await mongo_outbox.try_claim(iid, at=at)
        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.claimed_at.tzinfo is not None, "claimed_at must be tz-aware"

    async def test_sent_at_tz_aware_after_roundtrip(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime(2026, 5, 29, 9, 35, 0, tzinfo=UTC)
        sent_at = at + timedelta(seconds=5)
        await mongo_outbox.try_claim(iid, at=at)
        await mongo_outbox.mark_sent(iid, message_id="msg-tz", at=sent_at)
        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.sent_at is not None
        assert entry.sent_at.tzinfo is not None, "sent_at must be tz-aware"

    async def test_none_sent_at_on_pending(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        iid = _unique_id()
        at = datetime.now(UTC)
        await mongo_outbox.try_claim(iid, at=at)
        entry = await mongo_outbox.get(iid)
        assert entry is not None
        assert entry.sent_at is None


@_skip_if_no_mongo
class TestGet:
    """get returns None for unknown ids."""

    async def test_get_unknown_id_returns_none(
        self, mongo_outbox: MongoOutboxRepository
    ) -> None:
        entry = await mongo_outbox.get(_unique_id())
        assert entry is None
