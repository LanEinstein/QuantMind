"""Hidden variable extractor for MiroFish simulation output.

Extracts emergent variables NOT present in the original event but
discovered through simulated group dynamics. This is the CORE innovation
of the QuantMind system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.extractors.schemas import (
    AgentAction,
    EnrichedHiddenVariable,
    RawSimulationOutput,
    SentimentRound,
)
from backend.mirofish.prompts import HIDDEN_VARIABLE_EXTRACTION_PROMPT
from backend.mirofish.report_parser import extract_deep_json

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.extractor.hidden_variables")

# Agent type weights for probability assessment
_AGENT_WEIGHTS: dict[str, float] = {
    "institutional": 1.5,
    "analyst": 1.3,
    "speculator": 1.0,
    "retail": 0.7,
}

_DISCLAIMER = (
    "This probability is a simulated crowd wisdom estimate, "
    "NOT a statistically rigorous probability."
)


class HiddenVariableExtractor:
    """Extract emergent hidden variables from simulation dynamics."""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def extract(
        self,
        raw_simulation: RawSimulationOutput,
        sentiment_rounds: tuple[SentimentRound, ...] = (),
    ) -> tuple[EnrichedHiddenVariable, ...]:
        """Analyze simulation transcripts to identify emergent hidden variables.

        Process:
        1. Build context from event + evolution + sentiment enrichment
        2. Use Kimi K2.6 to identify themes absent from original event
        3. Assess probability via weighted agent consensus
        4. Filter to only truly hidden variables
        5. Rank by potential market impact

        Args:
            raw_simulation: Raw output from simulation.
            sentiment_rounds: Enriched sentiment data for richer context.

        Returns:
            Tuple of EnrichedHiddenVariable sorted by probability descending.
        """
        evolution_text = "\n".join(
            f"Round {s.round}: bullish={s.bullish:.2f} "
            f"bearish={s.bearish:.2f} neutral={s.neutral:.2f}"
            for s in raw_simulation.sentiment_evolution
        )

        narrative_text = ""
        if sentiment_rounds:
            narrative_text = "\n主导叙事演变:\n" + "\n".join(
                f"  R{r.round}: {r.dominant_narrative} "
                f"(intensity={r.intensity:.2f})"
                for r in sentiment_rounds
                if r.dominant_narrative
            )

        user_content = (
            f"=== 原始事件 ===\n"
            f"标题: {raw_simulation.event_title}\n"
            f"内容: {raw_simulation.event_content[:800]}\n"
            f"涉及板块: {', '.join(raw_simulation.event_sectors)}\n"
            f"涉及个股: {', '.join(raw_simulation.event_stocks)}\n\n"
            f"=== 仿真数据 ===\n"
            f"参与者: {raw_simulation.agent_count}人 "
            f"({raw_simulation.rounds}轮)\n"
            f"初始情绪: bullish={raw_simulation.initial_sentiment.get('bullish', 0.33):.2f} "
            f"bearish={raw_simulation.initial_sentiment.get('bearish', 0.33):.2f}\n\n"
            f"情绪演变:\n{evolution_text}"
            f"{narrative_text}"
        )

        raw_response = await call_agent(
            self._router,
            "intelligence_officer",
            HIDDEN_VARIABLE_EXTRACTION_PROMPT,
            user_content,
        )

        variables = self._parse_response(raw_response)

        # Filter to only truly hidden variables (absent from original)
        hidden_only = [
            v for v in variables if v.is_absent_from_original
        ]

        # Sort by probability descending
        sorted_vars = sorted(
            hidden_only, key=lambda v: v.probability, reverse=True
        )

        log.info(
            "hidden_variable_extraction_complete",
            count=len(sorted_vars),
        )
        return tuple(sorted_vars)

    def assess_probability(
        self,
        variable: str,
        supporting_agents: tuple[AgentAction, ...],
        total_agents: int,
    ) -> float:
        """Calculate probability estimate for a hidden variable.

        Method: Weighted agent consensus
        - Base probability = supporting_agents / total_agents
        - Weight by agent type (institutional > analyst > retail)
        - Adjust by confidence of expressions
        - Normalize to 0-1 range

        Note: This is a simulated crowd wisdom estimate, NOT a
        statistically rigorous probability.

        Args:
            variable: The hidden variable description.
            supporting_agents: Agents expressing this variable.
            total_agents: Total number of simulated agents.

        Returns:
            Probability estimate between 0.0 and 1.0.
        """
        if total_agents <= 0 or not supporting_agents:
            return 0.0

        weighted_sum = sum(
            _AGENT_WEIGHTS.get(agent.agent_type, 1.0) * agent.confidence
            for agent in supporting_agents
        )

        max_possible_weight = total_agents * max(_AGENT_WEIGHTS.values())
        raw_probability = weighted_sum / max_possible_weight

        return round(max(0.0, min(1.0, raw_probability)), 3)

    @staticmethod
    def _parse_response(
        raw: str,
    ) -> list[EnrichedHiddenVariable]:
        """Parse hidden variable extraction LLM response."""
        data = extract_deep_json(raw)
        if data is None:
            log.warning("hidden_variable_parse_failed")
            return []

        variables: list[EnrichedHiddenVariable] = []
        for item in data.get("hidden_variables", []):
            if not isinstance(item, dict):
                continue
            try:
                variables.append(
                    EnrichedHiddenVariable(
                        variable=str(item.get("variable", "")),
                        probability=max(
                            0.0,
                            min(1.0, float(item.get("probability", 0.5))),
                        ),
                        reasoning=str(item.get("reasoning", "")),
                        agent_consensus_ratio=max(
                            0.0,
                            min(
                                1.0,
                                float(
                                    item.get("agent_consensus_ratio", 0.0)
                                ),
                            ),
                        ),
                        is_absent_from_original=bool(
                            item.get("is_absent_from_original", True)
                        ),
                        disclaimer=_DISCLAIMER,
                    )
                )
            except (ValueError, TypeError) as exc:
                log.warning("hidden_var_item_parse_failed", error=str(exc))
                continue

        return variables
