"""Integer-分 portfolio accounting for the deterministic harness (AE-004).

The forward-simulation twin of :mod:`backend.backtest.golden_replay` (which
*replays recorded* fills): this carries the running cash + holdings the event
loop mutates as harsh fills land, and marks the book to each day's close. All
money is integer 分 so the equity curve is bit-reproducible and NEP-50
insensitive (AE-003 discipline); every *returned* object is frozen.

The accounting mirrors the live mirror exactly:

* a BUY debits the unsigned ``net_cents`` (gross + commission + transfer) from
  cash and adds shares;
* a SELL credits ``net_cents`` (gross − commission − stamp tax − transfer) to
  cash and removes shares;
* a day's ``market_value`` = Σ ``volume * close_mark``;
* ``total_equity`` = ``cash + frozen_cash + market_value``.

Over-selling more than held is fail-closed (:class:`PortfolioError`) — a
forward strategy that emitted such an order has a bug the harness must surface,
never silently mint shares.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class PortfolioError(RuntimeError):
    """Raised on an impossible accounting operation (fail-closed)."""


@dataclass(frozen=True)
class OpeningLot:
    """An opening holding seeded into the portfolio (integer units)."""

    code: str
    volume: int
    cost_cents: int
    """Cost basis *per share* in 分."""


@dataclass(frozen=True)
class AppliedFill:
    """One fill the loop applied — the audit/invariant record (immutable).

    Carries every friction component so :mod:`backend.backtest.invariants` can
    recompute the fee from the formula and assert the books charged it (the
    closed-form "fee = explicit recompute" invariant, breaking the N=2
    common-mode blind spot).
    """

    trade_date: str
    code: str
    side_is_buy: bool
    volume: int
    fill_price_cents: int
    gross_cents: int
    commission_cents: int
    stamp_tax_cents: int
    transfer_fee_cents: int
    slippage_cents: int
    net_cents: int
    board: str
    transfer_fee_applies: bool
    """Board + SZ-fee toggle carried so the fee-recompute invariant is
    self-contained (re-derives friction from the fill price + these alone)."""


@dataclass(frozen=True)
class PositionMark:
    """One held position marked to a day's close (integer 分)."""

    code: str
    volume: int
    cost_cents: int
    market_value_cents: int


@dataclass(frozen=True)
class EquitySnapshot:
    """One end-of-day equity row with per-position marks (immutable).

    Per-position marks are carried so the single-stock / total-position
    exposure invariants are self-contained (no need to re-thread the close
    marks). Duplicate codes are rejected at construction (mirrors
    ``EquityPoint``).
    """

    trade_date: str
    cash_cents: int
    market_value_cents: int
    total_equity_cents: int
    positions: tuple[PositionMark, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for pos in self.positions:
            if pos.code in seen:
                raise PortfolioError(
                    f"duplicate position code {pos.code} in EquitySnapshot"
                )
            seen.add(pos.code)


class BacktestPortfolio:
    """Running cash + holdings during a backtest (mutates internally).

    Internal state is a local accumulator (the same pragmatic pattern
    ``golden_replay`` uses); every value it *hands out* is a frozen snapshot.
    """

    def __init__(
        self,
        *,
        initial_cash_cents: int,
        frozen_cash_cents: int = 0,
        opening_positions: Sequence[OpeningLot] = (),
    ) -> None:
        if initial_cash_cents < 0:
            raise PortfolioError(
                f"initial_cash_cents must be >= 0, got {initial_cash_cents}"
            )
        if frozen_cash_cents < 0:
            raise PortfolioError(
                f"frozen_cash_cents must be >= 0, got {frozen_cash_cents}"
            )
        self._cash = initial_cash_cents
        self._frozen_cash = frozen_cash_cents
        # code -> [volume, cost_basis_cents_per_share]
        self._holdings: dict[str, list[int]] = {}
        for lot in opening_positions:
            if lot.code in self._holdings:
                raise PortfolioError(f"duplicate opening lot {lot.code}")
            if lot.volume <= 0:
                raise PortfolioError(f"opening lot {lot.code} volume must be > 0")
            self._holdings[lot.code] = [lot.volume, lot.cost_cents]

    @property
    def cash_cents(self) -> int:
        return self._cash

    def held_volume(self, code: str) -> int:
        lot = self._holdings.get(code)
        return lot[0] if lot else 0

    def holdings_snapshot(self) -> dict[str, int]:
        """Current ``code -> volume`` (a fresh dict; never the internal state)."""
        return {code: lot[0] for code, lot in self._holdings.items() if lot[0] > 0}

    def apply(self, fill: AppliedFill) -> None:
        """Apply one fill to cash + holdings (fail-closed on over-sell)."""
        if fill.volume <= 0:
            raise PortfolioError(f"fill volume must be > 0, got {fill.volume}")
        if fill.side_is_buy:
            self._cash -= fill.net_cents
            lot = self._holdings.setdefault(fill.code, [0, 0])
            new_volume = lot[0] + fill.volume
            # Weighted-average cost basis per share including the buy friction.
            total_cost = (
                lot[0] * lot[1]
                + fill.gross_cents
                + fill.commission_cents
                + fill.transfer_fee_cents
            )
            lot[0] = new_volume
            lot[1] = total_cost // new_volume if new_volume else 0
        else:
            sell_lot = self._holdings.get(fill.code)
            if sell_lot is None or sell_lot[0] < fill.volume:
                held = 0 if sell_lot is None else sell_lot[0]
                raise PortfolioError(
                    f"SELL {fill.volume} of {fill.code} exceeds held {held}"
                )
            self._cash += fill.net_cents
            sell_lot[0] -= fill.volume
            if sell_lot[0] == 0:
                del self._holdings[fill.code]

    def mark(
        self, *, trade_date: str, close_marks_cents: Mapping[str, int]
    ) -> EquitySnapshot:
        """Mark the book to a day's closes → an immutable equity snapshot.

        Raises:
            PortfolioError: a held code has no closing mark (the loop must feed
                a mark for every holding — a missing mark is a data gap, not a
                zero-valued position).
        """
        market_value = 0
        marks: list[PositionMark] = []
        for code in sorted(self._holdings):
            volume, cost_cents = self._holdings[code]
            if volume <= 0:
                continue
            mark = close_marks_cents.get(code)
            if mark is None:
                raise PortfolioError(
                    f"{trade_date}: no closing mark for held code {code}"
                )
            position_value = volume * mark
            market_value += position_value
            marks.append(
                PositionMark(
                    code=code,
                    volume=volume,
                    cost_cents=cost_cents,
                    market_value_cents=position_value,
                )
            )
        return EquitySnapshot(
            trade_date=trade_date,
            cash_cents=self._cash,
            market_value_cents=market_value,
            total_equity_cents=self._cash + self._frozen_cash + market_value,
            positions=tuple(marks),
        )


__all__ = [
    "AppliedFill",
    "BacktestPortfolio",
    "EquitySnapshot",
    "OpeningLot",
    "PortfolioError",
    "PositionMark",
]
