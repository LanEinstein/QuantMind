"""Line-2 daily anomaly runner (Phase U-C2).

The Line-2 (held-position monitoring) **daily** production entry point — the
deterministic, **zero-LLM** sibling of the Line-1 runner. Once per trading
day it runs the既有 daily-statistics anomaly detector against the **T-1 EOD**
PIT market frame (Codex P1: keep the daily detector on a replayable snapshot,
distinct from the U-C3 30s intraday runner) and emits at most one SELL per
held code with an adverse anomaly:

    T-1 EOD frame → partition_by_suspension → AnomalyDetector.scan →
    evaluate_sell_intents (sizes from settled available_volume, T+1) →
    assemble_monitoring_plan (single construction point, 14-check) →
    RouteCoordinator

The SELL direction is derived deterministically by the anomaly evaluator —
it does **not** pass through the fund_manager / 4-agent debate (R0 §8 /
P0-10-amendment-2026-05-25-line2): the ``LINE2-MON-`` signal_id prefix marks
the no-debate monitoring path so audit can tell the two lines apart, and
``assemble_monitoring_plan`` rejects a non-prefixed id so a Line-1 plan can
never borrow it.

LLM red line (orchestration isolation + Line-2 zero-LLM): imports NO
``backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}``. The
``backend.monitoring`` detectors are pure-quant (themselves import-clean of
llm/agents); the heavy risk/broker objects the SELL context needs are built
by the caller's :class:`Line2DailyContextProvider` (the U-D1 scheduler /
``main.py``), exactly like Line-1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import InstructionPlan, InstructionStatus
from backend.monitoring.anomaly import AnomalyDetector
from backend.monitoring.degrade import partition_by_suspension
from backend.monitoring.sell_signal import SellIntent, evaluate_sell_intents
from backend.orchestration.instruction_dispatcher import OutboundSignal
from backend.orchestration.route_coordinator import RouteCoordinator, RouteOutcome
from backend.services.instruction_plan_builder import (
    InstructionPlanBuilder,
    MonitoringAssemblyContext,
    MonitoringPlan,
)
from backend.services.ledger import DecisionLedgerService

log = structlog.get_logger(component="orchestration.line2_daily_runner")

MONITORING_SIGNAL_PREFIX = "LINE2-MON-"
"""Locked Line-2 signal_id prefix (audit + assemble_monitoring_plan gate)."""


class SellRouteOutcome(StrEnum):
    """Terminal outcome of one held-code SELL evaluation."""

    ROUTED = "routed"
    """A VALIDATED SELL was rendered + handed to the RouteCoordinator."""
    REJECTED = "rejected"
    """The RiskEngine 14-check rejected the SELL — no signal sent."""
    EARLY_RETURN = "early_return"
    """A freeze early-return blocked the SELL (mode switch / ticket / data)."""


@dataclass(frozen=True)
class SellRoute:
    """Per-held-code SELL routing summary."""

    code: str
    outcome: SellRouteOutcome
    anomaly_reason: str
    route_outcome: RouteOutcome | None = None
    plan: InstructionPlan | None = None


@dataclass(frozen=True)
class Line2DailyRunResult:
    """Audit-grade summary of one Line-2 daily run."""

    signal_id: str
    held_count: int
    active_count: int
    degraded_codes: tuple[str, ...] = ()
    sell_routes: tuple[SellRoute, ...] = ()


@runtime_checkable
class Line2DailyContextProvider(Protocol):
    """Caller-supplied bridge to the risk/broker/data objects the runner must
    not import (orchestration isolation + Line-2 zero-LLM).

    Implemented by the U-D1 scheduler (pulls live held positions from the
    MockBroker + builds the RiskEngine + per-code SELL context). Tests inject
    a fake. ``held_positions`` / ``spot_by_code`` are typed ``Any`` because
    their concrete types (``backend.broker.Position`` /
    ``backend.data.WatchlistMarketSnapshot``) live in packages the runner is
    forbidden to import — it only passes them through the pure monitoring
    functions.
    """

    @property
    def held_positions(self) -> Sequence[Any]:
        """Current held positions (``backend.broker.Position`` objects)."""
        ...

    @property
    def spot_by_code(self) -> Mapping[str, Any]:
        """code → spot snapshot for suspension partitioning (may be empty)."""
        ...

    @property
    def name_by_code(self) -> Mapping[str, str]:
        """code → display name for the SELL intent / rendered message."""
        ...

    def build_sell_context(
        self, intent: SellIntent, *, signal_id: str, seq: int, now: datetime
    ) -> MonitoringAssemblyContext:
        """Build the SELL ``MonitoringAssemblyContext`` for one intent.

        Supplies the per-code risk/broker objects (account / positions /
        prev_close / daily_state / stock_meta / RiskEngine / circuit_breaker /
        data_quality / watchlist_policy+signal) via ``make_sell_context``.
        """
        ...


class Line2DailyRunner:
    """Compose the Line-2 daily anomaly→SELL chain into one production run."""

    def __init__(
        self,
        *,
        anomaly_detector: AnomalyDetector,
        builder: InstructionPlanBuilder,
        renderer: MessageRenderer,
        coordinator: RouteCoordinator,
        ledger: DecisionLedgerService,
        pilot: bool = False,
    ) -> None:
        self._detector = anomaly_detector
        self._builder = builder
        self._renderer = renderer
        self._coordinator = coordinator
        self._ledger = ledger
        # PILOT go-live tier → prepend the "模拟盘·人工·试点" banner to every
        # order-bearing Feishu message (P0-6-amendment-2026-05-25 §2.3).
        self._pilot = pilot

    async def run(
        self,
        *,
        frame: MarketDataSnapshot,
        provider: Line2DailyContextProvider,
        now: datetime,
        signal_id: str | None = None,
    ) -> Line2DailyRunResult:
        """Run the Line-2 daily anomaly scan once; route every SELL it finds."""
        # Only None derives the default — an explicit "" (or any non-prefixed
        # caller id) must fail the prefix check, not be silently replaced by a
        # ``signal_id or default`` falsy coercion (Codex U-C2 verify P2).
        sid = (
            f"{MONITORING_SIGNAL_PREFIX}{frame.trade_date}-daily"
            if signal_id is None
            else signal_id
        )
        if not sid.startswith(MONITORING_SIGNAL_PREFIX):
            raise ValueError(
                f"Line-2 signal_id {sid!r} must start with {MONITORING_SIGNAL_PREFIX!r}"
            )

        held = tuple(provider.held_positions)
        if not held:
            log.info("line2_daily_empty_portfolio", signal_id=sid)
            return Line2DailyRunResult(signal_id=sid, held_count=0, active_count=0)

        # Suspended holdings degrade cleanly (no SELL on a halted instrument).
        held_codes = [p.code for p in held]
        partition = partition_by_suspension(held_codes, dict(provider.spot_by_code))

        # Deterministic daily-statistics anomaly scan over the active holdings.
        scan = self._detector.scan(frame, partition.active_codes, sid)
        intents = evaluate_sell_intents(
            scan, held, name_by_code=dict(provider.name_by_code)
        )

        routes: list[SellRoute] = []
        for seq, intent in enumerate(intents, start=1):
            routes.append(await self._route_sell(intent, provider, sid, seq, now))

        log.info(
            "line2_daily_complete",
            signal_id=sid,
            held=len(held),
            active=len(partition.active_codes),
            degraded=len(partition.degrades),
            intents=len(intents),
            routed=sum(1 for r in routes if r.outcome is SellRouteOutcome.ROUTED),
        )
        return Line2DailyRunResult(
            signal_id=sid,
            held_count=len(held),
            active_count=len(partition.active_codes),
            degraded_codes=tuple(d.code for d in partition.degrades),
            sell_routes=tuple(routes),
        )

    async def _route_sell(
        self,
        intent: SellIntent,
        provider: Line2DailyContextProvider,
        signal_id: str,
        seq: int,
        now: datetime,
    ) -> SellRoute:
        """Assemble + route one SELL intent through the single construction point."""
        context = provider.build_sell_context(
            intent, signal_id=signal_id, seq=seq, now=now
        )
        built = await self._builder.assemble_monitoring_plan(context)

        common = {"code": intent.code, "anomaly_reason": intent.anomaly_reason}
        if not isinstance(built, MonitoringPlan):
            # A freeze early-return (mode switch / ticket / data quality).
            log.info("line2_daily_early_return", signal_id=signal_id, code=intent.code)
            return SellRoute(outcome=SellRouteOutcome.EARLY_RETURN, **common)

        # Open the decision-ledger entry before routing (idempotent
        # PLAN_DRAFTED) — both routing targets append onto it (mirrors Line-1).
        plan = built.plan
        await self._ledger.open_for_plan(plan, at=now)
        if plan.status is not InstructionStatus.VALIDATED:
            return SellRoute(outcome=SellRouteOutcome.REJECTED, plan=plan, **common)

        wire = self._renderer.render_monitoring_sell(
            plan, anomaly_reason=intent.anomaly_reason, pilot=self._pilot
        )
        outcome = await self._coordinator.route(
            OutboundSignal(plan=plan, wire_text=wire), now=now
        )
        log.info(
            "line2_daily_sell_routed",
            signal_id=signal_id,
            instruction_id=plan.instruction_id,
            action=outcome.action,
            mode=outcome.mode.value,
        )
        return SellRoute(
            outcome=SellRouteOutcome.ROUTED,
            plan=plan,
            route_outcome=outcome,
            **common,
        )


__all__ = [
    "MONITORING_SIGNAL_PREFIX",
    "Line2DailyContextProvider",
    "Line2DailyRunResult",
    "Line2DailyRunner",
    "SellRoute",
    "SellRouteOutcome",
]
