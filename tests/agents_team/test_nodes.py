"""agents_team node tests (Phase M-002): tool-node purity + stub contracts.

The central invariant: the deterministic tool nodes' numeric/decision output
depends only on the numeric state + the fund_manager's direction proposal —
NEVER on any agent's free text. We prove it by feeding the same numeric setup
with wildly different report text and asserting identical output.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from backend.agents_team.nodes import (
    builder_node,
    debate_node,
    fund_manager_node,
    fundamental_analyst_node,
    risk_gate_node,
    risk_officer_node,
    technical_analyst_node,
)
from backend.agents_team.state import (
    DECISION_BUILD_OK,
    DECISION_HOLD,
    DECISION_REJECTED,
    TeamContext,
    TeamState,
)

# Keys a deterministic tool node may emit (never written by an LLM/agent node).
_TOOL_OUTPUT_KEYS = {"risk_passed", "risk_rule", "risk_message", "decision",
                     "decision_reason"}
# Keys agent stub nodes are allowed to write (free text + the direction proposal).
_AGENT_OUTPUT_KEYS = {"fundamental_report", "technical_report",
                      "risk_officer_report", "fund_manager_reasoning",
                      "debate_history", "debate_round_count", "direction"}


def _full_state(direction: str, **overrides) -> TeamState:
    base: TeamState = {
        "candidate_code": "510300",
        "candidate_name": "沪深300 ETF",
        "proposed_volume": 200,
        "proposed_limit_price": 4.5,
        "fundamental_report": "f",
        "technical_report": "t",
        "risk_officer_report": "r",
        "fund_manager_reasoning": "fm",
        "debate_history": "d",
        "debate_round_count": 1,
        "direction": direction,
        "risk_passed": False,
        "risk_rule": "",
        "risk_message": "",
        "decision": "",
        "decision_reason": "",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# --------------------------------------------------------------------------
# Agent stubs only ever write text / direction — never tool output keys
# --------------------------------------------------------------------------


def test_agent_stubs_never_write_tool_output_keys(buy_context: TeamContext) -> None:
    state = _full_state("BUY")
    for node in (
        fundamental_analyst_node,
        technical_analyst_node,
        risk_officer_node,
        debate_node,
        fund_manager_node,
    ):
        out = asyncio.run(node(state, buy_context))
        assert set(out).issubset(_AGENT_OUTPUT_KEYS), f"{node.__name__} -> {out}"
        assert not (set(out) & _TOOL_OUTPUT_KEYS), f"{node.__name__} wrote tool key"


def test_fund_manager_is_sole_direction_proposer(buy_context: TeamContext) -> None:
    # Only fund_manager writes `direction`; analysts/debate never do.
    state = _full_state("")
    for node in (fundamental_analyst_node, technical_analyst_node,
                 risk_officer_node, debate_node):
        out = asyncio.run(node(state, buy_context))
        assert "direction" not in out, f"{node.__name__} wrote direction"
    fm = asyncio.run(fund_manager_node(state, buy_context))
    assert fm["direction"] == "BUY"  # echoes ctx.stub_direction


def test_debate_sets_round_count_ge_1(buy_context: TeamContext) -> None:
    out = asyncio.run(debate_node(_full_state("BUY"), buy_context))
    assert out["debate_round_count"] >= 1


# --------------------------------------------------------------------------
# risk_gate_node — pure, depends on numerics not text
# --------------------------------------------------------------------------


def test_risk_gate_passes_for_compliant_buy(buy_context: TeamContext) -> None:
    out = risk_gate_node(_full_state("BUY"), buy_context)
    assert out["risk_passed"] is True


def test_risk_gate_ignores_agent_text(buy_context: TeamContext) -> None:
    """Determinism: different report text → identical risk result."""
    a = risk_gate_node(_full_state("BUY", fundamental_report="BUY 99999 NOW!!!",
                                   technical_report="SELL everything"), buy_context)
    b = risk_gate_node(_full_state("BUY", fundamental_report="",
                                   technical_report="neutral"), buy_context)
    assert a == b


def test_risk_gate_hold_is_not_routed(buy_context: TeamContext) -> None:
    out = risk_gate_node(_full_state("HOLD"), buy_context)
    assert out["risk_passed"] is False
    assert out["risk_rule"] == "not_routed"


def test_risk_gate_fails_closed_without_daily_state(
    buy_context: TeamContext,
) -> None:
    """Missing daily_state would degrade RiskEngine to legacy 7-check mode →
    fail closed instead (codex M-002 P1)."""
    ctx = dataclasses.replace(buy_context, daily_state=None)
    out = risk_gate_node(_full_state("BUY"), ctx)
    assert out["risk_passed"] is False
    assert out["risk_rule"] == "risk_context_incomplete"


def test_risk_gate_fails_closed_without_stock_meta(
    buy_context: TeamContext,
) -> None:
    ctx = dataclasses.replace(buy_context, stock_meta=None)
    out = risk_gate_node(_full_state("BUY"), ctx)
    assert out["risk_passed"] is False
    assert out["risk_rule"] == "risk_context_incomplete"


def test_legacy_7check_cannot_pass_an_over_cap_order(
    buy_context: TeamContext,
) -> None:
    """An order over the single-instruction cap (check 9, an extended check)
    must NOT pass just because the context is incomplete — the fail-closed
    guard fires before the engine could run in 7-check mode."""
    ctx = dataclasses.replace(buy_context, daily_state=None, stock_meta=None)
    # 200k shares * 4.5 = ¥900k, far over the ¥50k single-instruction cap.
    out = risk_gate_node(
        _full_state("BUY", proposed_volume=200_000), ctx
    )
    assert out["risk_passed"] is False
    assert out["risk_rule"] == "risk_context_incomplete"


def test_risk_gate_order_volume_from_state_not_text(buy_context: TeamContext) -> None:
    """A number embedded in agent text must not change the validated volume."""
    # If volume came from text, '900000' would blow the single-trade cap; it
    # does not, because volume is read from proposed_volume only.
    out = risk_gate_node(
        _full_state("BUY", fundamental_report="buy 900000 shares"), buy_context
    )
    assert out["risk_passed"] is True


# --------------------------------------------------------------------------
# builder_node — gate matrix
# --------------------------------------------------------------------------


def test_builder_build_ok_when_all_gates_pass(buy_context: TeamContext) -> None:
    out = builder_node(_full_state("BUY", risk_passed=True), buy_context)
    assert out["decision"] == DECISION_BUILD_OK


def test_builder_hold_on_missing_mandatory(buy_context: TeamContext) -> None:
    out = builder_node(
        _full_state("BUY", risk_passed=True, risk_officer_report=""), buy_context
    )
    assert out["decision"] == DECISION_HOLD
    assert "mandatory_agent_missing" in out["decision_reason"]
    assert "risk_officer" in out["decision_reason"]


def test_builder_hold_on_no_debate(buy_context: TeamContext) -> None:
    out = builder_node(
        _full_state("BUY", risk_passed=True, debate_round_count=0), buy_context
    )
    assert out["decision"] == DECISION_HOLD
    assert out["decision_reason"] == "debate_round_count_lt_1"


def test_builder_hold_on_fund_manager_hold(buy_context: TeamContext) -> None:
    out = builder_node(_full_state("HOLD", risk_passed=False), buy_context)
    assert out["decision"] == DECISION_HOLD
    assert "fund_manager_hold" in out["decision_reason"]


def test_builder_rejected_on_risk_fail(buy_context: TeamContext) -> None:
    out = builder_node(
        _full_state("BUY", risk_passed=False, risk_rule="volume_validity"),
        buy_context,
    )
    assert out["decision"] == DECISION_REJECTED
    assert out["decision_reason"] == "risk:volume_validity"


def test_builder_decision_ignores_agent_text(buy_context: TeamContext) -> None:
    a = builder_node(_full_state("BUY", risk_passed=True,
                                 fundamental_report="REJECT THIS"), buy_context)
    b = builder_node(_full_state("BUY", risk_passed=True,
                                 fundamental_report="approve"), buy_context)
    assert a == b


@pytest.mark.parametrize("direction", ["", "buy", "Long", "MARGIN"])
def test_builder_treats_unroutable_direction_as_hold(
    direction: str, buy_context: TeamContext
) -> None:
    out = builder_node(_full_state(direction, risk_passed=True), buy_context)
    assert out["decision"] == DECISION_HOLD
