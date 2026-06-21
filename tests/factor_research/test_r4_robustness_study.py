"""Tests for the R5 train_val robustness study (dev evidence, train_val only)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import R4_CARRY_FACTORS
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME
from scripts.factor_research.r4_robustness_study import (
    ANALYST_FACTORS,
    DOMINANT_ANALYST,
    K_SWEEP,
    build_report,
    factor_ablation,
    tilt_sweep,
)


def _panel(dates: list[str]) -> pd.DataFrame:
    """A cross-section over the 16-factor round-4 carry.

    Most factors rank with the score (and with fwd_ret), but ``rev_diff`` is
    DELIBERATELY anti-aligned so the composite genuinely RESPONDS to dropping it —
    otherwise (all factors identically ranked) the ablation would be a no-op and
    the ablation test could not detect a broken zeroing.
    """
    names = ["a", "b", "c", "d", "e", "f"]
    base = {"a": 6.0, "b": 5.0, "c": 4.0, "d": 3.0, "e": 2.0, "f": 1.0}
    rows = []
    for d in dates:
        for nm in names:
            sc = base[nm]
            row: dict[str, object] = {
                "date": d,
                "code": f"{nm}00000",
                "ts_code": f"{nm}00000.SH",
                "industry_l1": "801080.SI",
                "log_circ_mv": math.log(1e6 * sc),
                "fwd_ret_5d": 0.01 * sc,
            }
            for f in R4_CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[f].attractive_high else -1.0
                # rev_diff anti-aligned (opposite ranking) so the dominant-factor
                # ablation measurably changes the composite.
                flip = -1.0 if f == DOMINANT_ANALYST else 1.0
                row[f"{f}_neut"] = flip * sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def _bench() -> dict[str, float]:
    return {f"{nm}00000.SH": 1.0 / 6 for nm in ["a", "b", "c", "d", "e", "f"]}


def _frozen_weights() -> dict[str, float]:
    # rev_diff DOMINANT enough to drive the composite ranking (so that, paired
    # with its anti-aligned _neut in _panel, dropping it flips the tilt and the
    # ablation is observable even under the aggressive k=0.20/a_max=0.04 clip).
    w = {f: 0.02 for f in R4_CARRY_FACTORS}
    w[DOMINANT_ANALYST] = 0.70
    return w


def test_tilt_sweep_runs_every_k() -> None:
    panel = _panel([f"202401{i:02d}" for i in range(1, 11)])
    bench = _bench()
    idx = dict.fromkeys(panel["date"].unique(), 0.0)
    sweep = tilt_sweep(
        panel,
        lambda d: bench,
        idx,
        weights=_frozen_weights(),
        a_max=0.04,
        constraint="constituent_only",
        nonconst_cap=0.10,
        horizon=5,
    )
    assert set(sweep) == {f"k={k:.2f}" for k in K_SWEEP}
    for s in sweep.values():
        assert "total_excess" in s and "information_ratio" in s


def test_factor_ablation_zeroes_the_named_factors() -> None:
    # drop_rev_diff must differ from frozen_full when rev_diff carries weight;
    # no_analyst_block zeros all four analyst survivors.
    panel = _panel([f"202401{i:02d}" for i in range(1, 11)])
    bench = _bench()
    idx = dict.fromkeys(panel["date"].unique(), 0.0)
    ablation = factor_ablation(
        panel,
        lambda d: bench,
        idx,
        weights=_frozen_weights(),
        k=0.20,
        a_max=0.04,
        constraint="constituent_only",
        nonconst_cap=0.10,
        horizon=5,
    )
    assert set(ablation) == {"frozen_full", "drop_rev_diff", "no_analyst_block"}
    assert DOMINANT_ANALYST in ANALYST_FACTORS
    # dropping the (anti-aligned) dominant factor MUST change the composite's
    # excess — proving the zeroing actually takes effect (not a no-op).
    assert ablation["drop_rev_diff"]["total_excess"] != pytest.approx(
        ablation["frozen_full"]["total_excess"]
    )
    assert ablation["no_analyst_block"]["total_excess"] != pytest.approx(
        ablation["frozen_full"]["total_excess"]
    )


def test_build_report_structure_and_sentinel_flags() -> None:
    panel = _panel([f"202401{i:02d}" for i in range(1, 13)])
    bench = _bench()
    idx = dict.fromkeys(panel["date"].unique(), 0.0)
    report = build_report(
        panel,
        lambda d: bench,
        idx,
        weights=_frozen_weights(),
        k=0.20,
        a_max=0.04,
        constraint="constituent_only",
        nonconst_cap=0.10,
        horizon=5,
        seeds=(1, 2),
    )
    keys = {f"k={k:.2f}" for k in K_SWEEP}
    assert set(report.tilt_sweep) == keys
    assert set(report.sentinel_ir_by_k) == keys
    assert set(report.frozen_ir_by_k) == keys
    # beaten flags are booleans (real IR > best noise IR ⇒ sentinel passes)
    assert all(isinstance(v, bool) for v in report.sentinel_beaten_by_k.values())
    assert report.n_train_val_dates == 12
