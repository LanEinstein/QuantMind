"""EvolutionAuditWriter — 7 Category-5 audit emissions (P2-2 + X-015).

Thin domain wrapper over :class:`backend.audit.store.AuditStore` so the
self-evolution chain has one ergonomic entrypoint for each of the seven
``Category 5`` event types locked by
``P1-6-amendment-2026-05-11-audit-eventtype-34``.

Why a separate writer when ``AuditStore.write`` is already the underlying
API? Two reasons:

* The actor red line (P2-2 §2 red line 12) restricts Category-5 events to
  ``SYSTEM`` or ``SCHEDULER``. The wrapper picks the right actor based on
  *who* the caller is — the BrokerScheduler 5th cron writes
  ``SCHEDULER`` events, every other module writes ``SYSTEM``. Each method
  enforces this so the caller never has to remember the rule, and a
  mis-actor regression surfaces as a ``ValueError`` instead of a silent
  audit row mutation.
* Each event type carries a tightly-scoped payload contract.  Keyword-only
  arguments with explicit types prevent the call sites from drifting into
  free-form ``dict`` payloads that would defeat the JSONL/Mongo schema
  symmetry.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
``backend.audit.store`` and ``backend.audit.models`` are explicitly
allowed (they are the audit substrate, not the decision path).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from backend.audit.models import (
    EVOLUTION_EVENT_TYPES,
    SYSTEM_ONLY_ACTORS,
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import AuditStore

log = logging.getLogger(__name__)


REASON_NAMESPACE = "evolution"
"""Single ``reason_namespace`` for every Category-5 row; lets the
audit query CLI filter the whole self-evolution lane with one clause."""


_SCHEDULER_EVENTS: frozenset[AuditEventType] = frozenset(
    {
        # The shadow-run cron is the only event SCHEDULER raises directly;
        # every other Category-5 event is emitted by a service the
        # scheduler invokes (so they are SYSTEM-actor by definition).
        AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
    }
)
"""Subset of Category-5 events that the BrokerScheduler raises
directly. The wrapper applies ``actor=SCHEDULER`` to these and
``actor=SYSTEM`` to the rest."""


def _default_actor_for(event_type: AuditEventType) -> AuditActor:
    return (
        AuditActor.SCHEDULER if event_type in _SCHEDULER_EVENTS else AuditActor.SYSTEM
    )


@dataclass(frozen=True)
class EvolutionAuditWriter:
    """Type-safe writer for the seven evolution event types.

    Frozen so swapping the underlying store at runtime is a setattr
    error — the dispatcher constructs one instance at boot.
    """

    store: AuditStore

    async def _emit(
        self,
        *,
        event_type: AuditEventType,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any],
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        actor: AuditActor | None = None,
        actor_detail: str | None = None,
        correlation_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        if event_type not in EVOLUTION_EVENT_TYPES:
            raise ValueError(
                f"event_type {event_type.value!r} is not a Category-5 "
                f"evolution event; refusing to route through "
                f"EvolutionAuditWriter"
            )
        effective_actor = actor or _default_actor_for(event_type)
        if effective_actor not in SYSTEM_ONLY_ACTORS:
            raise ValueError(
                f"evolution audit actor must be one of "
                f"{sorted(a.value for a in SYSTEM_ONLY_ACTORS)}; got "
                f"{effective_actor.value!r}"
            )
        return await self.store.write(
            event_type=event_type,
            actor=effective_actor,
            actor_detail=actor_detail,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
            outcome=outcome,
            correlation_id=correlation_id,
            reason_namespace=REASON_NAMESPACE,
            timestamp=timestamp or datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # 7 explicit event writers
    # ------------------------------------------------------------------

    async def prompt_version_pinned(
        self,
        *,
        agent: str,
        version_tag: str,
        sha256: str,
        pinned_by: str,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record a new ``config/prompts/{agent}/{version}.yaml`` pin."""
        return await self._emit(
            event_type=AuditEventType.PROMPT_VERSION_PINNED,
            resource_type="prompt_version",
            resource_id=f"{agent}:{version_tag}",
            payload={
                "agent": agent,
                "version_tag": version_tag,
                "sha256": sha256,
                "pinned_by": pinned_by,
            },
            correlation_id=correlation_id,
        )

    async def prompt_version_rolled_back(
        self,
        *,
        agent: str,
        from_version: str,
        to_version: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record a rollback from ``from_version`` to ``to_version``."""
        return await self._emit(
            event_type=AuditEventType.PROMPT_VERSION_ROLLED_BACK,
            resource_type="prompt_version",
            resource_id=f"{agent}:{from_version}->{to_version}",
            payload={
                "agent": agent,
                "from_version": from_version,
                "to_version": to_version,
                "reason": reason,
            },
            correlation_id=correlation_id,
        )

    async def rag_document_ingested(
        self,
        *,
        doc_id: str,
        source: str,
        content_sha256: str,
        whitelist_rule_version: str,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record a successful ingest into ``data/rag/{source}/...``."""
        return await self._emit(
            event_type=AuditEventType.RAG_DOCUMENT_INGESTED,
            resource_type="rag_document",
            resource_id=doc_id,
            payload={
                "doc_id": doc_id,
                "source": source,
                "content_sha256": content_sha256,
                "whitelist_rule_version": whitelist_rule_version,
            },
            correlation_id=correlation_id,
        )

    async def rag_document_rejected_non_whitelist(
        self,
        *,
        attempted_source: str,
        url: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record a rejection — ``BLOCKED`` outcome for the audit lane."""
        return await self._emit(
            event_type=AuditEventType.RAG_DOCUMENT_REJECTED_NON_WHITELIST,
            resource_type="rag_document",
            resource_id=f"{attempted_source}::{url[:96]}",
            payload={
                "attempted_source": attempted_source,
                "url": url,
                "reason": reason,
            },
            outcome=AuditOutcome.BLOCKED,
            correlation_id=correlation_id,
        )

    async def shadow_evolution_run_completed(
        self,
        *,
        challenger_artifact_id: str,
        champion_baseline_id: str,
        passed: bool,
        metrics_summary: dict[str, Any],
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record the verdict of one ``evolution_shadow_run`` cron pass."""
        outcome = AuditOutcome.SUCCESS if passed else AuditOutcome.FAILURE
        return await self._emit(
            event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
            resource_type="shadow_evolution_run",
            resource_id=challenger_artifact_id,
            payload={
                "challenger_artifact_id": challenger_artifact_id,
                "champion_baseline_id": champion_baseline_id,
                "passed": passed,
                "metrics_summary": metrics_summary,
            },
            outcome=outcome,
            correlation_id=correlation_id,
        )

    async def evolution_amendment_drafted(
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
    ) -> AuditEvent:
        """Record the X-013 drafter writing a ``pending/<id>.md``."""
        return await self._emit(
            event_type=AuditEventType.EVOLUTION_AMENDMENT_DRAFTED,
            resource_type="evolution_amendment",
            resource_id=amendment_id,
            payload={
                "amendment_id": amendment_id,
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "amendment_path": amendment_path,
            },
            correlation_id=correlation_id,
        )

    async def evolution_feishu_notified(
        self,
        *,
        amendment_id: str,
        chat_id: str,
        message_uuid: str,
        suppressed: bool = False,
        suppression_reason: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        """Record the X-014 Feishu page (or its dedup suppression)."""
        outcome = AuditOutcome.SUCCESS if not suppressed else AuditOutcome.BLOCKED
        return await self._emit(
            event_type=AuditEventType.EVOLUTION_FEISHU_NOTIFIED,
            resource_type="evolution_amendment",
            resource_id=amendment_id,
            payload={
                "amendment_id": amendment_id,
                "chat_id": chat_id,
                "message_uuid": message_uuid,
                "suppressed": suppressed,
                "suppression_reason": suppression_reason,
            },
            outcome=outcome,
            correlation_id=correlation_id,
        )


__all__ = [
    "REASON_NAMESPACE",
    "EvolutionAuditWriter",
]
