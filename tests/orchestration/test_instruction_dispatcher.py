"""InstructionDispatcher tests (U-B2).

The dispatcher is the feishu_interactive outbound edge: durable outbox
claim → Feishu send → state-machine VALIDATED→DISPATCHED → ledger
PLAN_DISPATCHED + feishu_message_id → audit FEISHU_MESSAGE_SENT. The
critical invariant is **restart/retry idempotency**: the same
instruction_id is never sent to the owner twice.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.audit.models import AuditEventType
from backend.models.instruction import InstructionSide, InstructionStatus
from backend.models.ledger import LedgerEventKind
from backend.orchestration.instruction_dispatcher import (
    DISPATCH_AUDIT_NAMESPACE,
    InMemoryOutboxRepository,
    InstructionDispatcher,
    OutboundSignal,
    OutboxStatus,
)
from tests.orchestration.conftest import SHANGHAI, FakeFeishuSender, make_plan

_CHAT = "oc_decision_group_0001"
_NOW = dt.datetime(2026, 5, 15, 10, 0, 5, tzinfo=SHANGHAI)


def _signal(plan, text="【QuantMind 买入信号】..."):
    return OutboundSignal(plan=plan, wire_text=text)


async def _make(
    *,
    ledger,
    audit_store,
    sender: FakeFeishuSender | None = None,
    outbox: InMemoryOutboxRepository | None = None,
):
    sender = sender or FakeFeishuSender()
    outbox = outbox or InMemoryOutboxRepository()
    dispatcher = InstructionDispatcher(
        feishu_client=sender,
        decision_chat_id=_CHAT,
        outbox=outbox,
        ledger=ledger,
        audit_store=audit_store,
    )
    return dispatcher, sender, outbox


class TestDispatchHappyPath:
    async def test_sends_and_records_full_lifecycle(self, ledger, audit_store):
        plan = make_plan()
        await ledger.open_for_plan(plan)
        dispatcher, sender, outbox = await _make(
            ledger=ledger, audit_store=audit_store
        )

        outcome = await dispatcher.dispatch(_signal(plan), now=_NOW)

        # 1. Outbound send: decision chat, wire text, uuid == instruction_id.
        assert len(sender.calls) == 1
        call = sender.calls[0]
        assert call["chat_id"] == _CHAT
        assert call["content"] == "【QuantMind 买入信号】..."
        assert call["uuid"] == plan.instruction_id

        # 2. Outbox marked SENT with the returned message id.
        entry = await outbox.get(plan.instruction_id)
        assert entry is not None
        assert entry.status is OutboxStatus.SENT
        assert entry.feishu_message_id == sender.message_id

        # 3. Ledger PLAN_DISPATCHED + feishu_message_id correlation handle.
        led = await ledger.get_by_instruction(plan.instruction_id)
        kinds = [e.kind for e in led.events]
        assert LedgerEventKind.PLAN_DISPATCHED in kinds
        assert led.feishu_message_id == sender.message_id

        # 4. Audit FEISHU_MESSAGE_SENT.
        events = audit_store._mongo.documents  # type: ignore[attr-defined]
        assert any(
            e["event_type"] == AuditEventType.FEISHU_MESSAGE_SENT.value
            and e.get("reason_namespace") == DISPATCH_AUDIT_NAMESPACE
            for e in events
        )

        # 5. Outcome summary.
        assert outcome.action == "dispatched"
        assert outcome.final_status is InstructionStatus.DISPATCHED
        assert outcome.feishu_message_id == sender.message_id


class TestOutboxIdempotency:
    async def test_second_dispatch_after_restart_does_not_resend(
        self, ledger, audit_store
    ):
        """The outbox survives a restart (same repo) → no double send."""
        plan = make_plan()
        await ledger.open_for_plan(plan)
        outbox = InMemoryOutboxRepository()
        dispatcher, sender, _ = await _make(
            ledger=ledger, audit_store=audit_store, outbox=outbox
        )

        first = await dispatcher.dispatch(_signal(plan), now=_NOW)
        # "Restart": a fresh dispatcher instance, but the SAME durable outbox.
        dispatcher2, sender2, _ = await _make(
            ledger=ledger, audit_store=audit_store, outbox=outbox,
            sender=FakeFeishuSender(message_id="om_test_2"),
        )
        second = await dispatcher2.dispatch(
            _signal(plan), now=_NOW + dt.timedelta(hours=3)
        )

        assert first.action == "dispatched"
        assert second.action == "skipped_duplicate"
        assert len(sender2.calls) == 0  # never re-sent
        # The original message id is preserved on the skip outcome.
        assert second.feishu_message_id == sender.message_id

    async def test_definitive_send_failure_releases_claim_then_retry_resends(
        self, ledger, audit_store
    ):
        """An API rejection (nothing delivered) releases the claim so a
        retry cleanly re-claims and re-sends."""
        plan = make_plan()
        await ledger.open_for_plan(plan)
        # First send rejected (code != 0), second accepted.
        sender = FakeFeishuSender(fail_first_n=1)
        outbox = InMemoryOutboxRepository()
        dispatcher, _, _ = await _make(
            ledger=ledger, audit_store=audit_store, sender=sender, outbox=outbox
        )

        failed = await dispatcher.dispatch(_signal(plan), now=_NOW)
        assert failed.action == "send_failed"
        # Claim released (definitive non-delivery) — retry can re-claim.
        assert await outbox.get(plan.instruction_id) is None
        led = await ledger.get_by_instruction(plan.instruction_id)
        assert LedgerEventKind.PLAN_DISPATCHED not in [e.kind for e in led.events]

        retry = await dispatcher.dispatch(
            _signal(plan), now=_NOW + dt.timedelta(seconds=30)
        )
        assert retry.action == "dispatched"
        assert len(sender.calls) == 2  # re-sent after the release
        entry = await outbox.get(plan.instruction_id)
        assert entry.status is OutboxStatus.SENT

    async def test_pre_existing_pending_claim_is_not_resent(
        self, ledger, audit_store
    ):
        """An ambiguous PENDING claim (lost try_claim race / crash mid-send)
        is never blindly re-sent — at-most-once (Codex P1)."""
        plan = make_plan()
        await ledger.open_for_plan(plan)
        outbox = InMemoryOutboxRepository()
        # Another worker / a crashed prior attempt already holds the claim.
        await outbox.try_claim(plan.instruction_id, at=_NOW)
        dispatcher, sender, _ = await _make(
            ledger=ledger, audit_store=audit_store, outbox=outbox
        )

        outcome = await dispatcher.dispatch(_signal(plan), now=_NOW)

        assert outcome.action == "skipped_in_flight"
        assert len(sender.calls) == 0  # never re-sent on ambiguous PENDING

    async def test_sent_without_ledger_recovers_bookkeeping(
        self, ledger, audit_store
    ):
        """A crash after mark_sent but before the ledger write is recovered
        idempotently on the next dispatch — no re-send (Codex P2)."""
        plan = make_plan()
        await ledger.open_for_plan(plan)
        outbox = InMemoryOutboxRepository()
        # Simulate the crash window: message delivered + SENT recorded, but
        # the PLAN_DISPATCHED ledger row never written.
        await outbox.try_claim(plan.instruction_id, at=_NOW)
        await outbox.mark_sent(plan.instruction_id, message_id="om_crash", at=_NOW)
        dispatcher, sender, _ = await _make(
            ledger=ledger, audit_store=audit_store, outbox=outbox
        )

        outcome = await dispatcher.dispatch(
            _signal(plan), now=_NOW + dt.timedelta(seconds=30)
        )

        assert outcome.action == "skipped_duplicate"
        assert len(sender.calls) == 0  # never re-sent
        led = await ledger.get_by_instruction(plan.instruction_id)
        assert LedgerEventKind.PLAN_DISPATCHED in [e.kind for e in led.events]
        assert led.feishu_message_id == "om_crash"

    async def test_re_presented_dispatched_plan_recovers_not_rejected(
        self, ledger, audit_store
    ):
        """A SENT id re-presented with a non-VALIDATED (DISPATCHED) status is
        recovered idempotently, not rejected by the VALIDATED guard (Codex
        verify #2)."""
        plan = make_plan(status=InstructionStatus.DISPATCHED)
        await ledger.open_for_plan(plan)
        outbox = InMemoryOutboxRepository()
        await outbox.try_claim(plan.instruction_id, at=_NOW)
        await outbox.mark_sent(plan.instruction_id, message_id="om_x", at=_NOW)
        dispatcher, sender, _ = await _make(
            ledger=ledger, audit_store=audit_store, outbox=outbox
        )

        outcome = await dispatcher.dispatch(_signal(plan), now=_NOW)

        assert outcome.action == "skipped_duplicate"
        assert len(sender.calls) == 0
        led = await ledger.get_by_instruction(plan.instruction_id)
        assert LedgerEventKind.PLAN_DISPATCHED in [e.kind for e in led.events]


class TestReadModelHooks:
    async def test_upserts_dispatched_plan_and_publishes_update(
        self, ledger, audit_store
    ):
        plan = make_plan()
        await ledger.open_for_plan(plan)
        upserted: list = []
        published: list = []

        class _Repo:
            async def upsert(self, p):
                upserted.append(p)

        async def _publish(p):
            published.append(p)

        sender = FakeFeishuSender()
        dispatcher = InstructionDispatcher(
            feishu_client=sender,
            decision_chat_id=_CHAT,
            outbox=InMemoryOutboxRepository(),
            ledger=ledger,
            audit_store=audit_store,
            plan_repository=_Repo(),
            publish_update=_publish,
        )

        await dispatcher.dispatch(_signal(plan), now=_NOW)

        # Both hooks receive the DISPATCHED plan (not the VALIDATED input).
        assert len(upserted) == 1
        assert upserted[0].status is InstructionStatus.DISPATCHED
        assert len(published) == 1
        assert published[0].status is InstructionStatus.DISPATCHED


class TestDispatchGuards:
    async def test_hold_plan_is_rejected(self, ledger, audit_store):
        plan = make_plan(side=InstructionSide.HOLD)
        dispatcher, sender, _ = await _make(ledger=ledger, audit_store=audit_store)
        with pytest.raises(ValueError, match="HOLD"):
            await dispatcher.dispatch(_signal(plan), now=_NOW)
        assert len(sender.calls) == 0

    async def test_non_validated_plan_is_rejected(self, ledger, audit_store):
        plan = make_plan(status=InstructionStatus.DRAFT)
        dispatcher, sender, _ = await _make(ledger=ledger, audit_store=audit_store)
        with pytest.raises(ValueError, match="VALIDATED"):
            await dispatcher.dispatch(_signal(plan), now=_NOW)
        assert len(sender.calls) == 0

    async def test_empty_wire_text_is_rejected(self, ledger, audit_store):
        plan = make_plan()
        await ledger.open_for_plan(plan)
        dispatcher, sender, _ = await _make(ledger=ledger, audit_store=audit_store)
        with pytest.raises(ValueError, match="wire_text"):
            await dispatcher.dispatch(_signal(plan, text=""), now=_NOW)
        assert len(sender.calls) == 0
