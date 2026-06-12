"""SimulationExecutor — route VALIDATED InstructionPlan to MockBroker.

E-007 + Phase C audit closeout: the legacy gap was that RiskEngine
PASSED orders never actually reached the broker (audit Phase C "RiskEngine
不接订单"). This module bridges Builder → broker so a VALIDATED BUY/SELL
InstructionPlan in ``simulation_auto`` mode lands on the MockBroker and
the decision_ledger reflects the broker outcome.

Contract:

* Input: an :class:`InstructionPlan` with ``status=VALIDATED`` and
  ``side ∈ {BUY, SELL}``. HOLD plans never route (CLAUDE.md §2.7).
* The executor consults two freeze flags before routing:
    1. ``EodPipelineFreezeState`` (E-005's 5th freeze source).
    2. An injected ``ModeSwitchProbe`` (D-005 lifecycle).
  Either active → status flips to REJECTED with reason
  ``simulation_route_frozen``; no order placed.
* On route: MockBroker.place_order applies the existing ALL_OR_NONE +
  at-fill recheck pipeline. The executor mirrors the outcome onto the
  InstructionPlan via the state machine (DISPATCHED → FILLED or
  REJECTED) and writes the matching ledger event + BrokerEvent.
* Decision ledger is the single correlation graph: the executor
  appends ``PLAN_DISPATCHED`` then ``BROKER_FILLED`` / ``PLAN_REJECTED``
  with the broker_order_id + trade_id linkage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.broker.mock_broker import MockBroker
from backend.broker.models import OrderDirection, OrderStatus, OrderType
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.broker.scheduler import EodPipelineFreezeState
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.models.ledger import LedgerEventKind
from backend.services.instruction_plan_builder import ModeSwitchProbe
from backend.services.instruction_state_machine import transition as _transition
from backend.services.ledger import DecisionLedgerService

log = structlog.get_logger(component="services.simulation_executor")

SHANGHAI = ZoneInfo("Asia/Shanghai")

ROUTE_FROZEN_REASON = "simulation_route_frozen"
"""Locked rejection reason when a freeze source blocks routing."""


@dataclass(frozen=True)
class SimulationRouteResult:
    """Audit-grade summary of a SimulationExecutor.route call."""

    instruction_id: str
    final_status: InstructionStatus
    broker_order_id: str | None
    trade_ids: tuple[str, ...]
    reason: str | None


class SimulationExecutor:
    """Routes VALIDATED InstructionPlan to MockBroker in simulation_auto.

    Why this exists: per audit Phase C 1139 green tests masked the fact
    that RiskEngine PASSED orders never reached the broker. The
    SimulationExecutor is the single edge that closes that gap;
    end-to-end tests cover ``TradingSignal → InstructionPlan →
    RiskEngine → MockBroker`` so a regression that breaks this wiring
    surfaces immediately.
    """

    def __init__(
        self,
        *,
        broker: MockBroker,
        event_store: BrokerEventStore,
        audit_store: AuditStore,
        ledger: DecisionLedgerService,
        freeze_state: EodPipelineFreezeState | None = None,
        mode_switch_probe: ModeSwitchProbe | None = None,
    ) -> None:
        self._broker = broker
        self._events = event_store
        self._audit = audit_store
        self._ledger = ledger
        self._freeze = freeze_state
        self._mode_switch = mode_switch_probe

    async def route(
        self,
        plan: InstructionPlan,
        *,
        now: datetime | None = None,
    ) -> SimulationRouteResult:
        """Route ``plan`` to the broker. Returns the lifecycle summary."""
        if plan.side is InstructionSide.HOLD:
            raise ValueError(
                "SimulationExecutor.route: HOLD plans must not be routed "
                "(CLAUDE.md §2.7); upstream caller has a bug"
            )
        if plan.status is not InstructionStatus.VALIDATED:
            raise ValueError(
                f"SimulationExecutor.route requires VALIDATED status; "
                f"got {plan.status.value}"
            )

        timestamp = now or plan.created_at

        # Freeze checks (5th source — eod_pipeline + mode_switch) ---
        freeze_reason = self._check_freeze_sources()
        if freeze_reason is not None:
            rejected = await self._reject(
                plan, reason=freeze_reason, at=timestamp
            )
            return rejected

        # Place the order on the broker --------------------------------
        order_result = await self._broker.place_order(
            code=plan.stock_code,
            price=plan.limit_price,
            volume=plan.volume,
            direction=(
                OrderDirection.BUY
                if plan.side is InstructionSide.BUY
                else OrderDirection.SELL
            ),
            order_type=OrderType.LIMIT,
        )

        if not order_result.success:
            return await self._reject(
                plan, reason=order_result.message, at=timestamp,
                broker_order_id=order_result.order_id,
            )

        # Success ------------------------------------------------------
        dispatched_plan = _transition(
            plan,
            InstructionStatus.DISPATCHED,
            at=timestamp,
            allow_post_close=True,
        )
        await self._ledger.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.PLAN_DISPATCHED,
            at=timestamp,
            actor="SYSTEM",
            broker_order_id=order_result.order_id,
            payload={"broker_order_id": order_result.order_id},
        )

        trades = await self._broker.get_trades()
        last_trade = trades[-1] if trades else None
        trade_id = last_trade.trade_id if last_trade else None

        # Persist BOTH ORDER_PLACED and ORDER_FILLED atomically so
        # recovery sees the same lifecycle MockBroker.place_order
        # took live: PLACED freezes cash on BUY → FILLED settles it.
        # daily_state_assembler counts ORDER_PLACED + EXECUTION_REPORT_APPLIED
        # so the simulation route must surface as a PLACED event to be
        # included in the P0-7 §1.4 daily cap.
        if last_trade is not None:
            frozen_amount = (
                last_trade.net_amount
                if last_trade.direction is OrderDirection.BUY
                else 0.0
            )
            # AA-004: persist the entry nameplate on the FILLED event so
            # recovery replay stamps a freshly-created position with the
            # same policy stack the live _apply_buy used (bit-identical
            # replay). getattr-guarded for broker fakes.
            nameplate_hash, nameplate_stack = getattr(
                self._broker, "entry_nameplate", (None, None)
            )
            # AC-001: the per-code style nameplate (stamped on the position at
            # episode-open) rides the FILLED payload too, so recovery rebuilds
            # entry_style instead of resetting it to None on a restart.
            _style_for = getattr(self._broker, "entry_style_for", None)
            nameplate_style = (
                _style_for(last_trade.code) if callable(_style_for) else None
            )
            await self._events.append_many(
                [
                    (
                        BrokerEventType.ORDER_PLACED,
                        timestamp,
                        order_result.order_id,
                        None,
                        plan.instruction_id,
                        {
                            "code": last_trade.code,
                            "direction": last_trade.direction.value,
                            "volume": last_trade.volume,
                            "limit_price": plan.limit_price,
                            "frozen_amount": frozen_amount,
                        },
                    ),
                    (
                        BrokerEventType.ORDER_FILLED,
                        timestamp,
                        order_result.order_id,
                        trade_id,
                        plan.instruction_id,
                        {
                            "code": last_trade.code,
                            "volume": last_trade.volume,
                            "fill_price": last_trade.price,
                            "direction": last_trade.direction.value,
                            "commission": last_trade.commission,
                            "stamp_tax": last_trade.stamp_tax,
                            "transfer_fee": last_trade.transfer_fee,
                            "frozen_amount": frozen_amount,
                            "entry_policy_hash": nameplate_hash,
                            "entry_sell_stack_version": nameplate_stack,
                            "entry_style": nameplate_style,
                        },
                    ),
                ]
            )

        filled_plan = _transition(
            dispatched_plan,
            InstructionStatus.FILLED,
            at=timestamp,
            allow_post_close=True,
        )
        await self._ledger.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.BROKER_FILLED,
            at=timestamp,
            actor="SYSTEM",
            trade_ids=(trade_id,) if trade_id else None,
            payload={
                "broker_order_id": order_result.order_id,
                "trade_id": trade_id or "",
                "fill_price": last_trade.price if last_trade else 0.0,
                "volume": last_trade.volume if last_trade else 0,
            },
        )

        log.info(
            "simulation_executor_filled",
            instruction_id=plan.instruction_id,
            broker_order_id=order_result.order_id,
            trade_id=trade_id,
        )
        _ = filled_plan  # plan-state mutation is owned by the state machine
        return SimulationRouteResult(
            instruction_id=plan.instruction_id,
            final_status=InstructionStatus.FILLED,
            broker_order_id=order_result.order_id,
            trade_ids=(trade_id,) if trade_id else (),
            reason=None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_freeze_sources(self) -> str | None:
        if self._freeze is not None and self._freeze.is_active():
            return f"{ROUTE_FROZEN_REASON}: eod_pipeline_freeze"
        if self._mode_switch is not None and self._mode_switch.is_active():
            return f"{ROUTE_FROZEN_REASON}: mode_switch_in_progress"
        return None

    async def _reject(
        self,
        plan: InstructionPlan,
        *,
        reason: str,
        at: datetime,
        broker_order_id: str | None = None,
    ) -> SimulationRouteResult:
        # Note: the InstructionPlan state machine does not allow
        # VALIDATED → REJECTED directly (the lifecycle owner is the
        # builder / Feishu orchestrator). The executor records the
        # rejection in the ledger + audit but leaves the plan's stored
        # status untouched — callers query SimulationRouteResult.final_status
        # for the routed outcome.
        await self._ledger.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.PLAN_REJECTED,
            at=at,
            actor="SYSTEM",
            broker_order_id=broker_order_id,
            payload={"reason": reason[:256]},
        )
        await self._audit.write(
            event_type=AuditEventType.RISK_ENGINE_CHECK_REJECTED,
            actor=AuditActor.SYSTEM,
            resource_type="instruction_plan",
            resource_id=plan.instruction_id,
            payload={
                "stock_code": plan.stock_code,
                "side": plan.side.value,
                "reason": reason[:256],
                "broker_order_id": broker_order_id,
            },
            outcome=AuditOutcome.BLOCKED,
            correlation_id=plan.instruction_id,
            reason_namespace=ROUTE_FROZEN_REASON,
            timestamp=at,
        )
        return SimulationRouteResult(
            instruction_id=plan.instruction_id,
            final_status=InstructionStatus.REJECTED,
            broker_order_id=broker_order_id,
            trade_ids=(),
            reason=reason,
        )


__all__ = [
    "ROUTE_FROZEN_REASON",
    "SimulationExecutor",
    "SimulationRouteResult",
]


# Silence unused-import warnings used only for typing convenience.
_ = OrderStatus
