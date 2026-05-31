"""Tests for the Line-1 production runner (Phase U-C1).

The runner composes the proven ``test_mvp_e2e`` Line-1 chain into a single
production entry point: screen → budget tier → candidate select → ONE
4-agent debate (cost_guard真·预留 inside ``run_shortlist``) →
``to_fund_manager_output`` → ``assemble_plan`` (14-check single
construction point) → ``RouteCoordinator``.

Zero real LLM: a stub router is injected via the test's context provider.
The runner stays import-clean (no backend.{risk,broker,data}); the heavy
risk/broker objects are built HERE (tests may import them freely), exactly
as the U-D1 scheduler will build them in production.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

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
from backend.budget_policy.policy import BudgetTierConfig, BudgetTierPolicy
from backend.candidate_selector.selector import CandidateSelector, SelectorConfig
from backend.data.data_quality import DataQualityState
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import DataSnapshot
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.line1_runner import (
    Line1AllocationSkip,
    Line1LeadContext,
    Line1Outcome,
    Line1QuoteDegrade,
    Line1Runner,
    Line1SelectionMode,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.screening.screener import CandidateRow, Screener
from backend.services import cost_guard
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    InstructionPlanBuilder,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import ExclusionRules, load_policy
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_DECISION_CHAT = "oc_decision_group"


# ---------------------------------------------------------------------------
# Fixture market frame: several liquid sh_main uptrend stocks
# ---------------------------------------------------------------------------


def _uptrend(base: float, n: int = 30) -> list[float]:
    return [base + 0.10 * i for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _snapshot() -> MarketDataSnapshot:
    frame = "\n".join(
        [
            _HEADER,
            _row("600000", "浦发银行", _uptrend(10.0)),
            _row("600004", "白云机场", _uptrend(9.0)),
            _row("600006", "东风汽车", _uptrend(8.0)),
        ]
    )
    raw = frame.encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind",
        endpoint="line1_screener_frame",
        params={"as_of": "20260514"},
        trade_date="20260514",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )


def _etf_snapshot() -> MarketDataSnapshot:
    """Single whitelisted broad-ETF row whose 1 lot tops 15% of a Small tier.

    Uptrend ending at 12.0 → per-lot cost ¥1,200 (> 15% of ¥5,000 cash) so the
    budget policy flags a concentration exception for the over-15% ETF buy.
    """
    frame = "\n".join([_HEADER, _row("510300", "沪深300ETF", _uptrend(9.1))])
    raw = frame.encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind",
        endpoint="line1_screener_frame",
        params={"as_of": "20260514"},
        trade_date="20260514",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )


def _empty_snapshot() -> MarketDataSnapshot:
    """A structurally-valid frame whose only row is ST → excluded → empty."""
    frame = "\n".join([_HEADER, _row("600000", "ST浦发", _uptrend(10.0))])
    raw = frame.encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind",
        endpoint="line1_screener_frame",
        params={"as_of": "20260514"},
        trade_date="20260514",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Stub LLM router + fake redis (mirrors test_mvp_e2e)
# ---------------------------------------------------------------------------


class _StubRouter:
    """4-agent debate stub. ``action`` is configurable (买入/卖出/持有)."""

    def __init__(self, *, action: str = "买入", silent: bool = False) -> None:
        self.calls = 0
        self._action = action
        self._silent = silent

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if self._silent:
            content = ""
        elif agent_name == "fund_manager":
            content = f'{{"action": "{self._action}", "reasoning": "stub thesis"}}'
        else:
            content = f"{agent_name} stub analysis report"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, float] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def decr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def get(self, key: str):  # noqa: ANN201
        v = self.store.get(key)
        return None if v is None else str(v)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


# ---------------------------------------------------------------------------
# Fake context provider — builds the heavy risk/broker context the runner
# is forbidden to import (this is the U-D1 scheduler's job in production).
# ---------------------------------------------------------------------------


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(),
            stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(),
            universe=UniverseConfig(),
        )
    )


def _clean_dq() -> DataQualityState:
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


@dataclass
class FakeProvider:
    """Builds TeamContext + AssemblyContext for each shortlist candidate.

    Structurally satisfies ``Line1ContextProvider`` (duck-typed, no Protocol
    inheritance to keep the dataclass machinery simple).

    ``reject_codes`` forces a RiskEngine REJECT for the named codes (an
    oversized proposed_volume blows the ¥50k single-instruction cap, check 9),
    so a basket walk can be driven past a rejected lead onto the next name.
    The fake accepts (and ignores) ``committed`` — the real provider's
    cash-threading is unit-tested in ``test_line1_context_provider``; here the
    fixed small volume keeps every candidate affordable, isolating the loop.
    """

    cash: float
    router: _StubRouter
    lot_size: int = 100
    hold_lead: bool = False
    proposed_volume: int = 200
    board: RiskBoard = RiskBoard.SH_MAIN
    reject_codes: frozenset[str] = frozenset()
    today_instruction_count: int = 0
    blocking_data_quality: bool = False
    degrade_codes: frozenset[str] = frozenset()
    alloc_skip_codes: frozenset[str] = frozenset()

    @property
    def available_cash(self) -> float:
        return self.cash

    def per_lot_cost(self, code: str, last_price: float) -> float:
        return last_price * self.lot_size

    def prime_allocation(self, shortlist_rows) -> None:
        # No-op: this fake sizes via the fixed ``proposed_volume`` (200), so the
        # runner's hasattr-guarded prime call must not change behaviour here
        # (P-003 — allocation clamp is unit-tested in test_line1_context_provider).
        return None

    async def build_lead_context(
        self,
        lead: CandidateRow,
        *,
        concentration_exception: bool = False,
        committed: tuple = (),
        signal_id: str = "",
        seq: int = 0,
    ) -> Line1LeadContext | Line1QuoteDegrade:
        if lead.code in self.degrade_codes:
            # U-E2: a lead whose live quote is unusable degrades to a
            # non-actionable notice — the runner skips the debate + falls through.
            return Line1QuoteDegrade(
                code=lead.code, name=lead.name, reason="stub: no 卖一"
            )
        if lead.code in self.alloc_skip_codes:
            # P-003: allocation funded 0 lots for this name — a distinct skip
            # (not a quote degrade); the runner falls through to the next name.
            return Line1AllocationSkip(
                code=lead.code, name=lead.name, reason="stub: 0-lot allocation"
            )
        risk_engine = _risk_engine()
        limit_price = round(lead.last_price, 2)
        # An oversized order for a reject_code fails RiskEngine check 9 (single
        # instruction ≤ ¥50k) → status REJECTED → the basket falls through.
        proposed_volume = (
            10_000_000 if lead.code in self.reject_codes else self.proposed_volume
        )
        account = AccountInfo(
            total_assets=self.cash + 2_000.0,
            available_cash=self.cash,
            frozen_cash=0.0,
            market_value=2_000.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=100_000.0,
        )
        # ``hold_lead`` makes the account already hold the lead with ample
        # available volume, so a fund_manager SELL proposal can VALIDATE — the
        # path the non-BUY-discard guard protects against.
        positions: tuple[Position, ...] = (
            (
                Position(
                    code=lead.code,
                    volume=1_000,
                    available_volume=1_000,
                    cost_price=limit_price,
                    market_value=limit_price * 1_000,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                ),
            )
            if self.hold_lead
            else ()
        )
        prev_close = round(lead.last_price * 0.99, 2)
        daily_state = DailyTradingState(
            # Mirror the real provider: count BUYs already routed this BASKET run
            # so the ≤5-orders/day cap (check 10) binds across the basket.
            today_new_instruction_count=self.today_instruction_count + len(committed),
            today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(),
            current_price=limit_price,
            is_in_halt_cooldown=False,
            halt_until=None,
        )
        stock_meta = RiskStockMetadata(
            code=lead.code,
            name=lead.name,
            board=self.board,
            is_st=False,
            instrument_type="etf" if self.board is RiskBoard.ETF else "stock",
        )
        from backend.agents_team.state import CandidateBrief, TeamContext

        brief = CandidateBrief(
            code=lead.code,
            name=lead.name,
            proposed_volume=proposed_volume,
            proposed_limit_price=limit_price,
        )
        team_ctx = TeamContext(
            risk_engine=risk_engine,
            account=account,
            positions=positions,
            prev_close=prev_close,
            daily_state=daily_state,
            stock_meta=stock_meta,
            concentration_exception=concentration_exception,
            now=_NOW,
            llm_router=self.router,
        )
        policy = load_policy(Path("config/universe_policy.yaml"))
        # A blocking data-quality state makes the Builder five-early-return
        # freeze fire (run-level) → EARLY_RETURN, used to assert the basket
        # walk stops instead of debating the rest of the shortlist.
        dq = (
            replace(_clean_dq(), quote_unavailable=True)
            if self.blocking_data_quality
            else _clean_dq()
        )

        def make_assembly_context(
            *,
            signal_id: str,
            seq: int,
            debate_round_count: int,
            analysis_record_id: str,
            risk_validation_id: str,
        ) -> AssemblyContext:
            return AssemblyContext(
                stock_code=lead.code,
                stock_name=lead.name,
                now=_NOW,
                open_tickets=(),
                circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
                data_quality=dq,
                watchlist_policy=policy,
                watchlist_signal=WatchlistMarketSignal(
                    listed_at_trading_days=720,
                    avg_amount_20d_yuan=1_000_000_000.0,
                    last_price_yuan=limit_price,
                ),
                risk_engine=risk_engine,
                account=account,
                positions=positions,
                prev_close=prev_close,
                daily_state=daily_state,
                stock_meta=stock_meta,
                proposed_volume=proposed_volume,
                proposed_limit_price=limit_price,
                concentration_exception=concentration_exception,
                seq=seq,
                signal_id=signal_id,
                analysis_record_id=analysis_record_id,
                risk_validation_id=risk_validation_id,
                debate_round_count=debate_round_count,
                evidence_ids=(),
                data_snapshot=DataSnapshot(
                    snapshot_at=_SNAP_AT,
                    quote_source="primary",
                    is_trading_day=True,
                    is_trading_hours=True,
                    prev_close=prev_close,
                ),
                invalidation_summary="Line-1 BUY",
            )

        return Line1LeadContext(
            brief=brief,
            team_context=team_ctx,
            make_assembly_context=make_assembly_context,
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


def _budget_policy() -> BudgetTierPolicy:
    return BudgetTierPolicy(
        BudgetTierConfig(
            micro_max_cash_yuan=2_000.0,
            small_max_cash_yuan=10_000.0,
            max_single_stock_pct=0.15,
            lot_size=100,
            etf_whitelist=frozenset({"510300", "510500", "159949"}),
        )
    )


def _make_runner(
    *,
    mode: RouteMode,
    sender: FakeFeishuSender,
    builder: InstructionPlanBuilder,
    tmp_path: Path,
    selection_mode: Line1SelectionMode = Line1SelectionMode.BASKET,
    digest_outbox: InMemoryOutboxRepository | None = None,
) -> Line1Runner:
    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch_audit.jsonl"
    )
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
    )

    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    dispatcher = InstructionDispatcher(
        feishu_client=sender,
        decision_chat_id=_DECISION_CHAT,
        outbox=InMemoryOutboxRepository(),
        ledger=ledger,
        audit_store=audit,
    )
    coordinator = RouteCoordinator(
        mode=mode,
        simulation_executor=_FakeSimExecutor(),
        dispatcher=dispatcher,
    )
    return Line1Runner(
        screener=Screener(ExclusionRules()),
        budget_policy=_budget_policy(),
        selector=CandidateSelector(
            SelectorConfig(
                version="u-c1/v1",
                final_shortlist_size=5,
                min_quant_slots=3,
                max_percentile_shift=0.01,
                advisory_weight=0.0,
                feature_def_hash="h",
            )
        ),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        redis_client=_FakeRedis(),
        selection_mode=selection_mode,
        # P-004 basket digest: wired only when a digest_outbox is supplied (most
        # tests leave it off so their sender.calls counts stay order-only).
        digest_sender=sender if digest_outbox is not None else None,
        digest_chat_id=_DECISION_CHAT if digest_outbox is not None else "",
        digest_outbox=digest_outbox,
    )


class _FakeSimExecutor:
    """Records auto-fill routing without a live MockBroker."""

    def __init__(self) -> None:
        self.routed: list[str] = []

    async def route(self, plan, *, now):  # noqa: ANN001, ANN201
        self.routed.append(plan.instruction_id)
        from backend.services.simulation_executor import SimulationRouteResult

        return SimulationRouteResult(
            instruction_id=plan.instruction_id,
            final_status=plan.status,
            broker_order_id=None,
            trade_ids=(),
            reason=None,
        )


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_feishu_mode_routes_validated_buy_basket(builder, tmp_path) -> None:
    # BASKET default (P1-7-amendment-2026-05-26): every VALIDATED BUY in the
    # shortlist is routed + sent, not just the lead.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    # 3 screened candidates, all VALIDATED → a 3-name basket.
    assert len(result.routed_buys) == 3
    assert all(rb.plan.side.value == "BUY" for rb in result.routed_buys)
    # The deterministic screener ranks by *percentage* momentum (lowest base,
    # same absolute step) — the lead is the strongest, processed first.
    assert result.lead_code in {"600000", "600004", "600006"}
    assert result.plan is not None and result.plan.stock_code == result.lead_code
    assert result.route_outcome is not None
    assert result.route_outcome.action == "dispatched"
    # Each BUY reached the decision group with a distinct instruction_id.
    assert len(sender.calls) == 3
    assert all("【QuantMind 买入信号 · 合规】" in c["content"] for c in sender.calls)
    ids = {rb.plan.instruction_id for rb in result.routed_buys}
    assert len(ids) == 3  # distinct ids (code segment differs per candidate)


async def test_basket_digest_sent_once_after_routing(builder, tmp_path) -> None:
    # P-004: after the per-name orders, ONE display-only basket digest is sent
    # to the decision group (independent of the dispatcher).
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
        digest_outbox=InMemoryOutboxRepository(),
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    digests = [c for c in sender.calls if "组合配比概览" in c["content"]]
    assert len(digests) == 1  # exactly one digest
    orders = [c for c in sender.calls if "买入信号" in c["content"]]
    assert len(orders) == 3  # the 3 per-name orders are unchanged
    # Display-only: the digest carries no instruction_id / execution verb.
    assert "QM-" not in digests[0]["content"]


async def test_basket_digest_excludes_send_failed_orders(builder, tmp_path) -> None:
    # P-004 (codex P2): a per-name dispatch that failed to reach Feishu
    # (send_failed) is still ROUTED, but the digest must NOT claim it — only
    # delivered orders (dispatched / skipped_duplicate) appear in the summary.
    sender = FakeFeishuSender(fail_first_n=1)  # first BUY send fails
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
        digest_outbox=InMemoryOutboxRepository(),
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    # 3 candidates routed; 1 send_failed, 2 dispatched.
    assert len(result.routed_buys) == 3
    digests = [c for c in sender.calls if "组合配比概览" in c["content"]]
    assert len(digests) == 1
    assert "共 2 只" in digests[0]["content"]  # only the 2 delivered orders


async def test_basket_digest_idempotent_across_reruns(builder, tmp_path) -> None:
    # P-004: a same-day rerun (same signal_id) must NOT re-send the digest —
    # the durable outbox claim is the at-most-once gate.
    sender = FakeFeishuSender()
    shared_outbox = InMemoryOutboxRepository()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
        digest_outbox=shared_outbox,
    )
    router = _StubRouter(action="买入")
    for _ in range(2):
        await runner.run(
            frame=_snapshot(),
            provider=FakeProvider(cash=98_000.0, router=router),
            now=_NOW,
        )
    digests = [c for c in sender.calls if "组合配比概览" in c["content"]]
    assert len(digests) == 1  # two runs → one digest


async def test_buy_signal_carries_display_only_rationale(builder, tmp_path) -> None:
    # U-E4 缺口3: the routed BUY wire carries a 判据 block — 量化 (score +
    # factors) + 推理 (fund_manager + analysts) — assembled by the runner from
    # the screener CandidateRow + debate state and passed as a render param.
    # It is display-only: NEVER on the routed InstructionPlan, and the
    # deterministic volume is untouched (single construction point M-004).
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
        selection_mode=Line1SelectionMode.SINGLE,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    wire = sender.calls[0]["content"]
    # 量化 + 推理 判据 on the wire (display-only).
    assert "—— 量化判据 ——" in wire
    assert "综合评分:" in wire
    assert "动量(20日):" in wire
    assert "—— 推理判据 ——" in wire
    assert "基金经理: stub thesis" in wire
    assert "基本面: fundamental_analyst stub analysis report" in wire
    # Prominent 交易要点 block at the top of the signal.
    assert "交易要点" in wire
    # …but the rationale text is NOT on the routed plan, and the deterministic
    # provider volume (200) is unchanged by the LLM reasoning text.
    plan = result.routed_buys[0].plan
    assert "stub thesis" not in plan.model_dump_json()
    assert plan.volume == 200


async def test_small_tier_etf_routes_concentration_exception_template(
    builder, tmp_path
) -> None:
    # U-C4: a Small-tier (¥5,000) buy of a whitelisted broad ETF whose 1 lot
    # tops the 15% single-stock cap must thread the budget-policy
    # concentration flag through assemble_plan's 14-check → VALIDATE → render
    # with the ETF concentration-exception template (human-confirm block),
    # NOT the normal-compliant template.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_etf_snapshot(),
        provider=FakeProvider(
            cash=5_000.0,
            router=router,
            proposed_volume=100,  # exactly 1 lot (the exception's absolute cap)
            board=RiskBoard.ETF,
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert result.tier is not None and result.tier.value == "small"
    assert result.lead_code == "510300"
    assert result.route_outcome is not None
    # The over-15% ETF VALIDATED only because the exception was granted.
    assert result.plan is not None
    assert any(
        row.passed is True
        and "concentration_exception_granted" in (row.message or "")
        for row in result.plan.risk_summary
    )
    # The wire text uses the ETF concentration-exception template + confirm.
    assert len(sender.calls) == 1
    content = sender.calls[0]["content"]
    assert "【QuantMind 买入信号 · ETF 集中度例外 · 需确认】" in content
    assert f"确认执行请回复:确认 {result.plan.instruction_id}" in content


async def test_basket_debates_each_shortlist_candidate(builder, tmp_path) -> None:
    # P1-7-amendment-2026-05-26 OVERTURNS "one debate per daily shortlist": the
    # basket debates EACH candidate (4 agent calls × N candidates), each via the
    # single construction point. RiskEngine still independently validates each.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    n = len(result.shortlist)
    assert n == 3
    assert router.calls == 4 * n  # one 4-agent debate per candidate
    assert len(result.routed_buys) == n
    # Single construction point holds for EVERY basket BUY: the deterministic
    # provider volume (200), never a number parsed from the LLM "买入" text.
    assert all(rb.plan.volume == 200 for rb in result.routed_buys)


async def test_basket_falls_through_rejected_lead_to_next(builder, tmp_path) -> None:
    # The top-ranked lead is REJECTED by the RiskEngine (oversized order) → the
    # basket falls through and still routes the remaining VALIDATED names.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    # 600006 is the strongest %-momentum lead (lowest base) → reject it.
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router, reject_codes=frozenset({"600006"})
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert result.lead_code == "600006"  # the rejected top remains the lead
    routed_codes = {rb.plan.stock_code for rb in result.routed_buys}
    assert "600006" not in routed_codes  # rejected → not routed
    assert routed_codes == {"600000", "600004"}  # fell through to the rest
    assert router.calls == 12  # every candidate still debated (3 × 4)


async def test_basket_falls_through_quote_degraded_lead_to_next(
    builder, tmp_path
) -> None:
    # U-E2: a lead whose live quote is unusable degrades to a non-actionable
    # notice (no debate burned) → the basket falls through to the next name.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router, degrade_codes=frozenset({"600006"})
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    routed_codes = {rb.plan.stock_code for rb in result.routed_buys}
    assert "600006" not in routed_codes  # degraded → never priced / routed
    assert routed_codes == {"600000", "600004"}
    # The degraded lead burned NO debate (skipped before run_shortlist): 2 × 4.
    assert router.calls == 8


async def test_all_quote_degraded_zero_buy(builder, tmp_path) -> None:
    # Whole shortlist quote-degraded → 0 BUY, no Feishu order send, no debate.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router,
            degrade_codes=frozenset({"600000", "600004", "600006"}),
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.QUOTE_DEGRADED
    assert result.routed_buys == ()
    assert sender.calls == []  # no order ever sent on a degraded quote
    assert router.calls == 0  # no debate burned on any degraded lead


async def test_basket_falls_through_allocation_skipped_lead_to_next(
    builder, tmp_path
) -> None:
    # P-003: a lead whose allocation target floored to 0 lots is SKIPPED (no
    # debate burned, no quote notice) → the basket falls through to the next.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router, alloc_skip_codes=frozenset({"600006"})
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    routed_codes = {rb.plan.stock_code for rb in result.routed_buys}
    assert "600006" not in routed_codes  # 0-lot allocation → skipped
    assert routed_codes == {"600000", "600004"}
    # The skipped lead burned NO debate (skipped before run_shortlist): 2 × 4.
    assert router.calls == 8


async def test_all_allocation_skipped_zero_buy(builder, tmp_path) -> None:
    # Whole shortlist allocation-skipped → 0 BUY, classified ALLOCATION_SKIPPED
    # (NOT QUOTE_DEGRADED), no order send, no debate.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router,
            alloc_skip_codes=frozenset({"600000", "600004", "600006"}),
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ALLOCATION_SKIPPED
    assert result.routed_buys == ()
    assert sender.calls == []  # no order ever sent
    assert router.calls == 0  # no debate burned


async def test_basket_all_rejected_zero_buy_graceful(builder, tmp_path) -> None:
    # Whole shortlist REJECTED → 0 BUY, graceful terminal (no crash, no send).
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0,
            router=router,
            reject_codes=frozenset({"600000", "600004", "600006"}),
        ),
        now=_NOW,
    )
    assert result.routed_buys == ()
    assert result.outcome is Line1Outcome.REJECTED  # last candidate's terminal
    assert sender.calls == []


async def test_basket_stops_when_daily_budget_exhausted(
    builder, tmp_path, monkeypatch
) -> None:
    # ¥100 daily hard cap already spent → the first reservation is refused
    # (the crossing call never runs) → 0 BUY, BUDGET_EXHAUSTED, no crash.
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 100.0  # at the hard ceiling

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.BUDGET_EXHAUSTED
    assert result.routed_buys == ()
    assert router.calls == 0  # the refused reservation ran no debate
    assert sender.calls == []


async def test_basket_stops_when_debate_slots_exhausted(
    builder, tmp_path, monkeypatch
) -> None:
    # max_debates_per_day = 1 → 1st candidate debates + routes, 2nd candidate's
    # debate-slot claim is refused → walk stops fail-closed, keeping the 1 BUY.
    monkeypatch.setenv("QUANTMIND_MAX_DEBATES_PER_DAY", "1")
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED  # ≥1 routed → ROUTED
    assert len(result.routed_buys) == 1
    assert router.calls == 4  # only the first candidate's debate completed
    assert len(sender.calls) == 1


async def test_basket_bounded_by_daily_order_cap(builder, tmp_path) -> None:
    # codex P1: with 4 orders already used today, only 1 daily slot remains
    # (check 10 ≤ 5/day). The basket threads each routed BUY into the next
    # candidate's count, so it routes exactly 1 and the rest are REJECTED.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router, today_instruction_count=4
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert len(result.routed_buys) == 1  # only the 5th-order slot was free
    assert len(sender.calls) == 1


async def test_basket_stops_on_run_level_freeze(builder, tmp_path) -> None:
    # codex P2: a Builder five-early-return freeze (here a blocking data-quality
    # state) is run-level — the basket must STOP after the first candidate
    # rather than burn debates on names that would all freeze identically.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(
            cash=98_000.0, router=router, blocking_data_quality=True
        ),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.EARLY_RETURN
    assert result.routed_buys == ()
    assert router.calls == 4  # only the FIRST candidate was debated, then stop
    assert sender.calls == []


async def test_single_mode_returns_first_validated_buy(builder, tmp_path) -> None:
    # SINGLE mode stops at the first VALIDATED BUY — no further debates.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
        selection_mode=Line1SelectionMode.SINGLE,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert len(result.routed_buys) == 1
    assert len(result.shortlist) >= 2  # more candidates existed, not debated
    assert router.calls == 4  # exactly one debate
    assert len(sender.calls) == 1


async def test_no_compliant_trade_when_budget_blocks(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    # Micro tier (¥500 < ¥2k): individual stocks are not in the tier universe.
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=500.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.NO_COMPLIANT_TRADE
    assert result.route_outcome is None
    assert router.calls == 0  # no debate spun up — no LLM spend
    assert sender.calls == []


async def test_simulation_mode_auto_fills_no_feishu(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.SIMULATION_AUTO,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert len(result.routed_buys) == 3  # basket auto-filled
    assert result.route_outcome.action == "simulation_routed"
    assert all(
        rb.route_outcome.action == "simulation_routed" for rb in result.routed_buys
    )
    assert sender.calls == []  # no Feishu send in simulation_auto


async def test_dry_run_renders_only(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    sink: list[str] = []
    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch_audit.jsonl"
    )
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
    )

    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    dispatcher = InstructionDispatcher(
        feishu_client=sender,
        decision_chat_id=_DECISION_CHAT,
        outbox=InMemoryOutboxRepository(),
        ledger=ledger,
        audit_store=audit,
    )
    coordinator = RouteCoordinator(
        mode=RouteMode.DRY_RUN,
        simulation_executor=_FakeSimExecutor(),
        dispatcher=dispatcher,
        dry_run_sink=sink.append,
    )
    runner = Line1Runner(
        screener=Screener(ExclusionRules()),
        budget_policy=_budget_policy(),
        selector=CandidateSelector(
            SelectorConfig(
                version="u-c1/v1",
                final_shortlist_size=5,
                min_quant_slots=3,
                max_percentile_shift=0.01,
                advisory_weight=0.0,
                feature_def_hash="h",
            )
        ),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        redis_client=_FakeRedis(),
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert len(result.routed_buys) == 3  # basket rendered to the dry-run sink
    assert result.route_outcome.action == "dry_run_rendered"
    assert sender.calls == []  # never sent
    assert len(sink) == 3
    assert "【QuantMind 买入信号 · 合规】" in sink[0]


async def test_hold_recommendation_not_routed(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="持有")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.HOLD
    assert sender.calls == []  # HOLD never routes/sends (CLAUDE.md §2.7)


async def test_validated_sell_is_discarded_not_rendered_as_buy(
    builder, tmp_path
) -> None:
    # fund_manager proposes 卖出 on a held lead → assemble_plan can VALIDATE a
    # SELL. Line-1 must NOT feed it to the BUY-only renderer (would raise) and
    # must NOT auto-sell (SELL is Line-2's job) — discard fail-closed.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="卖出")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router, hold_lead=True),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.NON_BUY_DISCARDED
    assert result.plan is not None and result.plan.side.value == "SELL"
    assert result.route_outcome is None
    assert sender.calls == []  # no BUY sent, no crash


async def test_silent_agents_degrade_to_hold(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    # Silent router → every agent report empty → the 4-agent gate degrades.
    router = _StubRouter(silent=True)
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.DEGRADED
    assert sender.calls == []


async def test_empty_universe_short_circuits(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_empty_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.EMPTY_UNIVERSE
    assert router.calls == 0  # no debate when no candidate survives the screen
    assert sender.calls == []


def test_runner_is_import_clean() -> None:
    """The runner module must not import backend.{risk,broker,data,llm,...}."""
    import ast

    src = Path("backend/orchestration/line1_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"risk", "broker", "data", "llm", "agents", "mirofish", "api"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in banned:
                raise AssertionError(
                    f"line1_runner imports forbidden backend.{parts[1]} "
                    f"({node.module}) — orchestration isolation broken"
                )
