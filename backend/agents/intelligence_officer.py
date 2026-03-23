"""Intelligence officer agent: fuses all analysis reports and market data.

When a MiroFish simulator is available, extracts high-importance events
from the news report and runs group-intelligence simulations. Results
are formatted and injected into the LLM prompt as additional context
for the Bull/Bear debate (Blueprint V3 Section 3.2).
"""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import INTELLIGENCE_OFFICER_PROMPT

log = structlog.get_logger(component="agent.intelligence_officer")


async def intelligence_officer_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Fuse all Stage 1 reports with market overview and MiroFish simulation.

    Steps:
    1. Fetch market context (indices, capital flow)
    2. If MiroFish simulator available: extract key events from news,
       run simulation for high-importance events, format results
    3. Call LLM with enriched context (reports + market + simulation)

    Returns:
        Dict with 'intelligence_report' key for state update.
    """
    # -- Step 1: Market context (existing) --
    market_context_parts: list[str] = []
    try:
        indices = await services.market_data.get_index_realtime()
        idx_text = "\n".join(
            f"  {i.name}: {i.price} ({i.change_pct:+.2f}%)"
            for i in indices
        )
        market_context_parts.append(f"大盘指数:\n{idx_text}")
    except Exception as exc:
        log.warning("index_fetch_failed", error=str(exc))

    try:
        flow = await services.market_data.get_capital_flow()
        market_context_parts.append(
            f"北向资金净流入: {flow.north_net_inflow / 1e8:.2f}亿"
        )
    except Exception as exc:
        log.warning("capital_flow_failed", error=str(exc))

    market_context = "\n".join(market_context_parts) or "市场概览数据不可用"

    # -- Step 2: MiroFish simulation (new) --
    # Lazy imports to avoid circular dependency (mirofish -> agents -> graph -> here)
    simulation_context = ""
    if services.mirofish_simulator is not None:
        from backend.mirofish.event_filter import extract_key_events
        from backend.mirofish.formatter import format_simulation_context

        try:
            events = await extract_key_events(
                services.llm_router,
                state["news_report"],
                state["stock_code"],
                state["stock_name"],
            )
            if events:
                from backend.mirofish.schemas import SimulationResult

                results: list[SimulationResult] = []
                for event in events:
                    try:
                        result = await services.mirofish_simulator.run_simulation(
                            event
                        )
                        results.append(result)
                    except Exception as exc:
                        log.warning(
                            "mirofish_simulation_failed",
                            event=event.title,
                            error=str(exc),
                        )
                if results:
                    simulation_context = format_simulation_context(
                        tuple(results)
                    )
                    log.info(
                        "mirofish_simulations_complete",
                        count=len(results),
                    )
        except Exception as exc:
            log.warning("mirofish_pipeline_failed", error=str(exc))

    # -- Step 3: Call LLM with enriched context --
    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"=== 新闻分析报告 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析报告 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析报告 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析报告 ===\n{state['technical_report']}\n\n"
        f"=== 市场概览 ===\n{market_context}"
    )

    if simulation_context:
        user_content += (
            f"\n\n=== MiroFish群体智能仿真 ===\n{simulation_context}"
        )

    report = await call_agent(
        services.llm_router,
        "intelligence_officer",
        INTELLIGENCE_OFFICER_PROMPT,
        user_content,
    )
    return {"intelligence_report": report}
