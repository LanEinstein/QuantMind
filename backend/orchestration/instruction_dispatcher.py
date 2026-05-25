"""Outbound InstructionPlan dispatcher for feishu_interactive (Phase U-B2).

This is the missing *outbound* edge of the double-line MVP. When the
process runs in ``feishu_interactive``, a VALIDATED BUY/SELL
InstructionPlan must be rendered and sent to the owner's **decision
group** so the owner executes it manually in the 同花顺 simulation
account and reports the fill back (which the existing inbound
orchestrator mirrors onto the MockBroker). The dispatcher does **not**
auto-fill the broker — that is the SimulationExecutor's job in
``simulation_auto``, and :class:`RouteCoordinator` guarantees exactly one
of the two paths runs per plan (no double execution, Codex P0 #4).

Durable idempotency (Codex P0 #5): Feishu's server-side ``uuid`` dedup
only covers a 1-hour window, so a restart/retry an hour later could
re-send the same BUY to the owner — a real double-buy risk. The
dispatcher therefore claims a row in a **durable outbox** keyed by
``instruction_id`` *before* sending and marks it ``SENT`` immediately
*after* a confirmed send (before the rest of the bookkeeping). On restart
the SENT claim short-circuits, so the owner is never messaged twice.

Reuses (no re-implementation): ``FeishuClient.send_message`` /
``instruction_state_machine.transition`` / ``DecisionLedgerService`` /
``AuditStore``. Renders nothing itself — the caller (a Line-1/Line-2
runner) passes the already-rendered wire text via :class:`OutboundSignal`,
because the renderer needs run-specific context (template / anomaly_reason
/ stop_price) that is not stored on the frozen InstructionPlan.

LLM red line: imports NO ``backend.{llm,agents,agents_team,mirofish}``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.renderer import FeishuMessageKind
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.models.ledger import LedgerEventKind
from backend.services.instruction_state_machine import transition as _transition
from backend.services.ledger import DecisionLedgerService

log = structlog.get_logger(component="orchestration.instruction_dispatcher")

DISPATCH_AUDIT_NAMESPACE = "feishu_dispatch"
"""``reason_namespace`` on the FEISHU_MESSAGE_SENT audit row for an
outbound InstructionPlan dispatch (distinguishes it from alert / clarify
/ reconciliation sends, which use their own namespaces)."""


class OutboxStatus(StrEnum):
    """Lifecycle of a durable outbox claim."""

    PENDING = "pending"
    """Claimed, send in flight (or a prior attempt crashed mid-send)."""
    SENT = "sent"
    """Send confirmed by Feishu; never re-send for this instruction_id."""


@dataclass(frozen=True)
class OutboxEntry:
    """A durable outbox row keyed by ``instruction_id``."""

    instruction_id: str
    status: OutboxStatus
    claimed_at: datetime
    sent_at: datetime | None = None
    feishu_message_id: str | None = None


@runtime_checkable
class OutboxRepository(Protocol):
    """Durable claim store guarding against duplicate outbound sends.

    Production wires a Mongo-backed implementation (U-D1) whose
    ``try_claim`` is an atomic unique-key insert; tests + the dry-run use
    :class:`InMemoryOutboxRepository`. Both honour the same contract:
    a SENT claim is terminal and never re-sent.
    """

    async def get(self, instruction_id: str) -> OutboxEntry | None: ...

    async def try_claim(self, instruction_id: str, *, at: datetime) -> bool:
        """Insert a PENDING claim. Return True iff newly created.

        Atomic (unique-key insert in the Mongo impl): the call that creates
        the row returns True and is the *only* one entitled to send; a
        second claim for an existing id is a no-op returning False (its
        status is unchanged). This is the at-most-once gate.
        """
        ...

    async def release(self, instruction_id: str) -> None:
        """Delete a PENDING claim so a later dispatch can re-claim + re-send.

        Only ever called after a **definitive** non-delivery (the Feishu API
        returned ``ok=False`` — the message was rejected, nothing reached the
        owner). A claim whose send outcome is *unknown* (transport exception
        / crash) is deliberately NOT released, so it is never auto-resent.
        Releasing a SENT row is a no-op (fail-closed against re-send).
        """
        ...

    async def mark_sent(
        self, instruction_id: str, *, message_id: str | None, at: datetime
    ) -> None: ...


class InMemoryOutboxRepository:
    """Reference + test implementation of :class:`OutboxRepository`.

    Surviving across two dispatcher instances in a test models the durable
    store surviving a process restart.
    """

    def __init__(self) -> None:
        self._rows: dict[str, OutboxEntry] = {}

    async def get(self, instruction_id: str) -> OutboxEntry | None:
        return self._rows.get(instruction_id)

    async def try_claim(self, instruction_id: str, *, at: datetime) -> bool:
        if instruction_id in self._rows:
            return False
        self._rows[instruction_id] = OutboxEntry(
            instruction_id=instruction_id,
            status=OutboxStatus.PENDING,
            claimed_at=at,
        )
        return True

    async def release(self, instruction_id: str) -> None:
        existing = self._rows.get(instruction_id)
        # Never release a SENT row — that would re-open a delivered signal
        # for re-send (the exact double-execution the outbox prevents).
        if existing is not None and existing.status is OutboxStatus.PENDING:
            del self._rows[instruction_id]

    async def mark_sent(
        self, instruction_id: str, *, message_id: str | None, at: datetime
    ) -> None:
        existing = self._rows.get(instruction_id)
        claimed_at = existing.claimed_at if existing is not None else at
        self._rows[instruction_id] = OutboxEntry(
            instruction_id=instruction_id,
            status=OutboxStatus.SENT,
            claimed_at=claimed_at,
            sent_at=at,
            feishu_message_id=message_id,
        )


@runtime_checkable
class FeishuSender(Protocol):
    """Narrow async view of :class:`FeishuClient` the dispatcher needs."""

    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        msg_type: str = "text",
        uuid: str | None = None,
    ) -> SendMessageResult: ...


@runtime_checkable
class _PlanRepository(Protocol):
    async def upsert(self, plan: InstructionPlan) -> None: ...


@dataclass(frozen=True)
class OutboundSignal:
    """A render-complete signal ready to route.

    The caller (runner) renders ``wire_text`` via
    :class:`backend.integrations.feishu.renderer.MessageRenderer` because
    the renderer needs run-specific context not stored on the plan. The
    dispatcher only transports it.
    """

    plan: InstructionPlan
    wire_text: str
    message_kind: FeishuMessageKind = FeishuMessageKind.INSTRUCTION_PLAN


@dataclass(frozen=True)
class DispatchOutcome:
    """Audit-grade summary of one dispatch call."""

    instruction_id: str
    action: str
    """``dispatched`` | ``skipped_duplicate`` | ``send_failed``."""
    final_status: InstructionStatus | None
    feishu_message_id: str | None


class InstructionDispatcher:
    """Send a VALIDATED plan to the decision group, exactly once."""

    def __init__(
        self,
        *,
        feishu_client: FeishuSender,
        decision_chat_id: str,
        outbox: OutboxRepository,
        ledger: DecisionLedgerService,
        audit_store: AuditStore,
        plan_repository: _PlanRepository | None = None,
        publish_update: Callable[[InstructionPlan], Awaitable[None]] | None = None,
    ) -> None:
        if not decision_chat_id:
            raise ValueError(
                "decision_chat_id must be the decision group's open_chat_id "
                "(FEISHU_DECISION_CHAT_ID); empty value forbidden"
            )
        self._feishu = feishu_client
        self._chat_id = decision_chat_id
        self._outbox = outbox
        self._ledger = ledger
        self._audit = audit_store
        self._plans = plan_repository
        # Optional async WS-publish hook (injected by U-D1 main.py so the
        # dispatcher stays decoupled from backend.data — orchestration must
        # not import backend.data per the Phase-X isolation lint).
        self._publish_update = publish_update

    async def dispatch(
        self, signal: OutboundSignal, *, now: datetime
    ) -> DispatchOutcome:
        """Render-complete signal → decision group, at most once.

        Idempotency model (Codex U-B2 review): the durable outbox claim is
        the **at-most-once gate**. Only the call that wins the atomic
        ``try_claim`` may send. A SENT row short-circuits (and recovers any
        missing bookkeeping idempotently). A pre-existing PENDING row whose
        send outcome is unknown is *never* blindly re-sent — that is the
        stale-claim double-send the outbox exists to prevent.
        """
        plan = signal.plan
        # Structural guards apply in every state (HOLD / empty text never
        # dispatch). The VALIDATED-status guard is deferred until AFTER the
        # SENT short-circuit so an already-sent id that is re-presented with
        # a non-VALIDATED status is still recovered idempotently rather than
        # rejected (Codex U-B2 verify #2).
        self._guard_structural(signal)

        # 1. SENT short-circuit. The owner already received this signal —
        #    never re-send. Recover any post-send bookkeeping that a crash
        #    may have skipped (idempotent), then report the duplicate.
        existing = await self._outbox.get(plan.instruction_id)
        if existing is not None and existing.status is OutboxStatus.SENT:
            await self._finalize_dispatch(
                plan, signal, message_id=existing.feishu_message_id, now=now
            )
            log.info(
                "dispatch_skipped_duplicate",
                instruction_id=plan.instruction_id,
                feishu_message_id=existing.feishu_message_id,
            )
            return DispatchOutcome(
                instruction_id=plan.instruction_id,
                action="skipped_duplicate",
                final_status=InstructionStatus.DISPATCHED,
                feishu_message_id=existing.feishu_message_id,
            )

        # Fresh send requires a VALIDATED plan (the SENT recovery path above
        # tolerates any status; this guard only gates a NEW outbound send).
        if plan.status is not InstructionStatus.VALIDATED:
            raise ValueError(
                "InstructionDispatcher requires VALIDATED status for a fresh "
                f"send; got {plan.status.value}"
            )

        # 2. Claim BEFORE sending. ``try_claim`` is the at-most-once gate:
        #    only the call that *creates* the row may send. If a claim
        #    already exists (a concurrent worker, or a prior attempt whose
        #    delivery is unknown), do NOT send — re-sending a stale PENDING
        #    row after Feishu's 1h uuid-dedup window would double-message the
        #    owner (Codex P1). Missing a signal is the safe failure; a stuck
        #    PENDING claim is surfaced for manual recovery.
        claimed = await self._outbox.try_claim(plan.instruction_id, at=now)
        if not claimed:
            log.warning(
                "dispatch_skipped_in_flight",
                instruction_id=plan.instruction_id,
            )
            return DispatchOutcome(
                instruction_id=plan.instruction_id,
                action="skipped_in_flight",
                final_status=None,
                feishu_message_id=None,
            )

        # 3. Send. uuid=instruction_id arms Feishu's 1h server-side dedup as
        #    a second line of defence behind the durable claim.
        send = await self._feishu.send_message(
            self._chat_id, signal.wire_text, uuid=plan.instruction_id
        )
        if not send.ok:
            # Definitive API rejection (code != 0) → nothing was delivered.
            # Release the claim so a later retry can cleanly re-claim and
            # re-send (safe: no duplicate message reached the owner). A
            # transport EXCEPTION is different — it propagates with the claim
            # left PENDING, so an ambiguous delivery is never auto-resent.
            await self._outbox.release(plan.instruction_id)
            await self._audit_send(plan, signal, send, ok=False, at=now)
            log.warning(
                "dispatch_send_failed",
                instruction_id=plan.instruction_id,
                code=send.code,
            )
            return DispatchOutcome(
                instruction_id=plan.instruction_id,
                action="send_failed",
                final_status=None,
                feishu_message_id=None,
            )

        # 4. Mark SENT IMMEDIATELY after a confirmed send — before the rest
        #    of the bookkeeping — so a crash here can only lose a ledger row
        #    (idempotently recovered on the next dispatch via step 1) and can
        #    NEVER cause a second send.
        await self._outbox.mark_sent(
            plan.instruction_id, message_id=send.message_id, at=now
        )

        # 5. Post-send bookkeeping (state machine + ledger + audit + read
        #    model). Idempotent so the step-1 recovery path is a no-op once
        #    it has run.
        await self._finalize_dispatch(
            plan, signal, message_id=send.message_id, now=now
        )

        log.info(
            "dispatch_sent",
            instruction_id=plan.instruction_id,
            feishu_message_id=send.message_id,
            message_kind=signal.message_kind.value,
        )
        return DispatchOutcome(
            instruction_id=plan.instruction_id,
            action="dispatched",
            final_status=InstructionStatus.DISPATCHED,
            feishu_message_id=send.message_id,
        )

    # -- helpers --------------------------------------------------------

    async def _finalize_dispatch(
        self,
        plan: InstructionPlan,
        signal: OutboundSignal,
        *,
        message_id: str | None,
        now: datetime,
    ) -> None:
        """Idempotently record the DISPATCHED bookkeeping for a sent signal.

        Gated on the absence of a ``PLAN_DISPATCHED`` ledger row so a retry
        (or the SENT short-circuit) running after a crash between
        ``mark_sent`` and the bookkeeping fills the gap exactly once (Codex
        P2). Ordering:

        1. **Ledger ``PLAN_DISPATCHED`` marker FIRST** — the single
           non-idempotent, correctness-critical step (it backs the decision
           correlation graph + acceptance metrics) and the idempotency gate.
           Writing it first means a persisted ``DISPATCHED`` read model can
           never exist *without* the marker, so a re-presented plan is never
           wrongly treated as un-dispatched (Codex verify #2).
        2. Then the **best-effort, fail-open** derived bookkeeping: the audit
           row (append-only) + read-model upsert (idempotent by id) + WS
           publish (dedup-tolerant). A crash strictly between the marker and
           these leaves at most a benign audit gap / cosmetically-stale read
           model — never a decision error — consistent with the project's
           "fail-open for infra glitches" policy; the authoritative ledger is
           already correct.

        Concurrency note: a duplicate ``PLAN_DISPATCHED`` could only arise if
        two writers finalised the *same* instruction_id concurrently (the
        absent-row pre-check is not atomic with the append). Production runs
        the orchestration layer single-instance / single-writer — the same
        deployment invariant the EOD pipeline + AuditStore rely on — so two
        concurrent finalisations of one id cannot occur.
        """
        entry = await self._ledger.get_by_instruction(plan.instruction_id)
        already_dispatched = entry is not None and any(
            e.kind is LedgerEventKind.PLAN_DISPATCHED for e in entry.events
        )
        if already_dispatched:
            return

        # State machine VALIDATED → DISPATCHED. ``allow_post_close`` is True
        # because this is a SYSTEM/scheduler outbound action (Line-1 09:00,
        # Line-2 intraday), mirroring SimulationExecutor's routed DISPATCHED —
        # not a user-driven status change (P0-4 §1.6). A recovery path may
        # already hold a DISPATCHED plan (DISPATCHED→DISPATCHED is not a legal
        # transition), so only transition from VALIDATED; otherwise reuse it.
        dispatched = (
            _transition(
                plan, InstructionStatus.DISPATCHED, at=now, allow_post_close=True
            )
            if plan.status is InstructionStatus.VALIDATED
            else plan
        )

        # 1. Marker FIRST — non-idempotent + correctness-critical + the gate.
        await self._ledger.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.PLAN_DISPATCHED,
            at=now,
            actor="SYSTEM",
            feishu_message_id=message_id,
            payload={
                "mode": "feishu_interactive",
                "message_kind": signal.message_kind.value,
                "feishu_message_id": message_id,
            },
        )

        # 2. Best-effort / fail-open derived bookkeeping (audit append-only;
        #    read-model upsert keyed by id; dedup-tolerant WS publish).
        await self._audit_send(
            plan, signal, _sent_result(message_id), ok=True, at=now
        )
        if self._plans is not None:
            await self._plans.upsert(dispatched)
        if self._publish_update is not None:
            await self._publish_update(dispatched)

    @staticmethod
    def _guard_structural(signal: OutboundSignal) -> None:
        """Structural guards that hold in every state (status-independent)."""
        plan = signal.plan
        if plan.side is InstructionSide.HOLD:
            raise ValueError(
                "InstructionDispatcher: HOLD plans never route to Feishu "
                "(CLAUDE.md §2.7); upstream caller has a bug"
            )
        if not signal.wire_text:
            raise ValueError(
                "OutboundSignal.wire_text must be the rendered message text; "
                "empty value forbidden"
            )

    async def _audit_send(
        self,
        plan: InstructionPlan,
        signal: OutboundSignal,
        send: SendMessageResult,
        *,
        ok: bool,
        at: datetime,
    ) -> None:
        await self._audit.write(
            event_type=AuditEventType.FEISHU_MESSAGE_SENT,
            actor=AuditActor.SYSTEM,
            resource_type="instruction_plan",
            resource_id=plan.instruction_id,
            payload={
                "message_kind": signal.message_kind.value,
                "side": plan.side.value,
                "stock_code": plan.stock_code,
                "signal_id": plan.signal_id,
                "feishu_message_id": send.message_id,
                "ok": ok,
                "code": send.code,
            },
            outcome=AuditOutcome.SUCCESS if ok else AuditOutcome.FAILURE,
            correlation_id=plan.instruction_id,
            reason_namespace=DISPATCH_AUDIT_NAMESPACE,
            timestamp=at,
        )


def _sent_result(message_id: str | None) -> SendMessageResult:
    """A synthetic OK result for the bookkeeping-recovery audit row.

    The recovery path (SENT short-circuit / post-crash retry) has no live
    :class:`SendMessageResult` — the message was already delivered — so it
    reconstructs a code-0 result carrying the stored ``message_id``.
    """
    return SendMessageResult(
        ok=True, code=0, msg="", message_id=message_id, log_id=None
    )


__all__ = [
    "DISPATCH_AUDIT_NAMESPACE",
    "DispatchOutcome",
    "FeishuSender",
    "InMemoryOutboxRepository",
    "InstructionDispatcher",
    "OutboundSignal",
    "OutboxEntry",
    "OutboxRepository",
    "OutboxStatus",
]

# Silence unused-import warning for the dataclass field helper.
_ = field
