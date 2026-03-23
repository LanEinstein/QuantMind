"""Technical analyst agent: analyzes K-line patterns and indicators."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import TECHNICAL_ANALYST_PROMPT

log = structlog.get_logger(component="agent.technical_analyst")


def _summarize_kline(df: Any) -> str:
    """Summarize K-line DataFrame into text for LLM consumption."""
    if df is None or df.empty:
        return "K线数据不可用"

    lines = ["近期K线数据 (最近20个交易日):"]
    recent = df.tail(20)
    for _, row in recent.iterrows():
        lines.append(
            f"  {row.get('date', '?')}: "
            f"开{row.get('open', 0):.2f} "
            f"高{row.get('high', 0):.2f} "
            f"低{row.get('low', 0):.2f} "
            f"收{row.get('close', 0):.2f} "
            f"量{row.get('volume', 0)}"
        )

    # Basic MA calculations
    if "close" in df.columns and len(df) >= 5:
        close = df["close"].astype(float)
        lines.append("\n均线系统:")
        lines.append(f"  MA5: {close.tail(5).mean():.2f}")
        if len(df) >= 20:
            lines.append(f"  MA20: {close.tail(20).mean():.2f}")
        if len(df) >= 60:
            lines.append(f"  MA60: {close.tail(60).mean():.2f}")
        lines.append(f"  近60日最高: {close.tail(60).max():.2f}")
        lines.append(f"  近60日最低: {close.tail(60).min():.2f}")

    return "\n".join(lines)


async def technical_analyst_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Fetch K-line data and analyze technicals via Qwen.

    Returns:
        Dict with 'technical_report' key for state update.
    """
    stock_code = state["stock_code"]
    try:
        df = await services.history_data.get_kline(
            stock_code, period="daily", adjust="qfq"
        )
        kline_text = _summarize_kline(df)
    except Exception as exc:
        log.warning("kline_fetch_failed", error=str(exc))
        kline_text = "K线数据获取失败"

    user_content = (
        f"目标股票: {stock_code} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"{kline_text}"
    )
    report = await call_agent(
        services.llm_router,
        "technical_analyst",
        TECHNICAL_ANALYST_PROMPT,
        user_content,
    )
    return {"technical_report": report}
