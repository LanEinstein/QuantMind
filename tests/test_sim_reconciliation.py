"""AA-001 sim auto-reconciliation tests (P1-2.A-amendment-2026-06-12 §1.2).

Covers the pure three-way integrity comparison, the orchestration
(zero-diff auto-resolve / divergence fail-closed / mode + prior-ticket
gating / resume), and the scheduler retry + audit semantics.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.broker.persistence.checksum import compute_snapshot_checksum
from backend.broker.persistence.snapshots import (
    BrokerSnapshot,
    BrokerSnapshotPosition,
)
from backend.models.equity import (
    EquityPoint,
    EquityPointPosition,
    EquityPointQuality,
)
from backend.models.reconciliation import ReconciliationTicketStatus
from backend.services.reconciliation_initiate import (
    build_open_reconciliation_ticket,
)
from backend.services.sim_reconciliation import (
    SIM_AUTO_RESOLUTION_PREFIX,
    SimReconciliationStatus,
    build_sim_integrity_report,
    run_sim_auto_reconciliation,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 16, 10, tzinfo=SHANGHAI)
TRADE_DATE = "2026-06-12"
TICKET_ID = "RECON-20260612-001"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pos:
    code: str
    volume: int
    cost_price: float


@dataclass(frozen=True)
class _Account:
    available_cash: float
    frozen_cash: float


@dataclass
class _FakeBroker:
    account: _Account
    positions: tuple[_Pos, ...]

    async def get_account(self) -> _Account:
        return self.account

    async def get_positions(self) -> tuple[_Pos, ...]:
        return self.positions


@dataclass
class _FakeSnapshotStore:
    snapshot: BrokerSnapshot | None

    async def read_latest(self) -> BrokerSnapshot | None:
        return self.snapshot


@dataclass
class _FakeEquityRepo:
    point: EquityPoint | None

    async def get_latest(self) -> EquityPoint | None:
        return self.point


@dataclass
class _FakeTicketRepo:
    open_tickets: tuple[Any, ...] = ()
    saved: list[Any] = field(default_factory=list)
    allocated: list[str] = field(default_factory=list)

    async def save(self, ticket: Any) -> None:
        self.saved.append(ticket)

    async def list_all_open(self) -> tuple[Any, ...]:
        return self.open_tickets

    async def allocate_next_id(self, trade_date_compact: str) -> str:
        self.allocated.append(trade_date_compact)
        return f"RECON-{trade_date_compact}-001"


@dataclass
class _FakeApplier:
    calls: list[Any] = field(default_factory=list)
    raise_on_call: bool = False

    async def reset_to_snapshot(
        self, ticket: Any, *, actor: AuditActor, now: dt.datetime
    ) -> Any:
        if self.raise_on_call:
            raise RuntimeError("applier exploded")
        self.calls.append((ticket, actor, now))
        return object()


@dataclass
class _FakeAudit:
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def write(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


@dataclass
class _FakeDispatcher:
    fired: list[dict[str, Any]] = field(default_factory=list)

    async def fire(self, **kwargs: Any) -> None:
        self.fired.append(kwargs)


def _snapshot(
    *,
    cash: float = 65_123.86,
    frozen_cash: float = 0.0,
    positions: tuple[BrokerSnapshotPosition, ...] = (),
    trade_date: str = TRADE_DATE,
    corrupt_checksum: bool = False,
) -> BrokerSnapshot:
    checksum = compute_snapshot_checksum(cash, frozen_cash, 100_000.0, positions)
    if corrupt_checksum:
        checksum = ("0" * 16) if checksum != "0" * 16 else ("1" * 16)
    return BrokerSnapshot(
        created_at=NOW - dt.timedelta(minutes=10),
        trade_date=trade_date,
        last_event_sequence=42,
        cash=cash,
        frozen_cash=frozen_cash,
        initial_capital=100_000.0,
        positions=positions,
        checksum=checksum,
    )


def _snap_pos(
    code: str, volume: int, cost: float
) -> BrokerSnapshotPosition:
    return BrokerSnapshotPosition(
        code=code,
        volume=volume,
        today_bought_volume=0,
        cost_price=cost,
        bought_by_date={},
    )


def _equity_point(
    *,
    cash: float = 65_123.86,
    frozen_cash: float = 0.0,
    positions: tuple[EquityPointPosition, ...] = (),
    trade_date: str = TRADE_DATE,
) -> EquityPoint:
    market_value = sum(p.market_value for p in positions)
    return EquityPoint(
        snapshot_at=NOW - dt.timedelta(minutes=10),
        trade_date=trade_date,
        cash=cash,
        frozen_cash=frozen_cash,
        market_value=market_value,
        total_equity=cash + frozen_cash + market_value,
        initial_capital=100_000.0,
        pnl=0.0,
        pnl_pct=0.0,
        quality=EquityPointQuality.EOD_FALLBACK,
        positions=positions,
    )


def _eq_pos(code: str, volume: int, cost: float) -> EquityPointPosition:
    return EquityPointPosition(
        code=code,
        volume=volume,
        cost_price=cost,
        last_price=cost,
        market_value=cost * volume,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        price_quality=EquityPointQuality.EOD_FALLBACK,
        last_price_at=None,
    )


def _report(
    *,
    snapshot: BrokerSnapshot,
    checksum_ok: bool = True,
    account_cash: float = 65_123.86,
    account_frozen: float = 0.0,
    broker_positions: tuple[_Pos, ...] = (),
    equity_point: EquityPoint | None = None,
) -> Any:
    return build_sim_integrity_report(
        ticket_id=TICKET_ID,
        snapshot=snapshot,
        checksum_ok=checksum_ok,
        recomputed_checksum=snapshot.checksum if checksum_ok else "f" * 16,
        account_cash=account_cash,
        account_frozen_cash=account_frozen,
        broker_positions=broker_positions,
        equity_point=(
            equity_point
            if equity_point is not None
            else _equity_point(cash=account_cash, frozen_cash=account_frozen)
        ),
        trade_date=TRADE_DATE,
    )


def _failed_fields(report: Any) -> set[str]:
    return {d.field for d in report.deviations if not d.passed}


# ---------------------------------------------------------------------------
# Pure comparison
# ---------------------------------------------------------------------------


class TestBuildSimIntegrityReport:
    def test_identical_states_pass(self) -> None:
        pos = (_snap_pos("600519", 200, 12.34),)
        report = _report(
            snapshot=_snapshot(positions=pos),
            broker_positions=(_Pos("600519", 200, 12.34),),
            equity_point=_equity_point(
                positions=(_eq_pos("600519", 200, 12.34),)
            ),
        )
        assert report.overall_passed
        assert report.ticket_id == TICKET_ID

    def test_cash_within_locked_tolerance_passes(self) -> None:
        report = _report(
            snapshot=_snapshot(cash=65_123.86), account_cash=65_124.50
        )
        assert "snapshot_vs_broker.cash" not in _failed_fields(report)

    def test_cash_beyond_tolerance_fails(self) -> None:
        report = _report(
            snapshot=_snapshot(cash=65_123.86), account_cash=65_126.00
        )
        assert "snapshot_vs_broker.cash" in _failed_fields(report)
        assert not report.overall_passed

    def test_volume_mismatch_is_exact_zero_tolerance(self) -> None:
        report = _report(
            snapshot=_snapshot(positions=(_snap_pos("600519", 200, 12.34),)),
            broker_positions=(_Pos("600519", 300, 12.34),),
            equity_point=_equity_point(
                positions=(_eq_pos("600519", 300, 12.34),)
            ),
        )
        assert "snapshot_vs_broker.positions[600519].volume" in _failed_fields(
            report
        )

    def test_missing_position_fails_presence(self) -> None:
        report = _report(
            snapshot=_snapshot(positions=(_snap_pos("600519", 200, 12.34),)),
            broker_positions=(),
        )
        assert (
            "snapshot_vs_broker.positions[600519].presence"
            in _failed_fields(report)
        )

    def test_cost_price_tolerance(self) -> None:
        snap = _snapshot(positions=(_snap_pos("600519", 200, 12.34),))
        ok = _report(
            snapshot=snap,
            broker_positions=(_Pos("600519", 200, 12.345),),
            equity_point=_equity_point(
                positions=(_eq_pos("600519", 200, 12.345),)
            ),
        )
        assert (
            "snapshot_vs_broker.positions[600519].cost_price"
            not in _failed_fields(ok)
        )
        bad = _report(
            snapshot=snap,
            broker_positions=(_Pos("600519", 200, 12.37),),
            equity_point=_equity_point(
                positions=(_eq_pos("600519", 200, 12.37),)
            ),
        )
        assert (
            "snapshot_vs_broker.positions[600519].cost_price"
            in _failed_fields(bad)
        )

    def test_checksum_mismatch_fails(self) -> None:
        report = _report(snapshot=_snapshot(), checksum_ok=False)
        assert "snapshot.checksum" in _failed_fields(report)

    def test_missing_equity_point_fails_presence(self) -> None:
        report = build_sim_integrity_report(
            ticket_id=TICKET_ID,
            snapshot=_snapshot(),
            checksum_ok=True,
            recomputed_checksum=_snapshot().checksum,
            account_cash=65_123.86,
            account_frozen_cash=0.0,
            broker_positions=(),
            equity_point=None,
            trade_date=TRADE_DATE,
        )
        assert "equity_point.presence" in _failed_fields(report)

    def test_stale_equity_point_fails_presence(self) -> None:
        report = _report(
            snapshot=_snapshot(),
            equity_point=_equity_point(trade_date="2026-06-11"),
        )
        assert "equity_point.presence" in _failed_fields(report)

    def test_equity_cash_divergence_fails(self) -> None:
        report = _report(
            snapshot=_snapshot(cash=65_123.86),
            account_cash=65_123.86,
            equity_point=_equity_point(cash=60_000.00),
        )
        assert "equity_vs_broker.cash" in _failed_fields(report)

    def test_zero_volume_broker_positions_ignored(self) -> None:
        report = _report(
            snapshot=_snapshot(),
            broker_positions=(_Pos("600519", 0, 12.34),),
        )
        assert report.overall_passed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _wiring(
    *,
    snapshot: BrokerSnapshot | None,
    cash: float = 65_123.86,
    positions: tuple[_Pos, ...] = (),
    equity_point: EquityPoint | None = None,
    open_tickets: tuple[Any, ...] = (),
    applier: _FakeApplier | None = None,
) -> dict[str, Any]:
    if equity_point is None:
        # Default: a consistent EOD point mirroring the broker side so
        # tests not exercising the equity leg stay on the happy path.
        equity_point = _equity_point(
            cash=cash,
            positions=tuple(
                _eq_pos(p.code, p.volume, p.cost_price) for p in positions
            ),
        )
    return {
        "broker": _FakeBroker(_Account(cash, 0.0), positions),
        "snapshot_store": _FakeSnapshotStore(snapshot),
        "equity_points": _FakeEquityRepo(equity_point),
        "tickets": _FakeTicketRepo(open_tickets=open_tickets),
        "applier": applier or _FakeApplier(),
        "audit": _FakeAudit(),
        "alert_dispatcher": _FakeDispatcher(),
        "now": NOW,
    }


@pytest.fixture(autouse=True)
def _pure_sim_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "false")


class TestRunSimAutoReconciliation:
    @pytest.mark.asyncio
    async def test_feishu_mode_skips_without_any_io(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "true")
        wiring = _wiring(snapshot=_snapshot())
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.SKIPPED_FEISHU_MODE
        assert wiring["tickets"].saved == []
        assert wiring["audit"].rows == []

    @pytest.mark.asyncio
    async def test_prior_open_ticket_skips(self) -> None:
        prior = build_open_reconciliation_ticket(
            ticket_id="RECON-20260611-001",
            trade_date="2026-06-11",
            created_at=NOW - dt.timedelta(days=1),
            expected_snapshot_id="snap-prior",
        )
        wiring = _wiring(snapshot=_snapshot(), open_tickets=(prior,))
        result = await run_sim_auto_reconciliation(**wiring)
        assert (
            result.status
            is SimReconciliationStatus.SKIPPED_PRIOR_OPEN_TICKET
        )
        assert result.ticket_id == "RECON-20260611-001"
        assert wiring["tickets"].saved == []

    @pytest.mark.asyncio
    async def test_missing_snapshot_aborts_with_degraded_audit(self) -> None:
        wiring = _wiring(snapshot=None)
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.ABORTED_NO_SNAPSHOT
        assert wiring["tickets"].saved == []
        (row,) = wiring["audit"].rows
        assert row["event_type"] is AuditEventType.SYSTEM_INTERRUPTED
        assert row["outcome"] is AuditOutcome.DEGRADED
        assert (
            row["reason_namespace"] == "sim_auto_reconciliation_no_snapshot"
        )

    @pytest.mark.asyncio
    async def test_stale_snapshot_aborts(self) -> None:
        wiring = _wiring(snapshot=_snapshot(trade_date="2026-06-11"))
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.ABORTED_NO_SNAPSHOT

    @pytest.mark.asyncio
    async def test_clean_run_auto_resolves_system_as_truth(self) -> None:
        pos = (_snap_pos("600519", 200, 12.34),)
        applier = _FakeApplier()
        wiring = _wiring(
            snapshot=_snapshot(positions=pos),
            positions=(_Pos("600519", 200, 12.34),),
            equity_point=_equity_point(
                positions=(_eq_pos("600519", 200, 12.34),)
            ),
            applier=applier,
        )
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.RESOLVED_CLEAN
        saved = wiring["tickets"].saved
        # OPEN persisted first (fail-closed), then the resolution.
        assert [t.status for t in saved] == [
            ReconciliationTicketStatus.OPEN,
            ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        ]
        resolved = saved[-1]
        assert resolved.resolution_message_id == (
            f"{SIM_AUTO_RESOLUTION_PREFIX}{resolved.ticket_id}"
        )
        # Applier ran exactly once, with SYSTEM actor (P0-5 §2 ordering).
        ((ticket, actor, _),) = applier.calls
        assert actor is AuditActor.SYSTEM
        assert (
            ticket.status
            is ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH
        )
        # No divergence alert fired.
        assert wiring["alert_dispatcher"].fired == []

    @pytest.mark.asyncio
    async def test_divergence_leaves_open_ticket_and_alerts(self) -> None:
        wiring = _wiring(
            snapshot=_snapshot(cash=65_123.86),
            cash=60_000.00,
        )
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.DIVERGENCE_OPEN
        assert "snapshot_vs_broker.cash" in result.failed_fields
        saved = wiring["tickets"].saved
        assert len(saved) == 1
        assert saved[0].status is ReconciliationTicketStatus.OPEN
        assert not saved[0].deviation_report.overall_passed
        (alert,) = wiring["alert_dispatcher"].fired
        assert alert["alert_type"] == "sim_reconciliation_divergence"
        assert alert["dedup_key"] == saved[0].ticket_id
        assert alert["actor"] is AuditActor.SCHEDULER

    @pytest.mark.asyncio
    async def test_divergence_without_dispatcher_still_freezes(self) -> None:
        wiring = _wiring(snapshot=_snapshot(cash=65_123.86), cash=60_000.00)
        wiring["alert_dispatcher"] = None
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.DIVERGENCE_OPEN
        assert (
            wiring["tickets"].saved[0].status
            is ReconciliationTicketStatus.OPEN
        )

    @pytest.mark.asyncio
    async def test_resume_same_day_open_ticket_resolves_it(self) -> None:
        existing = build_open_reconciliation_ticket(
            ticket_id="RECON-20260612-007",
            trade_date=TRADE_DATE,
            created_at=NOW - dt.timedelta(minutes=5),
            expected_snapshot_id="snap-earlier",
        )
        wiring = _wiring(snapshot=_snapshot(), open_tickets=(existing,))
        result = await run_sim_auto_reconciliation(**wiring)
        assert result.status is SimReconciliationStatus.RESOLVED_CLEAN
        assert result.ticket_id == "RECON-20260612-007"
        # No new allocation; the resolved save targets the existing id.
        assert wiring["tickets"].allocated == []
        (resolved,) = wiring["tickets"].saved
        assert (
            resolved.status
            is ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH
        )

    @pytest.mark.asyncio
    async def test_applier_failure_propagates_leaving_open_ticket(
        self,
    ) -> None:
        applier = _FakeApplier(raise_on_call=True)
        wiring = _wiring(snapshot=_snapshot(), applier=applier)
        with pytest.raises(RuntimeError, match="applier exploded"):
            await run_sim_auto_reconciliation(**wiring)
        # The OPEN ticket persisted before the resolution attempt — the
        # freeze is live, fail-closed.
        (open_ticket,) = wiring["tickets"].saved
        assert open_ticket.status is ReconciliationTicketStatus.OPEN


# ---------------------------------------------------------------------------
# Scheduler retry + audit semantics
# ---------------------------------------------------------------------------


class TestSchedulerSimAutoReconciliation:
    def _scheduler(self, callback: Any) -> Any:
        from backend.broker.scheduler import BrokerScheduler

        audit = _FakeAudit()
        sched = BrokerScheduler(
            broker=_FakeBroker(_Account(0.0, 0.0), ()),  # type: ignore[arg-type]
            event_store=None,  # type: ignore[arg-type]
            snapshot_store=None,  # type: ignore[arg-type]
            audit_store=audit,  # type: ignore[arg-type]
            sim_auto_reconciliation_callback=callback,
            now_func=lambda: NOW,
        )
        return sched, audit

    @pytest.mark.asyncio
    async def test_unwired_callback_is_noop(self) -> None:
        sched, audit = self._scheduler(None)
        assert await sched.run_sim_auto_reconciliation() is True
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_non_trading_day_skips(self) -> None:
        calls: list[dt.datetime] = []

        async def cb(now: dt.datetime) -> None:
            calls.append(now)

        sched, _ = self._scheduler(cb)
        saturday = dt.datetime(2026, 6, 13, 16, 10, tzinfo=SHANGHAI)
        assert (
            await sched.run_sim_auto_reconciliation(force_now=saturday)
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

        sched, audit = self._scheduler(cb)
        assert await sched.run_sim_auto_reconciliation() is True
        assert len(attempts) == 2
        assert audit.rows == []

    @pytest.mark.asyncio
    async def test_double_failure_emits_degraded_audit(self) -> None:
        async def cb(now: dt.datetime) -> None:
            raise RuntimeError("persistent failure")

        sched, audit = self._scheduler(cb)
        assert await sched.run_sim_auto_reconciliation() is False
        (row,) = audit.rows
        assert row["event_type"] is AuditEventType.SYSTEM_INTERRUPTED
        assert row["outcome"] is AuditOutcome.DEGRADED
        assert row["reason_namespace"] == "sim_auto_reconciliation_failed"
        assert row["payload"]["retried"] is True


class TestEquityPointFreshness:
    """Codex Phase-AA P2 — an earlier intraday point must not satisfy
    the closing-point requirement when the EOD upsert was swallowed."""

    def test_stale_intraday_point_fails_freshness(self) -> None:
        snap = _snapshot()
        stale = _equity_point().model_copy(
            update={"snapshot_at": snap.created_at - dt.timedelta(hours=2)}
        )
        report = _report(snapshot=snap, equity_point=stale)
        assert "equity_point.freshness" in _failed_fields(report)

    def test_closing_point_at_snapshot_time_passes(self) -> None:
        snap = _snapshot()
        closing = _equity_point().model_copy(
            update={"snapshot_at": snap.created_at}
        )
        report = _report(snapshot=snap, equity_point=closing)
        assert "equity_point.freshness" not in _failed_fields(report)
