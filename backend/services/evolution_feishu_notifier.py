"""EvolutionFeishuNotifier — fires a Feishu page when an amendment is drafted.

After the X-013 ``AmendmentDrafter`` writes
``docs/decisions/pending/{amendment_id}.md``, this notifier:

1. Renders a single-line summary via
   :meth:`backend.integrations.feishu.renderer.MessageRenderer.render_evolution_pending`.
2. Dispatches the message through :class:`FeishuAlerter` (which uses
   the self-built-app OpenAPI ``POST /open-apis/im/v1/messages`` —
   P0-2-amendment-2026-05-16 §4 red line 7) to ``FEISHU_ALERT_CHAT_ID``.
3. Emits a Category-5 ``evolution_feishu_notified`` audit row through
   :class:`backend.services.evolution_audit_writer.EvolutionAuditWriter`
   (X-015).

Locked invariants:

* Route is the self-built-app OpenAPI — the legacy custom-bot
  webhook envs are explicitly disallowed (see
  P0-2-amendment-2026-05-16 §4 red line 7 for the banned env names;
  this notifier does not reference them by literal). The constructor
  only takes a ``FeishuAlerter`` whose ``_feishu`` is itself a
  ``FeishuClient`` (OpenAPI route); legacy webhook clients have a
  different surface and won't satisfy the type hint.
* Dedup via the alerter's ``dedup_15min`` cooldown — repeated calls
  with the same ``amendment_id`` within 15 minutes are suppressed
  (P1-7 §1.7).
* Prompt text / shadow metric raw values are NEVER included in the
  alert body (P2-2 §2 — prompt-injection containment). The body
  surfaces identifiers + amendment_path only; the operator follows
  the path to read the full draft inside SystemStatus.vue.
* Zero ``backend.{api, broker, risk, llm, agents, mirofish, data}``
  imports — Phase X red line (P2-2 §2 red line 17).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from backend.integrations.feishu.alerter import AlertResult, FeishuAlerter
from backend.integrations.feishu.renderer import MessageRenderer
from backend.services.evolution_audit_writer import EvolutionAuditWriter

log = logging.getLogger(__name__)


EVOLUTION_ALERT_TYPE = "evolution_amendment_drafted"
"""Alert-type vocabulary entry already locked inside
:data:`backend.integrations.feishu.alerter.ALERT_TYPES`. Mirrored here
so tests can assert the spelling stays in sync."""


@dataclass(frozen=True)
class EvolutionNotifyResult:
    """Outcome of one :meth:`EvolutionFeishuNotifier.fire_pending` call."""

    sent: bool
    suppressed: bool
    reason: str
    alert_result: AlertResult


@dataclass(frozen=True)
class EvolutionFeishuNotifier:
    """Page the operator about a freshly-drafted amendment.

    Frozen so the alerter / renderer / audit writer cannot be swapped
    at runtime — the wiring is constructed once at boot.
    """

    alerter: FeishuAlerter
    renderer: MessageRenderer
    audit: EvolutionAuditWriter

    async def fire_pending(
        self,
        *,
        amendment_id: str,
        artifact_type: Literal[
            "prompt",
            "rag_document",
            "risk_parameter_proposal",
            "exemplar_schema",
        ],
        artifact_id: str,
        amendment_path: str,
        correlation_id: str | None = None,
        fired_at: datetime | None = None,
    ) -> EvolutionNotifyResult:
        """Render → dispatch → audit a single pending-amendment page.

        Returns the dispatch / suppression result so the caller can
        record it (e.g. set ``feishu_notified_at`` on the
        ``risk_parameter_proposals`` row when the alerter returned a
        ``sent=True`` outcome).
        """
        now = fired_at or datetime.now(UTC)
        body = self.renderer.render_evolution_pending(
            amendment_id=amendment_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            amendment_path=amendment_path,
        )
        alert_result = await self.alerter.fire(
            alert_type=EVOLUTION_ALERT_TYPE,
            severity="info",
            message=body,
            dedup_key=amendment_id,
            fired_at=now,
        )

        chat_id = self.alerter.alert_chat_id
        message_uuid = (
            alert_result.send_result.message_id
            if alert_result.send_result and alert_result.send_result.message_id
            else f"alert-{EVOLUTION_ALERT_TYPE}-{amendment_id}-{now.isoformat()}"
        )

        await self.audit.evolution_feishu_notified(
            amendment_id=amendment_id,
            chat_id=chat_id,
            message_uuid=message_uuid,
            suppressed=alert_result.suppressed,
            suppression_reason=(
                alert_result.reason if alert_result.suppressed else None
            ),
            correlation_id=correlation_id,
        )

        return EvolutionNotifyResult(
            sent=alert_result.sent,
            suppressed=alert_result.suppressed,
            reason=alert_result.reason,
            alert_result=alert_result,
        )


__all__ = [
    "EVOLUTION_ALERT_TYPE",
    "EvolutionFeishuNotifier",
    "EvolutionNotifyResult",
]
