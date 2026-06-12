"""AA-002 deterministic attribution builder tests."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest

from backend.review.attribution import (
    build_daily_review,
    build_trade_fact,
    derive_vwap_basis,
    normalize_kline_vwap,
)
from backend.review.models import (
    CounterfactualEntry,
    CounterfactualKind,
    TradeSide,
    VwapQuality,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 18, 0, tzinfo=SHANGHAI)


@dataclass(frozen=True)
class _Trade:
    trade_id: str
    order_id: str
    code: str
    price: float
    volume: int
    amount: float
    direction: str
    commission: float
    stamp_tax: float
    slippage_cost: float
    transfer_fee: float
    net_amount: float
    traded_at: dt.datetime


def _trade(
    *,
    code: str = "600519",
    price: float = 12.34,
    volume: int = 200,
    direction: str = "BUY",
) -> _Trade:
    return _Trade(
        trade_id=f"T-{code}",
        order_id=f"O-{code}",
        code=code,
        price=price,
        volume=volume,
        amount=price * volume,
        direction=direction,
        commission=5.0,
        stamp_tax=0.0 if direction == "BUY" else 1.23,
        slippage_cost=0.37,
        transfer_fee=0.0,
        net_amount=price * volume,
        traded_at=NOW.replace(hour=10),
    )


class TestDeriveVwapBasis:
    def test_plausible_vwap_is_ok(self) -> None:
        vwap, quality = derive_vwap_basis(12.34, 12.20)
        assert vwap == 12.20
        assert quality is VwapQuality.OK

    def test_missing_vwap(self) -> None:
        assert derive_vwap_basis(12.34, None) == (None, VwapQuality.MISSING)

    def test_unit_corrupted_vwap_dropped(self) -> None:
        # amount in 千元 → VWAP 1000x off; must not be recorded.
        vwap, quality = derive_vwap_basis(12.34, 12_340.0)
        assert vwap is None
        assert quality is VwapQuality.IMPLAUSIBLE

    def test_nonpositive_vwap_is_missing(self) -> None:
        assert derive_vwap_basis(12.34, 0.0) == (None, VwapQuality.MISSING)


class TestNormalizeKlineVwap:
    def test_share_unit_volume(self) -> None:
        # volume in shares: amount/volume directly plausible.
        vwap = normalize_kline_vwap(
            amount=2_468_000.0, volume=200_000.0, close=12.30
        )
        assert vwap == pytest.approx(12.34)

    def test_lot_unit_volume_rescued(self) -> None:
        # volume in 手 (100 shares): naive VWAP is 100x; the /100
        # candidate is the plausible one.
        vwap = normalize_kline_vwap(
            amount=2_468_000.0, volume=2_000.0, close=12.30
        )
        assert vwap == pytest.approx(12.34)

    def test_garbage_units_give_none(self) -> None:
        assert (
            normalize_kline_vwap(amount=2_468.0, volume=2_000.0, close=12.30)
            is None
        )

    def test_zero_inputs_give_none(self) -> None:
        assert (
            normalize_kline_vwap(amount=0.0, volume=100.0, close=12.30)
            is None
        )


class TestBuildTradeFact:
    def test_buy_below_vwap_scores_positive(self) -> None:
        fact = build_trade_fact(
            _trade(price=12.20),
            day_vwap=12.34,
            entry_cost_price=None,
            policy_hash="abc123",
            style=None,
        )
        assert fact.side is TradeSide.BUY
        assert fact.execution_vs_vwap_bps is not None
        assert fact.execution_vs_vwap_bps > 0
        assert fact.policy_hash == "abc123"

    def test_sell_above_vwap_scores_positive(self) -> None:
        fact = build_trade_fact(
            _trade(price=12.50, direction="SELL"),
            day_vwap=12.34,
            entry_cost_price=None,
            policy_hash=None,
            style=None,
        )
        assert fact.side is TradeSide.SELL
        assert fact.execution_vs_vwap_bps is not None
        assert fact.execution_vs_vwap_bps > 0

    def test_sell_holding_return_from_entry_cost(self) -> None:
        fact = build_trade_fact(
            _trade(price=13.20, direction="SELL"),
            day_vwap=None,
            entry_cost_price=12.00,
            policy_hash=None,
            style=None,
        )
        assert fact.entry_cost_price == 12.00
        assert fact.holding_return_pct == pytest.approx(0.10)

    def test_buy_never_gets_holding_return(self) -> None:
        fact = build_trade_fact(
            _trade(price=12.34),
            day_vwap=None,
            entry_cost_price=12.00,
            policy_hash=None,
            style=None,
        )
        assert fact.entry_cost_price is None
        assert fact.holding_return_pct is None

    def test_implausible_vwap_recorded_as_null(self) -> None:
        fact = build_trade_fact(
            _trade(price=12.34),
            day_vwap=12_340.0,
            entry_cost_price=None,
            policy_hash=None,
            style=None,
        )
        assert fact.day_vwap is None
        assert fact.execution_vs_vwap_bps is None
        assert fact.vwap_quality is VwapQuality.IMPLAUSIBLE


class TestBuildDailyReview:
    def test_empty_day_still_produces_record(self) -> None:
        record = build_daily_review(
            trade_date="2026-06-12",
            created_at=NOW,
            trades=(),
            vwap_by_code={},
            entry_cost_by_code={},
            policy_hash=None,
        )
        assert record.trade_facts == ()
        assert record.trade_date == "2026-06-12"

    def test_full_day_assembly_is_deterministic(self) -> None:
        trades = (
            _trade(code="600519", price=12.20),
            _trade(code="300433", price=41.50, direction="SELL"),
        )
        kwargs: dict[str, object] = {
            "trade_date": "2026-06-12",
            "created_at": NOW,
            "trades": trades,
            "vwap_by_code": {"600519": 12.34, "300433": 41.30},
            "entry_cost_by_code": {"300433": 40.00},
            "policy_hash": "ph-1",
            "counterfactuals": (
                CounterfactualEntry(
                    signal_id="QM-20260612-093500-000001-HOLD-001",
                    kind=CounterfactualKind.HOLD_PLAN,
                    pre_registered=True,
                    promotable=True,
                ),
            ),
            "risk_rejected_count": 3,
            "builder_early_return_count": 1,
        }
        a = build_daily_review(**kwargs)  # type: ignore[arg-type]
        b = build_daily_review(**kwargs)  # type: ignore[arg-type]
        assert a.model_dump(exclude={"record_id"}) == b.model_dump(
            exclude={"record_id"}
        )
        sell = next(f for f in a.trade_facts if f.side is TradeSide.SELL)
        assert sell.holding_return_pct == pytest.approx((41.50 - 40.0) / 40.0)
        assert a.risk_rejected_count == 3
