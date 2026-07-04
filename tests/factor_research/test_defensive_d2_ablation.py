"""Unit tests for the D2 ablation's pure helpers (universe filter + diagnostic read).

The heavy 11-arm event-loop run is exercised by ``main --smoke-periods`` + the full
build;
here we pin (1) the frozen universe filter as a pure per-date row-mask (the sole
difference
between the A0 and D2 ranker tables), including the committed missing rules,
(2) that the D2 ranker table is a strict subset of the A0 ranker table (same reused
``build_ranker_table``), and (3) the pre-registered three-branch read truth table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.factor_research import exit_veto_panel as xv
from scripts.factor_research.defensive_d1_ablation import DefensiveArm
from scripts.factor_research.defensive_d2_ablation import (
    _read,
    apply_d2_universe_filter,
)

_NEUT_COLS = (
    "rev_1d_neut",
    "max_5d_neut",
    "turn_spike_neut",
    "ideal_amplitude_20d_neut",
)


def _neut_frame() -> pd.DataFrame:
    """One date, 10 names, distinct vol / max / dividend / quality so gates are
    decidable."""
    n = 10
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"c{i}.SH" for i in range(n)],
            "log_circ_mv": [10.0 + i for i in range(n)],
            # low vol = small index; high dividend = large dv; quality good for mid
            # names.
            "vol_20d": [float(i) for i in range(n)],  # 0..9
            "max_20d": [float(i) for i in range(n)],  # 0..9 (top decile = c9)
            "dv_ratio": [float(i) for i in range(n)],  # 0..9 (median split at ~4.5)
            "roe": [1.0] * n,
            "gpm": [50.0] * n,
        }
    )
    for col in _NEUT_COLS:
        frame[col] = rng.normal(size=n)
    return frame


def test_filter_is_a_pure_row_subset() -> None:
    frame = _neut_frame()
    out = apply_d2_universe_filter(frame)
    assert set(out.index) <= set(frame.index)  # only drops rows, never adds
    assert list(out.columns) == list(frame.columns)  # columns untouched
    # Applying the same boolean survivors twice is idempotent.
    again = apply_d2_universe_filter(out)
    assert set(again.index) <= set(out.index)


def test_filter_frozen_gate_logic() -> None:
    # vol keep ≤ 60th pct (0..9 → thr≈5.4 → keep c0..c5); lottery drop max ≥ 90th pct
    # (thr≈8.1 → drop c9, keep < thr); dividend branch dv ≥ median (thr≈4.5 → keep
    # c5..c9).
    # Intersection kept = vol_ok(c0..c5) ∧ lottery_ok(c0..c8) ∧ (dv≥4.5 OR quality).
    # quality: roe>0 ∧ gpm above bottom decile → all true here (roe=1, gpm=50 uniform →
    # gpm 10th pct == 50, gpm>50 is False → quality branch FALSE for all). So only the
    # dividend branch decides: kept = {c5} (vol_ok ∩ dv≥4.5), since c6..c9 fail vol_ok.
    out = apply_d2_universe_filter(_neut_frame())
    assert set(out["ts_code"]) == {"c5.SH"}


def test_filter_missing_rules() -> None:
    frame = _neut_frame()
    # c0 keeps vol/max low; give it high dividend so it passes but blank vol → dropped.
    frame.loc[0, "vol_20d"] = np.nan  # missing vol → dropped regardless
    frame.loc[0, "dv_ratio"] = 9.0
    # c1: missing max → dropped.
    frame.loc[1, "max_20d"] = np.nan
    frame.loc[1, "dv_ratio"] = 9.0
    out = apply_d2_universe_filter(frame)
    assert "c0.SH" not in set(out["ts_code"])
    assert "c1.SH" not in set(out["ts_code"])


def test_quality_branch_admits_when_dividend_missing() -> None:
    # Build a frame where a low-vol, non-lottery name has NO dividend but strong quality
    # (roe>0 ∧ gpm above the bottom decile) → admitted via branch two.
    n = 10
    rng = np.random.default_rng(1)
    frame = pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"c{i}.SH" for i in range(n)],
            "log_circ_mv": [10.0 + i for i in range(n)],
            "vol_20d": [float(i) for i in range(n)],
            "max_20d": [float(i) for i in range(n)],
            "dv_ratio": [np.nan] * n,  # nobody passes the dividend branch
            "roe": [1.0] * n,
            "gpm": [
                float(i) for i in range(n)
            ],  # bottom decile = c0 dropped by gpm floor
        }
    )
    for col in _NEUT_COLS:
        frame[col] = rng.normal(size=n)
    out = apply_d2_universe_filter(frame)
    # vol_ok (c0..c5), lottery_ok (c0..c8), quality (gpm>10th pct ≈ 0.9 → c1..c9). Kept
    # =
    # c1..c5 (dividend all NaN → branch one dead, branch two carries).
    assert set(out["ts_code"]) == {"c1.SH", "c2.SH", "c3.SH", "c4.SH", "c5.SH"}


def test_d2_ranker_table_is_subset_of_a0() -> None:
    frame = _neut_frame()
    a0 = xv.build_ranker_table(frame)
    d2 = xv.build_ranker_table(apply_d2_universe_filter(frame))
    a0_keys = set(zip(a0["date"], a0["ts_code"], strict=True))
    d2_keys = set(zip(d2["date"], d2["ts_code"], strict=True))
    assert d2_keys <= a0_keys


# --------------------------------------------------------------------------- #
# Three-branch read truth table.                                              #
# --------------------------------------------------------------------------- #


def _arm(label: str, net: float, returns: tuple[float, ...]) -> DefensiveArm:
    return DefensiveArm(
        label=label,
        slots=5,
        cap_percent=100,
        net_pnl_yuan=net,
        max_drawdown_pct=0.2,
        monthly_turnover=1.0,
        fill_count=10,
        avg_exposure=0.7,
        conservation_ok=True,
        dsr=0.01,
        period_returns=returns,
    )


def _regime(
    labels: list[str], bear: float, *, n: int = 4
) -> dict[str, dict[str, dict[str, float]]]:
    return {lb: {"bear": {"n": float(n), "sum_return": bear}} for lb in labels}


def _crash(
    labels: list[str], cum: float, *, n: int = 4
) -> dict[str, dict[str, dict[str, float]]]:
    return {lb: {"slice1": {"n": float(n), "cum_return": cum}} for lb in labels}


def _arms_for(*, d2_edge: bool, a0_edge: bool, net: float) -> dict[str, DefensiveArm]:
    strong = (0.02, 0.021, 0.019, 0.022, 0.018, 0.02, 0.021, 0.019)
    flat = (0.0,) * 8
    d2_ret = strong if d2_edge else flat
    a0_ret = strong if a0_edge else flat
    arms: dict[str, DefensiveArm] = {}
    for c in ("eq_5", "buf40_5"):
        arms[f"a0_{c}"] = _arm(f"a0_{c}", net, a0_ret)
        arms[f"d2_{c}"] = _arm(f"d2_{c}", net, d2_ret)
        arms[f"placebo_random_a0_{c}"] = _arm(f"placebo_random_a0_{c}", 0.0, flat)
        arms[f"placebo_random_d2_{c}"] = _arm(f"placebo_random_d2_{c}", 0.0, flat)
        arms[f"placebo_sizematched_d2_{c}"] = _arm(
            f"placebo_sizematched_d2_{c}", 0.0, flat
        )
    return arms


def _run_read(
    *, d2_edge: bool, a0_edge: bool, bear: float, net: float
) -> dict[str, object]:
    arms = _arms_for(d2_edge=d2_edge, a0_edge=a0_edge, net=net)
    labels = list(arms)
    return _read(arms, _regime(labels, bear), _crash(labels, 0.1 if net > 0 else -0.1))


def test_branch_a_when_d2_beats_placebo_and_owner_gates_pass() -> None:
    read = _run_read(d2_edge=True, a0_edge=True, bear=0.05, net=100.0)
    assert read["d2_beats_own_placebo_joint"] is True
    assert read["owner_gates_improved"] is True
    branch = read["branch_read"]  # type: ignore[index]
    assert branch["a"] is True
    assert branch["b"] is False


def test_branch_b_when_no_placebo_edge_but_sleeve_intact() -> None:
    read = _run_read(d2_edge=False, a0_edge=True, bear=0.05, net=100.0)
    assert read["d2_beats_own_placebo_joint"] is False
    branch = read["branch_read"]  # type: ignore[index]
    assert branch["b"] is True  # sleeve profile intact (bear ≥ 0)
    assert branch["a"] is False


def test_empty_bucket_is_untested_not_a_pass() -> None:
    # An arm that beats its placebo with positive net but has ZERO bear/crash periods
    # must NOT read as owner-gate-satisfied (codex: 0.0 default ≠ "survived"). Guarding
    # on n>0 keeps an untested crash/regime from inflating the promotion read.
    arms = _arms_for(d2_edge=True, a0_edge=True, net=100.0)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.0, n=0), _crash(labels, 0.0, n=0))
    d2 = read["d2"]["eq_5"]  # type: ignore[index]
    assert d2["bear_regime_nonneg"] is False  # n=0 → untested, not a pass
    assert d2["all_crash_slices_nonneg"] is False  # no tested slice
    assert read["owner_gates_improved"] is False
    assert read["branch_read"]["a"] is False  # type: ignore[index]


def test_branch_c_when_a0_fails_its_own_placebo() -> None:
    read = _run_read(d2_edge=False, a0_edge=False, bear=-0.05, net=-100.0)
    assert read["a0_beats_own_placebo_joint"] is False
    branch = read["branch_read"]  # type: ignore[index]
    assert branch["c"] is True
