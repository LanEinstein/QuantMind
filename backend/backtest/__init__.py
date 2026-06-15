"""backend/backtest/ — deterministic, offline backtest harness (AE-003/AE-004).

Governing decision: ``P2-2-amendment-2026-06-14-deterministic-backtest-harness``.

The quant-parameter evolution loop needs a *trustworthy* engine that, given a
parameter set + a PIT window, replays a deterministic strategy day-by-day and
produces an equity curve. Per codex's headline finding, the engine must first
prove it is **same-source** as the live MockBroker before any statistical gate
is added — that proof is the **golden replay** (AE-003, this module's first
component): replay a real recorded trading day and assert the reconstructed
equity curve matches the live record exactly.

Module red lines (amendment §2.1 / §4 red line 1, enforced by redline
``[BACKTEST]`` + ``test_module_contract``):

* **Import allowlist** — may import ``backend.candidate_selector`` /
  ``backend.slot_portfolio`` / ``backend.monitoring`` /
  ``backend.marketdata_snapshot`` / ``backend.strategy_evolution.harsh_fill_model``
  / ``backend.services.acceptance_report`` / ``backend.utils`` / ``backend.models``.
* **Forbidden** — ``backend.{llm,agents,agents_team,mirofish}`` (P1 replays no
  LLM) and ``backend.{api,broker}`` (harsh-fill is broker-free; the live mirror
  is never touched).
* **Zero LLM, test-time / offline only, never on the realtime path.**
* All decisions go through the fixed-point :mod:`backend.utils.decision_compare`
  so a replay is deterministic across numpy versions (NEP 50).
"""

from __future__ import annotations

from backend.backtest.event_loop import (
    BacktestClock,
    BarSource,
    ClockViolationError,
    DayBar,
)
from backend.backtest.friction import (
    FillEconomics,
    FrictionError,
    FrictionParams,
    compute_fill_economics,
)
from backend.backtest.golden_replay import (
    GoldenReplayResult,
    ReplayDay,
    ReplayEquityPoint,
    ReplayFill,
    ReplayPosition,
    assert_conservation,
    compare_to_golden,
    replay_equity_curve,
)
from backend.backtest.golden_vector import (
    DecisionVector,
    GoldenVectorResult,
    VectorDivergence,
    verify_decision_vectors,
)
from backend.backtest.harness import (
    BacktestResult,
    BacktestSpec,
    run_backtest,
    to_acceptance_report,
)
from backend.backtest.invariants import (
    ExposureObservation,
    InvariantReport,
    InvariantVerdict,
    InvariantViolation,
    check_invariants,
)
from backend.backtest.portfolio import (
    AppliedFill,
    BacktestPortfolio,
    EquitySnapshot,
    OpeningLot,
    PortfolioError,
    PositionMark,
)
from backend.backtest.strategy import (
    CodeHealth,
    DailySignals,
    DayDecision,
    HeldPosition,
    OrderIntent,
    PortfolioView,
    ScoreProvider,
    StrategyConfig,
    decide_day,
)

__all__ = [
    "AppliedFill",
    "BacktestClock",
    "BacktestPortfolio",
    "BacktestResult",
    "BacktestSpec",
    "BarSource",
    "ClockViolationError",
    "CodeHealth",
    "DailySignals",
    "DayBar",
    "DayDecision",
    "DecisionVector",
    "EquitySnapshot",
    "ExposureObservation",
    "FillEconomics",
    "FrictionError",
    "FrictionParams",
    "GoldenReplayResult",
    "GoldenVectorResult",
    "HeldPosition",
    "InvariantReport",
    "InvariantVerdict",
    "InvariantViolation",
    "OpeningLot",
    "OrderIntent",
    "PortfolioError",
    "PortfolioView",
    "PositionMark",
    "ReplayDay",
    "ReplayEquityPoint",
    "ReplayFill",
    "ReplayPosition",
    "ScoreProvider",
    "StrategyConfig",
    "VectorDivergence",
    "assert_conservation",
    "check_invariants",
    "compare_to_golden",
    "compute_fill_economics",
    "decide_day",
    "replay_equity_curve",
    "run_backtest",
    "to_acceptance_report",
    "verify_decision_vectors",
]
