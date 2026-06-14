"""Golden replay — the backtest harness's same-source proof (AE-003 / §2.5).

The single empirical evidence that the harness models the *same reality* as the
live MockBroker: take a real recorded trading day (opening cash + positions, the
day's fills, the day's closing marks) and reconstruct the equity curve, then
assert it matches the live-recorded ``EquityPoint`` series **exactly**. Per the
amendment this gate comes *before* any statistical gate — "不过此关后续统计门一门
不加".

Everything is computed in **integer 分 (cents)** so the reconstruction is exact
and reproducible (no float drift, no numpy-version sensitivity). The accounting
mirrors the live mirror:

* a BUY debits ``volume * price + cost`` from cash and adds shares;
* a SELL credits ``volume * price - cost`` to cash and removes shares;
* a day's ``market_value`` = Σ ``volume * close_mark``;
* ``total_equity`` = ``cash + market_value`` (frozen cash is carried separately
  and added back, mirroring ``EquityPoint.total_equity``).

Closed-form invariants (codex J3 — break the N=2 common-mode blind spot, i.e.
two engines agreeing on a shared bug): cash conservation (initial + Σsell −
Σbuy = final, integer-exact) and per-position share conservation (never
negative). They do not depend on any framework version.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

_BUY = "BUY"
_SELL = "SELL"


@dataclass(frozen=True)
class ReplayPosition:
    """An opening (or reconstructed) holding, in integer units."""

    code: str
    volume: int
    cost_cents: int
    """Cost basis *per share* in 分."""


@dataclass(frozen=True)
class ReplayFill:
    """One recorded fill (from a live ``Trade`` / broker event).

    Validated at construction (fail-closed): a malformed fill — non-positive
    volume/price, negative cost, or an unknown side — would otherwise mint cash
    or grow a position during accounting (codex AE-003 cycle-2 P2).
    """

    code: str
    side: str  # _BUY / _SELL
    volume: int
    price_cents: int
    """Fill price per share in 分."""
    cost_cents: int = 0
    """Commission + transfer + tax for this fill, in 分 (as recorded)."""

    def __post_init__(self) -> None:
        if self.side not in (_BUY, _SELL):
            raise ValueError(f"fill side must be BUY/SELL, got {self.side!r}")
        if self.volume <= 0:
            raise ValueError(f"fill volume must be > 0, got {self.volume}")
        if self.price_cents <= 0:
            raise ValueError(
                f"fill price_cents must be > 0, got {self.price_cents}"
            )
        if self.cost_cents < 0:
            raise ValueError(
                f"fill cost_cents must be >= 0, got {self.cost_cents}"
            )


@dataclass(frozen=True)
class ReplayDay:
    """One trading day's recorded inputs."""

    trade_date: str
    fills: tuple[ReplayFill, ...] = ()
    close_marks_cents: Mapping[str, int] = field(default_factory=dict)
    """code -> closing price in 分 (used for end-of-day MTM)."""


