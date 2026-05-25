"""RouteCoordinator — single mutually-exclusive routing edge (Phase U-B2).

Every VALIDATED InstructionPlan produced by a Line-1/Line-2 runner is
handed to exactly one :meth:`RouteCoordinator.route` call, which dispatches
it down the **one** path the process's active :class:`RouteMode` selects:

* ``SIMULATION_AUTO``    → :class:`SimulationExecutor` auto-fill (no Feishu)
* ``FEISHU_INTERACTIVE`` → :class:`InstructionDispatcher` (Feishu send, no
  auto-fill — the owner executes manually and reports the fill)
* ``DRY_RUN``            → render-only sink (no send, no broker mutation)

This is the structural guarantee against double execution (Codex P0 #4):
because the two execution paths are selected by a single mutually-exclusive
mode, a plan can never be both auto-filled in simulation *and* fanned out
to the owner. ``tests/orchestration/test_no_stray_route_callers.py`` asserts
that ``SimulationExecutor.route`` has no production caller other than this
coordinator, closing the bypass.

HOLD plans never reach a path: they are dropped here (and again in the
dispatcher + renderer) — HOLD is not routable (CLAUDE.md §2.7).

LLM red line: imports NO ``backend.{llm,agents,agents_team,mirofish}``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import structlog

from backend.models.instruction import InstructionSide, InstructionStatus
from backend.orchestration.instruction_dispatcher import (
    InstructionDispatcher,
    OutboundSignal,
)
from backend.services.run_mode import (
    RouteMode,
    resolve_route_mode,
)
from backend.services.simulation_executor import (
    SimulationExecutor,
    SimulationRouteResult,
)

log = structlog.get_logger(component="orchestration.route_coordinator")


@dataclass(frozen=True)
class RouteOutcome:
    """Audit-grade summary of one routing decision."""

    instruction_id: str
    mode: RouteMode
    action: str
    """``simulation_routed`` | ``dispatched`` | ``skipped_duplicate`` |
    ``send_failed`` | ``dry_run_rendered`` | ``skipped_hold``."""
    final_status: InstructionStatus | None = None
    feishu_message_id: str | None = None
    simulation_result: SimulationRouteResult | None = None


class RouteCoordinator:
    """Route a render-complete signal down the single active mode path."""

    def __init__(
        self,
        *,
        mode: RouteMode,
        simulation_executor: SimulationExecutor,
        dispatcher: InstructionDispatcher,
        dry_run_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._mode = mode
        self._executor = simulation_executor
        self._dispatcher = dispatcher
        # Default dry-run sink logs the rendered text (the dry-run script
        # injects a printer / collector). Never sends, never fills.
        self._dry_sink = dry_run_sink or self._log_dry_run

    @property
    def mode(self) -> RouteMode:
        return self._mode

    async def route(
        self, signal: OutboundSignal, *, now: datetime
    ) -> RouteOutcome:
        """Route ``signal`` once, down the path its mode selects."""
        plan = signal.plan

        # HOLD never routes/renders/sends in any mode (CLAUDE.md §2.7).
        if plan.side is InstructionSide.HOLD:
            log.info("route_skipped_hold", instruction_id=plan.instruction_id)
            return RouteOutcome(
                instruction_id=plan.instruction_id,
                mode=self._mode,
                action="skipped_hold",
            )

        if self._mode is RouteMode.SIMULATION_AUTO:
            sim = await self._executor.route(plan, now=now)
            return RouteOutcome(
                instruction_id=plan.instruction_id,
                mode=self._mode,
                action="simulation_routed",
                final_status=sim.final_status,
                simulation_result=sim,
            )

        if self._mode is RouteMode.FEISHU_INTERACTIVE:
            out = await self._dispatcher.dispatch(signal, now=now)
            return RouteOutcome(
                instruction_id=plan.instruction_id,
                mode=self._mode,
                action=out.action,
                final_status=out.final_status,
                feishu_message_id=out.feishu_message_id,
            )

        # DRY_RUN — render-only. The text is already rendered by the caller;
        # emit it to the sink and touch nothing else (no broker, no Feishu,
        # no ledger/audit side effects).
        self._dry_sink(signal.wire_text)
        return RouteOutcome(
            instruction_id=plan.instruction_id,
            mode=self._mode,
            action="dry_run_rendered",
        )

    @staticmethod
    def _log_dry_run(wire_text: str) -> None:
        log.info("route_dry_run_rendered", wire_text=wire_text)


__all__ = [
    "RouteCoordinator",
    "RouteMode",
    "RouteOutcome",
    "resolve_route_mode",
]
