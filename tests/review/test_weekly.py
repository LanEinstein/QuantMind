"""AA-003 weekly review window resolution + aggregation tests."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.review.models import (
    CounterfactualEntry,
    CounterfactualKind,
    DailyReviewRecord,
    ReviewLane,
    TradeFact,
    TradeSide,
    VwapQuality,
)
from backend.review.store import MongoWeeklyReviewStore
from backend.review.weekly import build_weekly_review, resolve_review_week

SHANGHAI = ZoneInfo("Asia/Shanghai")
# 2026-06-13 is a Saturday; 2026-06-12 (Fri) is a trading day.
SAT_10 = dt.datetime(2026, 6, 13, 10, 0, tzinfo=SHANGHAI)


def _fact(
    *,
    code: str = "600519",
    side: TradeSide = TradeSide.BUY,
    holding_return: float | None = None,
    bps: float | None = None,
) -> TradeFact:
    return TradeFact(
        trade_id=f"T-{code}-{side.value}",
        order_id="O-1",
        code=code,
        side=side,
        volume=200,
        price=12.34,
        amount=2468.0,
        traded_at=SAT_10 - dt.timedelta(days=1),
        commission=5.0,
        stamp_tax=1.0 if side is TradeSide.SELL else 0.0,
        transfer_fee=0.5,
        slippage_cost=0.37,
        day_vwap=12.30 if bps is not None else None,
        execution_vs_vwap_bps=bps,
        vwap_quality=(
            VwapQuality.OK if bps is not None else VwapQuality.MISSING
        ),
        entry_cost_price=11.0 if holding_return is not None else None,
        holding_return_pct=holding_return,
    )


def _daily(
    trade_date: str, facts: tuple[TradeFact, ...] = ()
) -> DailyReviewRecord:
    return DailyReviewRecord(
        trade_date=trade_date,
        created_at=SAT_10,
        trade_facts=facts,
        counterfactuals=(
            CounterfactualEntry(
                signal_id=f"hold-{trade_date}",
                kind=CounterfactualKind.HOLD_PLAN,
                pre_registered=True,
                promotable=True,
            ),
        ),
        risk_rejected_count=1,
        builder_early_return_count=2,
    )


class TestResolveReviewWeek:
    def test_saturday_reviews_its_own_week(self) -> None:
        week = resolve_review_week(SAT_10)
        assert week is not None
        assert week.week_key == "2026-W24"
        assert week.window_start.isoformat() == "2026-06-08"
        assert week.last_trading_date.isoformat() == "2026-06-12"
        assert week.expected_trade_dates == (
            "2026-06-08",
            "2026-06-09",
            "2026-06-10",
            "2026-06-11",
            "2026-06-12",
        )
        assert week.complete

    def test_sunday_agrees_with_saturday(self) -> None:
        sun = resolve_review_week(SAT_10 + dt.timedelta(days=1))
        sat = resolve_review_week(SAT_10)
        assert sun is not None and sat is not None
        assert sun.week_key == sat.week_key
        assert sun.complete

    def test_monday_holiday_reviews_previous_week(self) -> None:
        # Pretend Monday 06-15 is a holiday: last trading day = Fri 06-12.
        def fake_trading(d: dt.date) -> bool:
            if d == dt.date(2026, 6, 15):
                return False
            return d.weekday() < 5

        mon = dt.datetime(2026, 6, 15, 10, 0, tzinfo=SHANGHAI)
        week = resolve_review_week(mon, is_trading_day_fn=fake_trading)
        assert week is not None
        assert week.week_key == "2026-W24"
        assert week.complete

    def test_midweek_holiday_is_incomplete(self) -> None:
        # Pretend Wednesday 06-17 is a holiday: last trading day = Tue
        # 06-16 → review week = W25, but Thu/Fri are still ahead.
        def fake_trading(d: dt.date) -> bool:
            if d == dt.date(2026, 6, 17):
                return False
            return d.weekday() < 5

        wed = dt.datetime(2026, 6, 17, 10, 0, tzinfo=SHANGHAI)
        week = resolve_review_week(wed, is_trading_day_fn=fake_trading)
        assert week is not None
        assert week.week_key == "2026-W25"
        assert not week.complete

    def test_no_trading_history_returns_none(self) -> None:
        assert (
            resolve_review_week(SAT_10, is_trading_day_fn=lambda _d: False)
            is None
        )


class TestBuildWeeklyReview:
    def test_aggregates_and_missing_dates(self) -> None:
        week = resolve_review_week(SAT_10)
        assert week is not None
        dailies = (
            _daily(
                "2026-06-11",
                facts=(
                    _fact(side=TradeSide.BUY, bps=12.0),
                    _fact(
                        code="300433",
                        side=TradeSide.SELL,
                        holding_return=0.10,
                        bps=-4.0,
                    ),
                ),
            ),
            _daily(
                "2026-06-12",
                facts=(
                    _fact(
                        code="600909",
                        side=TradeSide.SELL,
                        holding_return=-0.02,
                    ),
                ),
            ),
        )
        record = build_weekly_review(
            week=week,
            created_at=SAT_10,
            lane=ReviewLane.WEEKEND,
            daily_records=dailies,
            policy_hash="ph-1",
        )
        assert record.week_key == "2026-W24"
        assert record.reviewed_trade_dates == ("2026-06-11", "2026-06-12")
        assert record.missing_trade_dates == (
            "2026-06-08",
            "2026-06-09",
            "2026-06-10",
        )
        assert record.total_trades == 3
        assert record.buy_count == 1
        assert record.sell_count == 2
        assert record.sell_with_return_count == 2
        assert record.sell_win_count == 1
        assert record.avg_execution_vs_vwap_bps == pytest.approx(4.0)
        assert record.risk_rejected_total == 2
        assert record.builder_early_return_total == 4
        assert record.counterfactual_total == 2
        assert record.counterfactual_promotable_total == 2
        assert record.policy_hash == "ph-1"

    def test_empty_week_record(self) -> None:
        week = resolve_review_week(SAT_10)
        assert week is not None
        record = build_weekly_review(
            week=week,
            created_at=SAT_10,
            lane=ReviewLane.HOLIDAY_CATCHUP,
            daily_records=(),
            policy_hash=None,
        )
        assert record.total_trades == 0
        assert record.avg_execution_vs_vwap_bps is None
        assert record.missing_trade_dates == week.expected_trade_dates


class _FakeColl:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.docs.append(dict(document))

    async def find_one(self, query: dict) -> dict | None:
        for doc in self.docs:
            if doc.get("week_key") == query.get("week_key"):
                return dict(doc)
        return None


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeColl()

    def __getitem__(self, name: str) -> _FakeColl:
        assert name == MongoWeeklyReviewStore.COLLECTION
        return self.coll


class TestMongoWeeklyReviewStore:
    @pytest.mark.asyncio
    async def test_append_get_round_trip_and_idempotence(self) -> None:
        week = resolve_review_week(SAT_10)
        assert week is not None
        record = build_weekly_review(
            week=week,
            created_at=SAT_10,
            lane=ReviewLane.WEEKEND,
            daily_records=(),
            policy_hash=None,
        )
        db = _FakeDb()
        store = MongoWeeklyReviewStore(db)
        assert await store.append(record) is True
        assert await store.exists(record.week_key) is True
        assert await store.append(record) is False
        revived = await store.get(record.week_key)
        assert revived is not None
        assert revived.week_key == record.week_key
        assert len(db.coll.docs) == 1

    @pytest.mark.asyncio
    async def test_no_update_or_delete_surface(self) -> None:
        forbidden = {"update", "update_one", "delete", "delete_one"}
        public = {
            n for n in dir(MongoWeeklyReviewStore) if not n.startswith("_")
        }
        assert forbidden.isdisjoint(public)
