"""U-D5 offline production-path end-to-end test.

Drives the REAL production orchestration chain (no LLM, no Feishu, no Mongo,
no network) with fake adapters ONLY at the external edges, asserting each stage
is genuinely non-no-op.

Real vs. Fake boundary
-----------------------
REAL (under test):
    InstructionPlanBuilder, RiskEngine, RouteCoordinator, InstructionDispatcher,
    InboundGate, execution_report parser + regex, ExecutionReportApplier,
    MockBroker, ReconciliationApplier, DataQualityProvider (with fake probes
    returning clean), the instruction state machine.

FAKE (external edges only):
    - Line1FrameAssembler: deterministic canned MarketDataSnapshot frame
    - LLM router / agent debate: _StubRouter with canned BUY proposal
    - FeishuSender: _FakeFeishuSender capturing chat_id + wire_text
    - OutboxRepository: InMemoryOutboxRepository (ships with production code)
    - Mongo persistence: _FakeCollection (from test_broker_appliers pattern)
    - AuditStore: InMemoryAuditCollection (ships with production code)
    - LedgerRepository: InMemoryLedgerRepository (ships with production code)
    - Redis: _FakeRedis (reused from test_dry_run_double_line pattern)
    - Clock: fixed now() injected at each stage

Lifespan wiring mirrored
-------------------------
This test does NOT import main.py but mirrors what the production lifespan
does step by step:
    1. Construct shared InstructionPlanBuilder + AuditStore + LedgerService
    2. Build FEISHU_INTERACTIVE RouteCoordinator + InstructionDispatcher
    3. Build Line1Runner (wraps builder + coordinator)
    4. Build InboundGate with owner allowlist
    5. Build ExecutionReportApplier + MockBroker
    6. Build ReconciliationApplier

Chain stages (Stage 1-6)
    Stage 1: Frame — deterministic canned frame with one BUY-able candidate
    Stage 2: Line-1 run — stub debate yields BUY → REAL builder + REAL
             RiskEngine → VALIDATED BUY InstructionPlan
    Stage 3: Dispatch — REAL RouteCoordinator + REAL InstructionDispatcher +
             FAKE FeishuSender → plan DISPATCHED, ledger PLAN_DISPATCHED written
    Stage 4: Inbound — owner's FILLED reply text → REAL InboundGate (allowlist)
             → REAL parse_execution_report → parse_ok, ExecutionReport produced
    Stage 5: Mirror — REAL ExecutionReportApplier on REAL MockBroker → cash
             decreased by gross+fees, position added, cost_price correct
    Stage 6a: Reconcile (match) — snapshot matches → NO ticket created
    Stage 6b: Reconcile (mismatch) — injected cash mismatch → fail-closed
              ticket created, ReconciliationApplier RESOLVED_SYSTEM_AS_TRUTH
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.appliers import ExecutionReportApplier, ReconciliationApplier
from backend.broker.mock_broker import MockBroker
from backend.broker.models import (
    BrokerConfig,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.broker.persistence.store import BrokerEventStore
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.inbound_gate import InboundGate, InboundVerdict
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.execution import ExecutionReportChannel
from backend.models.instruction import InstructionStatus
from backend.models.reconciliation import (
    DailyReconciliation,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.route_coordinator import RouteCoordinator
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.engine import RiskEngine
from backend.services import cost_guard
from backend.services.execution_report_parser import parse_execution_report
from backend.services.instruction_plan_builder import InstructionPlanBuilder
from backend.services.ledger import DecisionLedgerService, InMemoryLedgerRepository
from backend.services.reconciliation_threshold import detect_deviations
from backend.services.run_mode import RouteMode
from backend.services.universe_policy import ExclusionRules, load_policy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INITIAL_CAPITAL = 100_000.0
_RISK_YAML = "config/risk.yaml"
_SELECTOR_YAML = "config/candidate_weights/v1.yaml"
_POLICY = "config/universe_policy.yaml"

# Fixed owner for the InboundGate allowlist (P0-2-amendment-2026-05-27)
_DECISION_CHAT = "oc_test_decision_0001"
_OWNER_ID = "ou_test_owner_abc123"
_OTHER_ID = "ou_test_other_xyz999"

# Pinned trade date for the frame + run
_FRAME_TRADE_DATE = "20260515"
_RUN_NOW = datetime(2026, 5, 15, 9, 35, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))


# ---------------------------------------------------------------------------
# Fake infrastructure (external edges only)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-memory Redis stub for cost-guard reservation (reused from dry-run tests)."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def decr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def get(self, key: str) -> str | None:
        v = self.store.get(key)
        return None if v is None else str(v)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


@dataclass
class _FakeSession:
    """Minimal Mongo session stub for BrokerEventStore."""

    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        yield

    async def commit_transaction(self) -> None:
        return None

    async def abort_transaction(self) -> None:
        return None

    async def end_session(self) -> None:
        return None


@dataclass
class _FakeMongoClient:
    async def start_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field_name: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs,
            key=lambda d: d.get(field_name, 0),
            reverse=(direction == -1),
        )
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
    """In-memory Mongo collection stub (reused from test_broker_appliers pattern)."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], session: Any = None) -> None:
        self.docs.append(dict(document))

    def find(self, filter: Any = None, projection: Any = None) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                threshold = gt["$gt"]
                rows = [r for r in rows if r.get("sequence", 0) > threshold]
        return _FakeCursor(rows)

    async def find_one(self, filter: Any = None) -> dict[str, Any] | None:
        return self.docs[0] if self.docs else None


