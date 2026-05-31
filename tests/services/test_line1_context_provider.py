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
from backend.data.market_data import DataFetchError
from backend.data.stock_metadata import Board
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.market import StockOrderbook, StockQuote
from backend.orchestration.line1_runner import (
    CommittedBuy,
    Line1AllocationSkip,
    Line1Outcome,
    Line1QuoteDegrade,
    Line1Runner,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.portfolio_allocation import (
    INVERSE_VOLATILITY,
    AllocationPolicy,
    cash_to_lots,
    compute_target_cash,
)
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


# Live last==best_ask per code (≈ each lead's T-1 last close from the uptrend
# fixtures) so the U-E2 cage limit (≈ last×1.02) stays within both the cage and
# the prev_close band. The fake returns these as the dual-source spot + 卖一.
_LIVE_PRICES: dict[str, float] = {
    "600000": 12.9,   # _uptrend(10.0)[-1]
    "600004": 11.9,   # _uptrend(9.0)[-1]
    "600006": 10.9,   # _uptrend(8.0)[-1]
    "510300": 12.0,   # _uptrend(9.1)[-1]
    "600999": 20.0,   # absent-from-frame lead in the fallback test
}


class _FakeMarketData:
    """Stub live quote layer for the U-E2 cage path (0 real network).

    ``get_stock_realtime_dual`` returns BOTH spot legs (adata + akshare) and
    ``get_stock_orderbook`` returns a 卖一 = best_ask, all priced from
    ``_LIVE_PRICES``. The opt-in sets drive the degrade branches:
    ``single_source`` (no akshare leg), ``divergent`` (legs disagree >0.3%),
    ``missing_ask`` (no 卖一), ``unknown`` (no quote at all → DataFetchError),
    ``nan_backup`` (akshare leg returns a real quote with a NaN price).
    """

    def __init__(
        self,
        *,
        prices: dict[str, float] | None = None,
        single_source: frozenset[str] = frozenset(),
        divergent: frozenset[str] = frozenset(),
        missing_ask: frozenset[str] = frozenset(),
        unknown: frozenset[str] = frozenset(),
        nan_backup: frozenset[str] = frozenset(),
        now: datetime = _NOW,
    ) -> None:
        self._prices = dict(prices or _LIVE_PRICES)
        self._single_source = single_source
        self._divergent = divergent
        self._missing_ask = missing_ask
        self._unknown = unknown
        self._nan_backup = nan_backup
        self._now = now

    def _q(self, code: str, price: float) -> StockQuote:
        return StockQuote(
            code=code, name=code, price=price, open=price, high=price,
            low=price, prev_close=price, change_pct=0.0, volume=1.0,
            amount=1.0, turnover_rate=0.0, timestamp=self._now,
        )

    async def get_stock_realtime_dual(
        self, code: str
    ) -> tuple[StockQuote | None, StockQuote | None]:
        if code in self._unknown or code not in self._prices:
            return None, None
        price = self._prices[code]
        primary = self._q(code, price)
        if code in self._single_source:
            return primary, None
        if code in self._nan_backup:
            # A pandas NaN cell from a vendor brown-out: a real StockQuote whose
            # price is non-finite (must NOT pass as dual-source confirmation).
            return primary, self._q(code, float("nan"))
        fallback_price = price * 1.01 if code in self._divergent else price
        return primary, self._q(code, fallback_price)

    async def get_stock_orderbook(self, code: str) -> StockOrderbook:
        if code in self._unknown or code not in self._prices:
            raise DataFetchError(f"no orderbook for {code}")
        price = self._prices[code]
        return StockOrderbook(
            code=code,
            last=price,
            best_ask=None if code in self._missing_ask else price,
            best_bid=price * 0.999,
            source="adata",
            ts=self._now,
        )


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
    market_data: Any | None = None,
    allocation_policy: AllocationPolicy | None = None,
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
        run_state=run_state,
        frame=frame,
        llm_router=router,
        now=_NOW,
        # U-E2: inject the stub live-quote layer so the cage path engages; tests
        # that exercise degrade branches pass a configured _FakeMarketData.
        market_data=market_data if market_data is not None else _FakeMarketData(),
        allocation_policy=allocation_policy,
    )


