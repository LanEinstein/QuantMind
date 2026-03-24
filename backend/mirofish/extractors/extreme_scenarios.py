"""Extreme scenario analyzer for MiroFish simulation output.

Identifies tail risk and outlier scenarios that deviate significantly
from the median simulated expectation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.extractors.schemas import (
    EnrichedExtremeScenario,
    RawSimulationOutput,
    SentimentRound,
)
from backend.mirofish.prompts import EXTREME_SCENARIO_PROMPT
from backend.mirofish.report_parser import extract_deep_json

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.extractor.extreme_scenarios")


class ExtremeScenarioAnalyzer:
    """Identify tail risk and outlier scenarios from simulation."""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def extract(
        self,
        raw_simulation: RawSimulationOutput,
        evolution: tuple[SentimentRound, ...],
    ) -> tuple[EnrichedExtremeScenario, ...]:
        """Identify scenarios where outcomes deviate significantly from median.

        Process:
        1. Cluster simulation outcomes into scenario groups
        2. Identify the median scenario
        3. Find outlier clusters that deviate significantly
        4. For each outlier, estimate probability, impact, triggers, warnings
        5. Must produce at least 1 upside + 1 downside extreme

        Args:
            raw_simulation: Raw simulation output for event context.
            evolution: Enriched sentiment evolution data.

        Returns:
            Tuple of extreme scenarios with at least 1 upside + 1 downside.
        """
        evolution_text = "\n".join(
            f"Round {r.round}: bullish={r.bullish:.2f} "
            f"bearish={r.bearish:.2f} neutral={r.neutral:.2f} "
            f"narrative='{r.dominant_narrative}' "
            f"intensity={r.intensity:.2f}"
            for r in evolution
        ) if evolution else "无情绪演变数据"

        user_content = (
            f"=== 事件信息 ===\n"
            f"事件: {raw_simulation.event_summary}\n"
            f"详情: {raw_simulation.event_content[:500]}\n"
            f"板块: {', '.join(raw_simulation.event_sectors)}\n\n"
            f"=== 仿真参数 ===\n"
            f"参与者: {raw_simulation.agent_count}人, "
            f"{raw_simulation.rounds}轮\n\n"
            f"=== 情绪演变 ===\n{evolution_text}"
        )

        raw_response = await call_agent(
            self._router,
            "intelligence_officer",
            EXTREME_SCENARIO_PROMPT,
            user_content,
        )

        scenarios = self._parse_response(raw_response)

        # Ensure at least 1 upside + 1 downside
        scenarios = self._ensure_both_directions(
            scenarios, raw_simulation.event_summary
        )

        log.info(
            "extreme_scenario_extraction_complete",
            count=len(scenarios),
        )
        return tuple(scenarios)

    @staticmethod
    def _ensure_both_directions(
        scenarios: list[EnrichedExtremeScenario],
        event_summary: str,
    ) -> list[EnrichedExtremeScenario]:
        """Guarantee at least 1 upside and 1 downside scenario.

        Adds generic fallback scenarios if LLM did not produce both.
        """
        has_upside = any(s.direction == "upside" for s in scenarios)
        has_downside = any(s.direction == "downside" for s in scenarios)

        if not has_upside:
            scenarios.append(
                EnrichedExtremeScenario(
                    scenario=f"超预期利好叠加 — {event_summary}",
                    probability=0.10,
                    impact="+3-5%",
                    direction="upside",
                    trigger_conditions="多重利好同时兑现，市场情绪超预期乐观",
                    early_warning_signals="北向资金大幅流入，成交量放大",
                )
            )
        if not has_downside:
            scenarios.append(
                EnrichedExtremeScenario(
                    scenario=f"利好出尽见光死 — {event_summary}",
                    probability=0.10,
                    impact="-2-4%",
                    direction="downside",
                    trigger_conditions="利好兑现后获利盘涌出，市场情绪急转",
                    early_warning_signals="龙虎榜机构大幅卖出，融资余额下降",
                )
            )

        return scenarios

    @staticmethod
    def _parse_response(raw: str) -> list[EnrichedExtremeScenario]:
        """Parse extreme scenario LLM response."""
        data = extract_deep_json(raw)
        if data is None:
            log.warning("extreme_scenario_parse_failed")
            return []

        scenarios: list[EnrichedExtremeScenario] = []
        for item in data.get("extreme_scenarios", []):
            if not isinstance(item, dict):
                continue
            try:
                scenarios.append(
                    EnrichedExtremeScenario(
                        scenario=str(item.get("scenario", "")),
                        probability=max(
                            0.0,
                            min(
                                1.0,
                                float(item.get("probability", 0.1)),
                            ),
                        ),
                        impact=str(item.get("impact", "")),
                        direction=str(item.get("direction", "")),
                        trigger_conditions=str(
                            item.get("trigger_conditions", "")
                        ),
                        early_warning_signals=str(
                            item.get("early_warning_signals", "")
                        ),
                    )
                )
            except (ValueError, TypeError) as exc:
                log.warning(
                    "extreme_scenario_item_parse_failed",
                    error=str(exc),
                )
                continue

        return scenarios
