"""Fund manager agent: makes final trading decision and outputs TradingSignal."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent, extract_json_from_response
from backend.agents.models import AnalysisServices, AnalysisState, TradingSignal
from backend.agents.prompts import FUND_MANAGER_PROMPT

log = structlog.get_logger(component="agent.fund_manager")


def _parse_signal(
    raw: str, stock_code: str, stock_name: str, trade_date: str
) -> TradingSignal:
    """Parse LLM response into a TradingSignal, with fallback."""
    data = extract_json_from_response(raw)
    if data is not None:
        try:
            return TradingSignal(
                action=data.get("action", "持有"),
                target_price=data.get("target_price"),
                confidence=float(data.get("confidence", 0.5)),
                risk_score=float(data.get("risk_score", 0.5)),
                reasoning=data.get("reasoning", raw[:200]),
                stock_code=stock_code,
                stock_name=stock_name,
                trade_date=trade_date,
            )
        except Exception as exc:
            log.warning("signal_parse_validation_failed", error=str(exc))

    # Fallback: default hold signal with raw reasoning
    return TradingSignal(
        action="持有",
        confidence=0.5,
        risk_score=0.5,
        reasoning=raw[:500] if raw else "LLM response could not be parsed",
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
    )


async def fund_manager_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Synthesize all reports and produce final TradingSignal.

    Returns:
        Dict with 'trading_signal' key (serialized TradingSignal dict).
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
        f"=== 多空辩论记录 ===\n{debate['history']}\n\n"
        f"=== 风控评估 ===\n{state['risk_assessment']}"
    )
    raw_response = await call_agent(
        services.llm_router,
        "fund_manager",
        FUND_MANAGER_PROMPT,
        user_content,
    )
    signal = _parse_signal(
        raw_response,
        state["stock_code"],
        state["stock_name"],
        state["trade_date"],
    )
    return {"trading_signal": signal.model_dump()}
