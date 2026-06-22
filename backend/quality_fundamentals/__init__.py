"""Quality-fundamentals composite for the value-line surface tier (AF-003).

The three-tier value score's surface tier carries a ``fundamentals_score`` that
is stubbed ``None`` in production. This package computes it: a name's earnings
quality — high ROE / gross margin / earnings yield, **low** accruals (earnings
backed by cash, not accrual gaming) — point-in-time by announcement date and
cross-sectionally percentile-ranked into [0, 1].

Pure, deterministic, 0 LLM. Reuses ``backend.screening.value_factors`` for the
PIT selection + percentile helpers; does no IO (the caller supplies
announcement-dated metric records). Must NOT import
``backend.{llm, agents, mirofish}``.
"""

from backend.quality_fundamentals.quality import (
    QualityMetric,
    fundamentals_scores,
    quality_pit_values,
)

__all__ = [
    "QualityMetric",
    "fundamentals_scores",
    "quality_pit_values",
]
