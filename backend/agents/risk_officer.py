"""Risk officer agent: evaluates portfolio risk and recommends position sizing."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import RISK_OFFICER_PROMPT

log = structlog.get_logger(component="agent.risk_officer")


async def risk_officer_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Evaluate risk based on all reports and debate transcript.

    Returns:
        Dict with 'risk_assessment' key for state update.
    """
    debate = state["debate_state"]
    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}\n\n"
        f"=== 多空辩论记录 ===\n{debate['history']}"
    )
    assessment = await call_agent(
        services.llm_router,
        "risk_officer",
        RISK_OFFICER_PROMPT,
        user_content,
    )
    return {"risk_assessment": assessment}
