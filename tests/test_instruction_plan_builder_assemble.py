"""Tests for D-004 InstructionPlanBuilder.assemble_plan + 4-Agent gate.

Coverage focus:

* 4-Agent gate (P0-10): missing any of fundamental / technical /
  risk_officer / fund_manager record id → BuilderDegrade with
  ``reason_namespace='missing_mandatory_agent'``; debate_round_count
  < 1 → BuilderDegrade ``debate_bypass``; one BUILDER_EARLY_RETURN
  audit event per degrade.
* Forced HOLD on ``parse_ok=False`` (P0-3 §2 redline 6).
* HOLD recommendation → HOLD InstructionPlan with
  ``status=VALIDATED`` and 14-entry stub risk_summary.
* BUY/SELL recommendation reaches RiskEngine: pass → BUY/SELL
  InstructionPlan with ``status=VALIDATED`` and 14 ``passed=True``
  rows; reject → BUY/SELL InstructionPlan with ``status=REJECTED``,
  rejection_reason set, and the failing rule row carrying
  ``passed=False`` plus subsequent rows ``passed=None``.
* Five-early-return chain (D-003) still gates BUY/SELL: with a freeze
  source active, assemble_plan short-circuits to BuilderEarlyReturn
  before the engine runs.
* P0-10 schema gate: FundManagerOutput refuses unknown fields
  (volume / limit_price / status leakage attempts).
* FundManagerOutput.from_fund_manager_record drops the LLM-advisory
  ``target_price`` and maps Chinese action to InstructionSide.
* PositionSummary derivation matches the RiskEngine exposure algorithm
  (existing position + order_value mode-aware).
* HOLD plan rule_name list matches the 14-check canonical order so
  the front-end Reason drawer (P1-5 §1.5 Engine tab) can render rows
  positionally.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    Position,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.data.data_quality import DataQualityState
from backend.models.instruction import (
    DataSnapshot,
    InstructionSide,
    InstructionStatus,
)
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.services.fund_manager_output import (
    FundManagerOutput,
    from_fund_manager_record,
)
from backend.services.instruction_plan_builder import (
    REASON_DEBATE_BYPASS,
    REASON_MISSING_AGENT,
    REASON_TICKET_OPEN,
    AssemblyContext,
    BuilderDegrade,
    BuilderEarlyReturn,
    BuilderPlan,
    InstructionPlanBuilder,
    MandatoryAgentRecords,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import UniversePolicy, load_policy

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAPSHOT_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_CODE = "510300"
_NAME = "沪深300 ETF"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> UniversePolicy:
    return load_policy(Path("config/universe_policy.yaml"))


@pytest.fixture
def passing_signal() -> WatchlistMarketSignal:
    return WatchlistMarketSignal(
        listed_at_trading_days=720,
        avg_amount_20d_yuan=1_000_000_000.0,
        last_price_yuan=4.5,
    )


@pytest.fixture
def clean_data_quality() -> DataQualityState:
    return DataQualityState(
        quote_unavailable=False,
        quote_staleness_breach=False,
        quote_divergence_breach=False,
        minimum_freshness_breach=False,
        news_outage_breach=False,
        mirofish_unavailable=False,
        watchlist_snapshot_outage=False,
        primary_quote_age_seconds=2,
        backup_quote_age_seconds=2,
        news_sources_alive_count=5,
    )


@pytest.fixture
def quiet_breaker() -> CircuitBreaker:
    return CircuitBreaker(CircuitBreakerConfig())


@pytest.fixture
def fat_account() -> AccountInfo:
    return AccountInfo(
        total_assets=1_000_000.0,
        available_cash=900_000.0,
        frozen_cash=0.0,
        market_value=100_000.0,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def empty_positions() -> tuple[Position, ...]:
    return ()


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        position_limits=PositionLimitsConfig(),
        stop_loss=StopLossConfig(),
        circuit_breaker=CircuitBreakerConfig(),
        universe=UniverseConfig(),
    )


@pytest.fixture
def risk_engine(risk_config: RiskConfig) -> RiskEngine:
    return RiskEngine(risk_config)


@pytest.fixture
def stock_meta() -> RiskStockMetadata:
    return RiskStockMetadata(
        code=_CODE,
        name=_NAME,
        board=RiskBoard.ETF,
        is_st=False,
        instrument_type="etf",
    )


@pytest.fixture
def daily_state() -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0,
        today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(),
        current_price=4.5,
        is_in_halt_cooldown=False,
        halt_until=None,
    )


@pytest.fixture
def all_records_present() -> MandatoryAgentRecords:
    return MandatoryAgentRecords(
        fundamental_analyst_record_id="step-fa-1",
        technical_analyst_record_id="step-ta-1",
        risk_officer_record_id="step-ro-1",
        fund_manager_record_id="step-fm-1",
    )


@pytest.fixture
def data_snapshot() -> DataSnapshot:
    return DataSnapshot(
        snapshot_at=_SNAPSHOT_AT,
        quote_source="primary",
        quote_latency_ms=200,
        is_trading_day=True,
        is_trading_hours=True,
        prev_close=4.5,
    )


@pytest.fixture
async def audit_store(tmp_path: Path) -> AuditStore:
    return AuditStore(
        InMemoryAuditCollection(),
        jsonl_path=tmp_path / "audit.jsonl",
    )


def _build_context(
    *,
    risk_engine: RiskEngine,
    fat_account: AccountInfo,
    empty_positions: tuple[Position, ...],
    daily_state: DailyTradingState | None,
    stock_meta: RiskStockMetadata | None,
    quiet_breaker: CircuitBreaker,
    clean_data_quality: DataQualityState,
    policy: UniversePolicy,
    passing_signal: WatchlistMarketSignal,
    data_snapshot: DataSnapshot,
    debate_round_count: int = 1,
    proposed_volume: int = 200,
    proposed_limit_price: float = 4.5,
    open_tickets: tuple = (),
    seq: int = 1,
) -> AssemblyContext:
    return AssemblyContext(
        stock_code=_CODE,
        stock_name=_NAME,
        now=_NOW,
        open_tickets=open_tickets,
        circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality,
        watchlist_policy=policy,
        watchlist_signal=passing_signal,
        risk_engine=risk_engine,
        account=fat_account,
        positions=empty_positions,
        prev_close=4.5,
        daily_state=daily_state,
        stock_meta=stock_meta,
        proposed_volume=proposed_volume,
        proposed_limit_price=proposed_limit_price,
        seq=seq,
        signal_id="sig-2026-05-15-001",
        analysis_record_id="ar-2026-05-15-001",
        risk_validation_id="rv-2026-05-15-001",
        debate_round_count=debate_round_count,
        evidence_ids=(),
        data_snapshot=data_snapshot,
        invalidation_summary="default invalidation summary",
    )


# ---------------------------------------------------------------------------
# FundManagerOutput contract
# ---------------------------------------------------------------------------


class TestFundManagerOutput:
    def test_strict_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            FundManagerOutput.model_validate(
                {
                    "side": InstructionSide.BUY,
                    "proposal_text": "buy because reasons",
                    "volume": 100,
                }
            )

    def test_no_volume_or_price_field(self) -> None:
        # The schema must NOT declare numeric decision fields — that is
        # the lint half of the P0-10 dual gate.
        forbidden_fields = {
            "volume",
            "limit_price",
            "price",
            "status",
            "risk_summary",
            "valid_until",
        }
        actual = set(FundManagerOutput.model_fields)
        leaked = actual & forbidden_fields
        assert leaked == set(), f"forbidden numeric fields leaked: {leaked}"
        assert actual == {"side", "proposal_text", "parse_ok"}

    def test_min_proposal_text_length(self) -> None:
        with pytest.raises(ValidationError):
            FundManagerOutput(side=InstructionSide.HOLD, proposal_text="")

    @pytest.mark.parametrize(
        ("action", "expected_side"),
        [
            ("买入", InstructionSide.BUY),
            ("持有", InstructionSide.HOLD),
            ("卖出", InstructionSide.SELL),
        ],
    )
    def test_from_legacy_record(
        self, action: str, expected_side: InstructionSide
    ) -> None:
        out = from_fund_manager_record(
            action=action,  # type: ignore[arg-type]
            reasoning="legacy reasoning",
        )
        assert out.side is expected_side
        assert out.proposal_text == "legacy reasoning"
        assert out.parse_ok is True

    def test_from_legacy_record_unknown_action_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown legacy action"):
            from_fund_manager_record(
                action="unknown",  # type: ignore[arg-type]
                reasoning="x",
            )

    def test_from_legacy_record_drops_target_price(self) -> None:
        # Calling site only passes (action, reasoning, parse_ok) — the
        # legacy ``target_price`` float never reaches FundManagerOutput.
        out = from_fund_manager_record(
            action="买入", reasoning="drop the target_price", parse_ok=True
        )
        assert "target_price" not in out.model_dump()


# ---------------------------------------------------------------------------
# 4-Agent gate
# ---------------------------------------------------------------------------


class TestFourAgentGate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing_field",
        [
            "fundamental_analyst_record_id",
            "technical_analyst_record_id",
            "risk_officer_record_id",
            "fund_manager_record_id",
        ],
    )
    async def test_missing_any_mandatory_agent_degrades(
        self,
        audit_store: AuditStore,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
        missing_field: str,
    ) -> None:
        records = MandatoryAgentRecords(
            fundamental_analyst_record_id="step-fa",
            technical_analyst_record_id="step-ta",
            risk_officer_record_id="step-ro",
            fund_manager_record_id="step-fm",
        )
        records = MandatoryAgentRecords(**{**records.__dict__, missing_field: ""})
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="buy"
            ),
            mandatory_records=records,
            context=ctx,
        )
        assert isinstance(result, BuilderDegrade)
        assert result.reason_namespace == REASON_MISSING_AGENT
        docs = audit_store._mongo.documents  # type: ignore[attr-defined]
        assert len(docs) == 1
        assert docs[0]["event_type"] == AuditEventType.BUILDER_EARLY_RETURN.value
        assert docs[0]["actor"] == AuditActor.SYSTEM.value
        assert docs[0]["outcome"] == AuditOutcome.BLOCKED.value
        assert docs[0]["reason_namespace"] == REASON_MISSING_AGENT

    @pytest.mark.asyncio
    async def test_debate_bypass_degrades(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
            debate_round_count=0,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="buy"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderDegrade)
        assert result.reason_namespace == REASON_DEBATE_BYPASS

    @pytest.mark.asyncio
    async def test_missing_short_circuits_before_debate_check(
        self,
        audit_store: AuditStore,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        records = MandatoryAgentRecords(
            fundamental_analyst_record_id="",
            technical_analyst_record_id="",
            risk_officer_record_id="",
            fund_manager_record_id="",
        )
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
            debate_round_count=0,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="buy"
            ),
            mandatory_records=records,
            context=ctx,
        )
        assert isinstance(result, BuilderDegrade)
        assert result.reason_namespace == REASON_MISSING_AGENT
        # 4 missing — payload preserves ordering.
        assert "fundamental_analyst" in result.payload["missing_agents"]
        assert "fund_manager" in result.payload["missing_agents"]


# ---------------------------------------------------------------------------
# HOLD path
# ---------------------------------------------------------------------------


class TestHoldPath:
    @pytest.mark.asyncio
    async def test_fund_manager_hold_emits_hold_plan(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.HOLD, proposal_text="hold for now"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderPlan)
        plan = result.plan
        assert plan.side is InstructionSide.HOLD
        assert plan.volume is None
        assert plan.limit_price is None
        assert plan.position_summary is None
        assert plan.status is InstructionStatus.VALIDATED
        assert plan.rejection_reason is None
        assert len(plan.risk_summary) == 14
        assert all(row.passed is None for row in plan.risk_summary)
        assert plan.risk_summary[0].rule_name == "code_validity", (
            "rule_name order must match the 14-check engine list"
        )
        assert plan.risk_summary[-1].rule_name == "consecutive_loss_halt"
        assert plan.debate_round_count == 1
        # No audit event for HOLD path (proceed-path ledger entry is
        # opened by the next stage, not the Builder).
        assert audit_store._mongo.documents == []  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_parse_ok_false_forces_hold(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        # Even though the LLM said BUY, parse_ok=False forces HOLD.
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY,
                proposal_text="buy",
                parse_ok=False,
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderPlan)
        plan = result.plan
        assert plan.side is InstructionSide.HOLD
        assert "LLM parse failure" in plan.invalidation_summary


# ---------------------------------------------------------------------------
# BUY/SELL path
# ---------------------------------------------------------------------------


class TestBuySellPath:
    @pytest.mark.asyncio
    async def test_buy_validated_when_engine_passes(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="strong buy signal"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderPlan)
        plan = result.plan
        assert plan.side is InstructionSide.BUY
        assert plan.status is InstructionStatus.VALIDATED
        assert plan.rejection_reason is None
        assert plan.volume == 200
        assert plan.limit_price == 4.5
        assert len(plan.risk_summary) == 14
        assert all(row.passed is True for row in plan.risk_summary)
        assert plan.position_summary is not None
        assert plan.position_summary.pre_position_pct == pytest.approx(0.0)
        assert plan.position_summary.post_position_pct > 0.0
        # InstructionPlan's instruction_id segments line up with code/side/now
        assert plan.instruction_id.split("-")[3] == _CODE
        assert plan.instruction_id.split("-")[4] == "BUY"

    @pytest.mark.asyncio
    async def test_buy_rejected_by_engine_emits_rejected_plan(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        # Push daily_state.today_new_instruction_count to the cap so
        # check 10 (daily_new_instruction_count) trips.
        breached_state = DailyTradingState(
            today_new_instruction_count=5,
            today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(),
            current_price=4.5,
            is_in_halt_cooldown=False,
            halt_until=None,
        )
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=breached_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="buy"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderPlan)
        plan = result.plan
        assert plan.status is InstructionStatus.REJECTED
        assert plan.rejection_reason is not None
        assert "daily_new_instruction_count" in plan.rejection_reason
        # risk_summary: rules before failure passed, failure passed=False,
        # rules after passed=None.
        rules = list(plan.risk_summary)
        idx_failed = next(i for i, r in enumerate(rules) if r.passed is False)
        assert rules[idx_failed].rule_name == "daily_new_instruction_count"
        assert all(r.passed is True for r in rules[:idx_failed])
        assert all(r.passed is None for r in rules[idx_failed + 1 :])

    @pytest.mark.asyncio
    async def test_buy_short_circuits_on_freeze_source(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        empty_positions: tuple[Position, ...],
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        # Re-use the D-003 test fixture for a frozen ticket.
        from backend.models.reconciliation import (
            DeviationReport,
            FieldDeviation,
            ReconciliationTicket,
            ReconciliationTicketStatus,
        )

        devreport = DeviationReport(
            ticket_id="RECON-20260514-001",
            overall_passed=False,
            deviations=(
                FieldDeviation(
                    field="cash",
                    expected="100000.00",
                    actual="99998.00",
                    abs_diff=2.0,
                    threshold=1.0,
                    passed=False,
                ),
            ),
        )
        ticket = ReconciliationTicket(
            ticket_id="RECON-20260514-001",
            trade_date="2026-05-14",
            created_at=_NOW,
            deviation_report=devreport,
            expected_snapshot_id="snap-001",
            actual_reconciliation_id="recon-001",
            status=ReconciliationTicketStatus.OPEN,
        )
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=empty_positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
            open_tickets=(ticket,),
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.BUY, proposal_text="buy"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderEarlyReturn)
        assert result.reason_namespace == REASON_TICKET_OPEN

    @pytest.mark.asyncio
    async def test_sell_validated_when_engine_passes(
        self,
        audit_store: AuditStore,
        all_records_present: MandatoryAgentRecords,
        risk_engine: RiskEngine,
        fat_account: AccountInfo,
        daily_state: DailyTradingState,
        stock_meta: RiskStockMetadata,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        data_snapshot: DataSnapshot,
    ) -> None:
        # Need a position to satisfy fund_sufficiency on SELL.
        positions = (
            Position(
                code=_CODE,
                volume=500,
                available_volume=500,
                cost_price=4.0,
                market_value=2_250.0,
                unrealized_pnl=250.0,
                unrealized_pnl_pct=0.125,
            ),
        )
        ctx = _build_context(
            risk_engine=risk_engine,
            fat_account=fat_account,
            empty_positions=positions,
            daily_state=daily_state,
            stock_meta=stock_meta,
            quiet_breaker=quiet_breaker,
            clean_data_quality=clean_data_quality,
            policy=policy,
            passing_signal=passing_signal,
            data_snapshot=data_snapshot,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        result = await builder.assemble_plan(
            fund_manager_output=FundManagerOutput(
                side=InstructionSide.SELL, proposal_text="take profit"
            ),
            mandatory_records=all_records_present,
            context=ctx,
        )
        assert isinstance(result, BuilderPlan)
        plan = result.plan
        assert plan.side is InstructionSide.SELL
        assert plan.status is InstructionStatus.VALIDATED
        assert plan.position_summary is not None
        assert plan.position_summary.pre_position_pct > 0.0
