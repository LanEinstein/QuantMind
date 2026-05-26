"""Tests for the production Line-1 context provider (Phase U-D1b).

Two layers:

* **Unit** — the deterministic ``max_compliant_buy_volume`` sizing helper, the
  async ``build_line1_run_state`` assembler, and the sync provider surface
  (``available_cash`` / ``per_lot_cost`` / ``build_lead_context``).
* **End-to-end** — the REAL :class:`Line1Runner` driven by the REAL
  :class:`Line1ContextProvider` on a fixture T-1 EOD frame, with a stub LLM
  router (zero real provider calls): screen → budget → select → ONE 4-agent
  debate → ``assemble_plan`` 14-check single construction point → VALIDATED
  BUY → ``RouteCoordinator``. Plus the U-C4 concentration-exception ETF path.
"""

from __future__ import annotations

import hashlib
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
from backend.data.stock_metadata import Board
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.orchestration.line1_runner import Line1Outcome, Line1Runner
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.engine import RiskEngine
from backend.screening.factors import FactorVector
from backend.screening.screener import CandidateRow, Screener
from backend.services import cost_guard
from backend.services.instruction_plan_builder import InstructionPlanBuilder
from backend.services.line1_context_provider import (
    Line1ContextProvider,
    build_line1_run_state,
    max_compliant_buy_volume,
)
from backend.services.universe_policy import ExclusionRules, load_policy
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_DECISION_CHAT = "oc_decision_group"


# ---------------------------------------------------------------------------
# Fixture frame + doubles (mirror test_line1_runner)
# ---------------------------------------------------------------------------


def _uptrend(base: float, n: int = 30) -> list[float]:
    return [base + 0.10 * i for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _snapshot(rows: list[str]) -> MarketDataSnapshot:
    frame = "\n".join([_HEADER, *rows])
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


def _stock_frame() -> MarketDataSnapshot:
    return _snapshot(
        [
            _row("600000", "浦发银行", _uptrend(10.0)),
            _row("600004", "白云机场", _uptrend(9.0)),
            _row("600006", "东风汽车", _uptrend(8.0)),
        ]
    )


def _etf_frame() -> MarketDataSnapshot:
    # Whitelisted broad ETF whose 1 lot tops 15% of a Small tier (¥5k cash).
    return _snapshot([_row("510300", "沪深300ETF", _uptrend(9.1))])


class _StubRouter:
    """4-agent debate stub; ``action`` configurable (买入/卖出/持有)."""

    def __init__(self, *, action: str = "买入") -> None:
        self.calls = 0
        self._action = action

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if agent_name == "fund_manager":
            content = f'{{"action": "{self._action}", "reasoning": "stub"}}'
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


class _FakeBroker:
    """Minimal broker: get_account + get_positions for build_line1_run_state."""

    def __init__(
        self, *, cash: float, positions: tuple[Position, ...] = ()
    ) -> None:
        self._cash = cash
        self._positions = positions

    async def get_account(self) -> AccountInfo:
        market_value = sum(p.market_value for p in self._positions)
        return AccountInfo(
            total_assets=self._cash + market_value,
            available_cash=self._cash,
            frozen_cash=0.0,
            market_value=market_value,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=100_000.0,
        )

    async def get_positions(self) -> list[Position]:
        return list(self._positions)


def _risk_config() -> RiskConfig:
    return RiskConfig(
        position_limits=PositionLimitsConfig(),
        stop_loss=StopLossConfig(),
        circuit_breaker=CircuitBreakerConfig(),
        universe=UniverseConfig(),
    )


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


def _selector() -> CandidateSelector:
    return CandidateSelector(
        SelectorConfig(
            version="u-d1b/v1",
            final_shortlist_size=5,
            min_quant_slots=3,
            max_percentile_shift=0.01,
            advisory_weight=0.0,
            feature_def_hash="h",
        )
    )


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


async def _make_provider(
    *,
    frame: MarketDataSnapshot,
    cash: float,
    router: _StubRouter,
    positions: tuple[Position, ...] = (),
) -> Line1ContextProvider:
    run_state = await build_line1_run_state(
        broker=_FakeBroker(cash=cash, positions=positions),
        risk_engine=RiskEngine(_risk_config()),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        risk_config=_risk_config(),
        now=_NOW,
    )
    return Line1ContextProvider(
        run_state=run_state, frame=frame, llm_router=router, now=_NOW
    )


def _make_runner(
    *, builder: InstructionPlanBuilder, sender: FakeFeishuSender, tmp_path: Path
) -> Line1Runner:
    from backend.orchestration.instruction_dispatcher import (
        InMemoryOutboxRepository,
        InstructionDispatcher,
    )
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
    )

    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch.jsonl"
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
        mode=RouteMode.FEISHU_INTERACTIVE,
        simulation_executor=None,
        dispatcher=dispatcher,
    )
    return Line1Runner(
        screener=Screener(ExclusionRules()),
        budget_policy=_budget_policy(),
        selector=_selector(),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        redis_client=_FakeRedis(),
    )


