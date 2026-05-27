"""Tests for the U-D3 render-only double-line dry-run harness.

NO network, NO real LLM, NO Mongo/Redis: every external seam is a fake —
* a canned :class:`MarketDataSnapshot` screener frame (no Tushare pull),
* a stub LLM router with canned 4-agent debate responses (no real qwen),
* an in-memory fake redis for the debate's budget reservation,
* a collecting ``dry_run_sink`` so the rendered wire texts can be inspected.

These assert the harness's structural contract:
  (a) it runs 1 day render-only — the DRY_RUN coordinator's executor +
      dispatcher stubs are NEVER called (asserted via call-recording stubs),
  (b) the artifact JSON is written with the rendered wire texts +
      ``real_sends == 0`` + ``pass == false`` + ``owner_reviewed == false``,
  (c) BOTH lines are exercised (Line-1 BUY + Line-2 daily SELL render),
  (d) a HOLD / empty-universe path does not crash.

The REAL run (real Tushare frame + real qwen + cost) is the owner's, not CI's,
so the few paths that would need real network/LLM are ``@pytest.mark.skip``
(default-skip) and documented.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from backend.broker.mock_broker import MockBroker
from backend.broker.models import (
    BrokerConfig,
    CircuitBreakerConfig,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.data.trading_calendar import next_trading_day, prev_trading_day
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.engine import RiskEngine
from backend.services import cost_guard
from backend.services.universe_policy import ExclusionRules, load_policy
from backend.utils.trading_hours import SHANGHAI
from scripts import dry_run_double_line as harness
from scripts import dry_run_realdata as realdata

_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_RISK_YAML = "config/risk.yaml"
_SELECTOR_YAML = "config/candidate_weights/v1.yaml"
_POLICY = "config/universe_policy.yaml"


# ---------------------------------------------------------------------------
# Canned market frame (mirrors tests/orchestration/test_line1_runner.py)
# ---------------------------------------------------------------------------


def _uptrend(base: float, n: int = 30) -> list[float]:
    return [base + 0.10 * i for i in range(n)]


def _crash(n: int = 30) -> list[float]:
    closes = [4.5 + (0.001 if i % 2 else -0.001) for i in range(n)]
    closes[-1] = closes[-2] * 0.96  # -4% adverse (oversold, not limit-down)
    return closes


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _frame(rows: list[str]) -> MarketDataSnapshot:
    raw = "\n".join([_HEADER, *rows]).encode("utf-8")
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
        metadata={"parent_snapshot_ids": ["raw-1", "raw-2"]},
    )


def _buy_frame() -> MarketDataSnapshot:
    """Liquid sh_main uptrend stocks + one held crashing ETF (Line-2 daily)."""
    return _frame(
        [
            _row("600000", "浦发银行", _uptrend(10.0)),
            _row("600004", "白云机场", _uptrend(9.0)),
            _row("600006", "东风汽车", _uptrend(8.0)),
            _row("510300", "沪深300ETF", _crash()),  # held → Line-2 SELL
        ]
    )


def _empty_frame() -> MarketDataSnapshot:
    """Only an ST name → excluded → empty universe (HOLD/empty path)."""
    return _frame([_row("600000", "ST浦发", _uptrend(10.0))])


# ---------------------------------------------------------------------------
# Stub LLM router + fake redis (mirrors test_line1_runner)
# ---------------------------------------------------------------------------


class _StubRouter:
    """4-agent debate stub. ``action`` is configurable (买入/卖出/持有)."""

    def __init__(self, *, action: str = "买入") -> None:
        self.calls = 0
        self._action = action

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if agent_name == "fund_manager":
            content = f'{{"action": "{self._action}", "reasoning": "stub thesis"}}'
        else:
            content = f"{agent_name} stub analysis report"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeMarketMeta:
    """Returns a fixed prev_close per code (the live MarketMetaProvider analogue).

    Offline the harness has no live market_meta; the SELL 14-check's
    ``limit_up_down_block`` needs a prev_close, so the test injects one — exactly
    what the real owner-driven run gets from the Mongo-backed provider.
    """

    def __init__(self, prev_close_by_code: dict[str, float]) -> None:
        self._prev = prev_close_by_code

    async def get_prev_close(self, code: str) -> float | None:
        return self._prev.get(code.split(".")[0].strip())


class _FakeLiveData:
    """Offline stand-in for the U-E2 live quote layer (dual-source + 卖一).

    Returns last == best_ask ≈ each lead's T-1 last close so the cage limit
    stays within the prev_close band, with the spot timestamp at the real wall
    clock — strictly AFTER the replayed run ``now`` (a past trade date) so the
    provider's staleness window passes (age is negative). The real owner-driven
    run gets these from adata/akshare during market hours.
    """

    _PRICES: dict[str, float] = {
        "600000": 12.9, "600004": 11.9, "600006": 10.9, "510300": 9.6,
    }

    def _q(self, code: str, price: float):  # noqa: ANN202
        from backend.models.market import StockQuote

        return StockQuote(
            code=code, name=code, price=price, open=price, high=price,
            low=price, prev_close=price, change_pct=0.0, volume=1.0,
            amount=1.0, turnover_rate=0.0, timestamp=dt.datetime.now(dt.UTC),
        )

    async def get_stock_realtime_dual(self, code: str):  # noqa: ANN201
        bare = code.split(".")[0].strip()
        price = self._PRICES.get(bare)
        if price is None:
            return None, None
        return self._q(bare, price), self._q(bare, price)

    async def get_stock_orderbook(self, code: str):  # noqa: ANN201
        from backend.models.market import StockOrderbook

        bare = code.split(".")[0].strip()
        price = self._PRICES.get(bare)
        if price is None:
            raise harness_data_fetch_error(bare)
        return StockOrderbook(
            code=bare, last=price, best_ask=price, best_bid=price * 0.999,
            source="adata", ts=dt.datetime.now(dt.UTC),
        )

    async def get_watchlist_snapshot(self, codes, snapshot_at):  # noqa: ANN001, ANN201
        # Line-2 intraday triggers stay off offline (empty spots) — only the
        # Line-1 cage path needs this fake (matches the prior market_data=None).
        return []


def harness_data_fetch_error(code: str) -> Exception:
    from backend.data.market_data import DataFetchError

    return DataFetchError(f"no orderbook for {code}")


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
# DryRunContext builder over fakes (no network, no real LLM)
# ---------------------------------------------------------------------------


def _risk_config() -> RiskConfig:
    return RiskConfig(
        position_limits=PositionLimitsConfig(),
        stop_loss=StopLossConfig(),
        circuit_breaker=CircuitBreakerConfig(),
        universe=UniverseConfig(),
    )


async def _make_ctx(
    *,
    frame: MarketDataSnapshot,
    router: _StubRouter,
    tmp_path: Path,
    held_positions: tuple[Any, ...] = (),
    market_meta: Any = None,
    market_data: Any | None = None,
) -> harness.DryRunContext:
    """Assemble a DryRunContext with fakes (the offline analogue of
    :func:`harness.build_real_context`)."""
    collector = harness.DryRunCollector()
    coordinator, executor, dispatcher = harness._build_coordinator(collector)
    policy = load_policy(Path(_POLICY))
    line1, line2_daily, line2_intraday, snapshot_store = harness._build_runners(
        coordinator=coordinator,
        exclusion_rules=ExclusionRules(),
        risk_yaml=_RISK_YAML,
        selector_yaml=_SELECTOR_YAML,
        redis_client=_FakeRedis(),
    )

    broker = MockBroker(config=BrokerConfig(initial_capital=harness._INITIAL_CAPITAL))
    if held_positions:
        await broker.seed_from_recovery(
            cash=harness._INITIAL_CAPITAL,
            frozen_cash=0.0,
            initial_capital=harness._INITIAL_CAPITAL,
            positions=held_positions,
        )

    risk_config = _risk_config()
    return harness.DryRunContext(
        collector=collector,
        executor=executor,
        dispatcher=dispatcher,
        line1_runner=line1,
        line2_daily_runner=line2_daily,
        line2_intraday_runner=line2_intraday,
        broker=broker,
        risk_engine=RiskEngine(risk_config),
        risk_config=risk_config,
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=policy,
        llm_router=router,
        frame=frame,
        index_closes=(),  # NEUTRAL — no bear-regime ADD ban (prereq 4 fallback)
        market_meta=market_meta,
        # U-E2: a fake live quote layer so Line-1 can price a cage-bounded BUY
        # offline. ``None`` keeps the legacy empty-spot path (Line-2 intraday
        # triggers off; every Line-1 lead degrades to a non-actionable notice).
        market_data=market_data if market_data is not None else _FakeLiveData(),
        data_quality_provider=None,  # clean fallback (prereq 1)
        snapshot_store=snapshot_store,
        run_trade_date="20260515",
        frame_trade_date=frame.trade_date,
        token_fingerprint="abcd1234",
        llm_models=("qwen-stub",),
        redis_client=None,
    )


def _held_etf() -> tuple[Any, ...]:
    """A held crashing ETF position to exercise the Line-2 daily SELL scan."""
    return (
        SimpleNamespace(
            code="510300", volume=300, today_bought_volume=0, cost_price=4.55
        ),
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the debate budget reservation under the ¥20 cap with a fake redis,
    and redirect every scratch path the harness writes into ``tmp_path`` so the
    test never touches the repo's ``data/`` tree."""

    async def _spent(_redis, *, today=None):  # noqa: ANN001, ANN202
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)
    monkeypatch.setenv(
        "QUANTMIND_DRYRUN_SNAPSHOT_ROOT", str(tmp_path / "line2_snapshots")
    )
    monkeypatch.setenv(
        "QUANTMIND_DRYRUN_MANIFEST_ROOT", str(tmp_path / "intraday_manifests")
    )
    monkeypatch.setenv(
        "QUANTMIND_DRYRUN_AUDIT_JSONL", str(tmp_path / "audit.jsonl")
    )


