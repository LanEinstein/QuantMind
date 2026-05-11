"""AuditEvent schema (P1-6 §1.7.1 + 3 amendments → 34 event types).

Locked invariants (red lines):

* 34 distinct ``AuditEventType`` values across 5 categories
  (CLAUDE.md §2.9; the redline-check.sh / B-005 unit tests verify the
  set stays closed).
* Evolution category (Category 5, 7 types) must use ``actor=SYSTEM`` or
  ``actor=SCHEDULER`` — LLM / FRONTEND_USER / FEISHU_USER actors are a
  red line (P2-2 §2 red line 12).
* Frozen + strict + ``extra='forbid'`` (P0-3 §2 红线 12).
* Credential fingerprints only — plaintext secret values in
  ``payload`` is a red line (P1-6 §1.7.1 docstring).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AuditEventType(StrEnum):
    """34 locked event types across 5 categories.

    Categories: (1) write-endpoint entries x2, (2) mode/freeze/lifecycle
    x11, (3) credentials + Feishu connectivity x7, (4) exception +
    enforcement x13, (5) self-evolution lifecycle x7.
    """

    # === Category 1 — two write-endpoint invocations ===
    EXECUTION_REPORT_SUBMITTED = "execution_report_submitted"
    RECONCILIATION_TICKET_DECIDED = "reconciliation_ticket_decided"

    # === Category 2 — mode switch + freeze sources + lifecycle ===
    MODE_SWITCH_INITIATED = "mode_switch_initiated"
    MODE_SWITCH_COMPLETED = "mode_switch_completed"
    FREEZE_SOURCE_SWITCH_ACTIVATED = "freeze_source_switch_activated"
    FREEZE_SOURCE_TICKET_OPEN = "freeze_source_ticket_open"
    FREEZE_SOURCE_CIRCUIT_BREAKER_COOLDOWN = "freeze_source_circuit_breaker_cooldown"
    FREEZE_SOURCE_DATA_QUALITY = "freeze_source_data_quality"
    FREEZE_SOURCE_EOD_PIPELINE_FREEZE = "freeze_source_eod_pipeline_freeze"
    MOCKBROKER_RESET = "mockbroker_reset"
    SYSTEM_INTERRUPTED = "system_interrupted"
    BROKERSCHEDULER_STARTED = "brokerscheduler_started"
    BROKERSCHEDULER_STOPPED = "brokerscheduler_stopped"

    # === Category 3 — credential lifecycle + Feishu connectivity ===
    CREDENTIAL_ROTATED = "credential_rotated"
    CREDENTIAL_REVOKED = "credential_revoked"
    CREDENTIAL_LEAK_INCIDENT = "credential_leak_incident"
    FEISHU_LONGCONN_CONNECTED = "feishu_longconn_connected"
    FEISHU_LONGCONN_DISCONNECTED = "feishu_longconn_disconnected"
    FEISHU_MESSAGE_RECEIVED = "feishu_message_received"
    FEISHU_MESSAGE_SENT = "feishu_message_sent"

    # === Category 4 — exception + enforcement ===
    STATE_MACHINE_ILLEGAL_TRANSITION = "state_machine_illegal_transition"
    RISK_ENGINE_CHECK_REJECTED = "risk_engine_check_rejected"
    BUILDER_EARLY_RETURN = "builder_early_return"
    MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL = (
        "mockbroker_price_limit_violation_at_fill"
    )
    DATA_QUALITY_BREACH = "data_quality_breach"
    RECONCILIATION_TICKET_OPEN_OR_EXPIRED = "reconciliation_ticket_open_or_expired"
    LLM_CALL_TIMEOUT_30S = "llm_call_timeout_30s"
    DAILY_COST_CEILING_20CNY_BREACHED = "daily_cost_ceiling_20cny_breached"
    MONTHLY_BUDGET_50PCT_REACHED = "monthly_budget_50pct_reached"
    MONTHLY_BUDGET_80PCT_REACHED = "monthly_budget_80pct_reached"
    MONTHLY_BUDGET_100PCT_REACHED = "monthly_budget_100pct_reached"
    KIMI_DAILY_CAP_4CNY_BREACHED = "kimi_daily_cap_4cny_breached"
    EXECUTION_REPORT_PARSE_FAILED = "execution_report_parse_failed"

    # === Category 5 — self-evolution lifecycle (P2-2) ===
    PROMPT_VERSION_PINNED = "prompt_version_pinned"
    PROMPT_VERSION_ROLLED_BACK = "prompt_version_rolled_back"
    RAG_DOCUMENT_INGESTED = "rag_document_ingested"
    RAG_DOCUMENT_REJECTED_NON_WHITELIST = "rag_document_rejected_non_whitelist"
    SHADOW_EVOLUTION_RUN_COMPLETED = "shadow_evolution_run_completed"
    EVOLUTION_AMENDMENT_DRAFTED = "evolution_amendment_drafted"
    EVOLUTION_FEISHU_NOTIFIED = "evolution_feishu_notified"


AUDIT_EVENT_TYPES: frozenset[AuditEventType] = frozenset(AuditEventType)
"""Convenience handle used by tests + scripts/query_audit.py."""

EVOLUTION_EVENT_TYPES: frozenset[AuditEventType] = frozenset(
    {
        AuditEventType.PROMPT_VERSION_PINNED,
        AuditEventType.PROMPT_VERSION_ROLLED_BACK,
        AuditEventType.RAG_DOCUMENT_INGESTED,
        AuditEventType.RAG_DOCUMENT_REJECTED_NON_WHITELIST,
        AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
        AuditEventType.EVOLUTION_AMENDMENT_DRAFTED,
        AuditEventType.EVOLUTION_FEISHU_NOTIFIED,
    }
)
"""Category 5 — must use SYSTEM or SCHEDULER actor only."""


class AuditActor(StrEnum):
    """5 locked actor types (P1-6 §1.7.1)."""

    FEISHU_USER = "feishu_user"
    FRONTEND_USER = "frontend_user"
    SYSTEM = "system"
    SCHEDULER = "scheduler"
    CLI = "cli"


SYSTEM_ONLY_ACTORS: frozenset[AuditActor] = frozenset(
    {AuditActor.SYSTEM, AuditActor.SCHEDULER}
)
"""Actors allowed for Category 5 evolution events (P2-2 §2 red line 12)."""


class AuditOutcome(StrEnum):
    """4 locked outcome values."""

    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    DEGRADED = "degraded"


# Credential-style values in payload must use the 8-char SHA256 fingerprint
# format from P1-6 §1.2 — never plaintext. Validators reject the obvious
# shapes (Anthropic-style sk-..., Feishu cli_/secret_) so a typo or
# untrusted contributor cannot ship a secret to the audit store.
_FORBIDDEN_PLAINTEXT_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{16,}|cli_[A-Za-z0-9_\-]{16,}|"
    r"secret_[A-Za-z0-9_\-]{16,}|"
    r"(?:DEEPSEEK|DASHSCOPE|MOONSHOT|FEISHU)_API_KEY=)"
)


def _payload_has_plaintext_secret(value: Any) -> bool:
    """Best-effort scan for plaintext credentials inside an audit payload."""
    if isinstance(value, str):
        return bool(_FORBIDDEN_PLAINTEXT_RE.search(value))
    if isinstance(value, dict):
        return any(_payload_has_plaintext_secret(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_payload_has_plaintext_secret(v) for v in value)
    return False


class AuditEvent(BaseModel):
    """Single audit_events row.

    Frozen + strict + ``extra='forbid'`` (CLAUDE.md §2.9). LLMs never
    construct this directly — only :class:`AuditStore` does, and the
    store accepts a small set of structured parameters.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime
    event_type: AuditEventType
    actor: AuditActor
    actor_detail: str | None = Field(default=None, max_length=128)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    outcome: AuditOutcome
    correlation_id: str | None = Field(default=None, max_length=128)
    reason_namespace: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_evolution_actor(self) -> AuditEvent:
        if self.event_type in EVOLUTION_EVENT_TYPES:
            if self.actor not in SYSTEM_ONLY_ACTORS:
                raise ValueError(
                    f"evolution event {self.event_type.value} requires "
                    f"actor in {sorted(a.value for a in SYSTEM_ONLY_ACTORS)}; "
                    f"got {self.actor.value}"
                )
        return self

    @model_validator(mode="after")
    def _reject_plaintext_secrets(self) -> AuditEvent:
        if _payload_has_plaintext_secret(self.payload):
            raise ValueError(
                "audit payload contains plaintext credential — only the "
                "SHA256[:8] fingerprint is permitted (P1-6 §1.2)"
            )
        return self


__all__ = [
    "AUDIT_EVENT_TYPES",
    "AuditActor",
    "AuditEvent",
    "AuditEventType",
    "AuditOutcome",
    "EVOLUTION_EVENT_TYPES",
    "SYSTEM_ONLY_ACTORS",
]
