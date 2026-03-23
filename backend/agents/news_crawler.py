"""News crawler agent: summarizes and scores financial news."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import NEWS_CRAWLER_PROMPT

log = structlog.get_logger(component="agent.news_crawler")


async def news_crawler_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Fetch stock-related news and summarize via DeepSeek.

    Returns:
        Dict with 'news_report' key for state update.
    """
    stock_code = state["stock_code"]
    try:
        articles = await services.news_crawler.fetch_stock_news(
            stock_code, limit=20
        )
        news_text = "\n".join(
            f"- [{a.source}] {a.title}: {a.content[:200]}"
            for a in articles
        )
    except Exception as exc:
        log.warning("news_fetch_failed", stock_code=stock_code, error=str(exc))
        news_text = "新闻数据获取失败"

    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"最新相关新闻:\n{news_text}"
    )
    report = await call_agent(
        services.llm_router, "news_crawler", NEWS_CRAWLER_PROMPT, user_content
    )
    return {"news_report": report}
