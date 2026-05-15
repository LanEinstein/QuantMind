"""Tests for the P1-5 §1.1 InstructionPlan pool + 3-tab reason drawer API.

Lock the three-namespace contract:

* ``builder_early_return`` — Builder gate audit rows.
* ``risk_engine_check`` — 14 row RiskCheckSummary projection.
* ``broker_at_fill`` — MockBroker terminal outcome with namespace
  ``price_limit_violation_at_fill`` distinct from the engine's
  ``limit_up_block`` / ``limit_down_block`` reasons.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.instruction_plans import (
    REASON_NAMESPACES,
    InstructionPlanReadRepository,
)
from backend.main import app
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)
from backend.utils.trading_hours import SHANGHAI

_STATE_KEYS = ("instruction_plan_repository",)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    for key in _STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


@pytest.fixture()
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _risk_summary_14(passing: bool = True) -> tuple[RiskCheckSummary, ...]:
    names = (
        "code_validity", "price_reasonability", "volume_validity",
        "fund_sufficiency", "position_limit", "total_position_limit",
        "trading_time", "total_position_pct", "single_instruction_amount",
        "daily_new_instruction_count", "universe_whitelist",
        "limit_up_down_block", "daily_loss_halt", "consecutive_loss_halt",
    )
    return tuple(
        RiskCheckSummary(rule_name=n, passed=passing, message="") for n in names
    )


def _validated_plan(
    *,
    instruction_id: str = "QM-20260515-100001-600519-BUY-001",
    status: InstructionStatus = InstructionStatus.VALIDATED,
    side: InstructionSide = InstructionSide.BUY,
    trade_date: str = "2026-05-15",
    created_at: dt.datetime | None = None,
    rejection_reason: str | None = None,
) -> InstructionPlan:
    created = created_at or dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI)
    snap = created - dt.timedelta(seconds=2)
    return InstructionPlan(
        instruction_id=instruction_id,
        created_at=created,
        valid_until=created + dt.timedelta(minutes=5),
        trade_date=trade_date,
        stock_code="600519",
        stock_name="贵州茅台",
        side=side,
        volume=100,
        limit_price=100.0,
        data_snapshot=DataSnapshot(
            snapshot_at=snap,
            quote_source="adata",
            quote_latency_ms=100,
            prev_close=100.0,
            is_trading_day=True,
            is_trading_hours=True,
        ),
        evidence_ids=("MARKET-600519-2026-05-15T10:00:00",),
        position_summary=PositionSummary(
            pre_position_pct=0.05, post_position_pct=0.06,
            pre_total_position_pct=0.30, post_total_position_pct=0.31,
            pre_cash=500_000.0, post_cash=489_950.0,
        ),
        risk_summary=_risk_summary_14(),
        risk_validation_id="RV-1",
        signal_id="sig-1",
        analysis_record_id="run-1",
        debate_round_count=2,
        invalidation_summary="跌破 95",
        status=status,
        rejection_reason=rejection_reason,
    )


class _StubRepo:
    """Honors the :class:`InstructionPlanReadRepository` Protocol."""

    def __init__(self) -> None:
        self.plans: dict[str, InstructionPlan] = {}
        self.builder_rows: dict[str, list[dict[str, Any]]] = {}
        self.broker_rows: dict[str, dict[str, Any] | None] = {}

    async def list_recent(
        self,
        *,
        limit: int,
        status: str | None,
        trade_date: str | None,
    ) -> list[InstructionPlan]:
        rows = list(self.plans.values())
        if status is not None:
            rows = [p for p in rows if p.status.value == status]
        if trade_date is not None:
            rows = [p for p in rows if p.trade_date == trade_date]
        rows.sort(key=lambda p: p.created_at, reverse=True)
        return rows[:limit]

    async def get_by_id(self, instruction_id: str) -> InstructionPlan | None:
        return self.plans.get(instruction_id)

    async def builder_early_returns(
        self, instruction_id: str
    ) -> list[dict[str, Any]]:
        return list(self.builder_rows.get(instruction_id, []))

    async def broker_at_fill(
        self, instruction_id: str
    ) -> dict[str, Any] | None:
        return self.broker_rows.get(instruction_id)


class TestListUnwired:
    @pytest.mark.asyncio
    async def test_returns_empty_when_repository_missing(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/instruction-plans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["plans"] == []
        assert body["data"]["repository_status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_rejects_invalid_limit(self, client: AsyncClient) -> None:
        resp = await client.get("/api/instruction-plans?limit=0")
        assert resp.status_code == 400
        resp = await client.get("/api/instruction-plans?limit=999")
        assert resp.status_code == 400


class TestListWired:
    @pytest.mark.asyncio
    async def test_returns_summaries_with_locked_fields(
        self,
        client: AsyncClient,
    ) -> None:
        repo = _StubRepo()
        plan = _validated_plan()
        repo.plans[plan.instruction_id] = plan
        app.state.instruction_plan_repository = repo

        resp = await client.get("/api/instruction-plans")
        body = resp.json()
        data = body["data"]
        assert data["repository_status"] == "ok"
        assert data["total"] == 1
        summary = data["plans"][0]
        for key in (
            "instruction_id",
            "trade_date",
            "stock_code",
            "stock_name",
            "side",
            "status",
            "volume",
            "limit_price",
            "valid_until",
        ):
            assert key in summary
        assert summary["side"] == "BUY"
        assert summary["status"] == "VALIDATED"

    @pytest.mark.asyncio
    async def test_filters_by_status_and_trade_date(
        self,
        client: AsyncClient,
    ) -> None:
        repo = _StubRepo()
        # 2 plans: one VALIDATED on 2026-05-15, one REJECTED on 2026-05-14
        validated = _validated_plan(
            instruction_id="QM-20260515-100001-600519-BUY-001",
            status=InstructionStatus.VALIDATED,
        )
        rejected = _validated_plan(
            instruction_id="QM-20260514-100501-600519-BUY-001",
            status=InstructionStatus.REJECTED,
            trade_date="2026-05-14",
            created_at=dt.datetime(2026, 5, 14, 10, 5, 1, tzinfo=SHANGHAI),
            rejection_reason="limit_up_block",
        )
        repo.plans[validated.instruction_id] = validated
        repo.plans[rejected.instruction_id] = rejected
        app.state.instruction_plan_repository = repo

        # status=REJECTED
        resp = await client.get("/api/instruction-plans?status=REJECTED")
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["plans"][0]["status"] == "REJECTED"

        # trade_date=2026-05-15
        resp = await client.get(
            "/api/instruction-plans?trade_date=2026-05-15"
        )
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["plans"][0]["trade_date"] == "2026-05-15"


class TestDetailUnwired:
    @pytest.mark.asyncio
    async def test_returns_503_when_repository_missing(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get(
            "/api/instruction-plans/QM-20260515-100001-600519-BUY-001"
        )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_returns_404_when_unknown_id(
        self,
        client: AsyncClient,
    ) -> None:
        app.state.instruction_plan_repository = _StubRepo()
        resp = await client.get(
            "/api/instruction-plans/QM-20260515-100001-600519-BUY-001"
        )
        assert resp.status_code == 404


class TestDetailThreeTabs:
    @pytest.mark.asyncio
    async def test_drawer_returns_three_namespaces(
        self,
        client: AsyncClient,
    ) -> None:
        repo = _StubRepo()
        plan = _validated_plan()
        repo.plans[plan.instruction_id] = plan
        repo.builder_rows[plan.instruction_id] = [
            {
                "reason_namespace": "watchlist_exclusion",
                "payload": {"exclusion_sub_reason": "is_st"},
                "at": "2026-05-15T01:55:01+00:00",
            }
        ]
        repo.broker_rows[plan.instruction_id] = {
            "outcome": "FILLED",
            "reason": None,
            "fill_price": 100.5,
            "fill_volume": 100,
        }
        app.state.instruction_plan_repository = repo

        resp = await client.get(
            f"/api/instruction-plans/{plan.instruction_id}"
        )
        body = resp.json()
        assert body["status"] == "ok"
        tabs = body["data"]["reason_tabs"]
        assert set(tabs.keys()) == set(REASON_NAMESPACES)

        # Builder tab
        builder_row = tabs["builder_early_return"][0]
        assert builder_row["reason_namespace"] == "watchlist_exclusion"
        # Risk engine tab — 14 rows
        assert len(tabs["risk_engine_check"]) == 14
        for i, row in enumerate(tabs["risk_engine_check"], start=1):
            assert row["check_id"] == i
            assert "rule_name" in row
            assert "passed" in row
        # Broker tab
        assert tabs["broker_at_fill"]["outcome"] == "FILLED"

    @pytest.mark.asyncio
    async def test_broker_at_fill_carries_locked_price_limit_reason(
        self,
        client: AsyncClient,
    ) -> None:
        """Locks the namespace lock: 'price_limit_violation_at_fill'
        only appears under broker_at_fill, never inside the engine tab.
        """
        repo = _StubRepo()
        plan = _validated_plan(
            status=InstructionStatus.REJECTED,
            rejection_reason="price_limit_violation_at_fill",
        )
        repo.plans[plan.instruction_id] = plan
        repo.broker_rows[plan.instruction_id] = {
            "outcome": "REJECTED",
            "reason": "price_limit_violation_at_fill",
            "fill_price": None,
            "fill_volume": 0,
        }
        app.state.instruction_plan_repository = repo

        resp = await client.get(
            f"/api/instruction-plans/{plan.instruction_id}"
        )
        body = resp.json()
        tabs = body["data"]["reason_tabs"]
        assert tabs["broker_at_fill"]["reason"] == "price_limit_violation_at_fill"
        # Engine tab must NOT carry the broker namespace literal — it
        # uses limit_up_block / limit_down_block (D-001 14-check rule 12).
        for row in tabs["risk_engine_check"]:
            assert row["rule_name"] != "price_limit_violation_at_fill"

    @pytest.mark.asyncio
    async def test_drawer_omits_broker_tab_when_no_terminal_fill(
        self,
        client: AsyncClient,
    ) -> None:
        repo = _StubRepo()
        plan = _validated_plan()
        repo.plans[plan.instruction_id] = plan
        # No broker rows wired in
        app.state.instruction_plan_repository = repo

        resp = await client.get(
            f"/api/instruction-plans/{plan.instruction_id}"
        )
        body = resp.json()
        assert body["data"]["reason_tabs"]["broker_at_fill"] is None


class TestLockedNamespaceTuple:
    def test_reason_namespaces_tuple_is_locked(self) -> None:
        assert REASON_NAMESPACES == (
            "builder_early_return",
            "risk_engine_check",
            "broker_at_fill",
        )

    def test_repository_protocol_runtime_check(self) -> None:
        repo = _StubRepo()
        assert isinstance(repo, InstructionPlanReadRepository)
