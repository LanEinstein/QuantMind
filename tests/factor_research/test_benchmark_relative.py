"""Tests for the benchmark-relative construction + excess backtest (R2-3 / T2)."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import (
    CARRY_FACTORS,
    benchmark_relative_backtest,
    build_active_weights,
    composite_score,
    drift_weights,
    weight_turnover,
)


def _group(scores: dict[str, float], *, with_neut: bool = True) -> pd.DataFrame:
    """A one-date cross-section where every carry factor's _neut == the score
    (so the composite is monotonic in the score) for the given ts_codes."""
    rows = []
    for code, sc in scores.items():
        row: dict[str, object] = {"ts_code": code, "industry_l1": "801080.SI"}
        for base in CARRY_FACTORS:
            # attractive-high factors take +sc; attractive-low take -sc so the
            # oriented composite rises with sc regardless of orientation.
            from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME

            sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
            row[f"{base}_neut"] = (sign * sc) if with_neut else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def _weights() -> dict[str, float]:
    return {f: 1.0 for f in CARRY_FACTORS}


def test_composite_score_monotonic_and_nan_on_incomplete() -> None:
    g = _group({"a.SH": 3.0, "b.SH": 2.0, "c.SH": 1.0})
    score = composite_score(g, _weights())
    assert score["a.SH"] > score["b.SH"] > score["c.SH"]
    # a name missing all neut factors → NaN composite
    g2 = _group({"a.SH": 1.0}, with_neut=False)
    assert pd.isna(composite_score(g2, _weights())["a.SH"])


def test_composite_score_raises_on_absent_weighted_column() -> None:
    # A weighted factor whose _neut column is entirely absent → fail closed
    # (codex P2: never silently rank on a biased partial vector).
    g = pd.DataFrame({"ts_code": ["a.SH"], "roe_neut": [1.0]})
    with pytest.raises(ValueError, match="np_yoy_neut"):
        composite_score(g, {"roe": 1.0, "np_yoy": 1.0})


def test_drift_weights_and_static_target_still_trades() -> None:
    # Two names 50/50; a +10% vs −10% holding-period return drifts them to
    # 55/45, so rebalancing back to the unchanged 50/50 target DOES trade
    # (codex P2: target-to-target turnover would wrongly read 0).
    drifted = drift_weights({"a": 0.5, "b": 0.5}, {"a": 0.10, "b": -0.10}, 0.0)
    assert drifted["a"] == pytest.approx(0.55)
    assert drifted["b"] == pytest.approx(0.45)
    assert sum(drifted.values()) == pytest.approx(1.0)
    buy, sell = weight_turnover(drifted, {"a": 0.5, "b": 0.5})
    assert buy == pytest.approx(0.05) and sell == pytest.approx(0.05)


def test_weight_turnover_charges_resize_of_retained() -> None:
    # codex P2 example: 50/50 → 25/25/25/25. Retained a,b resize down (sell 0.5),
    # new c,d buy 0.5 — a set-diff would miss the retained resize.
    buy, sell = weight_turnover(
        {"a.SH": 0.5, "b.SH": 0.5},
        {"a.SH": 0.25, "b.SH": 0.25, "c.SH": 0.25, "d.SH": 0.25},
    )
    assert buy == pytest.approx(0.5)
    assert sell == pytest.approx(0.5)


def test_amihud_neut_orientation_override() -> None:
    # amihud_20d's raw prior is attractive-low, but its size-neutralized residual
    # flips positive (codex P2) → the composite must treat HIGH amihud_neut as
    # attractive (NOT invert it).
    g = pd.DataFrame(
        {
            "ts_code": ["a.SH", "b.SH", "c.SH"],
            "amihud_20d_neut": [3.0, 2.0, 1.0],  # a highest residual
        }
    )
    score = composite_score(g, {"amihud_20d": 1.0})
    assert score["a.SH"] > score["b.SH"] > score["c.SH"]  # high residual → high score


def test_build_active_weights_net_zero_and_long_only() -> None:
    g = _group({"a.SH": 3.0, "b.SH": 2.0, "c.SH": 1.0})
    score = composite_score(g, _weights())
    w_bench = {"a.SH": 0.5, "b.SH": 0.3, "c.SH": 0.2}
    w = build_active_weights(g, w_bench, score, k=0.05, a_max=0.02)
    assert sum(w.values()) == pytest.approx(1.0)  # fully invested
    assert all(v >= 0 for v in w.values())  # long-only
    # net-zero active vs benchmark
    active = {c: w.get(c, 0.0) - w_bench.get(c, 0.0) for c in w_bench}
    assert sum(active.values()) == pytest.approx(0.0, abs=1e-9)
    # the high-score name is overweighted, the low-score name underweighted
    assert active["a.SH"] > 0 > active["c.SH"]


def test_build_active_weights_forced_underweight_excluded_constituent() -> None:
    # 'd.SH' is a benchmark member but NOT investable (absent from the panel) →
    # forced underweight; net active over the union still sums to 0.
    g = _group({"a.SH": 2.0, "b.SH": 1.0})
    score = composite_score(g, _weights())
    w_bench = {"a.SH": 0.4, "b.SH": 0.3, "d.SH": 0.3}
    w = build_active_weights(g, w_bench, score, k=0.05, a_max=0.02)
    assert "d.SH" not in w  # excluded constituent is not held
    union = set(w) | set(w_bench)
    active = {c: w.get(c, 0.0) - w_bench.get(c, 0.0) for c in union}
    assert active["d.SH"] == pytest.approx(-0.3)  # forced underweight
    assert sum(active.values()) == pytest.approx(0.0, abs=1e-9)


def test_partial_nan_holds_unscored_at_exact_benchmark_weight() -> None:
    # codex P2: with only SOME names scored, the unscored benchmark constituent
    # must stay at EXACTLY its benchmark weight (not be scaled by a whole-book
    # renormalize), while the book still sums to 1 and active nets to 0.
    g = _group({"a.SH": 3.0, "b.SH": 1.0})  # both scored
    g = pd.concat([g, _group({"d.SH": 1.0}, with_neut=False)], ignore_index=True)
    score = composite_score(g, _weights())  # d → NaN composite (unscored)
    w_bench = {"a.SH": 0.3, "b.SH": 0.3, "d.SH": 0.4}
    w = build_active_weights(g, w_bench, score, k=0.05, a_max=0.02)
    assert w["d.SH"] == pytest.approx(0.4)  # unscored held at exact benchmark
    assert sum(w.values()) == pytest.approx(1.0)
    active = {c: w.get(c, 0.0) - w_bench.get(c, 0.0) for c in w_bench}
    assert sum(active.values()) == pytest.approx(0.0, abs=1e-9)
    assert active["d.SH"] == pytest.approx(0.0)


def test_no_room_holds_benchmark_and_drops_offbench_scored() -> None:
    # Edge (codex P2): benchmark fully UNSCORED (sums to 1) while off-benchmark
    # names are scored → there is no room. Must hold the benchmark name at its
    # exact PIT weight and add NO off-benchmark active (not scale it down).
    scored = _group({"a.SH": 2.0, "b.SH": 1.0})  # off-benchmark, scored
    unscored = _group({"bench.SH": 1.0}, with_neut=False)  # benchmark, NaN score
    g = pd.concat([scored, unscored], ignore_index=True)
    score = composite_score(g, _weights())
    w_bench = {"bench.SH": 1.0}  # whole benchmark is the one unscored name
    w = build_active_weights(g, w_bench, score, k=0.05, a_max=0.02)
    assert w == {"bench.SH": pytest.approx(1.0)}  # held at PIT; off-bench dropped


def test_build_active_weights_nan_score_held_at_benchmark() -> None:
    # All names have NaN composite → no tilt → weights collapse to the
    # normalised benchmark weights (active ~ 0).
    g = _group({"a.SH": 1.0, "b.SH": 1.0}, with_neut=False)
    score = composite_score(g, _weights())
    w_bench = {"a.SH": 0.6, "b.SH": 0.4}
    w = build_active_weights(g, w_bench, score, k=0.05, a_max=0.02)
    assert w["a.SH"] == pytest.approx(0.6)
    assert w["b.SH"] == pytest.approx(0.4)


def _panel_two_dates() -> pd.DataFrame:
    rows = []
    for date in ("20240105", "20240112"):
        for code, sc, fwd in [
            ("a.SH", 3.0, 0.04),
            ("b.SH", 2.0, 0.01),
            ("c.SH", 1.0, -0.02),
        ]:
            row: dict[str, object] = {
                "date": date,
                "ts_code": code,
                "industry_l1": "801080.SI",
                "log_circ_mv": 10.0,
                "fwd_ret_5d": fwd,
            }
            from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME

            for base in CARRY_FACTORS:
                sign = 1.0 if ALL_FACTORS_BY_NAME[base].attractive_high else -1.0
                row[f"{base}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def test_benchmark_relative_backtest_runs_and_reports_te_ir() -> None:
    panel = _panel_two_dates()
    bench = {
        "20240105": {"a.SH": 0.34, "b.SH": 0.33, "c.SH": 0.33},
        "20240112": {"a.SH": 0.34, "b.SH": 0.33, "c.SH": 0.33},
    }
    # flat index (so excess ~ the tilt's contribution)
    index_returns = {"20240105": 0.0, "20240112": 0.0}
    res = benchmark_relative_backtest(
        panel,
        lambda d: bench.get(d, {}),
        index_returns,
        weights=_weights(),
        horizon=5,
        k=0.1,
        a_max=0.05,
    )
    assert res.n_periods == 2
    # overweighting the high-fwd name 'a' → positive excess on a flat index
    assert res.total_excess > 0
    assert res.tracking_error >= 0
    # net active per date ~ 0 (beta ~ 1)
    assert abs(res.mean_net_active) < 1e-6
