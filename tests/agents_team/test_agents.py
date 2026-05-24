"""agents_team real LLM agent-node tests (Phase M-003).

Covers the four mandatory agents + single-round debate driven by the
injected :class:`LLMCompleter` fake:

* each analyst node turns the router response into its report field;
* fail-closed: missing router / provider exception / empty content →
  empty report (which the builder turns into a HOLD);
* fund_manager is the sole direction proposer; JSON envelope → routable
  direction + parse_ok; parse failure → forced HOLD + parse_ok=False;
* the debate fan-in is deterministic (no LLM call) and sets round_count≥1;
* ``to_fund_manager_output`` produces ONLY the LLM-writable bridge
  (side / proposal_text / parse_ok); an unroutable direction maps to HOLD;
* the agents never write tool-output keys (decision-path isolation).
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from backend.agents_team.agents import (
    _parse_fund_manager,
    debate_node,
    fund_manager_node,
    fundamental_analyst_node,
    risk_officer_node,
    technical_analyst_node,
    to_fund_manager_output,
)
from backend.agents_team.state import TeamContext, TeamState
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.models.instruction import InstructionSide
from backend.risk.engine import RiskEngine
from tests.agents_team.conftest import FakeRouter

_TOOL_OUTPUT_KEYS = {
    "risk_passed", "risk_rule", "risk_message", "decision", "decision_reason",
}
_AGENT_OUTPUT_KEYS = {
    "fundamental_report", "technical_report", "risk_officer_report",
    "fund_manager_reasoning", "debate_history", "debate_round_count",
    "direction", "fund_manager_parse_ok",
}


def _state(**overrides) -> TeamState:
    base: TeamState = {
        "candidate_code": "510300",
        "candidate_name": "沪深300 ETF",
        "proposed_volume": 200,
        "proposed_limit_price": 4.5,
        "fundamental_report": "f",
        "technical_report": "t",
        "risk_officer_report": "r",
        "fund_manager_reasoning": "",
        "debate_history": "",
        "debate_round_count": 0,
        "direction": "",
        "fund_manager_parse_ok": False,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# --------------------------------------------------------------------------
# Analyst nodes — turn router output into report fields
# --------------------------------------------------------------------------


def test_analyst_nodes_write_reports(buy_context: TeamContext) -> None:
    for node, key in (
        (fundamental_analyst_node, "fundamental_report"),
        (technical_analyst_node, "technical_report"),
        (risk_officer_node, "risk_officer_report"),
    ):
        out = asyncio.run(node(_state(), buy_context))
        assert set(out).issubset(_AGENT_OUTPUT_KEYS)
        assert not (set(out) & _TOOL_OUTPUT_KEYS)
        assert out[key], f"{node.__name__} produced empty report"


def test_analyst_fail_closed_without_router(buy_context: TeamContext) -> None:
    ctx = dataclasses.replace(buy_context, llm_router=None)
    out = asyncio.run(fundamental_analyst_node(_state(), ctx))
    assert out["fundamental_report"] == ""


def test_analyst_fail_closed_on_provider_exception(buy_context: TeamContext) -> None:
    ctx = dataclasses.replace(
        buy_context, llm_router=FakeRouter(fail_agents=frozenset({"technical_analyst"}))
    )
    out = asyncio.run(technical_analyst_node(_state(), ctx))
    assert out["technical_report"] == ""


def test_analyst_fail_closed_on_empty_content(buy_context: TeamContext) -> None:
    ctx = dataclasses.replace(
        buy_context, llm_router=FakeRouter(empty_agents=frozenset({"risk_officer"}))
    )
    out = asyncio.run(risk_officer_node(_state(), ctx))
    assert out["risk_officer_report"] == ""


def test_analyst_fail_closed_on_no_choices(buy_context: TeamContext) -> None:
    ctx = dataclasses.replace(
        buy_context,
        llm_router=FakeRouter(no_choices_agents=frozenset({"fundamental_analyst"})),
    )
    out = asyncio.run(fundamental_analyst_node(_state(), ctx))
    assert out["fundamental_report"] == ""


def test_analyst_fail_closed_on_none_content(buy_context: TeamContext) -> None:
    ctx = dataclasses.replace(
        buy_context,
        llm_router=FakeRouter(none_content_agents=frozenset({"technical_analyst"})),
    )
    out = asyncio.run(technical_analyst_node(_state(), ctx))
    assert out["technical_report"] == ""


def test_analyst_fail_closed_on_whitespace_only(buy_context: TeamContext) -> None:
    """A whitespace-only completion must fail closed (codex M-003 P2): a
    truthy '   ' report would otherwise satisfy the mandatory-agent gate."""
    ctx = dataclasses.replace(
        buy_context,
        llm_router=FakeRouter(whitespace_agents=frozenset({"fundamental_analyst"})),
    )
    out = asyncio.run(fundamental_analyst_node(_state(), ctx))
    assert out["fundamental_report"] == ""


# --------------------------------------------------------------------------
# debate — deterministic single round, no LLM call
# --------------------------------------------------------------------------


def test_debate_is_deterministic_single_round(buy_context: TeamContext) -> None:
    router = buy_context.llm_router
    assert isinstance(router, FakeRouter)
    out = asyncio.run(debate_node(_state(), buy_context))
    assert out["debate_round_count"] == 1
    assert out["debate_history"]
    # The debate fan-in must NOT call the LLM (keeps MVP at 4 calls).
    assert router.calls == []


def test_debate_never_writes_direction(buy_context: TeamContext) -> None:
    out = asyncio.run(debate_node(_state(), buy_context))
    assert "direction" not in out


# --------------------------------------------------------------------------
# fund_manager — sole proposer + JSON parse + fail-closed HOLD
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [("买入", "BUY"), ("卖出", "SELL"), ("持有", "HOLD")],
)
def test_fund_manager_parses_direction(action, expected) -> None:
    out = asyncio.run(_run_fm(_state(), FakeRouter(action=action)))
    assert out["direction"] == expected
    assert out["fund_manager_parse_ok"] is True
    assert out["fund_manager_reasoning"]


async def _run_fm(state: TeamState, router: FakeRouter) -> dict:
    """Run fund_manager_node with a minimal ctx (only the router matters)."""
    ctx = TeamContext(
        risk_engine=RiskEngine(
            RiskConfig(
                position_limits=PositionLimitsConfig(),
                stop_loss=StopLossConfig(),
                circuit_breaker=CircuitBreakerConfig(),
                universe=UniverseConfig(),
            )
        ),
        account=AccountInfo(
            total_assets=1.0, available_cash=1.0, frozen_cash=0.0,
            market_value=0.0, total_pnl=0.0, total_pnl_pct=0.0,
            initial_capital=1.0,
        ),
        llm_router=router,
    )
    return await fund_manager_node(state, ctx)


def test_fund_manager_bad_json_forces_hold() -> None:
    out = asyncio.run(_run_fm(_state(), FakeRouter(bad_fund_manager_json=True)))
    assert out["direction"] == "HOLD"
    assert out["fund_manager_parse_ok"] is False
    assert out["fund_manager_reasoning"]


def test_fund_manager_empty_response_forces_hold() -> None:
    out = asyncio.run(
        _run_fm(_state(), FakeRouter(empty_agents=frozenset({"fund_manager"})))
    )
    assert out["direction"] == "HOLD"
    assert out["fund_manager_parse_ok"] is False


def test_fund_manager_is_sole_direction_proposer(buy_context: TeamContext) -> None:
    for node in (
        fundamental_analyst_node, technical_analyst_node, risk_officer_node,
    ):
        out = asyncio.run(node(_state(), buy_context))
        assert "direction" not in out, f"{node.__name__} wrote direction"
    fm = asyncio.run(fund_manager_node(_state(), buy_context))
    assert fm["direction"] == "BUY"


# --------------------------------------------------------------------------
# _parse_fund_manager unit coverage
# --------------------------------------------------------------------------


def test_parse_unknown_action_forces_hold() -> None:
    direction, _reasoning, ok = _parse_fund_manager('{"action": "做多", "x": 1}')
    assert direction == "HOLD"
    assert ok is False


def test_parse_uses_reasoning_field() -> None:
    raw = '{"action": "买入", "reasoning": "估值低估"}'
    direction, reasoning, ok = _parse_fund_manager(raw)
    assert direction == "BUY"
    assert reasoning == "估值低估"
    assert ok is True


def test_parse_falls_back_to_raw_when_reasoning_blank() -> None:
    raw = '{"action": "买入", "reasoning": ""} trailing context'
    direction, reasoning, ok = _parse_fund_manager(raw)
    assert direction == "BUY"
    assert reasoning  # non-empty (FundManagerOutput requires min_length=1)
    assert ok is True


# --------------------------------------------------------------------------
# to_fund_manager_output — the LLM-only bridge
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "side"),
    [
        ("BUY", InstructionSide.BUY),
        ("SELL", InstructionSide.SELL),
        ("HOLD", InstructionSide.HOLD),
    ],
)
def test_bridge_maps_direction_to_side(direction, side) -> None:
    out = to_fund_manager_output(
        _state(
            direction=direction,
            fund_manager_reasoning="理由",
            fund_manager_parse_ok=True,
        )
    )
    assert out.side is side
    assert out.proposal_text == "理由"
    assert out.parse_ok is True


def test_bridge_unroutable_direction_maps_to_hold() -> None:
    out = to_fund_manager_output(
        _state(
            direction="MARGIN",
            fund_manager_reasoning="x",
            fund_manager_parse_ok=True,
        )
    )
    assert out.side is InstructionSide.HOLD


def test_bridge_blank_reasoning_gets_placeholder() -> None:
    out = to_fund_manager_output(
        _state(
            direction="HOLD",
            fund_manager_reasoning="",
            fund_manager_parse_ok=False,
        )
    )
    assert out.proposal_text  # min_length=1 satisfied
    assert out.parse_ok is False


def test_bridge_only_exposes_llm_writable_fields() -> None:
    out = to_fund_manager_output(
        _state(
            direction="BUY",
            fund_manager_reasoning="r",
            fund_manager_parse_ok=True,
        )
    )
    assert set(type(out).model_fields) == {"side", "proposal_text", "parse_ok"}
