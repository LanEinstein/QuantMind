"""MiroFish financial simulation adapter.

Replaces MiroFish's heavyweight pipeline (GraphRAG + Zep + OASIS, 4-8 hours)
with 3 structured LLM calls through our LLM Router (~30-60 seconds).
Full OASIS integration planned for Phase 3 (P3-T01).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from backend.agents.base import call_agent
from backend.mirofish.prompts import (
    EVOLUTION_SIMULATION_PROMPT,
    EXTRACTION_PROMPT,
    PERSONA_GENERATION_PROMPT,
)
from backend.mirofish.report_parser import (
    parse_evolution_response,
    parse_extraction_response,
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

    Makes 3 sequential LLM calls via the intelligence_officer agent:
    1. Persona generation + initial sentiment
    2. Multi-round sentiment evolution
    3. Hidden variable extraction + inflection points

    Each call has independent fallback. The simulator never raises;
    it always returns a valid SimulationResult (potentially degraded).
    """

    def __init__(
        self,
        router: LLMRouter,
        config_path: str | Path = _DEFAULT_CONFIG_PATH,
    ) -> None:
        self._router = router
        self._config = self._load_config(Path(config_path))
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
        threshold = self._trigger_threshold
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

        # --- Call 3: Extraction ---
        evolution_text = "\n".join(
            f"Round {s.round}: 看多{s.bullish:.2f} "
            f"看空{s.bearish:.2f} 中性{s.neutral:.2f}"
            for s in evolution
        )
        user_content_3 = (
            f"金融事件: {event_summary}\n"
            f"事件详情: {event.content[:500]}\n"
            f"涉及板块: {', '.join(event.sectors)}\n\n"
            f"情绪演变数据:\n{evolution_text}"
        )
        prompts_text.append(user_content_3)
        raw_3 = await call_agent(
            self._router,
            "intelligence_officer",
            EXTRACTION_PROMPT,
            user_content_3,
        )
        responses_text.append(raw_3)

        extraction = parse_extraction_response(raw_3)
        if not extraction:
            self._log.warning("extraction_parse_failed")
            extraction = {
                "hidden_variables": (),
                "key_inflection_points": (),
                "extreme_scenarios": (),
                "recommended_action": "仿真结果解析失败，请参考其他分析报告",
            }

        duration = time.monotonic() - start
        cost = self._estimate_cost(prompts_text, responses_text)

        return SimulationResult(
            event_summary=event_summary,
            simulation_config=config,
            sentiment_evolution=evolution,
            hidden_variables=extraction["hidden_variables"],
            key_inflection_points=extraction["key_inflection_points"],
            extreme_scenarios=extraction["extreme_scenarios"],
            recommended_action=extraction["recommended_action"],
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

    @property
    def _trigger_threshold(self) -> int:
        """Load trigger threshold from config file."""
        try:
            with _DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f)
            return int(raw.get("simulation", {}).get("trigger_threshold", 7))
        except Exception:
            return 7

    def _estimate_cost(
        self, prompts: list[str], responses: list[str]
    ) -> float:
        """Rough cost estimate based on character counts."""
        try:
            with _DEFAULT_CONFIG_PATH.open("r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f)
            cost_cfg = raw.get("cost_estimate", {})
            chars_per_token = cost_cfg.get("chars_per_token", 1.5)
            input_price = cost_cfg.get("input_price_per_1k", 0.0021)
            output_price = cost_cfg.get("output_price_per_1k", 0.0084)
        except Exception:
            chars_per_token = 1.5
            input_price = 0.0021
            output_price = 0.0084

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
