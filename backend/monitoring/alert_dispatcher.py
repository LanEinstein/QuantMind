"""H-004 — Central alert dispatcher (FeishuAlerter + AuditStore).

Routes the locked alert vocabulary to the correct sink combination:

* **Critical alerts** → ``AuditStore`` + ``FeishuAlerter`` (dedup_15min)
* **Soft / informational alerts** → ``AuditStore`` only (no Feishu)

Why split: P1-7 §1.7 lists `monthly_budget_50pct_reached` as audit-only
(operators read it from the cost dashboard, not the alert chat). The
daily ¥20 hard breach, LLM full outage, MockBroker errors, long-conn
disconnects, and the P2-2 evolution amendment notification all fire to
Feishu because they require an immediate operator decision.

Red lines (CLAUDE.md §2.9 + §2.11 / P1-7 §2):

* The dispatcher MUST NOT compose buy / sell / clarification / recon
  messages — those flow through ``FeishuMessenger`` (F-002 / F-004 /
  F-005). The matrix below explicitly excludes those vocabularies.
* Dedup window is enforced by :class:`FeishuAlerter` (15min).
* When the FeishuAlerter is ``None`` (simulation_auto) every alert
  degrades gracefully to audit-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.integrations.feishu.alerter import (
    ALERT_TYPES as FEISHU_ALERT_TYPES,
)
from backend.integrations.feishu.alerter import FeishuAlerter

log = structlog.get_logger(component="alert_dispatcher")


# ---------------------------------------------------------------------------
# Alert matrix — locked vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertSpec:
    """Locked recipe for one alert type."""

    alert_type: str
    audit_event_type: AuditEventType
    fire_to_feishu: bool
    severity: str  # "info" | "warning" | "critical"
    reason_namespace: str
    description: str


ALERT_MATRIX: dict[str, AlertSpec] = {
    # P1-7 budget breaches -- daily hard + Kimi cap fire to Feishu;
    # monthly milestones are audit-only (status panel reads them).
    "daily_cost_ceiling_20cny_breached": AlertSpec(
        alert_type="daily_cost_ceiling_20cny_breached",
        audit_event_type=AuditEventType.DAILY_COST_CEILING_20CNY_BREACHED,
        fire_to_feishu=True,
        severity="critical",
        reason_namespace="cost_budget_threshold",
        description="Daily ¥20 hard cap exceeded — full LLM circuit breaker.",
    ),
    "kimi_daily_cap_4cny_breached": AlertSpec(
        alert_type="kimi_daily_cap_4cny_breached",
        audit_event_type=AuditEventType.KIMI_DAILY_CAP_4CNY_BREACHED,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="cost_budget_threshold",
        description="Kimi ¥4 daily cap — Kimi escalation blocked; "
        "DeepSeek + Qwen unaffected.",
    ),
    # Monthly milestones — AUDIT ONLY (no Feishu). 100% does NOT halt.
    "monthly_budget_50pct_reached": AlertSpec(
        alert_type="monthly_budget_50pct_reached",
        audit_event_type=AuditEventType.MONTHLY_BUDGET_50PCT_REACHED,
        fire_to_feishu=False,
        severity="info",
        reason_namespace="cost_budget_threshold",
        description="Monthly soft budget at 50% — audit-only milestone.",
    ),
    "monthly_budget_80pct_reached": AlertSpec(
        alert_type="monthly_budget_80pct_reached",
        audit_event_type=AuditEventType.MONTHLY_BUDGET_80PCT_REACHED,
        fire_to_feishu=False,
        severity="warning",
        reason_namespace="cost_budget_threshold",
        description="Monthly soft budget at 80% — audit-only milestone.",
    ),
    "monthly_budget_100pct_reached": AlertSpec(
        alert_type="monthly_budget_100pct_reached",
        audit_event_type=AuditEventType.MONTHLY_BUDGET_100PCT_REACHED,
        fire_to_feishu=False,
        severity="critical",
        reason_namespace="cost_budget_threshold",
        description="Monthly soft budget at 100% — audit-only, LLM stays alive.",
    ),
    # P0-6 system interruptions
    "llm_all_providers_failed": AlertSpec(
        alert_type="llm_all_providers_failed",
        audit_event_type=AuditEventType.SYSTEM_INTERRUPTED,
        fire_to_feishu=True,
        severity="critical",
        reason_namespace="llm_all_outage",
        description="All 3 LLM providers down for >1 hour.",
    ),
    "scheduler_lag": AlertSpec(
        alert_type="scheduler_lag",
        audit_event_type=AuditEventType.SYSTEM_INTERRUPTED,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="scheduler_lag",
        description="AnalysisScheduler or BrokerScheduler lag beyond threshold.",
    ),
    "circuit_breaker_open": AlertSpec(
        alert_type="circuit_breaker_open",
        audit_event_type=AuditEventType.FREEZE_SOURCE_CIRCUIT_BREAKER_COOLDOWN,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="circuit_breaker_open",
        description="Daily-loss / consecutive-loss / order-count breaker open.",
    ),
    "data_quality_breach": AlertSpec(
        alert_type="data_quality_breach",
        audit_event_type=AuditEventType.DATA_QUALITY_BREACH,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="data_quality_breach",
        description="Market staleness / divergence / suspension breach.",
    ),
    # Operational lifecycle
    "backup_failed": AlertSpec(
        alert_type="backup_failed",
        audit_event_type=AuditEventType.SYSTEM_INTERRUPTED,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="backup_failed",
        description="Nightly Mongo / config backup job failed.",
    ),
    "health_critical": AlertSpec(
        alert_type="health_critical",
        audit_event_type=AuditEventType.SYSTEM_INTERRUPTED,
        fire_to_feishu=True,
        severity="critical",
        reason_namespace="health_critical",
        description="Service health check critical (Mongo / Redis / process).",
    ),
    "feishu_longconn_disconnected": AlertSpec(
        alert_type="feishu_longconn_disconnected",
        audit_event_type=AuditEventType.FEISHU_LONGCONN_DISCONNECTED,
        fire_to_feishu=True,
        severity="warning",
        reason_namespace="feishu_longconn_disconnected",
        description="Feishu lark-oapi WebSocket dropped > threshold.",
    ),
    # P2-2 self-evolution gate
    "evolution_amendment_drafted": AlertSpec(
        alert_type="evolution_amendment_drafted",
        audit_event_type=AuditEventType.EVOLUTION_AMENDMENT_DRAFTED,
        fire_to_feishu=True,
        severity="info",
        reason_namespace="evolution_amendment_drafted",
        description="Shadow evolution shortlist amendment awaiting owner gate.",
    ),
    # J-004 — P0-6 §1 acceptance-window reset triggers. All 5 trigger
    # sub-types (MARKET_DATA_OUTAGE_30MIN / LLM_FULL_STOP_1H /
    # MOCK_BROKER_CORRUPTION / STATE_MACHINE_ILLEGAL_TRANSITION /
    # LONG_CONN_OUTAGE_4H) share this alert_type so the operator sees
    # a single locked vocabulary; the specific trigger sub-type rides
    # in the payload and feeds the dedup_key per fire so the
    # FeishuAlerter dedup window does not collapse distinct triggers.
    "acceptance_reset_triggered": AlertSpec(
        alert_type="acceptance_reset_triggered",
        audit_event_type=AuditEventType.SYSTEM_INTERRUPTED,
        fire_to_feishu=True,
        severity="critical",
        reason_namespace="acceptance_reset_trigger",
        description="One of the 5 P0-6 §1 system-level interruptions reset "
        "the 45-trading-day acceptance window.",
    ),
}
"""Locked alert vocabulary. Adding a new entry needs an amendment +
audit-type addition + FeishuAlerter.ALERT_TYPES inclusion. Buy/sell/
clarification/recon names are deliberately absent — they flow through
``FeishuMessenger``, not this dispatcher (P0-2-amendment-2026-05-16 §4
red line 7)."""


# Buy/sell/clarification/recon names that MUST never appear in
# ALERT_MATRIX. Enforced by :func:`assert_no_decision_path_alerts`.
FORBIDDEN_DECISION_PATH_ALERTS: frozenset[str] = frozenset(
    {
        "instruction_dispatched",
        "execution_filled",
        "reconciliation_ticket_open",
        "clarification_request",
        "trade_executed",
        "order_placed",
    }
)


def assert_no_decision_path_alerts() -> None:
    """Boot-time guard against a decision-path leak (called by tests)."""
    leaked = set(ALERT_MATRIX.keys()) & FORBIDDEN_DECISION_PATH_ALERTS
    if leaked:
        raise AssertionError(
            f"ALERT_MATRIX leaked decision-path alert(s): {sorted(leaked)}"
        )


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a single :meth:`AlertDispatcher.fire` call."""

    alert_type: str
    audit_written: bool
    feishu_sent: bool
    feishu_suppressed: bool
    reason: str


