"""X-005 unit tests — BrokerScheduler 5th cron ``evolution_shadow_run``.

Exercises the Phase X 22:00 mon-fri cron added by
P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th. Lives in a
dedicated module so the existing E-005 EOD pipeline suite stays
untouched.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
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
# In-memory test doubles (mirror of the E-005 suite, kept compact)
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
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(
        self, document: dict[str, Any], session: Any = None
    ) -> None:
        self.docs.append(dict(document))

    def find(self, filter: Any = None, projection: Any = None) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                rows = [r for r in rows if r.get("sequence", 0) > gt["$gt"]]
        return _FakeCursor(rows)

    async def find_one(self, filter: Any = None) -> dict[str, Any] | None:
        return self.docs[0] if self.docs else None


@dataclass
class _CallableRecorder:
    """Captures invocations of the evolution shadow callback."""

    behaviour: list[type[BaseException] | None] = field(default_factory=list)
    calls: list[dt.datetime] = field(default_factory=list)

    async def __call__(self, when: dt.datetime) -> None:
        self.calls.append(when)
        idx = len(self.calls) - 1
        if idx < len(self.behaviour):
            err = self.behaviour[idx]
            if err is not None:
                raise err("simulated shadow chain failure")


def _make_scheduler(
    *,
    tmp_path: Path,
    evolution_callback: Any | None,
    now: dt.datetime | None = None,
) -> tuple[BrokerScheduler, EodPipelineFreezeState, InMemoryAuditCollection]:
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: dt.datetime(2026, 5, 18, 22, 0, tzinfo=SHANGHAI),
    )
    client = _FakeClient()
    event_store = BrokerEventStore(client, _FakeCollection())
    snap_store = BrokerSnapshotStore(client, _FakeCollection())
    audit_coll = InMemoryAuditCollection()
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    freeze = EodPipelineFreezeState()
    fixed_now = now or dt.datetime(2026, 5, 18, 22, 0, 0, tzinfo=SHANGHAI)
    scheduler = BrokerScheduler(
        broker=broker,
        event_store=event_store,
        snapshot_store=snap_store,
        audit_store=audit_store,
        freeze_state=freeze,
        evolution_shadow_run_callback=evolution_callback,
        now_func=lambda: fixed_now,
    )
    return scheduler, freeze, audit_coll


# ---------------------------------------------------------------------------
# Cron constant + class-level invariants
# ---------------------------------------------------------------------------


def test_cron_constant_locked_to_22_00_mon_fri() -> None:
    assert BrokerScheduler.EVOLUTION_SHADOW_RUN_CRON == "0 22 * * mon-fri"


def test_init_accepts_evolution_callback_kwarg(tmp_path: Path) -> None:
    scheduler, _, _ = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=None
    )
    # The constructor must not raise; freeze unaffected.
    assert scheduler.freeze_state.is_active() is False


# ---------------------------------------------------------------------------
# run_evolution_shadow — happy paths + retry semantics + audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_callback_is_silent_noop(tmp_path: Path) -> None:
    scheduler, _, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=None
    )
    ok = await scheduler.run_evolution_shadow()
    assert ok is True
    assert audit.documents == []  # no audit emission when the chain is not wired


@pytest.mark.asyncio
async def test_success_emits_audit_success_outcome(tmp_path: Path) -> None:
    callback = _CallableRecorder(behaviour=[None])
    scheduler, freeze, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=callback
    )
    ok = await scheduler.run_evolution_shadow()
    assert ok is True
    assert len(callback.calls) == 1
    assert len(audit.documents) == 1
    event = audit.documents[0]
    assert event["event_type"] == AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED
    assert event["outcome"] == AuditOutcome.SUCCESS
    assert event["actor"] == AuditActor.SCHEDULER
    assert event["resource_type"] == "evolution_shadow_run"
    assert event["resource_id"] == "2026-05-18"
    assert freeze.is_active() is False


@pytest.mark.asyncio
async def test_first_failure_retries_then_succeeds(tmp_path: Path) -> None:
    callback = _CallableRecorder(behaviour=[RuntimeError, None])
    scheduler, freeze, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=callback
    )
    ok = await scheduler.run_evolution_shadow()
    assert ok is True
    assert len(callback.calls) == 2
    # Exactly one audit event — only the successful attempt logs.
    assert len(audit.documents) == 1
    assert audit.documents[0]["outcome"] == AuditOutcome.SUCCESS
    assert audit.documents[0]["payload"]["retried"] is True
    assert freeze.is_active() is False


@pytest.mark.asyncio
async def test_both_attempts_fail_emits_failure_audit(tmp_path: Path) -> None:
    callback = _CallableRecorder(behaviour=[RuntimeError, RuntimeError])
    scheduler, freeze, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=callback
    )
    ok = await scheduler.run_evolution_shadow()
    assert ok is False
    assert len(callback.calls) == 2
    assert len(audit.documents) == 1
    event = audit.documents[0]
    assert event["event_type"] == AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED
    assert event["outcome"] == AuditOutcome.FAILURE
    assert event["reason_namespace"] == "evolution_shadow_run_failed"
    assert "simulated shadow chain failure" in event["payload"]["error"]
    # Crucial X-005 invariant — the EOD freeze stays clear so live
    # trading routing is not blocked by a broken challenger prompt.
    assert freeze.is_active() is False


@pytest.mark.asyncio
async def test_failure_uses_scheduler_actor_not_system(tmp_path: Path) -> None:
    callback = _CallableRecorder(behaviour=[RuntimeError, RuntimeError])
    scheduler, _, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=callback
    )
    await scheduler.run_evolution_shadow()
    # Category 5 evolution events must use SYSTEM or SCHEDULER —
    # this cron is owned by the scheduler.
    assert audit.documents[0]["actor"] == AuditActor.SCHEDULER


# ---------------------------------------------------------------------------
# start() — cron registration includes evolution_shadow_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_evolution_shadow_run_job(tmp_path: Path) -> None:
    callback = _CallableRecorder(behaviour=[None])
    scheduler, _, audit = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=callback
    )
    try:
        await scheduler.start()
    finally:
        await scheduler.stop()

    job_ids = {job.id for job in scheduler._scheduler.get_jobs()}  # type: ignore[union-attr]
    assert "evolution_shadow_run" in job_ids
    # U-D1 — the two Line-2 production runner crons are registered too.
    assert "line2_daily_runner" in job_ids
    assert "line2_intraday_runner" in job_ids
    # U-D1b — the Line-1 BUY-selection cron too.
    assert "line1_runner" in job_ids
    # W-002 — the Line-2 post-close thesis-review cron too.
    assert "thesis_review_runner" in job_ids

    started_events = [
        d for d in audit.documents
        if d["event_type"] == AuditEventType.BROKERSCHEDULER_STARTED
    ]
    assert len(started_events) == 1
    jobs = started_events[0]["payload"]["jobs"]
    assert jobs == [
        "eod_pipeline",
        "intraday_mtm",
        "mirofish_postclose",
        "advance_day",
        "line2_daily_runner",
        "line2_intraday_runner",
        "line1_runner",
        "thesis_review_runner",
        "evolution_shadow_run",
    ]


@pytest.mark.asyncio
async def test_start_registers_five_jobs_when_no_evolution_callback(
    tmp_path: Path,
) -> None:
    scheduler, _, _ = _make_scheduler(
        tmp_path=tmp_path, evolution_callback=None
    )
    try:
        await scheduler.start()
    finally:
        await scheduler.stop()
    job_ids = {job.id for job in scheduler._scheduler.get_jobs()}  # type: ignore[union-attr]
    # The cron registers regardless of callback wiring so deploys
    # missing the X-008 dispatcher boot cleanly. The job is a no-op.
    assert "evolution_shadow_run" in job_ids
