"""O-003 sector-forecast → per-code advisory signal mapping (pure).

The deterministic bridge between the MiroFish sector forecast (O-002) and
the :class:`~backend.candidate_selector.selector.CandidateSelector`'s
bounded re-rank. It maps a sector-level forecast score onto the
individual candidate codes that belong to that sector — nothing more.

Red-line posture (P0-8-amendment-2026-05-24 §2.3, CandidateSelector
isolation):

* **Pure, primitives-only.** This helper takes plain mappings/iterables
  and never imports ``backend.{mirofish,llm,agents}`` — the
  orchestration layer reads the MiroFish evidence and resolves the
  code→sector map; this function only shapes the result. So the
  candidate_selector package stays free of the LLM/MiroFish stacks.
* **Advisory only.** Output is a tuple of
  :class:`AdvisorySignal`; the selector clamps each magnitude to the
  ≤1-percentile bound and drops the whole re-rank if any displacement
  exceeds it. A code with no sector, or whose sector has no forecast
  score, gets no signal (neutral) — never a qualification change.
* **Quant qualification is authoritative.** This maps scores onto an
  already-qualified candidate set; it can reorder, never admit or evict.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from backend.candidate_selector.selector import AdvisorySignal


def build_advisory_signals(
    *,
    sector_scores: Mapping[str, float],
    sector_by_code: Mapping[str, str],
    candidate_codes: Iterable[str],
) -> tuple[AdvisorySignal, ...]:
    """Map sector forecast scores onto candidate codes.

    Args:
        sector_scores: forecast score per sector name, in [-1, 1]
            (positive = relatively stronger). Out-of-range / non-finite
            scores are ignored fail-closed.
        sector_by_code: candidate code → its sector name.
        candidate_codes: the qualified candidate codes to signal over.

    Returns:
        One :class:`AdvisorySignal` per candidate that (a) maps to a
        sector and (b) whose sector carries a valid, non-zero score.
        Order follows ``candidate_codes`` for determinism. Codes without
        a mapped, scored sector are omitted (treated as neutral by the
        selector). Duplicate codes collapse to their first occurrence.
    """
    clean_scores = {
        sector: score
        for sector, score in sector_scores.items()
        if isinstance(score, int | float)
        and not isinstance(score, bool)
        and math.isfinite(score)
        and -1.0 <= score <= 1.0
        and score != 0.0
    }
    signals: list[AdvisorySignal] = []
    seen: set[str] = set()
    for code in candidate_codes:
        if code in seen:
            continue
        seen.add(code)
        sector = sector_by_code.get(code)
        if sector is None:
            continue
        score = clean_scores.get(sector)
        if score is None:
            continue
        signals.append(AdvisorySignal(code=code, advisory_score=float(score)))
    return tuple(signals)


__all__ = ["build_advisory_signals"]
