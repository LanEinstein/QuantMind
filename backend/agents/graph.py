"""LangGraph state graph for the multi-agent analysis pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from backend.agents.bear_researcher import bear_researcher_node
from backend.agents.bull_researcher import bull_researcher_node
from backend.agents.fund_manager import fund_manager_node
from backend.agents.fundamental_analyst import fundamental_analyst_node
from backend.agents.intelligence_officer import intelligence_officer_node
from backend.agents.models import (
    AnalysisServices,
    AnalysisState,
    DebateState,
    PipelineConfig,
    TradingSignal,
)
from backend.agents.news_crawler import news_crawler_node
from backend.agents.risk_officer import risk_officer_node
from backend.agents.sentiment_analyst import sentiment_analyst_node
from backend.agents.technical_analyst import technical_analyst_node

log = structlog.get_logger(component="analysis_graph")


def should_continue_debate(
    state: AnalysisState, config: PipelineConfig
) -> str:
    """Determine next node after a debate turn.

    Faithful port of TradingAgents-CN conditional logic:
    - count >= 2 * max_rounds → risk_officer (end debate)
    - last speaker was Bull → bear_researcher
    - last speaker was Bear or count == 0 → bull_researcher
    """
    debate = state["debate_state"]
    count = debate["count"]
    max_count = 2 * config.max_debate_rounds

    if count >= max_count:
        return "risk_officer"

    current = debate["current_response"]
    if current.startswith("Bull:"):
        return "bear_researcher"
    return "bull_researcher"


async def _init_debate_node(state: AnalysisState) -> dict[str, Any]:
    """Initialize debate state with empty values."""
    return {
        "debate_state": DebateState(
            history="",
            bull_history="",
            bear_history="",
            current_response="",
            count=0,
        )
    }


def _make_node(
    fn: Any, services: AnalysisServices
) -> Any:
    """Wrap an agent node function to inject services."""

    async def wrapper(state: AnalysisState) -> dict[str, Any]:
        return await fn(state, services)

    wrapper.__name__ = fn.__name__
    return wrapper


def build_analysis_graph(
    services: AnalysisServices,
) -> Any:
    """Build and compile the LangGraph analysis pipeline.

    Pipeline:
    1. Parallel: news, sentiment, fundamental, technical analysts
    2. Sequential: intelligence_officer (reads all 4 reports)
    3. Init debate → Bull/Bear alternating debate with conditional edges
    4. Sequential: risk_officer → fund_manager

    Args:
        services: Bundle of LLM router and data services.

    Returns:
        Compiled LangGraph graph ready for ainvoke().
    """
    config = services.pipeline_config
    graph = StateGraph(AnalysisState)

    # Stage 1: parallel analysts
    graph.add_node("news_crawler", _make_node(news_crawler_node, services))
    graph.add_node(
        "sentiment_analyst", _make_node(sentiment_analyst_node, services)
    )
    graph.add_node(
        "fundamental_analyst",
        _make_node(fundamental_analyst_node, services),
    )
    graph.add_node(
        "technical_analyst", _make_node(technical_analyst_node, services)
    )
    graph.add_node(
        "intelligence_officer",
        _make_node(intelligence_officer_node, services),
    )

    # Stage 2: debate
    graph.add_node("init_debate", _init_debate_node)
    graph.add_node(
        "bull_researcher", _make_node(bull_researcher_node, services)
    )
    graph.add_node(
        "bear_researcher", _make_node(bear_researcher_node, services)
    )

    # Stage 3: decision
    graph.add_node("risk_officer", _make_node(risk_officer_node, services))
    graph.add_node("fund_manager", _make_node(fund_manager_node, services))

    # Edges: START → 4 parallel analysts
    graph.add_edge(START, "news_crawler")
    graph.add_edge(START, "sentiment_analyst")
    graph.add_edge(START, "fundamental_analyst")
    graph.add_edge(START, "technical_analyst")

    # 4 analysts → intelligence_officer
    graph.add_edge("news_crawler", "intelligence_officer")
    graph.add_edge("sentiment_analyst", "intelligence_officer")
    graph.add_edge("fundamental_analyst", "intelligence_officer")
    graph.add_edge("technical_analyst", "intelligence_officer")

    # intelligence_officer → init_debate → debate loop
    graph.add_edge("intelligence_officer", "init_debate")

    # Debate conditional routing
    def _debate_router(state: AnalysisState) -> str:
        return should_continue_debate(state, config)

    graph.add_conditional_edges(
        "init_debate",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )
    graph.add_conditional_edges(
        "bull_researcher",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )
    graph.add_conditional_edges(
        "bear_researcher",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )

    # Decision: risk → fund_manager → END
    graph.add_edge("risk_officer", "fund_manager")
    graph.add_edge("fund_manager", END)

    return graph.compile()


async def run_analysis(
    stock_code: str, services: AnalysisServices
) -> TradingSignal:
    """Run the full multi-agent analysis pipeline for a stock.

    Args:
        stock_code: 6-digit A-share stock code.
        services: Bundle of LLM router and data services.

    Returns:
        TradingSignal with the final trading decision.
    """
    log.info("analysis_started", stock_code=stock_code)

    # Look up stock name
    stock_name = stock_code
    try:
        quote = await services.market_data.get_stock_realtime(stock_code)
        stock_name = getattr(quote, "name", stock_code)
    except Exception as exc:
        log.warning("stock_name_lookup_failed", stock_code=stock_code, error=str(exc))

    trade_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    initial_state: AnalysisState = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "news_report": "",
        "sentiment_report": "",
        "fundamental_report": "",
        "technical_report": "",
        "intelligence_report": "",
        "debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_assessment": "",
        "trading_signal": {},
    }

    compiled = build_analysis_graph(services)
    result = await compiled.ainvoke(initial_state)

    signal_data = result.get("trading_signal", {})
    signal = TradingSignal(
        action=signal_data.get("action", "持有"),
        target_price=signal_data.get("target_price"),
        confidence=signal_data.get("confidence", 0.5),
        risk_score=signal_data.get("risk_score", 0.5),
        reasoning=signal_data.get("reasoning", "Pipeline completed"),
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
    )

    log.info(
        "analysis_completed",
        stock_code=stock_code,
        action=signal.action,
        confidence=signal.confidence,
    )
    return signal
