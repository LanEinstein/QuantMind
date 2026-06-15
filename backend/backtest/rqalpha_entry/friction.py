"""Friction model mirroring config/broker.yaml (rqalpha venv side, AE-002).

The differential oracle is only meaningful when rqalpha charges the *same*
friction as the MockBroker, otherwise a divergence is a config artefact, not an
execution-logic bug (R-002-amendment-2026-06-14 §2.3). This module re-implements
``backend.broker.cost_calculator`` exactly — it cannot import it (venv has no
backend), so the values are passed in via ``spec.json`` (sourced from
``config/broker.yaml`` in the main env) and the *formulas* are duplicated here
verbatim:

* slippage: ``round2(price * (1 ± bps/10000))`` board-tiered, BUY up / SELL down;
* commission: ``round2(max(gross * rate, min_commission))``;
* stamp tax: ``round2(gross * stamp_rate)`` on SELL of a stock (CS) only;
* transfer fee: ``round2(gross * 0.0000341)`` on Shenzhen boards only.

All components round to 2 decimals (the exchange settles in 0.01 CNY). The 25bp
oracle tolerance absorbs any residual rqalpha-internal float difference; this
alignment exists to keep the *gross* friction terms correct, not bit-exact.

The board lookup tables are populated once per subprocess by
:func:`configure` (called from the injector mod's ``start_up``) and read lazily
during matching/cost calc — so there is no start-up ordering dependency.
"""

from __future__ import annotations

from rqalpha.const import SIDE
from rqalpha.interface import (
    AbstractTransactionCostDecider,
    TransactionCost,
    TransactionCostArgs,
)
from rqalpha.mod.rqalpha_mod_sys_simulation.slippage import BaseSlippage

# Populated by configure(); read lazily. Single-process subprocess => safe.
_BOARD_BY_OBID: dict[str, str] = {}
_SLIPPAGE_BPS_BY_BOARD: dict[str, float] = {}
_TRANSFER_FEE_BY_OBID: dict[str, bool] = {}
_FRICTION: dict[str, float] = {}


def configure(
    *,
    board_by_obid: dict[str, str],
    transfer_fee_by_obid: dict[str, bool],
    friction: dict[str, float],
    slippage_bps_by_board: dict[str, float],
) -> None:
    """Install the per-instrument board / friction tables for this run."""
    _BOARD_BY_OBID.clear()
    _BOARD_BY_OBID.update(board_by_obid)
    _TRANSFER_FEE_BY_OBID.clear()
    _TRANSFER_FEE_BY_OBID.update(transfer_fee_by_obid)
    _SLIPPAGE_BPS_BY_BOARD.clear()
    _SLIPPAGE_BPS_BY_BOARD.update(slippage_bps_by_board)
    _FRICTION.clear()
    _FRICTION.update(friction)


def _round2(value: float) -> float:
    """Round to 2 decimals — the exchange settles in 0.01 CNY (broker parity)."""
    return round(value, 2)


def _slippage_bps(order_book_id: str) -> float:
    """Board-tiered slippage bps; fail-closed on an unmapped board.

    A board missing from the table is a wiring gap — silently charging 0
    would *under*-cost the oracle and could mask a real divergence as
    CONSISTENT (a false pass). Raising propagates to a non-zero subprocess
    exit -> ORACLE_UNAVAILABLE, which is the correct fail-closed outcome.
    """
    board = _BOARD_BY_OBID.get(order_book_id)
    if board is None or board not in _SLIPPAGE_BPS_BY_BOARD:
        raise KeyError(
            f"no slippage bps for {order_book_id!r} (board {board!r}) — "
            "friction table is incomplete"
        )
    return float(_SLIPPAGE_BPS_BY_BOARD[board])


class QuantMindSlippage(BaseSlippage):
    """Board-tiered slippage mirroring ``cost_calculator.apply_slippage``.

    rqalpha instantiates this with a single ``rate`` (unused — the per-board
    table is read lazily at trade time from the module globals installed by the
    injector mod, so it survives whatever order rqalpha builds its matcher in).
    """

    def __init__(self, rate: float = 0.0) -> None:  # noqa: ARG002 — rqalpha API
        self.rate = 0.0

    def get_trade_price(self, order: object, price: float) -> float:
        bps = _slippage_bps(getattr(order, "order_book_id", ""))
        factor = bps / 10_000.0
        side = getattr(order, "side", None)
        if side == SIDE.BUY:
            return _round2(price * (1.0 + factor))
        return _round2(price * (1.0 - factor))


class QuantMindStockCostDecider(AbstractTransactionCostDecider):
    """Commission + stamp tax + transfer fee, mirroring ``calculate_cost``.

    rqalpha applies slippage (via :class:`QuantMindSlippage`) before this runs,
    so ``args.price`` is already the post-slippage fill price. The matching is
    current-bar full-fill (ALL_OR_NONE parity), i.e. one trade per order, so the
    ``min_commission`` floor is applied once per fill exactly as the broker
    applies it once per order.
    """

    def calc(self, args: TransactionCostArgs) -> TransactionCost:
        gross = _round2(args.price * args.quantity)
        commission = _round2(
            max(
                gross * _FRICTION["commission_rate"],
                _FRICTION["min_commission"],
            )
        )
        # Stamp tax on EVERY sell — mirror backend.broker.cost_calculator,
        # which charges it on any SELL regardless of instrument type (the
        # MockBroker is the cross-check authority, not statutory ETF rules).
        # Gating on CS here (rqalpha's own default) would under-tax ETF sells
        # by ~100bps vs the broker -> spurious DIVERGENT on a correct ETF run.
        is_sell = args.side == SIDE.SELL
        tax = _round2(gross * _FRICTION["stamp_tax_rate"]) if is_sell else 0.0
        if _TRANSFER_FEE_BY_OBID.get(args.instrument.order_book_id, False):
            transfer_fee = _round2(gross * _FRICTION["transfer_fee_rate"])
        else:
            transfer_fee = 0.0
        return TransactionCost(commission=commission, tax=tax, other_fees=transfer_fee)

    def batch_estimate(self, delta_quantities: object, prices: object) -> object:
        # Not used by the oracle's current-bar matching path; the abstract base
        # only requires ``calc``.
        raise NotImplementedError


__all__ = [
    "QuantMindSlippage",
    "QuantMindStockCostDecider",
    "configure",
]