@dataclass(frozen=True)
class ReplayEquityPoint:
    """One reconstructed end-of-day equity row, in integer 分.

    Duplicate position codes are rejected at construction (mirroring
    ``EquityPoint``) so a malformed recorded/adaptor row cannot be silently
    normalised away by the comparator (codex AE-003 cycle-2 P2).
    """

    trade_date: str
    cash_cents: int
    market_value_cents: int
    total_equity_cents: int
    positions: tuple[ReplayPosition, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for pos in self.positions:
            if pos.code in seen:
                raise ValueError(
                    f"duplicate position code {pos.code} in ReplayEquityPoint"
                )
            seen.add(pos.code)


@dataclass(frozen=True)
class GoldenDivergence:
    """A single mismatch between replay and the recorded golden series.

    ``replayed`` / ``recorded`` are stringified so one type carries both the
    integer aggregates and the date / position-state mismatches.
    """

    trade_date: str
    field_name: str
    replayed: str
    recorded: str


@dataclass(frozen=True)
class GoldenReplayResult:
    """Outcome of comparing a replay against the recorded golden series."""

    matched: bool
    divergences: tuple[GoldenDivergence, ...]


class ConservationError(RuntimeError):
    """Raised when a closed-form accounting invariant is violated."""


def replay_equity_curve(
    *,
    initial_cash_cents: int,
    frozen_cash_cents: int = 0,
    opening_positions: Sequence[ReplayPosition] = (),
    days: Sequence[ReplayDay],
) -> tuple[ReplayEquityPoint, ...]:
    """Reconstruct the per-day equity curve from recorded inputs (integer 分).

    Raises:
        ConservationError: a SELL exceeds the held volume, or a fill references
            a code with no opening lot and no prior BUY (fail-closed — the
            recorded stream is internally inconsistent).
    """
    cash = initial_cash_cents
    holdings: dict[str, list[int]] = {
        p.code: [p.volume, p.cost_cents] for p in opening_positions
    }
    curve: list[ReplayEquityPoint] = []

    for day in days:
        for fill in day.fills:
            cash = _apply_fill(cash, holdings, fill)
        market_value = 0
        positions: list[ReplayPosition] = []
        for code in sorted(holdings):
            volume, cost_cents = holdings[code]
            if volume <= 0:
                continue
            mark = day.close_marks_cents.get(code)
            if mark is None:
                raise ConservationError(
                    f"{day.trade_date}: no closing mark for held code {code}"
                )
            market_value += volume * mark
            positions.append(
                ReplayPosition(code=code, volume=volume, cost_cents=cost_cents)
            )
        curve.append(
            ReplayEquityPoint(
                trade_date=day.trade_date,
                cash_cents=cash,
                market_value_cents=market_value,
                total_equity_cents=cash + frozen_cash_cents + market_value,
                positions=tuple(positions),
            )
        )
    return tuple(curve)


def _apply_fill(
    cash: int, holdings: dict[str, list[int]], fill: ReplayFill
) -> int:
    """Apply one fill to cash + holdings (in place on ``holdings``)."""
    gross = fill.volume * fill.price_cents
    if fill.side == _BUY:
        cash -= gross + fill.cost_cents
        lot = holdings.setdefault(fill.code, [0, 0])
        new_volume = lot[0] + fill.volume
        # Weighted-average cost basis per share (integer 分, floor division is
        # deterministic; exact basis is not needed for the equity identity).
        total_cost = lot[0] * lot[1] + gross + fill.cost_cents
        lot[0] = new_volume
        lot[1] = total_cost // new_volume if new_volume else 0
    else:  # _SELL — side is validated by ReplayFill.__post_init__
        sell_lot = holdings.get(fill.code)
        if sell_lot is None or sell_lot[0] < fill.volume:
            held = 0 if sell_lot is None else sell_lot[0]
            raise ConservationError(
                f"SELL {fill.volume} of {fill.code} exceeds held {held}"
            )
        cash += gross - fill.cost_cents
        sell_lot[0] -= fill.volume
    return cash


def assert_conservation(
    *,
    initial_cash_cents: int,
    days: Sequence[ReplayDay],
    final_cash_cents: int,
) -> None:
    """Verify cash conservation: initial + Σsell − Σbuy = final (integer-exact).

    Raises:
        ConservationError: the identity does not hold.
    """
    delta = 0
    for day in days:
        for fill in day.fills:
            gross = fill.volume * fill.price_cents
            if fill.side == _BUY:
                delta -= gross + fill.cost_cents
            elif fill.side == _SELL:
                delta += gross - fill.cost_cents
    expected = initial_cash_cents + delta
    if expected != final_cash_cents:
        raise ConservationError(
            f"cash conservation broken: initial {initial_cash_cents} + Δ {delta}"
            f" = {expected} != final {final_cash_cents}"
        )


def compare_to_golden(
    replayed: Sequence[ReplayEquityPoint],
    recorded: Sequence[ReplayEquityPoint],
    *,
    tolerance_cents: int = 0,
) -> GoldenReplayResult:
    """Compare a replay to the recorded golden series (integer 分, exact).

    The default ``tolerance_cents=0`` is the same-source bar: the harness must
    match the live record to the cent. Returns a structured result rather than
    raising so callers can report every divergence at once.
    """
    divergences: list[GoldenDivergence] = []
    if len(replayed) != len(recorded):
        divergences.append(
            GoldenDivergence(
                trade_date="*",
                field_name="length",
                replayed=str(len(replayed)),
                recorded=str(len(recorded)),
            )
        )
        return GoldenReplayResult(matched=False, divergences=tuple(divergences))

    for rep, rec in zip(replayed, recorded, strict=True):
        # Row identity: a same-source proof must align the dates, not just the
        # aggregates (codex AE-003 P2 — a date-shifted curve must not pass).
        if rep.trade_date != rec.trade_date:
            divergences.append(
                GoldenDivergence(
                    trade_date=rec.trade_date,
                    field_name="trade_date",
                    replayed=rep.trade_date,
                    recorded=rec.trade_date,
                )
            )
        for name in ("cash_cents", "market_value_cents", "total_equity_cents"):
            rep_v = getattr(rep, name)
            rec_v = getattr(rec, name)
            if abs(rep_v - rec_v) > tolerance_cents:
                divergences.append(
                    GoldenDivergence(
                        trade_date=rec.trade_date,
                        field_name=name,
                        replayed=str(rep_v),
                        recorded=str(rec_v),
                    )
                )
        # Holdings: a swapped position with an equal closing market value would
        # otherwise pass on the aggregate alone (codex AE-003 P2). Compare the
        # authoritative sorted (code, volume) list — duplicates are preserved
        # so they cannot collapse away (cost basis is derived, not checked).
        rep_pos = sorted((p.code, p.volume) for p in rep.positions)
        rec_pos = sorted((p.code, p.volume) for p in rec.positions)
        if rep_pos != rec_pos:
            divergences.append(
                GoldenDivergence(
                    trade_date=rec.trade_date,
                    field_name="positions",
                    replayed=str(rep_pos),
                    recorded=str(rec_pos),
                )
            )
    return GoldenReplayResult(
        matched=not divergences, divergences=tuple(divergences)
    )


__all__ = [
    "ConservationError",
    "GoldenDivergence",
    "GoldenReplayResult",
    "ReplayDay",
    "ReplayEquityPoint",
    "ReplayFill",
    "ReplayPosition",
    "assert_conservation",
    "compare_to_golden",
    "replay_equity_curve",
]
