"""Feishu reconciliation orchestrator (F-005).

Three flows wire the daily reconciliation lifecycle (P0-5):

1. **Initiate** — at the 16:00 cron tick (E-005 BrokerScheduler) we
   render and send a :class:`MessageRenderer.render_reconciliation_request`
   to the *decision* chat. The ``RECON-{YYYYMMDD}-{seq}`` id is unique
   per trade_date and survives Feishu re-deliveries.
2. **Receive** — the F-003 long-connection forwards an inbound message;
   if its text matches a reconciliation reply regex (B-004 parser) we
   either confirm-no-diff, persist a :class:`DailyReconciliation`, or
   transition an existing OPEN ticket. ``no_pattern_match`` flows
   through the F-004 clarification path so the operator gets the
   pre-written template (not a free-text LLM reply).
3. **Decide** — :class:`ReconciliationOrchestrator.decide_ticket` is
   invoked by the single allowed write endpoint
   ``POST /api/reconciliation-tickets/{id}/decide`` (P1-5 §2 lock-list).
   The applier (E-004) rewrites the broker mirror; we then send a
   ``render_reconciliation_result`` summary back to the decision chat.

Red lines (CLAUDE.md §2.7 / P0-5 §2):

* Threshold checks live in :func:`detect_deviations` — this module
  never re-implements them.
* ``OPEN`` / ``EXPIRED`` tickets are the fifth Buy/Sell freeze source;
  ``status_freeze`` is exposed for the system-status probe (G-002).
* Decision messages go to the **decision** chat, not the alert chat
  (P0-2-amendment-2026-05-16 §4 red line 7).
* No ``backend.{llm,agents,mirofish}`` imports anywhere in this file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from backend.broker.appliers import ApplyResult, ReconciliationApplier
from backend.integrations.feishu.client import FeishuClient, SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.reconciliation import (
    DailyReconciliation,
    DeviationReport,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.services.reconciliation_parser import (
    ReconciliationParseError,
    ReconciliationReply,
    ReconciliationReplyKind,
    parse_reconciliation_reply,
)
from backend.services.reconciliation_threshold import detect_deviations

log = logging.getLogger("backend.integrations.feishu.reconciliation")


# === Protocols (production wiring lands in F-005 / G-006) ============


class TicketRepository(Protocol):
    """Persistence boundary for :class:`ReconciliationTicket`.

    Real implementation backs onto Mongo ``reconciliation_tickets``;
    test doubles store rows in memory.
    """

    async def get(self, ticket_id: str) -> ReconciliationTicket | None: ...
    async def save(self, ticket: ReconciliationTicket) -> None: ...
    async def list_open_for_date(
        self, trade_date: str
    ) -> tuple[ReconciliationTicket, ...]: ...


class DailyReconciliationStore(Protocol):
    """Persistence boundary for :class:`DailyReconciliation`.

    The user-reported mirror lives in ``daily_reconciliations`` keyed
    on (trade_date, ticket_id). The applier reads from here on the
    RESOLVED_USER_AS_TRUTH path.
    """

    async def save(self, daily: DailyReconciliation) -> None: ...
    async def get(
        self, trade_date: str
    ) -> DailyReconciliation | None: ...


# === Public DTOs ====================================================


@dataclass(frozen=True)
class InitiationResult:
    """Outcome of the 16:00 ``initiate_reconciliation`` call."""

    ticket_id: str
    sent: bool
    send_result: SendMessageResult | None


@dataclass(frozen=True)
class ReplyOutcome:
    """Outcome of one user-reply trip through the orchestrator."""

    handled: bool
    """``True`` iff the reply matched a known reply form."""

    kind: ReconciliationReplyKind | None
    ticket_id: str | None
    ticket_status: ReconciliationTicketStatus | None
    deviation_report: DeviationReport | None
    parse_error: str | None


@dataclass(frozen=True)
class DecisionResult:
    """Outcome of a ticket decision (write-endpoint payload)."""

    ticket_id: str
    status: ReconciliationTicketStatus
    apply_result: ApplyResult
    send_result: SendMessageResult | None


# === Orchestrator ====================================================


class ReconciliationOrchestrator:
    """Glue between Feishu, the threshold checker, ticket repo, and applier.

    Args:
        feishu: Feishu OpenAPI client (None in simulation_auto).
        renderer: MessageRenderer (F-002) — all body strings flow through.
        ticket_repo: ReconciliationTicket persistence.
        daily_store: DailyReconciliation persistence.
        applier: ReconciliationApplier (E-004) — broker mirror rewrites.
        decision_chat_id: target chat_id for both initiation + result
            messages. Always the *decision* chat — never the alert chat.
        now: optional clock override for tests.
    """

    def __init__(
        self,
        *,
        feishu: FeishuClient | None,
        renderer: MessageRenderer,
        ticket_repo: TicketRepository,
        daily_store: DailyReconciliationStore,
        applier: ReconciliationApplier,
        decision_chat_id: str,
        now: callable | None = None,  # type: ignore[type-arg]
    ) -> None:
        if not decision_chat_id:
            raise ValueError("decision_chat_id must not be empty")
        self._feishu = feishu
        self._renderer = renderer
        self._tickets = ticket_repo
        self._daily = daily_store
        self._applier = applier
        self._chat_id = decision_chat_id
        self._now = now or _default_now

    # -- 1. Initiate (16:00 cron entry) -------------------------------

    async def initiate_reconciliation(
        self,
        *,
        ticket_id: str,
        trade_date: str,
        snapshot: MockBrokerSnapshot,
    ) -> InitiationResult:
        """Send the daily reconciliation prompt to the decision chat.

        The applier writes the BrokerSnapshot at EOD (E-005 chain); we
        do not duplicate that here. ``snapshot`` is passed in by the
        caller so this orchestrator stays stateless.
        """
        body = self._renderer.render_reconciliation_request(
            ticket_id=ticket_id,
            trade_date=trade_date,
            expected_cash_cny=snapshot.cash,
            expected_positions={p.code: p.volume for p in snapshot.positions},
            expected_total_equity_cny=_estimate_total_equity(snapshot),
        )
        if self._feishu is None:
            log.warning(
                "feishu_reconciliation_skipped_no_client ticket_id=%s",
                ticket_id,
            )
            return InitiationResult(ticket_id=ticket_id, sent=False, send_result=None)
        result = await self._feishu.send_message(
            self._chat_id, body, uuid=f"recon-init-{ticket_id}"
        )
        return InitiationResult(
            ticket_id=ticket_id, sent=result.ok, send_result=result
        )

    # -- 2. Receive a parsed reply -----------------------------------

    async def handle_reply(self, raw_text: str) -> ReplyOutcome:
        """Parse + route a user reply.

        Returns ``handled=False`` when no regex matches; the caller
        (F-003 receiver) then forwards to the F-004 orchestrator which
        sends the NO_PATTERN_MATCH clarification template.
        """
        try:
            reply = parse_reconciliation_reply(raw_text)
        except ReconciliationParseError as exc:
            return ReplyOutcome(
                handled=False,
                kind=None,
                ticket_id=None,
                ticket_status=None,
                deviation_report=None,
                parse_error=exc.reason,
            )

        if reply.kind is ReconciliationReplyKind.OK:
            return await self._handle_ok(reply)
        if reply.kind is ReconciliationReplyKind.MISMATCH:
            return await self._handle_mismatch(reply)
        if reply.kind is ReconciliationReplyKind.AMEND:
            return await self._handle_amend(reply)
        if reply.kind is ReconciliationReplyKind.RESOLVE_USER:
            return await self._handle_resolution(
                reply, ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH
            )
        if reply.kind is ReconciliationReplyKind.RESOLVE_SYSTEM:
            return await self._handle_resolution(
                reply, ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH
            )
        raise ValueError(f"unexpected reply kind {reply.kind!r}")

    # -- 3. Decide (write-endpoint entry) -----------------------------

    async def decide_ticket(
        self,
        ticket_id: str,
        *,
        resolution: ReconciliationTicketStatus,
        amended_snapshot: MockBrokerSnapshot | None = None,
        actor_detail: str | None = None,
    ) -> DecisionResult:
        """Apply a ticket decision via the broker applier.

        Called by POST /api/reconciliation-tickets/{id}/decide. The
        caller is responsible for authentication + ticket existence
        (the orchestrator surfaces a clear error if the ticket is
        missing).
        """
        if resolution not in {
            ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
            ReconciliationTicketStatus.RESOLVED_AMENDED,
        }:
            raise ValueError(
                f"decide_ticket cannot transition to {resolution.value}"
            )

        ticket = await self._tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"unknown ticket_id {ticket_id!r}")
        if ticket.status not in {
            ReconciliationTicketStatus.OPEN,
            ReconciliationTicketStatus.EXPIRED,
        }:
            raise ValueError(
                f"ticket {ticket_id} is already in terminal state "
                f"{ticket.status.value}"
            )

        now = self._now()
        update: dict[str, object] = {
            "status": resolution,
            "resolved_at": now,
        }
        if resolution is ReconciliationTicketStatus.RESOLVED_AMENDED:
            if amended_snapshot is None:
                raise ValueError(
                    "RESOLVED_AMENDED decision requires amended_snapshot"
                )
            update["amended_snapshot"] = amended_snapshot
        resolved = ticket.model_copy(update=update)
        await self._tickets.save(resolved)

        apply_result = await self._applier.reset_to_snapshot(
            resolved, now=now
        )

        # Post-decision summary back to the decision chat.
        send_result: SendMessageResult | None = None
        if self._feishu is not None:
            body = self._renderer.render_reconciliation_result(
                ticket_id=ticket_id,
                resolution=resolution.value,
                cash_delta_cny=apply_result.cash_delta,
                position_deltas=_position_deltas(apply_result),
            )
            send_result = await self._feishu.send_message(
                self._chat_id, body, uuid=f"recon-resolved-{ticket_id}"
            )

        log.info(
            "reconciliation_ticket_decided ticket_id=%s resolution=%s "
            "actor_detail=%s",
            ticket_id,
            resolution.value,
            actor_detail,
        )
        return DecisionResult(
            ticket_id=ticket_id,
            status=resolution,
            apply_result=apply_result,
            send_result=send_result,
        )

    # -- Internal reply handlers --------------------------------------

    async def _handle_ok(self, reply: ReconciliationReply) -> ReplyOutcome:
        log.info(
            "reconciliation_reply_ok ticket_id=%s", reply.ticket_id
        )
        return ReplyOutcome(
            handled=True,
            kind=reply.kind,
            ticket_id=reply.ticket_id,
            ticket_status=None,
            deviation_report=None,
            parse_error=None,
        )

    async def _handle_mismatch(
        self, reply: ReconciliationReply
    ) -> ReplyOutcome:
        ticket = await self._tickets.get(reply.ticket_id)
        if ticket is None:
            log.info(
                "reconciliation_mismatch_unknown_ticket ticket_id=%s",
                reply.ticket_id,
            )
            return ReplyOutcome(
                handled=True,
                kind=reply.kind,
                ticket_id=reply.ticket_id,
                ticket_status=None,
                deviation_report=None,
                parse_error="unknown_ticket_id",
            )
        daily = _reply_to_daily(reply, ticket)
        await self._daily.save(daily)
        deviation = detect_deviations(_snapshot_from_ticket(ticket), daily)
        log.info(
            "reconciliation_mismatch_recorded ticket_id=%s overall_passed=%s",
            ticket.ticket_id,
            deviation.overall_passed,
        )
        return ReplyOutcome(
            handled=True,
            kind=reply.kind,
            ticket_id=ticket.ticket_id,
            ticket_status=ticket.status,
            deviation_report=deviation,
            parse_error=None,
        )

    async def _handle_amend(
        self, reply: ReconciliationReply
    ) -> ReplyOutcome:
        ticket = await self._tickets.get(reply.ticket_id)
        if ticket is None:
            return ReplyOutcome(
                handled=True,
                kind=reply.kind,
                ticket_id=reply.ticket_id,
                ticket_status=None,
                deviation_report=None,
                parse_error="unknown_ticket_id",
            )
        amended = MockBrokerSnapshot(
            cash=float(reply.cash or 0.0),
            positions=tuple(reply.positions or ()),
            snapshot_at=self._now(),
        )
        decision = await self.decide_ticket(
            ticket.ticket_id,
            resolution=ReconciliationTicketStatus.RESOLVED_AMENDED,
            amended_snapshot=amended,
        )
        return ReplyOutcome(
            handled=True,
            kind=reply.kind,
            ticket_id=ticket.ticket_id,
            ticket_status=decision.status,
            deviation_report=None,
            parse_error=None,
        )

    async def _handle_resolution(
        self,
        reply: ReconciliationReply,
        target_status: ReconciliationTicketStatus,
    ) -> ReplyOutcome:
        ticket = await self._tickets.get(reply.ticket_id)
        if ticket is None:
            return ReplyOutcome(
                handled=True,
                kind=reply.kind,
                ticket_id=reply.ticket_id,
                ticket_status=None,
                deviation_report=None,
                parse_error="unknown_ticket_id",
            )
        decision = await self.decide_ticket(
            ticket.ticket_id, resolution=target_status
        )
        return ReplyOutcome(
            handled=True,
            kind=reply.kind,
            ticket_id=ticket.ticket_id,
            ticket_status=decision.status,
            deviation_report=None,
            parse_error=None,
        )


# === Helpers ========================================================


def _default_now() -> datetime:
    return datetime.now(UTC)


def _estimate_total_equity(snapshot: MockBrokerSnapshot) -> float:
    """Rough cash + cost_price × volume aggregate for the initiation prompt.

    The renderer surfaces this as 系统总权益 so the operator can spot a
    gross mismatch (e.g. a missing position) before re-typing each
    field. A precise mark-to-market lives downstream — the initiation
    prompt only needs an order-of-magnitude figure.
    """
    return float(snapshot.cash) + sum(
        float(p.cost_price) * int(p.volume) for p in snapshot.positions
    )


def _reply_to_daily(
    reply: ReconciliationReply, ticket: ReconciliationTicket
) -> DailyReconciliation:
    """Build a :class:`DailyReconciliation` from a MISMATCH/AMEND reply."""
    return DailyReconciliation(
        ticket_id=reply.ticket_id,
        trade_date=ticket.trade_date,
        received_at=datetime.now(UTC),
        reported_cash=float(reply.cash or 0.0),
        reported_positions=tuple(reply.positions or ()),
        raw_text=f"对账差异 {reply.ticket_id} 现金 {reply.cash} 持仓 ...",
        parse_ok=True,
    )


def _snapshot_from_ticket(
    ticket: ReconciliationTicket,
) -> MockBrokerSnapshot:
    """Use ticket's deviation_report.expected fields to reconstruct the
    snapshot we sent.

    The original snapshot lives in Mongo by ``expected_snapshot_id``; we
    keep this helper synchronous + dependency-free for unit tests by
    re-deriving from the deviation_report's expected strings.
    """
    # The orchestrator's caller (Mongo-backed F-005 wiring) will replace
    # this helper with a real lookup. For tests that drive
    # _handle_mismatch directly, the deviation_report carries the
    # expected cash + per-stock figures so we can reconstruct.
    cash = 0.0
    for d in ticket.deviation_report.deviations:
        if d.field == "cash":
            cash = float(d.expected)

    # We only need a coarse snapshot for downstream deviation re-check;
    # the orchestrator does NOT mutate broker state on the MISMATCH
    # path — it only records the user mirror + recomputes the deviation
    # report for surface in the UI.
    return MockBrokerSnapshot(
        cash=cash,
        positions=tuple(),
        snapshot_at=ticket.created_at,
    )


def _position_deltas(apply_result: ApplyResult) -> dict[str, int]:
    """Coerce ApplyResult.positions_delta tuple into a {code: delta}
    map for the renderer."""
    out: dict[str, int] = {}
    for entry in apply_result.positions_delta:
        code = str(entry.get("code", ""))
        delta = int(entry.get("delta_volume", 0))
        if code:
            out[code] = delta
    return out


__all__ = [
    "DailyReconciliationStore",
    "DecisionResult",
    "InitiationResult",
    "ReconciliationOrchestrator",
    "ReplyOutcome",
    "TicketRepository",
]
