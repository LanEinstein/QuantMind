"""Tests for the baseline panel (QGR-2 build-new ⑥, anti long-beta)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.baselines import (
    BASELINE_PANEL,
    buy_and_hold_baseline,
    random_top_n_scores,
    run_baselines,
    single_asset_scores,
)
from scripts.factor_research.gate_backtest import PanelScoreProvider
from scripts.factor_research.gate_bar_source import PitBarSource

_CODES = ["600519.SH", "600036.SH", "601318.SH", "510300.SH"]
_DAYS = [f"202301{d:02d}" for d in range(4, 17)]


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
            fetch_time_utc=datetime(2023, 1, 17, tzinfo=UTC),
            metadata={"rows": len(df)},
        )
    )


def _seed(root: Path) -> SnapshotStore:
    store = SnapshotStore(root)
    for i, day in enumerate(_DAYS):
        stock_rows, etf_rows, limits = [], [], []
        for j, ts in enumerate(_CODES):
            px = 100.0 + j * 7.0 + i * 0.3
            row = {"ts_code": ts, "open": px, "high": px + 1, "low": px - 1,
                   "close": px, "vol": 80_000.0, "amount": px * 8e6}
            # ETFs (510300) live in fund_daily, stocks in daily.
            (etf_rows if ts == "510300.SH" else stock_rows).append(row)
            limits.append({"ts_code": ts, "pre_close": px,
                           "up_limit": round(px * 1.1, 2),
                           "down_limit": round(px * 0.9, 2)})
        _put(store, "daily", day, pd.DataFrame(stock_rows))
        _put(store, "fund_daily", day, pd.DataFrame(etf_rows))
        _put(store, "adj_factor", day, pd.DataFrame(
            [{"ts_code": ts, "adj_factor": 1.0} for ts in _CODES if ts != "510300.SH"]))
        _put(store, "stk_limit", day, pd.DataFrame(limits))
    return store


def test_baseline_panel_names_the_codex_p1_set() -> None:
    names = {b.name for b in BASELINE_PANEL}
    assert names == {
        "random_top5",
        "live_momentum_0p40",
        "pure_liquidity",
        "etf_only_510300",
        "csi300_etf_hold",
    }


def test_random_top_n_is_deterministic() -> None:
    uni = {d: list(_CODES) for d in _DAYS}
    a = random_top_n_scores(uni, seed=20260622)
    b = random_top_n_scores(uni, seed=20260622)
    assert a == b
    # a different seed reshuffles.
    c = random_top_n_scores(uni, seed=1)
    assert a != c


def test_single_asset_scores_holds_one_name() -> None:
    scores = single_asset_scores(_DAYS, "510300.SH")
    prov = PanelScoreProvider(scores)
    sig = prov.signals_asof(_DAYS[0])
    assert [c.code for c in sig.quant_candidates] == ["510300.SH"]


def test_run_baselines_executes_no_factor_baselines(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    src = PitBarSource(store=store, trading_days=_DAYS, universe=set(_CODES))
    results = run_baselines(
        bar_source=src,
        universe_by_day={d: list(_CODES) for d in _DAYS},
        etf_code="510300.SH",
        initial_capital_yuan=1_000_000.0,
        horizon=5,
    )
    # the three baselines that need no factor panel run now.
    assert {"random_top5", "etf_only_510300", "csi300_etf_hold"} <= set(results)
    for name, res in results.items():
        assert res.conservation_ok is True, name
        assert res.trading_days == len(_DAYS)


def test_csi300_hold_is_fully_invested_beta_not_a_capped_slot(tmp_path: Path) -> None:
    # codex P1: buy-and-hold beta must be ~fully invested in the ETF, NOT the
    # 15%-capped single-slot etf_only mechanics (which would be a too-easy hurdle
    # and a duplicate). In a rising fixture the full-beta hold gains much more.
    store = _seed(tmp_path)
    src = PitBarSource(store=store, trading_days=_DAYS, universe=set(_CODES))
    results = run_baselines(
        bar_source=src,
        universe_by_day={d: list(_CODES) for d in _DAYS},
        etf_code="510300.SH",
        initial_capital_yuan=1_000_000.0,
        horizon=5,
    )
    hold = results["csi300_etf_hold"]
    etf_only = results["etf_only_510300"]
    # fully invested → roughly the whole book is in the ETF at inception.
    assert hold.invested_fraction > 0.9
    # the 15%-capped slot baseline holds far less ⇒ far smaller absolute P&L.
    assert hold.net_pnl_yuan > etf_only.net_pnl_yuan * 3


def test_buy_and_hold_skips_unfillable_limit_up_opens(tmp_path: Path) -> None:
    # codex P2: every day's ETF open AT the upper limit is unfillable for a BUY
    # in the event-loop mechanics; buy-and-hold must honour that and NOT record an
    # entry (no fill, no invested capital) rather than enter at an unattainable price.
    store = SnapshotStore(tmp_path)
    for day in _DAYS:
        # ETF in fund_daily; open == up_limit every day → at_limit_up True.
        _put(store, "fund_daily", day, pd.DataFrame([{
            "ts_code": "510300.SH", "open": 110.0, "high": 110.0, "low": 110.0,
            "close": 110.0, "vol": 80_000.0, "amount": 8e6}]))
        _put(store, "stk_limit", day, pd.DataFrame([{
            "ts_code": "510300.SH", "pre_close": 100.0,
            "up_limit": 110.0, "down_limit": 90.0}]))
    src = PitBarSource(store=store, trading_days=_DAYS, universe={"510300.SH"})
    res = buy_and_hold_baseline(
        bar_source=src, asset_code="510300.SH", initial_capital_yuan=1_000_000.0,
    )
    assert res.fill_count == 0
    assert res.invested_fraction == 0.0
    assert res.net_pnl_yuan == 0.0
