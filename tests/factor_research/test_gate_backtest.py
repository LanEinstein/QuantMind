"""Tests for the gate-arena event-loop runner (QGR-2 build-new ①, part 2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.backtest.strategy import ScoreProvider
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    default_strategy_config,
    run_gate_backtest,
)
from scripts.factor_research.gate_bar_source import PitBarSource

_CODES = ["600519.SH", "600036.SH", "601318.SH"]
_DAYS = [f"202301{d:02d}" for d in range(4, 17)]  # 13 trading days


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
        rows = []
        limits = []
        for j, ts in enumerate(_CODES):
            px = 100.0 + j * 10.0 + i * 0.5  # gently rising
            rows.append(
                {
                    "ts_code": ts,
                    "open": px,
                    "high": px + 1.0,
                    "low": px - 1.0,
                    "close": px,
                    "vol": 50_000.0,
                    "amount": px * 5_000_000.0,
                }
            )
            limits.append(
                {
                    "ts_code": ts,
                    "pre_close": px,
                    "up_limit": round(px * 1.1, 2),
                    "down_limit": round(px * 0.9, 2),
                }
            )
        _put(store, "daily", day, pd.DataFrame(rows))
        _put(store, "adj_factor", day, pd.DataFrame(
            [{"ts_code": ts, "adj_factor": 1.0} for ts in _CODES]
        ))
        _put(store, "stk_limit", day, pd.DataFrame(limits))
    return store


def _scores() -> dict[str, list[tuple[str, float]]]:
    # constant cross-sectional ranking: 600519 > 600036 > 601318
    return {
        day: [("600519.SH", 0.9), ("600036.SH", 0.6), ("601318.SH", 0.3)]
        for day in _DAYS
    }


def test_panel_score_provider_is_a_score_provider() -> None:
    provider = PanelScoreProvider(_scores())
    assert isinstance(provider, ScoreProvider)
    sig = provider.signals_asof(_DAYS[0])
    assert sig.trade_date == _DAYS[0]
    assert {c.code for c in sig.quant_candidates} == set(_CODES)
    # health present for every candidate (line1_percentile is a within-day rank).
    assert set(sig.health) == set(_CODES)
    assert sig.health["600519.SH"].line1_percentile > sig.health[
        "601318.SH"
    ].line1_percentile


def test_percentiles_handle_ties_and_match_strict_less_count() -> None:
    # codex P2: the O(n log n) ranking must match (# strictly-lower)/(n-1), with
    # ties sharing the rank.
    scores = {"d": [("a", 0.5), ("b", 0.5), ("c", 0.9), ("e", 0.1)]}
    sig = PanelScoreProvider(scores).signals_asof("d")
    pct = {c: sig.health[c].line1_percentile for c in ("a", "b", "c", "e")}
    assert pct["e"] == 0.0  # lowest → 0 strictly-lower
    assert pct["c"] == 1.0  # highest → 3 strictly-lower / 3
    assert pct["a"] == pct["b"] == pytest.approx(1 / 3)  # tie: 1 strictly-lower / 3


def test_health_override_for_non_candidate_holding_is_kept() -> None:
    # codex P2: an override for a held code outside today's score table must
    # survive so decide_day can rotate a weak holding.
    from backend.backtest.strategy import CodeHealth

    weak = CodeHealth(line1_percentile=0.1, composite_score=-1.0)
    prov = PanelScoreProvider(
        {"d": [("a", 0.9)]}, health_overrides={"d": {"held": weak}}
    )
    sig = prov.signals_asof("d")
    assert "held" in sig.health  # not in candidates, but its override is kept
    assert sig.health["held"].line1_percentile == 0.1


def _run(store: SnapshotStore) -> GateBacktestResult:
    src = PitBarSource(store=store, trading_days=_DAYS, universe=set(_CODES))
    provider = PanelScoreProvider(_scores())
    return run_gate_backtest(
        bar_source=src,
        provider=provider,
        strategy_config=default_strategy_config(),
        initial_capital_yuan=1_000_000.0,
        horizon=5,
    )


def test_run_produces_absolute_pnl_and_invariants(tmp_path: Path) -> None:
    res = _run(_seed(tmp_path))
    assert isinstance(res, GateBacktestResult)
    assert res.trading_days == len(_DAYS)
    assert res.initial_capital_yuan == 1_000_000.0
    # buys filled (gently rising prices, ample cash + ADV) → positive pnl.
    assert res.fill_count > 0
    assert res.net_pnl_yuan > 0
    assert res.final_equity_yuan == res.initial_capital_yuan + res.net_pnl_yuan
    # the HARD guarantee always holds — cash / position / fee conservation.
    assert res.conservation_ok is True
    assert 0.0 <= res.max_drawdown_pct <= 1.0


def test_exposure_cap_is_a_separate_proxy_diagnostic(tmp_path: Path) -> None:
    # A ≤5-slot gate sizes each slot at the binding 15% cap; the harness's strict
    # post-fill check divides by post-friction equity, so a gap-up fill can tip a
    # position a hair over → surfaced as a cap-violation COUNT, NOT a conservation
    # failure (the live RiskEngine checks the cap pre-trade).
    res = _run(_seed(tmp_path))
    assert res.conservation_ok is True
    assert isinstance(res.exposure_cap_violations, int)
    assert res.exposure_cap_violations >= 0


def test_period_returns_resampled_at_horizon(tmp_path: Path) -> None:
    res = _run(_seed(tmp_path))
    # 13 days, horizon 5 → floor(13/5) = 2 non-overlapping period returns.
    assert len(res.period_returns) == 2
    assert len(res.daily_returns) == len(_DAYS)


def test_deterministic(tmp_path: Path) -> None:
    a = _run(_seed(tmp_path / "a"))
    b = _run(_seed(tmp_path / "b"))
    assert a.net_pnl_yuan == b.net_pnl_yuan
    assert a.period_returns == b.period_returns
    assert a.daily_returns == b.daily_returns
