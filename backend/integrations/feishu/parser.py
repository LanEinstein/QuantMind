"""Feishu inbound message orchestrator (F-004).

Bridges the WebSocket receiver (F-003) to the parser (B-003 ->
:mod:`backend.services.execution_report_parser`) and the applier
(E-004 -> :mod:`backend.broker.appliers`). When parsing succeeds we
route the report to the applier; when it fails we send one of the five
pre-written clarification templates (F-002) and write the
``EXECUTION_REPORT_PARSE_FAILED`` audit event (P1-6 amendment #27 /
[[feedback_codex_findings_real]]).

Red lines (P0-2 §2 / P0-4 §1):

* The frontend backup channel and the Feishu main path share *this*
  orchestrator — :class:`backend.execution.regex_patterns` is the
  single source of truth. The receiver injects the channel.
* AMBIGUOUS path **never** updates MockBroker (P0-2 §2.5 / P0-4 §1.1.1).
* Clarification goes through :class:`MessageRenderer` (F-002); LLMs
  never compose Feishu wire text (P0-2 §1.2).
* Clarification is sent to the **decision** chat (the same chat the
  inbound message came from), not to the alert chat
  (P0-2-amendment-2026-05-16 §4 red line 7).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.broker.appliers import ApplyResult, ExecutionReportApplier
from backend.integrations.feishu.client import FeishuClient, SendMessageResult
from backend.integrations.feishu.events import ReceivedMessage
from backend.integrations.feishu.renderer import (
    ClarificationTemplate,
    MessageRenderer,
)
from backend.models.execution import (
    ExecutionReport,
    ExecutionReportChannel,
    ExecutionReportKind,
)
from backend.models.instruction import InstructionPlan, InstructionSide
from backend.services.execution_report_parser import (
    ExecutionReportParseError,
    parse_execution_report,
)

_VOLUME_LOT_SIZE = 100


def _cross_check_volume(
    report: ExecutionReport, plan: InstructionPlan
) -> str | None:
    """Return a reason tag when the report's volumes don't match the plan.

    Reasons (mirror the parser's ``ExecutionReportParseError.reason`` set
    so the audit row uses the same vocabulary):

    * ``volume_mismatch_filled`` — FILLED with filled_volume != plan.volume.
    * ``volume_mismatch_partial_sum`` — PARTIAL with filled+remain != plan.volume.
    * ``volume_lot_violation`` — any volume not a multiple of 100.

    ``None`` means the report passes the volume cross-check. UNFILLED
    reports carry no volume by schema, so always pass here.
    """
    if report.kind is ExecutionReportKind.UNFILLED:
        return None
    if plan.volume is None:
        # HOLD plan should never reach this point (HOLD is not
        # routable), but fail-closed if it ever did.
        return "field_cross_check_failed"

    if report.kind is ExecutionReportKind.FILLED:
        if report.filled_volume != plan.volume:
            return "volume_mismatch_filled"
        if report.filled_volume % _VOLUME_LOT_SIZE != 0:
            return "volume_lot_violation"
    elif report.kind is ExecutionReportKind.PARTIAL:
        filled = report.filled_volume or 0
        remain = report.remain_volume or 0
        if filled + remain != plan.volume:
            return "volume_mismatch_partial_sum"
        if (
            filled % _VOLUME_LOT_SIZE != 0
            or remain % _VOLUME_LOT_SIZE != 0
        ):
            return "volume_lot_violation"
    return None

log = logging.getLogger("backend.integrations.feishu.parser")


# === Lookup Protocol =================================================


class InstructionPlanLookup(Protocol):
    """Resolve an ``instruction_id`` → :class:`InstructionPlan`.

    Implementations live in the orchestration layer (Phase F services
    + Mongo-backed repository). Returning ``None`` distinguishes
    *unknown* (no such id) from *expired* (callers check ``valid_until``
    themselves) so the orchestrator can route to the right
    clarification template.
    """

    async def get(self, instruction_id: str) -> InstructionPlan | None: ...


# === Result envelope =================================================


@dataclass(frozen=True)
class ParseOutcome:
    """Summary of one inbound message's journey through the orchestrator.

    The dataclass is the public contract surfaced to monitoring +
    tests; the audit + Feishu side effects are observable separately.
    """

    success: bool
    """``True`` iff the report was parsed AND applied to MockBroker."""

    ambiguous: bool
    """``True`` iff parsing failed and a clarification was dispatched."""

    instruction_id: str | None
    """Best-effort instruction_id — populated whenever the parser
    reached the regex match step, even when the cross-check failed."""

    template_id: ClarificationTemplate | None
    """Clarification template id selected on the ambiguous path."""

    apply_result: ApplyResult | None
    """Applier delta on the success path."""

    send_result: SendMessageResult | None
    """Outcome of the clarification send (None on success path)."""


# === Orchestrator ====================================================


class ExecutionReportOrchestrator:
    """Glue between the WS receiver, parser, applier, and Feishu OpenAPI.

    Stateless — each handle call resolves its own context. Use one
    instance per process (the dependencies — broker, audit, lookup —
    are shared singletons).

    Args:
        applier: ExecutionReportApplier (E-004 surface).
        plan_lookup: resolves instruction_id → InstructionPlan.
        feishu: FeishuClient for outbound clarification messages;
            ``None`` is acceptable for the frontend backup channel
            where the front-end already shows clarifications via the
            POST response.
        renderer: MessageRenderer (F-002). Injected so tests can stub.
        audit: AuditStore for the EXECUTION_REPORT_PARSE_FAILED event.
        now: optional clock override for chase / expiry tests.
    """

    def __init__(
        self,
        *,
        applier: ExecutionReportApplier,
        plan_lookup: InstructionPlanLookup,
        feishu: FeishuClient | None,
        renderer: MessageRenderer,
        audit: AuditStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._applier = applier
        self._lookup = plan_lookup
        self._feishu = feishu
        self._renderer = renderer
        self._audit = audit
        self._now = now or _default_now

    async def handle_feishu(
        self, message: ReceivedMessage
    ) -> ParseOutcome:
        """Entry point invoked by the WS receiver (F-003).

        Channel is locked to :class:`ExecutionReportChannel.FEISHU` and
        the clarification reply is sent back to the same ``chat_id`` we
        received from (decision chat — never the alert chat).
        """
        return await self._handle(
            raw_text=message.text,
            channel=ExecutionReportChannel.FEISHU,
            received_at=message.received_at,
            target_chat_id=message.chat_id,
        )

    async def handle_frontend(
        self,
        raw_text: str,
        *,
        received_at: datetime,
    ) -> ParseOutcome:
        """Entry point for the POST /api/execution-reports frontend path.

        Frontend never sends Feishu clarifications — the front-end
        renders the same template inline via the JS regex mirror.
        """
        return await self._handle(
            raw_text=raw_text,
            channel=ExecutionReportChannel.FRONTEND,
            received_at=received_at,
            target_chat_id=None,
        )

    # -- Core flow ----------------------------------------------------

    async def _handle(
        self,
        *,
        raw_text: str,
        channel: ExecutionReportChannel,
        received_at: datetime,
        target_chat_id: str | None,
    ) -> ParseOutcome:
        parsed_at = self._now()
        try:
            report = parse_execution_report(
                raw_text,
                channel=channel,
                received_at=received_at,
                parsed_at=parsed_at,
            )
        except ExecutionReportParseError as exc:
            return await self._handle_parse_failure(
                raw_text=raw_text,
                channel=channel,
                exc=exc,
                target_chat_id=target_chat_id,
            )

        # Parser succeeded — now cross-check against the InstructionPlan.
        plan = await self._lookup.get(report.instruction_id)
        if plan is None:
            return await self._handle_unknown_plan(
                report=report,
                target_chat_id=target_chat_id,
            )

        # P0-4 §1.1 cross-check — volume against plan. The
        # ExecutionReport model validator already enforced
        # side_zh ↔ instruction_id side and stock_code ↔ instruction_id
        # code; the plan's stock_code is encoded in the
        # instruction_id segment so we only need to verify totals here.
        # A FILLED report whose filled_volume != plan.volume, or a
        # PARTIAL whose filled+remain != plan.volume, is a field
        # cross-check failure that must route to the
        # FIELD_CROSS_CHECK_FAILED clarification template — NOT apply
        # to MockBroker (P0-4 §1.1.1 / red line).
        cross_check_error = _cross_check_volume(report, plan)
        if cross_check_error is not None:
            return await self._handle_volume_mismatch(
                report=report,
                plan=plan,
                cross_check_error=cross_check_error,
                target_chat_id=target_chat_id,
            )

        # P0-3 §1.4 — expired instructions cannot apply unless the
        # report carries the 盘后补录 prefix. The parser preserves the
        # prefix on the report.
        is_post_close = report.prefix.value == "POST_CLOSE"
        if not is_post_close and parsed_at > plan.valid_until:
            return await self._handle_expired_plan(
                report=report,
                plan=plan,
                target_chat_id=target_chat_id,
            )

        try:
            apply_result = await self._applier.apply(
                report, side_is_buy=plan.side is InstructionSide.BUY
            )
        except Exception as exc:  # noqa: BLE001 — surfaced via audit
            log.warning(
                "execution_report_apply_failed instruction_id=%s "
                "error_class=%s",
                report.instruction_id,
                exc.__class__.__name__,
            )
            return ParseOutcome(
                success=False,
                ambiguous=False,
                instruction_id=report.instruction_id,
                template_id=None,
                apply_result=None,
                send_result=None,
            )

        log.info(
            "execution_report_applied instruction_id=%s kind=%s",
            report.instruction_id,
            report.kind.value,
        )
        # P0-4-amendment-2026-05-30b — confirm back to the operator so every
        # report gets exactly one reply (ack on success / clarification on
        # failure). Fail-open: the broker mirror is already the source of
        # truth, so a failed ack send must never undo the applied report.
        send_result = await self._send_ack(
            report=report,
            apply_result=apply_result,
            target_chat_id=target_chat_id,
        )
        return ParseOutcome(
            success=True,
            ambiguous=False,
            instruction_id=report.instruction_id,
            template_id=None,
            apply_result=apply_result,
            send_result=send_result,
        )

    # -- Failure / clarification paths -------------------------------

    async def _handle_parse_failure(
        self,
        *,
        raw_text: str,
        channel: ExecutionReportChannel,
        exc: ExecutionReportParseError,
        target_chat_id: str | None,
    ) -> ParseOutcome:
        template = _REASON_TO_TEMPLATE.get(
            exc.reason, ClarificationTemplate.NO_PATTERN_MATCH
        )
        await self._audit_parse_failure(
            channel=channel,
            reason=exc.reason,
            raw_text=raw_text,
            instruction_id=None,
        )
        send_result = await self._send_clarification(
            template=template,
            instruction_id=None,
            raw_text=raw_text,
            target_chat_id=target_chat_id,
        )
        return ParseOutcome(
            success=False,
            ambiguous=True,
            instruction_id=None,
            template_id=template,
            apply_result=None,
            send_result=send_result,
        )

    async def _handle_unknown_plan(
        self,
        *,
        report: ExecutionReport,
        target_chat_id: str | None,
    ) -> ParseOutcome:
        template = ClarificationTemplate.UNKNOWN_INSTRUCTION_ID
        await self._audit_parse_failure(
            channel=report.channel,
            reason="unknown_instruction_id",
            raw_text=report.raw_text,
            instruction_id=report.instruction_id,
        )
        send_result = await self._send_clarification(
            template=template,
            instruction_id=report.instruction_id,
            raw_text=report.raw_text,
            target_chat_id=target_chat_id,
        )
        return ParseOutcome(
            success=False,
            ambiguous=True,
            instruction_id=report.instruction_id,
            template_id=template,
            apply_result=None,
            send_result=send_result,
        )

    async def _handle_volume_mismatch(
        self,
        *,
        report: ExecutionReport,
        plan: InstructionPlan,  # noqa: ARG002 — included for parity
        cross_check_error: str,
        target_chat_id: str | None,
    ) -> ParseOutcome:
        template = ClarificationTemplate.FIELD_CROSS_CHECK_FAILED
        await self._audit_parse_failure(
            channel=report.channel,
            reason=cross_check_error,
            raw_text=report.raw_text,
            instruction_id=report.instruction_id,
        )
        send_result = await self._send_clarification(
            template=template,
            instruction_id=report.instruction_id,
            raw_text=report.raw_text,
            target_chat_id=target_chat_id,
        )
        return ParseOutcome(
            success=False,
            ambiguous=True,
            instruction_id=report.instruction_id,
            template_id=template,
            apply_result=None,
            send_result=send_result,
        )

    async def _handle_expired_plan(
        self,
        *,
        report: ExecutionReport,
        plan: InstructionPlan,  # noqa: ARG002 — included for symmetry with audit payload
        target_chat_id: str | None,
    ) -> ParseOutcome:
        template = ClarificationTemplate.EXPIRED_INSTRUCTION
        await self._audit_parse_failure(
            channel=report.channel,
            reason="expired_instruction",
            raw_text=report.raw_text,
            instruction_id=report.instruction_id,
        )
        send_result = await self._send_clarification(
            template=template,
            instruction_id=report.instruction_id,
            raw_text=report.raw_text,
            target_chat_id=target_chat_id,
        )
        return ParseOutcome(
            success=False,
            ambiguous=True,
            instruction_id=report.instruction_id,
            template_id=template,
            apply_result=None,
            send_result=send_result,
        )

    # -- Side-effects ------------------------------------------------

    async def _send_clarification(
        self,
        *,
        template: ClarificationTemplate,
        instruction_id: str | None,
        raw_text: str,
        target_chat_id: str | None,
    ) -> SendMessageResult | None:
        if self._feishu is None or target_chat_id is None:
            # Frontend channel — the JS regex mirror surfaces the same
            # template inline; nothing to send.
            return None
        body = self._renderer.render_clarification(
            template=template,
            instruction_id=instruction_id,
            raw_text_excerpt=raw_text,
        )
        return await self._feishu.send_message(target_chat_id, body)

    async def _send_ack(
        self,
        *,
        report: ExecutionReport,
        apply_result: ApplyResult,
        target_chat_id: str | None,
    ) -> SendMessageResult | None:
        """Send the post-apply confirmation back to the decision chat.

        Frontend channel (``target_chat_id is None``) shows the applied
        result inline, so nothing is sent. Fail-open: a send/render error is
        logged but never propagates — the report is already applied and the
        broker mirror is authoritative (P0-5). Returning ``None`` keeps the
        ParseOutcome honest about the (failed/absent) send.
        """
        if self._feishu is None or target_chat_id is None:
            return None
        # The applier's idempotency guard suppresses a re-delivered / double-
        # sent report as a no-op (cash_delta=0, broker_event_sequence=None).
        # Flag it so the ack says "received but not re-applied" rather than a
        # fabricated "recorded, cash +0.00" that reads as a fresh application.
        is_duplicate = (
            apply_result.reason == "execution_report_duplicate_skipped"
        )
        try:
            body = self._renderer.render_execution_ack(
                report=report,
                cash_delta=apply_result.cash_delta,
                broker_event_sequence=apply_result.broker_event_sequence,
                is_duplicate=is_duplicate,
            )
            return await self._feishu.send_message(target_chat_id, body)
        except Exception as exc:  # noqa: BLE001 — ack is best-effort
            log.warning(
                "execution_report_ack_send_failed instruction_id=%s "
                "error_class=%s",
                report.instruction_id,
                exc.__class__.__name__,
            )
            return None

    async def _audit_parse_failure(
        self,
        *,
        channel: ExecutionReportChannel,
        reason: str,
        raw_text: str,
        instruction_id: str | None,
    ) -> None:
        await self._audit.write(
            event_type=AuditEventType.EXECUTION_REPORT_PARSE_FAILED,
            actor=(
                AuditActor.FEISHU_USER
                if channel is ExecutionReportChannel.FEISHU
                else AuditActor.FRONTEND_USER
            ),
            resource_type="instruction_plan",
            resource_id=instruction_id,
            payload={
                "channel": channel.value,
                "reason": reason,
                "raw_text_length": len(raw_text),
            },
            outcome=AuditOutcome.FAILURE,
            reason_namespace="execution_report_ambiguous",
        )


# === Internal mapping table =========================================


_REASON_TO_TEMPLATE: dict[str, ClarificationTemplate] = {
    "no_pattern_match": ClarificationTemplate.NO_PATTERN_MATCH,
    "empty_payload": ClarificationTemplate.EMPTY_PAYLOAD,
    "field_cross_check_failed": ClarificationTemplate.FIELD_CROSS_CHECK_FAILED,
    "unknown_instruction_id": ClarificationTemplate.UNKNOWN_INSTRUCTION_ID,
    "expired_instruction": ClarificationTemplate.EXPIRED_INSTRUCTION,
}


def _default_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


__all__ = [
    "ExecutionReportOrchestrator",
    "InstructionPlanLookup",
    "ParseOutcome",
]
