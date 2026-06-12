"""ManualTradeService — orchestrates the AD-005 manual-trade write path.

Mirrors :class:`backend.integrations.feishu.parser.ExecutionReportOrchestrator`
for the user-discretionary domain: it owns the applier side-effect and the
"已记录-用户自主操作" Feishu acknowledgement so the POST /api/manual-trades
router stays a thin HTTP ↔ service translator (single-construction-point
discipline — the endpoint never composes Feishu text or touches the mirror).

LLM red line: this module never imports ``backend.{llm,agents,mirofish}``;
the trade DTO is already validated and the renderer is the single text SSoT.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from backend.broker.appliers import ApplyResult, ManualTradeApplier
from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.manual_trade import ExternalExecutionEvent

log = structlog.get_logger(component="services.manual_trade")


@dataclass(frozen=True)
class ManualTradeOutcome:
    """Result of recording a manual trade (router projects this to JSON)."""

    external_trade_id: str
    apply_result: ApplyResult
    feishu_sent: bool


# Duck-typed: only ``send_message(chat_id, body)`` is used. ``None`` is a
# valid wiring (simulation_auto / no decision chat) — the send degrades to a
# no-op, matching the ExecutionReportOrchestrator frontend-channel contract.
FeishuSender = object


class ManualTradeService:
    """Apply a manual trade + send the display-only Feishu acknowledgement."""

    def __init__(
        self,
        *,
        applier: ManualTradeApplier,
        renderer: MessageRenderer,
        feishu: object | None,
        decision_chat_id: str | None,
    ) -> None:
        self._applier = applier
        self._renderer = renderer
        self._feishu = feishu
        self._decision_chat_id = (decision_chat_id or "").strip() or None

    async def record(self, event: ExternalExecutionEvent) -> ManualTradeOutcome:
        """Apply ``event`` to the mirror, then best-effort Feishu ack.

        The applier raises on an impossible fill (unaffordable BUY / over-sell
        beyond settled holdings — both raise BEFORE mutating, so the mirror is
        unchanged); the router surfaces that as a 4xx. On success the Feishu
        ack is fail-open: a send/render error is logged but never rolls back
        the already-applied trade (the mirror is authoritative, U-D12 ack
        precedent).
        """
        apply_result = await self._applier.apply(event)
        feishu_sent = await self._send_ack(event, apply_result)
        return ManualTradeOutcome(
            external_trade_id=event.external_trade_id,
            apply_result=apply_result,
            feishu_sent=feishu_sent,
        )

    async def _send_ack(
        self, event: ExternalExecutionEvent, apply_result: ApplyResult
    ) -> bool:
        if self._feishu is None or self._decision_chat_id is None:
            return False
        is_duplicate = apply_result.reason == "manual_trade_duplicate_skipped"
        try:
            body = self._renderer.render_manual_trade_ack(
                event=event,
                cash_delta=apply_result.cash_delta,
                broker_event_sequence=apply_result.broker_event_sequence,
                is_duplicate=is_duplicate,
            )
            result = await self._feishu.send_message(self._decision_chat_id, body)  # type: ignore[attr-defined]
            # FeishuClient.send_message resolves with SendMessageResult(ok=False)
            # on an API-level rejection rather than raising (codex P2). Report
            # the true ack status so the response never claims a send that the
            # Feishu API actually refused.
            return bool(getattr(result, "ok", False))
        except Exception as exc:  # noqa: BLE001 — ack is best-effort
            log.warning(
                "manual_trade_ack_send_failed",
                external_trade_id=event.external_trade_id,
                error_class=exc.__class__.__name__,
            )
            return False


__all__ = ["ManualTradeOutcome", "ManualTradeService"]
