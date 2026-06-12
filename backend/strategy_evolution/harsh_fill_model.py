"""Harsh fill model — anti-MockBroker-overfit shadow execution
(AB-007 / P2-2-amendment-2026-06-12 §1.1(5); codex P2-5).

Evolution optimising against MockBroker's friendly quirks is a real
incentive loop: a challenger that "wins" by exploiting ALL_OR_NONE
fills at the quoted price would promote on simulator artefacts. Shadow
EVALUATION therefore prices orders under deliberately harsher
assumptions — limit-board no-fill, ADV participation caps, linear price
impact, stale-quote rejection, optional next-bar delayed fills.

This module is used by the SHADOW EVALUATION PATH ONLY. The live
MockBroker matching engine is untouched (the AB-008 adversarial test
pins bit-identical live behaviour); rqalpha differential (R-002) is the
second, independent engine-overfit defence.

Pure functions over plain inputs — no broker import, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

DEFAULT_ADV_PARTICIPATION_CAP = 0.05
"""A shadow order may consume at most 5% of the bar's average daily
volume — far below anything QuantMind's ¥100k account trades, so this
only bites strategies whose edge depends on impossible size."""

DEFAULT_IMPACT_BPS_PER_PCT_PARTICIPATION = 10.0
"""Linear price impact: 10bps of adverse move per 1% ADV participation."""

DEFAULT_STALE_QUOTE_MAX_AGE_S = 60.0
"""Quotes older than 60s reject the order — the live freshness gate's
shadow twin."""


class HarshRejectReason(StrEnum):
    LIMIT_UP_NO_FILL = "limit_up_no_fill"
    LIMIT_DOWN_NO_FILL = "limit_down_no_fill"
    STALE_QUOTE = "stale_quote"
    ZERO_CAPACITY = "zero_capacity"


@dataclass(frozen=True)
class HarshFillConfig:
    """Tunable harshness — itself NOT evolvable (defence layer)."""

    adv_participation_cap: float = DEFAULT_ADV_PARTICIPATION_CAP
    impact_bps_per_pct_participation: float = (
        DEFAULT_IMPACT_BPS_PER_PCT_PARTICIPATION
    )
    stale_quote_max_age_s: float = DEFAULT_STALE_QUOTE_MAX_AGE_S
    delay_to_next_bar: bool = True


@dataclass(frozen=True)
class ShadowOrder:
    """One shadow order to price harshly."""

    side_is_buy: bool
    volume: int
    reference_price: float
    """The price the naive simulator would have filled at."""


@dataclass(frozen=True)
class ShadowBar:
    """Market context for the order's bar (plain PIT data)."""

    adv_volume: float
    """Average daily volume (shares) — capacity reference."""
    limit_up: bool
    limit_down: bool
    quote_age_s: float
    next_bar_open: float | None = None
    """Next bar's open for the delayed-fill assumption (None = use the
    reference price; the impact penalty still applies)."""


@dataclass(frozen=True)
class HarshFill:
    """Outcome of harsh pricing for one order."""

    filled_volume: int
    fill_price: float
    rejected_reason: HarshRejectReason | None

    @property
    def filled(self) -> bool:
        return self.filled_volume > 0 and self.rejected_reason is None


def simulate_harsh_fill(
    order: ShadowOrder,
    bar: ShadowBar,
    *,
    config: HarshFillConfig | None = None,
) -> HarshFill:
    """Price one shadow order under the harsh assumptions (pure).

    Guarantee (the anti-overfit contract, AB-008 adversarially pinned):
    the harsh fill is never BETTER than the naive one — volume is
    capped (≤ requested), and the fill price is adverse-or-equal
    relative to the reference for the order's side.
    """
    cfg = config or HarshFillConfig()

    if order.side_is_buy and bar.limit_up:
        return HarshFill(0, 0.0, HarshRejectReason.LIMIT_UP_NO_FILL)
    if not order.side_is_buy and bar.limit_down:
        return HarshFill(0, 0.0, HarshRejectReason.LIMIT_DOWN_NO_FILL)
    if bar.quote_age_s > cfg.stale_quote_max_age_s:
        return HarshFill(0, 0.0, HarshRejectReason.STALE_QUOTE)

    capacity = int(bar.adv_volume * cfg.adv_participation_cap)
    # Round down to the A-share lot (100 shares).
    capacity = (capacity // 100) * 100
    filled = min(order.volume, capacity)
    if filled <= 0:
        return HarshFill(0, 0.0, HarshRejectReason.ZERO_CAPACITY)

    base_price = (
        bar.next_bar_open
        if cfg.delay_to_next_bar and bar.next_bar_open is not None
        else order.reference_price
    )
    # Delayed fill must never IMPROVE on the reference for the side —
    # a favourable gap is clamped back (harsh-or-equal contract).
    if order.side_is_buy:
        base_price = max(base_price, order.reference_price)
    else:
        base_price = min(base_price, order.reference_price)

    participation_pct = (
        filled / bar.adv_volume * 100.0 if bar.adv_volume > 0 else 0.0
    )
    impact_bps = (
        participation_pct * cfg.impact_bps_per_pct_participation
    )
    impact = base_price * impact_bps / 10_000.0
    fill_price = (
        base_price + impact if order.side_is_buy else base_price - impact
    )
    return HarshFill(
        filled_volume=filled,
        fill_price=round(fill_price, 4),
        rejected_reason=None,
    )


__all__ = [
    "DEFAULT_ADV_PARTICIPATION_CAP",
    "DEFAULT_IMPACT_BPS_PER_PCT_PARTICIPATION",
    "DEFAULT_STALE_QUOTE_MAX_AGE_S",
    "HarshFill",
    "HarshFillConfig",
    "HarshRejectReason",
    "ShadowBar",
    "ShadowOrder",
    "simulate_harsh_fill",
]
