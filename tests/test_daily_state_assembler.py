"""DailyTradingState assembler tests (D-002)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import CircuitBreakerConfig
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.data.market_meta_provider import InMemoryMarketMetaProvider
from backend.models.equity import EquityPoint, EquityPointQuality
from backend.risk.circuit_breaker import CircuitBreaker
from backend.services.daily_state_assembler import (
    assemble_daily_risk_inputs,
    assemble_daily_state,
    compute_today_portfolio_pnl_pct,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class _FakeSession:
    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        yield

    async def end_session(self) -> None:
        return None


@dataclass
class _FakeClient:
    async def start_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        reverse = direction == -1
        self._docs = sorted(self._docs, key=lambda d: d.get(field, 0), reverse=reverse)
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], session=None) -> None:
        self.docs.append(dict(document))

    def find(self, filter=None, projection=None) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                rows = [r for r in rows if r.get("sequence", 0) > gt["$gt"]]
        return _FakeCursor(rows)


@pytest.fixture()
def event_store() -> BrokerEventStore:
    return BrokerEventStore(_FakeClient(), _FakeCollection())


@pytest.fixture()
def market_meta() -> InMemoryMarketMetaProvider:
    return InMemoryMarketMetaProvider(current_price={"600519": 100.5})


@pytest.fixture()
def breaker() -> CircuitBreaker:
    return CircuitBreaker(
        CircuitBreakerConfig(
            daily_loss_limit_pct=0.05,
            consecutive_loss_count=3,
            cooldown_minutes=60,
            apply_to_sell_orders=False,
        )
    )


@pytest.mark.asyncio
async def test_assembler_returns_state_with_live_price(
    event_store: BrokerEventStore,
    market_meta: InMemoryMarketMetaProvider,
    breaker: CircuitBreaker,
) -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    state = await assemble_daily_state(
        stock_code="600519",
        now=now,
        event_store=event_store,
        market_meta=market_meta,
        circuit_breaker=breaker,
        day_open_nav=1_000_000.0,
        current_nav=1_050_000.0,
    )
    assert state.today_new_instruction_count == 0
    assert state.current_price == 100.5
    assert state.today_portfolio_pnl_pct == pytest.approx(0.05)
    assert state.is_in_halt_cooldown is False
    assert state.halt_until is None


@pytest.mark.asyncio
async def test_assembler_counts_today_dispatched_events(
    event_store: BrokerEventStore,
    market_meta: InMemoryMarketMetaProvider,
    breaker: CircuitBreaker,
) -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    # Two ORDER_PLACED events today + one EXECUTION_REPORT_APPLIED today +
    # one event from yesterday.
    for occ in (
        dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        dt.datetime(2026, 5, 15, 9, 45, tzinfo=SHANGHAI),
    ):
        await event_store.append(
            event_type=BrokerEventType.ORDER_PLACED,
            occurred_at=occ,
            payload={"direction": "BUY", "frozen_amount": 100.0},
        )
    await event_store.append(
        event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
        occurred_at=dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        payload={"cash_delta": -100.0},
    )
    await event_store.append(
        event_type=BrokerEventType.ORDER_PLACED,
        occurred_at=dt.datetime(2026, 5, 14, 14, 0, tzinfo=SHANGHAI),
        payload={"direction": "BUY", "frozen_amount": 1.0},
    )
    # ORDER_FILLED is not counted in the 5-cap (BUY+SELL dispatch only)
    await event_store.append(
        event_type=BrokerEventType.ORDER_FILLED,
        occurred_at=dt.datetime(2026, 5, 15, 9, 31, tzinfo=SHANGHAI),
        payload={"direction": "BUY", "code": "x", "volume": 100,
                 "fill_price": 1.0, "frozen_amount": 100.0,
                 "commission": 0.0, "stamp_tax": 0.0, "transfer_fee": 0.0},
    )
    state = await assemble_daily_state(
        stock_code="600519",
        now=now,
        event_store=event_store,
        market_meta=market_meta,
        circuit_breaker=breaker,
        day_open_nav=1_000_000.0,
        current_nav=1_000_000.0,
    )
    assert state.today_new_instruction_count == 3


@pytest.mark.asyncio
async def test_assembler_handles_stale_quote_with_none(
    event_store: BrokerEventStore,
    breaker: CircuitBreaker,
) -> None:
    meta = InMemoryMarketMetaProvider()
    meta.set_current_price_stale("600519")
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    state = await assemble_daily_state(
        stock_code="600519",
        now=now,
        event_store=event_store,
        market_meta=meta,
        circuit_breaker=breaker,
        day_open_nav=1_000_000.0,
        current_nav=1_000_000.0,
    )
    assert state.current_price is None


@pytest.mark.asyncio
async def test_assembler_emits_halt_window_when_breaker_active(
    event_store: BrokerEventStore,
    market_meta: InMemoryMarketMetaProvider,
) -> None:
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            daily_loss_limit_pct=0.01,  # very low so 1 loss trips
            consecutive_loss_count=10,
            cooldown_minutes=15,
            apply_to_sell_orders=False,
        )
    )
    trip_at = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    breaker.record_trade_result(-0.02, now=trip_at)
    now = trip_at + dt.timedelta(minutes=5)
    state = await assemble_daily_state(
        stock_code="600519",
        now=now,
        event_store=event_store,
        market_meta=market_meta,
        circuit_breaker=breaker,
        day_open_nav=1_000_000.0,
        current_nav=999_000.0,
    )
    assert state.is_in_halt_cooldown is True
    assert state.halt_until is not None
    # halt_until = trip_at + cooldown_minutes
    assert state.halt_until == trip_at + dt.timedelta(minutes=15)


@pytest.mark.asyncio
async def test_assembler_passes_recent_trade_pnls(
    event_store: BrokerEventStore,
    market_meta: InMemoryMarketMetaProvider,
    breaker: CircuitBreaker,
) -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    state = await assemble_daily_state(
        stock_code="600519",
        now=now,
        event_store=event_store,
        market_meta=market_meta,
        circuit_breaker=breaker,
        day_open_nav=1_000_000.0,
        current_nav=1_000_000.0,
        recent_trade_pnls=(-100.0, 50.0, -25.0, 75.0),
    )
    # Only the last 3 are kept
    assert state.last_3_trade_pnls == (50.0, -25.0, 75.0)


# ---------------------------------------------------------------------------
# P0-7-amendment-2026-06-23 — daily-loss-brake NAV + combined risk inputs
# ---------------------------------------------------------------------------


def _eq_point(
    trade_date: str, total_equity: float, *, initial: float = 1_000_000.0
) -> EquityPoint:
    """Build a consistent EquityPoint (validator: total == cash+frozen+mv)."""
    return EquityPoint(
        snapshot_at=dt.datetime.fromisoformat(f"{trade_date}T15:00:00+08:00"),
        trade_date=trade_date,
        cash=total_equity,
        frozen_cash=0.0,
        market_value=0.0,
        total_equity=total_equity,
        initial_capital=initial,
        pnl=total_equity - initial,
        pnl_pct=(total_equity - initial) / initial,
        quality=EquityPointQuality.FRESH,
    )


class _FakeEquityRepo:
    """get_latest() = newest tick; get_latest_before_trade_date = seeded prior
    session close (None unless strictly before the query date)."""

    def __init__(
        self, latest: EquityPoint | None, prior: EquityPoint | None = None
    ) -> None:
        self._latest = latest
        self._prior = prior

    async def get_latest(self) -> EquityPoint | None:
        return self._latest

    async def get_latest_before_trade_date(
        self, trade_date: str
    ) -> EquityPoint | None:
        if self._prior is not None and self._prior.trade_date < trade_date:
            return self._prior
        return None


@pytest.mark.asyncio
async def test_compute_pnl_uses_prior_day_close() -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 1_050_000.0),
        prior=_eq_point("2026-05-14", 1_000_000.0),
    )
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == pytest.approx(
        0.05
    )


@pytest.mark.asyncio
async def test_compute_pnl_drawdown_sign_reaches_threshold() -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 940_000.0),
        prior=_eq_point("2026-05-14", 1_000_000.0),
    )
    # -6% → below the -5% daily-loss threshold (check 13 / breaker would halt).
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == pytest.approx(
        -0.06
    )


@pytest.mark.asyncio
async def test_compute_pnl_stale_latest_is_fail_safe_zero() -> None:
    # Newest point is YESTERDAY's close (today's MTM has not ticked yet). Must
    # NOT mis-read yesterday's -6% as today's drawdown (codex review #2/#8).
    now = dt.datetime(2026, 5, 15, 9, 31, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-14", 940_000.0),  # not dated today
        prior=_eq_point("2026-05-13", 1_000_000.0),
    )
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == 0.0


@pytest.mark.asyncio
async def test_compute_pnl_prior_survives_long_holiday_gap() -> None:
    # Prior close is 15 calendar days ago (after a long holiday) — the unbounded
    # lookback must still find it, not fall back to seed (codex review #1).
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 1_010_000.0),
        prior=_eq_point("2026-04-30", 1_000_000.0),  # 15 days earlier
    )
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == pytest.approx(
        0.01
    )


@pytest.mark.asyncio
async def test_compute_pnl_cold_start_uses_initial_capital() -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 1_020_000.0, initial=1_000_000.0),
        prior=None,  # genuine first session → seed-capital reference
    )
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == pytest.approx(
        0.02
    )


@pytest.mark.asyncio
async def test_compute_pnl_zero_equity_wipeout_trips_fail_safe() -> None:
    # A validated total_equity of 0 is a real near-total loss → large negative
    # ratio → halt (fail-SAFE), NOT a fail-open 0.0 (codex review #4).
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 0.0),
        prior=_eq_point("2026-05-14", 1_000_000.0),
    )
    assert await compute_today_portfolio_pnl_pct(repo, now=now) == pytest.approx(
        -1.0
    )


@pytest.mark.asyncio
async def test_compute_pnl_none_repo_and_no_point_are_zero() -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    assert await compute_today_portfolio_pnl_pct(None, now=now) == 0.0
    empty = _FakeEquityRepo(latest=None, prior=None)
    assert await compute_today_portfolio_pnl_pct(empty, now=now) == 0.0


@pytest.mark.asyncio
async def test_risk_inputs_combine_count_and_pnl(
    event_store: BrokerEventStore,
) -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)
    await event_store.append(
        event_type=BrokerEventType.ORDER_PLACED,
        occurred_at=dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        payload={"direction": "BUY", "frozen_amount": 1.0},
    )
    repo = _FakeEquityRepo(
        latest=_eq_point("2026-05-15", 950_000.0),
        prior=_eq_point("2026-05-14", 1_000_000.0),
    )
    count, pnl = await assemble_daily_risk_inputs(
        event_store=event_store, equity_repo=repo, now=now
    )
    assert count == 1
    assert pnl == pytest.approx(-0.05)


@pytest.mark.asyncio
async def test_risk_inputs_failopen_on_equity_error(
    event_store: BrokerEventStore,
) -> None:
    now = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)

    class _BoomRepo:
        async def get_latest(self) -> EquityPoint | None:
            raise RuntimeError("mongo down")

        async def get_latest_before_trade_date(
            self, trade_date: str
        ) -> EquityPoint | None:
            return None

    count, pnl = await assemble_daily_risk_inputs(
        event_store=event_store, equity_repo=_BoomRepo(), now=now
    )
    assert count == 0  # no events appended
    assert pnl == 0.0  # equity read error → fail-open (infra glitch)
