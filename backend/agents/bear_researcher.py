"""Bear researcher agent: builds bearish investment thesis."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState, DebateState
from backend.agents.prompts import BEAR_RESEARCHER_PROMPT

log = structlog.get_logger(component="agent.bear_researcher")


async def bear_researcher_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Build a bearish argument based on all reports and debate history.

    Updates debate_state with new bear argument and incremented count.

    Returns:
        Dict with 'debate_state' key for state update.
    """
    debate = state["debate_state"]

    reports_context = (
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}"
    )

    debate_context = ""
    if debate["bull_history"]:
        debate_context = (
            f"\n\n=== 看多研究员论点（你需要反驳）===\n"
            f"{debate['bull_history']}"
        )

    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"{reports_context}{debate_context}"
    )
    argument = await call_agent(
        services.llm_router,
        "bear_researcher",
        BEAR_RESEARCHER_PROMPT,
        user_content,
    )

    new_debate: DebateState = {
        "history": debate["history"] + f"\n\n【看空研究员】\n{argument}",
        "bull_history": debate["bull_history"],
        "bear_history": debate["bear_history"] + f"\n{argument}",
        "current_response": f"Bear: {argument}",
        "count": debate["count"] + 1,
    }
    return {"debate_state": new_debate}
