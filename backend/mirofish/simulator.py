"""MiroFish financial simulation adapter.

Replaces MiroFish's heavyweight pipeline (GraphRAG + Zep + OASIS, 4-8 hours)
with 2 structured LLM calls for persona/evolution, then delegates extraction
to the HiddenVariableExtractionPipeline (~30-60 seconds total).
Full OASIS integration planned for Phase 3 (P3-T01).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from backend.agents.base import call_agent
from backend.mirofish.extractors import HiddenVariableExtractionPipeline
from backend.mirofish.extractors.schemas import (
    RawSimulationOutput,
    SentimentSnapshotRaw,
)
from backend.mirofish.prompts import (
    EVOLUTION_SIMULATION_PROMPT,
    PERSONA_GENERATION_PROMPT,
)
from backend.mirofish.report_parser import (
    parse_evolution_response,
    parse_persona_response,
)
from backend.mirofish.schemas import (
    EventDescription,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="mirofish.simulator")

_DEFAULT_CONFIG_PATH = Path("config/mirofish.yaml")


class MiroFishSimulator:
    """LLM-driven financial event simulation adapter.

    Makes 2 sequential LLM calls via the intelligence_officer agent:
    1. Persona generation + initial sentiment
    2. Multi-round sentiment evolution

    Then delegates extraction to HiddenVariableExtractionPipeline which
    runs 4 specialized extractors + 1 recommendation generator.

    Each step has independent fallback. The simulator never raises;
    it always returns a valid SimulationResult (potentially degraded).
    """

    def __init__(
        self,
        router: LLMRouter,
        config_path: str | Path = _DEFAULT_CONFIG_PATH,
    ) -> None:
        self._router = router
        self._config_path = Path(config_path)
        self._config = self._load_config(self._config_path)
        self._trigger_threshold_value = self._read_threshold(self._config_path)
        self._cost_params = self._read_cost_params(self._config_path)
        self._extraction_pipeline = HiddenVariableExtractionPipeline(router)
        self._log = log

    async def run_simulation(
        self, event: EventDescription
    ) -> SimulationResult:
        """Run a financial event simulation.

        Args:
            event: Structured description of the financial event.

        Returns:
            SimulationResult with sentiment evolution, hidden variables,
            inflection points, and extreme scenarios.
        """
        config = self._config

        # Gate: skip simulation for low-importance events
        threshold = self._trigger_threshold_value
        if event.importance_score < threshold:
            self._log.info(
                "simulation_skipped",
                score=event.importance_score,
                threshold=threshold,
            )
            return self._skipped_result(event, config)

        start = time.monotonic()
        prompts_text: list[str] = []
        responses_text: list[str] = []

        # --- Call 1: Persona generation ---
        user_content_1 = (
            f"金融事件标题: {event.title}\n"
            f"事件内容: {event.content}\n"
            f"涉及板块: {', '.join(event.sectors)}\n"
            f"涉及个股: {', '.join(event.stocks)}\n"
            f"模拟参与者数量: {config.agent_count}"
        )
        prompts_text.append(user_content_1)
        raw_1 = await call_agent(
            self._router,
            "intelligence_officer",
            PERSONA_GENERATION_PROMPT,
            user_content_1,
        )
        responses_text.append(raw_1)

        parsed_1 = parse_persona_response(raw_1)
        if parsed_1:
            event_summary, initial_sentiment = parsed_1
        else:
            self._log.warning("persona_parse_failed_using_fallback")
            event_summary = event.title
            initial_sentiment = {
                "bullish": 0.33,
                "bearish": 0.33,
                "neutral": 0.34,
            }

        # --- Call 2: Evolution simulation ---
        user_content_2 = (
            f"金融事件: {event_summary}\n"
            f"事件详情: {event.content[:500]}\n"
            f"初始情绪分布: 看多{initial_sentiment['bullish']:.2f} "
            f"看空{initial_sentiment['bearish']:.2f} "
            f"中性{initial_sentiment['neutral']:.2f}\n"
            f"模拟轮数: {config.rounds}\n"
            f"参与者数量: {config.agent_count}"
        )
        prompts_text.append(user_content_2)
        raw_2 = await call_agent(
            self._router,
            "intelligence_officer",
            EVOLUTION_SIMULATION_PROMPT,
            user_content_2,
        )
        responses_text.append(raw_2)

        evolution = parse_evolution_response(raw_2)
        if not evolution:
            self._log.warning("evolution_parse_failed_using_synthetic")
            evolution = self._build_fallback_evolution(
                initial_sentiment, config.rounds
            )

        # --- Extraction pipeline (replaces monolithic call 3) ---
        raw_sim = RawSimulationOutput(
            event_title=event.title,
            event_content=event.content,
            event_sectors=event.sectors,
            event_stocks=event.stocks,
            event_summary=event_summary,
            initial_sentiment=initial_sentiment,
            sentiment_evolution=tuple(
                SentimentSnapshotRaw(
                    round=s.round,
                    bullish=s.bullish,
                    bearish=s.bearish,
                    neutral=s.neutral,
                )
                for s in evolution
            ),
            agent_count=config.agent_count,
            rounds=config.rounds,
        )

        try:
            extraction = await self._extraction_pipeline.extract_all(
                raw_sim
            )
            duration = time.monotonic() - start
            # Cost includes persona + evolution calls. The extraction
            # pipeline makes 5 additional calls; estimate their cost
            # as ~2.5x the average of the first 2 calls (extraction
            # prompts are similar-sized but there are 5 of them).
            base_cost = self._estimate_cost(prompts_text, responses_text)
            pipeline_multiplier = 3.5  # 2 base + 5 pipeline ≈ 3.5x
            total_cost = round(base_cost * pipeline_multiplier, 4)

            return self._extraction_pipeline.to_simulation_result(
                extraction,
                config,
                cost_rmb=total_cost,
                duration_seconds=round(duration, 2),
            )
        except Exception as exc:
            self._log.warning(
                "extraction_pipeline_failed", error=str(exc)
            )
            duration = time.monotonic() - start
            cost = self._estimate_cost(prompts_text, responses_text)

            return SimulationResult(
                event_summary=event_summary,
                simulation_config=config,
                sentiment_evolution=evolution,
                hidden_variables=(),
                key_inflection_points=(),
                extreme_scenarios=(),
                recommended_action="提取管道执行失败，请参考其他分析报告",
                cost_rmb=cost,
                duration_seconds=round(duration, 2),
            )

    def _load_config(self, path: Path) -> SimulationConfig:
        """Load simulation config from YAML."""
        try:
            with path.open("r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f)
            sim = raw.get("simulation", {})
            return SimulationConfig(
                agent_count=sim.get("agent_count", 300),
                rounds=sim.get("rounds", 20),
                model=sim.get("model", "MiniMax-M2.5"),
            )
        except Exception as exc:
            self._log.warning("config_load_failed", error=str(exc))
            return SimulationConfig()

    @staticmethod
    def _read_threshold(path: Path) -> int:
        """Read trigger threshold once at init time."""
        try:
            with path.open("r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f)
            return int(raw.get("simulation", {}).get("trigger_threshold", 7))
        except Exception:
            return 7

    @staticmethod
    def _read_cost_params(
        path: Path,
    ) -> tuple[float, float, float]:
        """Read cost estimation params once at init time."""
        try:
            with path.open("r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f)
            cfg = raw.get("cost_estimate", {})
            return (
                cfg.get("chars_per_token", 1.5),
                cfg.get("input_price_per_1k", 0.0021),
                cfg.get("output_price_per_1k", 0.0084),
            )
        except Exception:
            return (1.5, 0.0021, 0.0084)

    def _estimate_cost(
        self, prompts: list[str], responses: list[str]
    ) -> float:
        """Rough cost estimate based on character counts."""
        chars_per_token, input_price, output_price = self._cost_params
        input_chars = sum(len(p) for p in prompts)
        output_chars = sum(len(r) for r in responses)
        input_tokens = input_chars / chars_per_token
        output_tokens = output_chars / chars_per_token

        return round(
            (input_tokens / 1000) * input_price
            + (output_tokens / 1000) * output_price,
            4,
        )

    def _skipped_result(
        self, event: EventDescription, config: SimulationConfig
    ) -> SimulationResult:
        """Return a minimal result for events below threshold."""
        return SimulationResult(
            event_summary=event.title,
            simulation_config=config,
            sentiment_evolution=(),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            recommended_action="事件重要性不足，未触发仿真",
            cost_rmb=0.0,
            duration_seconds=0.0,
        )

    @staticmethod
    def _build_fallback_evolution(
        initial: dict[str, float], rounds: int
    ) -> tuple[SentimentSnapshot, ...]:
        """Generate a synthetic linear evolution as degraded fallback."""
        bull_start = initial.get("bullish", 0.33)
        bear_start = initial.get("bearish", 0.33)

        # Slight drift toward neutral over rounds
        snapshots: list[SentimentSnapshot] = []
        for i in range(1, rounds + 1):
            t = i / rounds
            bull = bull_start + (0.33 - bull_start) * t * 0.3
            bear = bear_start + (0.33 - bear_start) * t * 0.3
            neut = 1.0 - bull - bear
            snapshots.append(
                SentimentSnapshot(
                    round=i,
                    bullish=round(bull, 3),
                    bearish=round(bear, 3),
                    neutral=round(neut, 3),
                )
            )
        return tuple(snapshots)
