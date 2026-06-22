"""Tests for the PIT-backed event-loop BarSource (QGR-2 build-new ①, part 1).

A tiny K-002 snapshot store (daily + adj_factor + stk_limit) is seeded and the
:class:`PitBarSource` is asserted to (a) reconstruct qfq bars as-of the window
end, (b) carry the real ``stk_limit`` price limits (qfq-scaled so the limit gate
is adjustment-invariant), (c) classify board / transfer fee, and (d) expose a
look-ahead-free ``trading_days`` / ``bars_on`` honouring the event-loop contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.backtest.event_loop import BarSource
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.gate_bar_source import PitBarSource

_DAYS = ["20230104", "20230105", "20230106"]


def _put(store: SnapshotStore, endpoint: str, day: str, df: pd.DataFrame) -> None:
    from backend.data.historical_ingest.serialization import canonical_csv_bytes

    store.put(
        MarketDataSnapshot.create(
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
    )


def _seed(root: Path) -> SnapshotStore:
    store = SnapshotStore(root)
    # 600519.SH (SH main) flat factor; 300750.SZ (chuangye) has a 2x split on day3.
    closes = {"600519.SH": [100.0, 101.0, 102.0], "300750.SZ": [20.0, 21.0, 11.0]}
    factors = {"600519.SH": [1.0, 1.0, 1.0], "300750.SZ": [2.0, 2.0, 1.0]}
    for i, day in enumerate(_DAYS):
        daily = pd.DataFrame(
            [
                {
                    "ts_code": ts,
                    "open": px[i],
                    "high": px[i] + 1.0,
                    "low": px[i] - 1.0,
                    "close": px[i],
                    "vol": 50_000.0,
                    "amount": px[i] * 5_000_000.0,
                }
                for ts, px in closes.items()
            ]
        )
        adj = pd.DataFrame(
            [{"ts_code": ts, "adj_factor": f[i]} for ts, f in factors.items()]
        )
        limit = pd.DataFrame(
            [
                {
                    "ts_code": ts,
                    "pre_close": px[i],
                    "up_limit": round(px[i] * 1.1, 2),
                    "down_limit": round(px[i] * 0.9, 2),
                }
                for ts, px in closes.items()
            ]
        )
        _put(store, "daily", day, daily)
        _put(store, "adj_factor", day, adj)
        _put(store, "stk_limit", day, limit)
    return store


def test_is_a_barsource(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"600519.SH", "300750.SZ"}
    )
    assert isinstance(src, BarSource)
    assert src.trading_days() == tuple(_DAYS)


def test_qfq_adjustment_as_of_window_end(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"300750.SZ"}
    )
    # 300750 raw closes 20/21/11 with factors 2/2/1, asof factor = 1.0 (day3).
    # qfq(d) = raw * factor_d / factor_asof → 40/42/11 (yuan) → cents.
    bars = {d: src.bars_on(d)["300750.SZ"].close_cents for d in _DAYS}
    assert bars["20230104"] == 4000  # 20 * 2/1 = 40.00
    assert bars["20230105"] == 4200  # 21 * 2/1 = 42.00
    assert bars["20230106"] == 1100  # 11 * 1/1 = 11.00


def test_real_stk_limit_is_qfq_scaled(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(store=store, trading_days=_DAYS, universe={"300750.SZ"})
    bar = src.bars_on("20230104")["300750.SZ"]
    # raw up_limit 22.00 (=20*1.1), qfq ×(2/1) → 44.00 → 4400 cents.
    assert bar.limit_up_cents == 4400
    assert bar.limit_down_cents == 3600  # 18.00 * 2 = 36.00


def test_board_and_transfer_fee(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"600519.SH", "300750.SZ"}
    )
    bars = src.bars_on("20230104")
    assert bars["600519.SH"].board == "sh_main"
    assert bars["600519.SH"].transfer_fee_applies is False
    assert bars["300750.SZ"].board == "chuangye"
    assert bars["300750.SZ"].transfer_fee_applies is True


def test_universe_filter_excludes_others(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(store=store, trading_days=_DAYS, universe={"600519.SH"})
    bars = src.bars_on("20230104")
    assert set(bars) == {"600519.SH"}


def test_adv_volume_is_trailing_mean_in_shares(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"600519.SH"}, adv_window=20
    )
    # Tushare daily vol is in 手 (100 shares); 50_000 手 → 5_000_000 shares ADV.
    bar = src.bars_on("20230106")["600519.SH"]
    assert bar.adv_volume == pytest.approx(5_000_000.0)


def test_adv_is_as_of_no_future_leak(tmp_path: Path) -> None:
    # codex P1: ADV on an early day must use only volumes ≤ that day — never the
    # full (future-inclusive) history. Volume rises each day; with a window wider
    # than the run, day-0 ADV must equal day-0 volume, NOT the mean of all days.
    store = SnapshotStore(tmp_path)
    for i, day in enumerate(_DAYS):
        vol_hands = (i + 1) * 1000.0  # 1000, 2000, 3000 手
        _put(store, "daily", day, pd.DataFrame([{
            "ts_code": "600519.SH", "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "vol": vol_hands, "amount": 1e7,
        }]))
        _put(store, "adj_factor", day, pd.DataFrame(
            [{"ts_code": "600519.SH", "adj_factor": 1.0}]))
        _put(store, "stk_limit", day, pd.DataFrame([{
            "ts_code": "600519.SH", "pre_close": 100.0,
            "up_limit": 110.0, "down_limit": 90.0}]))
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"600519.SH"}, adv_window=20
    )
    # day 0: only 1000 手 known → 100_000 shares (the buggy full-history mean of
    # 1000/2000/3000 手 would give 200_000).
    assert src.bars_on(_DAYS[0])["600519.SH"].adv_volume == pytest.approx(100_000.0)
    # day 2: trailing mean of 1000/2000/3000 手 = 2000 手 → 200_000 shares.
    assert src.bars_on(_DAYS[2])["600519.SH"].adv_volume == pytest.approx(200_000.0)


def test_etf_bars_loaded_from_fund_daily(tmp_path: Path) -> None:
    # codex P1: ETFs live in fund_daily (not daily) and are absent from
    # adj_factor (flat factor 1.0). Without this the ETF beta baselines get no
    # bars. Seed an ETF ONLY into fund_daily and assert a bar is produced.
    store = SnapshotStore(tmp_path)
    for day in _DAYS:
        _put(store, "daily", day, pd.DataFrame([{  # a stock, so daily is non-empty
            "ts_code": "600519.SH", "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "vol": 50_000.0, "amount": 1e7}]))
        _put(store, "adj_factor", day, pd.DataFrame(
            [{"ts_code": "600519.SH", "adj_factor": 1.0}]))
        _put(store, "fund_daily", day, pd.DataFrame([{
            "ts_code": "510300.SH", "open": 4.0, "high": 4.05, "low": 3.95,
            "close": 4.02, "vol": 200_000.0, "amount": 8e5}]))
        _put(store, "stk_limit", day, pd.DataFrame([{
            "ts_code": "510300.SH", "pre_close": 4.0,
            "up_limit": 4.4, "down_limit": 3.6}]))
    src = PitBarSource(
        store=store, trading_days=_DAYS, universe={"600519.SH", "510300.SH"}
    )
    bars = src.bars_on(_DAYS[0])
    assert "510300.SH" in bars  # ETF bar sourced from fund_daily
    etf = bars["510300.SH"]
    assert etf.board == "etf"
    assert etf.close_cents == 402  # raw fund price ¥4.02 (flat factor 1.0)
    assert "600519.SH" in bars  # stock still from daily


def test_missing_day_raises(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(store=store, trading_days=_DAYS, universe={"600519.SH"})
    with pytest.raises(KeyError):
        src.bars_on("20991231")


def test_nan_limit_row_falls_back_not_crash(tmp_path: Path) -> None:
    # codex P2: a blank/NaN stk_limit must fall back to synthetic limits, never
    # crash bars_on for the whole day.
    store = SnapshotStore(tmp_path)
    for day in _DAYS:
        _put(store, "daily", day, pd.DataFrame([{
            "ts_code": "600519.SH", "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "vol": 50_000.0, "amount": 1e7}]))
        _put(store, "adj_factor", day, pd.DataFrame(
            [{"ts_code": "600519.SH", "adj_factor": 1.0}]))
        _put(store, "stk_limit", day, pd.DataFrame([{
            "ts_code": "600519.SH", "pre_close": 100.0,
            "up_limit": float("nan"), "down_limit": float("nan")}]))
    src = PitBarSource(store=store, trading_days=_DAYS, universe={"600519.SH"})
    bar = src.bars_on(_DAYS[0])["600519.SH"]
    # synthetic fallback used (≈ ±21% of close) → bar produced, not a crash.
    assert bar.limit_up_cents > bar.close_cents
    assert bar.limit_down_cents < bar.close_cents
