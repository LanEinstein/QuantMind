"""Tests for the §3.8B bottom-confirmation gate diagnostics (conditional IC).

A gate is validated by its forward-return DISCRIMINATION (confirmed vs not),
not a rank-IC ranking axis. Synthetic panel: confirmed names earn a higher
forward return → the per-date spread is positive with a real t-stat; the dip-pool
mask and the cyq_perf ablation are exercised deterministically.
"""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.bottom_confirmation_diagnostics import (
    build_report,
    dip_pool_mask,
    group_spread_series,
    spread_stats,
)


def _panel(n_dates: int = 30, n_codes: int = 40) -> pd.DataFrame:
    """Confirmed (flag=1) names get +2% forward; not-confirmed (flag=0) get 0%."""
    rows = []
    for di in range(n_dates):
        date = f"2019{di + 1:04d}"
        for ci in range(n_codes):
            confirmed = 1.0 if ci % 2 == 0 else 0.0
            rows.append(
                {
                    "date": date,
                    "code": f"{ci:06d}",
                    "ret_20d": -0.2 + ci * 0.01,  # spread across the dip terciles
                    "bc_core_confirmed": confirmed,
                    "bc_full_confirmed": confirmed,
                    "bc_above_cost_band": confirmed,
                    "bc_vol_dryup": confirmed,
                    "bc_cost_premium": 0.1 if confirmed else -0.1,
                    "bc_winner_rate": 70.0 if confirmed else 40.0,
                    "fwd_ret_5d": 0.02 if confirmed else 0.0,
                    "fwd_ret_10d": 0.02 if confirmed else 0.0,
                    "fwd_ret_20d": 0.02 if confirmed else 0.0,
                }
            )
    return pd.DataFrame(rows)


class TestGroupSpreadSeries:
    def test_positive_spread_when_confirmed_outperforms(self) -> None:
        spreads = group_spread_series(
            _panel(), "bc_core_confirmed", "fwd_ret_5d", min_group=5
        )
        assert len(spreads) == 30
        assert all(abs(s - 0.02) < 1e-9 for s in spreads)

    def test_too_thin_group_skipped(self) -> None:
        spreads = group_spread_series(
            _panel(n_codes=6), "bc_core_confirmed", "fwd_ret_5d", min_group=5
        )
        assert spreads == []  # only 3 confirmed / 3 not per date < min_group


class TestSpreadStats:
    def test_t_stat_positive_and_finite(self) -> None:
        s = spread_stats([0.02] * 20)
        assert s.mean_spread > 0
        assert s.n_dates == 20
        # zero variance → t defined as 0 (fail-closed, not inf)
        assert s.t_stat == 0.0

    def test_empty_is_zero(self) -> None:
        s = spread_stats([])
        assert s.n_dates == 0
        assert s.mean_spread == 0.0


class TestDipPoolMask:
    def test_bottom_tercile_selected(self) -> None:
        panel = _panel(n_dates=1, n_codes=30)
        mask = dip_pool_mask(panel, quantile=1 / 3)
        sub = panel[mask]
        # ~bottom third by ret_20d (the most pulled-back names).
        assert 0 < len(sub) <= 12
        assert sub["ret_20d"].max() <= panel["ret_20d"].quantile(1 / 3) + 1e-9


class TestBuildReport:
    def test_smoke_runs_and_mentions_caveats(self) -> None:
        report = build_report(_panel())
        assert "bottom-confirmation" in report.lower()
        assert "cyq_perf" in report
        assert "资金流" in report  # the deferred-component disclosure
        assert "ablat" in report.lower()  # cyq ablation section present
