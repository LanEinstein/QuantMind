#!/usr/bin/env python
"""U-D3 — render-only double-line 1-day dry-run harness.

Walks ONE real trading day on the pinned-clock harness (``simulate_n_trading_days``
``run_simulation(days=1, …)``) and drives BOTH production lines **render-only**
against a **real Tushare T-1 EOD frame** + **real qwen** (no LLM stub):

* **Line-1** (full-market BUY selection) fires once at ``morning_open`` —
  screen → budget tier → candidate-select → ONE real 4-agent qwen debate →
  ``assemble_plan`` (14-check single construction point) → RouteCoordinator.
* **Line-2 daily** (held-position SELL scan) fires once at ``morning_open`` —
  deterministic, zero-LLM anomaly scan over the same T-1 frame.
* **Line-2 30s intraday** (SELL/ADD triggers) fires on each
  ``intraday_mtm_sample`` tick — deterministic, zero-LLM live-quote triggers.

Every routed signal is RENDER-ONLY: the shared :class:`RouteCoordinator` runs
in :data:`RouteMode.DRY_RUN`, whose branch calls ``dry_run_sink(wire_text)`` and
touches NOTHING else — no Feishu send, no order matching, no MockBroker
mutation, no ledger/audit write from routing. The harness asserts this BY
CONSTRUCTION (the SimulationExecutor + InstructionDispatcher it must pass to the
coordinator ctor are no-op call-recording stubs that the DRY_RUN branch never
invokes — :meth:`_NoopExecutor.route` / :meth:`_NoopDispatcher.dispatch` raise
if ever called).

WHY render-only against real qwen: the artifact this writes
(``data/dry_run/<trade_date>_double_line.json``) is what the **owner reads** to
judge whether the live signals are *reasonable* before flipping
``dry_run_double_line_pass`` in ``config/pilot_readiness.yaml`` (PILOT condition
3). A stubbed LLM would render canned text the owner cannot judge — so the real
run uses real qwen with the ¥20/day cost_guard hard reserve still in force
(reused via the existing runners/agents path; never bypassed). The harness
itself NEVER flips the manifest — it writes ``owner_reviewed: false`` /
``pass: false`` and the owner flips them after review.

The 5 carry-forward prereqs are wired faithfully for a FRESH 1-day dry-run
(each ``# prereq N`` tagged at its wiring point below): (1) per-code
data_quality via a real :class:`DataQualityProvider` (clean fallback offline);
(2) today_instruction_count starts 0 + increments per rendered BUY so check-10
engages; (3) day-open NAV + ledger PnL legitimately zero on a fresh ¥100k
account; (4) index_closes from ``TushareClient.index_daily`` (empty → NEUTRAL);
(5) suspension partition excludes universe codes absent from the frame quotes.

Red lines honoured (CLAUDE.md §2.0 / §2.7): zero real send / matching / broker
mutation / ledger write from routing (DRY_RUN guarantees it); InstructionPlans
only via the builder (the harness never constructs ``InstructionPlan(...)``);
LLM only ever writes the 4 allowed text fields (reused runners enforce it).

Usage (owner-driven; real Tushare + real qwen; costs budget)::

    python scripts/dry_run_double_line.py [--start YYYY-MM-DD] [--json] [--out PATH]

Exit codes: ``0`` on success; ``1`` on any tick error, a frame-assembly
failure, or 0 signals when signals were expected (Line-1 BUY).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from backend.data.trading_calendar import (
    is_trading_day,
    next_trading_day,
    prev_trading_day,
)
from backend.marketdata_snapshot import MarketDataSnapshot, SnapshotStore
from backend.orchestration.line1_runner import Line1RunResult
from backend.orchestration.line2_daily_runner import SellRouteOutcome
from backend.orchestration.line2_intraday_runner import TriggerRouteOutcome
from backend.orchestration.route_coordinator import RouteMode
from scripts.dry_run_artifact import (
    build_artifact,
    format_json,
    format_table,
    write_artifact,
)
from scripts.dry_run_realdata import (
    assemble_real_frame,
    build_data_layer,
    build_redis_or_none,
    pull_index_closes,
    resolve_llm_models,
)
from scripts.simulate_n_trading_days import run_simulation

log = structlog.get_logger(component="scripts.dry_run_double_line")

# The pinned-clock harness fires these labels (simulate_n_trading_days). We map
# Line-1 + Line-2-daily to morning_open (the once-per-day scans) and Line-2
# intraday to every intraday_mtm_sample tick.
_LINE1_TICK = "morning_open"
_LINE2_DAILY_TICK = "morning_open"
_LINE2_INTRADAY_TICK = "intraday_mtm_sample"

_DEFAULT_OUT_DIR = "data/dry_run"
_INITIAL_CAPITAL = 100_000.0


# ---------------------------------------------------------------------------
# Render-only sink + the no-op execution stubs (asserted never called)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderedSignal:
    """One render-only routed signal (the owner reads ``wire_text``)."""

    line: str  # "line1" | "line2_daily" | "line2_intraday"
    side: str  # "BUY" | "SELL" | "ADD"
    instruction_id: str
    code: str
    wire_text: str


class DryRunCollector:
    """Collects every rendered wire text; the RouteCoordinator's dry_run_sink.

    The coordinator's DRY_RUN branch calls ``__call__(wire_text)`` with ONLY
    the rendered text (no plan). The harness later pairs each text with its
    structured metadata (line / side / id / code) by matching the runner's
    per-route result — every wire text carries its ``instruction_id`` (the
    renderer enforces the canonical pattern), so the match is exact and the
    sink itself stays a dumb append-only buffer (no shared mutable state with
    the runners). The pairing is built once in :meth:`finalize`.
    """

    def __init__(self) -> None:
        self.raw_texts: list[str] = []
        self._labels: dict[str, tuple[str, str, str]] = {}

    def __call__(self, wire_text: str) -> None:
        """RouteCoordinator dry_run_sink — capture the rendered text in order."""
        self.raw_texts.append(wire_text)

    def label(self, instruction_id: str, *, line: str, side: str, code: str) -> None:
        """Register the metadata for a routed instruction (called by the harness)."""
        self._labels[instruction_id] = (line, side, code)

    @property
    def signals(self) -> tuple[RenderedSignal, ...]:
        """Pair each rendered text with its registered metadata (by id match)."""
        out: list[RenderedSignal] = []
        for text in self.raw_texts:
            line, side, code, iid = "unknown", "?", "?", "?"
            for cand_id, (cl, cs, cc) in self._labels.items():
                if cand_id in text:
                    line, side, code, iid = cl, cs, cc, cand_id
                    break
            out.append(
                RenderedSignal(
                    line=line, side=side, instruction_id=iid,
                    code=code, wire_text=text,
                )
            )
        return tuple(out)


class _NoopExecutor:
    """SimulationExecutor stub the DRY_RUN coordinator must NEVER call.

    Passed only to satisfy the :class:`RouteCoordinator` ctor signature. The
    DRY_RUN branch renders to the sink and never touches the executor; if it
    ever did, this raises so the violation surfaces immediately (and a test
    asserts it stays uncalled).
    """

    def __init__(self) -> None:
        self.calls = 0

    async def route(self, plan: Any, *, now: dt.datetime) -> Any:
        self.calls += 1
        raise AssertionError(
            "DRY_RUN must never reach the SimulationExecutor — render-only "
            "red line broken (no broker mutation allowed)"
        )


class _NoopDispatcher:
    """InstructionDispatcher stub the DRY_RUN coordinator must NEVER call."""

    def __init__(self) -> None:
        self.calls = 0

    async def dispatch(self, signal: Any, *, now: dt.datetime) -> Any:
        self.calls += 1
        raise AssertionError(
            "DRY_RUN must never reach the InstructionDispatcher — render-only "
            "red line broken (no Feishu send allowed)"
        )


# ---------------------------------------------------------------------------
# Wiring — build the two lines sharing one DRY_RUN coordinator + builder
# ---------------------------------------------------------------------------


@dataclass
class DryRunContext:
    """Everything one dry-run needs, assembled once before the tick loop."""

    collector: DryRunCollector
    executor: _NoopExecutor
    dispatcher: _NoopDispatcher
    line1_runner: Any
    line2_daily_runner: Any
    line2_intraday_runner: Any
    broker: Any
    risk_engine: Any
    risk_config: Any
    circuit_breaker: Any
    watchlist_policy: Any
    llm_router: Any
    frame: MarketDataSnapshot
    index_closes: tuple[float, ...]
    market_meta: Any
    market_data: Any
    data_quality_provider: Any
    snapshot_store: SnapshotStore
    run_trade_date: str
    frame_trade_date: str
    token_fingerprint: str
    llm_models: tuple[str, ...]
    redis_client: Any = None
    # Mutable run state: increment per rendered BUY so check-10 engages.
    today_instruction_count: int = 0
    line1_results: list[Line1RunResult] = field(default_factory=list)
    line2_daily_results: list[Any] = field(default_factory=list)
    line2_intraday_results: list[Any] = field(default_factory=list)


def _build_coordinator(
    collector: DryRunCollector,
) -> tuple[Any, _NoopExecutor, _NoopDispatcher]:
    """Build the single DRY_RUN RouteCoordinator with no-op execution stubs."""
    from backend.orchestration.route_coordinator import RouteCoordinator

    executor = _NoopExecutor()
    dispatcher = _NoopDispatcher()
    coordinator = RouteCoordinator(
        mode=RouteMode.DRY_RUN,
        simulation_executor=executor,
        dispatcher=dispatcher,
        dry_run_sink=collector,
    )
    return coordinator, executor, dispatcher


def _build_runners(*, coordinator: Any, exclusion_rules: Any, risk_yaml: str,
                   selector_yaml: str, redis_client: Any
                   ) -> tuple[Any, Any, Any, SnapshotStore]:
    """Construct the three import-isolated production runners (pilot=False).

    pilot=False: this is a dry run, NOT a pilot — no "模拟盘·人工·试点" banner.
    All three runners share ONE builder + ONE coordinator so the single
    construction point + mode-switch / freeze gates are identical across lines
    (mirrors ``backend.main._init_line2_runners``).
    """
    from backend.audit.store import AuditStore, InMemoryAuditCollection
    from backend.budget_policy.policy import BudgetTierPolicy, load_budget_tier_config
    from backend.candidate_selector.selector import (
        CandidateSelector,
        load_selector_config,
    )
    from backend.integrations.feishu.renderer import MessageRenderer
    from backend.monitoring.anomaly import AnomalyDetector
    from backend.orchestration.intraday_manifest import IntradayTriggerManifestStore
    from backend.orchestration.line1_runner import Line1Runner
    from backend.orchestration.line2_daily_runner import Line2DailyRunner
    from backend.orchestration.line2_intraday_runner import Line2IntradayRunner
    from backend.screening.screener import Screener
    from backend.services.instruction_plan_builder import InstructionPlanBuilder
    from backend.services.ledger import DecisionLedgerService, InMemoryLedgerRepository

    # In-memory audit + ledger: the dry run never persists routing side effects.
    # The ledger.open_for_plan IS called by the runners before routing (it is
    # the "plan drafted" lifecycle step, not a routing side effect) — an
    # in-memory repo keeps it durable-free, matching the render-only contract.
    snap_root = os.environ.get(
        "QUANTMIND_DRYRUN_SNAPSHOT_ROOT", "data/dry_run/line2_snapshots"
    )
    manifest_root = os.environ.get(
        "QUANTMIND_DRYRUN_MANIFEST_ROOT", "data/dry_run/intraday_manifests"
    )
    audit_jsonl = Path(
        os.environ.get("QUANTMIND_DRYRUN_AUDIT_JSONL", "data/dry_run/audit.jsonl")
    )
    # In-memory Mongo collection: the dry run never persists routing side
    # effects to a durable audit store. The JSONL path is a local scratch file
    # (the builder writes its own internal events there, NOT routing sends).
    audit = AuditStore(InMemoryAuditCollection(), jsonl_path=audit_jsonl)
    builder = InstructionPlanBuilder(audit_store=audit)
    renderer = MessageRenderer()
    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    snapshot_store = SnapshotStore(root=snap_root)
    manifest_store = IntradayTriggerManifestStore(manifest_root)

    line1 = Line1Runner(
        screener=Screener(exclusion_rules),
        budget_policy=BudgetTierPolicy(load_budget_tier_config(risk_yaml)),
        selector=CandidateSelector(load_selector_config(selector_yaml)),
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        redis_client=redis_client,
        pilot=False,
    )
    line2_daily = Line2DailyRunner(
        anomaly_detector=AnomalyDetector(),
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        pilot=False,
    )
    line2_intraday = Line2IntradayRunner(
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        snapshot_store=snapshot_store,
        manifest_store=manifest_store,
        pilot=False,
    )
    return line1, line2_daily, line2_intraday, snapshot_store


# ---------------------------------------------------------------------------
# Tick callbacks — run each line render-only, record signals for the artifact
# ---------------------------------------------------------------------------


async def _run_line1(ctx: DryRunContext, now: dt.datetime) -> None:
    """Run Line-1 once; record the rendered BUY (prereq 2: count increments)."""
    from backend.portfolio_allocation import load_allocation_policy
    from backend.services.line1_context_provider import (
        Line1ContextProvider,
        build_line1_run_state,
    )

    # P-002/P-003: exercise the inverse-volatility allocation clamp end-to-end —
    # the dry-run basket is sized by the allocation envelope (not each name to
    # 15%), demonstrating "充分考虑持仓配比" over a multi-name shortlist.
    allocation_policy = load_allocation_policy(
        os.environ.get(
            "QUANTMIND_ALLOCATION_CONFIG_PATH", "config/allocation_policy.yaml"
        ),
        os.environ.get("QUANTMIND_RISK_CONFIG_PATH", "config/risk.yaml"),
    )

    run_state = await build_line1_run_state(
        broker=ctx.broker,
        risk_engine=ctx.risk_engine,
        circuit_breaker=ctx.circuit_breaker,
        watchlist_policy=ctx.watchlist_policy,
        risk_config=ctx.risk_config,
        now=now,
        open_tickets=(),
        today_instruction_count=ctx.today_instruction_count,  # prereq 2
    )
    provider = Line1ContextProvider(
        run_state=run_state, frame=ctx.frame, llm_router=ctx.llm_router, now=now,
        # U-E2 / 缺口4: live dual-source spot + 卖一 orderbook → cage limit. When
        # the live layer is unreachable (pure offline replay) market_data is
        # None and every lead degrades to a non-actionable notice (never the
        # T-1 close). PIT sink records the live cage inputs for replay.
        market_data=ctx.market_data,
        snapshot_store=ctx.snapshot_store,
        allocation_policy=allocation_policy,
    )
    # Stage the BUY metadata so the sink can attach the rendered text. The
    # runner records the lead AFTER the debate; we cannot know it pre-call, so
    # record() is invoked from a sink-shim wrapped around the run instead.
    result = await ctx.line1_runner.run(frame=ctx.frame, provider=provider, now=now)
    ctx.line1_results.append(result)
    # Label EVERY routed BUY in the basket (P1-7-amendment-2026-05-26): Line-1
    # now produces a multi-name basket, so each rendered wire text must pair
    # with its own metadata (else the 2nd+ stay line="unknown" and the
    # line1_rendered count + owner-reviewed artifact under-report the basket),
    # and each consumes a check-10 daily order slot.
    for routed in result.routed_buys:
        ctx.today_instruction_count += 1  # prereq 2 — engage check-10
        ctx.collector.label(
            routed.plan.instruction_id,
            line="line1",
            side="BUY",
            code=routed.plan.stock_code,
        )


async def _run_line2_daily(ctx: DryRunContext, now: dt.datetime) -> None:
    """Run the Line-2 daily SELL scan once over the T-1 frame (zero LLM)."""
    from backend.services.line2_context_providers import (
        Line2DailyProvider,
        build_line2_code_contexts,
        build_line2_run_state,
    )

    run_state = await build_line2_run_state(
        broker=ctx.broker,
        risk_engine=ctx.risk_engine,
        circuit_breaker=ctx.circuit_breaker,
        watchlist_policy=ctx.watchlist_policy,
        now=now,
        open_tickets=(),
        today_instruction_count=ctx.today_instruction_count,  # prereq 2
        today_portfolio_pnl_pct=0.0,  # prereq 3 — fresh account, NAV at open
        recent_trade_pnls=(),         # prereq 3 — no settled trades yet
    )
    if not run_state.positions:
        # Empty portfolio on a fresh dry-run → no SELL expected (not an error).
        return
    names = {p.code.split(".")[0].strip(): p.code.split(".")[0].strip()
             for p in run_state.positions}
    contexts = await build_line2_code_contexts(
        codes=[p.code for p in run_state.positions],
        name_by_code=names,
        market_meta=ctx.market_meta,
        frame=ctx.frame,
        data_quality_provider=ctx.data_quality_provider,  # prereq 1
        now=now,
    )
    provider = Line2DailyProvider(
        run_state=run_state,
        code_contexts=contexts,
        name_by_code=names,
        snapshot_at=ctx.frame.fetch_time_utc,
    )
    result = await ctx.line2_daily_runner.run(
        frame=ctx.frame, provider=provider, now=now
    )
    ctx.line2_daily_results.append(result)
    for route in result.sell_routes:
        if route.outcome is SellRouteOutcome.ROUTED and route.plan is not None:
            ctx.collector.label(
                route.plan.instruction_id, line="line2_daily",
                side="SELL", code=route.code,
            )


async def _run_line2_intraday(ctx: DryRunContext, now: dt.datetime) -> None:
    """Run one Line-2 30s intraday tick (deterministic SELL/ADD; zero LLM)."""
    from backend.services.line2_context_providers import (
        Line2IntradayProvider,
        build_line2_code_contexts,
        build_line2_run_state,
    )

    run_state = await build_line2_run_state(
        broker=ctx.broker,
        risk_engine=ctx.risk_engine,
        circuit_breaker=ctx.circuit_breaker,
        watchlist_policy=ctx.watchlist_policy,
        now=now,
        open_tickets=(),
        today_instruction_count=ctx.today_instruction_count,
        today_portfolio_pnl_pct=0.0,
        recent_trade_pnls=(),
    )
    if not run_state.positions:
        return
    names = {p.code.split(".")[0].strip(): p.code.split(".")[0].strip()
             for p in run_state.positions}
    contexts = await build_line2_code_contexts(
        codes=[p.code for p in run_state.positions],
        name_by_code=names,
        market_meta=ctx.market_meta,
        frame=ctx.frame,
        data_quality_provider=ctx.data_quality_provider,
        now=now,
    )

    async def _fetch_spots(codes: Sequence[str]) -> dict[str, Any]:
        if ctx.market_data is None:
            return {}
        # Tag the batch strictly before the tick ``now`` (runner contract).
        snap_at = now - dt.timedelta(seconds=1)
        rows = await ctx.market_data.get_watchlist_snapshot(list(codes), snap_at)
        return {row.code: row for row in rows}

    provider = Line2IntradayProvider(
        run_state=run_state,
        code_contexts=contexts,
        name_by_code=names,
        daily_frame=ctx.frame,
        index_closes=ctx.index_closes,  # prereq 4 — bear-regime ADD ban
        fetch_spots_fn=_fetch_spots,
    )
    result = await ctx.line2_intraday_runner.run(provider=provider, now=now)
    ctx.line2_intraday_results.append(result)
    for route in result.routes:
        if route.outcome is TriggerRouteOutcome.ROUTED and route.plan is not None:
            side = "ADD" if route.side.value == "BUY" else "SELL"
            ctx.collector.label(
                route.plan.instruction_id, line="line2_intraday",
                side=side, code=route.code,
            )


def _make_tick_callback(
    ctx: DryRunContext,
) -> Callable[[dt.datetime, str], Awaitable[None]]:
    """Build the pinned-tick callback that fans out to the three lines."""

    async def _on_tick(when: dt.datetime, label: str) -> None:
        if label == _LINE1_TICK:
            await _run_line1(ctx, when)
            await _run_line2_daily(ctx, when)
        elif label == _LINE2_INTRADAY_TICK:
            await _run_line2_intraday(ctx, when)
        # Other pinned labels (morning_close / afternoon_* / eod_pipeline /
        # advance_day) are no-ops for a render-only dry run — no EOD pipeline,
        # no MTM, no acceptance write (those mutate broker/ledger state).

    return _on_tick


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRunOutcome:
    """JSON-shaped envelope the harness emits at the end of a run."""

    ok: bool
    run_trade_date: str
    frame_trade_date: str
    artifact_path: str
    line1_rendered: int
    line2_daily_rendered: int
    line2_intraday_rendered: int
    cost_guard_spend_rmb: float | None
    errors: tuple[str, ...]


async def run_dry_run(
    ctx: DryRunContext, *, start_date: dt.date, out_path: Path
) -> DryRunOutcome:
    """Walk 1 trading day render-only, write the artifact, return the outcome.

    ``ok`` is False if any tick raised OR Line-1 rendered 0 BUY signals (a BUY
    is expected from a full-market screen on a real trading day — 0 means the
    chain silently no-op'd, which the owner must investigate before PILOT).
    """
    tick_cb = _make_tick_callback(ctx)
    sim = await run_simulation(
        days=1,
        start_date=start_date,
        tick_callback=tick_cb,
        allow_real_llm=True,  # real qwen — the owner's documented contract
    )

    artifact = build_artifact(ctx, initial_capital=_INITIAL_CAPITAL)
    spend = await _read_cost_guard_spend(ctx)
    artifact["run_metadata"]["cost_guard_spend_rmb"] = spend
    write_artifact(artifact, out_path)

    line1_rendered = sum(
        1 for s in ctx.collector.signals if s.line == "line1"
    )
    line2_daily_rendered = sum(
        1 for s in ctx.collector.signals if s.line == "line2_daily"
    )
    line2_intraday_rendered = sum(
        1 for s in ctx.collector.signals if s.line == "line2_intraday"
    )

    errors: list[str] = list(sim.tick_callback_errors)
    # Line-1 BUY is the signal we positively expect on a real trading day.
    if line1_rendered == 0 and not errors:
        errors.append(
            "line1 rendered 0 BUY signals — a full-market screen on a real "
            "trading day should surface a candidate; investigate the chain"
        )

    return DryRunOutcome(
        ok=not errors,
        run_trade_date=ctx.run_trade_date,
        frame_trade_date=ctx.frame_trade_date,
        artifact_path=str(out_path),
        line1_rendered=line1_rendered,
        line2_daily_rendered=line2_daily_rendered,
        line2_intraday_rendered=line2_intraday_rendered,
        cost_guard_spend_rmb=spend,
        errors=tuple(errors),
    )


async def _read_cost_guard_spend(ctx: DryRunContext) -> float | None:
    """Read today's aggregate LLM spend for the run (None if no redis)."""
    if ctx.redis_client is None:
        return None
    try:
        from backend.services.cost_guard import get_daily_spent

        return await get_daily_spent(ctx.redis_client)
    except Exception as exc:  # noqa: BLE001 — spend read is best-effort metadata
        log.warning("dry_run_cost_spend_read_failed", error=str(exc))
        return None


async def build_real_context(*, start_date: dt.date) -> DryRunContext:
    """Assemble the real-data DryRunContext (real Tushare frame + real qwen).

    Owner-driven: builds the real TushareClient frame, the real LLMRouter, a
    fresh ¥100k MockBroker, the RiskEngine / CircuitBreaker / configs loaded the
    same way ``backend.main`` loads them, and the live data layer (market_data /
    market_meta / DataQualityProvider) where constructible.
    """
    from backend.broker.mock_broker import MockBroker
    from backend.broker.models import (
        BrokerConfig,
        CircuitBreakerConfig,
        load_risk_config,
    )
    from backend.llm.router import LLMRouter
    from backend.risk.circuit_breaker import CircuitBreaker
    from backend.risk.engine import RiskEngine
    from backend.services.universe_policy import ExclusionRules, load_policy

    # The run day MUST match what run_simulation walks: it rolls start_date
    # FORWARD to the first trading day >= start (iter_trading_days). Mirror that
    # here with next_trading_day (NOT prev) so the artifact's run_trade_date +
    # the T-1 frame stay aligned with the actual simulated ticks even for a
    # weekend/holiday --start (Codex U-D3 P2). Line-1 runs against the PREVIOUS
    # close, so the frame is the run day's T-1 EOD.
    run_day = start_date if is_trading_day(start_date) else next_trading_day(start_date)
    t_minus_1 = prev_trading_day(run_day)
    signal_id = f"DRYRUN-{run_day.strftime('%Y%m%d')}-line1"

    frame, frame_td, token_fp = await assemble_real_frame(
        as_of=t_minus_1, signal_id=signal_id
    )
    index_closes = await pull_index_closes(end=t_minus_1)

    risk_yaml = os.environ.get("QUANTMIND_RISK_CONFIG_PATH", "config/risk.yaml")
    selector_yaml = os.environ.get(
        "QUANTMIND_SELECTOR_CONFIG_PATH", "config/candidate_weights/v1.yaml"
    )
    policy_path = os.environ.get(
        "QUANTMIND_UNIVERSE_POLICY_PATH", "config/universe_policy.yaml"
    )
    risk_config = load_risk_config(risk_yaml)
    risk_engine = RiskEngine(risk_config)
    circuit_breaker = CircuitBreaker(
        getattr(risk_config, "circuit_breaker", None) or CircuitBreakerConfig()
    )
    # The screener exclusion is the LAST-line defense (real exclusion is in the
    # screen per P0-9-amendment); ExclusionRules() defaults == the locked P0-9
    # values, so a missing/bad policy file degrades to the locked rules rather
    # than aborting the dry run (mirrors backend.main._init_line2_runners).
    try:
        watchlist_policy = load_policy(policy_path)
        exclusion_rules = watchlist_policy.exclusion_rules
    except Exception as exc:  # noqa: BLE001 — ExclusionRules() == locked P0-9
        log.warning("dry_run_exclusion_rules_fallback", error=str(exc))
        watchlist_policy = None
        exclusion_rules = ExclusionRules()

    broker = MockBroker(config=BrokerConfig(initial_capital=_INITIAL_CAPITAL))

    # Build the live Redis ONCE, BEFORE the runners + router (Codex U-D3 P1):
    # Line-1's 4-agent debate reserves budget on it (run_shortlist →
    # reserve_budget(redis_client, …)), and the LLM router needs it for cost
    # tracking + MUST be initialize()d before the first complete() — mirrors the
    # backend.main lifespan order. A None redis (offline) means no real run can
    # reserve budget, which fail-closes the debate (the owner-driven run has the
    # live 127.0.0.1 Redis).
    redis_client = build_redis_or_none()
    # Simulate a FRESH trading day: clear the transient fan-out / reservation
    # gate counters (debate-slot, in-flight reservation, anomaly) that normally
    # reset at the 00:00 BrokerScheduler cron. Without this a second same-day
    # dry-run inherits the first run's debate-slot count and spuriously trips
    # max_debates_per_day (the audited LLM spend is left intact).
    # Guard (codex P2): NEVER clear live gates if a production run is active —
    # the dry-run is a pre-go-live, render-only validation tool and must not
    # touch a live backend's in-flight reservation / fan-out gates. In a normal
    # (non-prod) dry-run QUANTMIND_PROD_RUN is unset, so the fresh-day reset
    # runs and the harness is reproducible.
    if redis_client is not None and os.environ.get("QUANTMIND_PROD_RUN"):
        log.warning("dry_run_skip_gate_reset_prod_active")
    elif redis_client is not None:
        from backend.services.cost_guard import reset_daily_gate_counters

        await reset_daily_gate_counters(redis_client)
    llm_router = LLMRouter(config_path="config/agent_models.yaml")
    await llm_router.initialize(redis_client=redis_client)
    llm_models = resolve_llm_models()

    collector = DryRunCollector()
    coordinator, executor, dispatcher = _build_coordinator(collector)
    line1, line2_daily, line2_intraday, snapshot_store = _build_runners(
        coordinator=coordinator,
        exclusion_rules=exclusion_rules,
        risk_yaml=risk_yaml,
        selector_yaml=selector_yaml,
        redis_client=redis_client,
    )

    market_data, market_meta, dq_provider = await build_data_layer()

    return DryRunContext(
        collector=collector,
        executor=executor,
        dispatcher=dispatcher,
        line1_runner=line1,
        line2_daily_runner=line2_daily,
        line2_intraday_runner=line2_intraday,
        broker=broker,
        risk_engine=risk_engine,
        risk_config=risk_config,
        circuit_breaker=circuit_breaker,
        watchlist_policy=watchlist_policy,
        llm_router=llm_router,
        frame=frame,
        index_closes=index_closes,
        market_meta=market_meta,
        market_data=market_data,
        data_quality_provider=dq_provider,
        snapshot_store=snapshot_store,
        run_trade_date=run_day.strftime("%Y%m%d"),
        frame_trade_date=frame_td,
        token_fingerprint=token_fp,
        llm_models=llm_models,
        redis_client=redis_client,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dry_run_double_line",
        description=(
            "Render-only double-line 1-day dry-run (real Tushare frame + real "
            "qwen). Writes a PASS artifact for owner review (PILOT cond 3)."
        ),
    )
    parser.add_argument(
        "--start",
        default=None,
        help=(
            "ISO date for the run day (defaults to today Asia/Shanghai). Rolls "
            "to the nearest trading day; the frame is its T-1 EOD."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Artifact path (default data/dry_run/<run_trade_date>_double_line.json).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON envelope (default: table)."
    )
    return parser.parse_args(argv)


def _resolve_start(raw: str | None) -> dt.date:
    if raw is None:
        from backend.utils.trading_hours import SHANGHAI

        return dt.datetime.now(SHANGHAI).date()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid --start date: {raw!r} ({exc})") from exc


def _resolve_out(raw: str | None, run_trade_date: str) -> Path:
    if raw is not None:
        return Path(raw)
    return Path(_DEFAULT_OUT_DIR) / f"{run_trade_date}_double_line.json"


async def _amain(args: argparse.Namespace) -> int:
    start = _resolve_start(args.start)
    try:
        ctx = await build_real_context(start_date=start)
    except Exception:  # noqa: BLE001 — frame assembly / config load failure
        print("dry_run_double_line verdict: FAIL", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1

    out_path = _resolve_out(args.out, ctx.run_trade_date)
    try:
        outcome = await run_dry_run(ctx, start_date=start, out_path=out_path)
    except Exception:  # noqa: BLE001 — any unexpected tick-loop failure
        print("dry_run_double_line verdict: FAIL", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1

    print(format_json(outcome) if args.json else format_table(outcome))
    return 0 if outcome.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":  # pragma: no cover — exercised via tests
    raise SystemExit(main())
