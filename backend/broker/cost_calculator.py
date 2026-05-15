"""Order cost calculator — pure function, no IO, no LLM (E-003 / P1-2.C).

The cost model encodes the four A-share friction components the
MockBroker charges on every fill:

* **Commission** — broker rate * amount, floored at min_commission.
* **Stamp tax** — flat rate * amount on SELL only (BUY exempt).
* **Slippage** — board-tiered basis points applied directly to the
  fill price (BUY adds, SELL subtracts). The bps table comes from
  :class:`BrokerConfig.slippage_bps_by_board` — single source of truth.
* **Transfer fee (过户费)** — 0.00341% (0.0000341) double-sided on
  Shenzhen-board trades only (SZ_MAIN + CHUANGYE + 159 ETFs). The
  Shanghai exchange dropped this fee in 2022; ChiNext was always
  Shenzhen.

The module is intentionally minimal: a frozen :class:`OrderCostBreakdown`
DTO and a single :func:`calculate_cost` function. No class, no
hidden mutable state — every input is an argument. The :mod:`backend.risk`
layer never imports this (red line); the Builder may import it but
treats the output as opaque numeric data, not as decision input.

The function is paired with :class:`backend.data.market_meta_provider
.MarketMetaProvider` — the provider supplies prev_close + live price,
this module turns the fill price into the per-fill economics. Splitting
them keeps the cost model pure (testable without Mongo / Redis).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from backend.broker.models import OrderDirection
from backend.data.stock_metadata import Board

if TYPE_CHECKING:
    from backend.broker.models import BrokerConfig


# Shenzhen-side transfer fee. P1-2.C locked the rate at the current
# (post-2022) rate of 0.00341% per side. SH_MAIN charges nothing post-
# 2022; ETF SH (510/511/512/513/515/516/517/518/588) likewise charges
# nothing; only SZ_MAIN, CHUANGYE, and SZ ETFs (159) carry the fee.
TRANSFER_FEE_RATE_SZ = 0.0000341
"""Shenzhen-board 过户费 rate (per side). Locked by P1-2.C; runtime
changes require a paired amendment doc + restart."""


_SZ_TRANSFER_FEE_BOARDS: frozenset[Board] = frozenset(
    {Board.SZ_MAIN, Board.CHUANGYE}
)
"""Boards charged the SZ transfer fee — Shenzhen main board + ChiNext.

ETFs are board-classified by prefix in :mod:`backend.data.stock_metadata`,
so a 159* ETF resolves to ``Board.ETF`` rather than ``Board.SZ_MAIN``.
ETF-board transfer-fee handling is therefore prefix-aware in
:func:`_is_shenzhen_etf`.
"""


def _is_shenzhen_etf(code: str) -> bool:
    """Return True for 159* ETFs (Shenzhen-listed)."""
    return code.startswith("159")


class OrderCostBreakdown(BaseModel):
    """Per-fill cost breakdown emitted by :func:`calculate_cost`.

    Frozen + strict + extra=forbid so callers cannot smuggle in new
    fields silently. The component fields are independent (sum
    derivable via :pyattr:`total_friction`) so audit can attribute the
    exact line item that drove the cost.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    direction: OrderDirection
    code: str = Field(pattern=r"^\d{6}$")
    board: Board
    order_price: float = Field(gt=0.0)
    fill_price: float = Field(gt=0.0)
    volume: int = Field(ge=1)
    gross_amount: float = Field(ge=0.0)
    commission: float = Field(ge=0.0)
    stamp_tax: float = Field(ge=0.0)
    transfer_fee: float = Field(ge=0.0)
    slippage_cost: float = Field(ge=0.0)
    net_amount: float = Field(ge=0.0)
    """Cash impact: BUY = gross + commission + transfer_fee;
    SELL = gross - commission - stamp_tax - transfer_fee. Always >= 0;
    direction tells the caller whether to subtract or add to cash."""

    @property
    def total_friction(self) -> float:
        """Sum of commission + stamp_tax + transfer_fee + slippage_cost."""
        return self.commission + self.stamp_tax + self.transfer_fee + self.slippage_cost


