"""AE-002 — rqalpha oracle end-to-end + friction calibration (venv-gated).

This is the task's *real* proof (CLAUDE.md "绿测试 ≠ 闭环可用"): it spawns the
actual rqalpha venv subprocess over a PIT export and asserts (1) the runner
produces a real ``BacktestRunResult`` (no longer perma-UNAVAILABLE), and (2) the
friction-calibration gate — rqalpha vs the deterministic golden-replay integer
accounting (AE-003), fed the *same* ``config/broker.yaml`` friction over the same
PIT window — returns **CONSISTENT** (≤25bps over ≥95% of days).

Skipped when the owner-gated oracle venv is absent (CI without rqalpha) — the
fail-closed paths are covered by ``test_rqalpha_runner_subprocess`` with fakes.
The owner-gated step is re-running this against the *live* MockBroker shadow;
golden-replay is a faithful same-source stand-in for that accounting here.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.backtest.golden_replay import (
    ReplayDay,
    ReplayFill,
    replay_equity_curve,
)
from backend.backtest.pit_export import BrokerFriction, SnapshotPitExporter
from backend.broker.cost_calculator import calculate_cost
from backend.broker.models import BrokerConfig, OrderDirection
from backend.data.stock_metadata import classify_board
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.strategy_evolution.backtest_oracle import (
    DEFAULT_VENV_ENV_VAR,
    DEFAULT_VENV_PYTHON,
    BacktestRunResult,
    BacktestSpec,
    EquityDay,
    OracleVerdict,
    RqalphaBacktestRunner,
    compare_equity_curves,
)

_VENV = os.environ.get(DEFAULT_VENV_ENV_VAR, DEFAULT_VENV_PYTHON)
_HAS_VENV = Path(_VENV).exists() and os.access(_VENV, os.X_OK)
pytestmark = pytest.mark.skipif(
    not _HAS_VENV, reason=f"oracle venv absent ({_VENV}) — owner-gated"
)

# 8 trading days, Jan 2023 (skip the weekend 07/08); main-board stock.
_DAYS = [
    "20230104", "20230105", "20230106", "20230109",
    "20230110", "20230111", "20230112", "20230113",
]
_CLOSES = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
# CS stock (600519, SH main) + ETF (510300, SH). The ETF case locks the
# stamp-tax parity fix: the broker charges stamp tax on *every* sell, so the
# oracle must too (codex review) — exempting ETFs would diverge ~100bps.
_CS = "600519.SH"
_ETF = "510300.SH"
_CAPITAL = 1_000_000.0

_COMMISSION_RATE = 0.00015
_MIN_COMMISSION = 5.0
_STAMP_TAX = 0.001
_TRANSFER_FEE = 0.0000341
_SLIPPAGE = {"sh_main": 1.5, "sz_main": 1.5, "chuangye": 3.5, "etf": 1.5}


def _friction() -> BrokerFriction:
    return BrokerFriction(
        commission_rate=_COMMISSION_RATE,
        min_commission=_MIN_COMMISSION,
        stamp_tax_rate=_STAMP_TAX,
        transfer_fee_rate=_TRANSFER_FEE,
        slippage_bps_by_board=_SLIPPAGE,
    )


def _broker_config() -> BrokerConfig:
    return BrokerConfig(
        initial_capital=_CAPITAL,
        commission_rate=_COMMISSION_RATE,
        stamp_tax_rate=_STAMP_TAX,
        slippage_bps=2,
        slippage_bps_by_board=_SLIPPAGE,
        min_commission=_MIN_COMMISSION,
        enable_transfer_fee=True,
    )


def _seed_store(root: Path, ts_code: str) -> SnapshotStore:
    from backend.data.historical_ingest.serialization import canonical_csv_bytes

    store = SnapshotStore(root)
    for day, close in zip(_DAYS, _CLOSES, strict=True):
        daily = pd.DataFrame(
            [{"ts_code": ts_code, "open": close, "high": close, "low": close,
              "close": close, "vol": 1e8, "amount": close * 1e8}]
        )
        adj = pd.DataFrame([{"ts_code": ts_code, "adj_factor": 1.0}])  # qfq == raw
        for endpoint, df in (("daily", daily), ("adj_factor", adj)):
            store.put(
                MarketDataSnapshot.create(
                    vendor="tushare", endpoint=endpoint,
                    params={"trade_date": day}, trade_date=day,
                    raw_payload=canonical_csv_bytes(df), encoding="csv",
                    compression="none",
                    fetch_time_utc=datetime(2023, 1, 13, tzinfo=UTC),
                    metadata={"rows": 1},
                )
            )
    return store


def _write_strategy(path: Path, ts_code: str) -> str:
    doc = {
        "schema_version": 1,
        "orders": [
            {"trade_date": "20230104", "ts_code": ts_code,
             "side": "BUY", "shares": 1000},
            {"trade_date": "20230113", "ts_code": ts_code,
             "side": "SELL", "shares": 1000},
        ],
    }
    raw = json.dumps(doc, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _golden_curve(ts_code: str) -> BacktestRunResult:
    """Deterministic integer-accounting curve with the SAME broker friction."""
    config = _broker_config()
    code6 = ts_code.partition(".")[0]
    board = classify_board(code6)

    def _fill(close: float, direction: OrderDirection) -> ReplayFill:
        cost = calculate_cost(
            code=code6, board=board, order_price=close, volume=1000,
            direction=direction, config=config,
        )
        fees = cost.commission + cost.stamp_tax + cost.transfer_fee
        return ReplayFill(
            code=code6,
            side="BUY" if direction is OrderDirection.BUY else "SELL",
            volume=1000,
            price_cents=round(cost.fill_price * 100),
            cost_cents=round(fees * 100),
        )

    days: list[ReplayDay] = []
    for i, (ymd, close) in enumerate(zip(_DAYS, _CLOSES, strict=True)):
        fills: tuple[ReplayFill, ...] = ()
        if i == 0:
            fills = (_fill(close, OrderDirection.BUY),)
        elif i == len(_DAYS) - 1:
            fills = (_fill(close, OrderDirection.SELL),)
        days.append(
            ReplayDay(
                trade_date=_iso(ymd),
                fills=fills,
                close_marks_cents={code6: round(close * 100)},
            )
        )
    curve = replay_equity_curve(
        initial_cash_cents=round(_CAPITAL * 100), days=days
    )
    return BacktestRunResult(
        engine="mockbroker",
        engine_version="golden_replay",
        strategy_hash="a" * 64,
        equity_curve=tuple(
            EquityDay(trade_date=p.trade_date, total_equity=p.total_equity_cents / 100)
            for p in curve
        ),
        fill_count=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ts_code", [_CS, _ETF])
async def test_oracle_runs_and_calibrates_to_golden(
    tmp_path: Path, ts_code: str
) -> None:
    store = _seed_store(tmp_path / "store", ts_code)
    strategy_hash = _write_strategy(tmp_path / "strategy.json", ts_code)
    spec = BacktestSpec(
        strategy_hash=strategy_hash,
        strategy_source_path=str(tmp_path / "strategy.json"),
        start_date="20230104",
        end_date="20230113",
        initial_capital=_CAPITAL,
    )
    exporter = SnapshotPitExporter(
        snapshot_store=store, friction=_friction(), calendar=_DAYS
    )
    runner = RqalphaBacktestRunner(exporter=exporter, venv_python=_VENV)

    # (1) the runner actually runs rqalpha (no longer perma-UNAVAILABLE).
    oracle = await runner.run(spec)
    assert oracle.engine == "rqalpha"
    assert oracle.fill_count == 2
    assert len(oracle.equity_curve) == len(_DAYS)
    assert oracle.engine_fingerprint and "numpy" in oracle.engine_fingerprint

    # (2) friction-calibration gate: rqalpha vs golden_replay -> CONSISTENT.
    # The ETF case proves the stamp-tax-on-every-sell parity (codex fix):
    # an ETF-exempt oracle would diverge ~100bps on the sell day.
    golden = _golden_curve(ts_code)
    # align strategy_hash so the pure comparator does not reject cross-artifact.
    golden = golden.model_copy(update={"strategy_hash": oracle.strategy_hash})
    report = compare_equity_curves(
        strategy_hash=oracle.strategy_hash, mock=golden, oracle=oracle
    )
    assert report.verdict is OracleVerdict.CONSISTENT, (
        f"calibration failed ({ts_code}): {report.detail}; "
        f"max_diff={report.max_abs_diff_bps:.2f}bps"
    )
    assert report.max_abs_diff_bps <= 25.0
