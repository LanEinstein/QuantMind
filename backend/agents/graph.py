"""LangGraph state graph for the multi-agent analysis pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from backend.agents.bear_researcher import bear_researcher_node
from backend.agents.bull_researcher import bull_researcher_node
from backend.agents.collector import EventEmitter, RunCollector
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
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.agents.risk_officer import risk_officer_node
from backend.agents.sentiment_analyst import sentiment_analyst_node
from backend.agents.technical_analyst import technical_analyst_node

log = structlog.get_logger(component="analysis_graph")

DEBATE_AGENTS = ("bull_researcher", "bear_researcher")


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
    """Initialize debate state with empty values. Not recorded as agent step."""
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
    node_name: str,
    fn: Any,
    services: AnalysisServices,
    collector: RunCollector | None,
) -> Any:
    """Wrap an agent node function to inject services and record steps.

    When `collector` is provided, each call emits agent_started /
    agent_completed events and appends an AgentStepRecord. Errors from
    `fn` are forwarded after emitting a failed step + error event, so
    LangGraph can still terminate the run; callers decide whether to
    re-raise.
    """

    async def wrapper(state: AnalysisState) -> dict[str, Any]:
        round_ = 0
        if node_name in DEBATE_AGENTS:
            current_count = state.get("debate_state", {}).get("count", 0)
            round_ = (current_count // 2) + 1

        if collector is not None:
            started_at = await collector.on_agent_started(node_name, round_)
        else:
            started_at = datetime.now(tz=UTC)

        try:
            result = await fn(state, services)
        except Exception as exc:
            if collector is not None:
                # Record a proper failed step (was previously emitted as
                # status=completed with empty content, which masked the
                # failure in the persisted record).
                await collector.on_agent_failed(
                    node_name, round_, started_at, str(exc)
                )
                await collector.on_error(f"{node_name}: {exc}")
            raise

        if collector is not None:
            await collector.on_agent_completed(
                node_name, round_, started_at, result
            )
        return result

    wrapper.__name__ = fn.__name__
    return wrapper


def build_analysis_graph(
    services: AnalysisServices,
    *,
    collector: RunCollector | None = None,
) -> Any:
    """Build and compile the LangGraph analysis pipeline.

    Pipeline:
    1. Parallel: news, sentiment, fundamental, technical analysts
    2. Sequential: intelligence_officer (reads all 4 reports)
    3. Init debate → Bull/Bear alternating debate with conditional edges
    4. Sequential: risk_officer → fund_manager

    Args:
        services: Bundle of LLM router and data services.
        collector: Optional run collector for step recording and SSE
            event emission. When None, graph runs without instrumentation
            (legacy callers).

    Returns:
        Compiled LangGraph graph ready for ainvoke().
    """
    config = services.pipeline_config
    graph = StateGraph(AnalysisState)

    # Stage 1: parallel analysts
    graph.add_node(
        "news_crawler",
        _make_node("news_crawler", news_crawler_node, services, collector),
    )
    graph.add_node(
        "sentiment_analyst",
        _make_node(
            "sentiment_analyst", sentiment_analyst_node, services, collector
        ),
    )
    graph.add_node(
        "fundamental_analyst",
        _make_node(
            "fundamental_analyst",
            fundamental_analyst_node,
            services,
            collector,
        ),
    )
    graph.add_node(
        "technical_analyst",
        _make_node(
            "technical_analyst",
            technical_analyst_node,
            services,
            collector,
        ),
    )
    graph.add_node(
        "intelligence_officer",
        _make_node(
            "intelligence_officer",
            intelligence_officer_node,
            services,
            collector,
        ),
    )

    # Stage 2: debate. init_debate is not recorded as an agent step.
    graph.add_node("init_debate", _init_debate_node)
    graph.add_node(
        "bull_researcher",
        _make_node(
            "bull_researcher", bull_researcher_node, services, collector
        ),
    )
    graph.add_node(
        "bear_researcher",
        _make_node(
            "bear_researcher", bear_researcher_node, services, collector
        ),
    )

    # Stage 3: decision
    graph.add_node(
        "risk_officer",
        _make_node("risk_officer", risk_officer_node, services, collector),
    )
    graph.add_node(
        "fund_manager",
        _make_node("fund_manager", fund_manager_node, services, collector),
    )

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
    stock_code: str,
    services: AnalysisServices,
    *,
    run_id: str | None = None,
    emitter: EventEmitter | None = None,
) -> AnalysisRunResult:
    """Run the full multi-agent analysis pipeline for a stock.

    Args:
        stock_code: 6-digit A-share stock code.
        services: Bundle of LLM router and data services.
        run_id: Optional pre-assigned UUID (the jobs API assigns one so
            the stream can key events before run_analysis starts).
        emitter: Optional async callable that receives SSE event dicts.
            When provided, per-agent started/completed events are pushed
            as the pipeline progresses (Session A2).

    Returns:
        AnalysisRunResult containing the terminal TradingSignal and the
        complete AnalysisRecord. `record.signal_id` stays None until the
        caller persists the signal and assigns it.
    """
    resolved_run_id = run_id or str(uuid.uuid4())
    log.info("analysis_started", stock_code=stock_code, run_id=resolved_run_id)

    # Look up stock name
    stock_name = stock_code
    try:
        quote = await services.market_data.get_stock_realtime(stock_code)
        stock_name = getattr(quote, "name", stock_code)
    except Exception as exc:
        log.warning(
            "stock_name_lookup_failed", stock_code=stock_code, error=str(exc)
        )

    trade_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    collector = RunCollector(
        run_id=resolved_run_id,
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
        max_rounds=services.pipeline_config.max_debate_rounds,
        emitter=emitter,
    )

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

    compiled = build_analysis_graph(services, collector=collector)

    try:
        result = await compiled.ainvoke(initial_state)
    except Exception as exc:
        log.error(
            "analysis_pipeline_failed",
            stock_code=stock_code,
            run_id=resolved_run_id,
            error=str(exc),
        )
        record = collector.finalize(
            status="failed", signal=None, error=str(exc)
        )
        raise AnalysisRunError(record) from exc

    # When the graph completes but at least one agent finalized as
    # failed (either a graceful "[agent error: ...]" string from
    # call_agent or a hard exception caught in _make_node), the run is
    # NOT a clean success. Promoting it to status=completed with a
    # synthetic neutral signal would silently bypass the failure
    # instead of surfacing it through /history and the SSE error path.
    if collector.has_failed_steps():
        summary = collector.first_failure_summary() or "agent failed"
        log.warning(
            "analysis_pipeline_partial_failure",
            stock_code=stock_code,
            run_id=resolved_run_id,
            failure=summary,
        )
        record = collector.finalize(
            status="failed", signal=None, error=summary
        )
        raise AnalysisRunError(record)

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
    # ``fund_manager_node`` writes ``parse_ok`` into the signal dict
    # (codex P5B-shadow R2 P2) so a synthetic ``持有 / 0.5`` fallback
    # surfaces on FundManagerRecord and the shadow harness can drop
    # it from gate math. Defaults to True for the legacy path.
    signal_parse_ok = bool(signal_data.get("parse_ok", True))

    record = collector.finalize(
        status="completed", signal=signal, signal_parse_ok=signal_parse_ok
    )

    log.info(
        "analysis_completed",
        stock_code=stock_code,
        run_id=resolved_run_id,
        action=signal.action,
        confidence=signal.confidence,
    )
    return AnalysisRunResult(signal=signal, record=record)


class AnalysisRunError(Exception):
    """Surfaces an AnalysisRecord through an exception path.

    Callers (jobs API, /stock, scheduler) catch this and persist
    ``record`` so failed runs still appear in /history. The exception
    message comes from ``record.error`` when available.
    """

    def __init__(self, record: AnalysisRecord) -> None:
        super().__init__(record.error or "analysis failed")
        self.record = record
