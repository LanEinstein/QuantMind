"""T-002 — trader personas wired into the debate (advisory text only).

The ≥2 trader personas run as one deterministic fan-in node between the debate
and the fund_manager. They write ONLY the free-text ``trader_advice`` field;
the fund_manager stays the sole BUY/SELL/HOLD proposer and the builder derives
every numeric order field deterministically (R0 §4). The single-construction
-point adversarial (trader numbers never set ``volume``) lives in
``tests/test_instruction_plan_builder_assemble.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from backend.agents_team.agents import (
    _fund_manager_context,
    _persona_system_prompt,
    traders_node,
)
from backend.agents_team.graph import run_team
from backend.agents_team.persona_registry import TraderPersona
from backend.agents_team.state import CandidateBrief, TeamContext, TeamState
from tests.agents_team.conftest import FakeRouter


def _persona(pid: str, **over: object) -> TraderPersona:
    base = {
        "persona_id": pid,
        "version": "v1",
        "sha256": "0" * 64,
        "identity": f"你是 {pid}",
        "mandate": "给 fund_manager 建议文本",
        "output_contract": "输出中文自由文本; 严禁 side/volume/price",
        "exemplars": (),
        "content": f"persona {pid}",
    }
    base.update(over)
    return TraderPersona(**base)  # type: ignore[arg-type]


_MOMENTUM = _persona("trader_momentum")
_MEAN_REV = _persona("trader_mean_reversion")
_BOTH = (_MOMENTUM, _MEAN_REV)


def _with_personas(
    ctx: TeamContext, personas: tuple[TraderPersona, ...], router: FakeRouter
) -> TeamContext:
    return dataclasses.replace(ctx, trader_personas=personas, llm_router=router)


def _state() -> TeamState:
    return {
        "candidate_code": "510300",
        "candidate_name": "沪深300 ETF",
        "fundamental_report": "FA",
        "technical_report": "TA",
        "risk_officer_report": "RO",
        "debate_history": "debate",
    }


# ---------------------------------------------------------------------------
# traders_node unit behaviour
# ---------------------------------------------------------------------------


class TestTradersNode:
    @pytest.mark.asyncio
    async def test_aggregates_both_personas(self, buy_context: TeamContext) -> None:
        router = FakeRouter(action="买入")
        ctx = _with_personas(buy_context, _BOTH, router)
        out = await traders_node(_state(), ctx)
        advice = out["trader_advice"]
        assert "trader_momentum" in advice
        assert "trader_mean_reversion" in advice
        assert "trader_momentum" in router.calls
        assert "trader_mean_reversion" in router.calls

    @pytest.mark.asyncio
    async def test_empty_personas_is_noop(self, buy_context: TeamContext) -> None:
        router = FakeRouter(action="买入")
        ctx = _with_personas(buy_context, (), router)
        out = await traders_node(_state(), ctx)
        assert out == {"trader_advice": ""}
        assert "trader_momentum" not in router.calls

    @pytest.mark.asyncio
    async def test_writes_only_trader_advice_key(
        self, buy_context: TeamContext
    ) -> None:
        """R0 §4: the node never writes a direction or numeric order field."""
        ctx = _with_personas(buy_context, _BOTH, FakeRouter(action="买入"))
        out = await traders_node(_state(), ctx)
        assert set(out.keys()) == {"trader_advice"}

    @pytest.mark.asyncio
    async def test_fail_closed_drops_failed_persona(
        self, buy_context: TeamContext
    ) -> None:
        router = FakeRouter(
            action="买入", fail_agents=frozenset({"trader_momentum"})
        )
        ctx = _with_personas(buy_context, _BOTH, router)
        out = await traders_node(_state(), ctx)
        advice = out["trader_advice"]
        assert "trader_momentum" not in advice  # failed → dropped, no raise
        assert "trader_mean_reversion" in advice

    @pytest.mark.asyncio
    async def test_all_failed_yields_empty_advice(
        self, buy_context: TeamContext
    ) -> None:
        router = FakeRouter(
            action="买入",
            fail_agents=frozenset({"trader_momentum", "trader_mean_reversion"}),
        )
        ctx = _with_personas(buy_context, _BOTH, router)
        out = await traders_node(_state(), ctx)
        assert out == {"trader_advice": ""}

    @pytest.mark.asyncio
    async def test_no_router_is_fail_closed(self, buy_context: TeamContext) -> None:
        ctx = dataclasses.replace(
            buy_context, trader_personas=_BOTH, llm_router=None
        )
        out = await traders_node(_state(), ctx)
        assert out == {"trader_advice": ""}

    @pytest.mark.asyncio
    async def test_deterministic_persona_order(
        self, buy_context: TeamContext
    ) -> None:
        """Personas are emitted sorted by id regardless of injection order."""
        router = FakeRouter(action="买入")
        ctx = _with_personas(buy_context, (_MOMENTUM, _MEAN_REV), router)
        a = (await traders_node(_state(), ctx))["trader_advice"]
        ctx2 = _with_personas(
            buy_context, (_MEAN_REV, _MOMENTUM), FakeRouter(action="买入")
        )
        b = (await traders_node(_state(), ctx2))["trader_advice"]
        assert a == b
        assert a.index("trader_mean_reversion") < a.index("trader_momentum")


# ---------------------------------------------------------------------------
# Persona prompt rendering + fund_manager context
# ---------------------------------------------------------------------------


class TestPersonaPrompt:
    @pytest.mark.unit
    def test_system_prompt_includes_skeleton(self) -> None:
        prompt = _persona_system_prompt(_MOMENTUM)
        assert "trader_momentum" in prompt
        assert "给 fund_manager 建议文本" in prompt
        assert "严禁 side/volume/price" in prompt

    @pytest.mark.unit
    def test_system_prompt_includes_exemplars(self) -> None:
        p = _persona("trader_momentum", exemplars=("示范一", "示范二"))
        prompt = _persona_system_prompt(p)
        assert "示范一" in prompt
        assert "示范二" in prompt

    @pytest.mark.unit
    def test_fund_manager_context_includes_trader_advice(
        self, buy_context: TeamContext
    ) -> None:
        state = {**_state(), "trader_advice": "动量交易员: 回踩后进场"}
        ctx_text = _fund_manager_context(state, buy_context)
        assert "动量交易员: 回踩后进场" in ctx_text
        assert "交易员建议" in ctx_text

    @pytest.mark.unit
    def test_fund_manager_context_omits_empty_trader_advice(
        self, buy_context: TeamContext
    ) -> None:
        state = {**_state(), "trader_advice": ""}
        ctx_text = _fund_manager_context(state, buy_context)
        assert "交易员建议" not in ctx_text


# ---------------------------------------------------------------------------
# Full graph integration
# ---------------------------------------------------------------------------


class TestGraphWithTraders:
    @pytest.mark.asyncio
    async def test_six_calls_with_two_personas(
        self, buy_context: TeamContext, candidate: CandidateBrief
    ) -> None:
        """3 analysts + 2 traders + fund_manager = 6 LLM calls."""
        router = FakeRouter(action="买入")
        ctx = _with_personas(buy_context, _BOTH, router)
        await run_team(ctx, candidate)
        assert sorted(router.calls) == [
            "fund_manager",
            "fundamental_analyst",
            "risk_officer",
            "technical_analyst",
            "trader_mean_reversion",
            "trader_momentum",
        ]

    @pytest.mark.asyncio
    async def test_traders_do_not_change_build_ok(
        self, buy_context: TeamContext, candidate: CandidateBrief
    ) -> None:
        """Adding traders keeps the BUY path intact (advice is advisory only)."""
        router = FakeRouter(action="买入")
        ctx = _with_personas(buy_context, _BOTH, router)
        result = await run_team(ctx, candidate)
        assert result["direction"] == "BUY"
        assert result["trader_advice"]
