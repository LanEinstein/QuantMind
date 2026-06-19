"""Tests for the R2-5 cost-stress engine cross-check."""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.benchmark_relative import CARRY_FACTORS
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME
from scripts.factor_research.full_engine_crosscheck import (
    _excess_max_drawdown,
    cross_check,
)


def _panel(dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        for code, sc, fwd in [
            ("a.SH", 3.0, 0.04),
            ("b.SH", 2.0, 0.01),
            ("c.SH", 1.0, -0.02),
        ]:
            row: dict[str, object] = {
                "date": d,
                "ts_code": code,
                "industry_l1": "801080.SI",
                "log_circ_mv": 10.0,
                "fwd_ret_5d": fwd,
            }
            for base in CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
                row[f"{base}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def test_excess_max_drawdown_counts_first_period_loss() -> None:
    # a first-period −10% then flat → 10% drawdown (peak includes the start).
    assert _excess_max_drawdown((-0.10, 0.0, 0.0)) > 0.09


def test_cross_check_friction_is_monotone_and_records_oracle() -> None:
    dates = ["20240105", "20240112", "20240119"]
    panel = _panel(dates)
    bench = {"a.SH": 0.34, "b.SH": 0.33, "c.SH": 0.33}
    idx = dict.fromkeys(dates, 0.0)
    res = cross_check(
        panel,
        lambda d: bench,
        idx,
        weights={f: 1.0 for f in CARRY_FACTORS},
        exposure_constraint="constituent_only",
        k=0.1,
        a_max=0.05,
        horizon=5,
    )
    assert res.n_periods == 3
    # stressed cost can only LOWER net excess (more friction never helps)
    assert res.stressed_total_excess <= res.base_total_excess + 1e-12
    assert res.excess_delta <= 1e-12
    assert res.monotone_friction is True
    # rqalpha oracle is recorded UNAVAILABLE (documented, not a silent pass)
    assert res.oracle_cross_checked is False
    assert "UNAVAILABLE" in res.oracle_status
