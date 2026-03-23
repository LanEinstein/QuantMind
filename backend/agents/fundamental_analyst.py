"""Fundamental analyst agent: analyzes financial metrics and valuation."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import FUNDAMENTAL_ANALYST_PROMPT

log = structlog.get_logger(component="agent.fundamental_analyst")


async def fundamental_analyst_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Fetch financial data and analyze fundamentals via Qwen.

    Returns:
        Dict with 'fundamental_report' key for state update.
    """
    stock_code = state["stock_code"]
    data_parts: list[str] = []

    try:
        fin = await services.history_data.get_financial_data(stock_code)
        data_parts.append(
            f"财务指标:\n"
            f"  PE: {fin.pe_ratio}\n"
            f"  PB: {fin.pb_ratio}\n"
            f"  ROE: {fin.roe}\n"
            f"  EPS: {fin.eps}\n"
            f"  营收增长率: {fin.revenue_growth}\n"
            f"  报告期: {fin.report_date}"
        )
    except Exception as exc:
        log.warning("financial_data_failed", error=str(exc))
        data_parts.append("财务数据获取失败，请基于已有信息分析")

    try:
        quote = await services.market_data.get_stock_realtime(stock_code)
        data_parts.append(
            f"\n当前行情:\n"
            f"  价格: {quote.price}\n"
            f"  涨跌幅: {quote.change_pct}%\n"
            f"  成交量: {quote.volume}\n"
            f"  成交额: {quote.amount}"
        )
    except Exception as exc:
        log.warning("quote_fetch_failed", error=str(exc))

    user_content = (
        f"目标股票: {stock_code} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        + "\n".join(data_parts)
    )
    report = await call_agent(
        services.llm_router,
        "fundamental_analyst",
        FUNDAMENTAL_ANALYST_PROMPT,
        user_content,
    )
    return {"fundamental_report": report}
