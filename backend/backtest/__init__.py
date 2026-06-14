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

__all__ = [
    "GoldenReplayResult",
    "ReplayDay",
    "ReplayEquityPoint",
    "ReplayFill",
    "ReplayPosition",
    "assert_conservation",
    "compare_to_golden",
    "replay_equity_curve",
]
