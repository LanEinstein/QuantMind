"""Tests for the R2-4 anchored-WF + combinatorial purged-CV robustness module."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import BenchmarkRelativeResult
from scripts.factor_research.locked_split import LockedSplit, SacredTestAccessError
from scripts.factor_research.walk_forward_eval import (
    anchored_walk_forward,
    build_report,
    combinatorial_purged_cv,
    evaluate_walk_forward,
)


def _result(excess: list[float]) -> BenchmarkRelativeResult:
    dates = tuple(f"2024{i:04d}" for i in range(len(excess)))
    return BenchmarkRelativeResult(
        n_periods=len(excess),
        total_excess=0.0,
        annual_excess=0.0,
        tracking_error=0.0,
        information_ratio=0.0,
        avg_turnover=0.0,
        avg_gross_active=0.0,
        avg_forced_underweight=0.0,
        mean_net_active=0.0,
        mean_size_active=0.0,
        mean_max_industry_active=0.0,
        excess_returns=tuple(excess),
        dates=dates,
    )


def test_anchored_walk_forward_is_expanding() -> None:
    # positive mean with non-zero variance (a constant series → IR undefined → 0)
    excess = [0.01 + 0.005 * (i % 2) for i in range(20)]
    folds = anchored_walk_forward(excess, n_folds=5, horizon=5)
    assert len(folds) == 5
    sizes = [f.n_periods for f in folds]
    assert sizes == [4, 8, 12, 16, 20]  # expanding, last covers all
    assert all(f.information_ratio > 0 for f in folds)  # all-positive excess


def test_anchored_too_few_periods_empty() -> None:
    assert anchored_walk_forward([0.01, 0.02], n_folds=5) == []


def test_combinatorial_purged_cv_counts_and_embargo() -> None:
    excess = [0.01] * 20
    paths = combinatorial_purged_cv(excess, n_groups=10, k=2, embargo=1, horizon=5)
    assert len(paths) == 45  # C(10, 2)
    # each block is size 2; embargo=1 drops the first → 1 period/block → 2/path
    assert all(p.n_periods == 2 for p in paths)


def test_combinatorial_purged_cv_degenerate_empty() -> None:
    assert combinatorial_purged_cv([0.01] * 3, n_groups=10, k=2) == []
    assert combinatorial_purged_cv([0.01] * 20, n_groups=10, k=10) == []


def test_combinatorial_ir_distribution_signs() -> None:
    # alternating sign excess → CPCV IR distribution straddles zero.
    excess = [0.02 if i % 2 == 0 else -0.02 for i in range(40)]
    paths = combinatorial_purged_cv(excess, n_groups=10, k=2, embargo=0, horizon=5)
    irs = [p.information_ratio for p in paths]
    assert any(x <= 0 for x in irs)  # not uniformly positive


def test_build_report_assembles_cpcv_stats() -> None:
    res = _result([0.01 + 0.005 * (i % 2) for i in range(30)])  # positive, var>0
    rep = build_report(res, horizon=5, n_folds=5, n_groups=10, cpcv_k=2, embargo=0)
    assert rep.n_periods == 30
    assert len(rep.anchored) == 5
    assert len(rep.cpcv_paths) == 45
    assert rep.cpcv_ir_frac_positive == pytest.approx(1.0)  # all-positive excess
    assert rep.cpcv_ir_mean > 0


def _tiny_panel(dates: list[str]) -> pd.DataFrame:
    from scripts.factor_research.benchmark_relative import CARRY_FACTORS
    from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME

    rows = []
    for d in dates:
        for code, sc in [("a.SH", 3.0), ("b.SH", 2.0), ("c.SH", 1.0)]:
            row: dict[str, object] = {
                "date": d,
                "ts_code": code,
                "industry_l1": "801080.SI",
                "log_circ_mv": 10.0,
                "fwd_ret_5d": 0.01,
            }
            for base in CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
                row[f"{base}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def test_evaluate_walk_forward_firewall_rejects_test_date() -> None:
    # defence-in-depth: a panel containing a sacred test date must fail closed.
    split = LockedSplit(
        train_val_dates=("20240105",),
        embargo_dates=(),
        test_dates=("20250604",),
    )
    from scripts.factor_research.benchmark_relative import CARRY_FACTORS

    panel = _tiny_panel(["20240105", "20250604"])
    bench = {"a.SH": 0.34, "b.SH": 0.33, "c.SH": 0.33}
    with pytest.raises(SacredTestAccessError):
        evaluate_walk_forward(
            panel,
            lambda d: bench,
            {"20240105": 0.0, "20250604": 0.0},
            weights={f: 1.0 for f in CARRY_FACTORS},
            split=split,
        )
