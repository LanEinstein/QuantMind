"""Tests for the R2-4 pre-declared benchmark-relative search."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import CARRY_FACTORS
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME, FACTOR_NAMES
from scripts.factor_research.locked_split import LockedSplit
from scripts.factor_research.round2_search import (
    _align,
    _select,
    _shuffle_neut,
    build_candidates,
    build_weight_vectors,
    load_manifest,
    search,
)

REAL_MANIFEST = "config/research/round2_experiment_manifest.json"


# --- manifest / candidate grid ----------------------------------------------


def test_load_real_manifest_carry_order_matches() -> None:
    manifest = load_manifest(REAL_MANIFEST)
    assert tuple(manifest["carry_factor_order"]) == tuple(CARRY_FACTORS)


def test_load_manifest_fails_closed_on_carry_drift(tmp_path: Path) -> None:
    bad = json.loads(Path(REAL_MANIFEST).read_text())
    bad["carry_factor_order"] = list(reversed(bad["carry_factor_order"]))
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="carry_factor_order"):
        load_manifest(str(p))


def test_weight_vectors_equal_anchor_plus_sobol_sum_to_one() -> None:
    manifest = load_manifest(REAL_MANIFEST)
    vecs = build_weight_vectors(manifest)
    spec = manifest["degrees_of_freedom"]["weight_simplex"]
    assert len(vecs) == 1 + int(spec["n_sobol"])
    for v in vecs:
        assert len(v) == len(CARRY_FACTORS)
        assert sum(v) == pytest.approx(1.0, abs=1e-9)
    # the anchor is exactly equal-weight
    assert vecs[0] == pytest.approx(
        tuple(1.0 / len(CARRY_FACTORS) for _ in CARRY_FACTORS)
    )


def test_candidate_count_matches_manifest_n_trials() -> None:
    manifest = load_manifest(REAL_MANIFEST)
    cands = build_candidates(manifest)
    assert len(cands) == int(manifest["search_design"]["n_trials_total"])


# --- pure helpers ------------------------------------------------------------


def test_select_train_robust_then_best_val() -> None:
    # candidate 2 has the best val IR but a poor train IR (excluded from top_k=2);
    # among the train-robust top-2 (idx 0, 1) the better val IR (idx 1) wins.
    train_irs = [0.9, 0.8, 0.1]
    val_irs = [0.2, 0.5, 0.99]
    finalists, selected = _select(train_irs, val_irs, top_k=2)
    assert set(finalists) == {0, 1}
    assert selected == 1


def test_align_reorders_to_common_dates() -> None:
    excess = [0.1, 0.2, 0.3]
    dates = ["20240103", "20240101", "20240102"]
    assert _align(excess, dates, ["20240101", "20240102"]) == [0.2, 0.3]


def test_shuffle_neut_preserves_per_date_multiset() -> None:
    panel = _synth_panel(["20240101", "20240102"])
    shuffled = _shuffle_neut(panel, seed=7)
    col = f"{CARRY_FACTORS[0]}_neut"
    for d in ("20240101", "20240102"):
        orig = sorted(panel.loc[panel["date"] == d, col].tolist())
        shuf = sorted(shuffled.loc[shuffled["date"] == d, col].tolist())
        assert orig == shuf  # same values, just permuted within the date
    assert panel[col].tolist() != shuffled[col].tolist()  # actually shuffled


# --- end-to-end search (tiny temp manifest) ---------------------------------


def _synth_panel(dates: list[str]) -> pd.DataFrame:
    names = ["a", "b", "c", "d", "e", "f"]
    base = {"a": 6.0, "b": 5.0, "c": 4.0, "d": 3.0, "e": 2.0, "f": 1.0}
    rows = []
    for d in dates:
        for nm in names:
            sc = base[nm]
            code = f"{nm}00000"
            row: dict[str, object] = {
                "date": d,
                "code": code,
                "ts_code": f"{code}.SH",
                "industry_l1": "801080.SI",
                "circ_mv": 1e6 * sc,
                "log_circ_mv": math.log(1e6 * sc),
                "fwd_ret_5d": 0.01 * sc,
                "fwd_ret_10d": 0.01 * sc,
                "fwd_ret_20d": 0.01 * sc,
            }
            for f in FACTOR_NAMES:  # round-1 raw factors (for the SPA baselines)
                row[f] = float(sc)
            for b in CARRY_FACTORS:  # neutralized composite inputs
                sign = 1.0 if ALL_FACTORS_BY_NAME[b].attractive_high else -1.0
                row[f"{b}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def _tiny_manifest(tmp_path: Path) -> str:
    real = json.loads(Path(REAL_MANIFEST).read_text())
    m = dict(real)
    m["degrees_of_freedom"] = dict(real["degrees_of_freedom"])
    m["degrees_of_freedom"]["exposure_constraint"] = {"values": ["constituent_only"]}
    m["degrees_of_freedom"]["k_grid"] = {"values": [0.1]}
    m["degrees_of_freedom"]["a_max_grid"] = {"values": [0.02]}
    m["degrees_of_freedom"]["weight_simplex"] = {
        "sampler": "scrambled_sobol_kraemer_simplex",
        "dim": len(CARRY_FACTORS),
        "n_sobol": 2,
        "seed": 1,
    }
    m["search_design"] = dict(real["search_design"])
    m["search_design"]["n_trials_total"] = 3  # 1 equal + 2 sobol
    m["search_design"]["selection"] = {
        "inner_train_val_cutoff": "20240115",
        "purge_rebalances": 0,
        "top_k_finalists": 2,
        "metric": "information_ratio",
        "tie_break": "lowest_candidate_index",
    }
    p = tmp_path / "tiny_manifest.json"
    p.write_text(json.dumps(m))
    return str(p)


def test_search_end_to_end_returns_selected_strategy(tmp_path: Path) -> None:
    dates = [f"202401{i:02d}" for i in range(1, 29)]  # 28 dates straddling cutoff
    panel = _synth_panel(dates)
    bench = {f"{nm}00000.SH": 1.0 / 6 for nm in ["a", "b", "c", "d", "e", "f"]}
    index_returns = dict.fromkeys(dates, 0.0)
    split = LockedSplit(
        train_val_dates=tuple(dates), embargo_dates=(), test_dates=("20991231",)
    )
    result = search(
        panel,
        lambda d: bench,
        index_returns,
        manifest_path=_tiny_manifest(tmp_path),
        split=split,
        horizon=5,
        progress_every=0,
    )
    assert result.selected_constraint == "constituent_only"
    assert result.n_trials == 3
    assert set(result.selected_weights) == set(CARRY_FACTORS)
    assert 0.0 <= result.disclosure["dsr"] <= 1.0
    assert 0.0 <= result.spa_p_vs_passive <= 1.0
    assert isinstance(result.sentinel_passes, bool)
    assert len(result.finalists) == 2
