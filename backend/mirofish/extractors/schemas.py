"""Frozen Pydantic models for the hidden variable extraction pipeline.

These schemas extend the base MiroFish schemas with richer intermediate
types used by the extraction engine. The pipeline preserves all enriched
fields through to SimulationResult without lossy stringification.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Re-export MomentumShift from canonical location so extractor code
# that imports it from here continues to work without changes.
from backend.mirofish.schemas import MomentumShift as MomentumShift  # noqa: PLC0414


class RawSimulationOutput(BaseModel):
    """Intermediate output from MiroFish simulation calls 1 & 2.

    Wraps persona generation + evolution results before extraction.
    """

    model_config = ConfigDict(frozen=True)

    event_title: str
    event_content: str
    event_sectors: tuple[str, ...] = ()
    event_stocks: tuple[str, ...] = ()
    event_summary: str
    initial_sentiment: dict[str, float]
    sentiment_evolution: tuple[SentimentSnapshotRaw, ...]
    agent_count: int = Field(default=300, ge=50, le=1000)
    rounds: int = Field(default=20, ge=5, le=50)


class SentimentSnapshotRaw(BaseModel):
    """Raw per-round sentiment from evolution simulation."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    bullish: float = Field(ge=0.0, le=1.0)
    bearish: float = Field(ge=0.0, le=1.0)
    neutral: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_sum(self) -> SentimentSnapshotRaw:
        total = self.bullish + self.bearish + self.neutral
        if abs(total - 1.0) > 0.05:
            msg = f"Sentiment values must sum to ~1.0 (got {total:.3f})"
            raise ValueError(msg)
        return self


class SentimentRound(BaseModel):
    """Enriched per-round sentiment with narrative and intensity."""

    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    bullish: float = Field(ge=0.0, le=1.0)
    bearish: float = Field(ge=0.0, le=1.0)
    neutral: float = Field(ge=0.0, le=1.0)
    dominant_narrative: str = ""
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_sum(self) -> SentimentRound:
        total = self.bullish + self.bearish + self.neutral
        if abs(total - 1.0) > 0.05:
            msg = f"Sentiment values must sum to ~1.0 (got {total:.3f})"
            raise ValueError(msg)
        return self


class AgentAction(BaseModel):
    """A simulated agent's expressed action/opinion."""

    model_config = ConfigDict(frozen=True)

    agent_type: str  # "institutional", "analyst", "retail", "speculator"
    action: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class EnrichedHiddenVariable(BaseModel):
    """Hidden variable with full provenance and disclaimer."""

    model_config = ConfigDict(frozen=True)

    variable: str
    probability: float = Field(ge=0.0, le=1.0)
    reasoning: str
    agent_consensus_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    is_absent_from_original: bool = True
    disclaimer: str = (
        "This probability is a simulated crowd wisdom estimate, "
        "NOT a statistically rigorous probability."
    )


class EnrichedInflectionPoint(BaseModel):
    """Inflection point with type, before/after snapshot, and confidence."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=1)
    event: str
    # sentiment_reversal | narrative_convergence | cascade_trigger | exhaustion
    inflection_type: str = ""
    before_sentiment: dict[str, float] = Field(default_factory=dict)
    after_sentiment: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class EnrichedExtremeScenario(BaseModel):
    """Extreme scenario with direction, triggers, and early warnings."""

    model_config = ConfigDict(frozen=True)

    scenario: str
    probability: float = Field(ge=0.0, le=1.0)
    impact: str
    direction: str = ""  # "upside" or "downside"
    trigger_conditions: str = ""
    early_warning_signals: str = ""


class ExtractionResult(BaseModel):
    """Full extraction pipeline output with rich intermediate data.

    Contains all enriched types plus data needed to build SimulationResult.
    """

    model_config = ConfigDict(frozen=True)

    event_summary: str
    sentiment_rounds: tuple[SentimentRound, ...] = ()
    momentum_shifts: tuple[MomentumShift, ...] = ()
    hidden_variables: tuple[EnrichedHiddenVariable, ...] = ()
    inflection_points: tuple[EnrichedInflectionPoint, ...] = ()
    extreme_scenarios: tuple[EnrichedExtremeScenario, ...] = ()
    recommended_action: str = ""


# Resolve forward references
RawSimulationOutput.model_rebuild()
