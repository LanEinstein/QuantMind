"""Theme four-tier weighting (Phase AC-004).

The value-line bottom tier multiplies a theme's contribution by a weight derived
from its :class:`~backend.theme_research.sop_schema.ThemeTier`: a national-event
catalyst is broader + more durable than a single-stock rumour, so it weighs more
(owner: 国家事件 > 政策 > 技术 > 个股). Initial weights 1.0 / 0.75 / 0.5 / 0.25.

The weights are **config-ised + on the P2-2 evolution whitelist** — an offline
recalibration can move them, but the **monotone order constraint
``tier1 ≥ tier2 ≥ tier3 ≥ tier4`` is an immutable clamp** (a higher tier can
never weigh less than a lower one; that inversion would break the driver
hierarchy). Construction validates the order + the [0, 1] range and is frozen, so
a runtime mutation is impossible.

Pure module: no LLM, no IO, no trading-stack import.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.theme_research.sop_schema import ThemeTier

DEFAULT_THEME_TIER_WEIGHTS: dict[ThemeTier, float] = {
    ThemeTier.NATIONAL_EVENT: 1.0,
    ThemeTier.POLICY: 0.75,
    ThemeTier.TECH: 0.5,
    ThemeTier.STOCK: 0.25,
}
"""Initial tier weights (P0-8-amendment-2026-06-12 §1.3)."""


@dataclass(frozen=True)
class ThemeTierWeights:
    """Immutable tier-weight set with the monotone order clamp.

    ``tier1 >= tier2 >= tier3 >= tier4`` and every weight ∈ [0, 1] are enforced
    at construction (immutable clamp, codex/owner): a recalibration that inverted
    the hierarchy or left the unit range fails closed rather than silently
    over-weighting a lower-tier theme.
    """

    national_event: float = 1.0
    policy: float = 0.75
    tech: float = 0.5
    stock: float = 0.25

    def __post_init__(self) -> None:
        ordered = (self.national_event, self.policy, self.tech, self.stock)
        for name, w in zip(
            ("national_event", "policy", "tech", "stock"), ordered, strict=True
        ):
            if not isinstance(w, int | float) or w != w or not (0.0 <= w <= 1.0):
                raise ValueError(f"tier weight {name!r}={w!r} must be in [0, 1]")
        for hi, lo in zip(ordered, ordered[1:], strict=False):
            if hi < lo:
                raise ValueError(
                    f"tier weights must be monotone non-increasing "
                    f"(tier1≥tier2≥tier3≥tier4), got {ordered}"
                )

    def weight_for(self, tier: ThemeTier) -> float:
        return {
            ThemeTier.NATIONAL_EVENT: self.national_event,
            ThemeTier.POLICY: self.policy,
            ThemeTier.TECH: self.tech,
            ThemeTier.STOCK: self.stock,
        }[tier]


def theme_tier_weight(
    tier: ThemeTier,
    weights: ThemeTierWeights | None = None,
) -> float:
    """Return the [0, 1] weight for ``tier`` (default = initial weights)."""
    return (weights or ThemeTierWeights()).weight_for(tier)


__all__ = [
    "DEFAULT_THEME_TIER_WEIGHTS",
    "ThemeTierWeights",
    "theme_tier_weight",
]
