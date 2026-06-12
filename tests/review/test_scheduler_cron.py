"""AA-002 scheduler gating for the 18:00 daily attribution review."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditEventType, AuditOutcome
from backend.broker.scheduler import BrokerScheduler

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 18, 0, tzinfo=SHANGHAI)


@dataclass
class _FakeAudit:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def write(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


def _scheduler(callback: Any) -> tuple[BrokerScheduler, _FakeAudit]:
    audit = _FakeAudit()
    sched = BrokerScheduler(
        broker=object(),  # type: ignore[arg-type]
        event_store=None,  # type: ignore[arg-type]
        snapshot_store=None,  # type: ignore[arg-type]
        audit_store=audit,  # type: ignore[arg-type]
        daily_attribution_review_callback=callback,
        now_func=lambda: NOW,
    )
    return sched, audit


class TestDailyAttributionReviewCron:
    @pytest.mark.asyncio
    async def test_unwired_callback_is_noop(self) -> None:
        sched, audit = _scheduler(None)
        assert await sched.run_daily_attribution_review() is True
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_non_trading_day_skips(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = _scheduler(cb)
        saturday = dt.datetime(2026, 6, 13, 18, 0, tzinfo=SHANGHAI)
        assert (
            await sched.run_daily_attribution_review(force_now=saturday)
            is True
        )
        assert calls == []

    @pytest.mark.asyncio
    async def test_retry_once_then_success(self) -> None:
        attempts: list[int] = []

        async def cb(now: dt.datetime) -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")

        sched, audit = _scheduler(cb)
        assert await sched.run_daily_attribution_review() is True
        assert len(attempts) == 2
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_double_failure_audits_degraded_without_freeze(
        self,
    ) -> None:
        async def cb(now: dt.datetime) -> None:
            raise RuntimeError("persistent failure")

        sched, audit = _scheduler(cb)
        assert await sched.run_daily_attribution_review() is False
        (row,) = audit.rows
        assert row["event_type"] is AuditEventType.SYSTEM_INTERRUPTED
        assert row["outcome"] is AuditOutcome.DEGRADED
        assert (
            row["reason_namespace"] == "daily_attribution_review_failed"
        )
        # The review lane never freezes trading (X-005 precedent).
        assert sched.freeze_state.is_active() is False
