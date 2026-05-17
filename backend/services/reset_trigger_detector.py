"""J-004 — Explicit detection for the 5 P0-6 §1 acceptance-window resets.

The detector is the single canonical surface for confirming that one of
the 5 system-level interruptions has occurred:

* ``MARKET_DATA_OUTAGE_30MIN`` — primary + backup quote feed both stale
  for ≥30 minutes.
* ``LLM_FULL_STOP_1H`` — all 3 LLM providers returning errors / timeouts
  for ≥1 hour (matches the existing ``llm_all_providers_failed`` alert).
* ``MOCK_BROKER_CORRUPTION`` — checksum mismatch on the broker
  hybrid-delta + EOD snapshot recovery path (P1-2.A).
* ``STATE_MACHINE_ILLEGAL_TRANSITION`` — InstructionPlan state machine
  observes a forbidden transition (P0-3 §1.4.4).
* ``LONG_CONN_OUTAGE_4H`` — Feishu lark-oapi WebSocket dropped for
  ≥4 hours after the overlay is enabled.

For each trigger the detector:

1. Calls :meth:`AcceptanceService.record_reset` so the rolling-window
   start clamps to the reset wall-clock (force-zero of the 45-day
   counter at the next ``compute()``).
2. Fires ``acceptance_reset_triggered`` via :class:`AlertDispatcher` —
   one ``AuditEvent`` (SYSTEM_INTERRUPTED, reason_namespace =
   ``acceptance_reset_trigger``) + one Feishu message on
   ``FEISHU_ALERT_CHAT_ID`` (P0-2-amendment-2026-05-16 — alert chat
   isolated from decision chat).

Reconciliation freeze is **explicitly excluded** — see
:meth:`notify_reconciliation_freeze`. P0-6 §1 locks reconciliation
freeze as a *pause* (acceptance window suspended; counter preserved),
not a reset.

The detector is intentionally side-effect-only on its constructor
inputs; it does not own state beyond the wired dispatcher + service
references. ``acceptance_service.reset_state()`` is the read-back
contract.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

from backend.monitoring.alert_dispatcher import AlertDispatcher, DispatchResult
from backend.services.acceptance_report import AcceptanceService

log = structlog.get_logger(component="reset_trigger_detector")


class ResetTriggerType(StrEnum):
    """5 locked P0-6 §1 system-level interruption sub-types."""

    MARKET_DATA_OUTAGE_30MIN = "MARKET_DATA_OUTAGE_30MIN"
    LLM_FULL_STOP_1H = "LLM_FULL_STOP_1H"
    MOCK_BROKER_CORRUPTION = "MOCK_BROKER_CORRUPTION"
    STATE_MACHINE_ILLEGAL_TRANSITION = "STATE_MACHINE_ILLEGAL_TRANSITION"
    LONG_CONN_OUTAGE_4H = "LONG_CONN_OUTAGE_4H"


# Locked thresholds — change requires a P0-6 amendment.
MARKET_DATA_OUTAGE_THRESHOLD: dt.timedelta = dt.timedelta(minutes=30)
LLM_FULL_STOP_THRESHOLD: dt.timedelta = dt.timedelta(hours=1)
LONG_CONN_OUTAGE_THRESHOLD: dt.timedelta = dt.timedelta(hours=4)

RESET_ALERT_TYPE = "acceptance_reset_triggered"
"""Single shared alert vocabulary entry in :data:`ALERT_MATRIX` for all
5 trigger sub-types. The sub-type rides in the payload and is also
used as the FeishuAlerter dedup_key so distinct triggers do not
collapse into a single notification."""


@dataclass(frozen=True)
class ResetTriggerEvent:
    """Outcome of a single fired reset trigger."""

    trigger_type: ResetTriggerType
    fired_at: dt.datetime
    payload: Mapping[str, Any]
    dispatch_result: DispatchResult


class ResetTriggerDetector:
    """Detects 5 acceptance-window reset conditions and fires the audit
    + Feishu alert + acceptance service ``record_reset`` chain.

    The detector is intentionally a thin coordinator — each ``notify_*``
    method evaluates the locked threshold for one trigger sub-type and
    invokes the shared ``_fire`` helper on confirmation.

    Args:
        alert_dispatcher: required. Routes the locked
            ``acceptance_reset_triggered`` alert to audit + Feishu via
            the H-004 matrix. Pass the same instance the rest of the
            backend uses (``app.state.alert_dispatcher``).
        acceptance_service: required. Receives ``record_reset(when,
            reason)`` so subsequent ``compute()`` calls clamp the
            rolling-window start.
    """

    def __init__(
        self,
        *,
        alert_dispatcher: AlertDispatcher,
        acceptance_service: AcceptanceService,
    ) -> None:
        self._alert_dispatcher = alert_dispatcher
        self._acceptance_service = acceptance_service

    # -- 5 trigger entry points -----------------------------------------

    async def notify_market_data_outage(
        self,
        *,
        started_at: dt.datetime,
        observed_at: dt.datetime,
    ) -> ResetTriggerEvent | None:
        """Fire if ``observed_at - started_at >= 30 minutes``."""
        elapsed = observed_at - started_at
        if elapsed < MARKET_DATA_OUTAGE_THRESHOLD:
            return None
        return await self._fire(
            trigger=ResetTriggerType.MARKET_DATA_OUTAGE_30MIN,
            when=observed_at,
            payload={
                "started_at": started_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "elapsed_seconds": elapsed.total_seconds(),
                "threshold_seconds": MARKET_DATA_OUTAGE_THRESHOLD.total_seconds(),
            },
        )

    async def notify_llm_full_stop(
        self,
        *,
        started_at: dt.datetime,
        observed_at: dt.datetime,
    ) -> ResetTriggerEvent | None:
        """Fire if ``observed_at - started_at >= 1 hour``."""
        elapsed = observed_at - started_at
        if elapsed < LLM_FULL_STOP_THRESHOLD:
            return None
        return await self._fire(
            trigger=ResetTriggerType.LLM_FULL_STOP_1H,
            when=observed_at,
            payload={
                "started_at": started_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "elapsed_seconds": elapsed.total_seconds(),
                "threshold_seconds": LLM_FULL_STOP_THRESHOLD.total_seconds(),
            },
        )

    async def notify_mock_broker_corruption(
        self,
        *,
        observed_at: dt.datetime,
        detail: str,
    ) -> ResetTriggerEvent:
        """Fire immediately — corruption is fail-closed by P1-2.A."""
        return await self._fire(
            trigger=ResetTriggerType.MOCK_BROKER_CORRUPTION,
            when=observed_at,
            payload={
                "observed_at": observed_at.isoformat(),
                "detail": detail[:256],
            },
        )

    async def notify_state_machine_illegal_transition(
        self,
        *,
        observed_at: dt.datetime,
        instruction_id: str,
        from_state: str,
        to_state: str,
    ) -> ResetTriggerEvent:
        """Fire immediately — an illegal transition means the state
        machine is no longer trustworthy for acceptance accumulation."""
        return await self._fire(
            trigger=ResetTriggerType.STATE_MACHINE_ILLEGAL_TRANSITION,
            when=observed_at,
            payload={
                "observed_at": observed_at.isoformat(),
                "instruction_id": instruction_id,
                "from_state": from_state,
                "to_state": to_state,
            },
        )

    async def notify_long_conn_outage(
        self,
        *,
        started_at: dt.datetime,
        observed_at: dt.datetime,
    ) -> ResetTriggerEvent | None:
        """Fire if ``observed_at - started_at >= 4 hours``."""
        elapsed = observed_at - started_at
        if elapsed < LONG_CONN_OUTAGE_THRESHOLD:
            return None
        return await self._fire(
            trigger=ResetTriggerType.LONG_CONN_OUTAGE_4H,
            when=observed_at,
            payload={
                "started_at": started_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "elapsed_seconds": elapsed.total_seconds(),
                "threshold_seconds": LONG_CONN_OUTAGE_THRESHOLD.total_seconds(),
            },
        )

    # -- explicit no-op for reconciliation freeze -----------------------

    async def notify_reconciliation_freeze(
        self,
        *,
        observed_at: dt.datetime,
        ticket_id: str,
    ) -> None:
        """No-op by design — reconciliation freeze PAUSES, not resets.

        P0-6 §1 locks reconciliation freeze as a *pause* of the rolling
        window (counter preserved, gate suspended). Callers that want
        to express the freeze intent explicitly invoke this method so
        the contract is searchable and a future contributor cannot
        accidentally wire freeze events to the reset chain.
        """
        log.info(
            "reconciliation_freeze_acknowledged_no_reset",
            ticket_id=ticket_id,
            observed_at=observed_at.isoformat(),
        )
        return None

    # -- internal helper ------------------------------------------------

    async def _fire(
        self,
        *,
        trigger: ResetTriggerType,
        when: dt.datetime,
        payload: dict[str, Any],
    ) -> ResetTriggerEvent:
        """Dispatch the alert (audit-first), then record the reset.

        Codex cycle 3 P1 fix — earlier ordering called
        :meth:`AcceptanceService.record_reset` BEFORE awaiting the
        alert dispatcher. ``AlertDispatcher`` swallows audit-write
        failures (fail-open per P1-6 §1.7.4) and returns
        ``audit_written=False`` — in that branch the in-memory reset
        state would update but the JSONL hydration on the next
        restart would find nothing to replay, silently dropping the
        clamp. The new ordering writes durable state first, refuses
        the reset path if the audit could not be persisted, and only
        then updates the in-process clamp.

        Codex cycle 4 P2 fix — also prevalidate ``when`` is
        timezone-aware BEFORE dispatching the alert. Previously a
        naive ``observed_at`` would pass through AlertDispatcher
        (audit + Feishu written) and then ``record_reset()`` would
        raise because it requires aware datetimes — leaving a durable
        reset alert without the in-process clamp, and the next
        startup hydration would also reject the naive timestamp.

        Codex cycle 1 P2 — dedup_key uses ONLY the trigger sub-type
        so two confirmations of the same outage at 10:30 / 10:31
        collapse inside FeishuAlerter's 15-min window; distinct
        trigger types keep distinct keys.
        """
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError(
                f"reset trigger {trigger.value} fired with naive "
                f"datetime {when!r} — provide an aware datetime so "
                "the audit + acceptance clamp share a single wall-clock"
            )
        # Always tag the trigger sub-type into the payload — downstream
        # (J-001 dashboard, audit query CLI, runbook) filters on it.
        enriched: dict[str, Any] = {
            "trigger_type": trigger.value,
            **payload,
        }
        dedup_key = trigger.value

        # 1. Audit-first: dispatch the alert (audit + Feishu via the
        # H-004 matrix) and verify the audit write succeeded.
        dispatch = await self._alert_dispatcher.fire(
            alert_type=RESET_ALERT_TYPE,
            message=(
                f"Acceptance window RESET — {trigger.value}; "
                "45-day counter zeroed at next compute()."
            ),
            payload=enriched,
            dedup_key=dedup_key,
            actor_detail="reset_trigger_detector",
            resource_type="acceptance_window",
            resource_id=trigger.value,
            now=when,
        )
        if not dispatch.audit_written:
            log.error(
                "acceptance_reset_audit_write_failed",
                trigger=trigger.value,
                fired_at=when.isoformat(),
                dispatch_reason=dispatch.reason,
            )
            raise RuntimeError(
                f"acceptance reset audit write failed for "
                f"{trigger.value} — refusing to update in-process "
                "reset state without a durable replay record"
            )

        # 2. Audit is durable — now zero the 45-day counter at the
        # next compute().
        self._acceptance_service.record_reset(
            when=when, reason=trigger.value,
        )
        log.warning(
            "acceptance_reset_fired",
            trigger=trigger.value,
            fired_at=when.isoformat(),
            audit_written=dispatch.audit_written,
            feishu_sent=dispatch.feishu_sent,
        )
        return ResetTriggerEvent(
            trigger_type=trigger,
            fired_at=when,
            payload=enriched,
            dispatch_result=dispatch,
        )


__all__ = [
    "LLM_FULL_STOP_THRESHOLD",
    "LONG_CONN_OUTAGE_THRESHOLD",
    "MARKET_DATA_OUTAGE_THRESHOLD",
    "RESET_ALERT_TYPE",
    "ResetTriggerDetector",
    "ResetTriggerEvent",
    "ResetTriggerType",
]
