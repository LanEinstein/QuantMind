"""Tests for LangGraph analysis pipeline (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from backend.agents.graph import (
    build_analysis_graph,
    run_analysis,
    should_continue_debate,
)
from backend.agents.models import (
    AnalysisServices,
    AnalysisState,
    PipelineConfig,
    TradingSignal,
)
from backend.agents.records import AnalysisRecord, AnalysisRunResult


def _make_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _mock_services(max_rounds: int = 1) -> AnalysisServices:
    """Create mock services for graph testing."""
    call_count = 0

    def _route_response(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        agent_name = args[0] if args else "unknown"
        if agent_name == "fund_manager":
            return _make_completion(
                '{"action": "买入", "target_price": 1900.0, '
                '"confidence": 0.8, "risk_score": 0.3, '
                '"reasoning": "分析完成"}'
            )
        return _make_completion(f"[{agent_name}] 模拟报告")

    router = AsyncMock()
    router.complete = AsyncMock(side_effect=_route_response)

    market_data = AsyncMock()
    stock_mock = MagicMock()
    stock_mock.code = "600519"
    stock_mock.name = "贵州茅台"
    stock_mock.price = 1800.0
    stock_mock.change_pct = 0.28
    stock_mock.volume = 5e6
    stock_mock.amount = 9e9
    market_data.get_stock_realtime = AsyncMock(return_value=stock_mock)

    index_mock = MagicMock()
    index_mock.name = "上证指数"
    index_mock.price = 3150.5
    index_mock.change_pct = 0.85
    market_data.get_index_realtime = AsyncMock(return_value=[index_mock])
    market_data.get_capital_flow = AsyncMock(
        return_value=MagicMock(north_net_inflow=3.2e9)
    )

    history_data = AsyncMock()
    history_data.get_financial_data = AsyncMock(
        return_value=MagicMock(
            pe_ratio=32.5, pb_ratio=10.2, roe=30.5, eps=45.8,
            revenue_growth=15.3, report_date="2025-12-31"
        )
    )
    history_data.get_kline = AsyncMock(
        return_value=pd.DataFrame([
            {"date": "2026-03-20", "open": 1790, "high": 1810,
             "low": 1785, "close": 1800, "volume": 5000000, "amount": 9e9},
        ])
    )

    news_crawler = AsyncMock()
    article = MagicMock(title="测试新闻", content="内容", source="eastmoney")
    news_crawler.fetch_stock_news = AsyncMock(return_value=[article])
    news_crawler.fetch_latest_news = AsyncMock(return_value=[article])

    return AnalysisServices(
        llm_router=router,
        market_data=market_data,
        history_data=history_data,
        news_crawler=news_crawler,
        pipeline_config=PipelineConfig(max_debate_rounds=max_rounds),
    )


class TestShouldContinueDebate:
    """Tests for debate conditional edge function."""

    def test_start_goes_to_bull(self) -> None:
        config = PipelineConfig(max_debate_rounds=2)
        state: AnalysisState = {
            "stock_code": "600519", "stock_name": "test",
            "trade_date": "2026-03-22",
            "news_report": "", "sentiment_report": "",
            "fundamental_report": "", "technical_report": "",
            "intelligence_report": "",
            "debate_state": {
                "history": "", "bull_history": "", "bear_history": "",
                "current_response": "", "count": 0,
            },
            "risk_assessment": "", "trading_signal": {},
        }
        assert should_continue_debate(state, config) == "bull_researcher"

    def test_after_bull_goes_to_bear(self) -> None:
        config = PipelineConfig(max_debate_rounds=2)
        state: AnalysisState = {
            "stock_code": "600519", "stock_name": "test",
            "trade_date": "2026-03-22",
            "news_report": "", "sentiment_report": "",
            "fundamental_report": "", "technical_report": "",
            "intelligence_report": "",
            "debate_state": {
                "history": "", "bull_history": "", "bear_history": "",
                "current_response": "Bull: arg", "count": 1,
            },
            "risk_assessment": "", "trading_signal": {},
        }
        assert should_continue_debate(state, config) == "bear_researcher"

    def test_after_max_rounds_goes_to_risk(self) -> None:
        config = PipelineConfig(max_debate_rounds=2)
        state: AnalysisState = {
            "stock_code": "600519", "stock_name": "test",
            "trade_date": "2026-03-22",
            "news_report": "", "sentiment_report": "",
            "fundamental_report": "", "technical_report": "",
            "intelligence_report": "",
            "debate_state": {
                "history": "", "bull_history": "", "bear_history": "",
                "current_response": "Bear: arg", "count": 4,
            },
            "risk_assessment": "", "trading_signal": {},
        }
        assert should_continue_debate(state, config) == "risk_officer"

    def test_single_round(self) -> None:
        config = PipelineConfig(max_debate_rounds=1)
        state: AnalysisState = {
            "stock_code": "600519", "stock_name": "test",
            "trade_date": "2026-03-22",
            "news_report": "", "sentiment_report": "",
            "fundamental_report": "", "technical_report": "",
            "intelligence_report": "",
            "debate_state": {
                "history": "", "bull_history": "", "bear_history": "",
                "current_response": "Bear: arg", "count": 2,
            },
            "risk_assessment": "", "trading_signal": {},
        }
        assert should_continue_debate(state, config) == "risk_officer"


class TestBuildAnalysisGraph:
    """Tests for graph compilation."""

    def test_compiles(self) -> None:
        services = _mock_services()
        graph = build_analysis_graph(services)
        assert graph is not None


class TestRunAnalysis:
    """Integration test for full pipeline execution."""

    @pytest.mark.asyncio
    async def test_full_pipeline_single_round(self) -> None:
        services = _mock_services(max_rounds=1)
        result = await run_analysis("600519", services)
        assert isinstance(result, AnalysisRunResult)
        assert isinstance(result.signal, TradingSignal)
        assert isinstance(result.record, AnalysisRecord)
        assert result.signal.action == "买入"
        assert result.signal.stock_code == "600519"
        # 5 analysts + 2 debate (1 round: bull + bear) + 2 decision = 9 calls
        assert services.llm_router.complete.call_count == 9

    @pytest.mark.asyncio
    async def test_full_pipeline_two_rounds(self) -> None:
        services = _mock_services(max_rounds=2)
        result = await run_analysis("600519", services)
        assert isinstance(result, AnalysisRunResult)
        assert isinstance(result.signal, TradingSignal)
        # 5 analysts + 4 debate (2 rounds) + 2 decision = 11 calls
        assert services.llm_router.complete.call_count == 11

    @pytest.mark.asyncio
    async def test_record_populated(self) -> None:
        """Record contains analysts, intelligence, debates, risk, decision."""
        services = _mock_services(max_rounds=2)
        result = await run_analysis("600519", services)
        record = result.record

        assert record.stock_code == "600519"
        assert record.status == "completed"
        assert record.max_rounds == 2
        assert record.current_round == 2

        # 4 parallel analysts all recorded
        analyst_agents = {s.agent for s in record.analysts}
        assert analyst_agents == {
            "news_crawler",
            "sentiment_analyst",
            "fundamental_analyst",
            "technical_analyst",
        }

        assert record.intelligence_officer is not None
        assert record.intelligence_officer.agent == "intelligence_officer"

        # 2 debate rounds, each with bull + bear
        assert len(record.debates) == 2
        for r in record.debates:
            assert r.bull is not None
            assert r.bear is not None

        # Risk + decision populated
        assert record.risk_assessment is not None
        assert record.decision is not None
        assert record.decision.action == result.signal.action

        # Steps total: 4 analysts + 1 intel + 4 debate + 1 risk + 1 fund = 11
        assert len(record.steps) == 11

        # Bull/bear content has prefix stripped
        for r in record.debates:
            if r.bull is not None:
                assert not r.bull.content.startswith("Bull:")
            if r.bear is not None:
                assert not r.bear.content.startswith("Bear:")

        # signal_id stays None until caller persists
        assert record.signal_id is None

    @pytest.mark.asyncio
    async def test_fund_manager_parse_failure_propagates_to_record(
        self,
    ) -> None:
        """codex P5B-shadow R4 P2 end-to-end lock.

        An invalid fund_manager response must:
        1. trigger ``_parse_signal``'s synthetic 持有/0.5 fallback
        2. surface ``trading_signal["parse_ok"] = False``
        3. flow through ``run_analysis`` → ``RunCollector.finalize``
        4. land on ``record.decision.parse_ok = False``
        5. survive ``model_dump(mode="json")`` round-trip so Mongo
           and AnalysisRecord rebuilds agree.

        Without this lock, a regression on any one of those hops
        would silently re-poison the shadow gate math (R2 P2 root).
        """
        call_count = 0

        def _route_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            agent_name = args[0] if args else "unknown"
            if agent_name == "fund_manager":
                # Malformed: not JSON at all → live extractor returns
                # None → _parse_signal hits its synthetic fallback.
                return _make_completion("not even close to json")
            return _make_completion(f"[{agent_name}] 模拟报告")

        services = _mock_services(max_rounds=1)
        services.llm_router.complete = AsyncMock(side_effect=_route_response)
        result = await run_analysis("600519", services)

        assert result.signal.action == "持有"  # synthetic fallback
        assert result.signal.confidence == 0.5
        assert result.record.decision is not None
        assert result.record.decision.parse_ok is False

        # Round-trip through model_dump(mode="json") — that's how
        # analysis_scheduler persists records into Mongo. The flag
        # must be retained so on read-back the shadow runner still
        # sees the synthetic decision.
        dumped = result.record.model_dump(mode="json")
        decision = dumped.get("decision")
        assert isinstance(decision, dict)
        assert decision["parse_ok"] is False
        rebuilt = AnalysisRecord.model_validate(dumped)
        assert rebuilt.decision is not None
        assert rebuilt.decision.parse_ok is False

    @pytest.mark.asyncio
    async def test_emitter_receives_events(self) -> None:
        """Emitter callback receives started/completed/pipeline events."""
        services = _mock_services(max_rounds=1)
        events: list[dict] = []

        async def emitter(event: dict) -> None:
            events.append(event)

        await run_analysis("600519", services, emitter=emitter)

        types = [e["event_type"] for e in events]
        assert "agent_started" in types
        assert "agent_completed" in types
        # started count == completed count == 9 (1-round pipeline)
        assert types.count("agent_started") == 9
        assert types.count("agent_completed") == 9

    @pytest.mark.asyncio
    async def test_agent_failure_raises_analysis_run_error(self) -> None:
        """Graph-level regression guard for R1 C2/C3:

        When call_agent returns its graceful "[agent error: ...]" string
        for a node, run_analysis() must NOT finalize a completed signal.
        Instead it raises AnalysisRunError and the attached record carries
        status=failed with at least one failed step. A regression that
        flips status back to completed would make this test fail.
        """
        from backend.agents.graph import AnalysisRunError

        services = _mock_services(max_rounds=1)
        original_side_effect = services.llm_router.complete.side_effect

        def side_effect(*args, **kwargs):
            agent_name = args[0] if args else ""
            if agent_name == "fundamental_analyst":
                # Empty choices triggers the graceful-failure branch in
                # backend/agents/base.py:call_agent, which returns
                # "[fundamental_analyst error: empty response]" — the
                # sentinel string that collector.classify_status maps to
                # status=failed.
                resp = MagicMock()
                resp.choices = []
                resp.usage = None
                return resp
            return original_side_effect(*args, **kwargs)

        services.llm_router.complete.side_effect = side_effect

        with pytest.raises(AnalysisRunError) as exc_info:
            await run_analysis("600519", services)

        record = exc_info.value.record
        assert record.status == "failed"
        failed_steps = [s for s in record.steps if s.status == "failed"]
        assert len(failed_steps) >= 1
        assert any(
            s.agent == "fundamental_analyst" for s in failed_steps
        )
        # error message surfaces the failing agent
        assert record.error is not None
        assert "fundamental_analyst" in record.error

    @pytest.mark.asyncio
    async def test_emitter_marks_failed_agent_step(self) -> None:
        """Ensure on_agent_failed emits agent_completed with status=failed.

        Guards against a regression where the SSE event for a failed
        agent would either be missing or would carry status=completed
        (the pre-fix behavior that showed up in R1 findings).
        """
        from backend.agents.graph import AnalysisRunError

        services = _mock_services(max_rounds=1)
        original_side_effect = services.llm_router.complete.side_effect

        def side_effect(*args, **kwargs):
            agent_name = args[0] if args else ""
            if agent_name == "sentiment_analyst":
                resp = MagicMock()
                resp.choices = []
                resp.usage = None
                return resp
            return original_side_effect(*args, **kwargs)

        services.llm_router.complete.side_effect = side_effect

        events: list[dict] = []

        async def emitter(event: dict) -> None:
            events.append(event)

        with pytest.raises(AnalysisRunError):
            await run_analysis("600519", services, emitter=emitter)

        failed_events = [
            e
            for e in events
            if e.get("event_type") == "agent_completed"
            and e.get("status") == "failed"
        ]
        assert any(
            e.get("agent") == "sentiment_analyst" for e in failed_events
        ), f"No failed agent_completed event: {events}"
