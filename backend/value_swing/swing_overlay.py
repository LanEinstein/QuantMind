"""Deterministic 做T (T-swing) overlay — bounded, floor-protected, T+1 (AF-006).

``evaluate_swing`` returns at most ONE :class:`SwingIntent` (a SELL of a swing
tranche when price is above the reference band, or a BUY-back when below) for a
value hold, or ``None`` (no action / disabled). Hard invariants:

* **env-OFF default** — ``config.enabled`` ships ``False`` → always ``None``
  (a value hold is a pure long hold, byte-identical).
* **base floor never broken** — a SELL leaves at least ``base_floor_fraction`` of
  the target core untouched (``total - sell ≥ floor_shares``).
* **swing band bounded** — at most ``max_swing_fraction`` of the core ever swings.
* **strict T+1** — a SELL only ever disposes of ``available_volume`` (shares
  settled on a prior day); same-day-bought shares (``total − available``) are
  never re-sold the same day.
* **round-trip bounded** — once ``round_trips_done_today`` reaches
  ``max_round_trips_per_day`` no further swing fires.
* **lot-aligned, deterministic** — every tranche is floored to a whole lot; the
  same inputs always yield the same intent (replayable).

Pure, 0 LLM, no IO, no InstructionPlan construction (import-isolated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SIDE_BUY = "buy"
SIDE_SELL = "sell"


@dataclass(frozen=True)
class SwingConfig:
    """Runtime-immutable 做T overlay parameters (amendment-gated).

    ``enabled`` ships ``False`` (env-OFF): the overlay is inert until the owner
    activates it. Changing any threshold live is an offline recalibration
    (P2-2 evolution whitelist + shadow + human gate), never a hot-reload.
    """

    enabled: bool = False
    base_floor_fraction: float = 0.60
    max_swing_fraction: float = 0.40
    sell_premium: float = 0.05
    buy_discount: float = 0.05
    lot_size: int = 100
    max_round_trips_per_day: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a bool")
        for name in ("base_floor_fraction", "max_swing_fraction"):
            v = getattr(self, name)
            if not isinstance(v, int | float) or isinstance(v, bool):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(v) or not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        # The floor + the swivel band must not over-commit the core (≤ 100%).
        if self.base_floor_fraction + self.max_swing_fraction > 1.0 + 1e-9:
            raise ValueError("base_floor_fraction + max_swing_fraction must be ≤ 1")
        for name in ("sell_premium", "buy_discount"):
            v = getattr(self, name)
            if not isinstance(v, int | float) or isinstance(v, bool):
                raise ValueError(f"{name} must be a number")
            if not math.isfinite(v) or v < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool):
            raise ValueError("lot_size must be an int")
        if self.lot_size < 1:
            raise ValueError("lot_size must be >= 1")
        if (
            not isinstance(self.max_round_trips_per_day, int)
            or isinstance(self.max_round_trips_per_day, bool)
            or self.max_round_trips_per_day < 0
        ):
            raise ValueError("max_round_trips_per_day must be a non-negative int")


@dataclass(frozen=True)
class SwingPosition:
    """The PIT state one value hold's 做T decision is derived from."""

    code: str
    total_volume: int
    """Shares currently held (settled + same-day-bought)."""
    available_volume: int
    """Shares settled on a prior day — the only shares a SELL may dispose (T+1)."""
    target_volume: int
    """The intended core size the floor / swing band are measured against."""
    reference_price: float
    """Deterministic cost reference (e.g. cyq_perf cost band / moving avg)."""
    last_price: float
    round_trips_done_today: int = 0


@dataclass(frozen=True)
class SwingIntent:
    """A bounded, deterministic 做T order intent (consumed by the builder)."""

    code: str
    side: str  # "buy" | "sell"
    volume: int
    limit_price: float
    reason: str


def _floor_lot(shares: int, lot: int) -> int:
    """Largest whole-lot count ≤ ``shares`` (never negative)."""
    if shares <= 0:
        return 0
    return (shares // lot) * lot


def _nonneg_int(value: object) -> bool:
    """True iff ``value`` is a real, non-negative ``int`` (not bool / float / inf)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def evaluate_swing(position: SwingPosition, config: SwingConfig) -> SwingIntent | None:
    """At most one bounded 做T intent for ``position``, or ``None``.

    Deterministic + total. ``None`` when the overlay is disabled, the round-trip
    budget is spent, the price is inside the band, or no whole-lot tranche fits
    without breaking the floor / the T+1 settled-share constraint.
    """
    if not config.enabled:
        return None
    # Fail-closed on malformed state: the share fields + the round-trip counter
    # must be real non-negative ints (a non-finite / negative / fractional value
    # must never slip a tranche through or bypass the round-trip cap — codex
    # AF-006 P1/P2), and the prices must be finite-positive.
    if not (
        _nonneg_int(position.round_trips_done_today)
        and _nonneg_int(position.total_volume)
        and _nonneg_int(position.available_volume)
        and _nonneg_int(position.target_volume)
    ):
        return None
    if position.round_trips_done_today >= config.max_round_trips_per_day:
        return None
    if (
        position.target_volume <= 0
        or not math.isfinite(position.reference_price)
        or position.reference_price <= 0
        or not math.isfinite(position.last_price)
        or position.last_price <= 0
    ):
        return None

    lot = config.lot_size
    floor_shares = _floor_lot(
        int(position.target_volume * config.base_floor_fraction), lot
    )
    max_swing_shares = _floor_lot(
        int(position.target_volume * config.max_swing_fraction), lot
    )
    if max_swing_shares < lot:
        return None

    # SELL-high: trim a swing tranche above the band, never below the floor, only
    # from settled (T+1) shares.
    if position.last_price >= position.reference_price * (1.0 + config.sell_premium):
        sellable = min(
            position.available_volume,
            max_swing_shares,
            max(0, position.total_volume - floor_shares),
        )
        tranche = _floor_lot(sellable, lot)
        if tranche >= lot:
            return SwingIntent(
                code=position.code,
                side=SIDE_SELL,
                volume=tranche,
                limit_price=position.last_price,
                reason="taot_sell_above_band",
            )
        return None

    # BUY-low: rebuild toward the core below the band (only when previously
    # trimmed — total below target), bounded by the swing band.
    if position.last_price <= position.reference_price * (1.0 - config.buy_discount):
        room = min(
            max_swing_shares, max(0, position.target_volume - position.total_volume)
        )
        tranche = _floor_lot(room, lot)
        if tranche >= lot:
            return SwingIntent(
                code=position.code,
                side=SIDE_BUY,
                volume=tranche,
                limit_price=position.last_price,
                reason="taot_buy_below_band",
            )
    return None


__all__ = [
    "SIDE_BUY",
    "SIDE_SELL",
    "SwingConfig",
    "SwingIntent",
    "SwingPosition",
    "evaluate_swing",
]
