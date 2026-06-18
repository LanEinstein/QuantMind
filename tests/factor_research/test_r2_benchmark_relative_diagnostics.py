"""Tests for the R2-3 benchmark-relative diagnostics helpers (T4)."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import CARRY_FACTORS
from scripts.factor_research.benchmark_weights import BenchmarkWeightsPIT
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME
from scripts.factor_research.r2_benchmark_relative_diagnostics import (
    build_index_returns,
    build_report,
    load_benchmark_before,
)


def test_load_benchmark_before_excludes_test_window(tmp_path: object) -> None:
    # codex P1: the streaming loader must never materialize a date >= ceiling
    # (the locked test window), even though it lives in the same CSV file.
    from pathlib import Path

    csv_path = Path(str(tmp_path)) / "csi300.csv"
    csv_path.write_text(
        "trade_date,close\n"
        "20250430,4000.0\n"  # train_val
        "20250604,4100.0\n"  # test_start — must be excluded
        "20260612,4200.0\n",  # test — must be excluded
        encoding="utf-8",
    )
    out = load_benchmark_before(str(csv_path), "20250604")
    assert out == {"20250430": 4000.0}  # only the pre-test row


def test_build_index_returns_horizon_exact() -> None:
    bench = {
        "20240102": 100.0,
        "20240103": 101.0,
        "20240104": 102.0,
        "20240105": 103.0,
    }
    out = build_index_returns(bench, ["20240102", "20240103"], horizon=2)
    assert out["20240102"] == pytest.approx(102.0 / 100.0 - 1.0)
    assert out["20240103"] == pytest.approx(103.0 / 101.0 - 1.0)
    # a date whose d+horizon bar is missing is omitted
    assert "20240104" not in out


_N_NAMES = 25  # >= neutralize_panel default min_obs (20) so the tilt is live


def _panel() -> pd.DataFrame:
    # RAW carry-factor columns (build_report neutralizes them itself); constant
    # size + single industry so the neutralized residual just demeans the factor
    # (rank order preserved → the composite tilt is live). >=20 names so
    # neutralize_panel's min_obs gate does not blank every score (codex P3).
    rows = []
    for date in ("20240105", "20240112"):
        for i in range(_N_NAMES):
            row: dict[str, object] = {
                "date": date,
                "code": f"{600000 + i}",
                "ts_code": f"{600000 + i}.SH",
                "industry_l1": "801080.SI",
                "log_circ_mv": 10.0,
                "fwd_ret_5d": 0.01 * i,
            }
            for base in CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
                row[base] = sign * float(i)
            rows.append(row)
    return pd.DataFrame(rows)


def test_build_report_smoke() -> None:
    panel = _panel()
    codes = [f"{600000 + i}.SH" for i in range(_N_NAMES)]
    bench_pit = BenchmarkWeightsPIT(
        by_publish={"20231229": {c: 1.0 / len(codes) for c in codes}},
        publish_dates=("20231229",),
    )
    index_returns = {"20240105": 0.0, "20240112": 0.0}
    report = build_report(panel, bench_pit, index_returns, horizon=5)
    assert "benchmark-relative diagnostics" in report.lower()
    assert "RESEARCH ONLY" in report
    assert "Honest read" in report
    # the primary grid is rendered (3 k × 3 a_max)
    assert (
        report.count("| 0.05 |") + report.count("| 0.10 |") + report.count("| 0.20 |")
        >= 3
    )
    # regression guard: the tilt must be LIVE — compare the METRIC columns (drop
    # the leading k/a_max cells), so an inert composite (identical metrics) is
    # caught even though the k/a_max prefixes always differ (codex P3).
    grid_rows = [ln for ln in report.splitlines() if ln.startswith("| 0.")]
    metric_cells = {"|".join(ln.split("|")[3:]) for ln in grid_rows}
    assert len(metric_cells) > 1  # a_max actually moves the realised metrics
    # the tilt actually traded → non-zero gross active (col 9), not inert
    gross_active_cells = {ln.split("|")[9].strip() for ln in grid_rows}
    assert gross_active_cells != {"0.00%"}
