"""RouteCoordinator tests (U-B2) — mutual-exclusion single routing.

The coordinator is the *single* edge that decides, per VALIDATED
instruction_id, exactly ONE outbound path for the process's active mode:

* ``simulation_auto``   → SimulationExecutor auto-fill (no Feishu send)
* ``feishu_interactive``→ InstructionDispatcher (Feishu send, NO auto-fill)
* ``dry_run``           → render-only (no send, no broker)

The headline invariant (Codex P0 #4): in feishu_interactive the broker is
never auto-filled for the same plan, so the owner's manual fill + report
mirror cannot double-execute against an auto-fill.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.models.instruction import InstructionSide, InstructionStatus
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
    OutboundSignal,
)
from backend.orchestration.route_coordinator import (
    RouteCoordinator,
    RouteMode,
    resolve_route_mode,
)
from backend.services.run_mode import RunMode
from tests.orchestration.conftest import SHANGHAI, FakeFeishuSender, make_plan

_CHAT = "oc_decision_group_0001"
_NOW = dt.datetime(2026, 5, 15, 10, 0, 5, tzinfo=SHANGHAI)


class _RecordingExecutor:
    """Stand-in SimulationExecutor that records whether route() ran."""

    def __init__(self) -> None:
        self.routed: list[str] = []

    async def route(self, plan, *, now=None):
        from backend.services.simulation_executor import SimulationRouteResult

        self.routed.append(plan.instruction_id)
        return SimulationRouteResult(
            instruction_id=plan.instruction_id,
            final_status=InstructionStatus.FILLED,
            broker_order_id="BO-1",
            trade_ids=("T-1",),
            reason=None,
        )


def _signal(plan):
    return OutboundSignal(plan=plan, wire_text="【QuantMind 买入信号】...")


def _coordinator(mode, *, executor, dispatcher, sink=None):
    return RouteCoordinator(
        mode=mode,
        simulation_executor=executor,
        dispatcher=dispatcher,
        dry_run_sink=sink,
    )


async def _dispatcher(ledger, audit_store, sender=None):
    sender = sender or FakeFeishuSender()
    return (
        InstructionDispatcher(
            feishu_client=sender,
            decision_chat_id=_CHAT,
            outbox=InMemoryOutboxRepository(),
            ledger=ledger,
            audit_store=audit_store,
        ),
        sender,
    )


class TestResolveRouteMode:
    def test_dry_run_wins(self):
        rm = RunMode(simulation_auto=True, feishu_interactive=True)
        assert resolve_route_mode(rm, dry_run=True) is RouteMode.DRY_RUN

    def test_feishu_owns_route_when_enabled(self):
        rm = RunMode(simulation_auto=True, feishu_interactive=True)
        assert resolve_route_mode(rm) is RouteMode.FEISHU_INTERACTIVE

    def test_simulation_auto_is_baseline(self):
        rm = RunMode(simulation_auto=True, feishu_interactive=False)
        assert resolve_route_mode(rm) is RouteMode.SIMULATION_AUTO


class TestSimulationAutoRoute:
    async def test_routes_to_executor_not_feishu(self, ledger, audit_store):
        plan = make_plan()
        await ledger.open_for_plan(plan)
        executor = _RecordingExecutor()
        dispatcher, sender = await _dispatcher(ledger, audit_store)
        coord = _coordinator(
            RouteMode.SIMULATION_AUTO, executor=executor, dispatcher=dispatcher
        )

        outcome = await coord.route(_signal(plan), now=_NOW)

        assert outcome.action == "simulation_routed"
        assert executor.routed == [plan.instruction_id]
        assert len(sender.calls) == 0  # NO Feishu fan-out in simulation_auto


class TestFeishuInteractiveRoute:
    async def test_dispatches_feishu_and_never_auto_fills(
        self, ledger, audit_store
    ):
        """NO-DOUBLE-EXECUTION: feishu mode must not auto-fill the broker."""
        plan = make_plan()
        await ledger.open_for_plan(plan)
        executor = _RecordingExecutor()
        dispatcher, sender = await _dispatcher(ledger, audit_store)
        coord = _coordinator(
            RouteMode.FEISHU_INTERACTIVE, executor=executor, dispatcher=dispatcher
        )

        outcome = await coord.route(_signal(plan), now=_NOW)

        assert outcome.action == "dispatched"
        assert len(sender.calls) == 1  # owner gets the decision-group message
        assert executor.routed == []  # broker NEVER auto-filled


class TestDryRunRoute:
    async def test_render_only_no_send_no_broker(self, ledger, audit_store):
        plan = make_plan()
        captured: list[str] = []
        executor = _RecordingExecutor()
        dispatcher, sender = await _dispatcher(ledger, audit_store)
        coord = _coordinator(
            RouteMode.DRY_RUN,
            executor=executor,
            dispatcher=dispatcher,
            sink=captured.append,
        )

        outcome = await coord.route(_signal(plan), now=_NOW)

        assert outcome.action == "dry_run_rendered"
        assert captured == ["【QuantMind 买入信号】..."]
        assert len(sender.calls) == 0
        assert executor.routed == []


class TestHoldNeverRoutes:
    @pytest.mark.parametrize(
        "mode",
        [RouteMode.SIMULATION_AUTO, RouteMode.FEISHU_INTERACTIVE, RouteMode.DRY_RUN],
    )
    async def test_hold_skipped_in_every_mode(self, mode, ledger, audit_store):
        plan = make_plan(side=InstructionSide.HOLD)
        captured: list[str] = []
        executor = _RecordingExecutor()
        dispatcher, sender = await _dispatcher(ledger, audit_store)
        coord = _coordinator(
            mode, executor=executor, dispatcher=dispatcher, sink=captured.append
        )

        outcome = await coord.route(_signal(plan), now=_NOW)

        assert outcome.action == "skipped_hold"
        assert executor.routed == []
        assert len(sender.calls) == 0
        assert captured == []
