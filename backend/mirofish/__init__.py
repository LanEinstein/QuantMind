"""MiroFish financial simulation adapter."""

from backend.mirofish.event_filter import extract_key_events
from backend.mirofish.extractors import HiddenVariableExtractionPipeline
from backend.mirofish.formatter import format_simulation_context
from backend.mirofish.output_writer import (
    EVENT_DRIVEN_DAILY_CAP,
    HIGH_SEVERITY_THRESHOLD,
    MiroFishEvidence,
    MiroFishEvidenceError,
    MiroFishEvidenceWriter,
    build_eod_evidence,
    build_event_evidence,
    is_high_severity_event,
)
from backend.mirofish.schemas import (
    EventDescription,
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)
from backend.mirofish.simulator import MiroFishSimulator

__all__ = [
    "EVENT_DRIVEN_DAILY_CAP",
    "EventDescription",
    "ExtremeScenario",
    "HIGH_SEVERITY_THRESHOLD",
    "HiddenVariable",
    "HiddenVariableExtractionPipeline",
    "InflectionPoint",
    "MiroFishEvidence",
    "MiroFishEvidenceError",
    "MiroFishEvidenceWriter",
    "MiroFishSimulator",
    "SentimentSnapshot",
    "SimulationConfig",
    "SimulationResult",
    "build_eod_evidence",
    "build_event_evidence",
    "extract_key_events",
    "format_simulation_context",
    "is_high_severity_event",
]