# ---------------------------------------------------------------------------
# (a) render-only — executor + dispatcher NEVER called
# ---------------------------------------------------------------------------


async def test_dry_run_render_only_no_executor_no_dispatcher(tmp_path) -> None:
    router = _StubRouter(action="买入")
    ctx = await _make_ctx(
        frame=_buy_frame(), router=router, tmp_path=tmp_path,
        held_positions=_held_etf(),
    )
    out_path = tmp_path / "artifact.json"
    outcome = await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=out_path
    )

    # The DRY_RUN coordinator renders to the sink and touches NOTHING else.
    assert ctx.executor.calls == 0, "DRY_RUN must never call the SimulationExecutor"
    assert ctx.dispatcher.calls == 0, "DRY_RUN must never call the Dispatcher"
    # At least the Line-1 BUY rendered.
    assert outcome.line1_rendered >= 1
    assert outcome.ok is True


async def test_dry_run_zero_broker_mutation(tmp_path) -> None:
    """The MockBroker mirror is untouched by render-only routing."""
    router = _StubRouter(action="买入")
    ctx = await _make_ctx(
        frame=_buy_frame(), router=router, tmp_path=tmp_path,
        held_positions=_held_etf(),
    )
    account_before = await ctx.broker.get_account()
    positions_before = await ctx.broker.get_positions()
    await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=tmp_path / "a.json"
    )
    account_after = await ctx.broker.get_account()
    positions_after = await ctx.broker.get_positions()
    assert account_after.available_cash == account_before.available_cash
    assert account_after.total_assets == account_before.total_assets
    assert {p.code for p in positions_after} == {p.code for p in positions_before}


