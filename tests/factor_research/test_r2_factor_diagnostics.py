"""Tests for the round-2 diagnostic report helpers (R2-2 / S6)."""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.factor_ic_study import ICSummary
from scripts.factor_research.factor_lib import FACTOR_NAMES, R2_FACTOR_NAMES
from scripts.factor_research.fundamentals_pit import VintageAudit
from scripts.factor_research.r2_factor_diagnostics import (
    build_report,
    verdicts,
)


def _sm(factor: str, horizon: str, ic: float, t: float, expected: int) -> ICSummary:
    return ICSummary(
        factor=factor,
        horizon=horizon,
        ic_mean=ic,
        ic_std=0.1,
        icir=ic / 0.1,
        t_stat=t,
        hit_rate=0.6,
        n_dates=100,
        expected_sign=expected,
    )


def test_verdicts_pick_best_horizon_and_flag_signal() -> None:
    summaries = [
        _sm("roe", "fwd_ret_5d", 0.01, 1.0, 1),
        _sm("roe", "fwd_ret_20d", 0.05, 4.0, 1),  # strongest |t|
        _sm("np_yoy", "fwd_ret_5d", -0.01, -1.2, 1),  # misaligned + weak
    ]
    vs = {v.factor: v for v in verdicts(summaries, ("roe", "np_yoy"))}
    assert vs["roe"].best_horizon == "fwd_ret_20d"
    assert vs["roe"].has_signal is True
    assert vs["roe"].aligned is True
    assert vs["np_yoy"].has_signal is False  # |t|=1.2 < 3
    assert vs["np_yoy"].aligned is False  # IC<0 vs prior +1


def _diag_panel() -> pd.DataFrame:
    rows = []
    for date in ("20200103", "20200110"):
        for i in range(25):
            row: dict[str, object] = {"date": date, "code": f"6000{i:02d}"}
            for f in (*FACTOR_NAMES, *R2_FACTOR_NAMES):
                row[f] = float(i)
            row["industry_l1"] = "801080.SI" if i % 2 == 0 else "801180.SI"
            row["log_circ_mv"] = float(i) + 1.0
            row["fwd_ret_5d"] = float(-i)
            row["fwd_ret_10d"] = float(-i)
            row["fwd_ret_20d"] = float(-i)
            rows.append(row)
    return pd.DataFrame(rows)


def test_build_report_produces_all_sections() -> None:
    panel = _diag_panel()
    audit = VintageAudit(
        n_codes=5,
        n_code_periods=10,
        n_restated_code_periods=3,
        restatement_rate=0.3,
        ann_lag_days_median=30.0,
        restate_gap_days_median=120.0,
    )
    report = build_report(
        panel, audit, industry_coverage=1.0, winsor_quantile=0.01, min_obs=5
    )
    assert "Round-2 factor diagnostics" in report
    assert "vintage audit" in report.lower()
    assert "neutralized" in report.lower()
    assert "Collinearity" in report
    # restatement rate rendered
    assert "30.00%" in report
