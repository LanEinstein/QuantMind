"""BrokerScheduler EOD pipeline + freeze state tests (E-005)."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from backend.audit.models import AuditActor, AuditEventType
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.snapshots import BrokerSnapshot
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


def _scheduler_with(
    *,
    tmp_path: Path,
    now: dt.datetime,
    line2_daily_runner_callback=None,  # noqa: ANN001
    line2_intraday_runner_callback=None,  # noqa: ANN001
    thesis_review_runner_callback=None,  # noqa: ANN001
) -> BrokerScheduler:
    """Build a BrokerScheduler pinned to ``now`` with optional Line-2 callbacks."""
    broker = MockBroker(
        config=BrokerConfig(initial_capital=100_000.0), now_func=lambda: now
    )
    client = _FakeClient()
    audit_store = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
    )
    return BrokerScheduler(
        broker=broker,
        event_store=BrokerEventStore(client, _FakeCollection()),
        snapshot_store=BrokerSnapshotStore(client, _FakeCollection()),
        audit_store=audit_store,
        line2_daily_runner_callback=line2_daily_runner_callback,
        line2_intraday_runner_callback=line2_intraday_runner_callback,
        thesis_review_runner_callback=thesis_review_runner_callback,
        now_func=lambda: now,
    )


class TestInitialCapitalDefault:
    """U-D1 — the BrokerScheduler default aligns to the ¥100k 同花顺模拟盘."""

    def test_default_initial_capital_is_100k(self, tmp_path: Path) -> None:
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        assert sched._initial_capital == 100_000.0  # noqa: SLF001


class TestAdvanceDayHolidayGating:
    """U-D1 / Codex P1 — advance_day must not unlock T+1 on a weekday holiday."""

    @pytest.mark.asyncio
    async def test_advance_day_skipped_on_weekday_holiday(
        self, tmp_path: Path
    ) -> None:
        # 2026-05-01 (Fri) is 劳动节 — a weekday exchange holiday.
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 1, 16, 30, tzinfo=SHANGHAI),
        )
        await sched._advance_day_job()  # noqa: SLF001
        # No DAY_ADVANCED event — the holiday session never happened.
        # ``_scheduler_with`` does not retain the event collection, so assert
        # via the broker: advance_day was never called, so the trade day did
        # not roll. Re-build with an inspectable event collection instead.
        client = _FakeClient()
        event_coll = _FakeCollection()
        broker = MockBroker(
            config=BrokerConfig(initial_capital=100_000.0),
            now_func=lambda: dt.datetime(2026, 5, 1, 16, 30, tzinfo=SHANGHAI),
        )
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "a2.jsonl"
        )
        sched2 = BrokerScheduler(
            broker=broker,
            event_store=BrokerEventStore(client, event_coll),
            snapshot_store=BrokerSnapshotStore(client, _FakeCollection()),
            audit_store=audit_store,
            now_func=lambda: dt.datetime(2026, 5, 1, 16, 30, tzinfo=SHANGHAI),
        )
        await sched2._advance_day_job()  # noqa: SLF001
        assert not any(
            doc["event_type"] == BrokerEventType.DAY_ADVANCED.value
            for doc in event_coll.docs
        )


class TestEodPipelineHolidayGating:
    """P0-6-amendment-2026-06-23 — EOD chain must not run on a weekday holiday."""

    @pytest.mark.asyncio
    async def test_eod_pipeline_skipped_on_weekday_holiday(
        self, tmp_path: Path
    ) -> None:
        # 2026-05-01 (Fri) is 劳动节 — a weekday exchange holiday. The 16:00 cron
        # still fires, but the gate must return before run_eod_pipeline so no
        # EOD snapshot/result is produced for a session that never happened.
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 1, 16, 0, tzinfo=SHANGHAI),
        )
        await sched._eod_pipeline_job()  # noqa: SLF001
        assert sched.last_eod_result() is None


class TestThesisReviewCron:
    """W-002 — Line-2 post-close thesis-review cron callback + gating."""

    @pytest.mark.asyncio
    async def test_no_op_when_callback_missing(self, tmp_path: Path) -> None:
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 17, 30, tzinfo=SHANGHAI),
        )
        await sched._thesis_review_job()  # noqa: SLF001 — must not raise

    @pytest.mark.asyncio
    async def test_runs_on_trading_day(self, tmp_path: Path) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 17, 30, tzinfo=SHANGHAI),  # Fri
            thesis_review_runner_callback=cb,
        )
        await sched._thesis_review_job()  # noqa: SLF001
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_skipped_on_weekday_holiday(self, tmp_path: Path) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 1, 17, 30, tzinfo=SHANGHAI),  # 劳动节 holiday
            thesis_review_runner_callback=cb,
        )
        await sched._thesis_review_job()  # noqa: SLF001
        assert calls == []

    @pytest.mark.asyncio
    async def test_failure_swallowed(self, tmp_path: Path) -> None:
        async def boom(now: dt.datetime) -> None:
            raise RuntimeError("advisory blew up")

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 17, 30, tzinfo=SHANGHAI),
            thesis_review_runner_callback=boom,
        )
        # Must not raise — a thesis-review failure never freezes routing.
        await sched._thesis_review_job()  # noqa: SLF001


class TestLine2RunnerCrons:
    """U-D1 — Line-2 daily + 30s intraday cron callbacks + gating."""

    @pytest.mark.asyncio
    async def test_line2_daily_no_op_when_callback_missing(
        self, tmp_path: Path
    ) -> None:
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 9, 0, tzinfo=SHANGHAI),
        )
        await sched._line2_daily_job()  # noqa: SLF001 — must not raise

    @pytest.mark.asyncio
    async def test_line2_daily_runs_on_trading_day(self, tmp_path: Path) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            # 09:35 Fri — inside trading hours so the runner's RiskEngine
            # trading-hours gate passes (Codex U-D1 P1).
            now=dt.datetime(2026, 5, 15, 9, 35, tzinfo=SHANGHAI),
            line2_daily_runner_callback=cb,
        )
        await sched._line2_daily_job()  # noqa: SLF001
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_line2_daily_skipped_on_weekday_holiday(
        self, tmp_path: Path
    ) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 1, 9, 0, tzinfo=SHANGHAI),  # 劳动节 holiday
            line2_daily_runner_callback=cb,
        )
        await sched._line2_daily_job()  # noqa: SLF001
        assert calls == []  # holiday → no daily scan

    @pytest.mark.asyncio
    async def test_line2_intraday_no_op_when_callback_missing(
        self, tmp_path: Path
    ) -> None:
        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 10, 30, tzinfo=SHANGHAI),
        )
        await sched._line2_intraday_job()  # noqa: SLF001 — must not raise

    @pytest.mark.asyncio
    async def test_line2_intraday_runs_in_trading_hours(
        self, tmp_path: Path
    ) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 10, 30, tzinfo=SHANGHAI),  # in session
            line2_intraday_runner_callback=cb,
        )
        await sched._line2_intraday_job()  # noqa: SLF001
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_line2_intraday_skipped_off_hours(
        self, tmp_path: Path
    ) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),  # after close
            line2_intraday_runner_callback=cb,
        )
        await sched._line2_intraday_job()  # noqa: SLF001
        assert calls == []  # off-hours → no tick (runner re-checks too)

    @pytest.mark.asyncio
    async def test_line2_daily_failure_does_not_raise(
        self, tmp_path: Path
    ) -> None:
        async def boom(now: dt.datetime) -> None:
            raise RuntimeError("line2 daily outage")

        sched = _scheduler_with(
            tmp_path=tmp_path,
            now=dt.datetime(2026, 5, 15, 9, 0, tzinfo=SHANGHAI),
            line2_daily_runner_callback=boom,
        )
        await sched._line2_daily_job()  # noqa: SLF001 — best-effort, no raise


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


class TestEodSnapshotCarriesTodayBought:
    @pytest.mark.asyncio
    async def test_snapshot_persists_same_day_bought_volume(
        self, env: _Env
    ) -> None:
        # P0-4-amendment-2026-06-04 (codex cycle-5 P1): the public Position
        # model has no today_bought_volume attribute, so the previous getattr
        # silently wrote 0 for every position — blinding the recovery T+1
        # reseed AND dropping the T+1 lock itself on a restart from the EOD
        # checkpoint. volume − available_volume IS the same-day bought
        # volume; the snapshot must carry it.
        await env.broker.apply_external_fill(
            order_id_hint="QM-20260515-100000-600519-BUY-001",
            code="600519", volume=200, fill_price=63.0, side_is_buy=True,
            traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            report_id="r-eod-buy", kind="FILLED", report_schema_version=2,
        )
        result = await env.scheduler.run_eod_pipeline()
        assert result.success
        doc = env.snapshot_coll.docs[0]
        rows = {p["code"]: p for p in doc["positions"]}
        assert rows["600519"]["volume"] == 200
        assert rows["600519"]["today_bought_volume"] == 200
        # BrokerSnapshot v2: the full per-trade-date buy map is persisted so
        # multi-day buy dates survive the snapshot cursor (codex cycle-7 P1).
        assert rows["600519"]["bought_by_date"] == {"2026-05-15": 200}


# ---------------------------------------------------------------------------
# Production-hardening Batch 5 — scheduler robustness (S5 / S6 / S8)
# ---------------------------------------------------------------------------


class TestCronConstantsAreValid5Field:
    """S5: every ``*_CRON`` constant is a standard 5-field crontab.

    The 6-field (seconds-prefixed) constants were dead but would have *raised*
    in ``CronTrigger.from_crontab`` (which is 5-field only) the moment they were
    wired. They are now the SSoT referenced directly by ``start``.
    """

    def test_all_cron_constants_parse_as_5_field(self) -> None:
        constants = {
            "EOD_CRON": BrokerScheduler.EOD_CRON,
            "MIROFISH_POSTCLOSE_CRON": BrokerScheduler.MIROFISH_POSTCLOSE_CRON,
            "THESIS_REVIEW_CRON": BrokerScheduler.THESIS_REVIEW_CRON,
            "ADVANCE_DAY_CRON": BrokerScheduler.ADVANCE_DAY_CRON,
            "LINE2_DAILY_CRON": BrokerScheduler.LINE2_DAILY_CRON,
            "LINE1_DAILY_CRON": BrokerScheduler.LINE1_DAILY_CRON,
            "EVOLUTION_SHADOW_RUN_CRON": BrokerScheduler.EVOLUTION_SHADOW_RUN_CRON,
            "WEEKEND_DEEP_REVIEW_CRON": BrokerScheduler.WEEKEND_DEEP_REVIEW_CRON,
            "HOLIDAY_CATCHUP_REVIEW_CRON": BrokerScheduler.HOLIDAY_CATCHUP_REVIEW_CRON,
            "DAILY_ATTRIBUTION_REVIEW_CRON": (
                BrokerScheduler.DAILY_ATTRIBUTION_REVIEW_CRON
            ),
            "SIM_AUTO_RECONCILIATION_CRON": (
                BrokerScheduler.SIM_AUTO_RECONCILIATION_CRON
            ),
        }
        for name, expr in constants.items():
            assert len(expr.split()) == 5, f"{name} is not 5-field: {expr!r}"
            # Must not raise — the old 6-field strings would have here.
            CronTrigger.from_crontab(expr, timezone="Asia/Shanghai")


class TestIntervalMisfireGrace:
    """S6: the 30s interval jobs carry explicit misfire grace.

    APScheduler's default ``misfire_grace_time=1`` silently drops any tick that
    is >1s late (e.g. behind a brief event-loop stall).
    """

    @pytest.mark.asyncio
    async def test_interval_jobs_have_explicit_misfire_grace(
        self, env: _Env
    ) -> None:
        try:
            await env.scheduler.start()
            for job_id in ("intraday_mtm", "line2_intraday_runner"):
                job = env.scheduler._scheduler.get_job(job_id)
                assert job is not None, job_id
                assert (
                    job.misfire_grace_time
                    == BrokerScheduler.INTERVAL_MISFIRE_GRACE_SECONDS
                ), job_id
        finally:
            await env.scheduler.stop()


def _watchdog_snapshot(trade_date: str) -> BrokerSnapshot:
    return BrokerSnapshot(
        created_at=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        trade_date=trade_date,
        last_event_sequence=0,
        cash=1_000_000.0,
        frozen_cash=0.0,
        initial_capital=1_000_000.0,
        checksum="0" * 16,
    )


class _StubSnapshotStore:
    def __init__(self, latest: BrokerSnapshot | None, raises: bool = False) -> None:
        self._latest = latest
        self._raises = raises

    async def read_latest(self) -> BrokerSnapshot | None:
        if self._raises:
            raise RuntimeError("mongo down")
        return self._latest


class TestEodSnapshotWatchdog:
    """S8: startup watchdog flags a missed EOD snapshot — alert-only."""

    _NOW = dt.datetime(2026, 5, 15, 9, 0, tzinfo=SHANGHAI)  # Friday pre-open
    _EXPECTED = "2026-05-14"  # prev trading day (Thursday)

    @pytest.mark.asyncio
    async def test_stale_snapshot_is_flagged_missed(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(
            _watchdog_snapshot("2026-05-08")
        )
        result = await env.scheduler._eod_snapshot_watchdog(self._NOW)
        assert result["status"] == "missed"
        assert result["expected_trade_date"] == self._EXPECTED
        assert result["latest_snapshot_date"] == "2026-05-08"

    @pytest.mark.asyncio
    async def test_recent_snapshot_is_ok(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(
            _watchdog_snapshot("2026-05-14")
        )
        result = await env.scheduler._eod_snapshot_watchdog(self._NOW)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_same_day_missed_eod_flagged_after_threshold(
        self, env: _Env
    ) -> None:
        """Codex S8: a same-evening restart (after 16:30) on a trading day whose
        16:00 EOD was skipped must be flagged TODAY, not deferred to tomorrow.
        """
        env.scheduler._snapshots = _StubSnapshotStore(
            _watchdog_snapshot("2026-05-14")  # yesterday — today's EOD skipped
        )
        evening = dt.datetime(2026, 5, 15, 17, 0, tzinfo=SHANGHAI)  # Fri 17:00
        result = await env.scheduler._eod_snapshot_watchdog(evening)
        assert result["status"] == "missed"
        assert result["expected_trade_date"] == "2026-05-15"  # today
        assert result["latest_snapshot_date"] == "2026-05-14"

    @pytest.mark.asyncio
    async def test_same_day_eod_present_is_ok(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(
            _watchdog_snapshot("2026-05-15")  # today's EOD already written
        )
        evening = dt.datetime(2026, 5, 15, 17, 0, tzinfo=SHANGHAI)
        result = await env.scheduler._eod_snapshot_watchdog(evening)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_hung_read_degrades_to_unknown(self, env: _Env) -> None:
        class _HangStore:
            async def read_latest(self) -> BrokerSnapshot | None:
                await asyncio.sleep(30)
                return None

        env.scheduler._snapshots = _HangStore()
        with patch(
            "backend.broker.scheduler._WATCHDOG_READ_TIMEOUT_SECONDS", 0.05
        ):
            result = await asyncio.wait_for(
                env.scheduler._eod_snapshot_watchdog(self._NOW), timeout=2.0
            )
        assert result["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_fresh_deploy_is_not_an_alert(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(None)
        result = await env.scheduler._eod_snapshot_watchdog(self._NOW)
        assert result["status"] == "fresh_deploy"

    @pytest.mark.asyncio
    async def test_read_error_degrades_to_unknown(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(None, raises=True)
        result = await env.scheduler._eod_snapshot_watchdog(self._NOW)
        assert result["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_watchdog_result_is_in_startup_audit(self, env: _Env) -> None:
        env.scheduler._snapshots = _StubSnapshotStore(
            _watchdog_snapshot("2026-05-08")
        )
        env.scheduler._now = lambda: self._NOW
        try:
            await env.scheduler.start()
        finally:
            await env.scheduler.stop()
        started = [
            e
            for e in env.audit_coll.documents
            if e["event_type"] == AuditEventType.BROKERSCHEDULER_STARTED.value
        ]
        assert started, "no BROKERSCHEDULER_STARTED audit"
        assert started[0]["payload"]["eod_watchdog"]["status"] == "missed"