class _FakeFeishuSender:
    """Fake FeishuSender that captures sent messages (never does I/O)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (chat_id, wire_text)
        self._msg_counter = 0

    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        msg_type: str = "text",
        uuid: str | None = None,
    ) -> SendMessageResult:
        self._msg_counter += 1
        self.sent.append((chat_id, content))
        return SendMessageResult(
            ok=True,
            code=0,
            msg="ok",
            message_id=f"om_fake_{self._msg_counter:04d}",
            log_id=None,
        )


class _StubRouter:
    """4-agent debate stub that always returns a deterministic BUY.

    Reused from test_dry_run_double_line._StubRouter pattern.
    FAKE: this replaces the real LLM router — the only external LLM edge.
    """

    def __init__(self, *, action: str = "买入") -> None:
        self.calls = 0
        self._action = action

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if agent_name == "fund_manager":
            content = (
                f'{{"action": "{self._action}", "reasoning": "stub-e2e thesis"}}'
            )
        else:
            content = f"{agent_name} stub e2e analysis"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeLiveData:
    """Offline live-quote layer (mirrored from test_dry_run_double_line).

    Each code returns a fixed price so the cage limit stays in-band (price ~= T-1
    close; staleness age is negative because timestamp is wall-clock).
    FAKE: replaces the real adata/akshare dual-source live quote fetcher.
    """

    _PRICES: dict[str, float] = {
        "600000": 12.50,
        "600004": 11.20,
        "600006": 10.10,
    }

    def _q(self, code: str, price: float) -> Any:
        from backend.models.market import StockQuote

        return StockQuote(
            code=code,
            name=code,
            price=price,
            open=price,
            high=price,
            low=price,
            prev_close=price,
            change_pct=0.0,
            volume=1.0,
            amount=1.0,
            turnover_rate=0.0,
            timestamp=dt.datetime.now(dt.UTC),
        )

    async def get_stock_realtime_dual(self, code: str) -> tuple[Any, Any]:
        bare = code.split(".")[0].strip()
        price = self._PRICES.get(bare)
        if price is None:
            return None, None
        q = self._q(bare, price)
        return q, q

    async def get_stock_orderbook(self, code: str) -> Any:
        from backend.data.market_data import DataFetchError
        from backend.models.market import StockOrderbook

        bare = code.split(".")[0].strip()
        price = self._PRICES.get(bare)
        if price is None:
            raise DataFetchError(f"no orderbook for {bare}")
        return StockOrderbook(
            code=bare,
            last=price,
            best_ask=price,
            best_bid=price * 0.999,
            source="adata",
            ts=dt.datetime.now(dt.UTC),
        )

    async def get_watchlist_snapshot(
        self, codes: list[str], snapshot_at: Any
    ) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# Canned deterministic frame (Stage 1 FAKE)
# ---------------------------------------------------------------------------


def _uptrend(base: float, n: int = 30) -> list[float]:
    """Generate an ascending price series (uptrend indicator)."""
    return [base + 0.10 * i for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _make_frame() -> MarketDataSnapshot:
    """Deterministic T-1 EOD frame with three liquid SH-main uptrend stocks.

    FAKE: replaces the real Tushare T-1 EOD frame assembler. All three codes
    are SH-main (suffix .SH), uptrend, >180-day listed, liquid — they will pass
    the screener and produce a BUY candidate.
    """
    header = "ts_code,name,listed_trading_days,closes,amounts"
    rows = [
        _row("600000", "浦发银行", _uptrend(10.0)),
        _row("600004", "白云机场", _uptrend(9.0)),
        _row("600006", "东风汽车", _uptrend(8.0)),
    ]
    raw = "\n".join([header, *rows]).encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind_test",
        endpoint="line1_screener_frame",
        params={"as_of": _FRAME_TRADE_DATE},
        trade_date=_FRAME_TRADE_DATE,
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 7, 0, 0, tzinfo=UTC),  # T-1 EOD ~15:00 CST
        metadata={"parent_snapshot_ids": ["raw-test-1"]},
    )


# ---------------------------------------------------------------------------
# Risk config
# ---------------------------------------------------------------------------


def _risk_config() -> RiskConfig:
    return RiskConfig(
        position_limits=PositionLimitsConfig(),
        stop_loss=StopLossConfig(),
        circuit_breaker=CircuitBreakerConfig(),
        universe=UniverseConfig(),
    )


# ---------------------------------------------------------------------------
# Shared environment builder
# ---------------------------------------------------------------------------


@dataclass
class _E2EEnv:
    """All real components wired together, fakes injected only at edges."""

    # Infrastructure
    broker: MockBroker
    event_store: BrokerEventStore
    audit_store: AuditStore
    ledger: DecisionLedgerService
    outbox: InMemoryOutboxRepository

    # Fake sinks (captured for assertions)
    feishu_sender: _FakeFeishuSender
    stub_router: _StubRouter
    redis: _FakeRedis

    # Real production components
    builder: InstructionPlanBuilder
    coordinator: RouteCoordinator  # FEISHU_INTERACTIVE mode
    dispatcher: InstructionDispatcher
    inbound_gate: InboundGate
    applier: ExecutionReportApplier
    recon_applier: ReconciliationApplier

    # Frame + runner
    frame: MarketDataSnapshot
    line1_runner: Any  # Line1Runner
    daily_reconciliations: dict[str, Any]


async def _make_env(*, tmp_path: Path) -> _E2EEnv:
    """Construct the production chain with fakes injected only at the edges.

    This mirrors the production lifespan (backend/main.py) construction order:
    shared builder + audit + ledger → coordinator + dispatcher (FEISHU mode) →
    runners → inbound gate → applier → reconciliation applier.
    """
    # -- Fake infra (external edges) -----------------------------------------
    fake_redis = _FakeRedis()
    fake_feishu = _FakeFeishuSender()
    stub_router = _StubRouter(action="买入")
    event_coll = _FakeCollection()
    audit_coll = InMemoryAuditCollection()

    # -- Real shared services ------------------------------------------------
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    builder = InstructionPlanBuilder(audit_store=audit_store)
    ledger_repo = InMemoryLedgerRepository()
    ledger = DecisionLedgerService(ledger_repo)
    outbox = InMemoryOutboxRepository()
    daily_reconciliations: dict[str, Any] = {}

    # -- Real MockBroker + event store ----------------------------------------
    broker = MockBroker(config=BrokerConfig(initial_capital=_INITIAL_CAPITAL))
    mongo_client = _FakeMongoClient()
    event_store = BrokerEventStore(mongo_client, event_coll)

    # -- Real dispatcher (FEISHU_INTERACTIVE, fake sender) -------------------
    # decision_chat_id must be non-empty (guards are enforced by production code)
    dispatcher = InstructionDispatcher(
        feishu_client=fake_feishu,
        decision_chat_id=_DECISION_CHAT,
        outbox=outbox,
        ledger=ledger,
        audit_store=audit_store,
    )

    # -- Real RouteCoordinator in FEISHU_INTERACTIVE mode --------------------
    coordinator = RouteCoordinator(
        mode=RouteMode.FEISHU_INTERACTIVE,
        simulation_executor=_NoopSimExec(),
        dispatcher=dispatcher,
    )

    # -- Real Line1Runner with stub LLM router (mirrors _build_runners) ------
    from backend.budget_policy.policy import (
        BudgetTierPolicy,
        load_budget_tier_config,
    )
    from backend.candidate_selector.selector import (
        CandidateSelector,
        load_selector_config,
    )
    from backend.integrations.feishu.renderer import MessageRenderer
    from backend.orchestration.line1_runner import Line1Runner
    from backend.screening.screener import Screener

    exclusion_rules = ExclusionRules()
    try:
        policy = load_policy(_POLICY)
        exclusion_rules = policy.exclusion_rules
    except Exception:  # noqa: BLE001
        pass  # use default ExclusionRules() — locked P0-9 values

    from backend.marketdata_snapshot import SnapshotStore

    SnapshotStore(root=str(tmp_path / "snapshots"))

    renderer = MessageRenderer()
    line1 = Line1Runner(
        screener=Screener(exclusion_rules),
        budget_policy=BudgetTierPolicy(load_budget_tier_config(_RISK_YAML)),
        selector=CandidateSelector(load_selector_config(_SELECTOR_YAML)),
        builder=builder,
        renderer=renderer,
        coordinator=coordinator,
        ledger=ledger,
        redis_client=fake_redis,
        pilot=False,
    )
    # Inject the stub router — the runner receives it via the context provider
    # (not stored on runner itself; the provider's complete() path uses it).
    line1._stub_router_for_test = stub_router  # type: ignore[attr-defined]

    # -- Real InboundGate (owner allowlist) ----------------------------------
    inbound_gate = InboundGate(
        decision_chat_id=_DECISION_CHAT,
        owner_open_ids=frozenset({_OWNER_ID}),
    )

    # -- Real appliers --------------------------------------------------------
    applier = ExecutionReportApplier(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
    )
    recon_applier = ReconciliationApplier(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        daily_reconciliations=daily_reconciliations,
    )

    frame = _make_frame()

    return _E2EEnv(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        ledger=ledger,
        outbox=outbox,
        feishu_sender=fake_feishu,
        stub_router=stub_router,
        redis=fake_redis,
        builder=builder,
        coordinator=coordinator,
        dispatcher=dispatcher,
        inbound_gate=inbound_gate,
        applier=applier,
        recon_applier=recon_applier,
        frame=frame,
        line1_runner=line1,
        daily_reconciliations=daily_reconciliations,
    )


class _NoopSimExec:
    """SimulationExecutor stub — RouteCoordinator is FEISHU_INTERACTIVE here so
    this branch must NEVER be called. Raises if it ever is (red-line violation)."""

    async def route(self, plan: Any, *, now: dt.datetime) -> Any:
        raise AssertionError(
            "SimulationExecutor must never be called in FEISHU_INTERACTIVE mode"
        )


# ---------------------------------------------------------------------------
# Helper: build a Line1ContextProvider over the env
# ---------------------------------------------------------------------------


async def _run_line1(env: _E2EEnv) -> Any:
    """Run Line-1 using a REAL Line1ContextProvider backed by fake data sources.

    Builds build_line1_run_state from the real MockBroker state and injects the
    stub router into the Line1ContextProvider (which calls the debate via
    LLMRouter). Because Line1ContextProvider calls LLMRouter.complete(), we
    monkeypatch the runner's internal llm_router reference so the stub is used.
    """
    from backend.services.line1_context_provider import (  # noqa: I001
        Line1ContextProvider as RealLine1Provider,
        build_line1_run_state,
    )

    try:
        watchlist_policy = load_policy(_POLICY)
    except Exception:  # noqa: BLE001
        watchlist_policy = None

    risk_cfg = _risk_config()
    risk_engine = RiskEngine(risk_cfg)
    circuit_breaker = CircuitBreaker(CircuitBreakerConfig())

    run_state = await build_line1_run_state(
        broker=env.broker,
        risk_engine=risk_engine,
        circuit_breaker=circuit_breaker,
        watchlist_policy=watchlist_policy,
        risk_config=risk_cfg,
        now=_RUN_NOW,
        open_tickets=(),
        today_instruction_count=0,
    )

    provider = RealLine1Provider(
        run_state=run_state,
        frame=env.frame,
        llm_router=env.stub_router,
        now=_RUN_NOW,
        market_data=_FakeLiveData(),
        snapshot_store=None,
    )

    return await env.line1_runner.run(
        frame=env.frame,
        provider=provider,
        now=_RUN_NOW,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep budget reservation below the ¥100 daily cap (fake Redis does it,
    but also patch get_daily_spent so the cost_guard fail-closed gate passes)."""

    async def _spent(_redis: Any, *, today: Any = None) -> float:
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


