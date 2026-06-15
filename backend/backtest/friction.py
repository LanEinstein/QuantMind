"""Lean-style single-factory friction for the deterministic harness (AE-004).

Amendment ``P2-2-amendment-2026-06-14`` §2.2 / §2.4: the harness charges the
*same* A-share friction the live MockBroker charges, organised behind one
factory (the "Lean 单工厂"). It cannot import ``backend.broker`` (the
``[BACKTEST]`` allowlist forbids it), so the four-component formula of
``backend.broker.cost_calculator.calculate_cost`` is re-implemented here over
plain primitives — exactly as the venv ``rqalpha_entry/friction.py`` re-declares
it on the other side of the subprocess wall.

Everything is computed in **integer 分 (cents)** so a replay is bit-reproducible
and NEP-50 (numpy-version) insensitive — the harness's whole point per AE-003.
The four components mirror the broker verbatim:

* **slippage** — board-tiered bps, ``round(price¢ * (1 ± bps/1e4))``, BUY up /
  SELL down. Applied only in *same-source* mode (``apply_board_slippage=True``);
  in the forward shadow-evaluation mode the :mod:`harsh_fill_model` already
  prices its own adverse impact, so layering the board model on top would
  double-count (this mirrors ``calculate_cost(apply_slippage_model=False)``).
* **commission** — ``round(max(gross¢ * rate, min_commission¢))``.
* **stamp tax** — ``round(gross¢ * rate)`` on **every SELL** (the MockBroker is
  the cross-check authority; it taxes any SELL regardless of CS/ETF — gating on
  instrument type here would under-tax ETF sells vs the broker).
* **transfer fee (过户费)** — ``round(gross¢ * rate)`` on Shenzhen-side trades
  only (caller decides via ``transfer_fee_applies``).

Pure leaf — stdlib only, no broker / pandas / backend.data import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

_BPS_DENOM = 10_000.0
"""Basis-point denominator (1bp = 1e-4). A multiplier, never a threshold."""


class FrictionError(ValueError):
    """Raised on a malformed friction input (fail-closed)."""


@dataclass(frozen=True)
class FrictionParams:
    """The MockBroker cost model's parameters, mirrored (single source).

    Sourced from ``config/broker.yaml`` + ``cost_calculator.TRANSFER_FEE_RATE_SZ``
    — never hand-typed in production (the dispatcher loads them) so the harness
    and the live broker charge identical friction. ``slippage_bps_by_board`` is
    keyed by :class:`backend.data.stock_metadata.Board` ``.value`` strings
    (``sh_main`` / ``sz_main`` / ``chuangye`` / ``etf``).
    """

    commission_rate: float
    min_commission_cents: int
    stamp_tax_rate: float
    transfer_fee_rate: float
    slippage_bps_by_board: Mapping[str, float]

    def __post_init__(self) -> None:
        for name in (
            "commission_rate",
            "stamp_tax_rate",
            "transfer_fee_rate",
        ):
            value = getattr(self, name)
            if value < 0:
                raise FrictionError(f"{name} must be >= 0, got {value}")
        if self.min_commission_cents < 0:
            raise FrictionError(
                f"min_commission_cents must be >= 0, got {self.min_commission_cents}"
            )

    def slippage_bps(self, board: str) -> float:
        """Board-tiered slippage bps; fail-closed on an unmapped board.

        A missing board would otherwise silently charge 0 slippage and
        *under*-cost the fill — exactly the kind of friction gap that could
        let an over-fit challenger look profitable (mirrors the venv entry's
        fail-closed lookup).
        """
        if board not in self.slippage_bps_by_board:
            raise FrictionError(
                f"no slippage bps for board {board!r} — friction table is "
                f"incomplete (have {sorted(self.slippage_bps_by_board)})"
            )
        return float(self.slippage_bps_by_board[board])


@dataclass(frozen=True)
class FillEconomics:
    """Per-fill economics in integer 分 (immutable).

    ``net_cents`` is the unsigned cash impact: a BUY debits it from cash, a
    SELL credits it. ``gross_cents`` is ``fill_price_cents * volume`` (the
    post-slippage notional).
    """

    fill_price_cents: int
    gross_cents: int
    commission_cents: int
    stamp_tax_cents: int
    transfer_fee_cents: int
    slippage_cents: int
    net_cents: int


def apply_board_slippage_cents(
    *, order_price_cents: int, side_is_buy: bool, slippage_bps: float
) -> int:
    """Post-slippage fill price in 分 — BUY up, SELL down (broker parity)."""
    factor = slippage_bps / _BPS_DENOM
    if side_is_buy:
        return round(order_price_cents * (1.0 + factor))
    return round(order_price_cents * (1.0 - factor))


def compute_fill_economics(
    *,
    side_is_buy: bool,
    order_price_cents: int,
    volume: int,
    board: str,
    transfer_fee_applies: bool,
    params: FrictionParams,
    apply_board_slippage: bool = True,
) -> FillEconomics:
    """Per-fill economics mirroring ``cost_calculator.calculate_cost`` (整数分).

    Args:
        side_is_buy: True for BUY, False for SELL.
        order_price_cents: pre-slippage order/limit price in 分 (> 0).
        volume: whole-share fill volume (> 0; lot constraints upstream).
        board: ``Board.value`` for the slippage tier.
        transfer_fee_applies: caller-resolved SZ-side 过户费 toggle.
        params: the mirrored friction parameters.
        apply_board_slippage: True for same-source parity; False when the
            caller (harsh fill) already priced its own slippage/impact.

    Raises:
        FrictionError: non-positive volume / price, or an unmapped board.
    """
    if volume <= 0:
        raise FrictionError(f"volume must be > 0, got {volume}")
    if order_price_cents <= 0:
        raise FrictionError(f"order_price_cents must be > 0, got {order_price_cents}")

    if apply_board_slippage:
        fill_price_cents = apply_board_slippage_cents(
            order_price_cents=order_price_cents,
            side_is_buy=side_is_buy,
            slippage_bps=params.slippage_bps(board),
        )
    else:
        fill_price_cents = order_price_cents
    gross_cents = fill_price_cents * volume
    slippage_cents = abs(fill_price_cents - order_price_cents) * volume

    commission_cents = round(
        max(gross_cents * params.commission_rate, params.min_commission_cents)
    )
    stamp_tax_cents = 0 if side_is_buy else round(gross_cents * params.stamp_tax_rate)
    transfer_fee_cents = (
        round(gross_cents * params.transfer_fee_rate) if transfer_fee_applies else 0
    )

    if side_is_buy:
        net_cents = gross_cents + commission_cents + transfer_fee_cents
    else:
        net_cents = (
            gross_cents - commission_cents - stamp_tax_cents - transfer_fee_cents
        )
        # An extreme tiny-volume SELL whose friction exceeds gross would credit
        # negative cash. The live ``cost_calculator`` *rejects* such an order
        # (raises); a backtest clamps to 0 instead — never minting negative cash
        # — so a degenerate lot does not abort the whole replay. Lot-size + price
        # floors make this effectively unreachable for QuantMind's universe.
        if net_cents < 0:
            net_cents = 0
    return FillEconomics(
        fill_price_cents=fill_price_cents,
        gross_cents=gross_cents,
        commission_cents=commission_cents,
        stamp_tax_cents=stamp_tax_cents,
        transfer_fee_cents=transfer_fee_cents,
        slippage_cents=slippage_cents,
        net_cents=net_cents,
    )


__all__ = [
    "FillEconomics",
    "FrictionError",
    "FrictionParams",
    "apply_board_slippage_cents",
    "compute_fill_economics",
]