# ---------------------------------------------------------------------------
# (b) artifact JSON written with rendered wire texts + flags
# ---------------------------------------------------------------------------


async def test_artifact_written_with_flags_and_wire_texts(tmp_path) -> None:
    router = _StubRouter(action="买入")
    ctx = await _make_ctx(
        frame=_buy_frame(), router=router, tmp_path=tmp_path,
        held_positions=_held_etf(),
    )
    out_path = tmp_path / "20260515_double_line.json"
    await harness.run_dry_run(ctx, start_date=_trading_start(), out_path=out_path)

    assert out_path.exists()
    data = json.loads(out_path.read_text("utf-8"))
    assert data["real_sends"] == 0
    assert data["real_broker_mutations"] == 0
    assert data["pass"] is False
    assert data["owner_reviewed"] is False
    assert data["noop_executor_calls"] == 0
    assert data["noop_dispatcher_calls"] == 0
    # Provenance: the frame's checksum + snapshot id are recorded for replay.
    prov = data["run_metadata"]["frame_provenance"]
    assert prov["raw_payload_sha256"] == ctx.frame.raw_payload_sha256
    assert prov["trade_date"] == "20260514"
    # The token fingerprint is recorded, NEVER the token.
    assert data["run_metadata"]["tushare_token_fingerprint"] == "abcd1234"
    # The actual rendered Feishu wire texts are present for owner review.
    assert data["rendered_count"] >= 1
    buy_texts = [s for s in data["rendered_signals"] if s["side"] == "BUY"]
    assert buy_texts, "the BUY wire text the owner reads must be in the artifact"
    assert "买入信号" in buy_texts[0]["wire_text"]


