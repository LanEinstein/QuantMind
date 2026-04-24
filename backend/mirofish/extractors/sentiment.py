"""Sentiment evolution tracker for MiroFish simulation output.

Enriches raw per-round sentiment with dominant narratives and intensity,
and detects momentum shifts between consecutive rounds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from backend.agents.base import call_agent
from backend.mirofish.extractors.schemas import (
    MomentumShift,
    RawSimulationOutput,
    SentimentRound,
)
from backend.mirofish.prompts import SENTIMENT_CLASSIFICATION_PROMPT
from backend.mirofish.report_parser import extract_deep_json

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.extractor.sentiment")

_MOMENTUM_THRESHOLD = 0.15  # 15% bullish ratio change = momentum shift


class SentimentEvolutionTracker:
    """Track how market sentiment evolves across simulation rounds."""

    def __init__(self, router: LLMRouter) -> None:
        self._router = router

    async def extract(
        self, raw_simulation: RawSimulationOutput
    ) -> tuple[SentimentRound, ...]:
        """Enrich each simulation round with dominant narrative and intensity.

        Uses Kimi K2.6 to classify sentiment characteristics per round,
        then merges with raw bullish/bearish/neutral ratios.

        Args:
            raw_simulation: Raw output from calls 1 & 2 of the simulator.

        Returns:
            Tuple of enriched SentimentRound objects.
        """
        evolution = raw_simulation.sentiment_evolution
        if not evolution:
            return ()

        # Build batch context for LLM classification
        evolution_text = "\n".join(
            f"Round {s.round}: bullish={s.bullish:.2f} "
            f"bearish={s.bearish:.2f} neutral={s.neutral:.2f}"
            for s in evolution
        )
        user_content = (
            f"金融事件: {raw_simulation.event_summary}\n"
            f"事件详情: {raw_simulation.event_content[:500]}\n"
            f"涉及板块: {', '.join(raw_simulation.event_sectors)}\n\n"
            f"情绪演变数据:\n{evolution_text}"
        )

        raw_response = await call_agent(
            self._router,
            "intelligence_officer",
            SENTIMENT_CLASSIFICATION_PROMPT,
            user_content,
        )

        narratives = self._parse_classification(raw_response, len(evolution))

        rounds: list[SentimentRound] = []
        for snapshot in evolution:
            enrichment = narratives.get(snapshot.round, {})
            rounds.append(
                SentimentRound(
                    round=snapshot.round,
                    bullish=snapshot.bullish,
                    bearish=snapshot.bearish,
                    neutral=snapshot.neutral,
                    dominant_narrative=enrichment.get(
                        "dominant_narrative", ""
                    ),
                    intensity=enrichment.get(
                        "intensity",
                        self._estimate_intensity(snapshot),
                    ),
                )
            )

        log.info("sentiment_extraction_complete", rounds=len(rounds))
        return tuple(rounds)

    async def detect_momentum_shift(
        self, evolution: tuple[SentimentRound, ...]
    ) -> tuple[MomentumShift, ...]:
        """Detect rounds where sentiment direction reverses significantly.

        A momentum shift occurs when bullish ratio changes by more than
        15% between consecutive rounds.

        Args:
            evolution: Enriched sentiment rounds from extract().

        Returns:
            Tuple of detected MomentumShift objects.
        """
        if len(evolution) < 2:
            return ()

        shifts: list[MomentumShift] = []
        for i in range(1, len(evolution)):
            prev = evolution[i - 1]
            curr = evolution[i]
            delta = curr.bullish - prev.bullish

            if abs(delta) < _MOMENTUM_THRESHOLD:
                continue

            # Only report a reversal when dominance actually flips
            prev_bull_dominant = prev.bullish > prev.bearish
            curr_bull_dominant = curr.bullish > curr.bearish
            if prev_bull_dominant == curr_bull_dominant:
                continue

            direction = (
                "bearish_to_bullish" if delta > 0 else "bullish_to_bearish"
            )
            shifts.append(
                MomentumShift(
                    round_number=curr.round,
                    direction=direction,
                    magnitude=round(abs(delta), 3),
                    trigger_narrative=curr.dominant_narrative,
                )
            )

        log.info("momentum_shifts_detected", count=len(shifts))
        return tuple(shifts)

    @staticmethod
    def _parse_classification(
        raw: str, expected_rounds: int
    ) -> dict[int, dict[str, object]]:
        """Parse LLM sentiment classification response.

        Returns:
            Dict mapping round number to {dominant_narrative, intensity}.
        """
        data = extract_deep_json(raw)
        if data is None:
            log.warning("sentiment_classification_parse_failed")
            return {}

        result: dict[int, dict[str, object]] = {}
        for item in data.get("rounds", []):
            if not isinstance(item, dict):
                continue
            try:
                round_num = int(item.get("round", 0))
                if round_num < 1:
                    continue
                result[round_num] = {
                    "dominant_narrative": str(
                        item.get("dominant_narrative", "")
                    ),
                    "intensity": max(
                        0.0, min(1.0, float(item.get("intensity", 0.5)))
                    ),
                }
            except (ValueError, TypeError) as exc:
                log.warning(
                    "sentiment_round_parse_failed", error=str(exc)
                )
                continue

        return result

    @staticmethod
    def _estimate_intensity(snapshot: object) -> float:
        """Heuristic intensity estimate when LLM classification fails.

        Higher when sentiment is polarized (bullish or bearish dominant).
        Lower when neutral is dominant.
        """
        bullish = getattr(snapshot, "bullish", 0.33)
        bearish = getattr(snapshot, "bearish", 0.33)
        neutral = getattr(snapshot, "neutral", 0.34)
        max_directional = max(bullish, bearish)
        if neutral > 0.5:
            return round(0.3 * max_directional / 0.5, 3)
        return round(min(1.0, max_directional * 1.3), 3)