class AlertDispatcher:
    """Composes :class:`AuditStore` + :class:`FeishuAlerter`.

    A single instance lives on ``app.state.alert_dispatcher`` after
    boot. Call ``.fire(alert_type, message, ...)`` and the dispatcher
    decides per the :data:`ALERT_MATRIX` recipe whether the alert flows
    to Feishu in addition to the audit trail.
    """

    def __init__(
        self,
        *,
        audit: AuditStore,
        feishu_alerter: FeishuAlerter | None,
    ) -> None:
        self._audit = audit
        self._feishu = feishu_alerter

    async def fire(
        self,
        *,
        alert_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        dedup_key: str = "",
        actor: AuditActor = AuditActor.SYSTEM,
        actor_detail: str | None = None,
        outcome: AuditOutcome = AuditOutcome.DEGRADED,
        resource_type: str = "system_alert",
        resource_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DispatchResult:
        spec = ALERT_MATRIX.get(alert_type)
        if spec is None:
            log.warning(
                "alert_dispatcher_unknown_type",
                alert_type=alert_type,
            )
            return DispatchResult(
                alert_type=alert_type,
                audit_written=False,
                feishu_sent=False,
                feishu_suppressed=True,
                reason="unknown_alert_type",
            )
        moment = now or datetime.now(UTC)

        # 1. Audit — always written.
        audit_payload = dict(payload or {})
        audit_payload.setdefault("severity", spec.severity)
        audit_payload.setdefault("alert_type", alert_type)
        audit_payload.setdefault("message", message)
        audit_written = False
        try:
            await self._audit.write(
                event_type=spec.audit_event_type,
                actor=actor,
                actor_detail=actor_detail,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=audit_payload,
                outcome=outcome,
                correlation_id=correlation_id,
                reason_namespace=spec.reason_namespace,
                timestamp=moment,
            )
            audit_written = True
        except Exception as exc:  # noqa: BLE001 — fail-open per P1-6 §1.7.4
            log.warning(
                "alert_dispatcher_audit_failed",
                alert_type=alert_type,
                error=str(exc),
            )

        # 2. Feishu — only for spec.fire_to_feishu=True.
        if not spec.fire_to_feishu:
            return DispatchResult(
                alert_type=alert_type,
                audit_written=audit_written,
                feishu_sent=False,
                feishu_suppressed=True,
                reason="audit_only",
            )
        if self._feishu is None:
            return DispatchResult(
                alert_type=alert_type,
                audit_written=audit_written,
                feishu_sent=False,
                feishu_suppressed=True,
                reason="no_feishu_client",
            )
        try:
            result = await self._feishu.fire(
                alert_type=alert_type,
                severity=spec.severity,
                message=message,
                dedup_key=dedup_key,
                fired_at=moment,
            )
        except Exception as exc:  # noqa: BLE001 — operator visibility
            log.warning(
                "alert_dispatcher_feishu_failed",
                alert_type=alert_type,
                error=str(exc),
            )
            return DispatchResult(
                alert_type=alert_type,
                audit_written=audit_written,
                feishu_sent=False,
                feishu_suppressed=True,
                reason="feishu_send_failed",
            )
        return DispatchResult(
            alert_type=alert_type,
            audit_written=audit_written,
            feishu_sent=result.sent,
            feishu_suppressed=result.suppressed,
            reason=result.reason,
        )


def matrix_summary() -> list[dict[str, Any]]:
    """Read-only view of the locked matrix for the operator UI."""
    return [
        {
            "alert_type": s.alert_type,
            "audit_event_type": s.audit_event_type.value,
            "fire_to_feishu": s.fire_to_feishu,
            "severity": s.severity,
            "reason_namespace": s.reason_namespace,
            "description": s.description,
        }
        for s in ALERT_MATRIX.values()
    ]


def _assert_feishu_subset() -> None:
    """Every fire_to_feishu=True entry must be a FeishuAlerter.ALERT_TYPES member.

    Mirrors the F-006 contract: only whitelisted types are allowed
    through the OpenAPI alert channel. Called by the unit tests so a
    matrix typo lights up at CI time.
    """
    leaked = {
        spec.alert_type
        for spec in ALERT_MATRIX.values()
        if spec.fire_to_feishu and spec.alert_type not in FEISHU_ALERT_TYPES
    }
    if leaked:
        raise AssertionError(
            f"ALERT_MATRIX fires to Feishu for types missing from "
            f"FeishuAlerter.ALERT_TYPES: {sorted(leaked)}"
        )


__all__ = [
    "ALERT_MATRIX",
    "FORBIDDEN_DECISION_PATH_ALERTS",
    "AlertDispatcher",
    "AlertSpec",
    "DispatchResult",
    "_assert_feishu_subset",
    "assert_no_decision_path_alerts",
    "matrix_summary",
]