# ---------------------------------------------------------------------------
# Unit — sizing helper
# ---------------------------------------------------------------------------


def test_sizing_capped_by_single_stock_pct() -> None:
    # 15% of ¥100k = ¥15k; ¥10/share → 1500 shares → 15 lots → 1500 shares.
    vol = max_compliant_buy_volume(
        last_price=10.0,
        total_assets=100_000.0,
        available_cash=100_000.0,
        existing_shares=0,
        lot_size=100,
        other_positions_value=0.0,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 1500
    assert vol * 10.0 <= 0.15 * 100_000.0


def test_sizing_capped_by_single_instruction_amount() -> None:
    # Huge cash so the 15% cap is not binding; ¥50k / ¥200 = 250 → 2 lots.
    vol = max_compliant_buy_volume(
        last_price=200.0,
        total_assets=5_000_000.0,
        available_cash=5_000_000.0,
        existing_shares=0,
        lot_size=100,
        other_positions_value=0.0,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 200  # 2 lots; 200×200 = ¥40k ≤ ¥50k, 300 would breach
    assert vol * 200.0 <= 50_000.0


def test_sizing_nets_existing_holding() -> None:
    # 15% cap = 1500 shares; already hold 1400 → only 1 more lot fits.
    vol = max_compliant_buy_volume(
        last_price=10.0,
        total_assets=100_000.0,
        available_cash=100_000.0,
        existing_shares=1_400,
        lot_size=100,
        other_positions_value=0.0,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 100


def test_sizing_cash_cap_honours_fee_buffer() -> None:
    # available_cash binds: WITHOUT the 0.1% fee buffer, 200 shares × ¥100 ×
    # 1.001 = ¥20,020 > ¥20,000 → RiskEngine check 4 REJECT. The buffer-aware
    # cap floors to 1 lot so the routed order actually VALIDATES.
    vol = max_compliant_buy_volume(
        last_price=100.0,
        total_assets=10_000_000.0,  # 15% + 70% caps non-binding
        available_cash=20_000.0,
        other_positions_value=0.0,
        existing_shares=0,
        lot_size=100,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 100
    assert vol * 100.0 * 1.001 <= 20_000.0


def test_sizing_capped_by_total_position_pct() -> None:
    # Book already 66% invested in OTHER names; the 70% total cap leaves only
    # ¥4k of headroom → 400 shares of a ¥10 name, not the 15% single-stock max.
    vol = max_compliant_buy_volume(
        last_price=10.0,
        total_assets=100_000.0,
        available_cash=100_000.0,
        other_positions_value=66_000.0,
        existing_shares=0,
        lot_size=100,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 400  # (0.70×100k − 66k) / 10 = 400; resulting total = 70%
    assert 66_000.0 + vol * 10.0 <= 0.70 * 100_000.0


def test_sizing_concentration_exception_is_one_lot() -> None:
    vol = max_compliant_buy_volume(
        last_price=12.0,
        total_assets=5_000.0,
        available_cash=5_000.0,
        existing_shares=0,
        lot_size=100,
        other_positions_value=0.0,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=True,
        exception_max_lots=1,
    )
    assert vol == 100  # exactly max_lots × lot_size


def test_sizing_bad_price_returns_one_lot() -> None:
    # A non-finite / non-positive price is rejected downstream (builder /
    # engine); sizing returns one lot so the plan still constructs.
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        assert (
            max_compliant_buy_volume(
                last_price=bad,
                total_assets=100_000.0,
                available_cash=100_000.0,
                other_positions_value=0.0,
                existing_shares=0,
                lot_size=100,
                max_single_stock_pct=0.15,
                max_total_position_pct=0.70,
                max_single_instruction_amount=50_000.0,
                concentration_exception=False,
                exception_max_lots=1,
            )
            == 100
        )


def test_sizing_floor_is_one_lot_never_zero() -> None:
    # An over-held name leaves no room; the floor returns 1 lot (the RiskEngine
    # is the authoritative REJECT — sizing never bypasses the cap).
    vol = max_compliant_buy_volume(
        last_price=10.0,
        total_assets=100_000.0,
        available_cash=100_000.0,
        existing_shares=10_000,
        lot_size=100,
        other_positions_value=0.0,
        max_single_stock_pct=0.15,
        max_total_position_pct=0.70,
        max_single_instruction_amount=50_000.0,
        concentration_exception=False,
        exception_max_lots=1,
    )
    assert vol == 100


# ---------------------------------------------------------------------------
# Unit — run-state + provider surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_run_state_pulls_account_and_positions() -> None:
    held = (
        Position(
            code="600000",
            volume=200,
            available_volume=200,
            cost_price=10.0,
            market_value=2_000.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
        ),
    )
    run_state = await build_line1_run_state(
        broker=_FakeBroker(cash=98_000.0, positions=held),
        risk_engine=RiskEngine(_risk_config()),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        risk_config=_risk_config(),
        now=_NOW,
    )
    assert run_state.account.available_cash == 98_000.0
    assert run_state.positions == held
    assert run_state.halted is False
    assert run_state.halt_until is None


@pytest.mark.asyncio
async def test_provider_available_cash_and_per_lot_cost() -> None:
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    assert provider.available_cash == 98_000.0
    assert provider.per_lot_cost("600000", 10.0) == 1_000.0  # 10 × 100-lot


@pytest.mark.asyncio
async def test_per_lot_cost_unknown_board_fails_closed() -> None:
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    # A 科创板 688xxx code is forbidden → non-finite cost → budget UNAFFORDABLE.
    cost = provider.per_lot_cost("688001", 10.0)
    assert cost == float("inf")


@pytest.mark.asyncio
async def test_build_lead_context_derives_prev_close_and_volume() -> None:
    frame = _stock_frame()
    provider = await _make_provider(
        frame=frame, cash=98_000.0, router=_StubRouter()
    )
    closes = _uptrend(10.0)
    lead = CandidateRow(
        code="600000",
        name="浦发银行",
        board=Board.SH_MAIN,
        score=0.9,
        last_price=closes[-1],
        factors=FactorVector(
            momentum_20d=0.2,
            ma_ratio_5_20=1.05,
            volatility_20d=0.01,
            rsi_14=60.0,
            avg_amount_20d=3e8,
        ),
    )
    lead_ctx = provider.build_lead_context(lead)
    # prev_close = the prior bar from the frame, not a synthetic guess.
    assert lead_ctx.team_context.prev_close == pytest.approx(closes[-2])
    # Deterministic compliant volume: whole lots within the 15% cap.
    vol = lead_ctx.brief.proposed_volume
    assert vol > 0 and vol % 100 == 0
    assert vol * round(closes[-1], 2) <= 0.15 * 100_000.0
    # The debate router is threaded through, not derived.
    assert lead_ctx.team_context.llm_router is provider._llm_router  # noqa: SLF001
    # The AssemblyContext factory carries the same deterministic volume.
    ctx = lead_ctx.make_assembly_context(
        signal_id="SIG-x",
        seq=1,
        debate_round_count=1,
        analysis_record_id="ar",
        risk_validation_id="rv",
    )
    assert ctx.proposed_volume == vol
    assert ctx.concentration_exception is False
    assert ctx.data_snapshot.snapshot_at == frame.fetch_time_utc


# ---------------------------------------------------------------------------
# End-to-end — real runner + real provider + stub LLM (0 real network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_lead_context_prev_close_falls_back_when_absent() -> None:
    # The lead's code is not in the frame (edge) → prev_close conservatively
    # falls back to the last price (a 0% move is always price-reasonable).
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    lead = CandidateRow(
        code="600999",  # not present in _stock_frame()
        name="缺席标的",
        board=Board.SH_MAIN,
        score=0.5,
        last_price=20.0,
        factors=FactorVector(
            momentum_20d=0.1,
            ma_ratio_5_20=1.0,
            volatility_20d=0.01,
            rsi_14=55.0,
            avg_amount_20d=3e8,
        ),
    )
    lead_ctx = provider.build_lead_context(lead)
    assert lead_ctx.team_context.prev_close == 20.0


@pytest.mark.asyncio
async def test_build_lead_context_non_positive_prev_close_falls_back() -> None:
    # A degenerate prior bar (closes[-2] = 0.0 from a data glitch) must NOT
    # reach DataSnapshot.prev_close (gt=0.0) — it would raise ValidationError
    # and abort the daily run. Fall back to the last price instead.
    glitched = [10.0 + 0.10 * i for i in range(28)] + [0.0, 12.9]
    frame = _snapshot([_row("600000", "浦发银行", glitched)])
    provider = await _make_provider(
        frame=frame, cash=98_000.0, router=_StubRouter()
    )
    lead = CandidateRow(
        code="600000",
        name="浦发银行",
        board=Board.SH_MAIN,
        score=0.9,
        last_price=12.9,
        factors=FactorVector(
            momentum_20d=0.2,
            ma_ratio_5_20=1.05,
            volatility_20d=0.01,
            rsi_14=60.0,
            avg_amount_20d=3e8,
        ),
    )
    lead_ctx = provider.build_lead_context(lead)
    assert lead_ctx.team_context.prev_close == 12.9
    # The factory must build a valid DataSnapshot (no ValidationError raised).
    ctx = lead_ctx.make_assembly_context(
        signal_id="SIG-x",
        seq=1,
        debate_round_count=1,
        analysis_record_id="ar",
        risk_validation_id="rv",
    )
    assert ctx.data_snapshot.prev_close == 12.9


@pytest.mark.asyncio
async def test_provider_runner_routes_validated_buy(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(builder=builder, sender=sender, tmp_path=tmp_path)
    router = _StubRouter(action="买入")
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=router
    )
    result = await runner.run(frame=_stock_frame(), provider=provider, now=_NOW)

    assert result.outcome is Line1Outcome.ROUTED
    assert result.plan is not None
    assert result.plan.side.value == "BUY"
    assert result.plan.status.value == "VALIDATED"
    # Exactly the 4 mandatory-agent calls — one debate per shortlist, 0 network.
    assert router.calls == 4
    # The compliant BUY reached the decision group.
    assert len(sender.calls) == 1
    assert "【QuantMind 买入信号 · 合规】" in sender.calls[0]["content"]
    assert f"-{result.lead_code}-BUY-" in sender.calls[0]["content"]


@pytest.mark.asyncio
async def test_provider_runner_concentration_exception_etf(builder, tmp_path) -> None:
    # U-C4: a Small-tier (¥5k) buy of a whitelisted broad ETF whose 1 lot tops
    # the 15% cap threads the budget concentration flag through the provider →
    # assemble_plan → VALIDATE with the ETF concentration-exception template.
    sender = FakeFeishuSender()
    runner = _make_runner(builder=builder, sender=sender, tmp_path=tmp_path)
    router = _StubRouter(action="买入")
    provider = await _make_provider(
        frame=_etf_frame(), cash=5_000.0, router=router
    )
    result = await runner.run(frame=_etf_frame(), provider=provider, now=_NOW)

    assert result.outcome is Line1Outcome.ROUTED
    assert result.tier is not None and result.tier.value == "small"
    assert result.lead_code == "510300"
    assert result.plan is not None
    assert any(
        row.passed is True
        and "concentration_exception_granted" in (row.message or "")
        for row in result.plan.risk_summary
    )
    assert len(sender.calls) == 1
    content = sender.calls[0]["content"]
    assert "【QuantMind 买入信号 · ETF 集中度例外 · 需确认】" in content


@pytest.mark.asyncio
async def test_provider_runner_hold_not_routed(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(builder=builder, sender=sender, tmp_path=tmp_path)
    router = _StubRouter(action="持有")
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=router
    )
    result = await runner.run(frame=_stock_frame(), provider=provider, now=_NOW)
    assert result.outcome is Line1Outcome.HOLD
    assert sender.calls == []  # HOLD never routes (CLAUDE.md §2.7)