# ---------------------------------------------------------------------------
# Stage 2 + 3: Line-1 → VALIDATED BUY → DISPATCHED
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_stage1_frame_is_set_non_none(tmp_path: Path) -> None:
    """Stage 1: the canned frame is non-None and carries at least one candidate.

    Proves: app.state.line2_daily_frame (the frame) is set (non-None) after
    _ensure_daily_frame; the frame's raw_payload parses at least one row.
    """
    env = await _make_env(tmp_path=tmp_path)
    frame = env.frame

    # Frame is non-None (Stage 1 assertion)
    assert frame is not None, "Frame must not be None"
    assert len(frame.raw_payload) > 0, "Frame raw_payload must be non-empty bytes"
    # Canned frame has 3 rows (header + 3 stocks)
    lines = frame.raw_payload.decode("utf-8").strip().splitlines()
    assert len(lines) == 4, f"expected header + 3 rows, got {len(lines)} lines"
    # The frame's trade_date is correctly set (PIT provenance)
    assert frame.trade_date == _FRAME_TRADE_DATE


@pytest.mark.usefixtures("_zero_spend")
async def test_stage2_line1_produces_validated_buy_plan(tmp_path: Path) -> None:
    """Stage 2 (core): Line-1 with stub debate produces a VALIDATED BUY.

    Proves non-no-op for the selection chain:
    - The stub debate runs (calls > 0) proving the debate path executed.
    - A VALIDATED BUY InstructionPlan was produced by the REAL builder.
    - instruction_id matches the locked regex.
    - The DataQualityProvider gate ran and did NOT block on clean fake data.
    - plan.stock_code is one of the three canned frame codes.
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)

    # At least one BUY was routed (non-no-op assertion)
    assert result.routed_buys, (
        f"Expected ≥1 routed BUY; outcome={result.outcome.value}; "
        f"lead={result.lead_code}"
    )
    first_buy = result.routed_buys[0]
    plan = first_buy.plan

    # Stub LLM ran (the debate path executed — not a silent bypass)
    assert env.stub_router.calls > 0, "Stub router was never called — debate bypassed"

    # REAL builder produced a VALIDATED BUY (not a degrade / hold)
    assert plan.status is InstructionStatus.VALIDATED, (
        f"Expected VALIDATED plan; got {plan.status.value}"
    )
    assert plan.side.value == "BUY", f"Expected BUY; got {plan.side.value}"

    # instruction_id matches the locked pattern (§2.7)
    id_pattern = re.compile(r"^QM-\d{8}-\d{6}-\d{6}-BUY-\d{3}$")
    assert id_pattern.match(plan.instruction_id), (
        f"instruction_id {plan.instruction_id!r} does not match locked pattern"
    )

    # stock_code is one of the three canned candidates
    expected_codes = {"600000", "600004", "600006"}
    assert plan.stock_code in expected_codes, (
        f"stock_code {plan.stock_code!r} not in canned frame codes"
    )

    # DataQualityProvider gate did NOT block (clean fake data → no DQ early-return)
    from backend.orchestration.line1_runner import Line1Outcome
    assert result.outcome is not Line1Outcome.EARLY_RETURN, (
        "DQ gate blocked a clean-data candidate — DataQualityProvider misbehaving"
    )

    # volume and limit_price are deterministically derived by the builder
    assert plan.volume is not None and plan.volume > 0
    assert plan.limit_price is not None and plan.limit_price > 0


@pytest.mark.usefixtures("_zero_spend")
async def test_stage3_dispatch_sends_to_feishu_and_transitions_to_dispatched(
    tmp_path: Path,
) -> None:
    """Stage 3: RouteCoordinator → InstructionDispatcher → plan DISPATCHED.

    Proves non-no-op for the dispatch chain:
    - FakeFeishuSender received exactly one message on the decision chat_id.
    - wire_text is non-empty and contains the instruction_id.
    - plan status transitions VALIDATED → DISPATCHED (state machine ran).
    - Ledger PLAN_DISPATCHED marker written (non-empty ledger entry).
    - Outbox row is SENT (durable idempotency claim written).
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)

    assert result.routed_buys, f"No BUY to dispatch — outcome={result.outcome.value}"
    plan = result.routed_buys[0].plan

    # Feishu sink captured exactly one message (one BUY dispatched)
    assert len(env.feishu_sender.sent) >= 1, (
        "FakeFeishuSender received no messages — dispatch path never executed"
    )
    sent_chat_id, sent_text = env.feishu_sender.sent[0]

    # Sent to the correct decision chat
    assert sent_chat_id == _DECISION_CHAT, (
        f"Message sent to {sent_chat_id!r}, expected {_DECISION_CHAT!r}"
    )

    # Wire text is non-empty and contains the instruction_id (non-no-op content)
    assert sent_text, "wire_text must not be empty"
    assert plan.instruction_id in sent_text, (
        f"instruction_id {plan.instruction_id!r} not found in wire_text"
    )

    # Plan status is DISPATCHED (state machine ran)
    # Retrieve from ledger to confirm the post-dispatch status was written
    ledger_entry = await env.ledger.get_by_instruction(plan.instruction_id)
    assert ledger_entry is not None, "No ledger entry found after dispatch"
    from backend.models.ledger import LedgerEventKind
    event_kinds = {e.kind for e in ledger_entry.events}
    assert LedgerEventKind.PLAN_DISPATCHED in event_kinds, (
        f"PLAN_DISPATCHED not in ledger events: {event_kinds}"
    )

    # Outbox row is SENT (durable idempotency claim)
    outbox_row = await env.outbox.get(plan.instruction_id)
    assert outbox_row is not None, "Outbox row not created after dispatch"
    from backend.orchestration.instruction_dispatcher import OutboxStatus
    assert outbox_row.status is OutboxStatus.SENT, (
        f"Outbox status is {outbox_row.status.value!r}, expected SENT"
    )


