"""Full-market quant screening (Line 1, Phase L).

Pure, deterministic pre-filter + factor ranking over a PIT market
snapshot. Produces a fixed-size candidate shortlist for the Phase M
multi-agent debate; the LLM never sees the full market. Import isolation
(P0-9-amendment-2026-05-24 §2.5): no ``backend.{llm,agents,mirofish}``.
"""

from backend.screening.factors import (
    FactorVector,
    compute_factors,
)
from backend.screening.screener import (
    DEFAULT_TOP_N_CAP,
    FACTOR_WEIGHTS,
    FEATURE_CODE_VERSION,
    MIN_HISTORY_BARS,
    CandidateRow,
    ExcludedRow,
    ExclusionReason,
    Screener,
    ScreeningError,
    ScreenResult,
)

__all__ = [
    "DEFAULT_TOP_N_CAP",
    "FACTOR_WEIGHTS",
    "FEATURE_CODE_VERSION",
    "MIN_HISTORY_BARS",
    "CandidateRow",
    "ExcludedRow",
    "ExclusionReason",
    "FactorVector",
    "ScreenResult",
    "Screener",
    "ScreeningError",
    "compute_factors",
]