# ---------------------------------------------------------------------------
# (c) both lines exercised
# ---------------------------------------------------------------------------


async def test_both_lines_exercised(tmp_path) -> None:
    router = _StubRouter(action="买入")
    # The crashing ETF's last bar = prev_bar * 0.96; supply the pre-crash bar as
    # prev_close so the -4% move reads as oversold (not limit-down) and the SELL
    # 14-check (limit_up_down_block) passes.
    crash = _crash()
    ctx = await _make_ctx(
        frame=_buy_frame(), router=router, tmp_path=tmp_path,
        held_positions=_held_etf(),
        market_meta=_FakeMarketMeta({"510300": crash[-2]}),
    )
    await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=tmp_path / "a.json"
    )
    # Line-1 ran (its result recorded) AND Line-2 daily ran over the held ETF.
    assert ctx.line1_results, "Line-1 was not exercised"
    assert ctx.line2_daily_results, "Line-2 daily was not exercised"
    # The held crashing ETF produced an adverse-anomaly SELL render.
    sides = {s.side for s in ctx.collector.signals}
    assert "BUY" in sides
    assert "SELL" in sides, "Line-2 daily SELL render expected on the crashing ETF"


# ---------------------------------------------------------------------------
# (d) HOLD / empty-universe path does not crash
# ---------------------------------------------------------------------------


async def test_empty_universe_does_not_crash(tmp_path) -> None:
    # Empty screen (only an ST name) → Line-1 EMPTY_UNIVERSE, no debate, no BUY.
    router = _StubRouter(action="买入")
    ctx = await _make_ctx(frame=_empty_frame(), router=router, tmp_path=tmp_path)
    out_path = tmp_path / "a.json"
    outcome = await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=out_path
    )
    # 0 BUY rendered is flagged (the owner must investigate), but it MUST NOT
    # crash and MUST still write the artifact.
    assert out_path.exists()
    assert outcome.line1_rendered == 0
    assert outcome.ok is False  # 0 BUY when a BUY was expected → flagged
    assert any("0 BUY" in e for e in outcome.errors)
    assert router.calls == 0  # no debate spun up — no LLM spend on empty screen


async def test_line1_basket_all_buys_labeled_line1(tmp_path) -> None:
    # P1-7-amendment-2026-05-26 basket: EVERY routed BUY (not just the first)
    # must be labeled line1 so line1_rendered + the owner-reviewed artifact
    # reflect the whole basket — before the harness fix the 2nd+ stayed
    # line="unknown" and were under-counted.
    router = _StubRouter(action="买入")
    ctx = await _make_ctx(frame=_buy_frame(), router=router, tmp_path=tmp_path)
    outcome = await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=tmp_path / "a.json"
    )
    basket = ctx.line1_results[0].routed_buys
    assert len(basket) >= 2, "the fixture should yield a multi-name basket"
    # All basket BUYs labeled → none counted as line="unknown".
    assert outcome.line1_rendered == len(basket)
    line1_codes = {s.code for s in ctx.collector.signals if s.line == "line1"}
    assert line1_codes == {rb.plan.stock_code for rb in basket}
    # Each basket BUY consumed a check-10 daily order slot.
    assert ctx.today_instruction_count >= len(basket)


