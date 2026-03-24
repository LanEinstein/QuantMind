"""Hidden variable extraction pipeline for MiroFish simulation.

Orchestrates 4 specialized extractors to distill raw simulation output
into actionable structured intelligence for the Bull/Bear debate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.extractors.extreme_scenarios import (
    ExtremeScenarioAnalyzer,
)
from backend.mirofish.extractors.hidden_variables import (
    HiddenVariableExtractor,
)
from backend.mirofish.extractors.inflection_points import (
    InflectionPointDetector,
)
from backend.mirofish.extractors.schemas import (
    ExtractionResult,
    RawSimulationOutput,
)
from backend.mirofish.extractors.sentiment import SentimentEvolutionTracker
from backend.mirofish.prompts import RECOMMENDED_ACTION_PROMPT
from backend.mirofish.report_parser import extract_deep_json
from backend.mirofish.schemas import (
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.extraction_pipeline")

__all__ = [
    "ExtremeScenarioAnalyzer",
    "ExtractionResult",
    "HiddenVariableExtractionPipeline",
    "HiddenVariableExtractor",
    "InflectionPointDetector",
    "RawSimulationOutput",
    "SentimentEvolutionTracker",
]


class HiddenVariableExtractionPipeline:
    """Orchestrate all extractors into a single pipeline.

    This is what the MiroFishSimulator calls after persona generation
    and evolution simulation complete. It replaces the monolithic
    extraction LLM call with a multi-extractor pipeline.
    """

    def __init__(self, router: LLMRouter) -> None:
        self._router = router
        self._sentiment_tracker = SentimentEvolutionTracker(router)
        self._hidden_var_extractor = HiddenVariableExtractor(router)
        self._inflection_detector = InflectionPointDetector(router)
        self._extreme_analyzer = ExtremeScenarioAnalyzer(router)

    async def extract_all(
        self,
        raw_simulation: RawSimulationOutput,
    ) -> ExtractionResult:
        """Run all extractors and assemble final ExtractionResult.

        Pipeline:
        1. SentimentEvolutionTracker.extract()
        2. SentimentEvolutionTracker.detect_momentum_shift()
        3. HiddenVariableExtractor.extract()
        4. InflectionPointDetector.extract()
        5. ExtremeScenarioAnalyzer.extract()
        6. Generate recommended_action summary
        7. Assemble into ExtractionResult

        Args:
            raw_simulation: Raw output from simulation calls 1 & 2.

        Returns:
            Complete ExtractionResult with all extracted intelligence.
        """
        log.info(
            "extraction_pipeline_start",
            sim_event=raw_simulation.event_summary,
            rounds=len(raw_simulation.sentiment_evolution),
        )

        # Step 1: Enrich sentiment evolution
        sentiment_rounds = await self._sentiment_tracker.extract(
            raw_simulation
        )

        # Step 2: Detect momentum shifts
        momentum_shifts = await self._sentiment_tracker.detect_momentum_shift(
            sentiment_rounds
        )

        # Step 3: Extract hidden variables
        hidden_variables = await self._hidden_var_extractor.extract(
            raw_simulation, sentiment_rounds
        )

        # Step 4: Detect inflection points
        inflection_points = await self._inflection_detector.extract(
            sentiment_rounds, hidden_variables
        )

        # Step 5: Analyze extreme scenarios
        extreme_scenarios = await self._extreme_analyzer.extract(
            raw_simulation, sentiment_rounds
        )

        # Step 6: Generate recommended action
        recommended_action = await self._generate_recommendation(
            raw_simulation.event_summary,
            sentiment_rounds,
            momentum_shifts,
            hidden_variables,
            inflection_points,
            extreme_scenarios,
        )

        result = ExtractionResult(
            event_summary=raw_simulation.event_summary,
            sentiment_rounds=sentiment_rounds,
            momentum_shifts=momentum_shifts,
            hidden_variables=hidden_variables,
            inflection_points=inflection_points,
            extreme_scenarios=extreme_scenarios,
            recommended_action=recommended_action,
        )

        log.info(
            "extraction_pipeline_complete",
            sentiment_rounds=len(sentiment_rounds),
            momentum_shifts=len(momentum_shifts),
            hidden_variables=len(hidden_variables),
            inflection_points=len(inflection_points),
            extreme_scenarios=len(extreme_scenarios),
        )

        return result

    def to_simulation_result(
        self,
        extraction: ExtractionResult,
        config: SimulationConfig,
        cost_rmb: float,
        duration_seconds: float,
    ) -> SimulationResult:
        """Map ExtractionResult to SimulationResult for backward compat.

        Converts enriched types back to base schema types so existing
        formatter and downstream consumers continue to work.

        Args:
            extraction: Full extraction pipeline output.
            config: Simulation configuration used.
            cost_rmb: Estimated cost in RMB.
            duration_seconds: Total execution time.

        Returns:
            SimulationResult conforming to Blueprint V3 section 3.3.
        """
        sentiment_evolution = tuple(
            SentimentSnapshot(
                round=r.round,
                bullish=r.bullish,
                bearish=r.bearish,
                neutral=r.neutral,
            )
            for r in extraction.sentiment_rounds
        )

        hidden_variables = tuple(
            HiddenVariable(
                variable=hv.variable,
                probability=hv.probability,
                reasoning=(
                    f"{hv.reasoning} "
                    f"[consensus={hv.agent_consensus_ratio:.0%}] "
                    f"[{hv.disclaimer}]"
                ),
            )
            for hv in extraction.hidden_variables
        )

        inflection_points = tuple(
            InflectionPoint(
                day=ip.day,
                event=(
                    f"[{ip.inflection_type}] {ip.event} "
                    f"(confidence={ip.confidence:.0%})"
                ),
            )
            for ip in extraction.inflection_points
        )

        extreme_scenarios = tuple(
            ExtremeScenario(
                scenario=(
                    f"[{es.direction}] {es.scenario}"
                    if es.direction
                    else es.scenario
                ),
                probability=es.probability,
                impact=es.impact,
            )
            for es in extraction.extreme_scenarios
        )

        # Append momentum shift info to recommended_action
        action_parts = [extraction.recommended_action]
        if extraction.momentum_shifts:
            shift_text = "; ".join(
                f"R{ms.round_number}: {ms.direction} "
                f"(magnitude={ms.magnitude:.0%})"
                for ms in extraction.momentum_shifts
            )
            action_parts.append(f"[动量转换: {shift_text}]")

        return SimulationResult(
            event_summary=extraction.event_summary,
            simulation_config=config,
            sentiment_evolution=sentiment_evolution,
            hidden_variables=hidden_variables,
            key_inflection_points=inflection_points,
            extreme_scenarios=extreme_scenarios,
            recommended_action=" ".join(action_parts),
            cost_rmb=cost_rmb,
            duration_seconds=duration_seconds,
        )

    async def _generate_recommendation(
        self,
        event_summary: str,
        sentiment_rounds: tuple,
        momentum_shifts: tuple,
        hidden_variables: tuple,
        inflection_points: tuple,
        extreme_scenarios: tuple,
    ) -> str:
        """Generate recommended action summary using MiniMax M2.5."""
        sentiment_text = ""
        if sentiment_rounds:
            last = sentiment_rounds[-1]
            sentiment_text = (
                f"最终情绪: bullish={last.bullish:.2f} "
                f"bearish={last.bearish:.2f}\n"
            )

        shifts_text = ""
        if momentum_shifts:
            shifts_text = "动量转换:\n" + "\n".join(
                f"  R{ms.round_number}: {ms.direction} "
                f"(magnitude={ms.magnitude:.0%})"
                for ms in momentum_shifts
            ) + "\n"

        hidden_text = ""
        if hidden_variables:
            hidden_text = "隐性变量:\n" + "\n".join(
                f"  - {hv.variable} ({hv.probability:.0%})"
                for hv in hidden_variables
            ) + "\n"

        inflection_text = ""
        if inflection_points:
            inflection_text = "拐点:\n" + "\n".join(
                f"  Day {ip.day}: {ip.event}"
                for ip in inflection_points
            ) + "\n"

        extreme_text = ""
        if extreme_scenarios:
            extreme_text = "极端场景:\n" + "\n".join(
                f"  [{es.direction}] {es.scenario} "
                f"({es.probability:.0%}, {es.impact})"
                for es in extreme_scenarios
            ) + "\n"

        user_content = (
            f"事件: {event_summary}\n\n"
            f"{sentiment_text}"
            f"{shifts_text}"
            f"{hidden_text}"
            f"{inflection_text}"
            f"{extreme_text}"
        )

        raw = await call_agent(
            self._router,
            "intelligence_officer",
            RECOMMENDED_ACTION_PROMPT,
            user_content,
        )

        data = extract_deep_json(raw)
        if data and "recommended_action" in data:
            return str(data["recommended_action"])

        log.warning("recommendation_parse_failed_using_fallback")
        return "仿真结果已提取，请结合隐性变量和极端场景综合判断"
