"""Phase I-001 — simulation_auto integration acceptance tests.

Three layers of coverage land here:

1. **Mongo-backed repository round-trips** — every adapter in
   :mod:`backend.services.mongo_repositories` accepts the domain model,
   round-trips it through a fake Motor collection, and yields the same
   model on read. Mongo coerces tuples → lists + BSON Date → naive
   datetime; the tests pin both paths so a future driver upgrade can't
   silently break the read path.

2. **5-day simulation_auto loop** — drives a deterministic 5-trading-day
   sequence through SimulationExecutor → MockBroker → EquityPointBuilder
   → AcceptanceService. Asserts:
   * zero illegal :class:`InstructionStatus` transitions (the state
     machine is the single owner — every path must go DRAFT → VALIDATED
     → DISPATCHED → FILLED / EXPIRED / REJECTED / AMBIGUOUS),
   * one acceptance_report row per simulated day,
   * MockBroker mirror state is internally consistent at EOD (cash +
     frozen_cash + market_value sums match equity_point.total_equity
     within the 0.05 CNY tolerance).

3. **main.py wiring smoke** — the lifespan event chain attaches all
   eight Phase I-001 components onto ``app.state`` without crashing,
   and the acceptance gate (P0-6 §2 红线 5) fail-closes when
   ``FEISHU_INTERACTIVE_ENABLED=true`` is set without a PASS acceptance
   report.

Mongo + Redis are mocked here — these are integration-shape tests that
exercise the wiring contract, not a real-replica-set load test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.data.market_meta_provider import StaleQuoteError
from backend.models.equity import (
    EquityPoint,
    EquityPointPosition,
    EquityPointQuality,
)
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)
from backend.models.reconciliation import (
    DailyReconciliation,
    DeviationReport,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.services.acceptance_report import (
    AcceptanceComputeInput,
    AcceptanceOutcome,
    AcceptanceReport,
    AcceptanceService,
    StabilityCounters,
    StrategyCounters,
)
from backend.services.mongo_repositories import (
    MongoAcceptanceRepository,
    MongoDailyReconciliationStore,
    MongoEquityPointRepository,
    MongoInstructionPlanRepository,
    MongoSnapshotLookup,
    MongoTicketRepository,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = dt.UTC


# ---------------------------------------------------------------------------
# Fake Motor collection + database
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Tiny in-memory async cursor that mimics Motor's iteration semantics."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def sort(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        if not args:
            return self
        field, direction = args[0], (args[1] if len(args) > 1 else 1)
        self._rows.sort(
            key=lambda r: r.get(field),
            reverse=(direction == -1),
        )
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._rows = self._rows[:n]
        return self

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def _iter() -> AsyncIterator[dict[str, Any]]:
            for row in list(self._rows):
                yield dict(row)

        return _iter()


class _FakeCollection:
    """Minimal Motor collection stub backing the Mongo repository tests."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def find(self, query: dict[str, Any] | None = None) -> _FakeCursor:
        query = query or {}
        matched = [r for r in self.rows if _matches(r, query)]
        return _FakeCursor(matched)

    async def find_one(
        self, query: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        for row in self.rows:
            if _matches(row, query or {}):
                return dict(row)
        return None

    async def update_one(
        self, filter_: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> Any:
        for i, row in enumerate(self.rows):
            if _matches(row, filter_):
                merged = {**row}
                merged.update(update.get("$set", {}))
                self.rows[i] = merged
                result = MagicMock()
                result.matched_count = 1
                result.modified_count = 1
                result.upserted_id = None
                return result
        if upsert:
            merged = {**filter_}
            merged.update(update.get("$set", {}))
            self.rows.append(merged)
        result = MagicMock()
        result.matched_count = 0
        result.modified_count = 0
        result.upserted_id = "_id_placeholder"
        return result

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self.rows.append(dict(doc))
        result = MagicMock()
        result.inserted_id = "_id_placeholder"
        return result

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(1 for r in self.rows if _matches(r, query))


class _FakeDatabase:
    def __init__(self) -> None:
        self._colls: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self._colls:
            self._colls[name] = _FakeCollection()
        return self._colls[name]


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
        else:
            if row.get(key) != expected:
                return False
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> _FakeDatabase:
    return _FakeDatabase()


def _instruction_plan(
    instruction_id: str = "QM-20260515-100001-600519-BUY-001",
    *,
    side: InstructionSide = InstructionSide.BUY,
    status: InstructionStatus = InstructionStatus.VALIDATED,
    stock_code: str = "600519",
    trade_date: str = "2026-05-15",
    created_at: dt.datetime | None = None,
    volume: int | None = 100,
    limit_price: float | None = 1800.0,
    risk_summary: tuple[RiskCheckSummary, ...] | None = None,
    rejection_reason: str | None = None,
) -> InstructionPlan:
    created_at = created_at or dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI)
    valid_until = created_at.replace(hour=14, minute=55, second=0, microsecond=0)
    snapshot_at = created_at - dt.timedelta(seconds=30)
    if risk_summary is None:
        risk_summary = tuple(
            RiskCheckSummary(
                rule_name=f"rule_{i:02d}",
                passed=True,
                threshold=None,
                actual=None,
                message="",
            )
            for i in range(1, 15)
        )
    return InstructionPlan(
        instruction_id=instruction_id,
        created_at=created_at,
        valid_until=valid_until,
        trade_date=trade_date,
        stock_code=stock_code,
        stock_name="贵州茅台",
        side=side,
        volume=volume if side is not InstructionSide.HOLD else None,
        limit_price=limit_price if side is not InstructionSide.HOLD else None,
        data_snapshot=DataSnapshot(
            snapshot_at=snapshot_at,
            quote_source="adata",
            quote_latency_ms=120,
            news_sources_by_domain={"financial": ("stock_news_em",)},
            news_window_seconds=600,
            prev_close=1800.0,
            is_trading_day=True,
            is_trading_hours=True,
        ),
        evidence_ids=("NEWS-20260515-001",),
        position_summary=(
            None
            if side is InstructionSide.HOLD
            else PositionSummary(
                pre_position_pct=0.0,
                post_position_pct=0.18,
                pre_total_position_pct=0.0,
                post_total_position_pct=0.18,
                pre_cash=1_000_000.0,
                post_cash=820_000.0,
            )
        ),
        risk_summary=risk_summary,
        risk_validation_id="RV-20260515-001",
        signal_id="SIG-20260515-001",
        analysis_record_id="AR-20260515-001",
        debate_round_count=1,
        invalidation_summary="N/A",
        status=status,
        rejection_reason=(
            rejection_reason
            if status in (InstructionStatus.REJECTED, InstructionStatus.AMBIGUOUS)
            else None
        ),
    )


def _equity_point(
    *,
    snapshot_at: dt.datetime | None = None,
    trade_date: str = "2026-05-15",
    cash: float = 1_000_000.0,
    market_value: float = 0.0,
    quality: EquityPointQuality = EquityPointQuality.FRESH,
    positions: tuple[EquityPointPosition, ...] = (),
) -> EquityPoint:
    snapshot_at = snapshot_at or dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI)
    total_equity = round(cash + market_value, 2)
    return EquityPoint(
        snapshot_at=snapshot_at,
        trade_date=trade_date,
        cash=cash,
        frozen_cash=0.0,
        market_value=market_value,
        total_equity=total_equity,
        initial_capital=1_000_000.0,
        pnl=round(total_equity - 1_000_000.0, 2),
        pnl_pct=round((total_equity - 1_000_000.0) / 1_000_000.0, 6),
        quality=quality,
        positions=positions,
        last_broker_event_id=None,
    )