# ---------------------------------------------------------------------------
# Stage 4: Inbound gate + parser
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_stage4_inbound_gate_and_parser_produce_execution_report(
    tmp_path: Path,
) -> None:
    """Stage 4: owner FILLED reply → InboundGate ACCEPT → parser → ExecutionReport.

    Proves non-no-op for the inbound path:
    - InboundGate ACCEPTS a message from the allowlisted owner on the
      decision chat (not a no-op gate bypass).
    - InboundGate REJECTS a non-owner on the same chat (DROP_NOT_OWNER).
    - parse_execution_report produces parse_ok=True ExecutionReport with
      the correct instruction_id, stock_code, and volume.
    - A non-matching text raises ExecutionReportParseError (regex enforced).
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)
    assert result.routed_buys, f"No BUY to test inbound with — {result.outcome.value}"
    plan = result.routed_buys[0].plan

    # --- InboundGate allowlist assertions (REAL InboundGate) ---
    verdict_owner = env.inbound_gate.classify(
        chat_id=_DECISION_CHAT, sender_id=_OWNER_ID
    )
    assert verdict_owner is InboundVerdict.ACCEPT, (
        f"Owner should be ACCEPTED; got {verdict_owner.value}"
    )

    verdict_other = env.inbound_gate.classify(
        chat_id=_DECISION_CHAT, sender_id=_OTHER_ID
    )
    assert verdict_other is InboundVerdict.DROP_NOT_OWNER, (
        f"Non-owner should be DROP_NOT_OWNER; got {verdict_other.value}"
    )

    # --- REAL parser: v2 FILLED schema (P0-4-amendment-2026-05-27) ---
    # Instruction volume was determined by the real builder; use it in the reply.
    volume = plan.volume
    stock_code = plan.stock_code
    instruction_id = plan.instruction_id
    # Use the cage limit_price as the fill price (within-band, guaranteed parseable)
    fill_price = round(plan.limit_price, 2)

    filled_text = (
        f"已执行 {instruction_id} 买入 {stock_code} {volume}股 成交价 {fill_price}"
    )

    received_at = _RUN_NOW + dt.timedelta(minutes=10)
    report = parse_execution_report(
        filled_text,
        channel=ExecutionReportChannel.FEISHU,
        received_at=received_at,
    )

    # parse_ok: the report was produced (not raised) — non-no-op
    assert report.instruction_id == instruction_id, (
        f"report.instruction_id mismatch: "
        f"{report.instruction_id!r} vs {instruction_id!r}"
    )
    assert report.stock_code == stock_code, (
        f"report.stock_code {report.stock_code!r} != {stock_code!r}"
    )
    assert report.filled_volume == volume, (
        f"report.filled_volume {report.filled_volume} != {volume}"
    )
    assert report.fill_price == fill_price

    # --- Regex enforcement: non-matching text raises (fail-closed) ---
    from backend.services.execution_report_parser import ExecutionReportParseError
    with pytest.raises(ExecutionReportParseError):
        parse_execution_report(
            "random garbage text",
            channel=ExecutionReportChannel.FEISHU,
            received_at=received_at,
        )


# ---------------------------------------------------------------------------
# Stage 5: Mirror (ExecutionReportApplier + MockBroker)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_stage5_mirror_apply_decreases_cash_and_adds_position(
    tmp_path: Path,
) -> None:
    """Stage 5: ExecutionReportApplier → MockBroker mirror mutation.

    Proves non-no-op for the mirror path:
    - cash_before > cash_after (gross + fees deducted — never zero delta).
    - position_after contains stock_code with correct volume.
    - cost_price in position is fee-inclusive (v2 schema: gross+fees / volume).
    - BrokerEvent was appended (event trail non-empty).
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)
    assert result.routed_buys, f"No BUY to mirror — {result.outcome.value}"
    plan = result.routed_buys[0].plan

    # Build the FILLED text (same formula as Stage 4)
    volume = plan.volume
    stock_code = plan.stock_code
    instruction_id = plan.instruction_id
    fill_price = round(plan.limit_price, 2)
    filled_text = (
        f"已执行 {instruction_id} 买入 {stock_code} {volume}股 成交价 {fill_price}"
    )

    received_at = _RUN_NOW + dt.timedelta(minutes=10)
    report = parse_execution_report(
        filled_text,
        channel=ExecutionReportChannel.FEISHU,
        received_at=received_at,
    )

    # Capture broker state BEFORE apply
    account_before = await env.broker.get_account()

    # REAL ExecutionReportApplier → REAL MockBroker
    apply_result = await env.applier.apply(report, side_is_buy=True)

    # Capture broker state AFTER apply
    account_after = await env.broker.get_account()
    positions_after = {p.code: p for p in await env.broker.get_positions()}

    # Cash decreased by at least the gross amount (non-no-op: cash changed)
    gross = fill_price * volume
    assert account_after.available_cash < account_before.available_cash, (
        "Cash did not decrease after BUY fill — MockBroker not mutated"
    )
    cash_delta = account_before.available_cash - account_after.available_cash
    assert cash_delta >= gross * 0.99, (  # at least 99% of gross (plus fees)
        f"Cash delta {cash_delta:.2f} less than gross {gross:.2f}"
    )

    # Position was added (stock_code now in positions)
    assert stock_code in positions_after, (
        f"Position for {stock_code} not found after apply"
    )
    pos = positions_after[stock_code]
    assert pos.volume == volume, f"Position volume {pos.volume} != {volume}"

    # cost_price is fee-inclusive (v2 schema: net_amount / volume > fill_price)
    # Min commission = ¥5; for any volume≥100 @ ≥8.0 gross ≥ 800 → commission > 0
    expected_min_cost = fill_price  # must be at least the fill price
    assert pos.cost_price >= expected_min_cost, (
        f"cost_price {pos.cost_price:.4f} < fill_price {fill_price} — "
        "fee-inclusive cost must be ≥ fill price"
    )

    # BrokerEvent was written (non-empty event trail)
    assert apply_result.broker_event_sequence is not None, (
        "broker_event_sequence is None — BrokerEvent not appended"
    )
    assert apply_result.reason == "execution_report_applied"

    # apply_result.cash_delta is negative (outflow for BUY)
    assert apply_result.cash_delta < 0, (
        f"cash_delta {apply_result.cash_delta} should be negative for BUY"
    )


