"""Tests for the R2-4 pre-declared benchmark-relative search."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import (
    CARRY_FACTORS,
    R3_CARRY_FACTORS,
    R4_CARRY_FACTORS,
)
from scripts.factor_research.factor_lib import ALL_FACTORS_BY_NAME, FACTOR_NAMES
from scripts.factor_research.locked_split import LockedSplit
from scripts.factor_research.round2_search import (
    _align,
    _select,
    _shuffle_neut,
    build_candidates,
    build_weight_vectors,
    load_manifest,
    resolve_carry_inputs,
    search,
)

REAL_MANIFEST = "config/research/round2_experiment_manifest.json"
R3_MANIFEST = "config/research/round3_experiment_manifest.json"
R4_MANIFEST = "config/research/round4_experiment_manifest.json"


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


# --- round-3 carry parameterization (round-2 byte-behavior preserved) --------


def test_r3_manifest_carry_order_matches_r3_carry() -> None:
    # The round-3 manifest is keyed to R3_CARRY_FACTORS (eleven + accr); loading
    # it with the round-3 carry passes, and the order matches by construction.
    manifest = load_manifest(R3_MANIFEST, carry=R3_CARRY_FACTORS)
    assert tuple(manifest["carry_factor_order"]) == tuple(R3_CARRY_FACTORS)
    assert R3_CARRY_FACTORS == (*CARRY_FACTORS, "accr")


def test_r3_manifest_fails_closed_under_round2_carry() -> None:
    # The drift guard still fires across rounds: the 12-factor R3 manifest must
    # NOT load under the 11-factor round-2 carry (the default).
    with pytest.raises(ValueError, match="carry_factor_order"):
        load_manifest(R3_MANIFEST)  # default carry == CARRY_FACTORS (eleven)


def test_r3_weight_vectors_dim_12_sum_to_one() -> None:
    manifest = load_manifest(R3_MANIFEST, carry=R3_CARRY_FACTORS)
    vecs = build_weight_vectors(manifest, carry=R3_CARRY_FACTORS)
    spec = manifest["degrees_of_freedom"]["weight_simplex"]
    assert len(vecs) == 1 + int(spec["n_sobol"])
    for v in vecs:
        assert len(v) == len(R3_CARRY_FACTORS) == 12
        assert sum(v) == pytest.approx(1.0, abs=1e-9)


def test_r3_candidate_count_matches_manifest_n_trials() -> None:
    manifest = load_manifest(R3_MANIFEST, carry=R3_CARRY_FACTORS)
    cands = build_candidates(manifest, carry=R3_CARRY_FACTORS)
    assert len(cands) == int(manifest["search_design"]["n_trials_total"]) == 612
    # weights are positional over the 12-factor carry
    assert all(len(c.weights) == 12 for c in cands)


def test_resolve_carry_inputs_selects_per_round_defaults() -> None:
    # `--carry r3` alone must pick the r3 panel/manifest/out (not the r2 ones,
    # which lack accr) — codex R3-4 P2.
    carry, panel, manifest, out = resolve_carry_inputs("r3")
    assert carry == R3_CARRY_FACTORS
    assert panel == "data/factor_research/panel_train_val_r3.csv"
    assert manifest == R3_MANIFEST
    assert out == "data/factor_research/round3_search_result.json"

    carry2, panel2, manifest2, out2 = resolve_carry_inputs("r2")
    assert carry2 == CARRY_FACTORS
    assert panel2 == "data/factor_research/panel_train_val_r2.csv"
    assert manifest2 == REAL_MANIFEST

    # explicit overrides win
    _, panel3, manifest3, out3 = resolve_carry_inputs(
        "r3", panel="x.csv", manifest="m.json", out="o.json"
    )
    assert (panel3, manifest3, out3) == ("x.csv", "m.json", "o.json")


# --- round-4 carry parameterization (round-2/3 byte-behavior preserved) -------


def test_r4_carry_is_r3_plus_four_analyst_survivors() -> None:
    assert R4_CARRY_FACTORS == (
        *R3_CARRY_FACTORS,
        "np_rev",
        "rev_diff",
        "tp_impl",
        "cover_chg",
    )
    assert len(R4_CARRY_FACTORS) == 16


def test_r4_manifest_carry_order_matches_r4_carry() -> None:
    manifest = load_manifest(R4_MANIFEST, carry=R4_CARRY_FACTORS)
    assert tuple(manifest["carry_factor_order"]) == tuple(R4_CARRY_FACTORS)


def test_r4_manifest_fails_closed_under_round2_carry() -> None:
    # The drift guard fires across rounds: the 16-factor R4 manifest must NOT load
    # under the 11-factor round-2 carry (the default).
    with pytest.raises(ValueError, match="carry_factor_order"):
        load_manifest(R4_MANIFEST)  # default carry == CARRY_FACTORS (eleven)


def test_r4_weight_vectors_dim_16_sum_to_one() -> None:
    manifest = load_manifest(R4_MANIFEST, carry=R4_CARRY_FACTORS)
    vecs = build_weight_vectors(manifest, carry=R4_CARRY_FACTORS)
    spec = manifest["degrees_of_freedom"]["weight_simplex"]
    assert len(vecs) == 1 + int(spec["n_sobol"])
    for v in vecs:
        assert len(v) == len(R4_CARRY_FACTORS) == 16
        assert sum(v) == pytest.approx(1.0, abs=1e-9)


def test_r4_candidate_count_matches_manifest_n_trials() -> None:
    manifest = load_manifest(R4_MANIFEST, carry=R4_CARRY_FACTORS)
    cands = build_candidates(manifest, carry=R4_CARRY_FACTORS)
    # n_trials_total is THIS round's grid (612 = 4×3×3×17), unchanged from r2/r3
    # because n_sobol stays 16; the carry dim grew 12→16, not the per-cell count.
    assert len(cands) == int(manifest["search_design"]["n_trials_total"]) == 612
    assert all(len(c.weights) == 16 for c in cands)


def test_r4_manifest_declares_cumulative_deflation_n() -> None:
    # The R4 manifest pre-declares the CUMULATIVE deflation count across all four
    # rounds (512 + 612 + 612 + 612 = 2348) — strictly larger than the per-round
    # grid so the DSR is deflated more harshly than round-2/3.
    manifest = load_manifest(R4_MANIFEST, carry=R4_CARRY_FACTORS)
    n_trials = int(manifest["search_design"]["n_trials_total"])
    deflation_n = int(manifest["search_design"]["dsr_deflation_n_trials"])
    assert n_trials == 612
    assert deflation_n == 2348
    assert deflation_n > n_trials


def test_resolve_carry_inputs_selects_r4_defaults() -> None:
    carry, panel, manifest, out = resolve_carry_inputs("r4")
    assert carry == R4_CARRY_FACTORS
    assert panel == "data/factor_research/panel_train_val_r4.csv"
    assert manifest == R4_MANIFEST
    assert out == "data/factor_research/round4_search_result.json"


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


def _synth_panel(
    dates: list[str], carry: tuple[str, ...] = CARRY_FACTORS
) -> pd.DataFrame:
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
            for b in carry:  # neutralized composite inputs
                sign = 1.0 if ALL_FACTORS_BY_NAME[b].attractive_high else -1.0
                row[f"{b}_neut"] = sign * sc
            rows.append(row)
    return pd.DataFrame(rows)


def _tiny_manifest(tmp_path: Path, carry: tuple[str, ...] = CARRY_FACTORS) -> str:
    real = json.loads(Path(REAL_MANIFEST).read_text())
    m = dict(real)
    m["carry_factor_order"] = list(carry)
    m["degrees_of_freedom"] = dict(real["degrees_of_freedom"])
    m["degrees_of_freedom"]["exposure_constraint"] = {"values": ["constituent_only"]}
    m["degrees_of_freedom"]["k_grid"] = {"values": [0.1]}
    m["degrees_of_freedom"]["a_max_grid"] = {"values": [0.02]}
    m["degrees_of_freedom"]["weight_simplex"] = {
        "sampler": "scrambled_sobol_kraemer_simplex",
        "dim": len(carry),
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


def test_search_end_to_end_r3_carry_threads_accr(tmp_path: Path) -> None:
    # Full carry-threaded path under the 12-factor round-3 carry: weights, the
    # sentinel shuffle, and neutralized composite all span accr (the R3-3 survivor).
    dates = [f"202401{i:02d}" for i in range(1, 29)]
    panel = _synth_panel(dates, carry=R3_CARRY_FACTORS)
    bench = {f"{nm}00000.SH": 1.0 / 6 for nm in ["a", "b", "c", "d", "e", "f"]}
    index_returns = dict.fromkeys(dates, 0.0)
    split = LockedSplit(
        train_val_dates=tuple(dates), embargo_dates=(), test_dates=("20991231",)
    )
    result = search(
        panel,
        lambda d: bench,
        index_returns,
        manifest_path=_tiny_manifest(tmp_path, carry=R3_CARRY_FACTORS),
        split=split,
        horizon=5,
        progress_every=0,
        carry=R3_CARRY_FACTORS,
    )
    assert result.selected_constraint == "constituent_only"
    assert set(result.selected_weights) == set(R3_CARRY_FACTORS)
    assert "accr" in result.selected_weights
    assert isinstance(result.sentinel_passes, bool)


def test_search_end_to_end_r4_carry_threads_analyst(tmp_path: Path) -> None:
    # Full carry-threaded path under the 16-factor round-4 carry: weights, the
    # sentinel shuffle, and the neutralized composite all span the four analyst
    # survivors (np_rev / rev_diff / tp_impl / cover_chg).
    dates = [f"202401{i:02d}" for i in range(1, 29)]
    panel = _synth_panel(dates, carry=R4_CARRY_FACTORS)
    bench = {f"{nm}00000.SH": 1.0 / 6 for nm in ["a", "b", "c", "d", "e", "f"]}
    index_returns = dict.fromkeys(dates, 0.0)
    split = LockedSplit(
        train_val_dates=tuple(dates), embargo_dates=(), test_dates=("20991231",)
    )
    result = search(
        panel,
        lambda d: bench,
        index_returns,
        manifest_path=_tiny_manifest(tmp_path, carry=R4_CARRY_FACTORS),
        split=split,
        horizon=5,
        progress_every=0,
        carry=R4_CARRY_FACTORS,
    )
    assert result.selected_constraint == "constituent_only"
    assert set(result.selected_weights) == set(R4_CARRY_FACTORS)
    for survivor in ("np_rev", "rev_diff", "tp_impl", "cover_chg"):
        assert survivor in result.selected_weights
    assert isinstance(result.sentinel_passes, bool)


# --- cumulative-N DSR deflation decoupling (round-2/3 byte-identical) ---------


def _run_tiny_search(manifest_path: str) -> object:  # noqa: ANN401
    dates = [f"202401{i:02d}" for i in range(1, 29)]
    panel = _synth_panel(dates)
    bench = {f"{nm}00000.SH": 1.0 / 6 for nm in ["a", "b", "c", "d", "e", "f"]}
    index_returns = dict.fromkeys(dates, 0.0)
    split = LockedSplit(
        train_val_dates=tuple(dates), embargo_dates=(), test_dates=("20991231",)
    )
    return search(
        panel,
        lambda d: bench,
        index_returns,
        manifest_path=manifest_path,
        split=split,
        horizon=5,
        progress_every=0,
    )


def test_deflation_n_defaults_to_grid_when_absent(tmp_path: Path) -> None:
    # Round-2/3 manifests carry NO dsr_deflation_n_trials → the disclosure's
    # n_trials equals the grid n_trials (their behavior is byte-identical).
    result = _run_tiny_search(_tiny_manifest(tmp_path))
    assert result.n_trials == 3  # top-level = this round's grid
    assert result.disclosure["n_trials"] == 3  # DSR deflation = grid (no override)


def test_deflation_n_overrides_disclosure_when_present(tmp_path: Path) -> None:
    # A manifest carrying dsr_deflation_n_trials deflates the DSR/MinBTL by the
    # cumulative count while the grid candidate-count check still uses the
    # per-round n_trials (the round-4 cross-round multiple-testing correction).
    m = json.loads(Path(_tiny_manifest(tmp_path)).read_text())
    m["search_design"]["dsr_deflation_n_trials"] = 999  # cumulative, > grid 3
    p = tmp_path / "tiny_deflate.json"
    p.write_text(json.dumps(m))
    result = _run_tiny_search(str(p))
    assert result.n_trials == 3  # grid candidate count unchanged
    assert result.disclosure["n_trials"] == 999  # DSR deflated by cumulative N
