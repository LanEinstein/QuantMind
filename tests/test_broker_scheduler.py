"""BrokerScheduler EOD pipeline + freeze state tests (E-005)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditActor, AuditEventType
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import (
    BrokerEventStore,
    BrokerSnapshotStore,
)
from backend.broker.scheduler import (
    BrokerScheduler,
    EodPipelineFreezeState,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# In-memory test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        yield

    async def commit_transaction(self) -> None:
        return None

    async def abort_transaction(self) -> None:
        return None

    async def end_session(self) -> None:
        return None


@dataclass
class _FakeClient:
    async def start_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        reverse = direction == -1
        self._docs = sorted(self._docs, key=lambda d: d.get(field, 0), reverse=reverse)
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
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], session=None) -> None:
        self.docs.append(dict(document))

    def find(self, filter=None, projection=None) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                rows = [r for r in rows if r.get("sequence", 0) > gt["$gt"]]
        return _FakeCursor(rows)

    async def find_one(self, filter=None) -> dict[str, Any] | None:
        return self.docs[0] if self.docs else None


@dataclass
class _Env:
    scheduler: BrokerScheduler
    broker: MockBroker
    audit_coll: InMemoryAuditCollection
    event_coll: _FakeCollection
    snapshot_coll: _FakeCollection
    freeze: EodPipelineFreezeState = field(default_factory=EodPipelineFreezeState)


@pytest.fixture()
def env(tmp_path: Path) -> _Env:
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
    )
    client = _FakeClient()
    event_coll = _FakeCollection()
    snap_coll = _FakeCollection()
    event_store = BrokerEventStore(client, event_coll)
    snap_store = BrokerSnapshotStore(client, snap_coll)
    audit_coll = InMemoryAuditCollection()
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    freeze = EodPipelineFreezeState()
    scheduler = BrokerScheduler(
        broker=broker,
        event_store=event_store,
        snapshot_store=snap_store,
        audit_store=audit_store,
        freeze_state=freeze,
        now_func=lambda: dt.datetime(2026, 5, 15, 16, 0, 30, tzinfo=SHANGHAI),
    )
    return _Env(
        scheduler=scheduler,
        broker=broker,
        audit_coll=audit_coll,
        event_coll=event_coll,
        snapshot_coll=snap_coll,
        freeze=freeze,
    )


class TestEodPipelineSuccess:
    @pytest.mark.asyncio
    async def test_writes_snapshot_with_checksum(self, env: _Env) -> None:
        result = await env.scheduler.run_eod_pipeline()
        assert result.success
        assert result.snapshot_id is not None
        assert len(env.snapshot_coll.docs) == 1
        # checksum is 16 hex chars per the schema lock
        doc = env.snapshot_coll.docs[0]
        assert isinstance(doc["checksum"], str)
        assert len(doc["checksum"]) == 16

    @pytest.mark.asyncio
    async def test_success_keeps_freeze_clear(self, env: _Env) -> None:
        await env.scheduler.run_eod_pipeline()
        assert env.freeze.is_active() is False

    @pytest.mark.asyncio
    async def test_clears_prior_freeze_on_success(self, env: _Env) -> None:
        env.freeze.record_failure(
            reason="prior",
            trade_date="2026-05-14",
            when=dt.datetime(2026, 5, 14, 16, 0, tzinfo=SHANGHAI),
        )
        env.freeze.record_failure(
            reason="prior",
            trade_date="2026-05-14",
            when=dt.datetime(2026, 5, 14, 16, 0, tzinfo=SHANGHAI),
        )
        assert env.freeze.is_active() is True
        await env.scheduler.run_eod_pipeline()
        assert env.freeze.is_active() is False


class TestEodPipelineFreeze:
    """One retry; second failure activates the freeze + audit row."""

    @pytest.mark.asyncio
    async def test_second_failure_activates_freeze(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        # Snapshot append blows up — simulates Mongo outage / write error.

        class _ExplodingCollection(_FakeCollection):
            async def insert_one(self, document, session=None) -> None:
                raise RuntimeError("simulated snapshot insert failure")

        snap_store = BrokerSnapshotStore(client, _ExplodingCollection())
        audit_coll = InMemoryAuditCollection()
        audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
        freeze = EodPipelineFreezeState()
        scheduler = BrokerScheduler(
            broker=broker,
            event_store=event_store,
            snapshot_store=snap_store,
            audit_store=audit_store,
            freeze_state=freeze,
            now_func=lambda: dt.datetime(2026, 5, 15, 16, 0, 30, tzinfo=SHANGHAI),
        )
        result = await scheduler.run_eod_pipeline()
        assert result.success is False
        assert freeze.is_active() is True
        assert freeze.raised_for_trade_date() == "2026-05-15"
        # Audit row for the freeze emission
        assert any(
            d["event_type"]
            == AuditEventType.FREEZE_SOURCE_EOD_PIPELINE_FREEZE.value
            and d["actor"] == AuditActor.SCHEDULER.value
            for d in audit_coll.documents
        )

    def test_freeze_state_inactive_by_default(self) -> None:
        f = EodPipelineFreezeState()
        assert f.is_active() is False
        assert f.reason() is None
        assert f.raised_at() is None

    def test_freeze_state_record_failure_idempotent_after_active(self) -> None:
        f = EodPipelineFreezeState()
        first = f.record_failure(
            reason="r1", trade_date="2026-05-15",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        second = f.record_failure(
            reason="r2", trade_date="2026-05-15",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        assert first is True
        assert second is False
        assert f.is_active() is True

    def test_freeze_state_clear(self) -> None:
        f = EodPipelineFreezeState()
        f.record_failure(
            reason="r1", trade_date="d",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        f.clear()
        assert f.is_active() is False
        # After clear a subsequent failure activates fresh (no carry-over).
        first_again = f.record_failure(
            reason="r3", trade_date="d",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        assert first_again is True
        assert f.is_active() is True


class TestSchedulerCronCallbacks:
    """The four cron callbacks dispatch to the right hook + best-effort
    MiroFish failure does not raise."""

    @pytest.mark.asyncio
    async def test_advance_day_emits_event(self, env: _Env) -> None:
        await env.scheduler._advance_day_job()
        assert any(
            doc["event_type"] == BrokerEventType.DAY_ADVANCED.value
            for doc in env.event_coll.docs
        )

    @pytest.mark.asyncio
    async def test_intraday_no_op_when_callback_missing(
        self, env: _Env
    ) -> None:
        await env.scheduler._intraday_mtm_job()  # must not raise

    @pytest.mark.asyncio
    async def test_intraday_runs_callback_when_present(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        snap_store = BrokerSnapshotStore(client, _FakeCollection())
        audit_coll = InMemoryAuditCollection()
        audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        scheduler = BrokerScheduler(
            broker=broker,
            event_store=event_store,
            snapshot_store=snap_store,
            audit_store=audit_store,
            intraday_mtm_callback=cb,
            now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, 30, tzinfo=SHANGHAI),
        )
        await scheduler._intraday_mtm_job()
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_mirofish_best_effort_logs_failure(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 17, 0, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        snap_store = BrokerSnapshotStore(client, _FakeCollection())
        audit_coll = InMemoryAuditCollection()
        audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")

        async def boom(now: dt.datetime) -> None:
            raise RuntimeError("mirofish outage")

        scheduler = BrokerScheduler(
            broker=broker,
            event_store=event_store,
            snapshot_store=snap_store,
            audit_store=audit_store,
            mirofish_postclose_callback=boom,
        )
        # must NOT raise — best-effort semantics
        await scheduler._mirofish_postclose_job()
        assert any(
            d["event_type"] == AuditEventType.SYSTEM_INTERRUPTED.value
            and d["reason_namespace"] == "mirofish_best_effort"
            for d in audit_coll.documents
        )


class TestReplicaSetGate:
    @pytest.mark.asyncio
    async def test_start_calls_replica_gate(self, env: _Env) -> None:
        calls = []

        class _Gate:
            async def assert_replica_set(self) -> str:
                calls.append("called")
                return "rs0"

        env.scheduler._replica_gate = _Gate()
        # Without running APScheduler internals we'll abort early at
        # the add_job step — but the gate call happens first.
        try:
            await env.scheduler.start()
        finally:
            await env.scheduler.stop()
        assert calls == ["called"]
