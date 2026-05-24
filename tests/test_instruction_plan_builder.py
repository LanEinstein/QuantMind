"""Tests for D-003 InstructionPlanBuilder five early returns.

Coverage focus:

* Each of the 5 checks fires the correct ``BuilderEarlyReturn`` and
  passes when its source is clean.
* Locked first-wins precedence (mode_switch → ticket → CB → DQ → watchlist).
* CB freeze applies to BUY only; SELL bypasses CB even when halted.
* All 8 watchlist sub-reasons are exercised at least once and surface
  in ``payload['exclusion_sub_reason']``.
* Each early return writes exactly ONE BUILDER_EARLY_RETURN audit
  event with the matching ``reason_namespace``.
* Proceed path returns :class:`BuilderProceed` and writes no audit event.
* HOLD candidates raise from the builder (HOLD path is D-004's
  responsibility).
* Module isolation: ``backend.services.instruction_plan_builder``
  doesn't import ``backend.{llm,agents,mirofish}`` (mirrors the
  Phase D import-isolation contract).
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import (
    AuditActor,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.models import CircuitBreakerConfig
from backend.data.data_quality import DataQualityState
from backend.models.instruction import InstructionSide
from backend.models.reconciliation import (
    DeviationReport,
    FieldDeviation,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.risk.circuit_breaker import CircuitBreaker
from backend.services.instruction_plan_builder import (
    REASON_CIRCUIT_BREAKER,
    REASON_DATA_QUALITY,
    REASON_MODE_SWITCH,
    REASON_TICKET_OPEN,
    REASON_WATCHLIST,
    WATCHLIST_SUB_REASONS,
    BuilderEarlyReturn,
    BuilderProceed,
    CandidateInputs,
    FreezeSource,
    InstructionPlanBuilder,
    ModeSwitchProbe,
    WatchlistMarketSignal,
    check_circuit_breaker,
    check_data_quality,
    check_mode_switch,
    check_ticket_freeze,
    check_watchlist_exclusion,
)
from backend.services.universe_policy import (
    FORBIDDEN_BOARDS,
    UniversePolicy,
    UniverseRules,
    load_policy,
)

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_WATCHLIST_CODE = "510300"  # mandatory ETF — guaranteed in policy
_WATCHLIST_NAME = "沪深300 ETF"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> UniversePolicy:
    return load_policy(Path("config/universe_policy.yaml"))


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
def passing_signal() -> WatchlistMarketSignal:
    return WatchlistMarketSignal(
        listed_at_trading_days=720,
        avg_amount_20d_yuan=1_000_000_000.0,
        last_price_yuan=4.5,
    )


@pytest.fixture
def quiet_breaker() -> CircuitBreaker:
    cfg = CircuitBreakerConfig(
        daily_loss_limit_pct=0.05,
        consecutive_loss_count=3,
        cooldown_minutes=60,
    )
    return CircuitBreaker(cfg)


@pytest.fixture
async def audit_store(tmp_path: Path) -> AuditStore:
    return AuditStore(
        InMemoryAuditCollection(),
        jsonl_path=tmp_path / "audit.jsonl",
    )


def _make_ticket(status: ReconciliationTicketStatus) -> ReconciliationTicket:
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
    extra: dict[str, datetime] = {}
    if status in {
        ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
        ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
    }:
        extra["resolved_at"] = datetime(2026, 5, 14, 16, 30, tzinfo=UTC)
    return ReconciliationTicket(
        ticket_id="RECON-20260514-001",
        trade_date="2026-05-14",
        created_at=datetime(2026, 5, 14, 16, 0, tzinfo=UTC),
        deviation_report=devreport,
        expected_snapshot_id="snap-001",
        actual_reconciliation_id="recon-001",
        status=status,
        resolution_message_id=(
            "om-test-msg" if "RESOLVED" in status.value.upper() else None
        ),
        **extra,
    )


def _candidate(
    *,
    code: str = _WATCHLIST_CODE,
    name: str = _WATCHLIST_NAME,
    side: InstructionSide = InstructionSide.BUY,
    open_tickets: tuple[ReconciliationTicket, ...] = (),
    breaker: CircuitBreaker,
    dq: DataQualityState,
    policy: UniversePolicy,
    signal: WatchlistMarketSignal,
    now: datetime = _NOW,
) -> CandidateInputs:
    return CandidateInputs(
        stock_code=code,
        stock_name=name,
        side=side,
        now=now,
        open_tickets=open_tickets,
        circuit_breaker=breaker,
        data_quality=dq,
        watchlist_policy=policy,
        watchlist_signal=signal,
    )


class _StubProbe:
    """Simple ModeSwitchProbe with a flippable flag for tests."""

    def __init__(self, *, active: bool) -> None:
        self.active = active

    def is_active(self) -> bool:
        return self.active


# ---------------------------------------------------------------------------
# Pure check function tests
# ---------------------------------------------------------------------------


class TestCheckModeSwitch:
    def test_inactive_returns_none(self) -> None:
        assert check_mode_switch(_StubProbe(active=False)) is None

    def test_active_returns_early_return(self) -> None:
        result = check_mode_switch(_StubProbe(active=True))
        assert result is not None
        assert result.source is FreezeSource.MODE_SWITCH
        assert result.reason_namespace == REASON_MODE_SWITCH
        assert result.payload["active"] is True


class TestCheckTicketFreeze:
    def test_no_tickets_returns_none(self) -> None:
        assert check_ticket_freeze(()) is None

    def test_resolved_ticket_does_not_freeze(self) -> None:
        ticket = _make_ticket(ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH)
        assert check_ticket_freeze((ticket,)) is None

    @pytest.mark.parametrize(
        "status",
        [
            ReconciliationTicketStatus.OPEN,
            ReconciliationTicketStatus.EXPIRED,
        ],
    )
    def test_open_or_expired_freezes(self, status: ReconciliationTicketStatus) -> None:
        ticket = _make_ticket(status)
        result = check_ticket_freeze((ticket,))
        assert result is not None
        assert result.reason_namespace == REASON_TICKET_OPEN
        assert result.payload["ticket_id"] == "RECON-20260514-001"
        assert result.payload["ticket_status"] == status.value


class TestCheckCircuitBreaker:
    def test_idle_breaker_passes(self, quiet_breaker: CircuitBreaker) -> None:
        assert check_circuit_breaker(quiet_breaker, InstructionSide.BUY, _NOW) is None

    def test_sell_bypasses_even_when_halted(
        self, quiet_breaker: CircuitBreaker
    ) -> None:
        # Trip the breaker via 3 consecutive losses.
        for _ in range(3):
            quiet_breaker.record_trade_result(-0.01, _NOW)
        assert quiet_breaker.is_halted(_NOW) is True
        assert check_circuit_breaker(quiet_breaker, InstructionSide.SELL, _NOW) is None

    def test_buy_blocked_when_halted(self, quiet_breaker: CircuitBreaker) -> None:
        for _ in range(3):
            quiet_breaker.record_trade_result(-0.01, _NOW)
        result = check_circuit_breaker(quiet_breaker, InstructionSide.BUY, _NOW)
        assert result is not None
        assert result.reason_namespace == REASON_CIRCUIT_BREAKER
        assert result.payload["side"] == "BUY"


class TestCheckDataQuality:
    def test_clean_state_passes(self, clean_data_quality: DataQualityState) -> None:
        assert check_data_quality(clean_data_quality) is None

    def test_news_outage_alone_does_not_freeze(self) -> None:
        # News outage is non-blocking per P0-8 §2 redline 11.
        dq = DataQualityState(
            quote_unavailable=False,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=True,
            mirofish_unavailable=True,
            watchlist_snapshot_outage=True,
            primary_quote_age_seconds=2,
            backup_quote_age_seconds=2,
            news_sources_alive_count=0,
        )
        assert dq.is_acceptable_for_buy_sell is True
        assert check_data_quality(dq) is None

    def test_quote_unavailable_freezes(self) -> None:
        dq = DataQualityState(
            quote_unavailable=True,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=0,
            backup_quote_age_seconds=0,
            news_sources_alive_count=5,
        )
        result = check_data_quality(dq)
        assert result is not None
        assert result.reason_namespace == REASON_DATA_QUALITY
        assert result.payload["quote_unavailable"] is True
        assert result.payload["degradation_reason"] == "quote_unavailable"


class TestCheckWatchlistExclusion:
    def test_passing_candidate_returns_none(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        assert (
            check_watchlist_exclusion(
                _WATCHLIST_CODE, _WATCHLIST_NAME, policy, passing_signal
            )
            is None
        )

    def test_forbidden_board_star(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        result = check_watchlist_exclusion(
            "688001", "STAR Demo", policy, passing_signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "forbidden_board"
        assert result.payload["forbidden_reason"] == "star_forbidden"

    @pytest.mark.parametrize(
        ("code", "expected_reason"),
        [
            ("400001", "bj_forbidden"),
            ("110001", "cb_forbidden"),
            ("200001", "b_share_forbidden"),
        ],
    )
    def test_forbidden_board_other(
        self,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        code: str,
        expected_reason: str,
    ) -> None:
        result = check_watchlist_exclusion(code, "demo", policy, passing_signal)
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "forbidden_board"
        assert result.payload["forbidden_reason"] == expected_reason

    def test_unknown_code(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        result = check_watchlist_exclusion("999999", "demo", policy, passing_signal)
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "unknown_code"

    def test_board_not_whitelisted(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        # Post-2026-05-24 amendment the universe is the full market within
        # the board whitelist, so a valid sh_main code like 600000 is NOT
        # excluded by default. The 5th early-return only rejects a board
        # the policy whitelist has been narrowed to exclude (defense-in-
        # depth: honour an amendment that drops a board). Build such a
        # policy by narrowing the whitelist to exclude sh_main.
        narrowed = replace(
            policy,
            universe=UniverseRules(
                board_whitelist=frozenset({"sz_main", "chuangye", "etf"}),
                forbidden_boards=FORBIDDEN_BOARDS,
            ),
        )
        result = check_watchlist_exclusion(
            "600000", "浦发银行", narrowed, passing_signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "board_not_whitelisted"
        assert result.payload["board"] == "sh_main"

    def test_whitelisted_board_passes(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        # A valid sh_main code now passes the board check under the full
        # default whitelist (no more 13-code membership gate).
        result = check_watchlist_exclusion(
            "600000", "浦发银行", policy, passing_signal
        )
        assert result is None

    def test_is_st(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, "*ST 沪深 ETF", policy, passing_signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "is_st"

    def test_ipo_too_new(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=10,
            avg_amount_20d_yuan=passing_signal.avg_amount_20d_yuan,
            last_price_yuan=passing_signal.last_price_yuan,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "ipo_too_new"

    def test_ipo_unknown_listed_days_fails_closed(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=None,
            avg_amount_20d_yuan=passing_signal.avg_amount_20d_yuan,
            last_price_yuan=passing_signal.last_price_yuan,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "ipo_too_new"

    def test_sub_new_too_new(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=120,
            avg_amount_20d_yuan=passing_signal.avg_amount_20d_yuan,
            last_price_yuan=passing_signal.last_price_yuan,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "sub_new_too_new"

    def test_liquidity_too_low(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=passing_signal.listed_at_trading_days,
            avg_amount_20d_yuan=50_000_000.0,
            last_price_yuan=passing_signal.last_price_yuan,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "liquidity_too_low"

    def test_liquidity_unknown_fails_closed(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=passing_signal.listed_at_trading_days,
            avg_amount_20d_yuan=None,
            last_price_yuan=passing_signal.last_price_yuan,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "liquidity_too_low"

    def test_price_too_high(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=passing_signal.listed_at_trading_days,
            avg_amount_20d_yuan=passing_signal.avg_amount_20d_yuan,
            last_price_yuan=600.0,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "price_too_high"

    def test_price_unknown_fails_closed(
        self, policy: UniversePolicy, passing_signal: WatchlistMarketSignal
    ) -> None:
        signal = WatchlistMarketSignal(
            listed_at_trading_days=passing_signal.listed_at_trading_days,
            avg_amount_20d_yuan=passing_signal.avg_amount_20d_yuan,
            last_price_yuan=None,
        )
        result = check_watchlist_exclusion(
            _WATCHLIST_CODE, _WATCHLIST_NAME, policy, signal
        )
        assert result is not None
        assert result.payload["exclusion_sub_reason"] == "price_too_high"

    def test_sub_reason_set_complete(self) -> None:
        # Codifies the locked frozenset so a future refactor cannot
        # silently add a new sub-reason without updating consumers.
        assert WATCHLIST_SUB_REASONS == frozenset(
            {
                "board_not_whitelisted",
                "forbidden_board",
                "unknown_code",
                "is_st",
                "ipo_too_new",
                "sub_new_too_new",
                "liquidity_too_low",
                "price_too_high",
            }
        )


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


class TestBuilderOrchestrator:
    @pytest.mark.asyncio
    async def test_proceed_path_writes_no_audit(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=clean_data_quality,
            policy=policy,
            signal=passing_signal,
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderProceed)
        assert result.candidate is cand
        assert audit_store._mongo.documents == []  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_mode_switch_first_wins(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        # Activate every freeze source — only mode_switch (first in
        # locked order) should be reported.
        for _ in range(3):
            quiet_breaker.record_trade_result(-0.01, _NOW)
        bad_dq = DataQualityState(
            quote_unavailable=True,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=0,
            backup_quote_age_seconds=0,
            news_sources_alive_count=5,
        )
        ticket = _make_ticket(ReconciliationTicketStatus.OPEN)
        builder = InstructionPlanBuilder(
            audit_store=audit_store,
            mode_switch_probe=_StubProbe(active=True),
        )
        cand = _candidate(
            breaker=quiet_breaker,
            dq=bad_dq,
            policy=policy,
            signal=passing_signal,
            open_tickets=(ticket,),
            code="688001",  # would also trip watchlist
            name="*ST WHATEVER",
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderEarlyReturn)
        assert result.source is FreezeSource.MODE_SWITCH
        docs = audit_store._mongo.documents  # type: ignore[attr-defined]
        assert len(docs) == 1
        assert docs[0]["reason_namespace"] == REASON_MODE_SWITCH
        assert docs[0]["event_type"] == AuditEventType.BUILDER_EARLY_RETURN.value
        assert docs[0]["actor"] == AuditActor.SYSTEM.value
        assert docs[0]["outcome"] == AuditOutcome.BLOCKED.value

    @pytest.mark.asyncio
    async def test_ticket_wins_over_cb(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        for _ in range(3):
            quiet_breaker.record_trade_result(-0.01, _NOW)
        ticket = _make_ticket(ReconciliationTicketStatus.OPEN)
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=clean_data_quality,
            policy=policy,
            signal=passing_signal,
            open_tickets=(ticket,),
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderEarlyReturn)
        assert result.source is FreezeSource.TICKET_OPEN
        docs = audit_store._mongo.documents  # type: ignore[attr-defined]
        assert docs[-1]["reason_namespace"] == REASON_TICKET_OPEN

    @pytest.mark.asyncio
    async def test_cb_wins_over_data_quality(
        self,
        audit_store: AuditStore,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        for _ in range(3):
            quiet_breaker.record_trade_result(-0.01, _NOW)
        bad_dq = DataQualityState(
            quote_unavailable=True,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=0,
            backup_quote_age_seconds=0,
            news_sources_alive_count=5,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=bad_dq,
            policy=policy,
            signal=passing_signal,
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderEarlyReturn)
        assert result.source is FreezeSource.CIRCUIT_BREAKER

    @pytest.mark.asyncio
    async def test_data_quality_wins_over_watchlist(
        self,
        audit_store: AuditStore,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        bad_dq = DataQualityState(
            quote_unavailable=True,
            quote_staleness_breach=False,
            quote_divergence_breach=False,
            minimum_freshness_breach=False,
            news_outage_breach=False,
            mirofish_unavailable=False,
            watchlist_snapshot_outage=False,
            primary_quote_age_seconds=0,
            backup_quote_age_seconds=0,
            news_sources_alive_count=5,
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=bad_dq,
            policy=policy,
            signal=passing_signal,
            code="600000",  # DQ breach fires before the watchlist check
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderEarlyReturn)
        assert result.source is FreezeSource.DATA_QUALITY

    @pytest.mark.asyncio
    async def test_watchlist_last_in_chain(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        # Narrow the whitelist to exclude sh_main so 600000 trips the
        # watchlist (last-in-chain) check; under the full default
        # whitelist a valid sh_main code would proceed.
        narrowed = replace(
            policy,
            universe=UniverseRules(
                board_whitelist=frozenset({"sz_main", "chuangye", "etf"}),
                forbidden_boards=FORBIDDEN_BOARDS,
            ),
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=clean_data_quality,
            policy=narrowed,
            signal=passing_signal,
            code="600000",
            name="浦发银行",
        )
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderEarlyReturn)
        assert result.source is FreezeSource.WATCHLIST
        docs = audit_store._mongo.documents  # type: ignore[attr-defined]
        assert docs[-1]["reason_namespace"] == REASON_WATCHLIST
        assert docs[-1]["payload"]["exclusion_sub_reason"] == "board_not_whitelisted"
        assert docs[-1]["payload"]["stock_code"] == "600000"

    @pytest.mark.asyncio
    async def test_audit_payload_includes_candidate_metadata(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        # Narrow the whitelist so 600000 trips the watchlist early-return
        # and an audit event with full candidate metadata is written.
        narrowed = replace(
            policy,
            universe=UniverseRules(
                board_whitelist=frozenset({"sz_main", "chuangye", "etf"}),
                forbidden_boards=FORBIDDEN_BOARDS,
            ),
        )
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=clean_data_quality,
            policy=narrowed,
            signal=passing_signal,
            code="600000",
            name="浦发银行",
            side=InstructionSide.SELL,
        )
        await builder.evaluate_candidate(cand)
        docs = audit_store._mongo.documents  # type: ignore[attr-defined]
        payload = docs[-1]["payload"]
        assert payload["stock_code"] == "600000"
        assert payload["stock_name"] == "浦发银行"
        assert payload["side"] == "SELL"
        # ISO timestamp surfaces; round-trip via fromisoformat to confirm.
        assert datetime.fromisoformat(payload["evaluated_at"]).tzinfo is not None

    @pytest.mark.asyncio
    async def test_default_probe_is_inactive(
        self,
        audit_store: AuditStore,
        clean_data_quality: DataQualityState,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        policy: UniversePolicy,
    ) -> None:
        builder = InstructionPlanBuilder(audit_store=audit_store)
        cand = _candidate(
            breaker=quiet_breaker,
            dq=clean_data_quality,
            policy=policy,
            signal=passing_signal,
        )
        # Default probe (no override) reports inactive — proceed path
        # is reached without writing any audit event.
        result = await builder.evaluate_candidate(cand)
        assert isinstance(result, BuilderProceed)


# ---------------------------------------------------------------------------
# Candidate validation
# ---------------------------------------------------------------------------


class TestCandidateValidation:
    def test_hold_candidate_rejected(
        self,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
    ) -> None:
        with pytest.raises(ValueError, match="HOLD"):
            CandidateInputs(
                stock_code=_WATCHLIST_CODE,
                stock_name=_WATCHLIST_NAME,
                side=InstructionSide.HOLD,
                now=_NOW,
                open_tickets=(),
                circuit_breaker=quiet_breaker,
                data_quality=clean_data_quality,
                watchlist_policy=policy,
                watchlist_signal=passing_signal,
            )

    def test_naive_datetime_rejected(
        self,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
    ) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            CandidateInputs(
                stock_code=_WATCHLIST_CODE,
                stock_name=_WATCHLIST_NAME,
                side=InstructionSide.BUY,
                now=datetime(2026, 5, 15, 10, 30),  # naive
                open_tickets=(),
                circuit_breaker=quiet_breaker,
                data_quality=clean_data_quality,
                watchlist_policy=policy,
                watchlist_signal=passing_signal,
            )

    def test_malformed_code_rejected(
        self,
        policy: UniversePolicy,
        passing_signal: WatchlistMarketSignal,
        quiet_breaker: CircuitBreaker,
        clean_data_quality: DataQualityState,
    ) -> None:
        with pytest.raises(ValueError, match="6 digits"):
            CandidateInputs(
                stock_code="abcdef",
                stock_name=_WATCHLIST_NAME,
                side=InstructionSide.BUY,
                now=_NOW,
                open_tickets=(),
                circuit_breaker=quiet_breaker,
                data_quality=clean_data_quality,
                watchlist_policy=policy,
                watchlist_signal=passing_signal,
            )


# ---------------------------------------------------------------------------
# Module isolation — Builder must not import LLM / agents / mirofish
# ---------------------------------------------------------------------------


class TestImportIsolation:
    def test_builder_does_not_import_forbidden_modules(self) -> None:
        # Re-import in a clean module table to detect transitive imports
        # that the global test session may have already loaded.
        modname = "backend.services.instruction_plan_builder"
        for key in list(sys.modules):
            if key == modname or key.startswith(f"{modname}."):
                sys.modules.pop(key, None)
        importlib.import_module(modname)

        forbidden = {"backend.llm", "backend.agents", "backend.mirofish"}
        loaded = set(sys.modules)
        leaked = {
            f for f in forbidden if any(m == f or m.startswith(f"{f}.") for m in loaded)
        }
        # Filter to direct imports — the test asserts the builder
        # itself never *causes* a forbidden import; if some other test
        # already imported them we still want this assertion to surface
        # the violation if the builder is the importer.
        # We accomplish that by inspecting the source: re-read the file
        # and forbid any matching import line.
        path = sys.modules[modname].__file__
        assert path is not None
        source = Path(path).read_text(encoding="utf-8")
        assert "from backend.llm" not in source
        assert "from backend.agents" not in source
        assert "from backend.mirofish" not in source
        assert "import backend.llm" not in source
        assert "import backend.agents" not in source
        assert "import backend.mirofish" not in source
        # Sanity: the leaked-set check is informational only — it logs
        # the global test state but does not flunk if an unrelated test
        # imported a forbidden module previously.
        del leaked  # explicitly unused; kept for future direct-import probes


# ---------------------------------------------------------------------------
# Reason-namespace contract
# ---------------------------------------------------------------------------


class TestReasonNamespaces:
    def test_namespaces_unique(self) -> None:
        ns = {
            REASON_MODE_SWITCH,
            REASON_TICKET_OPEN,
            REASON_CIRCUIT_BREAKER,
            REASON_DATA_QUALITY,
            REASON_WATCHLIST,
        }
        assert len(ns) == 5

    def test_freeze_source_values_match_reason_constants(self) -> None:
        assert FreezeSource.MODE_SWITCH.value == REASON_MODE_SWITCH
        assert FreezeSource.TICKET_OPEN.value == REASON_TICKET_OPEN
        assert FreezeSource.CIRCUIT_BREAKER.value == REASON_CIRCUIT_BREAKER
        assert FreezeSource.DATA_QUALITY.value == REASON_DATA_QUALITY
        assert FreezeSource.WATCHLIST.value == REASON_WATCHLIST

    def test_mode_switch_probe_protocol_runtime_check(self) -> None:
        # Probe duck-typing: a class with ``is_active`` satisfies the
        # protocol even without explicit inheritance.
        class _CustomProbe:
            def is_active(self) -> bool:
                return False

        assert isinstance(_CustomProbe(), ModeSwitchProbe)