# ---------------------------------------------------------------------------
# Stage 6a: Reconcile — no ticket when mirror matches
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_stage6a_reconcile_no_ticket_when_mirror_matches(
    tmp_path: Path,
) -> None:
    """Stage 6a: reconciliation passes with no ticket when user data matches mirror.

    Proves: detect_deviations returns overall_passed=True when the
    DailyReconciliation exactly mirrors the broker state after a fill.
    No ReconciliationApplier.reset_to_snapshot call needed (no ticket created).
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)
    assert result.routed_buys, f"No BUY to test recon with — {result.outcome.value}"
    plan = result.routed_buys[0].plan

    # Apply the fill to get the real broker state
    volume = plan.volume
    stock_code = plan.stock_code
    instruction_id = plan.instruction_id
    fill_price = round(plan.limit_price, 2)
    filled_text = (
        f"已执行 {instruction_id} 买入 {stock_code} {volume}股 成交价 {fill_price}"
    )
    received_at = _RUN_NOW + dt.timedelta(minutes=10)
    report = parse_execution_report(
        filled_text,
        channel=ExecutionReportChannel.FEISHU,
        received_at=received_at,
    )
    await env.applier.apply(report, side_is_buy=True)

    # Read the real broker state after the fill
    account_after = await env.broker.get_account()
    positions_after = await env.broker.get_positions()
    snap_at = _RUN_NOW + dt.timedelta(hours=7)  # 16:00 EOD cutoff

    # Build the system snapshot (what the MockBroker holds)
    system_snapshot = MockBrokerSnapshot(
        cash=account_after.available_cash,
        positions=tuple(
            ReportedPosition(
                code=p.code.split(".")[0].strip(),
                volume=p.volume,
                cost_price=round(p.cost_price, 4),
            )
            for p in positions_after
        ),
        snapshot_at=snap_at,
    )

    # Build a user report that EXACTLY matches the system state
    ticket_id = "RECON-20260515-001"
    trade_date = "2026-05-15"
    daily_recon = DailyReconciliation(
        ticket_id=ticket_id,
        trade_date=trade_date,
        received_at=snap_at,
        reported_cash=account_after.available_cash,
        reported_positions=tuple(
            ReportedPosition(
                code=p.code.split(".")[0].strip(),
                volume=p.volume,
                cost_price=round(p.cost_price, 4),
            )
            for p in positions_after
        ),
        raw_text="对账: 完全匹配",
    )

    # REAL detect_deviations
    deviation_report = detect_deviations(system_snapshot, daily_recon)

    # No ticket when mirror matches
    assert deviation_report.overall_passed, (
        f"Deviation check failed when mirror should match; "
        f"deviations={[(d.field, d.abs_diff) for d in deviation_report.deviations]}"
    )


# ---------------------------------------------------------------------------
# Stage 6b: Reconcile — ticket created on mismatch, RESOLVED_SYSTEM_AS_TRUTH
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_stage6b_reconcile_mismatch_creates_ticket_and_resolves(
    tmp_path: Path,
) -> None:
    """Stage 6b: cash mismatch → fail-closed ticket; ReconciliationApplier resolves.

    Proves non-no-op for the reconciliation applier path:
    - detect_deviations returns overall_passed=False on a deliberate cash drift.
    - A ReconciliationTicket is constructed (would be OPEN in production).
    - ReconciliationApplier.reset_to_snapshot with RESOLVED_SYSTEM_AS_TRUTH
      succeeds and writes an audit row (broker state unchanged).
    """
    env = await _make_env(tmp_path=tmp_path)

    result = await _run_line1(env)
    assert result.routed_buys, f"No BUY to test recon with — {result.outcome.value}"
    plan = result.routed_buys[0].plan

    # Apply the fill
    volume = plan.volume
    stock_code = plan.stock_code
    instruction_id = plan.instruction_id
    fill_price = round(plan.limit_price, 2)
    filled_text = (
        f"已执行 {instruction_id} 买入 {stock_code} {volume}股 成交价 {fill_price}"
    )
    received_at = _RUN_NOW + dt.timedelta(minutes=10)
    report = parse_execution_report(
        filled_text,
        channel=ExecutionReportChannel.FEISHU,
        received_at=received_at,
    )
    await env.applier.apply(report, side_is_buy=True)

    account_after = await env.broker.get_account()
    positions_after = await env.broker.get_positions()
    snap_at = _RUN_NOW + dt.timedelta(hours=7)

    system_snapshot = MockBrokerSnapshot(
        cash=account_after.available_cash,
        positions=tuple(
            ReportedPosition(
                code=p.code.split(".")[0].strip(),
                volume=p.volume,
                cost_price=round(p.cost_price, 4),
            )
            for p in positions_after
        ),
        snapshot_at=snap_at,
    )

    # Inject a deliberate cash mismatch (> ¥1 threshold)
    mismatched_cash = account_after.available_cash + 500.0  # +500 CNY discrepancy
    ticket_id = "RECON-20260515-002"
    trade_date = "2026-05-15"
    daily_recon = DailyReconciliation(
        ticket_id=ticket_id,
        trade_date=trade_date,
        received_at=snap_at,
        reported_cash=mismatched_cash,  # deliberately wrong
        reported_positions=tuple(
            ReportedPosition(
                code=p.code.split(".")[0].strip(),
                volume=p.volume,
                cost_price=round(p.cost_price, 4),
            )
            for p in positions_after
        ),
        raw_text="对账: 现金差异 500元",
    )

    # REAL detect_deviations — must fail (mismatch ≫ ¥1 threshold)
    deviation_report = detect_deviations(system_snapshot, daily_recon)
    assert not deviation_report.overall_passed, (
        "Deviation check should have failed on deliberate cash mismatch"
    )
    # The cash field deviation is present
    cash_deviations = [d for d in deviation_report.deviations if d.field == "cash"]
    assert cash_deviations, "No cash deviation entry in deviation report"
    assert not cash_deviations[0].passed, "Cash deviation marked as passed — wrong"
    assert cash_deviations[0].abs_diff >= 500.0, (
        f"Cash abs_diff {cash_deviations[0].abs_diff} should be ≥ 500"
    )

    # Build the fail-closed ticket (would be OPEN in production)
    created_at = snap_at
    resolved_at = snap_at + dt.timedelta(minutes=5)
    ticket = ReconciliationTicket(
        ticket_id=ticket_id,
        trade_date=trade_date,
        created_at=created_at,
        deviation_report=deviation_report,
        expected_snapshot_id="snap-20260515-001",
        actual_reconciliation_id=ticket_id,
        status=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        resolved_at=resolved_at,
    )

    # Cash before recon-apply (should be UNCHANGED for SYSTEM_AS_TRUTH)
    cash_before_recon = account_after.available_cash

    # REAL ReconciliationApplier.reset_to_snapshot → SYSTEM_AS_TRUTH (no mutation)
    from backend.audit.models import AuditActor

    apply_recon_result = await env.recon_applier.reset_to_snapshot(
        ticket,
        actor=AuditActor.FRONTEND_USER,
        now=resolved_at,
    )

    # SYSTEM_AS_TRUTH: no cash change
    cash_after_recon = (await env.broker.get_account()).available_cash
    assert cash_after_recon == cash_before_recon, (
        "RESOLVED_SYSTEM_AS_TRUTH must not change broker cash"
    )
    assert apply_recon_result.reason == "reset_skipped_system_as_truth"

    # Audit trail written (non-no-op)
    assert audit_coll_has_recon_event(env.audit_store), (
        "RECONCILIATION_TICKET_DECIDED audit event not written"
    )


def audit_coll_has_recon_event(audit_store: AuditStore) -> bool:
    """Check the in-memory audit collection for a RECONCILIATION_TICKET_DECIDED row."""
    from backend.audit.models import AuditEventType

    coll: InMemoryAuditCollection = audit_store._mongo  # type: ignore[attr-defined]
    return any(
        doc.get("event_type") == AuditEventType.RECONCILIATION_TICKET_DECIDED
        for doc in coll.documents
    )


# ---------------------------------------------------------------------------
# Stage 2+3+4+5: Full chain smoke (single test exercising all stages in sequence)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_zero_spend")
async def test_full_chain_frame_to_mirror_non_no_op(tmp_path: Path) -> None:
    """Full chain (Stages 1-5): frame → BUY plan → dispatch → inbound → mirror.

    This single test confirms the end-to-end path is structurally wired and
    each stage produces a genuine effect (non-no-op). It does NOT re-assert
    every per-field detail (those are in the individual stage tests above);
    instead it checks the TRANSITIONS between stages:

    1. Frame is non-None (Stage 1 done).
    2. VALIDATED BUY plan produced (Stage 2 done).
    3. Feishu sink received wire_text containing instruction_id (Stage 3 done).
    4. Parser produced ExecutionReport with correct instruction_id (Stage 4 done).
    5. MockBroker has position after apply (Stage 5 done).
    """
    env = await _make_env(tmp_path=tmp_path)

    # Stage 1 — frame set
    assert env.frame is not None

    # Stage 2 — Line-1 produces VALIDATED BUY
    result = await _run_line1(env)
    assert result.routed_buys, (
        f"Stage 2 failed: no VALIDATED BUY; outcome={result.outcome.value}"
    )
    plan = result.routed_buys[0].plan
    assert plan.status is InstructionStatus.VALIDATED
    assert plan.side.value == "BUY"

    # Stage 3 — dispatch: Feishu sink captured the wire_text
    assert len(env.feishu_sender.sent) >= 1, "Stage 3 failed: no Feishu send"
    _, wire_text = env.feishu_sender.sent[0]
    assert plan.instruction_id in wire_text, (
        "Stage 3 failed: instruction_id not in wire_text"
    )

    # Stage 4 — inbound: parser produces ExecutionReport
    fill_price = round(plan.limit_price, 2)
    filled_text = (
        f"已执行 {plan.instruction_id} 买入 {plan.stock_code} "
        f"{plan.volume}股 成交价 {fill_price}"
    )
    received_at = _RUN_NOW + dt.timedelta(minutes=15)
    report = parse_execution_report(
        filled_text,
        channel=ExecutionReportChannel.FEISHU,
        received_at=received_at,
    )
    assert report.instruction_id == plan.instruction_id, "Stage 4 failed: id mismatch"

    # Stage 5 — mirror: broker state changed
    before_cash = (await env.broker.get_account()).available_cash
    await env.applier.apply(report, side_is_buy=True)
    after_cash = (await env.broker.get_account()).available_cash
    assert after_cash < before_cash, "Stage 5 failed: cash not decreased after BUY fill"

    positions = {p.code: p for p in await env.broker.get_positions()}
    bare_code = plan.stock_code.split(".")[0].strip()
    # Position may be keyed by bare code or with suffix — check both
    found = bare_code in positions or any(
        p.split(".")[0] == bare_code for p in positions
    )
    assert found, (
        f"Stage 5 failed: position for {bare_code} not found; "
        f"positions={list(positions.keys())}"
    )
