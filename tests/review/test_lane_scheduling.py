"""AA-003 scheduler gating for the weekend + holiday catch-up lanes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditEventType, AuditOutcome
from backend.broker.scheduler import BrokerScheduler

SHANGHAI = ZoneInfo("Asia/Shanghai")
SAT_10 = dt.datetime(2026, 6, 13, 10, 0, tzinfo=SHANGHAI)
SUN_10 = dt.datetime(2026, 6, 14, 10, 0, tzinfo=SHANGHAI)
FRI_10 = dt.datetime(2026, 6, 12, 10, 0, tzinfo=SHANGHAI)


@dataclass
class _FakeAudit:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def write(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


def _scheduler(
    *,
    weekend: Any = None,
    catchup: Any = None,
) -> tuple[BrokerScheduler, _FakeAudit]:
    audit = _FakeAudit()
    sched = BrokerScheduler(
        broker=object(),  # type: ignore[arg-type]
        event_store=None,  # type: ignore[arg-type]
        snapshot_store=None,  # type: ignore[arg-type]
        audit_store=audit,  # type: ignore[arg-type]
        weekend_deep_review_callback=weekend,
        holiday_catchup_review_callback=catchup,
        now_func=lambda: SAT_10,
    )
    return sched, audit


class TestWeekendLane:
    @pytest.mark.asyncio
    async def test_runs_on_saturday(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(weekend=cb)
        assert await sched.run_weekend_deep_review() is True
        assert calls == [SAT_10]

    @pytest.mark.asyncio
    async def test_skips_trading_day(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(weekend=cb)
        assert (
            await sched.run_weekend_deep_review(force_now=FRI_10) is True
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_double_failure_audits_degraded(self) -> None:
        async def cb(now: dt.datetime) -> None:
            raise RuntimeError("weekend lane broken")

        sched, audit = _scheduler(weekend=cb)
        assert await sched.run_weekend_deep_review() is False
        (row,) = audit.rows
        assert row["event_type"] is AuditEventType.SYSTEM_INTERRUPTED
        assert row["outcome"] is AuditOutcome.DEGRADED
        assert row["reason_namespace"] == "weekend_deep_review_failed"
        assert sched.freeze_state.is_active() is False


class TestHolidayCatchupLane:
    @pytest.mark.asyncio
    async def test_runs_on_sunday(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(catchup=cb)
        assert (
            await sched.run_holiday_catchup_review(force_now=SUN_10) is True
        )
        assert calls == [SUN_10]

    @pytest.mark.asyncio
    async def test_skips_saturday_weekend_lane_owns_it(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(catchup=cb)
        assert (
            await sched.run_holiday_catchup_review(force_now=SAT_10) is True
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_skips_trading_day(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(catchup=cb)
        assert (
            await sched.run_holiday_catchup_review(force_now=FRI_10) is True
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_retry_once_then_success(self) -> None:
        attempts: list[int] = []

        async def cb(now: dt.datetime) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")

        sched, audit = _scheduler(catchup=cb)
        assert (
            await sched.run_holiday_catchup_review(force_now=SUN_10) is True
        )
        assert len(attempts) == 2
        assert audit.rows == []
