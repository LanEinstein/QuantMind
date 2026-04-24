"""Frozen Pydantic models for MiroFish simulation I/O.

Schema matches QuantMind Blueprint V3 section 3.3 exactly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MomentumDirection = Literal["bullish_to_bearish", "bearish_to_bullish", ""]
InflectionTypeLiteral = Literal[
    "sentiment_reversal",
    "narrative_convergence",
    "cascade_trigger",
    "exhaustion",
    "",
]
ScenarioDirection = Literal["upside", "downside", ""]


class EventDescription(BaseModel):
    """Input: a financial event to simulate."""

    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    importance_score: int = Field(ge=0, le=10)
    sectors: tuple[str, ...] = ()
    stocks: tuple[str, ...] = ()


class SimulationConfig(BaseModel):
    """Simulation parameters."""

    model_config = ConfigDict(frozen=True)

    agent_count: int = Field(default=300, ge=50, le=1000)
    rounds: int = Field(default=20, ge=5, le=50)
    model: str = "kimi-k2.6"


class MomentumShift(BaseModel):
    """Detected momentum shift between consecutive simulation rounds."""

    model_config = ConfigDict(frozen=True)

    round_number: int = Field(ge=2)
    direction: MomentumDirection = ""
    magnitude: float = Field(ge=0.0, le=1.0)
    trigger_narrative: str = ""


class SentimentSnapshot(BaseModel):
    """Sentiment distribution for a single simulation round."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    bullish: float = Field(ge=0.0, le=1.0)
    bearish: float = Field(ge=0.0, le=1.0)
    neutral: float = Field(ge=0.0, le=1.0)
    dominant_narrative: str = ""
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_sum(self) -> SentimentSnapshot:
        total = self.bullish + self.bearish + self.neutral
        if abs(total - 1.0) > 0.05:
            msg = (
                f"Sentiment values must sum to ~1.0 "
                f"(got {total:.3f})"
            )
            raise ValueError(msg)
        return self


class HiddenVariable(BaseModel):
    """An emergent hidden variable discovered during simulation."""

    model_config = ConfigDict(frozen=True)

    variable: str
    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str
    agent_consensus_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    is_absent_from_original: bool = True


class InflectionPoint(BaseModel):
    """A key inflection point in the simulated timeline."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=1)
    event: str
    inflection_type: InflectionTypeLiteral = ""
    before_sentiment: dict[str, float] = Field(default_factory=dict)
    after_sentiment: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtremeScenario(BaseModel):
    """An extreme scenario with probability and impact estimate."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    probability: float = Field(ge=0.0, le=1.0)
    impact: str
    direction: ScenarioDirection = ""
    trigger_conditions: str = ""
    early_warning_signals: str = ""


class SimulationResult(BaseModel):
    """Complete output of a MiroFish simulation run.

    Conforms to Blueprint V3 section 3.3 JSON schema.
    """

    model_config = ConfigDict(frozen=True)

    event_summary: str
    simulation_config: SimulationConfig
    sentiment_evolution: tuple[SentimentSnapshot, ...]
    hidden_variables: tuple[HiddenVariable, ...]
    key_inflection_points: tuple[InflectionPoint, ...]
    extreme_scenarios: tuple[ExtremeScenario, ...]
    momentum_shifts: tuple[MomentumShift, ...] = ()
    recommended_action: str
    cost_rmb: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
