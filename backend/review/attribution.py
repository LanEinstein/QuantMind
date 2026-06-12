"""Deterministic daily attribution builder (AA-002).

Pure functions: in go the day's executed trades + per-code VWAP /
entry-cost lookups + violation counts, out comes a frozen
:class:`DailyReviewRecord`. No IO, no clock reads, no LLM — the
orchestration layer (main.py 18:00 cron callback) gathers the inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from backend.review.models import (
    CounterfactualEntry,
    DailyReviewRecord,
    TradeFact,
    TradeSide,
    VwapQuality,
)

VWAP_PLAUSIBILITY_BAND = (1.0 / 3.0, 3.0)
"""A day VWAP outside [price/3, price*3] of the fill price is treated
as unit corruption (e.g. an amount column in 千元 instead of 元 shifts
VWAP 1000x) and dropped — recording it would poison every downstream
slippage aggregate. A real same-day price never moves 3x through the
±10%/±20% A-share price-limit regime."""


def derive_vwap_basis(
    price: float, vwap: float | None
) -> tuple[float | None, VwapQuality]:
    """Validate a candidate day-VWAP against the fill price.

    Returns the usable VWAP (or ``None``) plus the provenance flag.
    """
    if vwap is None or vwap <= 0.0:
        return None, VwapQuality.MISSING
    low, high = VWAP_PLAUSIBILITY_BAND
    if not (price * low <= vwap <= price * high):
        return None, VwapQuality.IMPLAUSIBLE
    return float(vwap), VwapQuality.OK


def normalize_kline_vwap(
    *, amount: float, volume: float, close: float
) -> float | None:
    """Derive a day VWAP from a kline row across volume-unit dialects.

    A-share kline feeds disagree on units: volume may be 股 (shares) or
    手 (lots of 100), amount may be 元 or 千元 — a naive amount/volume
    is then off by 100x/1000x. Both candidate interpretations are
    checked against the day's close through the same plausibility band;
    exactly one can be plausible (the band spans 3x, the unit errors are
    ≥100x apart). ``None`` when no candidate is plausible — null beats
    a poisoned slippage aggregate.
    """
    if amount <= 0.0 or volume <= 0.0 or close <= 0.0:
        return None
    low, high = VWAP_PLAUSIBILITY_BAND
    for candidate in (amount / volume, amount / (volume * 100.0)):
        if close * low <= candidate <= close * high:
            return candidate
    return None


def _execution_vs_vwap_bps(
    side: TradeSide, price: float, vwap: float
) -> float:
    """Side-adjusted execution quality: positive = better than VWAP."""
    raw = (vwap - price) / vwap * 10_000.0
    return raw if side is TradeSide.BUY else -raw


def build_trade_fact(
    trade: Any,
    *,
    day_vwap: float | None,
    entry_cost_price: float | None,
    policy_hash: str | None,
    style: str | None,
) -> TradeFact:
    """Build one :class:`TradeFact` from a broker ``Trade``-like row.

    ``trade`` is duck-typed (the broker's frozen ``Trade`` model) so the
    module stays import-clean of ``backend.broker``.
    """
    side = (
        TradeSide.BUY
        if str(trade.direction).upper().endswith("BUY")
        else TradeSide.SELL
    )
    price = float(trade.price)
    vwap, quality = derive_vwap_basis(price, day_vwap)
    bps = (
        _execution_vs_vwap_bps(side, price, vwap)
        if vwap is not None
        else None
    )

    entry: float | None = None
    holding_return: float | None = None
    if side is TradeSide.SELL and entry_cost_price is not None and (
        entry_cost_price > 0.0
    ):
        entry = float(entry_cost_price)
        holding_return = (price - entry) / entry

    return TradeFact(
        trade_id=str(trade.trade_id),
        order_id=str(trade.order_id),
        code=str(trade.code),
        side=side,
        volume=int(trade.volume),
        price=price,
        amount=float(trade.amount),
        traded_at=trade.traded_at,
        commission=float(trade.commission),
        stamp_tax=float(getattr(trade, "stamp_tax", 0.0)),
        transfer_fee=float(getattr(trade, "transfer_fee", 0.0)),
        slippage_cost=float(trade.slippage_cost),
        day_vwap=vwap,
        execution_vs_vwap_bps=bps,
        vwap_quality=quality,
        entry_cost_price=entry,
        holding_return_pct=holding_return,
        policy_hash=policy_hash,
        style=style,
    )


def build_daily_review(
    *,
    trade_date: str,
    created_at: datetime,
    trades: Sequence[Any],
    vwap_by_code: Mapping[str, float],
    entry_cost_by_code: Mapping[str, float],
    style_by_code: Mapping[str, str] | None = None,
    policy_hash: str | None,
    counterfactuals: Sequence[CounterfactualEntry] = (),
    risk_rejected_count: int = 0,
    builder_early_return_count: int = 0,
) -> DailyReviewRecord:
    """Assemble the day's :class:`DailyReviewRecord` (pure).

    A day with zero trades still produces a record — "nothing executed"
    is itself a fact the weekly review and the AB promotion engine need
    (e.g. to distinguish "no signal" from "data outage").
    """
    styles = style_by_code or {}
    facts = tuple(
        build_trade_fact(
            trade,
            day_vwap=vwap_by_code.get(str(trade.code)),
            entry_cost_price=entry_cost_by_code.get(str(trade.code)),
            policy_hash=policy_hash,
            style=styles.get(str(trade.code)),
        )
        for trade in trades
    )
    return DailyReviewRecord(
        trade_date=trade_date,
        created_at=created_at,
        policy_hash=policy_hash,
        trade_facts=facts,
        counterfactuals=tuple(counterfactuals),
        risk_rejected_count=int(risk_rejected_count),
        builder_early_return_count=int(builder_early_return_count),
    )


__all__ = [
    "VWAP_PLAUSIBILITY_BAND",
    "build_daily_review",
    "build_trade_fact",
    "derive_vwap_basis",
    "normalize_kline_vwap",
]
