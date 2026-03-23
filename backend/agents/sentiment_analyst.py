"""Sentiment analyst agent: evaluates market emotion and investor mood."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import SENTIMENT_ANALYST_PROMPT

log = structlog.get_logger(component="agent.sentiment_analyst")


async def sentiment_analyst_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Analyze market sentiment from news and social indicators.

    Returns:
        Dict with 'sentiment_report' key for state update.
    """
    stock_code = state["stock_code"]
    try:
        general = await services.news_crawler.fetch_latest_news(limit=30)
        stock_news = await services.news_crawler.fetch_stock_news(
            stock_code, limit=20
        )
        all_news = general + stock_news
        news_text = "\n".join(
            f"- [{a.source}] {a.title}" for a in all_news[:40]
        )
    except Exception as exc:
        log.warning("sentiment_data_failed", error=str(exc))
        news_text = "新闻数据获取失败"

    user_content = (
        f"目标股票: {stock_code} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"近期新闻标题:\n{news_text}"
    )
    report = await call_agent(
        services.llm_router,
        "sentiment_analyst",
        SENTIMENT_ANALYST_PROMPT,
        user_content,
    )
    return {"sentiment_report": report}