async def test_hold_recommendation_not_routed(tmp_path) -> None:
    # fund_manager proposes HOLD → never routes/renders (CLAUDE.md §2.7).
    router = _StubRouter(action="持有")
    ctx = await _make_ctx(frame=_buy_frame(), router=router, tmp_path=tmp_path)
    out_path = tmp_path / "a.json"
    outcome = await harness.run_dry_run(
        ctx, start_date=_trading_start(), out_path=out_path
    )
    assert out_path.exists()
    # HOLD is not a routed BUY → 0 line1 rendered (flagged, not crashed).
    assert outcome.line1_rendered == 0
    assert ctx.executor.calls == 0 and ctx.dispatcher.calls == 0


# ---------------------------------------------------------------------------
# collector pairing + count helpers (unit)
# ---------------------------------------------------------------------------


def test_collector_pairs_text_with_label() -> None:
    c = harness.DryRunCollector()
    c("【QuantMind 买入信号 · 合规】\nQM-20260515-100001-600000-BUY-001")
    c.label(
        "QM-20260515-100001-600000-BUY-001", line="line1", side="BUY", code="600000"
    )
    sigs = c.signals
    assert len(sigs) == 1
    assert sigs[0].line == "line1"
    assert sigs[0].side == "BUY"
    assert sigs[0].code == "600000"


def test_collector_unlabelled_text_is_captured_not_lost() -> None:
    c = harness.DryRunCollector()
    c("some rendered text without a registered id")
    sigs = c.signals
    assert len(sigs) == 1
    assert sigs[0].line == "unknown"  # captured, not silently dropped


def test_count_by_tallies() -> None:
    from scripts.dry_run_artifact import count_by

    assert count_by(["routed", "routed", "rejected"]) == {
        "routed": 2,
        "rejected": 1,
    }


# ---------------------------------------------------------------------------
# CLI argument plumbing (no run)
# ---------------------------------------------------------------------------


def test_resolve_out_default_path() -> None:
    p = harness._resolve_out(None, "20260515")
    assert p == Path("data/dry_run/20260515_double_line.json")


def test_resolve_out_explicit() -> None:
    p = harness._resolve_out("/tmp/custom.json", "20260515")
    assert p == Path("/tmp/custom.json")


def test_resolve_start_invalid_raises() -> None:
    with pytest.raises(SystemExit):
        harness._resolve_start("not-a-date")


async def test_build_real_context_wires_redis_router_and_date(monkeypatch) -> None:
    """Codex U-D3 fixes (offline, fakes — no network/LLM):

    P1 — the live redis is threaded into the Line-1 runner (its debate reserves
    budget on it) AND the LLM router is ``initialize()``d with it before use.
    P2 — a weekend ``--start`` rolls FORWARD to the next trading day so the
    artifact's run_trade_date matches what ``run_simulation`` actually walks.
    """
    sentinel_redis = object()
    init_calls: list[Any] = []
    frame = _buy_frame()

    class _FakeRouter:
        def __init__(self, *, config_path: str) -> None:
            self.config_path = config_path

        async def initialize(self, redis_client: Any = None) -> None:
            init_calls.append(redis_client)

    async def _fake_frame(*, as_of, signal_id):  # noqa: ANN001, ANN202
        return frame, frame.trade_date, "fp123456"

    async def _fake_index(*, end):  # noqa: ANN001, ANN202
        return ()

    async def _fake_data_layer():  # noqa: ANN202
        return None, None, None

    monkeypatch.setattr(harness, "assemble_real_frame", _fake_frame)
    monkeypatch.setattr(harness, "pull_index_closes", _fake_index)
    monkeypatch.setattr(harness, "build_data_layer", _fake_data_layer)
    monkeypatch.setattr(harness, "build_redis_or_none", lambda: sentinel_redis)
    monkeypatch.setattr("backend.llm.router.LLMRouter", _FakeRouter)

    weekend = __import__("datetime").date(2026, 5, 16)  # Saturday
    ctx = await harness.build_real_context(start_date=weekend)

    # P1 — redis threaded into the runner + router initialized with it.
    assert ctx.redis_client is sentinel_redis
    assert ctx.line1_runner._redis is sentinel_redis
    assert init_calls == [sentinel_redis]
    # P2 — weekend rolled FORWARD to the next trading day (matches run_simulation).
    expected = harness.next_trading_day(weekend)
    assert ctx.run_trade_date == expected.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# REAL-network / REAL-LLM paths — owner's run, default-skip in CI
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="real Tushare + real qwen — owner-driven, not CI")
async def test_real_build_context_smoke() -> None:  # pragma: no cover
    import datetime as dt

    ctx = await harness.build_real_context(start_date=dt.date(2026, 5, 25))
    assert ctx.frame is not None
    assert ctx.token_fingerprint  # fingerprint, not the token