def _ticket(
    *,
    ticket_id: str = "RECON-20260515-001",
    status: ReconciliationTicketStatus = ReconciliationTicketStatus.OPEN,
    trade_date: str = "2026-05-15",
    resolved_at: dt.datetime | None = None,
    amended_snapshot: MockBrokerSnapshot | None = None,
) -> ReconciliationTicket:
    deviation = DeviationReport(
        ticket_id=ticket_id,
        overall_passed=False,
        deviations=(),
    )
    return ReconciliationTicket(
        ticket_id=ticket_id,
        trade_date=trade_date,
        created_at=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        deviation_report=deviation,
        expected_snapshot_id="snap-abc",
        actual_reconciliation_id="recon-actual-1",
        status=status,
        resolved_at=resolved_at,
        amended_snapshot=amended_snapshot,
    )


# ===========================================================================
# 1. Repository round-trip tests
# ===========================================================================


class TestMongoInstructionPlanRepository:
    @pytest.mark.asyncio
    async def test_upsert_then_get_by_id(self, db: _FakeDatabase) -> None:
        repo = MongoInstructionPlanRepository(db)
        plan = _instruction_plan()
        await repo.upsert(plan)
        loaded = await repo.get_by_id(plan.instruction_id)
        assert loaded is not None
        assert loaded.instruction_id == plan.instruction_id
        assert loaded.side is plan.side
        assert loaded.volume == plan.volume

    @pytest.mark.asyncio
    async def test_list_recent_orders_by_created_at_desc(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoInstructionPlanRepository(db)
        early = _instruction_plan(
            instruction_id="QM-20260515-100001-600519-BUY-001",
            created_at=dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI),
        )
        late = _instruction_plan(
            instruction_id="QM-20260515-110002-600519-BUY-002",
            created_at=dt.datetime(2026, 5, 15, 11, 0, 2, tzinfo=SHANGHAI),
        )
        await repo.upsert(early)
        await repo.upsert(late)
        plans = await repo.list_recent(limit=10, status=None, trade_date=None)
        assert [p.instruction_id for p in plans] == [
            late.instruction_id,
            early.instruction_id,
        ]

    @pytest.mark.asyncio
    async def test_list_recent_filters_by_status(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoInstructionPlanRepository(db)
        ok = _instruction_plan(
            instruction_id="QM-20260515-100001-600519-BUY-001",
            status=InstructionStatus.VALIDATED,
        )
        rejected = _instruction_plan(
            instruction_id="QM-20260515-100002-600519-BUY-002",
            status=InstructionStatus.REJECTED,
            created_at=dt.datetime(2026, 5, 15, 10, 0, 2, tzinfo=SHANGHAI),
            rejection_reason="position_limit",
        )
        await repo.upsert(ok)
        await repo.upsert(rejected)
        only_rejected = await repo.list_recent(
            limit=10, status=InstructionStatus.REJECTED.value, trade_date=None
        )
        assert len(only_rejected) == 1
        assert only_rejected[0].status is InstructionStatus.REJECTED

    @pytest.mark.asyncio
    async def test_broker_at_fill_returns_filled_event(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoInstructionPlanRepository(db)
        plan = _instruction_plan()
        await repo.upsert(plan)
        # Hand-seed a broker_events row for the plan to exercise the lookup.
        db[MongoInstructionPlanRepository.BROKER_EVENT_COLLECTION].rows.append(
            {
                "sequence": 42,
                "event_type": "order_filled",
                "correlation_id": plan.instruction_id,
                "payload": {"fill_price": 1801.23, "volume": 100},
            }
        )
        outcome = await repo.broker_at_fill(plan.instruction_id)
        assert outcome is not None
        assert outcome["outcome"] == "FILLED"
        assert outcome["fill_price"] == 1801.23
        assert outcome["broker_event_sequence"] == 42

    @pytest.mark.asyncio
    async def test_broker_at_fill_returns_rejected_when_no_fill(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoInstructionPlanRepository(db)
        plan = _instruction_plan()
        db[MongoInstructionPlanRepository.BROKER_EVENT_COLLECTION].rows.append(
            {
                "sequence": 17,
                "event_type": "order_rejected",
                "correlation_id": plan.instruction_id,
                "payload": {"reason": "price_limit_violation_at_fill"},
            }
        )
        outcome = await repo.broker_at_fill(plan.instruction_id)
        assert outcome is not None
        assert outcome["outcome"] == "REJECTED"
        assert outcome["reason"] == "price_limit_violation_at_fill"


class TestMongoEquityPointRepository:
    @pytest.mark.asyncio
    async def test_get_latest_returns_newest_snapshot(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoEquityPointRepository(db)
        first = _equity_point(
            snapshot_at=dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI)
        )
        second = _equity_point(
            snapshot_at=dt.datetime(2026, 5, 15, 9, 30, 30, tzinfo=SHANGHAI)
        )
        await repo.upsert(first)
        await repo.upsert(second)
        latest = await repo.get_latest()
        assert latest is not None
        assert latest.snapshot_at == second.snapshot_at

    @pytest.mark.asyncio
    async def test_get_latest_empty_returns_none(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoEquityPointRepository(db)
        assert await repo.get_latest() is None


class TestMongoTicketRepository:
    @pytest.mark.asyncio
    async def test_save_and_get(self, db: _FakeDatabase) -> None:
        repo = MongoTicketRepository(db)
        ticket = _ticket()
        await repo.save(ticket)
        loaded = await repo.get(ticket.ticket_id)
        assert loaded is not None
        assert loaded.ticket_id == ticket.ticket_id
        assert loaded.status is ReconciliationTicketStatus.OPEN

    @pytest.mark.asyncio
    async def test_list_open_for_date_filters_resolved(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoTicketRepository(db)
        open_ticket = _ticket(ticket_id="RECON-20260515-001")
        resolved_ticket = _ticket(
            ticket_id="RECON-20260515-002",
            status=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
            resolved_at=dt.datetime(2026, 5, 15, 16, 5, tzinfo=SHANGHAI),
        )
        expired_ticket = _ticket(
            ticket_id="RECON-20260515-003",
            status=ReconciliationTicketStatus.EXPIRED,
        )
        await repo.save(open_ticket)
        await repo.save(resolved_ticket)
        await repo.save(expired_ticket)
        rows = await repo.list_open_for_date("2026-05-15")
        ids = {t.ticket_id for t in rows}
        assert ids == {open_ticket.ticket_id, expired_ticket.ticket_id}


class TestMongoDailyReconciliationStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, db: _FakeDatabase) -> None:
        store = MongoDailyReconciliationStore(db)
        daily = DailyReconciliation(
            ticket_id="RECON-20260515-001",
            trade_date="2026-05-15",
            received_at=dt.datetime(2026, 5, 15, 16, 2, tzinfo=SHANGHAI),
            reported_cash=1_000_000.0,
            reported_positions=(),
            raw_text="对账无差异 RECON-20260515-001",
            parse_ok=True,
        )
        await store.save(daily)
        loaded = await store.get("2026-05-15")
        assert loaded is not None
        assert loaded.ticket_id == daily.ticket_id


class TestMongoSnapshotLookup:
    @pytest.mark.asyncio
    async def test_resolve_snapshot_from_broker_snapshots(
        self, db: _FakeDatabase
    ) -> None:
        lookup = MongoSnapshotLookup(db)
        db[MongoSnapshotLookup.COLLECTION].rows.append(
            {
                "snapshot_id": "snap-abc",
                "created_at": dt.datetime(
                    2026, 5, 15, 16, 0, 30, tzinfo=SHANGHAI
                ),
                "cash": 950_000.0,
                "frozen_cash": 50_000.0,
                "initial_capital": 1_000_000.0,
                "positions": [
                    {
                        "code": "600519",
                        "volume": 100,
                        "today_bought_volume": 100,
                        "cost_price": 1800.0,
                    }
                ],
                "checksum": "0123456789abcdef",
                "trade_date": "2026-05-15",
            }
        )
        snapshot = await lookup.get("snap-abc")
        assert snapshot is not None
        assert snapshot.cash == 950_000.0
        assert len(snapshot.positions) == 1
        assert snapshot.positions[0].code == "600519"

    @pytest.mark.asyncio
    async def test_missing_snapshot_returns_none(
        self, db: _FakeDatabase
    ) -> None:
        lookup = MongoSnapshotLookup(db)
        assert await lookup.get("snap-does-not-exist") is None


class TestMongoAcceptanceRepository:
    @pytest.mark.asyncio
    async def test_upsert_overwrites_same_trade_date(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoAcceptanceRepository(db)
        first = AcceptanceReport(
            computed_at=dt.datetime(2026, 5, 15, 16, 0, 30, tzinfo=UTC),
            trade_date="2026-05-15",
            window_start="2026-03-12",
            window_end="2026-05-15",
            trading_days_in_window=10,
            outcome=AcceptanceOutcome.INSUFFICIENT_DATA,
            metrics=(),
            notes="",
        )
        await repo.upsert(first)
        # Same trade_date — should overwrite, not append a second row.
        replacement = first.model_copy(update={"trading_days_in_window": 11})
        await repo.upsert(replacement)
        latest = await repo.latest()
        assert latest is not None
        assert latest.trading_days_in_window == 11
        coll = db[MongoAcceptanceRepository.COLLECTION]
        same_day = [r for r in coll.rows if r["trade_date"] == "2026-05-15"]
        assert len(same_day) == 1


# ===========================================================================
# 2. 5-day simulation_auto loop
# ===========================================================================


class _StubMarketMeta:
    """Deterministic market-meta for the 5-day loop.

    Holds a per-stock current price + prev_close; serves both the
    MockBroker at-fill recheck and the EquityPointBuilder.
    """

    def __init__(self, prev_close: dict[str, float]) -> None:
        self._prev = dict(prev_close)
        self._current: dict[str, float] = dict(prev_close)

    async def get_prev_close(self, code: str) -> float | None:
        return self._prev.get(code)

    async def get_current_price(
        self, code: str, *, now: dt.datetime | None = None
    ) -> float:
        if code not in self._current:
            raise StaleQuoteError(f"no quote for {code}")
        return self._current[code]

    def set(self, code: str, price: float) -> None:
        self._current[code] = price


def _broker_config() -> BrokerConfig:
    return BrokerConfig(initial_capital=1_000_000.0)


@pytest.mark.asyncio
async def test_simulation_auto_5_day_loop_yields_daily_acceptance_reports(
    db: _FakeDatabase,
) -> None:
    """Drive 5 simulated trading days through the I-001 pipeline.

    Daily steps per day::

        09:30 — broker quote refreshed
        10:00 — InstructionPlan emitted with status=VALIDATED
        10:00 — SimulationExecutor.route flips DISPATCHED → FILLED
        15:00 — EquityPointBuilder.build + repo.upsert
        16:00 — AcceptanceService.compute + repo.upsert

    Locked assertions:
        * Every status transition observed is in the lawful set per
          :class:`InstructionStatus` (no DRAFT→FILLED short-circuit, no
          REJECTED→VALIDATED un-reject).
        * The acceptance_reports collection has exactly five rows after
          the loop, one per simulated trading day.
        * The MockBroker NAV after each fill matches the EquityPoint
          total_equity within the 0.05 CNY model tolerance.
    """
    # Setup ---------------------------------------------------------------
    config = _broker_config()
    market_meta = _StubMarketMeta(prev_close={"600519": 1800.0})
    # Pin broker's clock to 10:00 Asia/Shanghai so the trading-hour check
    # passes on every simulated day — the test drives the clock manually
    # via market_meta.set + the explicit datetime hand-offs below.
    _current_now: list[dt.datetime] = [
        dt.datetime(2026, 5, 11, 10, 0, 1, tzinfo=SHANGHAI)
    ]
    broker = MockBroker(
        config,
        now_func=lambda: _current_now[0],
        market_meta=market_meta,
    )

    from backend.broker.equity import EquityPointBuilder

    equity_builder = EquityPointBuilder(broker, market_meta)
    equity_repo = MongoEquityPointRepository(db)

    acceptance_repo = MongoAcceptanceRepository(db)
    acceptance_service = AcceptanceService(repository=acceptance_repo)

    # Track every status transition the test observes for the assertion.
    lawful = {
        (InstructionStatus.DRAFT, InstructionStatus.VALIDATED),
        (InstructionStatus.DRAFT, InstructionStatus.REJECTED),
        (InstructionStatus.VALIDATED, InstructionStatus.DISPATCHED),
        (InstructionStatus.VALIDATED, InstructionStatus.REJECTED),
        (InstructionStatus.DISPATCHED, InstructionStatus.FILLED),
        (InstructionStatus.DISPATCHED, InstructionStatus.EXPIRED),
        (InstructionStatus.DISPATCHED, InstructionStatus.AMBIGUOUS),
    }
    transitions: list[tuple[InstructionStatus, InstructionStatus]] = []

    # 5 trading-day loop --------------------------------------------------
    start_date = dt.date(2026, 5, 11)  # Mon
    for offset in range(5):
        day = start_date + dt.timedelta(days=offset)
        trade_date = day.isoformat()

        # 09:30 — refresh the price (no-op on day 0 but mirrors a real
        # data tick; downstream EquityPointBuilder pulls the same value).
        market_meta.set("600519", 1800.0 + offset * 5.0)

        # 10:00 — VALIDATED plan emitted by Builder (D-003/D-004 stand-in).
        created_at = dt.datetime(
            day.year, day.month, day.day, 10, 0, 1, tzinfo=SHANGHAI
        )
        plan = _instruction_plan(
            instruction_id=(
                f"QM-{day.strftime('%Y%m%d')}-100001-600519-BUY-001"
            ),
            trade_date=trade_date,
            created_at=created_at,
        )
        transitions.append((InstructionStatus.DRAFT, plan.status))
        assert plan.status is InstructionStatus.VALIDATED

        # 10:00 — SimulationExecutor.route stand-in:
        #   place the order on MockBroker → DISPATCHED → FILLED.
        from backend.broker.models import OrderDirection, OrderType

        _current_now[0] = created_at
        transitions.append((plan.status, InstructionStatus.DISPATCHED))
        result = await broker.place_order(
            code=plan.stock_code,
            price=plan.limit_price,  # type: ignore[arg-type]
            volume=plan.volume,  # type: ignore[arg-type]
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
        )
        # ALL_OR_NONE: either fully filled or the test fails.
        assert result.success, f"day {trade_date} fill failed: {result.message}"
        transitions.append((InstructionStatus.DISPATCHED, InstructionStatus.FILLED))

        # 15:00 — intraday MTM tick.
        now_3pm = created_at.replace(hour=15, minute=0)
        point = await equity_builder.build(now=now_3pm, last_broker_event_id=offset + 1)
        await equity_repo.upsert(point)

        # NAV / equity consistency at this point (broker mirror vs EquityPoint).
        account = await broker.get_account()
        broker_nav = round(
            account.available_cash + account.frozen_cash + point.market_value,
            2,
        )
        assert abs(point.total_equity - broker_nav) <= 0.05, (
            f"day {trade_date}: equity_point.total_equity drifted from "
            f"broker NAV — {point.total_equity} vs {broker_nav}"
        )

        # 16:00 — acceptance recompute + upsert (Mongo path).
        now_4pm = created_at.replace(hour=16, minute=0, second=30)
        compute = AcceptanceComputeInput(
            now=now_4pm,
            trade_date=day,
            stability=StabilityCounters(
                completed_instructions=offset + 1,
                total_instructions=offset + 1,
                accurate_reports=0,
                total_reports=0,
                data_missing_ticks=0,
                total_data_ticks=10 * (offset + 1),
                llm_timeout_calls=0,
                total_llm_calls=4 * (offset + 1),
                generated_signal_days=offset + 1,
                expected_signal_days=offset + 1,
            ),
            strategy=StrategyCounters(
                max_drawdown_pct=0.0,
                pnl_cny=point.pnl,
                csi300_excess_pct=0.0,
            ),
            reconciliation_paused=False,
        )
        report = acceptance_service.compute(compute)
        await acceptance_service.upsert(report)

    # Assertions ----------------------------------------------------------
    # 1. Every transition tuple is in the lawful set.
    for prev, nxt in transitions:
        assert (prev, nxt) in lawful, f"illegal transition {prev}→{nxt}"

    # 2. Exactly five acceptance_report rows.
    coll = db[MongoAcceptanceRepository.COLLECTION]
    assert len(coll.rows) == 5
    trade_dates = {r["trade_date"] for r in coll.rows}
    assert trade_dates == {
        (start_date + dt.timedelta(days=i)).isoformat() for i in range(5)
    }

    # 3. ``can_switch_to_feishu_on`` rejects until a PASS row lands.
    # The synthetic counter set above produces a deterministic FAIL
    # (zero execution_reports → accurate_reports ratio = 0.0 misses the
    # 0.99 floor); the gate must therefore stay closed even though the
    # rolling 45-day window arithmetic returns a metric set.
    latest = await acceptance_repo.latest()
    assert latest is not None
    assert latest.outcome is not AcceptanceOutcome.PASS
    assert (await acceptance_service.can_switch_to_feishu_on()).allowed is False


# ===========================================================================
# 3. main.py wiring smoke
# ===========================================================================


@pytest.mark.asyncio
async def test_orchestration_layer_attaches_components(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """``_init_orchestration_layer`` populates the documented app.state slots.

    The lifespan helper is a black-box smoke check: feed it stubs for
    the data + trading layers and assert every expected slot is bound.
    The actual Mongo / Redis interaction is mocked because the only
    contract this test guards is the wiring graph.
    """
    from fastapi import FastAPI

    from backend.audit.store import AuditStore
    from backend.broker.models import BrokerConfig
    from backend.broker.registry import BrokerRegistry
    from backend.main import _init_orchestration_layer

    app = FastAPI()
    fake_db = _FakeDatabase()
    mongodb = MagicMock()
    mongodb._db = fake_db
    app.state.mongodb = mongodb
    app.state.mongo_client = MagicMock()
    app.state.audit_store = AuditStore(
        MagicMock(), jsonl_path=tmp_path / "audit.jsonl"
    )
    app.state.broker_registry = BrokerRegistry(BrokerConfig())
    app.state.redis = AsyncMock()
    app.state.feishu_client = None  # simulation_auto

    monkeypatch.delenv("FEISHU_DECISION_CHAT_ID", raising=False)
    # The integration smoke tests stub mongodb with a MagicMock —
    # MagicMock blocks ``assert_*`` attribute access, so the
    # BrokerScheduler replica-set fence cannot run. Opt out of the
    # gate for these tests (production never sets the env var).
    monkeypatch.setenv("QUANTMIND_BROKER_SKIP_RS_GATE", "1")
    # U-D1 — keep the Line-2 file stores (constructed by _init_line2_runners)
    # out of the repo working tree during the test.
    monkeypatch.setenv(
        "QUANTMIND_LINE2_SNAPSHOT_ROOT", str(tmp_path / "line2_snaps")
    )
    monkeypatch.setenv(
        "QUANTMIND_INTRADAY_MANIFEST_ROOT", str(tmp_path / "line2_manifests")
    )

    await _init_orchestration_layer(app)

    # Each slot below must be populated; the test fails noisily if any
    # of the 18 I-001 + 4 U-D1 Line-2 + 1 U-D1b Line-1 components were dropped.
    must_have = [
        "broker_event_store",
        "broker_snapshot_store",
        "market_meta_provider",
        "acceptance_repository",
        "acceptance_service",
        "execution_report_applier",
        "reconciliation_applier",
        "mode_router",
        "decision_ledger",
        "simulation_executor",
        "equity_point_repository",
        "equity_point_builder",
        "broker_scheduler",
        "instruction_plan_repository",
        "reconciliation_ticket_repository",
        "daily_reconciliation_store",
        "broker_snapshot_lookup",
        "execution_report_orchestrator",
        # U-D1 Line-2 orchestration slots.
        "instruction_dispatcher",
        "route_coordinator",
        "line2_daily_runner",
        "line2_intraday_runner",
        # U-D1b Line-1 runner slot (4-agent debate BUY line).
        "line1_runner",
    ]
    for name in must_have:
        assert getattr(app.state, name, None) is not None, (
            f"{name} was not attached to app.state"
        )

    # No FEISHU_DECISION_CHAT_ID → reconciliation orchestrator stays None
    # so feishu_interactive cannot bootstrap with a half-wired flow.
    assert app.state.reconciliation_orchestrator is None

    # Acceptance gate: latest report is None → can_switch_to_feishu_on is
    # False, which is the locked precondition for the feishu_interactive
    # SystemExit gate.
    assert (
        (await app.state.acceptance_service.can_switch_to_feishu_on()).allowed is False
    )

    # Codex Cycle 1 P2 regression — the dual-write cache shared between
    # the daily store and the ReconciliationApplier MUST be the same
    # dict object so a MISMATCH save by the orchestrator is visible to
    # the applier's sync ``dict.get`` lookup on the next decide call.
    cache = app.state.daily_reconciliation_cache
    assert isinstance(cache, dict)
    assert app.state.reconciliation_applier._daily is cache

    # Save a daily through the wrapped store; assert it lands in the
    # cache so the applier would resolve RESOLVED_USER_AS_TRUTH lookup.
    daily = DailyReconciliation(
        ticket_id="RECON-20260515-001",
        trade_date="2026-05-15",
        received_at=dt.datetime(2026, 5, 15, 16, 2, tzinfo=SHANGHAI),
        reported_cash=1_000_000.0,
        reported_positions=(),
        raw_text="对账无差异 RECON-20260515-001",
        parse_ok=True,
    )
    await app.state.daily_reconciliation_store.save(daily)
    # Codex Cycle 7 P2 fix — cache keyed by ticket_id (not trade_date)
    # so multi-ticket days don't collapse to a single overwrite.
    assert "RECON-20260515-001" in cache
    assert cache["RECON-20260515-001"].trade_date == "2026-05-15"

    # Clean up the broker scheduler so the test does not leave APScheduler
    # threads behind.
    await app.state.broker_scheduler.stop()


@pytest.mark.asyncio
async def test_orchestrator_decide_warms_daily_cache_for_user_as_truth() -> None:
    """Codex Cycle 2 P2 regression — RESOLVED_USER_AS_TRUTH path must
    warm the dual-write cache from the daily store BEFORE invoking the
    applier. Without the explicit warm-up call inside
    ``ReconciliationOrchestrator.decide_ticket``, a process restart
    between MISMATCH save and RESOLVE_USER decide would leave the
    applier-readable dict empty and the operator decision would crash
    with ValueError.
    """
    from datetime import datetime as _dt

    from backend.integrations.feishu.reconciliation import (
        ReconciliationOrchestrator,
    )
    from backend.integrations.feishu.renderer import MessageRenderer

    daily = DailyReconciliation(
        ticket_id="RECON-20260515-001",
        trade_date="2026-05-15",
        received_at=_dt(2026, 5, 15, 16, 2, tzinfo=SHANGHAI),
        reported_cash=1_000_000.0,
        reported_positions=(),
        raw_text="对账采纳: RECON-20260515-001",
        parse_ok=True,
    )

    # Daily store records every get() call so we can assert the
    # orchestrator warmed the cache before the applier ran.
    get_calls: list[str] = []
    save_calls: list[str] = []

    class _ProbeStore:
        async def save(self, d):
            save_calls.append(d.ticket_id)

        async def get(self, key: str):
            get_calls.append(key)
            return daily if key == daily.ticket_id else None

    ticket = _ticket(
        ticket_id="RECON-20260515-001",
        status=ReconciliationTicketStatus.OPEN,
        trade_date="2026-05-15",
    )

    class _TicketStore:
        def __init__(self) -> None:
            self._t = ticket

        async def get(self, ticket_id: str):
            return self._t if ticket_id == self._t.ticket_id else None

        async def save(self, t) -> None:
            self._t = t

        async def list_open_for_date(self, trade_date: str) -> tuple:
            return ()

    class _ProbeApplier:
        async def reset_to_snapshot(self, ticket, *, now):
            from backend.broker.appliers import ApplyResult

            return ApplyResult(
                cash_delta=0.0,
                positions_delta=(),
                broker_event_sequence=1,
                reason="probe",
            )

    orch = ReconciliationOrchestrator(
        feishu=None,
        renderer=MessageRenderer(),
        ticket_repo=_TicketStore(),
        daily_store=_ProbeStore(),
        applier=_ProbeApplier(),
        decision_chat_id="oc_probe",
    )

    result = await orch.decide_ticket(
        "RECON-20260515-001",
        resolution=ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
    )

    assert result.status is ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH
    # Codex Cycle 7 P2 fix — keyed by ticket_id (not trade_date) so
    # multi-ticket days don't collapse to the latest save.
    assert get_calls == ["RECON-20260515-001"], (
        "decide_ticket must warm the daily-cache via daily_store.get() "
        "by ticket_id before invoking the applier; otherwise a process "
        "restart with a persisted MISMATCH row crashes "
        "RESOLVED_USER_AS_TRUTH"
    )


@pytest.mark.asyncio
async def test_orchestrator_decide_skips_warmup_for_non_user_as_truth() -> None:
    """The cache warm-up is scoped to RESOLVED_USER_AS_TRUTH — other
    resolutions (SYSTEM_AS_TRUTH / AMENDED) don't read the daily map
    and the extra Mongo round-trip would be wasted work.
    """
    from backend.integrations.feishu.reconciliation import (
        ReconciliationOrchestrator,
    )
    from backend.integrations.feishu.renderer import MessageRenderer

    get_calls: list[str] = []

    class _ProbeStore:
        async def save(self, d) -> None: ...

        async def get(self, trade_date: str):
            get_calls.append(trade_date)
            return None

    ticket = _ticket(status=ReconciliationTicketStatus.OPEN)

    class _TicketStore:
        def __init__(self) -> None:
            self._t = ticket

        async def get(self, _):
            return self._t

        async def save(self, t) -> None:
            self._t = t

        async def list_open_for_date(self, _) -> tuple:
            return ()

    class _ProbeApplier:
        async def reset_to_snapshot(self, ticket, *, now):
            from backend.broker.appliers import ApplyResult

            return ApplyResult(
                cash_delta=0.0,
                positions_delta=(),
                broker_event_sequence=1,
                reason="probe",
            )

    orch = ReconciliationOrchestrator(
        feishu=None,
        renderer=MessageRenderer(),
        ticket_repo=_TicketStore(),
        daily_store=_ProbeStore(),
        applier=_ProbeApplier(),
        decision_chat_id="oc_probe",
    )

    await orch.decide_ticket(
        "RECON-20260515-001",
        resolution=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
    )

    assert get_calls == [], (
        "RESOLVED_SYSTEM_AS_TRUTH must not warm the daily cache — "
        "applier doesn't read it on that path"
    )


class TestMongoInstructionPlanLookupProtocol:
    """Codex Cycle 3 P1 regression — MongoInstructionPlanRepository must
    satisfy BOTH the ``InstructionPlanReadRepository.get_by_id`` API and
    the ``InstructionPlanLookup.get`` Protocol that
    :class:`backend.integrations.feishu.parser.ExecutionReportOrchestrator`
    calls. Without ``get()``, the first real execution report arriving
    via the receiver would AttributeError before reaching the broker.
    """

    @pytest.mark.asyncio
    async def test_get_alias_returns_same_plan_as_get_by_id(
        self, db: _FakeDatabase
    ) -> None:
        repo = MongoInstructionPlanRepository(db)
        plan = _instruction_plan()
        await repo.upsert(plan)
        by_id = await repo.get_by_id(plan.instruction_id)
        via_lookup = await repo.get(plan.instruction_id)
        assert by_id is not None
        assert via_lookup is not None
        assert via_lookup.instruction_id == by_id.instruction_id

    def test_repo_satisfies_instruction_plan_lookup_protocol(
        self, db: _FakeDatabase
    ) -> None:
        from backend.integrations.feishu.parser import (
            InstructionPlanLookup,
        )

        repo = MongoInstructionPlanRepository(db)
        # InstructionPlanLookup is a runtime-uncheckable Protocol so we
        # assert the actual method exists with the right shape instead.
        assert hasattr(repo, "get"), (
            "MongoInstructionPlanRepository must expose .get() to satisfy "
            "InstructionPlanLookup; ExecutionReportOrchestrator._handle "
            "calls self._lookup.get(report.instruction_id)"
        )
        assert callable(getattr(repo, "get", None))
        # Sanity check the symbol is actually the orchestrator's
        # expected Protocol — defends against a future rename breaking
        # the contract silently.
        assert InstructionPlanLookup.__name__ == "InstructionPlanLookup"


    @pytest.mark.asyncio
    async def test_broker_at_fill_reads_audit_for_simulation_reject(
        self, db: _FakeDatabase
    ) -> None:
        """Codex Cycle 4 P2 regression — SimulationExecutor's reject
        path writes RISK_ENGINE_CHECK_REJECTED to ``audit_events`` (with
        the broker reason in payload.reason) instead of an
        ORDER_REJECTED broker event. ``broker_at_fill`` must fall
        through to that audit row so the operator drawer keeps the
        ``price_limit_violation_at_fill`` reason visible.
        """
        repo = MongoInstructionPlanRepository(db)
        plan = _instruction_plan()
        await repo.upsert(plan)
        db[MongoInstructionPlanRepository.AUDIT_EVENT_COLLECTION].rows.append(
            {
                "event_type": "risk_engine_check_rejected",
                "correlation_id": plan.instruction_id,
                "timestamp": dt.datetime(
                    2026, 5, 15, 10, 0, 5, tzinfo=SHANGHAI
                ),
                "payload": {
                    "stock_code": plan.stock_code,
                    "side": "BUY",
                    "reason": "price_limit_violation_at_fill",
                },
            }
        )
        outcome = await repo.broker_at_fill(plan.instruction_id)
        assert outcome is not None
        assert outcome["outcome"] == "REJECTED"
        assert outcome["reason"] == "price_limit_violation_at_fill"


@pytest.mark.asyncio
async def test_intraday_mtm_callback_skips_outside_trading_hours(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Codex Cycle 3 P2 regression — the 30s MTM callback must short-
    circuit when ``is_trading_hours(now)`` returns False, otherwise the
    BrokerScheduler's flat IntervalTrigger pollutes ``equity_points``
    with FRESH-zero or DEGRADED stale rows overnight + on weekends.

    Wires the same orchestration layer as the smoke test, then drives
    a Saturday tick (always non-trading) through the intraday MTM job
    and asserts the equity repository stayed empty.
    """
    from fastapi import FastAPI

    from backend.audit.store import AuditStore
    from backend.broker.models import BrokerConfig
    from backend.broker.registry import BrokerRegistry
    from backend.main import _init_orchestration_layer

    app = FastAPI()
    fake_db = _FakeDatabase()
    mongodb = MagicMock()
    mongodb._db = fake_db
    app.state.mongodb = mongodb
    app.state.mongo_client = MagicMock()
    app.state.audit_store = AuditStore(
        MagicMock(), jsonl_path=tmp_path / "audit.jsonl"
    )
    app.state.broker_registry = BrokerRegistry(BrokerConfig())
    app.state.redis = AsyncMock()
    app.state.feishu_client = None

    monkeypatch.delenv("FEISHU_DECISION_CHAT_ID", raising=False)
    # The integration smoke tests stub mongodb with a MagicMock —
    # MagicMock blocks ``assert_*`` attribute access, so the
    # BrokerScheduler replica-set fence cannot run. Opt out of the
    # gate for these tests (production never sets the env var).
    monkeypatch.setenv("QUANTMIND_BROKER_SKIP_RS_GATE", "1")

    await _init_orchestration_layer(app)
    try:
        # Saturday 10:00 SHA — outside trading hours. The MTM callback
        # should not produce any row in the equity_points fake collection.
        sat_10am = dt.datetime(2026, 5, 16, 10, 0, tzinfo=SHANGHAI)
        callback = app.state.broker_scheduler._intraday  # type: ignore[attr-defined]
        assert callback is not None, "intraday MTM callback must be wired"
        await callback(sat_10am)

        coll = fake_db[MongoEquityPointRepository.COLLECTION]
        assert coll.rows == [], (
            f"Saturday MTM tick wrote an EquityPoint row: {coll.rows!r}; "
            "the trading-hours guard is missing or wrong"
        )

        # Sanity: a real trading-hour tick still produces a point.
        mon_10am = dt.datetime(2026, 5, 18, 10, 0, tzinfo=SHANGHAI)
        await callback(mon_10am)
        # Default broker has no positions so EquityPointBuilder.build()
        # succeeds with an empty positions tuple — the row count
        # increments by exactly 1.
        assert len(coll.rows) == 1, (
            "Trading-hour MTM tick should produce exactly one row"
        )

        # Codex Cycle 7 P2 regression — the intraday MTM callback must
        # use a STRICT trading-hours guard with NO 15:00–16:30 EOD
        # window: the 30s IntervalTrigger would otherwise fire ~180
        # times in that window and re-introduce the off-hours
        # pollution the guard was meant to prevent. The closing
        # EquityPoint is now produced by a separate
        # ``eod_close_callback`` invoked exactly once by
        # ``BrokerScheduler.run_eod_pipeline``.
        mon_4pm = dt.datetime(2026, 5, 18, 16, 0, tzinfo=SHANGHAI)
        await callback(mon_4pm)
        assert len(coll.rows) == 1, (
            "16:00 tick must be blocked by the intraday callback's "
            "strict trading-hours guard — the EOD close ping goes "
            "through a separate eod_close_callback path"
        )

        # The eod_close_callback wired on the BrokerScheduler must
        # ALWAYS write a point regardless of trading hours, since the
        # EOD pipeline only invokes it once per day after market close.
        eod_callback = app.state.broker_scheduler._eod_close  # type: ignore[attr-defined]
        assert eod_callback is not None, "EOD close callback must be wired"
        assert eod_callback is not callback, (
            "eod_close_callback must be a SEPARATE callable from the "
            "intraday MTM callback — wiring the same callable defeats "
            "the strict-guard / EOD-bypass split"
        )
        await eod_callback(mon_4pm)
        assert len(coll.rows) == 2, (
            "EOD close callback at 16:00 must produce one point"
        )

        # Confirm the trading-hours guard still blocks a 17:00 tick
        # (after the EOD window) so off-hours pollution stays excluded.
        mon_5pm = dt.datetime(2026, 5, 18, 17, 0, tzinfo=SHANGHAI)
        await callback(mon_5pm)
        assert len(coll.rows) == 2, (
            "17:00 tick is past the EOD window and must stay blocked"
        )
    finally:
        await app.state.broker_scheduler.stop()


@pytest.mark.asyncio
async def test_eod_pipeline_freeze_state_exposed_under_probed_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Codex Cycle 4 P2 regression — the system-status probe
    (``backend/api/system_status.py``) reads
    ``app.state.eod_pipeline_freeze_state`` first and falls back to
    ``broker_scheduler.eod_pipeline_freeze_state``. ``BrokerScheduler``
    only exposes ``.freeze_state``, so the orchestration layer must
    attach the canonical name on app.state directly — otherwise the
    StatusBar reports the EOD freeze as ``unavailable`` even when it
    is active.
    """
    from fastapi import FastAPI

    from backend.audit.store import AuditStore
    from backend.broker.models import BrokerConfig
    from backend.broker.registry import BrokerRegistry
    from backend.broker.scheduler import EodPipelineFreezeState
    from backend.main import _init_orchestration_layer

    app = FastAPI()
    fake_db = _FakeDatabase()
    mongodb = MagicMock()
    mongodb._db = fake_db
    app.state.mongodb = mongodb
    app.state.mongo_client = MagicMock()
    app.state.audit_store = AuditStore(
        MagicMock(), jsonl_path=tmp_path / "audit.jsonl"
    )
    app.state.broker_registry = BrokerRegistry(BrokerConfig())
    app.state.redis = AsyncMock()
    app.state.feishu_client = None

    monkeypatch.delenv("FEISHU_DECISION_CHAT_ID", raising=False)
    # The integration smoke tests stub mongodb with a MagicMock —
    # MagicMock blocks ``assert_*`` attribute access, so the
    # BrokerScheduler replica-set fence cannot run. Opt out of the
    # gate for these tests (production never sets the env var).
    monkeypatch.setenv("QUANTMIND_BROKER_SKIP_RS_GATE", "1")

    await _init_orchestration_layer(app)
    try:
        attached = getattr(app.state, "eod_pipeline_freeze_state", None)
        assert isinstance(attached, EodPipelineFreezeState), (
            "_init_orchestration_layer must attach the freeze state under "
            "app.state.eod_pipeline_freeze_state for the G-002 probe to "
            "find it"
        )
        # Must be the same object the BrokerScheduler holds — otherwise
        # an EOD freeze fired by the scheduler would not surface on
        # the app.state probe.
        scheduler_freeze = app.state.broker_scheduler.freeze_state
        assert attached is scheduler_freeze, (
            "app.state.eod_pipeline_freeze_state and "
            "broker_scheduler.freeze_state must be the same object"
        )
    finally:
        await app.state.broker_scheduler.stop()


@pytest.mark.asyncio
async def test_snapshot_lookup_fails_closed_on_position_decode_error(
    db: _FakeDatabase,
) -> None:
    """Codex Cycle 8 P2 regression — :class:`MongoSnapshotLookup` MUST
    return ``None`` (fail-closed) when any position row cannot be
    decoded into :class:`ReportedPosition`. Returning a partial
    snapshot would let the MISMATCH reconciliation path produce a
    false deviation report against an incomplete expected set.
    """
    lookup = MongoSnapshotLookup(db)
    # First position is valid; second one is malformed (negative volume).
    db[MongoSnapshotLookup.COLLECTION].rows.append(
        {
            "snapshot_id": "snap-corrupt",
            "created_at": dt.datetime(
                2026, 5, 15, 16, 0, 30, tzinfo=SHANGHAI
            ),
            "cash": 950_000.0,
            "frozen_cash": 50_000.0,
            "initial_capital": 1_000_000.0,
            "positions": [
                {
                    "code": "600519",
                    "volume": 100,
                    "today_bought_volume": 0,
                    "cost_price": 1800.0,
                },
                {
                    "code": "000001",
                    "volume": -50,  # invalid, triggers ReportedPosition guard
                    "today_bought_volume": 0,
                    "cost_price": 12.5,
                },
            ],
            "checksum": "0123456789abcdef",
            "trade_date": "2026-05-15",
        }
    )
    snapshot = await lookup.get("snap-corrupt")
    assert snapshot is None, (
        "MongoSnapshotLookup must fail-closed (return None) on any "
        "position decode failure; partial snapshots corrupt the "
        "MISMATCH deviation report path"
    )


def test_feishu_dispatcher_filters_by_decision_chat_id() -> None:
    """Codex Cycle 8 P1 regression — the lifespan-built Feishu
    dispatcher MUST filter inbound messages by ``chat_id`` and drop
    anything that did not come from ``FEISHU_DECISION_CHAT_ID``.
    Without this gate a stray message in the alert chat or a DM
    matching a reconciliation/execution regex would reach the
    appliers and mutate the broker mirror, violating
    P0-2-amendment-2026-05-16 §4 红线 7.

    AST check: the dispatcher function body must compare
    ``message.chat_id`` against the captured decision-chat env var
    and return on mismatch BEFORE invoking either orchestrator.
    """
    import ast
    from pathlib import Path

    src = Path("backend/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch_fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_feishu_dispatch"
        ):
            dispatch_fn = node
            break
    assert dispatch_fn is not None, (
        "_feishu_dispatch dispatcher not found in backend/main.py"
    )
    rendered = ast.unparse(dispatch_fn.body)
    assert "message.chat_id" in rendered, (
        "_feishu_dispatch must read message.chat_id to gate forwarding"
    )
    assert (
        "decision_chat_env" in rendered or "decision_chat" in rendered
    ), (
        "_feishu_dispatch must compare against the decision chat id "
        "captured at startup"
    )


def test_orchestration_layer_calls_recover_state_before_executor() -> None:
    """Codex Cycle 6 P1 regression — the lifespan MUST call
    ``recover_state`` and seed the broker BEFORE constructing
    ``SimulationExecutor`` or exposing the broker to MTM / EOD. Without
    this seed, a process restart with existing broker_events/snapshots
    silently routes orders against a fresh-zero account and the durable
    mirror diverges on the very first trade.

    AST check: in ``_init_orchestration_layer``, the call to
    ``recover_state(...)`` and the subsequent
    ``broker.seed_from_recovery(...)`` must appear BEFORE the
    ``SimulationExecutor(...)`` construction.
    """
    import ast
    from pathlib import Path

    src = Path("backend/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    init_fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_init_orchestration_layer"
        ):
            init_fn = node
            break
    assert init_fn is not None

    recover_idx = None
    seed_idx = None
    executor_idx = None
    # Walk body statements in source order; ``ast.walk`` interleaves
    # nested nodes and would give meaningless positions for the order
    # comparison.
    for i, stmt in enumerate(init_fn.body):
        rendered = ast.unparse(stmt)
        if "recover_state(" in rendered and recover_idx is None:
            recover_idx = i
        if "seed_from_recovery(" in rendered and seed_idx is None:
            seed_idx = i
        if "SimulationExecutor(" in rendered and executor_idx is None:
            executor_idx = i

    assert recover_idx is not None, (
        "_init_orchestration_layer must call recover_state — without it "
        "the broker is fresh-zero while broker_events tracks durable state"
    )
    assert seed_idx is not None, (
        "_init_orchestration_layer must call broker.seed_from_recovery "
        "after recover_state to apply the recovered state"
    )
    assert executor_idx is not None
    assert recover_idx < executor_idx and seed_idx < executor_idx, (
        "recovery + seeding must happen BEFORE SimulationExecutor "
        "construction so the executor reads the recovered broker"
    )


def test_broker_scheduler_start_failure_is_not_swallowed() -> None:
    """Codex Cycle 6 P1 regression — ``await broker_scheduler.start()``
    MUST NOT be wrapped in a try/except that logs and continues. The
    replica_set_gate failure (or any other start error) needs to abort
    the lifespan so SimulationExecutor is never exposed against a
    non-running broker scheduler — otherwise the first routed order
    mutates the broker mirror while ``BrokerEventStore.append_many``
    hits the same Mongo transaction failure, leaving an unpersisted
    fill.
    """
    import ast
    from pathlib import Path

    src = Path("backend/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    init_fn = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_init_orchestration_layer"
        ):
            init_fn = node
            break
    assert init_fn is not None

    for stmt in ast.walk(init_fn):
        if not isinstance(stmt, ast.Try):
            continue
        wrapped = ast.unparse(stmt.body)
        if "broker_scheduler.start(" in wrapped:
            raise AssertionError(
                "broker_scheduler.start() is wrapped in try/except — "
                "swallowing the failure leaves SimulationExecutor live "
                "against a non-running scheduler"
            )


def test_lifespan_fails_closed_when_decision_chat_missing_in_interactive() -> (
    None
):
    """Codex Cycle 5 P2 regression — when FEISHU_INTERACTIVE_ENABLED=true
    and the acceptance gate passes BUT FEISHU_DECISION_CHAT_ID is unset,
    the lifespan MUST fail closed. Otherwise the long-connection
    receiver starts with reconciliation_orchestrator=None, reconciliation
    replies fall through to the execution parser, and open tickets never
    resolve while the overlay looks enabled.

    AST check: there must be a SystemExit raise reachable from a check
    on ``application.state.reconciliation_orchestrator is None`` inside
    the lifespan (after the acceptance gate passes).
    """
    import ast
    from pathlib import Path

    src = Path("backend/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Walk the lifespan async function (or fall back to module-level)
    # looking for a SystemExit whose message names FEISHU_DECISION_CHAT_ID.
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        rendered = ast.unparse(node)
        if (
            "SystemExit" in rendered
            and "FEISHU_DECISION_CHAT_ID" in rendered
            and "Refusing to start" in rendered
        ):
            found = True
            break
    assert found, (
        "lifespan must raise SystemExit referring to FEISHU_DECISION_CHAT_ID "
        "when the interactive overlay is enabled without a decision chat — "
        "otherwise the long-connection receiver runs without a "
        "ReconciliationOrchestrator and silently drops reconciliation replies"
    )


def test_broker_scheduler_receives_replica_set_gate() -> None:
    """Codex Cycle 5 P2 regression — ``BrokerScheduler`` must be
    constructed with the ``MongoDBService`` as its
    ``replica_set_gate``. Without it the start() fence skips
    :meth:`MongoDBService.assert_replica_set` and a standalone Mongo
    quietly boots, only to fail the first multi-document transaction
    at order-routing / EOD time. Dev environments may opt out via
    ``QUANTMIND_BROKER_SKIP_RS_GATE=1`` — the AST check confirms the
    kwarg is wired (the env-var bypass is a separate branch).
    """
    import ast
    from pathlib import Path

    tree = ast.parse(
        Path("backend/main.py").read_text(encoding="utf-8")
    )
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = (
            node.func.attr
            if isinstance(node.func, ast.Attribute)
            else node.func.id
            if isinstance(node.func, ast.Name)
            else None
        )
        if callee != "BrokerScheduler":
            continue
        for kw in node.keywords:
            if kw.arg == "replica_set_gate":
                found = True
                break
        if found:
            break
    assert found, (
        "BrokerScheduler must be constructed with a replica_set_gate "
        "kwarg so the multi-document transaction prereq surfaces at "
        "startup, not at first trade"
    )


def test_acceptance_callback_propagates_upsert_failure() -> None:
    """Codex Cycle 5 P2 regression — the EOD acceptance callback MUST
    NOT swallow upsert errors. ``BrokerScheduler.run_eod_pipeline``
    treats a clean callback return as success and skips its retry/
    freeze path; swallowing means a Mongo outage during 16:00:30
    silently drops the acceptance row while EOD reports success.

    AST check: the call to ``acceptance_service.upsert`` must NOT be
    wrapped in a try/except that swallows the exception.
    """
    import ast
    from pathlib import Path

    src = Path("backend/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    callback_node = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_acceptance_callback"
        ):
            callback_node = node
            break
    assert callback_node is not None, (
        "_acceptance_callback not found in backend/main.py"
    )
    for child in ast.walk(callback_node):
        if not isinstance(child, ast.Try):
            continue
        # If any try-block wraps a call ending in `.upsert(`, fail.
        wrapped_src = ast.unparse(child.body)
        if (
            "acceptance_service.upsert" in wrapped_src
            or "_repo.upsert" in wrapped_src
            or "service.upsert" in wrapped_src
        ):
            raise AssertionError(
                "acceptance_service.upsert is wrapped in a try/except — "
                "swallowing the failure breaks BrokerScheduler EOD retry "
                "+ freeze path (Codex Cycle 5 P2 fix)"
            )


def test_mongo_client_uses_standard_uuid_representation() -> None:
    """Codex Cycle 1 P1 regression — the Motor client MUST be built
    with ``uuidRepresentation="standard"`` so the AcceptanceReport's
    ``report_id`` (uuid.UUID) round-trips through Mongo without
    PyMongo raising ``cannot encode native uuid.UUID with
    UuidRepresentation.UNSPECIFIED``. Without this the 16:00 acceptance
    upsert fails and the P0-6 §2 红线 5 gate never gets persisted data.

    Parses backend/main.py with ``ast`` (per Codex Cycle 2 INFO) so
    that simply mentioning the kwarg in a comment is not enough —
    the assertion only passes if an actual
    ``AsyncIOMotorClient(..., uuidRepresentation="standard")`` call
    exists.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(
        Path("backend/main.py").read_text(encoding="utf-8")
    )
    found_call = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match attribute call like motor.AsyncIOMotorClient(...).
        func = node.func
        callee = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if callee != "AsyncIOMotorClient":
            continue
        for kw in node.keywords:
            if (
                kw.arg == "uuidRepresentation"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "standard"
            ):
                found_call = True
                break
        if found_call:
            break
    assert found_call, (
        "AsyncIOMotorClient must be constructed with "
        'uuidRepresentation="standard" or AcceptanceReport.report_id '
        "UUID round-trip will fail in production Mongo"
    )
