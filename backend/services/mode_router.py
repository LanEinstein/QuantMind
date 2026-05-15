"""ModeRouter — run-mode lifecycle owner (D-005 / P0-1 §1).

Two run modes are locked by P0-1 (CLAUDE.md §2.1):

* ``simulation_auto`` — always-on. Every VALIDATED InstructionPlan is
  routed through :class:`SimulationExecutor` to the MockBroker.
* ``feishu_interactive`` — opt-in overlay; toggled by
  ``FEISHU_INTERACTIVE_ENABLED``. Adds the human-in-loop Feishu fan-out
  on top of the simulation route.

The toggle between the two modes is treated as an account lifecycle
event:

1. **Activate switch** — set ``mode_switch_in_progress=True`` so the
   Builder's first early-return + SimulationExecutor's freeze check
   reject all new BUY/SELL routing.
2. **Archive prior state** — write a ``MODE_SWITCH_RESET`` BrokerEvent
   containing the current cash + positions + initial_capital so the
   recovery loader can reconstruct the timeline.
3. **Reset MockBroker** — clear cash + positions via
   :meth:`MockBroker.reset_to_snapshot`; the new mode starts from the
   initial capital baseline (CLAUDE.md §2.1 "MockBroker reset" step).
4. **Audit** — write the mode-switch lifecycle pair
   (``MODE_SWITCH_INITIATED`` + ``MODE_SWITCH_COMPLETED``) so the
   audit trail surfaces the transition.
5. **Complete switch** — clear ``mode_switch_in_progress``.

LLM red line: this module never imports backend.{llm,agents,mirofish}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.broker.mock_broker import MockBroker
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore


@runtime_checkable
class _AcceptanceGate(Protocol):
    """Narrow protocol for the P0-6 §2 redline 5 acceptance gate.

    ModeRouter must consult this before switching to ``feishu_interactive``;
    the env-var ``FEISHU_INTERACTIVE_ENABLED`` alone is NOT a valid
    sanction. Production passes
    :class:`backend.services.acceptance_report.AcceptanceService`;
    tests pass an in-memory stub that returns the boolean directly.
    """

    async def can_switch_to_feishu_on(self) -> bool: ...

log = structlog.get_logger(component="services.mode_router")
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class ModeSwitchState:
    """Mode-switch lifecycle flag consumed by the Builder + Executor.

    Implements the :class:`backend.services.instruction_plan_builder
    .ModeSwitchProbe` protocol so the Builder's first early-return can
    consult it directly.
    """

    _active: bool = False
    _reason: str | None = None
    _started_at: datetime | None = None
    _initiated_by: str | None = None
    _from_mode: str | None = None
    _to_mode: str | None = None

    def is_active(self) -> bool:
        return self._active

    def context(self) -> dict[str, str | None]:
        return {
            "reason": self._reason,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "initiated_by": self._initiated_by,
            "from_mode": self._from_mode,
            "to_mode": self._to_mode,
        }

    def activate(
        self,
        *,
        from_mode: str,
        to_mode: str,
        reason: str,
        initiated_by: str,
        when: datetime,
    ) -> None:
        if self._active:
            raise ValueError(
                "mode-switch already in progress — refusing nested activation"
            )
        self._active = True
        self._from_mode = from_mode
        self._to_mode = to_mode
        self._reason = reason
        self._initiated_by = initiated_by
        self._started_at = when

    def deactivate(self) -> None:
        self._active = False
        self._reason = None
        self._from_mode = None
        self._to_mode = None
        self._initiated_by = None
        self._started_at = None


# Constants for the two valid run modes; the toggle value lives in the
# environment via assert_run_mode_env() (P0-1).
SIMULATION_AUTO = "simulation_auto"
FEISHU_INTERACTIVE = "feishu_interactive"
VALID_MODES = frozenset({SIMULATION_AUTO, FEISHU_INTERACTIVE})


class AcceptanceGateMissingError(RuntimeError):
    """Raised when ModeRouter is asked to enter feishu_interactive but no
    AcceptanceService gate was injected. P0-6 §2 redline 5 — no env-var
    bypass."""


class AcceptanceGateRejectedError(RuntimeError):
    """Raised when ``can_switch_to_feishu_on()`` returned False. The
    operator must wait for the 45-trading-day acceptance window to
    clear or accept the most recent report."""


@dataclass(frozen=True)
class ModeSwitchResult:
    """Summary of a mode-switch lifecycle round-trip."""

    from_mode: str
    to_mode: str
    initiated_at: datetime
    completed_at: datetime
    broker_event_sequence: int


class ModeRouter:
    """Owns the run-mode lifecycle + Builder/Executor probe wiring."""

    def __init__(
        self,
        *,
        broker: MockBroker,
        event_store: BrokerEventStore,
        audit_store: AuditStore,
        mode_state: ModeSwitchState | None = None,
        initial_mode: str = SIMULATION_AUTO,
        acceptance_gate: _AcceptanceGate | None = None,
    ) -> None:
        if initial_mode not in VALID_MODES:
            raise ValueError(f"unknown run mode {initial_mode!r}")
        self._broker = broker
        self._events = event_store
        self._audit = audit_store
        self._mode_state = mode_state or ModeSwitchState()
        self._current_mode = initial_mode
        self._acceptance_gate = acceptance_gate

    @property
    def mode_state(self) -> ModeSwitchState:
        return self._mode_state

    @property
    def current_mode(self) -> str:
        return self._current_mode

    async def switch_mode(
        self,
        *,
        to_mode: str,
        reason: str,
        initiated_by: str,
        when: datetime,
    ) -> ModeSwitchResult:
        """Execute a full mode-switch lifecycle.

        Args:
            to_mode: target run mode (must be in :data:`VALID_MODES`).
            reason: human-readable reason recorded on the audit row.
            initiated_by: CLI / FRONTEND_USER actor identifier.
            when: lifecycle start timestamp; used for both audit + the
                BrokerEvent occurred_at.
        """
        if to_mode not in VALID_MODES:
            raise ValueError(f"unknown run mode {to_mode!r}")
        if to_mode == self._current_mode:
            raise ValueError(
                f"mode switch noop: already in {self._current_mode!r}"
            )

        # P0-6 §2 redline 5 — switching to feishu_interactive requires
        # the AcceptanceService gate to return True. Env-var bypass is
        # explicitly forbidden (codex P1). The gate is optional in
        # constructor so existing tests + simulation-only environments
        # keep working; production wiring always injects it.
        if to_mode == FEISHU_INTERACTIVE:
            if self._acceptance_gate is None:
                raise AcceptanceGateMissingError(
                    "Cannot switch to feishu_interactive: no acceptance "
                    "gate wired. P0-6 §2 redline 5 forbids env-var "
                    "bypass; AcceptanceService must be injected."
                )
            sanctioned = await self._acceptance_gate.can_switch_to_feishu_on()
            if not sanctioned:
                raise AcceptanceGateRejectedError(
                    "Cannot switch to feishu_interactive: the latest "
                    "AcceptanceReport is not PASS. The 45-trading-day "
                    "8-metric gate (P0-6 §1) must clear before the "
                    "human-in-loop overlay is sanctioned."
                )

        from_mode = self._current_mode

        # 1. Activate switch flag — Builder + Executor freeze takes effect.
        self._mode_state.activate(
            from_mode=from_mode,
            to_mode=to_mode,
            reason=reason,
            initiated_by=initiated_by,
            when=when,
        )
        await self._audit.write(
            event_type=AuditEventType.MODE_SWITCH_INITIATED,
            actor=AuditActor.SYSTEM,
            resource_type="run_mode",
            resource_id=to_mode,
            payload={
                "from_mode": from_mode,
                "to_mode": to_mode,
                "reason": reason,
                "initiated_by": initiated_by,
            },
            outcome=AuditOutcome.SUCCESS,
            reason_namespace="mode_switch_in_progress",
            timestamp=when,
        )

        try:
            # 2. Archive prior broker state via a MODE_SWITCH_RESET event
            #    BEFORE we mutate the in-memory mirror. The payload is
            #    self-contained so recovery can replay either ACCOUNT_INITIALIZED
            #    or MODE_SWITCH_RESET interchangeably.
            account = await self._broker.get_account()
            positions = await self._broker.get_positions()
            archive_payload = {
                "from_mode": from_mode,
                "to_mode": to_mode,
                "prior_cash": account.available_cash,
                "prior_frozen_cash": account.frozen_cash,
                "prior_positions": [
                    {"code": p.code, "volume": p.volume, "cost_price": p.cost_price}
                    for p in positions
                ],
                "cash": account.initial_capital,
                "initial_capital": account.initial_capital,
                "frozen_cash": 0.0,
            }
            event = await self._events.append(
                event_type=BrokerEventType.MODE_SWITCH_RESET,
                occurred_at=when,
                correlation_id=f"mode-switch-{to_mode}",
                payload=archive_payload,
            )

            # 3. Reset the broker mirror to the initial capital baseline.
            await self._broker.reset_to_snapshot(
                cash=account.initial_capital,
                positions=(),
                reset_at=when,
                reason=f"mode_switch_{from_mode}_to_{to_mode}",
            )

            # 4. Audit MOCKBROKER_RESET so the broker-level event has its
            #    own audit row alongside the BrokerEvent.
            await self._audit.write(
                event_type=AuditEventType.MOCKBROKER_RESET,
                actor=AuditActor.SYSTEM,
                resource_type="mock_broker",
                payload={
                    "from_mode": from_mode,
                    "to_mode": to_mode,
                    "broker_event_sequence": event.sequence,
                },
                outcome=AuditOutcome.SUCCESS,
                reason_namespace="mode_switch_reset",
                timestamp=when,
            )

            self._current_mode = to_mode
            completed_at = when
            await self._audit.write(
                event_type=AuditEventType.MODE_SWITCH_COMPLETED,
                actor=AuditActor.SYSTEM,
                resource_type="run_mode",
                resource_id=to_mode,
                payload={
                    "from_mode": from_mode,
                    "to_mode": to_mode,
                    "broker_event_sequence": event.sequence,
                },
                outcome=AuditOutcome.SUCCESS,
                reason_namespace="mode_switch_completed",
                timestamp=completed_at,
            )
            return ModeSwitchResult(
                from_mode=from_mode,
                to_mode=to_mode,
                initiated_at=when,
                completed_at=completed_at,
                broker_event_sequence=event.sequence,
            )
        finally:
            # 5. Always clear the in-progress flag — even on failure, so
            #    a transient error doesn't permanently freeze trading.
            self._mode_state.deactivate()


__all__ = [
    "FEISHU_INTERACTIVE",
    "SIMULATION_AUTO",
    "VALID_MODES",
    "AcceptanceGateMissingError",
    "AcceptanceGateRejectedError",
    "ModeRouter",
    "ModeSwitchResult",
    "ModeSwitchState",
]

# Silence unused-import warning for the dataclass field helper.
_ = field