@pytest.mark.skip(reason="real Tushare + real qwen — owner-driven, not CI")
def test_real_cli_main() -> None:  # pragma: no cover
    assert harness.main(["--start", "2026-05-25", "--json"]) in (0, 1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _trading_start():  # noqa: ANN202
    """A known trading day for the pinned-clock harness (2026-05-15, Friday)."""
    return dt.date(2026, 5, 15)


# ---------------------------------------------------------------------------
# U-D4b regression — dry-run snapshot_at inversion (wall-clock fetch_time)
#
# The owner-driven real run (#43) crashed every InstructionPlan with
# ``data_snapshot.snapshot_at must be strictly before created_at``
# (instruction.py:230). Root cause: the dry-run REPLAYS a past run day, but
# the real assembler stamped ``fetch_time_utc = datetime.now(UTC)`` (tonight's
# wall clock), while ``run_simulation`` drives ``created_at`` from the replayed
# run-day 09:30 — so snapshot_at (tonight) >= created_at (the past 09:30). The
# fix anchors the dry-run frame's ``fetch_time_utc`` to the T-1 EOD logical
# close (``as_of`` 15:00 CST → UTC) via an injected clock, strictly before the
# replayed created_at. ``frame.fetch_time_utc`` is the SINGLE source both lines
# read (Line-1 via line1_context_provider DataSnapshot.snapshot_at; Line-2
# daily via ``Line2DailyProvider(snapshot_at=frame.fetch_time_utc)``), so this
# one anchor fixes both lines.
# ---------------------------------------------------------------------------


# (ts_code, name, list_date, close, amount_qianyuan) — old, liquid, scorable.
_REG_SPEC = [
    ("600000.SH", "浦发银行", "20100101", 10.0, 300_000.0),
    ("000001.SZ", "平安银行", "20100101", 12.0, 400_000.0),
]


class _FakeTushareClient:
    """In-memory FrameDataSource with the bits ``assemble_real_frame`` reads.

    Accepts ``token=`` like the real client and exposes ``token_fingerprint``
    (a fingerprint, NEVER the token); ``daily``/``daily_basic`` return a fixed
    frame for any trade_date, ``stock_basic`` the roster — no network.
    """

    def __init__(self, *, token: str | None = None) -> None:  # noqa: ARG002
        self.token_fingerprint = "feedf00d"

    async def daily(self, trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in _REG_SPEC],
                "trade_date": [trade_date] * len(_REG_SPEC),
                "close": [s[3] for s in _REG_SPEC],
                "amount": [s[4] for s in _REG_SPEC],
            }
        )

    async def daily_basic(self, trade_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in _REG_SPEC],
                "trade_date": [trade_date] * len(_REG_SPEC),
                "close": [s[3] for s in _REG_SPEC],
                "pe": [15.0] * len(_REG_SPEC),
            }
        )

    async def stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in _REG_SPEC],
                "name": [s[1] for s in _REG_SPEC],
                "list_date": [s[2] for s in _REG_SPEC],
            }
        )


def test_t_minus_1_eod_utc_anchors_to_tminus1_close() -> None:
    """The helper returns the ``as_of`` 15:00 CST close as a tz-aware UTC time."""
    as_of = prev_trading_day(dt.date(2026, 5, 18))  # T-1 trading day
    anchored = realdata.t_minus_1_eod_utc(as_of)

    assert anchored.tzinfo is not None, "fetch_time_utc must be tz-aware (UTC)"
    expected = datetime(
        as_of.year, as_of.month, as_of.day, 15, 0, tzinfo=SHANGHAI
    ).astimezone(UTC)
    assert anchored == expected
    # A-share close 15:00 CST == 07:00 UTC.
    assert anchored.hour == 7 and anchored.minute == 0