def _alloc_policy(
    *,
    per_name_target_pct: float = 0.10,
    deploy_fraction: float = 0.33,
    cash_buffer_pct: float = 0.05,
) -> AllocationPolicy:
    """A directly-constructed P-003 allocation policy (caps mirror risk.yaml)."""
    return AllocationPolicy(
        method=INVERSE_VOLATILITY,
        deploy_fraction=deploy_fraction,
        per_name_target_pct=per_name_target_pct,
        cash_buffer_pct=cash_buffer_pct,
        vol_lookback=20,
        single_stock_cap_pct=0.15,
        single_instruction_cap=50_000.0,
        lot_size=100,
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
    lead_ctx = await provider.build_lead_context(lead)
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


def _lead_600000() -> CandidateRow:
    closes = _uptrend(10.0)
    return CandidateRow(
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


@pytest.mark.asyncio
async def test_build_lead_context_committed_threads_cash_and_positions() -> None:
    # P1-7-amendment-2026-05-26: a basket BUY already routed this run is folded
    # into the next candidate's account (cash ↓ by notional, market_value ↑,
    # total_assets preserved) + positions, so the basket stays collectively
    # cash- + 70%-compliant. RiskEngine then re-validates against this state.
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    committed = (CommittedBuy(code="600004", volume=2_000, limit_price=10.0),)  # ¥20k
    ctx = await provider.build_lead_context(_lead_600000(), committed=committed)
    acct = ctx.team_context.account
    # Cash is debited by the check-4 cost (notional × 1.001 fee buffer) so a
    # later candidate is not handed overstated cash (codex P2).
    assert acct.available_cash == pytest.approx(98_000.0 - 20_000.0 * 1.001)
    assert acct.market_value == pytest.approx(20_000.0)  # shares valued at notional
    # total_assets drops by the cumulative fees (a real draw-down).
    assert acct.total_assets == pytest.approx(98_000.0 - 20_000.0 * 0.001)
    held = {p.code: p for p in ctx.team_context.positions}
    assert "600004" in held
    assert held["600004"].volume == 2_000
    assert held["600004"].market_value == pytest.approx(20_000.0)
    # The committed BUY is counted toward the ≤5-orders/day cap (codex P1).
    assert ctx.team_context.daily_state.today_new_instruction_count == 1
    # Empty committed = the U-D1b path (unchanged account / positions / count).
    base = await provider.build_lead_context(_lead_600000())
    assert base.team_context.account.available_cash == pytest.approx(98_000.0)
    assert base.team_context.positions == ()
    assert base.team_context.daily_state.today_new_instruction_count == 0


@pytest.mark.asyncio
async def test_build_lead_context_committed_shrinks_volume_toward_total_cap() -> None:
    # Committing ~61% of assets to another name squeezes the 70% total-position
    # headroom, so the next candidate is sized SMALLER than the uncommitted
    # baseline (RiskEngine check 8 binds, not the 15% single-stock cap) — the
    # basket cannot collectively breach the 70% cap.
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    baseline = (
        await provider.build_lead_context(_lead_600000())
    ).brief.proposed_volume
    committed = (CommittedBuy(code="600004", volume=6_000, limit_price=10.0),)  # ¥60k
    shrunk = (
        await provider.build_lead_context(_lead_600000(), committed=committed)
    ).brief.proposed_volume
    assert shrunk < baseline
    assert shrunk % 100 == 0 and shrunk >= 100  # still a whole lot ≥ 1 lot


@pytest.mark.asyncio
async def test_prime_allocation_clamps_volume_to_inverse_vol_target() -> None:
    # P-003 (P0-7-amendment-2026-05-30): after prime_allocation computes each
    # name's inverse-volatility cash target, build_lead_context clamps
    # volume = min(max_compliant, cash_to_lots(target, limit)). The target
    # (per-name 10% of ¥98k = ¥9,800) is tighter than the 15% max_compliant, so
    # the primed volume is SMALLER than the un-primed baseline.
    lead = _lead_600000()
    base_provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    baseline = (await base_provider.build_lead_context(lead)).brief.proposed_volume

    provider = await _make_provider(
        frame=_stock_frame(),
        cash=98_000.0,
        router=_StubRouter(),
        allocation_policy=_alloc_policy(),
    )
    provider.prime_allocation([lead])
    ctx = await provider.build_lead_context(lead)
    primed = ctx.brief.proposed_volume

    # Independently recompute the expected target lots off the carried cage limit.
    target_cash = compute_target_cash(
        {"600000": 1.0},
        deployable=min(98_000.0 * 0.33, 98_000.0 - 0.05 * 98_000.0),
        total_assets=98_000.0,
        existing_value_by_code={},
        per_name_target_pct=0.10,
        single_stock_cap_pct=0.15,
        single_instruction_cap=50_000.0,
    )
    expected = cash_to_lots(
        target_cash["600000"], ctx.brief.proposed_limit_price, lot=100
    )
    assert primed == expected
    assert 0 < primed < baseline  # allocation only tightened
    assert primed % 100 == 0
    # The AssemblyContext (single construction point) carries the SAME clamped
    # volume — allocation never re-relaxes the number downstream.
    asm = ctx.make_assembly_context(
        signal_id="SIG-a", seq=1, debate_round_count=1,
        analysis_record_id="ar", risk_validation_id="rv",
    )
    assert asm.proposed_volume == primed


@pytest.mark.asyncio
async def test_prime_allocation_zero_lot_target_degrades_not_one_lot() -> None:
    # A per-name target too small to afford even one lot (0.5% of ¥98k = ¥490 <
    # one lot ≈ ¥1,316) means allocation says "do not buy this name today" →
    # the provider degrades (skip), NEVER coerces to 1 lot (that would breach the
    # tranche envelope + Pydantic volume > 0).
    lead = _lead_600000()
    provider = await _make_provider(
        frame=_stock_frame(),
        cash=98_000.0,
        router=_StubRouter(),
        allocation_policy=_alloc_policy(per_name_target_pct=0.005),
    )
    provider.prime_allocation([lead])
    out = await provider.build_lead_context(lead)
    # A distinct skip type — NOT Line1QuoteDegrade — so the runner classifies
    # ALLOCATION_SKIPPED (not QUOTE_DEGRADED) and emits no quote notice.
    assert isinstance(out, Line1AllocationSkip)
    assert not isinstance(out, Line1QuoteDegrade)
    assert "allocation target 0 lots" in out.reason


@pytest.mark.asyncio
async def test_no_allocation_policy_leaves_volume_at_max_compliant() -> None:
    # Back-compat: with no allocation policy injected, prime_allocation is a
    # no-op and sizing stays at the existing max_compliant (un-primed) volume.
    lead = _lead_600000()
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    provider.prime_allocation([lead])  # no-op (no policy)
    primed = (await provider.build_lead_context(lead)).brief.proposed_volume
    base_provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    baseline = (await base_provider.build_lead_context(lead)).brief.proposed_volume
    assert primed == baseline


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
    lead_ctx = await provider.build_lead_context(lead)
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
    lead_ctx = await provider.build_lead_context(lead)
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
    # BASKET (P1-7-amendment-2026-05-26): every shortlist candidate is debated
    # (4 agent calls each) and the VALIDATED ones routed — 0 real network.
    assert len(result.routed_buys) >= 1
    assert router.calls == 4 * len(result.shortlist)
    assert all(
        rb.plan.side.value == "BUY" and rb.plan.status.value == "VALIDATED"
        for rb in result.routed_buys
    )
    # The cash-threading invariant: the routed basket stays collectively within
    # the 70% total-position cap (total_assets = ¥98k, no opening positions).
    basket_notional = sum(
        (rb.plan.volume or 0) * (rb.plan.limit_price or 0.0)
        for rb in result.routed_buys
    )
    assert basket_notional <= 0.70 * 98_000.0
    # Each compliant BUY reached the decision group with a distinct id.
    assert len(sender.calls) == len(result.routed_buys)
    assert all("【QuantMind 买入信号 · 合规】" in c["content"] for c in sender.calls)
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


# ---------------------------------------------------------------------------
# U-E2 / 缺口4 — live cage quote derivation + degrade paths + PIT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cage_limit_threaded_into_assembly_and_brief() -> None:
    # The live cage limit (≈ best_ask×1.02, floored) drives BOTH the brief
    # proposed_limit_price AND the AssemblyContext.live_quote (best_ask the
    # 14-check re-verifies). last == best_ask == 12.9 → cage ceiling
    # max(13.158, 13.0)=13.158 → floor 13.15.
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter()
    )
    lead_ctx = await provider.build_lead_context(_lead_600000(), signal_id="SIG-x")
    assert lead_ctx.brief.proposed_limit_price == 13.15
    ctx = lead_ctx.make_assembly_context(
        signal_id="SIG-x", seq=1, debate_round_count=1,
        analysis_record_id="ar", risk_validation_id="rv",
    )
    assert ctx.proposed_limit_price == 13.15
    assert ctx.live_quote is not None
    assert ctx.live_quote.best_ask == 12.9
    assert ctx.live_quote.source == "adata"


@pytest.mark.asyncio
async def test_degrade_when_no_market_data_layer() -> None:
    # No live layer → the provider NEVER prices a BUY on the T-1 close.
    run_state = await build_line1_run_state(
        broker=_FakeBroker(cash=98_000.0),
        risk_engine=RiskEngine(_risk_config()),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        risk_config=_risk_config(),
        now=_NOW,
    )
    provider = Line1ContextProvider(
        run_state=run_state, frame=_stock_frame(), llm_router=_StubRouter(),
        now=_NOW, market_data=None,
    )
    out = await provider.build_lead_context(_lead_600000())
    assert isinstance(out, Line1QuoteDegrade)
    assert "no live market-data" in out.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"single_source": frozenset({"600000"})}, "single-source"),
        ({"divergent": frozenset({"600000"})}, "divergence"),
        ({"missing_ask": frozenset({"600000"})}, "卖一"),
        ({"unknown": frozenset({"600000"})}, "primary spot leg"),
        # codex U-E2 P1: a NaN backup price must NOT pass as dual-source — a
        # malformed leg makes divergence unprovable, so degrade single-source.
        ({"nan_backup": frozenset({"600000"})}, "untrusted spot"),
    ],
)
async def test_degrade_paths(kwargs: dict, needle: str) -> None:
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter(),
        market_data=_FakeMarketData(**kwargs),
    )
    out = await provider.build_lead_context(_lead_600000())
    assert isinstance(out, Line1QuoteDegrade)
    assert needle in out.reason
    assert out.code == "600000"


