"""Deterministic candidate selector (Phase M).

Reads the quant shortlist + (optional) advisory evidence and emits the final
ordered shortlist the LLM agents debate. The bright line between "MiroFish
advises" and "code decides": advisory evidence may only re-rank within an
already-qualified set; qualification is purely quant. Import isolation
(P0-8-amendment-2026-05-24 §2.3): no ``backend.{llm,agents,mirofish}``.
"""

from backend.candidate_selector.selector import (
    AdvisorySignal,
    CandidateSelection,
    CandidateSelector,
    CandidateSelectorError,
    QuantCandidate,
    SelectorConfig,
    load_selector_config,
    selector_config_with_params,
)

__all__ = [
    "AdvisorySignal",
    "CandidateSelection",
    "CandidateSelector",
    "CandidateSelectorError",
    "QuantCandidate",
    "SelectorConfig",
    "load_selector_config",
    "selector_config_with_params",
]