def test_t_minus_1_eod_utc_strictly_before_replayed_created_at() -> None:
    """The anchor is strictly before the replayed run-day 09:30 created_at.

    This is the exact ``instruction.py:230`` invariant
    (``snapshot_at < created_at``) that crashed the real run, asserted for the
    snapshot_at BOTH lines feed into their InstructionPlan/SELL plans.
    """
    as_of = prev_trading_day(dt.date(2026, 5, 18))  # T-1
    run_day = next_trading_day(as_of)  # the replayed run day (T)
    created_at = datetime(
        run_day.year, run_day.month, run_day.day, 9, 30, tzinfo=SHANGHAI
    )
    snapshot_at = realdata.t_minus_1_eod_utc(as_of)

    # >= would raise the InstructionPlan ValueError; assert strictly-before.
    assert snapshot_at < created_at


async def test_assemble_real_frame_stamps_tminus1_eod_not_wallclock(
    tmp_path, monkeypatch
) -> None:
    """``assemble_real_frame`` stamps the frame at T-1 EOD, not wall clock.

    Exercises the REAL ``Line1FrameAssembler`` + ``SnapshotStore`` end-to-end
    with only the network client faked, proving the injected clock reaches the
    derived frame snapshot the harness threads into BOTH lines.
    """
    as_of = prev_trading_day(dt.date(2026, 5, 18))  # T-1
    run_day = next_trading_day(as_of)
    created_at = datetime(
        run_day.year, run_day.month, run_day.day, 9, 30, tzinfo=SHANGHAI
    )

    monkeypatch.setenv("QUANTMIND_DRYRUN_FRAME_ROOT", str(tmp_path / "frames"))
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-not-used")
    monkeypatch.setattr(
        "backend.data.tushare_client.TushareClient", _FakeTushareClient
    )

    frame, frame_td, token_fp = await realdata.assemble_real_frame(
        as_of=as_of, signal_id="SIG-ud4b"
    )

    # The derived frame is stamped at the T-1 EOD close, NOT tonight's wall
    # clock — so snapshot_at < created_at holds (no InstructionPlan crash).
    assert frame.fetch_time_utc == realdata.t_minus_1_eod_utc(as_of)
    assert frame.fetch_time_utc < created_at
    assert frame_td == as_of.strftime("%Y%m%d")
    assert token_fp == "feedf00d"  # fingerprint surfaced, never the token


async def test_assemble_real_frame_uses_fresh_store_when_root_unset(
    tmp_path, monkeypatch
) -> None:
    """With ``QUANTMIND_DRYRUN_FRAME_ROOT`` unset, the dry-run uses a FRESH store.

    Codex U-D4b P1: the persistent ``data/dry_run/frames`` store would REUSE a
    pre-fix derived frame stamped with wall-clock time verbatim (fetch_time is
    not a reuse key), so the corrected clock would not take effect. The default
    must therefore be a fresh ephemeral store, never the persistent path.
    """
    monkeypatch.delenv("QUANTMIND_DRYRUN_FRAME_ROOT", raising=False)
    monkeypatch.setenv("TUSHARE_TOKEN", "fake-token-not-used")
    monkeypatch.setattr(
        "backend.data.tushare_client.TushareClient", _FakeTushareClient
    )

    captured_roots: list[str] = []
    real_store_cls = realdata.SnapshotStore

    def _spy_store(*, root):  # noqa: ANN001, ANN202
        captured_roots.append(str(root))
        return real_store_cls(root=root)

    monkeypatch.setattr(realdata, "SnapshotStore", _spy_store)
    monkeypatch.setattr(
        realdata.tempfile, "mkdtemp", lambda **_: str(tmp_path / "fresh")
    )

    as_of = prev_trading_day(dt.date(2026, 5, 18))
    frame, _, _ = await realdata.assemble_real_frame(
        as_of=as_of, signal_id="SIG-ud4b-fresh"
    )

    # A fresh ephemeral store was used — NOT the persistent default that may
    # hold pre-fix stale frames.
    assert captured_roots == [str(tmp_path / "fresh")]
    assert "data/dry_run/frames" not in captured_roots[0]
    # And the frame is still anchored at the T-1 EOD close.
    assert frame.fetch_time_utc == realdata.t_minus_1_eod_utc(as_of)
