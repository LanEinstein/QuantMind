"""O-004 off-market briefing injection into the debate prompts.

Locks:
* the off-market block is appended to the analyst + fund_manager prompts
  when ctx.off_market_context is set, and absent (bit-identical) when empty;
* the injected text actually reaches the LLM call (captured user_content);
* it is labelled as background, never an instruction — and the LLM still
  writes only the four allowed text fields (direction comes from the
  fund_manager JSON envelope, not the off-market text).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from backend.agents_team.agents import (
    _analyst_context,
    _fund_manager_context,
    fund_manager_node,
    fundamental_analyst_node,
)
from backend.agents_team.state import TeamContext, TeamState

_BRIEF = "【板块推演】半导体 score +0.60 (uncalibrated) — 出口管制→国产替代"


def _state() -> TeamState:
    return {
        "candidate_code": "600001",
        "candidate_name": "测试股",
        "proposed_volume": 200,
        "proposed_limit_price": 4.5,
        "fundamental_report": "F",
        "technical_report": "T",
        "risk_officer_report": "R",
        "debate_history": "D",
    }


class TestContextBuilders:
    def test_analyst_context_appends_block(self, buy_context: TeamContext) -> None:
        ctx = dataclasses.replace(buy_context, off_market_context=_BRIEF)
        text = _analyst_context(_state(), ctx)
        assert "场外信息" in text
        assert _BRIEF in text
        assert "不得据此直接写买卖方向" in text

    def test_analyst_context_empty_is_bit_identical(
        self, buy_context: TeamContext
    ) -> None:
        empty = dataclasses.replace(buy_context, off_market_context="")
        whitespace = dataclasses.replace(buy_context, off_market_context="  \n ")
        baseline = _analyst_context(_state(), empty)
        assert "场外信息" not in baseline
        # Whitespace-only is also treated as absent.
        assert _analyst_context(_state(), whitespace) == baseline

    def test_fund_manager_context_appends_block(
        self, buy_context: TeamContext
    ) -> None:
        ctx = dataclasses.replace(buy_context, off_market_context=_BRIEF)
        text = _fund_manager_context(_state(), ctx)
        assert _BRIEF in text
        assert "场外信息" in text


class _CapturingRouter:
    """Records the user_content passed to complete()."""

    def __init__(self) -> None:
        self.user_contents: list[str] = []

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        self.user_contents.append(messages[-1]["content"])

        class _M:
            content = f"[{agent_name}] 报告"

        class _C:
            message = _M()

        class _Comp:
            choices = [_C()]

        return _Comp()


class TestReachesLlm:
    @pytest.mark.asyncio
    async def test_off_market_text_in_llm_call(
        self, buy_context: TeamContext
    ) -> None:
        router = _CapturingRouter()
        ctx = dataclasses.replace(
            buy_context, off_market_context=_BRIEF, llm_router=router
        )
        await fundamental_analyst_node(_state(), ctx)
        assert router.user_contents
        assert _BRIEF in router.user_contents[0]

    @pytest.mark.asyncio
    async def test_off_market_absent_when_empty(
        self, buy_context: TeamContext
    ) -> None:
        router = _CapturingRouter()
        ctx = dataclasses.replace(
            buy_context, off_market_context="", llm_router=router
        )
        await fundamental_analyst_node(_state(), ctx)
        assert "场外信息" not in router.user_contents[0]

    @pytest.mark.asyncio
    async def test_off_market_does_not_change_direction_source(
        self, buy_context: TeamContext
    ) -> None:
        # The off-market block is background only; the direction still comes
        # from the fund_manager JSON envelope (here the fake proposes 买入).
        ctx = dataclasses.replace(buy_context, off_market_context=_BRIEF)
        out = await fund_manager_node(_state(), ctx)
        assert out["direction"] == "BUY"
        assert out["fund_manager_parse_ok"] is True
        # The node writes only text + direction — never a numeric order field.
        assert set(out) <= {
            "fund_manager_reasoning",
            "direction",
            "fund_manager_parse_ok",
        }
