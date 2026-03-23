"""Extract high-importance financial events from news reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.prompts import EVENT_EXTRACTION_PROMPT
from backend.mirofish.report_parser import extract_deep_json
from backend.mirofish.schemas import EventDescription

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.event_filter")


async def extract_key_events(
    router: LLMRouter,
    news_report: str,
    stock_code: str,
    stock_name: str,
) -> tuple[EventDescription, ...]:
    """Extract structured events from a news analysis report.

    Uses DeepSeek (agent_name="news_crawler") to parse the news
    report and identify events with importance scores.

    Args:
        router: LLM router for DeepSeek calls.
        news_report: Text of the news analysis report.
        stock_code: Target stock code.
        stock_name: Target stock name.

    Returns:
        Tuple of EventDescription objects. Empty tuple on failure.
    """
    if not news_report or not news_report.strip():
        return ()

    # Skip error strings from failed agent calls
    stripped = news_report.strip()
    if stripped.startswith("[") and "error" in stripped.lower():
        log.info("skipping_error_news_report")
        return ()

    user_content = (
        f"目标股票: {stock_code} {stock_name}\n\n"
        f"新闻分析报告:\n{news_report}"
    )

    try:
        raw = await call_agent(
            router,
            "news_crawler",
            EVENT_EXTRACTION_PROMPT,
            user_content,
        )
    except Exception as exc:
        log.warning("event_extraction_llm_failed", error=str(exc))
        return ()

    data = extract_deep_json(raw)
    if data is None:
        log.warning("event_extraction_parse_failed")
        return ()

    events_raw = data.get("events", [])
    if not isinstance(events_raw, list):
        return ()

    events: list[EventDescription] = []
    for item in events_raw:
        if not isinstance(item, dict):
            continue
        try:
            events.append(
                EventDescription(
                    title=str(item.get("title", "")),
                    content=str(item.get("content", "")),
                    importance_score=int(item.get("importance_score", 0)),
                    sectors=tuple(item.get("sectors", ())),
                    stocks=tuple(item.get("stocks", ())),
                )
            )
        except Exception as exc:
            log.warning("event_parse_failed", error=str(exc))
            continue

    log.info("events_extracted", count=len(events))
    return tuple(events)
