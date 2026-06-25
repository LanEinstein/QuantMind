"""Deterministic quality-fundamentals composite (AF-003).

``fundamentals_scores`` maps a candidate cross-section → a [0, 1] quality score
per code (or ``None`` when no metric is available, so the value-score tier mean
drops the component rather than inventing a low quality — the AF-001 convention).
Each metric is PIT-selected (announced on/before the decision date) then
cross-sectionally percentile-ranked with its sign (high ROE good, low accruals
good); the composite is the mean of a code's present ranks.

The caller supplies, per code, ``(announce_date, value)`` records for each
metric (e.g. earnings yield = ttm earnings / market cap, accruals =
(net income − operating cash flow) / assets — both computed upstream from the
PIT statement snapshots). This module never reads IO and never recomputes raw
units, so it replays bit-exact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from backend.screening.value_factors import (
    clamp01,
    percentile_rank,
    pit_fundamentals_value,
)


class QualityMetric(StrEnum):
    """The earnings-quality metrics blended into ``fundamentals_score``."""

    ROE = "roe"  # return on equity — higher better
    GPM = "gpm"  # gross profit margin — higher better
    EP_TTM = "ep_ttm"  # earnings yield (E/P, ttm) — higher better
    ACCRUALS = "accruals"  # accrual ratio — LOWER better (cash-backed earnings)


# Sign convention: every metric is "higher is better" except accruals, where a
# low accrual ratio signals earnings backed by cash rather than accrual gaming.
_HIGHER_IS_BETTER: dict[QualityMetric, bool] = {
    QualityMetric.ROE: True,
    QualityMetric.GPM: True,
    QualityMetric.EP_TTM: True,
    QualityMetric.ACCRUALS: False,
}

# Per code, each metric's announcement-dated (YYYY-MM-DD, value) records.
MetricRecords = Mapping[QualityMetric, Sequence[tuple[str, float]]]


def quality_pit_values(
    records: MetricRecords, as_of_date: str
) -> dict[QualityMetric, float | None]:
    """PIT as-known value per metric (latest announced **strictly before**
    ``as_of_date``).

    Keyed by announcement date, so a quarter not yet disclosed by the decision
    date can never leak in. The cutoff is strict-exclusive
    (P0-8-amendment-2026-06-25 / M2): a report announced *on* the decision date
    is excluded, matching the research PIT convention. ``None`` for a metric
    with no qualifying vintage.
    """
    return {
        metric: pit_fundamentals_value(recs, as_of_date)
        for metric, recs in records.items()
    }


def fundamentals_scores(
    records_by_code: Mapping[str, MetricRecords], as_of_date: str
) -> dict[str, float | None]:
    """Per-code quality composite ∈ [0, 1], or ``None`` when no metric is known.

    Cross-sectional: each metric is ranked against the PIT values of all codes,
    then a code's composite is the mean of its present (signed) ranks. A code
    with no PIT metric maps to ``None`` (drop the component, don't fabricate 0.0).
    """
    pit: dict[str, dict[QualityMetric, float | None]] = {
        code: quality_pit_values(recs, as_of_date)
        for code, recs in records_by_code.items()
    }
    populations: dict[QualityMetric, list[float]] = {
        metric: [v for vals in pit.values() if (v := vals.get(metric)) is not None]
        for metric in QualityMetric
    }
    scores: dict[str, float | None] = {}
    for code, vals in pit.items():
        ranks: list[float] = []
        for metric in QualityMetric:
            value = vals.get(metric)
            if value is None:
                continue
            rank = percentile_rank(
                value,
                populations[metric],
                higher_is_better=_HIGHER_IS_BETTER[metric],
            )
            if rank is not None:
                ranks.append(rank)
        scores[code] = clamp01(sum(ranks) / len(ranks)) if ranks else None
    return scores


__all__ = [
    "QualityMetric",
    "fundamentals_scores",
    "quality_pit_values",
]
