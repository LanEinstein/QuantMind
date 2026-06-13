"""M-002 module contract: agents_team public API + graph topology invariant.

agents_team is a decision-path module (it legitimately imports backend.risk /
backend.broker as pure tool nodes), so the isolation rule here is structural,
not an import ban: prove the graph routes analysts/debate/fund_manager BEFORE
the deterministic tool nodes, so no agent node sits on an edge that writes the
risk/builder output (R0 §4 — LLM never writes the decision path).
"""

from __future__ import annotations

import pytest

import backend.agents_team as agents_team
from backend.agents_team.graph import build_team_graph
from backend.agents_team.state import TeamContext
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.risk.engine import RiskEngine


def _ctx() -> TeamContext:
    return TeamContext(
        risk_engine=RiskEngine(
            RiskConfig(
                position_limits=PositionLimitsConfig(),
                stop_loss=StopLossConfig(),
                circuit_breaker=CircuitBreakerConfig(),
                universe=UniverseConfig(),
            )
        ),
        account=AccountInfo(
            total_assets=1.0, available_cash=1.0, frozen_cash=0.0,
            market_value=0.0, total_pnl=0.0, total_pnl_pct=0.0,
            initial_capital=1.0,
        ),
    )


class TestPublicAPI:
    @pytest.mark.unit
    def test_exports_core_symbols(self) -> None:
        for name in (
            "build_team_graph",
            "run_team",
            "open_sqlite_checkpointer",
            "TeamContext",
            "TeamState",
            "CandidateBrief",
            "LLMCompleter",
            "MANDATORY_AGENTS",
            "make_initial_state",
            "to_fund_manager_output",
        ):
            assert hasattr(agents_team, name), f"missing {name}"

    @pytest.mark.unit
    def test_all_is_importable(self) -> None:
        for name in agents_team.__all__:
            assert hasattr(agents_team, name), f"__all__ missing {name}"


class TestGraphTopology:
    @pytest.mark.unit
    def test_expected_nodes_present(self) -> None:
        graph = build_team_graph(_ctx()).get_graph()
        nodes = set(graph.nodes)
        for n in (
            "fundamental_analyst",
            "technical_analyst",
            "risk_officer",
            "debate",
            "traders",
            "fund_manager",
            "risk_gate",
            "builder",
        ):
            assert n in nodes, f"node {n} missing from graph"

    @pytest.mark.unit
    def test_traders_feed_fund_manager_not_tool_nodes(self) -> None:
        """T-002: debate → traders → fund_manager; the traders node never has an
        outgoing edge to a deterministic tool node (R0 §4)."""
        graph = build_team_graph(_ctx()).get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("debate", "traders") in edges
        assert ("traders", "fund_manager") in edges
        tool_nodes = {"risk_gate", "builder"}
        for e in graph.edges:
            if e.source == "traders":
                assert e.target not in tool_nodes, (
                    f"traders node feeds tool node {e.target}"
                )

    @pytest.mark.unit
    def test_tool_nodes_run_after_fund_manager(self) -> None:
        """fund_manager → risk_gate → builder → END (tool nodes are downstream
        of every LLM/agent node, so no agent edge writes their output)."""
        graph = build_team_graph(_ctx()).get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("fund_manager", "risk_gate") in edges
        assert ("risk_gate", "builder") in edges

    @pytest.mark.unit
    def test_no_agent_node_feeds_a_tool_node_directly(self) -> None:
        """Only fund_manager feeds risk_gate; only risk_gate feeds builder —
        the three analysts + debate never have an outgoing edge to a tool node."""
        graph = build_team_graph(_ctx()).get_graph()
        agent_nodes = {
            "fundamental_analyst", "technical_analyst", "risk_officer", "debate",
            "traders",
        }
        tool_nodes = {"risk_gate", "builder"}
        for e in graph.edges:
            if e.source in agent_nodes:
                assert e.target not in tool_nodes, (
                    f"agent {e.source} feeds tool node {e.target}"
                )