def _round2(value: float) -> float:
    """Round to 2 decimal places. The exchange settles in 0.01 CNY units."""
    return round(value, 2)


def _slippage_bps_for_board(config: BrokerConfig, board: Board) -> float:
    """Return the bps multiplier for the given board, falling back to the
    legacy scalar slippage_bps if the per-board table is empty.
    """
    table = config.slippage_bps_by_board
    if board.value in table:
        return float(table[board.value])
    return float(config.slippage_bps)


def apply_slippage(
    order_price: float,
    direction: OrderDirection,
    board: Board,
    config: BrokerConfig,
) -> float:
    """Return the fill price after applying board-tiered slippage.

    BUY slippage pushes the price up (buyer is the price-taker);
    SELL slippage pushes the price down. Always rounded to 2dp so
    downstream maths is stable.
    """
    bps = _slippage_bps_for_board(config, board)
    factor = bps / 10_000.0
    if direction is OrderDirection.BUY:
        return _round2(order_price * (1.0 + factor))
    return _round2(order_price * (1.0 - factor))


def calculate_cost(
    *,
    code: str,
    board: Board,
    order_price: float,
    volume: int,
    direction: OrderDirection,
    config: BrokerConfig,
) -> OrderCostBreakdown:
    """Compute per-fill economics for a hypothetical or real fill.

    Inputs:
        code: 6-digit A-share code (used for SZ-ETF transfer-fee gating).
        board: pre-classified Board from :mod:`stock_metadata`.
        order_price: limit price the order carries (pre-slippage).
        volume: positive integer (lot constraints validated upstream).
        direction: BUY or SELL.
        config: BrokerConfig — supplies commission / stamp tax /
            slippage / min_commission / transfer_fee toggle.

    Returns:
        Immutable :class:`OrderCostBreakdown` carrying gross + each
        friction component + the net cash impact.
    """
    if volume <= 0:
        raise ValueError(f"volume {volume} must be positive")
    if order_price <= 0:
        raise ValueError(f"order_price {order_price} must be positive")

    fill_price = apply_slippage(order_price, direction, board, config)
    gross = _round2(fill_price * volume)
    slippage_cost = _round2(abs(fill_price - order_price) * volume)

    commission = _round2(
        max(gross * config.commission_rate, config.min_commission)
    )
    stamp_tax = (
        _round2(gross * config.stamp_tax_rate)
        if direction is OrderDirection.SELL
        else 0.0
    )

    transfer_fee = 0.0
    if board in _SZ_TRANSFER_FEE_BOARDS or _is_shenzhen_etf(code):
        transfer_fee = _round2(gross * TRANSFER_FEE_RATE_SZ)

    if direction is OrderDirection.BUY:
        net = _round2(gross + commission + transfer_fee)
    else:
        net = _round2(gross - commission - stamp_tax - transfer_fee)
        # Guard a negative net from an extreme low-volume edge case
        # (commission floor + stamp tax + transfer fee > gross). The
        # MockBroker's pre-fill validation should prevent the case from
        # ever reaching here, but the schema requires net_amount >= 0.
        if net < 0:
            raise ValueError(
                f"net_amount {net} negative for SELL — commission floor "
                "exceeds gross; reject upstream"
            )

    return OrderCostBreakdown(
        direction=direction,
        code=code,
        board=board,
        order_price=_round2(order_price),
        fill_price=fill_price,
        volume=volume,
        gross_amount=gross,
        commission=commission,
        stamp_tax=stamp_tax,
        transfer_fee=transfer_fee,
        slippage_cost=slippage_cost,
        net_amount=net,
    )


__all__ = [
    "TRANSFER_FEE_RATE_SZ",
    "OrderCostBreakdown",
    "apply_slippage",
    "calculate_cost",
]
