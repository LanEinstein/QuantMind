"""Value-score assembly orchestrator (AF-002).

The pivot that turns the three foundation modules into a live ``value_scores``
map for the CandidateSelector / StyleClassifier value path:

* AF-001 ``backend.theme_mapping`` — pinned national-strategy theme coverage;
* AF-003 ``backend.quality_fundamentals`` — earnings-quality composite (fed by
  the AF-002 ``backend.fundamentals_pit`` PIT statement reader);
* AF-002 valuation factor (this package) — high-dividend / low PE-PB cheapness.

It assembles a :class:`~backend.screening.value_score.ValueScoreInputs` per
candidate, runs the deterministic three-tier composite, and returns a
``dict[code, value_score]``. Pure, deterministic, 0 LLM, import-isolated. The
production Line-1 path passes ``value_scores=None`` (value sleeve dormant) → the
selector stays bit-identical; this assembler only runs once the sleeve activates.
"""

from __future__ import annotations

from backend.value_assembly.assembler import ValueScoreAssembler
from backend.value_assembly.valuation import valuation_scores

__all__ = [
    "ValueScoreAssembler",
    "valuation_scores",
]
