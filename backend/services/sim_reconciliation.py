"""Sim-mode automatic reconciliation — self-integrity check (AA-001).

P1-2.A-amendment-2026-06-12 §1.2: in pure ``simulation_auto`` mode the
MockBroker mirror IS the authoritative account, so the daily 16:10
reconciliation becomes a **self-integrity check** instead of a human
arbitration: compare the 16:00 EOD ``BrokerSnapshot`` ↔ the live broker
derived state ↔ the latest ``EquityPoint`` for the day.

* Zero difference (within the locked P0-5 §1.4.1 thresholds) → the run
  creates an OPEN :class:`ReconciliationTicket` and deterministically
  resolves it ``RESOLVED_SYSTEM_AS_TRUTH`` through the existing state
  machine + :class:`ReconciliationApplier` (audit row per decision —
  the append-only trail the amendment requires).
* ANY difference → the OPEN ticket stays OPEN (the existing 5th
  buy/sell freeze source fires fail-closed via the builder's
  ``check_ticket_freeze``) and a ``sim_reconciliation_divergence``
  alert goes to the alert chat — a divergence here is a program-bug
  signal that a human MUST look at.
* ``feishu_interactive`` mode → hard skip; the human arbitration
  semantics of P0-5 are untouched.

The module never mutates the broker mirror itself: the only resolution
path it takes is ``RESOLVED_SYSTEM_AS_TRUTH`` (a no-op rewrite by
contract) through the production applier. It never imports
``backend.{llm,agents,mirofish,data}``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.broker.persistence.checksum import compute_snapshot_checksum
from backend.broker.persistence.snapshots import BrokerSnapshot
from backend.models.equity import EquityPoint
from backend.models.reconciliation import (
    CASH_TOLERANCE_CNY,
    COST_PRICE_TOLERANCE_CNY,
    DeviationReport,
    FieldDeviation,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.services.reconciliation_initiate import (
    build_open_reconciliation_ticket,
)
from backend.services.reconciliation_state_machine import transition_ticket
from backend.services.run_mode import feishu_interactive_enabled

log = structlog.get_logger(component="services.sim_reconciliation")
SHANGHAI = ZoneInfo("Asia/Shanghai")

SIM_AUTO_RESOLUTION_PREFIX = "SIM-AUTO-"
"""Synthetic ``resolution_message_id`` prefix — there is no Feishu
arbitration reply in pure-sim mode, so the auto-resolution stamps a
deterministic marker instead (audit-greppable)."""


# ---------------------------------------------------------------------------
# Narrow protocols for injected collaborators (tests stay simple)
# ---------------------------------------------------------------------------


@runtime_checkable
class _BrokerView(Protocol):
    async def get_account(self) -> Any: ...

    async def get_positions(self) -> Any: ...


@runtime_checkable
class _SnapshotReader(Protocol):
    async def read_latest(self) -> BrokerSnapshot | None: ...


@runtime_checkable
class _EquityPointReader(Protocol):
    async def get_latest(self) -> EquityPoint | None: ...


@runtime_checkable
class _TicketRepo(Protocol):
    async def save(self, ticket: ReconciliationTicket) -> None: ...

    async def list_all_open(self) -> tuple[ReconciliationTicket, ...]: ...

    async def allocate_next_id(self, trade_date_compact: str) -> str: ...


@runtime_checkable
class _ReconciliationApplier(Protocol):
    async def reset_to_snapshot(
        self,
        ticket: ReconciliationTicket,
        *,
        actor: AuditActor,
        now: datetime,
    ) -> Any: ...


@runtime_checkable
class _AuditWriter(Protocol):
    async def write(self, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


class SimReconciliationStatus(StrEnum):
    """Outcome of one 16:10 sim auto-reconciliation run."""

    RESOLVED_CLEAN = "resolved_clean"
    DIVERGENCE_OPEN = "divergence_open"
    SKIPPED_FEISHU_MODE = "skipped_feishu_mode"
    SKIPPED_PRIOR_OPEN_TICKET = "skipped_prior_open_ticket"
    ABORTED_NO_SNAPSHOT = "aborted_no_snapshot"


@dataclass(frozen=True)
class SimReconciliationResult:
    """Immutable summary of one run (logged + returned to the cron)."""

    status: SimReconciliationStatus
    trade_date: str
    ticket_id: str | None = None
    failed_fields: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pure comparison — EOD snapshot ↔ broker derived state ↔ equity point
# ---------------------------------------------------------------------------


def build_sim_integrity_report(
    *,
    ticket_id: str,
    snapshot: BrokerSnapshot,
    checksum_ok: bool,
    recomputed_checksum: str,
    account_cash: float,
    account_frozen_cash: float,
    broker_positions: Sequence[Any],
    equity_point: EquityPoint | None,
    trade_date: str,
) -> DeviationReport:
    """Three-way integrity comparison as a pure function.

    WHY a bespoke comparison instead of ``detect_deviations``: that
    function's signature is "system snapshot vs USER-reported mirror";
    here all three sides are system-derived and the field namespace must
    say WHICH pair diverged (``snapshot_vs_broker.*`` /
    ``equity_vs_broker.*``) so the operator can localise the bug. The
    locked P0-5 §1.4.1 thresholds are reused verbatim (cash ±1元,
    volume exact, cost ±0.01元).
    """
    devs: list[FieldDeviation] = [
        FieldDeviation(
            field="snapshot.checksum",
            expected=snapshot.checksum,
            actual=recomputed_checksum,
            abs_diff=0.0 if checksum_ok else 1.0,
            threshold=0.0,
            passed=checksum_ok,
        )
    ]

    held = {
        str(pos.code): pos
        for pos in broker_positions
        if int(getattr(pos, "volume", 0)) > 0
    }
    devs.extend(
        _compare_account(
            prefix="snapshot_vs_broker",
            expected_cash=snapshot.cash,
            expected_frozen=snapshot.frozen_cash,
            actual_cash=account_cash,
            actual_frozen=account_frozen_cash,
        )
    )
    devs.extend(
        _compare_positions(
            prefix="snapshot_vs_broker",
            expected={p.code: p for p in snapshot.positions},
            actual=held,
        )
    )

    if equity_point is None or equity_point.trade_date != trade_date:
        devs.append(
            FieldDeviation(
                field="equity_point.presence",
                expected=trade_date,
                actual=(
                    "missing"
                    if equity_point is None
                    else equity_point.trade_date
                ),
                abs_diff=1.0,
                threshold=0.0,
                passed=False,
            )
        )
    elif _utc(equity_point.snapshot_at) < _utc(snapshot.created_at):
        # Codex Phase-AA P2 fix — a same-day point is not necessarily
        # the 16:00 CLOSING point: if the EOD equity upsert was
        # swallowed, an earlier intraday tick would otherwise pass the
        # trade_date check and let the run auto-resolve on stale data.
        # The closing point and the EOD snapshot share the pipeline's
        # start timestamp, so "point at/after snapshot" is the
        # deterministic freshness bound.
        devs.append(
            FieldDeviation(
                field="equity_point.freshness",
                expected=_utc(snapshot.created_at).isoformat(),
                actual=_utc(equity_point.snapshot_at).isoformat(),
                abs_diff=1.0,
                threshold=0.0,
                passed=False,
            )
        )
    else:
        devs.extend(
            _compare_account(
                prefix="equity_vs_broker",
                expected_cash=equity_point.cash,
                expected_frozen=equity_point.frozen_cash,
                actual_cash=account_cash,
                actual_frozen=account_frozen_cash,
            )
        )
        devs.extend(
            _compare_positions(
                prefix="equity_vs_broker",
                expected={p.code: p for p in equity_point.positions},
                actual=held,
            )
        )

    return DeviationReport(
        ticket_id=ticket_id,
        overall_passed=all(d.passed for d in devs),
        deviations=tuple(devs),
    )


def _utc(value: datetime) -> datetime:
    """Normalise to aware-UTC (Mongo round trips may drop tzinfo)."""
    from datetime import UTC

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _compare_account(
    *,
    prefix: str,
    expected_cash: float,
    expected_frozen: float,
    actual_cash: float,
    actual_frozen: float,
) -> list[FieldDeviation]:
    """Cash + frozen_cash within the locked ±1元 tolerance."""
    out: list[FieldDeviation] = []
    for name, exp, act in (
        ("cash", expected_cash, actual_cash),
        ("frozen_cash", expected_frozen, actual_frozen),
    ):
        diff = abs(exp - act)
        out.append(
            FieldDeviation(
                field=f"{prefix}.{name}",
                expected=f"{exp:.2f}",
                actual=f"{act:.2f}",
                abs_diff=diff,
                threshold=CASH_TOLERANCE_CNY,
                passed=diff <= CASH_TOLERANCE_CNY,
            )
        )
    return out


def _compare_positions(
    *,
    prefix: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[FieldDeviation]:
    """Per-code presence + exact volume + cost within ±0.01元."""
    out: list[FieldDeviation] = []
    for code in sorted(set(expected) | set(actual)):
        ep = expected.get(code)
        ap = actual.get(code)
        if ep is None or ap is None:
            out.append(
                FieldDeviation(
                    field=f"{prefix}.positions[{code}].presence",
                    expected=("missing" if ep is None else f"vol={ep.volume}"),
                    actual=("missing" if ap is None else f"vol={ap.volume}"),
                    abs_diff=1.0,
                    threshold=0.0,
                    passed=False,
                )
            )
            continue
        out.append(
            FieldDeviation(
                field=f"{prefix}.positions[{code}].volume",
                expected=str(int(ep.volume)),
                actual=str(int(ap.volume)),
                abs_diff=float(abs(int(ep.volume) - int(ap.volume))),
                threshold=0.0,
                passed=int(ep.volume) == int(ap.volume),
            )
        )
        cost_diff = abs(float(ep.cost_price) - float(ap.cost_price))
        out.append(
            FieldDeviation(
                field=f"{prefix}.positions[{code}].cost_price",
                expected=f"{float(ep.cost_price):.4f}",
                actual=f"{float(ap.cost_price):.4f}",
                abs_diff=cost_diff,
                threshold=COST_PRICE_TOLERANCE_CNY,
                passed=cost_diff <= COST_PRICE_TOLERANCE_CNY,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration — one 16:10 run
# ---------------------------------------------------------------------------


async def run_sim_auto_reconciliation(
    *,
    broker: _BrokerView,
    snapshot_store: _SnapshotReader,
    equity_points: _EquityPointReader,
    tickets: _TicketRepo,
    applier: _ReconciliationApplier,
    audit: _AuditWriter,
    alert_dispatcher: Any | None,
    now: datetime,
) -> SimReconciliationResult:
    """Execute one sim auto-reconciliation pass (16:10 cron body).

    Ordering is fail-closed by construction: the OPEN ticket is
    persisted BEFORE the resolution attempt, so a crash mid-run leaves
    the freeze active (exactly what a half-verified mirror deserves).
    The applier runs BEFORE the resolved ticket persists (P0-5 §2 red
    line ordering, same as the decide endpoint).
    """
    trade_date = now.astimezone(SHANGHAI).date().isoformat()

    if feishu_interactive_enabled():
        log.info("sim_auto_reconciliation_skipped_feishu_mode")
        return SimReconciliationResult(
            status=SimReconciliationStatus.SKIPPED_FEISHU_MODE,
            trade_date=trade_date,
        )

    open_tickets = await tickets.list_all_open()
    prior = [t for t in open_tickets if t.trade_date != trade_date]
    if prior:
        # A pre-existing OPEN/EXPIRED ticket already freezes routing —
        # piling a second ticket on top would only add arbitration noise.
        log.warning(
            "sim_auto_reconciliation_skipped_prior_open_ticket",
            ticket_ids=[t.ticket_id for t in prior],
        )
        return SimReconciliationResult(
            status=SimReconciliationStatus.SKIPPED_PRIOR_OPEN_TICKET,
            trade_date=trade_date,
            ticket_id=prior[0].ticket_id,
        )

    snapshot = await snapshot_store.read_latest()
    if snapshot is None or snapshot.trade_date != trade_date:
        # EOD pipeline did not produce today's snapshot — its own
        # freeze + audit already fired; record the aborted check and
        # let the EOD freeze carry the fail-closed semantics.
        await audit.write(
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SCHEDULER,
            resource_type="sim_auto_reconciliation",
            resource_id=trade_date,
            payload={
                "trade_date": trade_date,
                "latest_snapshot_trade_date": (
                    None if snapshot is None else snapshot.trade_date
                ),
            },
            outcome=AuditOutcome.DEGRADED,
            reason_namespace="sim_auto_reconciliation_no_snapshot",
        )
        log.warning(
            "sim_auto_reconciliation_no_snapshot", trade_date=trade_date
        )
        return SimReconciliationResult(
            status=SimReconciliationStatus.ABORTED_NO_SNAPSHOT,
            trade_date=trade_date,
        )

    recomputed = compute_snapshot_checksum(
        snapshot.cash,
        snapshot.frozen_cash,
        snapshot.initial_capital,
        snapshot.positions,
    )
    account = await broker.get_account()
    positions = await broker.get_positions()
    equity_point = await equity_points.get_latest()

    # Resume path: a same-day OPEN ticket (a crashed earlier attempt)
    # is re-verified instead of allocating a duplicate. Mode switches
    # archive + reset the account, so a same-day OPEN ticket in pure-sim
    # mode can only come from this very cron.
    resume = [t for t in open_tickets if t.trade_date == trade_date]
    if resume:
        ticket = resume[0]
        ticket_id = ticket.ticket_id
    else:
        ticket_id = await tickets.allocate_next_id(
            now.astimezone(SHANGHAI).strftime("%Y%m%d")
        )
        ticket = None

    report = build_sim_integrity_report(
        ticket_id=ticket_id,
        snapshot=snapshot,
        checksum_ok=recomputed == snapshot.checksum,
        recomputed_checksum=recomputed,
        account_cash=float(account.available_cash),
        account_frozen_cash=float(account.frozen_cash),
        broker_positions=tuple(positions),
        equity_point=equity_point,
        trade_date=trade_date,
    )

    if ticket is None:
        ticket = build_open_reconciliation_ticket(
            ticket_id=ticket_id,
            trade_date=trade_date,
            created_at=now,
            expected_snapshot_id=str(snapshot.snapshot_id),
            deviation_report=report,
        )
        await tickets.save(ticket)

    if not report.overall_passed:
        failed = tuple(
            d.field for d in report.deviations if not d.passed
        )
        log.error(
            "sim_auto_reconciliation_divergence",
            ticket_id=ticket_id,
            failed_fields=list(failed),
        )
        if alert_dispatcher is not None:
            await alert_dispatcher.fire(
                alert_type="sim_reconciliation_divergence",
                message=(
                    f"sim 自动对账发现差异(程序 bug 信号):ticket "
                    f"{ticket_id},失败字段 {len(failed)} 个:"
                    f"{', '.join(failed[:8])}。BUY/SELL 已 fail-closed "
                    f"冻结,需人工排查后通过 decide 端点解除。"
                ),
                payload={
                    "ticket_id": ticket_id,
                    "trade_date": trade_date,
                    "failed_fields": list(failed),
                },
                dedup_key=ticket_id,
                actor=AuditActor.SCHEDULER,
                resource_type="reconciliation_ticket",
                resource_id=ticket_id,
                now=now,
            )
        return SimReconciliationResult(
            status=SimReconciliationStatus.DIVERGENCE_OPEN,
            trade_date=trade_date,
            ticket_id=ticket_id,
            failed_fields=failed,
        )

    resolved = transition_ticket(
        ticket,
        ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        at=now if now >= ticket.created_at else ticket.created_at,
        resolution_message_id=f"{SIM_AUTO_RESOLUTION_PREFIX}{ticket_id}",
    )
    # Applier BEFORE persist (P0-5 §2): for SYSTEM_AS_TRUTH this is a
    # no-op rewrite that only emits the RECONCILIATION_TICKET_DECIDED
    # audit row — the append-only trail the amendment mandates.
    await applier.reset_to_snapshot(
        resolved, actor=AuditActor.SYSTEM, now=now
    )
    await tickets.save(resolved)
    log.info(
        "sim_auto_reconciliation_resolved_clean",
        ticket_id=ticket_id,
        trade_date=trade_date,
    )
    return SimReconciliationResult(
        status=SimReconciliationStatus.RESOLVED_CLEAN,
        trade_date=trade_date,
        ticket_id=ticket_id,
    )


__all__ = [
    "SIM_AUTO_RESOLUTION_PREFIX",
    "SimReconciliationResult",
    "SimReconciliationStatus",
    "build_sim_integrity_report",
    "run_sim_auto_reconciliation",
]
