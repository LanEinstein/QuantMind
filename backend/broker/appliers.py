"""Appliers — bridge user reports + reconciliation tickets to MockBroker.

E-004 / P1-2.A red line: direct mutation of MockBroker's ``_cash`` /
``_positions`` / ``_trades`` from outside the broker is forbidden.
Every external state change must flow through one of these classes:

* :class:`ExecutionReportApplier` — consumes a parsed
  :class:`ExecutionReport` (user said "FILLED 200 shares of 600519 @
  1800.5"), applies a delta to MockBroker, and writes a
  :class:`backend.broker.persistence.events.BrokerEvent` of type
  ``EXECUTION_REPORT_APPLIED`` so the recovery loader can replay the
  delta on the next boot.
* :class:`ReconciliationApplier` — consumes a resolved
  :class:`ReconciliationTicket` and rewrites the MockBroker mirror to
  match either the user-reported or amended snapshot, emitting a
  ``RECONCILIATION_RESET`` event with the new snapshot embedded.

Both appliers write a corresponding :class:`AuditEvent` (Category 1
"two write-endpoint invocations") so the audit trail surfaces every
out-of-band state change.

LLM red line: this module never imports
``backend.{llm,agents,mirofish}``. The appliers are pure-Python state
transitions over already-validated DTOs; the parsers (P0-4 / P0-5) own
the LLM-safe gating upstream.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.broker.applied_report_guard import (
    AppliedReportGuard,
    InMemoryAppliedReportGuard,
)
from backend.broker.mock_broker import MockBroker
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.models.execution import (
    REPORT_SCHEMA_V1_OWNER_FEE,
    ExecutionReport,
    ExecutionReportKind,
)
from backend.models.reconciliation import (
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)

log = structlog.get_logger(component="broker.appliers")


def compute_idempotency_key(report: ExecutionReport) -> str:
    """Deterministic dedupe key derived from a report's semantic content.

    The parser mints a fresh random ``report_id`` per parse, so keying
    the idempotency guard on ``report_id`` would NOT dedupe a genuine
    re-submission of the same fill — a frontend double-click or a Feishu
    redelivery that slipped past the envelope ``event_id`` dedupe each
    yield a different ``report_id`` and would both apply (Codex U-D4 P1).

    This key hashes the fields that define the *reported outcome* for an
    instruction — channel- and timestamp-agnostic — so the same reported
    fill claims the same key no matter how many times or through which
    channel it arrives. A genuinely different report (a correction with a
    different price/volume, or the next partial of a split fill with a
    different ``remain_volume``) hashes differently and is NOT suppressed.
    """

    def _num(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, int):
            return str(value)
        return format(float(value), ".4f")  # type: ignore[arg-type]

    # P0-4-amendment-2026-05-27 §2.4 — the key carries the schema version
    # + fill price + volumes, NOT a system-derived fee. ``report.fee`` is
    # None on v2 reports (so ``_num`` yields ""), which keeps the key
    # independent of the computed fee; v1 (legacy owner-fee) reports still
    # contribute their reported fee so a fee-only correction is not
    # silently deduped away.
    parts = (
        report.instruction_id,
        report.kind.value,
        report.prefix.value,
        str(report.report_schema_version),
        report.stock_code or "",
        _num(report.filled_volume),
        _num(report.remain_volume),
        _num(report.fill_price),
        _num(report.fee),
        report.reason or "",
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"erp-idem-{digest[:32]}"


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplyResult:
    """Summary of what the applier changed (for audit + tests).

    ``cash_delta`` is the net cash change in CNY (positive = inflow).
    ``positions_delta`` describes per-stock position changes — empty
    tuple for UNFILLED reports or RESOLVED_SYSTEM_AS_TRUTH tickets.
    ``broker_event_sequence`` is the sequence of the BrokerEvent row
    written by the applier (None when the applier was a no-op).
    """

    cash_delta: float
    positions_delta: tuple[dict[str, Any], ...]
    broker_event_sequence: int | None
    reason: str


# ---------------------------------------------------------------------------
# ExecutionReportApplier — user said "FILLED / PARTIAL / UNFILLED"
# ---------------------------------------------------------------------------


class ExecutionReportApplier:
    """Apply a parsed ExecutionReport to the MockBroker mirror.

    Caller responsibilities (the orchestrator in B-003 / Phase F):

    1. Validate the ExecutionReport against the InstructionPlan
       (state machine transitions, AMBIGUOUS rejection paths) BEFORE
       calling the applier. The applier itself assumes the report is
       authoritative and consistent with the plan.
    2. Pass the InstructionSide so the applier can decide BUY vs SELL
       cash flow direction without re-deriving it from the parsed
       Chinese text.

    Idempotency (U-D4): the applier itself guards against a double-apply
    of the same ``report_id`` via an injected
    :class:`~backend.broker.applied_report_guard.AppliedReportGuard`. The
    upstream Feishu dedupe keys on the Lark envelope ``event_id`` (not the
    report) and fails open when its store is down; the frontend POST path
    has no dedupe at all. Without this last-line guard a re-delivered or
    retried report would double-mutate the broker mirror (double cash
    deduction / double position delta), which the per-call BrokerEvent
    trail cannot undo. The guard claims ``report_id`` before mutating and
    releases the claim if the mutation raises, so a *failed* apply can be
    retried — at-most-once successful application, not at-most-once
    attempt. In production the guard is Redis-backed (restart-durable
    within its TTL); the default is in-process for tests / single-process
    dev.
    """

    def __init__(
        self,
        broker: MockBroker,
        event_store: BrokerEventStore,
        audit_store: AuditStore,
        applied_guard: AppliedReportGuard | None = None,
    ) -> None:
        self._broker = broker
        self._events = event_store
        self._audit = audit_store
        self._applied_guard: AppliedReportGuard = (
            applied_guard
            if applied_guard is not None
            else InMemoryAppliedReportGuard()
        )

    async def apply(
        self,
        report: ExecutionReport,
        *,
        side_is_buy: bool,
    ) -> ApplyResult:
        """Apply ``report`` to the broker mirror.

        Returns an :class:`ApplyResult` summarising the delta. The
        FILLED / PARTIAL paths mutate the broker through
        :meth:`MockBroker.apply_external_fill` (the only legitimate
        external-write entry on the broker); UNFILLED is a no-op apart
        from the audit + event trail.

        Idempotency: claims a deterministic content key
        (:func:`compute_idempotency_key`) first; a duplicate claim
        short-circuits to a no-op (``reason="execution_report_duplicate
        _skipped"``) without touching the broker, BrokerEvent trail, or
        audit log. The claim is released ONLY on a failure that happens
        BEFORE the broker mirror is mutated (so a corrected retry can
        proceed); once the broker has mutated, the claim is permanent and
        a later event/audit error propagates with the claim held — a
        retry must never re-apply the delta (Codex U-D4 P1).
        """
        idem_key = compute_idempotency_key(report)
        claimed = await self._applied_guard.claim(idem_key)
        if not claimed:
            log.warning(
                "execution_report_duplicate_skipped",
                report_id=report.report_id,
                instruction_id=report.instruction_id,
                kind=report.kind.value,
            )
            return ApplyResult(
                cash_delta=0.0,
                positions_delta=(),
                broker_event_sequence=None,
                reason="execution_report_duplicate_skipped",
            )

        if report.kind is ExecutionReportKind.UNFILLED:
            return await self._apply_unfilled(report, idem_key=idem_key)
        # FILLED / PARTIAL share the apply path — only the volume +
        # remain_volume differ at this layer.
        return await self._apply_fill(
            report, side_is_buy=side_is_buy, idem_key=idem_key
        )

    async def _apply_fill(
        self,
        report: ExecutionReport,
        *,
        side_is_buy: bool,
        idem_key: str,
    ) -> ApplyResult:
        # P0-4 §1.2 guarantees these fields are present for FILLED /
        # PARTIAL — the ExecutionReport model_validator enforces it.
        assert report.stock_code is not None
        assert report.filled_volume is not None
        assert report.fill_price is not None
        # P0-4-amendment-2026-05-27 §2.4 — v1 carries an owner fee; v2
        # (the current schema) omits it and the broker derives the
        # fee-inclusive cost. The model validator already enforces this
        # invariant per kind+version; assert it here as a last-line guard.
        if report.report_schema_version == REPORT_SCHEMA_V1_OWNER_FEE:
            assert report.fee is not None
        else:
            assert report.fee is None
        owner_fee = float(report.fee) if report.fee is not None else None

        try:
            applied = await self._broker.apply_external_fill(
                order_id_hint=report.instruction_id,
                code=report.stock_code,
                volume=int(report.filled_volume),
                fill_price=float(report.fill_price),
                side_is_buy=side_is_buy,
                traded_at=report.parsed_at,
                report_id=report.report_id,
                kind=report.kind.value,
                report_schema_version=report.report_schema_version,
                fee=owner_fee,
            )
        except Exception:
            # apply_external_fill validates + mutates atomically under its
            # lock — a raise here means the mirror is UNCHANGED, so release
            # the claim to let a corrected retry (e.g. after the position
            # exists) re-attempt. Beyond this point the broker IS mutated
            # and the claim is permanent.
            await self._applied_guard.release(idem_key)
            raise

        # Compose the BrokerEvent payload — the recovery loader expects
        # cash_delta + positions_delta keys for EXECUTION_REPORT_APPLIED.
        # ``report_schema_version`` is persisted so recovery can branch
        # (P0-4-amendment-2026-05-27 §2.4); the derived friction
        # breakdown is recorded for audit provenance (recovery itself
        # replays from the deltas, not by recomputing the fee).
        payload: dict[str, Any] = {
            "report_id": report.report_id,
            "instruction_id": report.instruction_id,
            "kind": report.kind.value,
            "prefix": report.prefix.value,
            "channel": report.channel.value,
            "stock_code": report.stock_code,
            "volume": int(report.filled_volume),
            "fill_price": float(report.fill_price),
            "report_schema_version": applied["report_schema_version"],
            "commission": applied["commission"],
            "stamp_tax": applied["stamp_tax"],
            "transfer_fee": applied["transfer_fee"],
            "net": applied["net"],
            "side_is_buy": side_is_buy,
            "cash_delta": applied["cash_delta"],
            "positions_delta": applied["positions_delta"],
        }
        # AA-004: persist the entry nameplate so recovery replay stamps a
        # freshly-created position with the same policy stack the live
        # _apply_buy used (bit-identical replay). getattr-guarded.
        nameplate_hash, nameplate_stack = getattr(
            self._broker, "entry_nameplate", (None, None)
        )
        payload["entry_policy_hash"] = nameplate_hash
        payload["entry_sell_stack_version"] = nameplate_stack
        event = await self._events.append(
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            occurred_at=report.parsed_at,
            order_id=applied.get("order_id"),
            trade_id=applied.get("trade_id"),
            correlation_id=report.instruction_id,
            payload=payload,
        )
        await self._audit.write(
            event_type=AuditEventType.EXECUTION_REPORT_SUBMITTED,
            actor=(
                AuditActor.FEISHU_USER
                if report.channel.value == "FEISHU"
                else AuditActor.FRONTEND_USER
            ),
            resource_type="instruction_plan",
            resource_id=report.instruction_id,
            payload={
                "report_id": report.report_id,
                "kind": report.kind.value,
                "channel": report.channel.value,
                "stock_code": report.stock_code,
                "filled_volume": int(report.filled_volume),
                "fill_price": float(report.fill_price),
                "report_schema_version": applied["report_schema_version"],
                "commission": applied["commission"],
                "stamp_tax": applied["stamp_tax"],
                "transfer_fee": applied["transfer_fee"],
                "broker_event_sequence": event.sequence,
            },
            outcome=AuditOutcome.SUCCESS,
            correlation_id=report.instruction_id,
            reason_namespace="execution_report_apply",
            timestamp=report.parsed_at,
        )

        return ApplyResult(
            cash_delta=applied["cash_delta"],
            positions_delta=tuple(applied["positions_delta"]),
            broker_event_sequence=event.sequence,
            reason="execution_report_applied",
        )

    async def _apply_unfilled(
        self, report: ExecutionReport, *, idem_key: str
    ) -> ApplyResult:
        # UNFILLED mutates no broker state — only the audit trail. If the
        # audit write fails, releasing the claim is safe (nothing to
        # double-apply) and lets a retry record the report.
        try:
            await self._audit.write(
                event_type=AuditEventType.EXECUTION_REPORT_SUBMITTED,
                actor=(
                    AuditActor.FEISHU_USER
                    if report.channel.value == "FEISHU"
                    else AuditActor.FRONTEND_USER
                ),
                resource_type="instruction_plan",
                resource_id=report.instruction_id,
                payload={
                    "report_id": report.report_id,
                    "kind": report.kind.value,
                    "channel": report.channel.value,
                    "reason": report.reason or "",
                },
                outcome=AuditOutcome.SUCCESS,
                correlation_id=report.instruction_id,
                reason_namespace="execution_report_apply",
                timestamp=report.parsed_at,
            )
        except Exception:
            await self._applied_guard.release(idem_key)
            raise
        return ApplyResult(
            cash_delta=0.0,
            positions_delta=(),
            broker_event_sequence=None,
            reason="execution_report_unfilled",
        )


# ---------------------------------------------------------------------------
# ReconciliationApplier — full-state rewrites only via reset_to_snapshot
# ---------------------------------------------------------------------------


# Lightweight protocol-style type so the applier's lookup map stays
# typed without dragging the full DailyReconciliation model into this
# module's public surface. Duck-typed: the applier only reads
# ``reported_cash`` and ``reported_positions``.
ReconciliationLookup = Any


class ReconciliationApplier:
    """Apply a resolved ReconciliationTicket to the MockBroker mirror.

    Three resolution paths:

    * ``RESOLVED_USER_AS_TRUTH`` — overwrite the broker mirror with the
      user-reported cash + positions.
    * ``RESOLVED_AMENDED`` — overwrite the broker mirror with
      ``ticket.amended_snapshot`` (operator-curated truth).
    * ``RESOLVED_SYSTEM_AS_TRUTH`` — no broker state change; the system
      was already right.

    Direct mutation of ``_cash`` / ``_positions`` is forbidden — the
    only entry that rewrites the mirror is
    :meth:`MockBroker.reset_to_snapshot`. The applier also emits a
    ``RECONCILIATION_RESET`` BrokerEvent so the recovery loader can
    replay the rewrite deterministically.
    """

    def __init__(
        self,
        broker: MockBroker,
        event_store: BrokerEventStore,
        audit_store: AuditStore,
        daily_reconciliations: dict[str, ReconciliationLookup] | None = None,
    ) -> None:
        self._broker = broker
        self._events = event_store
        self._audit = audit_store
        # Maps trade_date → DailyReconciliation for the user-as-truth
        # path. Production wiring loads this from Mongo; tests inject.
        # ``is not None`` check (not falsy) so a caller-owned empty dict
        # is preserved by reference — Phase I-001's dual-write daily
        # store mirrors saves into this exact dict, so re-creating a
        # fresh one here would break the RESOLVED_USER_AS_TRUTH path
        # (Codex Cycle 1 P2 regression).
        self._daily = (
            daily_reconciliations
            if daily_reconciliations is not None
            else {}
        )

    async def reset_to_snapshot(
        self,
        ticket: ReconciliationTicket,
        *,
        actor: AuditActor = AuditActor.FRONTEND_USER,
        now: datetime | None = None,
    ) -> ApplyResult:
        """Apply ``ticket``'s resolution to the broker mirror.

        Args:
            ticket: a RESOLVED_* ticket (OPEN/EXPIRED raise).
            actor: the audit actor — FRONTEND_USER by default since the
                resolution decision arrives from the UI; CLI tools can
                pass :attr:`AuditActor.CLI`.
            now: timestamp for the BrokerEvent + audit row; defaults to
                ``ticket.resolved_at`` when set.

        Returns:
            :class:`ApplyResult` describing the rewrite (the deltas
            here are absolute target values, not deltas — recovery
            applies them as a snapshot rewrite, not a delta).
        """
        if ticket.status not in {
            ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_AMENDED,
        }:
            raise ValueError(
                f"reset_to_snapshot requires a RESOLVED_* ticket; got "
                f"{ticket.status.value}"
            )

        timestamp = now or ticket.resolved_at or datetime.utcnow()

        if ticket.status is ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH:
            return await self._record_system_as_truth(ticket, actor, timestamp)

        if ticket.status is ReconciliationTicketStatus.RESOLVED_AMENDED:
            snapshot = ticket.amended_snapshot
            assert snapshot is not None  # schema-validated
            target_cash = snapshot.cash
            target_positions = snapshot.positions
            reason = "reset_to_amended_snapshot"
        else:
            # RESOLVED_USER_AS_TRUTH — look up the user-reported snapshot
            # keyed by ticket_id (Codex Cycle 7 P2 fix). Keying by
            # trade_date silently collapses multi-ticket days: a later
            # save would overwrite the first ticket's daily and a
            # decide on the earlier ticket would reset the broker to
            # the wrong reported snapshot. ticket_id is the
            # authoritative identity for a DailyReconciliation row
            # (Mongo unique index + RECON-{YYYYMMDD}-{seq}).
            daily = self._daily.get(ticket.ticket_id)
            if daily is None:
                raise ValueError(
                    f"reset_to_snapshot RESOLVED_USER_AS_TRUTH requires "
                    f"the DailyReconciliation for ticket_id "
                    f"{ticket.ticket_id!r} to be registered on the applier"
                )
            target_cash = daily.reported_cash
            target_positions = daily.reported_positions
            reason = "reset_to_user_snapshot"

        applied = await self._broker.reset_to_snapshot(
            cash=target_cash,
            positions=target_positions,
            reset_at=timestamp,
            reason=reason,
        )

        payload: dict[str, Any] = {
            "ticket_id": ticket.ticket_id,
            "trade_date": ticket.trade_date,
            "ticket_status": ticket.status.value,
            "cash": float(target_cash),
            "positions": [
                {
                    "code": pos.code,
                    "volume": int(pos.volume),
                    "today_bought_volume": 0,
                    "cost_price": float(pos.cost_price),
                }
                for pos in target_positions
            ],
        }
        event = await self._events.append(
            event_type=BrokerEventType.RECONCILIATION_RESET,
            occurred_at=timestamp,
            correlation_id=ticket.ticket_id,
            payload=payload,
        )
        await self._audit.write(
            event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
            actor=actor,
            resource_type="reconciliation_ticket",
            resource_id=ticket.ticket_id,
            payload={
                "ticket_id": ticket.ticket_id,
                "trade_date": ticket.trade_date,
                "ticket_status": ticket.status.value,
                "broker_event_sequence": event.sequence,
                "cash_after": float(target_cash),
                "positions_after_count": len(target_positions),
            },
            outcome=AuditOutcome.SUCCESS,
            correlation_id=ticket.ticket_id,
            reason_namespace="reconciliation_reset",
            timestamp=timestamp,
        )

        return ApplyResult(
            cash_delta=applied["cash_delta"],
            positions_delta=tuple(applied["positions_delta"]),
            broker_event_sequence=event.sequence,
            reason=reason,
        )

    async def _record_system_as_truth(
        self,
        ticket: ReconciliationTicket,
        actor: AuditActor,
        timestamp: datetime,
    ) -> ApplyResult:
        """No state change — only the audit row."""
        await self._audit.write(
            event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
            actor=actor,
            resource_type="reconciliation_ticket",
            resource_id=ticket.ticket_id,
            payload={
                "ticket_id": ticket.ticket_id,
                "trade_date": ticket.trade_date,
                "ticket_status": ticket.status.value,
            },
            outcome=AuditOutcome.SUCCESS,
            correlation_id=ticket.ticket_id,
            reason_namespace="reconciliation_reset",
            timestamp=timestamp,
        )
        return ApplyResult(
            cash_delta=0.0,
            positions_delta=(),
            broker_event_sequence=None,
            reason="reset_skipped_system_as_truth",
        )


__all__ = [
    "ApplyResult",
    "ExecutionReportApplier",
    "ReconciliationApplier",
    "ReportedPosition",
]