@pytest.mark.asyncio
async def test_stale_spot_degrades() -> None:
    # A spot fetch older than 5s vs the run ``now`` degrades (no T-1 fallback).
    stale = _FakeMarketData(
        now=_NOW.replace(minute=_NOW.minute - 1)  # 60s before the run now
    )
    provider = await _make_provider(
        frame=_stock_frame(), cash=98_000.0, router=_StubRouter(),
        market_data=stale,
    )
    out = await provider.build_lead_context(_lead_600000())
    assert isinstance(out, Line1QuoteDegrade)
    assert "stale" in out.reason


@pytest.mark.asyncio
async def test_tz_naive_now_degrades_not_crashes() -> None:
    # A tz-naive run ``now`` vs the tz-aware spot timestamp would crash the
    # subtraction; the provider fails closed for the lead instead of taking
    # down the whole basket walk.
    naive_now = datetime(2026, 5, 15, 10, 30, 0)  # no tzinfo
    run_state = await build_line1_run_state(
        broker=_FakeBroker(cash=98_000.0),
        risk_engine=RiskEngine(_risk_config()),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        risk_config=_risk_config(),
        now=naive_now,
    )
    aware = datetime(2026, 5, 15, 10, 30, tzinfo=UTC)
    provider = Line1ContextProvider(
        run_state=run_state, frame=_stock_frame(), llm_router=_StubRouter(),
        now=naive_now, market_data=_FakeMarketData(now=aware),
    )
    out = await provider.build_lead_context(_lead_600000())
    assert isinstance(out, Line1QuoteDegrade)
    assert "tz mismatch" in out.reason


@pytest.mark.asyncio
async def test_pit_persists_live_cage_inputs(tmp_path: Path) -> None:
    from backend.marketdata_snapshot import SnapshotStore

    store = SnapshotStore(root=tmp_path / "pit")
    run_state = await build_line1_run_state(
        broker=_FakeBroker(cash=98_000.0),
        risk_engine=RiskEngine(_risk_config()),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        risk_config=_risk_config(),
        now=_NOW,
    )
    provider = Line1ContextProvider(
        run_state=run_state, frame=_stock_frame(), llm_router=_StubRouter(),
        now=_NOW, market_data=_FakeMarketData(), snapshot_store=store,
    )
    out = await provider.build_lead_context(_lead_600000(), signal_id="SIG-pit")
    assert not isinstance(out, Line1QuoteDegrade)
    snap = store.latest(
        vendor="line1_live_cage", endpoint="spot_orderbook:600000",
        trade_date=_NOW.astimezone(UTC).strftime("%Y%m%d"),
    )
    assert snap is not None
    assert snap.metadata["signal_id"] == "SIG-pit"
    assert b"600000" in snap.raw_payload  # raw bytes stored, not hash-only
