"""Inflection point detector for MiroFish simulation output.

Detects key turning points where simulated market dynamics shift,
using sentiment evolution data and hidden variable emergence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.extractors.schemas import (
    EnrichedHiddenVariable,
    EnrichedInflectionPoint,
    SentimentRound,
)
from backend.mirofish.prompts import INFLECTION_POINT_PROMPT
from backend.mirofish.report_parser import extract_deep_json

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.extractor.inflection_points")


class InflectionPointDetector:
    """Detect key turning points in simulated market trajectory."""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def extract(
        self,
        evolution: tuple[SentimentRound, ...],
        hidden_vars: tuple[EnrichedHiddenVariable, ...],
    ) -> tuple[EnrichedInflectionPoint, ...]:
        """Identify critical moments where market dynamics shift.

        Detection methods:
        1. Sentiment reversal: bullish/bearish ratio crosses 50%
        2. Narrative convergence: >60% of agents align on same theme
        3. Cascade trigger: hidden variable goes viral among agents
        4. Exhaustion point: sentiment intensity drops sharply

        Args:
            evolution: Enriched sentiment rounds.
            hidden_vars: Extracted hidden variables for cascade detection.

        Returns:
            Tuple of detected inflection points.
        """
        if not evolution:
            return ()

        evolution_text = "\n".join(
            f"Round {r.round}: bullish={r.bullish:.2f} "
            f"bearish={r.bearish:.2f} neutral={r.neutral:.2f} "
            f"narrative='{r.dominant_narrative}' "
            f"intensity={r.intensity:.2f}"
            for r in evolution
        )

        hidden_vars_text = "\n".join(
            f"- {hv.variable} (probability={hv.probability:.2f}, "
            f"consensus={hv.agent_consensus_ratio:.2f})"
            for hv in hidden_vars
        ) or "无隐性变量"

        user_content = (
            f"=== 情绪演变数据 ===\n{evolution_text}\n\n"
            f"=== 隐性变量 ===\n{hidden_vars_text}"
        )

        raw_response = await call_agent(
            self._router,
            "intelligence_officer",
            INFLECTION_POINT_PROMPT,
            user_content,
        )

        points = self._parse_response(raw_response)

        # Fallback: detect inflection from raw data if LLM failed
        if not points and len(evolution) >= 2:
            points = list(self._detect_from_data(evolution))

        log.info(
            "inflection_point_detection_complete", count=len(points)
        )
        return tuple(points)

    @staticmethod
    def _detect_from_data(
        evolution: tuple[SentimentRound, ...],
    ) -> list[EnrichedInflectionPoint]:
        """Heuristic inflection detection from raw sentiment data.

        Used as fallback when LLM extraction fails.
        """
        points: list[EnrichedInflectionPoint] = []
        for i in range(1, len(evolution)):
            prev = evolution[i - 1]
            curr = evolution[i]

            # Sentiment reversal: bullish crosses 50%
            prev_bull_dominant = prev.bullish > 0.5
            curr_bull_dominant = curr.bullish > 0.5
            if prev_bull_dominant != curr_bull_dominant:
                day = curr.round * 2  # ~2 trading days per round
                points.append(
                    EnrichedInflectionPoint(
                        day=day,
                        event=(
                            f"情绪反转: 看多从{prev.bullish:.0%}"
                            f"变为{curr.bullish:.0%}"
                        ),
                        inflection_type="sentiment_reversal",
                        before_sentiment={
                            "bullish": prev.bullish,
                            "bearish": prev.bearish,
                            "neutral": prev.neutral,
                        },
                        after_sentiment={
                            "bullish": curr.bullish,
                            "bearish": curr.bearish,
                            "neutral": curr.neutral,
                        },
                        confidence=0.6,
                    )
                )

            # Exhaustion: intensity drops > 0.2
            intensity_drop = prev.intensity - curr.intensity
            if intensity_drop > 0.2:
                day = curr.round * 2
                points.append(
                    EnrichedInflectionPoint(
                        day=day,
                        event=(
                            f"情绪耗竭: intensity从{prev.intensity:.2f}"
                            f"降至{curr.intensity:.2f}"
                        ),
                        inflection_type="exhaustion",
                        before_sentiment={
                            "bullish": prev.bullish,
                            "bearish": prev.bearish,
                            "neutral": prev.neutral,
                        },
                        after_sentiment={
                            "bullish": curr.bullish,
                            "bearish": curr.bearish,
                            "neutral": curr.neutral,
                        },
                        confidence=0.5,
                    )
                )

        return points

    @staticmethod
    def _parse_response(raw: str) -> list[EnrichedInflectionPoint]:
        """Parse inflection point LLM response."""
        data = extract_deep_json(raw)
        if data is None:
            log.warning("inflection_point_parse_failed")
            return []

        points: list[EnrichedInflectionPoint] = []
        for item in data.get("inflection_points", []):
            if not isinstance(item, dict):
                continue
            try:
                before = item.get("before_sentiment", {})
                after = item.get("after_sentiment", {})
                points.append(
                    EnrichedInflectionPoint(
                        day=max(1, int(item.get("day", 1))),
                        event=str(item.get("event", "")),
                        inflection_type=str(
                            item.get("inflection_type", "")
                        ),
                        before_sentiment={
                            "bullish": float(
                                before.get("bullish", 0.33)
                            ),
                            "bearish": float(
                                before.get("bearish", 0.33)
                            ),
                            "neutral": float(
                                before.get("neutral", 0.34)
                            ),
                        },
                        after_sentiment={
                            "bullish": float(
                                after.get("bullish", 0.33)
                            ),
                            "bearish": float(
                                after.get("bearish", 0.33)
                            ),
                            "neutral": float(
                                after.get("neutral", 0.34)
                            ),
                        },
                        confidence=max(
                            0.0,
                            min(
                                1.0,
                                float(item.get("confidence", 0.5)),
                            ),
                        ),
                    )
                )
            except (ValueError, TypeError) as exc:
                log.warning(
                    "inflection_point_item_parse_failed",
                    error=str(exc),
                )
                continue

        return points
