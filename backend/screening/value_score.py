"""Three-tier value-line composite (Phase AC-003).

Combines the bottom / mid / surface tier signals (each a normalised [0, 1]
component set produced upstream by :mod:`backend.screening.value_factors` + the
cross-sectional percentile helper) into one deterministic ``value_score`` ∈
[0, 1]. The StyleClassifier gates VALUE on this score (AC-001 / AC-005).

* **Bottom · 大势主线**: pinned-THEME coverage + sector momentum percentile +
  the existing deterministic regime channel.
* **Mid · 资金认可+容量**: event-study CAR + capacity + turnover/northbound
  percentiles + (inverted) Amihud illiquidity — all already percentile-ranked
  against the candidate cross-section.
* **Surface · 共振+弹性**: independent-evidence-family resonance + PIT
  fundamentals support + elasticity (beta/amplitude/free-float).

Pure, deterministic, 0 LLM, import-isolated. A tier score is the equal-weight
mean of its **present** (non-None) components — a missing component (no pinned
theme, no fundamentals) lowers the tier conservatively rather than being
fabricated, so the no-pin path stays bit-identical to the legacy SHORT_TERM
behaviour (the value gate is simply never cleared). The theme component carries
the AC-004 ``theme_tier`` weight (passed pre-multiplied by the caller).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.screening.value_factors import clamp01

VALUE_SCORE_FEATURE_VERSION = "screening.value_score/v1"
"""Pinned composite-code version; bump on any maths change (stale replay fails)."""


def _tier_mean(components: tuple[float | None, ...]) -> float:
    """Equal-weight mean of present [0,1] components (0.0 if none present)."""
    present = [clamp01(c) for c in components if c is not None]
    if not present:
        return 0.0
    return sum(present) / len(present)


@dataclass(frozen=True)
class ValueScoreInputs:
    """Normalised [0, 1] tier-component signals for one candidate.

    Every field is already cross-sectionally normalised (percentile) or a
    bounded sub-score; ``None`` = the component is unavailable for this name and
    is dropped from its tier mean (conservative). The composite never re-reads
    raw units, so it replays bit-exact.
    """

    # Bottom · 大势主线
    theme_coverage: float | None = None
    sector_momentum_pct: float | None = None
    regime_score: float | None = None
    # Mid · 资金认可+容量
    abnormal_return_pct: float | None = None
    capacity_pct: float | None = None
    liquidity_pct: float | None = None  # inverted Amihud (higher = more liquid)
    turnover_pct: float | None = None
    capital_flow_pct: float | None = None  # northbound / main-capital
    # Surface · 共振+弹性
    resonance_score: float | None = None
    fundamentals_score: float | None = None
    elasticity_score: float | None = None


@dataclass(frozen=True)
class ValueScoreWeights:
    """Tier weights for the composite (normalised internally; non-negative).

    Frozen + validated: a runtime mutation is forbidden, a change is an offline
    recalibration (P2-2 evolution whitelist + shadow + human gate). The weights
    are normalised to sum 1 at construction so the composite is always ∈ [0, 1].
    """

    bottom: float = 0.34
    mid: float = 0.33
    surface: float = 0.33

    def __post_init__(self) -> None:
        for name in ("bottom", "mid", "surface"):
            w = getattr(self, name)
            if not isinstance(w, int | float) or w < 0 or w != w:  # NaN guard
                raise ValueError(f"value-score weight {name!r}={w!r} must be >= 0")
        if self.bottom + self.mid + self.surface <= 0:
            raise ValueError("value-score weights must sum to > 0")

    def normalised(self) -> tuple[float, float, float]:
        total = self.bottom + self.mid + self.surface
        return (self.bottom / total, self.mid / total, self.surface / total)


@dataclass(frozen=True)
class ValueScore:
    """The deterministic three-tier verdict + per-tier breakdown."""

    value_score: float
    bottom: float
    mid: float
    surface: float
    feature_version: str = VALUE_SCORE_FEATURE_VERSION
    components_present: tuple[str, ...] = field(default_factory=tuple)


_BOTTOM_FIELDS = ("theme_coverage", "sector_momentum_pct", "regime_score")
_MID_FIELDS = (
    "abnormal_return_pct",
    "capacity_pct",
    "liquidity_pct",
    "turnover_pct",
    "capital_flow_pct",
)
_SURFACE_FIELDS = ("resonance_score", "fundamentals_score", "elasticity_score")


def compute_value_score(
    inputs: ValueScoreInputs,
    weights: ValueScoreWeights | None = None,
) -> ValueScore:
    """Deterministic three-tier value composite ∈ [0, 1].

    Pure + total: each tier is the equal-weight mean of its present components,
    the composite is the weight-normalised blend of the three tiers. Replays
    bit-exact from the same inputs + weights.
    """
    w = weights or ValueScoreWeights()
    wb, wm, ws = w.normalised()
    bottom = _tier_mean(tuple(getattr(inputs, f) for f in _BOTTOM_FIELDS))
    mid = _tier_mean(tuple(getattr(inputs, f) for f in _MID_FIELDS))
    surface = _tier_mean(tuple(getattr(inputs, f) for f in _SURFACE_FIELDS))
    score = clamp01(wb * bottom + wm * mid + ws * surface)
    present = tuple(
        f
        for f in (*_BOTTOM_FIELDS, *_MID_FIELDS, *_SURFACE_FIELDS)
        if getattr(inputs, f) is not None
    )
    return ValueScore(
        value_score=score,
        bottom=bottom,
        mid=mid,
        surface=surface,
        components_present=present,
    )


__all__ = [
    "VALUE_SCORE_FEATURE_VERSION",
    "ValueScore",
    "ValueScoreInputs",
    "ValueScoreWeights",
    "compute_value_score",
]
