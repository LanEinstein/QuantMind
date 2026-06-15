"""AE-002 — PIT same-source exporter (Option B) unit tests.

Seeds a tiny K-002 snapshot store with ``daily`` + ``adj_factor`` frames and
asserts the exporter (a) reconstructs qfq bars, (b) writes a byte-stable
content-addressed ``bars.csv`` whose sha256 is pinned into ``spec.json``,
(c) hash-verifies the strategy artifact (fail-closed), and (d) maps board /
transfer-fee / friction faithfully.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.backtest import rqalpha_protocol as proto
from backend.backtest.pit_export import (
    BrokerFriction,
    PitExportError,
    SnapshotPitExporter,
)
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.strategy_evolution.backtest_oracle import BacktestSpec

_DAYS = ["20230104", "20230105", "20230106"]
_FRICTION = BrokerFriction(
    commission_rate=0.00015,
    min_commission=5.0,
    stamp_tax_rate=0.001,
    transfer_fee_rate=0.0000341,
    slippage_bps_by_board={"sh_main": 1.5, "sz_main": 1.5, "chuangye": 3.5, "etf": 1.5},
)


def _put_frame(store: SnapshotStore, endpoint: str, day: str, df: pd.DataFrame) -> None:
    from backend.data.historical_ingest.serialization import canonical_csv_bytes

    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=endpoint,
        params={"trade_date": day},
        trade_date=day,
        raw_payload=canonical_csv_bytes(df),
        encoding="csv",
        compression="none",
        fetch_time_utc=datetime(2023, 1, 6, tzinfo=UTC),
        metadata={"rows": len(df)},
    )
    store.put(snap)


def _seed_store(root: Path) -> SnapshotStore:
    store = SnapshotStore(root)
    closes = {"600519.SH": [100.0, 101.0, 102.0], "000001.SZ": [10.0, 10.5, 11.0]}
    for i, day in enumerate(_DAYS):
        daily = pd.DataFrame(
            [
                {
                    "ts_code": ts,
                    "open": px[i],
                    "high": px[i],
                    "low": px[i],
                    "close": px[i],
                    "vol": 1_000_000.0,
                    "amount": px[i] * 1_000_000.0,
                }
                for ts, px in closes.items()
            ]
        )
        adj = pd.DataFrame(
            [{"ts_code": ts, "adj_factor": 1.0} for ts in closes]
        )
        _put_frame(store, "daily", day, daily)
        _put_frame(store, "adj_factor", day, adj)
    return store


def _o(date: str, ts: str, side: str, shares: int) -> dict[str, object]:
    return {"trade_date": date, "ts_code": ts, "side": side, "shares": shares}


def _write_strategy(path: Path, orders: list[dict[str, object]]) -> str:
    raw = json.dumps({"schema_version": 1, "orders": orders}, sort_keys=True).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _spec(tmp_path: Path, *, strategy_hash: str) -> BacktestSpec:
    return BacktestSpec(
        strategy_hash=strategy_hash,
        strategy_source_path=str(tmp_path / "strategy.json"),
        start_date="20230104",
        end_date="20230106",
        initial_capital=1_000_000.0,
    )


def _export_spec(
    exporter: SnapshotPitExporter, tmp_path: Path, h: str
) -> dict[str, object]:
    workdir = tmp_path / "wd"
    exporter.export(_spec(tmp_path, strategy_hash=h), workdir)
    return json.loads((workdir / proto.SPEC_FILENAME).read_text())


class TestExport:
    def test_export_writes_spec_and_bars(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        h = _write_strategy(
            tmp_path / "strategy.json",
            [
                _o("20230104", "600519.SH", "BUY", 1000),
                _o("20230106", "600519.SH", "SELL", 1000),
            ],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        workdir = tmp_path / "wd"
        manifest = exporter.export(_spec(tmp_path, strategy_hash=h), workdir)

        bars_bytes = (workdir / proto.BARS_FILENAME).read_bytes()
        assert hashlib.sha256(bars_bytes).hexdigest() == manifest.bars_sha256
        spec_doc = json.loads((workdir / proto.SPEC_FILENAME).read_text())
        assert spec_doc[proto.SPEC_BARS_SHA256] == manifest.bars_sha256
        assert spec_doc[proto.SPEC_STRATEGY_HASH] == h
        # One instrument (600519 -> XSHG main board, no transfer fee).
        ins = spec_doc[proto.SPEC_INSTRUMENTS]
        assert len(ins) == 1
        assert ins[0][proto.INS_ORDER_BOOK_ID] == "600519.XSHG"
        assert ins[0][proto.INS_BOARD] == "sh_main"
        assert ins[0][proto.INS_TRANSFER_FEE_APPLIES] is False
        # Orders carry the converted order_book_id + side + shares.
        sides = {o[proto.ORD_SIDE] for o in spec_doc[proto.SPEC_ORDERS]}
        assert sides == {"BUY", "SELL"}
        assert manifest.trading_days == 3

    def test_friction_passthrough(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        h = _write_strategy(
            tmp_path / "strategy.json",
            [_o("20230104", "000001.SZ", "BUY", 100)],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        spec_doc = _export_spec(exporter, tmp_path, h)
        fric = spec_doc[proto.SPEC_FRICTION]
        assert fric[proto.FRIC_COMMISSION_RATE] == 0.00015
        assert fric[proto.FRIC_TRANSFER_FEE_RATE] == 0.0000341
        # 000001 -> SZ main board -> transfer fee applies.
        assert spec_doc[proto.SPEC_INSTRUMENTS][0][proto.INS_TRANSFER_FEE_APPLIES]

    def test_qfq_applies_adjustment_factor(self, tmp_path: Path) -> None:
        # adj_factor 1.1 on day1, 1.21 on day2, 1.21 on day3 (asof=day3) =>
        # qfq(day1) = raw(100) * 1.1 / 1.21.
        store = SnapshotStore(tmp_path / "store")
        factors = {"20230104": 1.1, "20230105": 1.21, "20230106": 1.21}
        for day in _DAYS:
            daily = pd.DataFrame(
                [{"ts_code": "600519.SH", "open": 100.0, "high": 100.0,
                  "low": 100.0, "close": 100.0, "vol": 1.0, "amount": 100.0}]
            )
            adj = pd.DataFrame([{"ts_code": "600519.SH", "adj_factor": factors[day]}])
            _put_frame(store, "daily", day, daily)
            _put_frame(store, "adj_factor", day, adj)
        h = _write_strategy(
            tmp_path / "strategy.json",
            [_o("20230104", "600519.SH", "BUY", 100)],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        workdir = tmp_path / "wd"
        exporter.export(_spec(tmp_path, strategy_hash=h), workdir)
        bars = pd.read_csv(workdir / proto.BARS_FILENAME)
        day1 = bars[bars["trade_date"] == 20230104].iloc[0]
        assert round(float(day1["close"]), 4) == round(100.0 * 1.1 / 1.21, 4)
        day3 = bars[bars["trade_date"] == 20230106].iloc[0]
        assert float(day3["close"]) == 100.0  # asof anchor -> factor cancels


class TestFailClosed:
    def test_strategy_hash_mismatch_raises(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        _write_strategy(
            tmp_path / "strategy.json",
            [_o("20230104", "600519.SH", "BUY", 100)],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        with pytest.raises(PitExportError, match="sha256"):
            exporter.export(_spec(tmp_path, strategy_hash="b" * 64), tmp_path / "wd")

    def test_missing_artifact_raises(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        with pytest.raises(PitExportError, match="missing"):
            exporter.export(_spec(tmp_path, strategy_hash="a" * 64), tmp_path / "wd")

    def test_no_bars_in_window_raises(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        h = _write_strategy(
            tmp_path / "strategy.json",
            [_o("20230104", "601318.SH", "BUY", 100)],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=_DAYS
        )
        with pytest.raises(PitExportError, match="no PIT"):
            exporter.export(_spec(tmp_path, strategy_hash=h), tmp_path / "wd")

    def test_empty_calendar_window_raises(self, tmp_path: Path) -> None:
        store = _seed_store(tmp_path / "store")
        h = _write_strategy(
            tmp_path / "strategy.json",
            [_o("20230104", "600519.SH", "BUY", 100)],
        )
        exporter = SnapshotPitExporter(
            snapshot_store=store, friction=_FRICTION, calendar=["20240101"]
        )
        with pytest.raises(PitExportError, match="no calendar"):
            exporter.export(_spec(tmp_path, strategy_hash=h), tmp_path / "wd")
