"""R2-4 pre-declared search → the single benchmark-relative strategy (dev evidence).

The closing R2-4 module. It reads the FROZEN ``round2_experiment_manifest.json``,
enumerates every pre-declared candidate (exposure_constraint × k × a_max × weight
simplex), backtests each on the inner train/val split of the train_val panel,
picks the SINGLE strategy carried into R2-5 freeze + R2-6 verdict, and attaches
the full multiple-testing disclosure.

Honesty discipline (the whole point — same as round-1 ``weight_search``):

* **Selection never touches test.** Inner train/val split lives entirely inside
  train_val; benchmark inputs are restricted to ``< test_start`` by the caller;
  every panel date is re-checked through ``LockedSplit.assert_all_not_test``.
* **Pre-declared N.** The manifest's cumulative trial count is the DSR/PBO
  ``n_trials`` — the search cannot be widened after the fact to manufacture a
  winner.
* **Disclosure, not alpha fabrication.** DSR (main gate, deflated by N) / PBO-CSCV
  / Hansen SPA vs passive-CSI300 + momentum incumbent + round-1 frozen / a
  shuffled-composite SENTINEL control / exposure disclosure (size/forced-UW
  active). These only lower the false-positive rate; the real verdict is the
  one-shot R2-6 locked test.
* **This is DEVELOPMENT EVIDENCE, never PASS/FAIL.** A low DSR here is reported,
  not papered over.

Reads only the train_val panel; deterministic (Sobol + SPA + sentinel all
fixed-seed). ``backend.strategy_evolution`` (via ``stats_disclosure``) is
import-allowed; ``backend.{llm,agents,mirofish}`` is not.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .benchmark_relative import (
    CARRY_FACTORS,
    R3_CARRY_FACTORS,
    R4_CARRY_FACTORS,
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .exposure_constraints import validate_constraint
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .portfolio_backtest import backtest as longonly_backtest
from .r2_benchmark_relative_diagnostics import pretest_benchmark_inputs
from .stats_disclosure import DSR_FLOOR, DisclosureReport, disclose
from .walk_forward_eval import WalkForwardReport
from .walk_forward_eval import build_report as wf_report
from .weight_search import simplex_sobol, split_train_val

# Round-1 frozen strategy (a SPA incumbent baseline; its excess-vs-CSI300 series).
ROUND1_FROZEN_WEIGHTS: dict[str, float] = {
    "ret_5d": 0.089,
    "ret_20d": 0.018,
    "vol_20d": 0.163,
    "max_20d": 0.163,
    "ep_ttm": 0.211,
    "turn_20d": 0.173,
    "amihud_20d": 0.183,
}
SENTINEL_SEEDS: tuple[int, ...] = (101, 202, 303, 404, 505, 606, 707, 808)
WINSOR_QUANTILE: float = 0.01
DEFAULT_MANIFEST = "config/research/round2_experiment_manifest.json"

# Per-round inputs, keyed by the --carry choice: (carry tuple, panel, manifest,
# out). One source of truth so the carry tuple, panel, and manifest can never
# drift apart — `--carry r4` selects the round-4 carry AND the round-4 panel/
# manifest/out together (the round-2/3 panels lack later columns and would
# KeyError). The carry tuple is the positional order the Sobol weight vectors map
# to + the columns the composite scores. Explicit --panel/--manifest/--out win.
_CARRY_INPUTS: dict[str, tuple[Sequence[str], str, str, str]] = {
    "r2": (
        CARRY_FACTORS,
        "data/factor_research/panel_train_val_r2.csv",
        DEFAULT_MANIFEST,
        "data/factor_research/round2_search_result.json",
    ),
    "r3": (
        R3_CARRY_FACTORS,
        "data/factor_research/panel_train_val_r3.csv",
        "config/research/round3_experiment_manifest.json",
        "data/factor_research/round3_search_result.json",
    ),
    "r4": (
        R4_CARRY_FACTORS,
        "data/factor_research/panel_train_val_r4.csv",
        "config/research/round4_experiment_manifest.json",
        "data/factor_research/round4_search_result.json",
    ),
}


def resolve_carry_inputs(
    choice: str,
    *,
    panel: str = "",
    manifest: str = "",
    out: str = "",
) -> tuple[Sequence[str], str, str, str]:
    """``(carry, panel, manifest, out)`` for a ``--carry`` choice.

    The carry tuple and the three file paths are selected together so a
    ``--carry r4`` run never mixes the round-4 carry with the round-2/3 panel/
    manifest. Any explicitly-passed path overrides its per-round default.
    """
    carry, d_panel, d_manifest, d_out = _CARRY_INPUTS[choice]
    return carry, (panel or d_panel), (manifest or d_manifest), (out or d_out)


@dataclass(frozen=True)
class Candidate:
    """One pre-declared search point (constraint × k × a_max × weights)."""

    constraint: str
    k: float
    a_max: float
    weights: tuple[float, ...]  # positional over the run's carry tuple


@dataclass(frozen=True)
class Round2SearchResult:
    """The single selected benchmark-relative strategy + full honest disclosure."""

    selected_constraint: str
    selected_k: float
    selected_a_max: float
    selected_nonconst_cap: float
    selected_weights: dict[str, float]
    n_trials: int
    cutoff: str
    n_train_dates: int
    n_val_dates: int
    train: dict[str, float]
    val: dict[str, float]
    disclosure: dict[str, float]
    spa_p_vs_passive: float
    spa_p_vs_momentum: float
    spa_p_vs_round1: float
    sentinel_max_val_ir: float
    sentinel_selected_val_ir: float
    sentinel_passes: bool
    walk_forward: dict[str, float]
    finalists: list[dict[str, float]]


# --- manifest ----------------------------------------------------------------


def load_manifest(
    path: str = DEFAULT_MANIFEST, *, carry: Sequence[str] = CARRY_FACTORS
) -> dict[str, Any]:
    """Load + sanity-check the frozen experiment manifest (fail closed on drift).

    ``carry`` is the factor order the searched weight vectors are positional over
    (round-2 :data:`CARRY_FACTORS` by default; round-3 passes ``R3_CARRY_FACTORS``).
    """
    manifest: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    order = tuple(manifest["carry_factor_order"])
    if order != tuple(carry):
        raise ValueError(
            f"manifest carry_factor_order {order} != carry {tuple(carry)} "
            "— searched weight vectors would remap; refusing (fail closed)."
        )
    return manifest


def build_weight_vectors(
    manifest: dict[str, Any], *, carry: Sequence[str] = CARRY_FACTORS
) -> list[tuple[float, ...]]:
    """Equal-weight anchor + Sobol simplex points (the per-cell weight vectors)."""
    spec = manifest["degrees_of_freedom"]["weight_simplex"]
    dim = int(spec["dim"])
    if dim != len(carry):
        raise ValueError(f"weight_simplex dim {dim} != {len(carry)} carry")
    equal = tuple(1.0 / dim for _ in range(dim))
    sobol = simplex_sobol(int(spec["n_sobol"]), dim, int(spec["seed"]))
    return [equal, *sobol]


def build_candidates(
    manifest: dict[str, Any], *, carry: Sequence[str] = CARRY_FACTORS
) -> list[Candidate]:
    """The full pre-declared candidate grid (constraint × k × a_max × weights)."""
    dof = manifest["degrees_of_freedom"]
    constraints = dof["exposure_constraint"]["values"]
    for c in constraints:
        validate_constraint(c)
    ks = dof["k_grid"]["values"]
    a_maxes = dof["a_max_grid"]["values"]
    vectors = build_weight_vectors(manifest, carry=carry)
    out: list[Candidate] = []
    for constraint in constraints:
        for k in ks:
            for a_max in a_maxes:
                for w in vectors:
                    out.append(Candidate(constraint, float(k), float(a_max), w))
    return out


def _weights_dict(
    w: Sequence[float], *, carry: Sequence[str] = CARRY_FACTORS
) -> dict[str, float]:
    return dict(zip(carry, (float(x) for x in w), strict=True))


# --- backtest + selection ----------------------------------------------------


def _run_candidate(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    cand: Candidate,
    *,
    horizon: int,
    nonconst_cap: float,
    carry: Sequence[str] = CARRY_FACTORS,
) -> BenchmarkRelativeResult:
    """Backtest one candidate (benchmark-relative excess + exposures)."""
    return benchmark_relative_backtest(
        panel,
        bench_asof,
        index_returns,
        weights=_weights_dict(cand.weights, carry=carry),
        horizon=horizon,
        k=cand.k,
        a_max=cand.a_max,
        exposure_constraint=cand.constraint,
        nonconst_cap=nonconst_cap,
    )


def _select(
    train_irs: list[float], val_irs: list[float], *, top_k: int
) -> tuple[list[int], int]:
    """Train-robust shortlist (top_k by train IR) → best val IR among them.

    Deterministic: shortlist by (−train IR, index); winner by (val IR, −index) so
    ties resolve to the lowest candidate index — the round-1 cross-validation
    discipline that curbs val-overfitting.
    """
    order = sorted(range(len(train_irs)), key=lambda i: (-train_irs[i], i))
    finalists = order[:top_k]
    selected = max(finalists, key=lambda i: (val_irs[i], -i))
    return finalists, selected


def _summary(r: BenchmarkRelativeResult) -> dict[str, float]:
    """Scalar summary of a benchmark-relative backtest (for JSON output)."""
    return {
        "n_periods": float(r.n_periods),
        "total_excess": r.total_excess,
        "annual_excess": r.annual_excess,
        "tracking_error": r.tracking_error,
        "information_ratio": r.information_ratio,
        "avg_turnover": r.avg_turnover,
        "avg_gross_active": r.avg_gross_active,
        "avg_forced_underweight": r.avg_forced_underweight,
        "mean_net_active": r.mean_net_active,
        "mean_size_active": r.mean_size_active,
        "mean_max_industry_active": r.mean_max_industry_active,
    }


# --- disclosure baselines ----------------------------------------------------


def _align(
    excess: Sequence[float], dates: Sequence[str], common: list[str]
) -> list[float]:
    by_date = dict(zip(dates, excess, strict=True))
    return [by_date[d] for d in common]


def _longonly_excess_by_date(
    val_panel: pd.DataFrame,
    weights: dict[str, float],
    index_returns: Mapping[str, float],
    *,
    horizon: int,
    top_n: int,
    orient: dict[str, bool] | None = None,
) -> dict[str, float]:
    """A long-only top-N strategy's per-period excess-vs-CSI300 keyed by date.

    Reuses the round-1 ``portfolio_backtest`` (round-1 factor columns live in the
    R2 panel) and subtracts the CSI300 horizon return per date — so it is an
    excess-vs-CSI300 series directly comparable to the benchmark-relative arm.
    """
    res = longonly_backtest(
        val_panel, weights, benchmark=None, horizon=horizon, top_n=top_n, orient=orient
    )
    return {
        d: float(net) - float(index_returns[d])
        for d, net in zip(res.dates, res.net_returns, strict=True)
        if d in index_returns
    }


def _spa_p(
    candidate_matrix: list[list[float]],
    common: list[str],
    baseline_by_date: dict[str, float],
) -> float:
    """Hansen SPA p-value of the candidate pool vs a baseline excess series."""
    from backend.strategy_evolution.disclosure_stats import spa_disclosure

    keep = [i for i, d in enumerate(common) if d in baseline_by_date]
    if len(keep) < 2:
        return 1.0
    base = [baseline_by_date[common[i]] for i in keep]
    excess_matrix = [
        [row[i] - base[j] for j, i in enumerate(keep)] for row in candidate_matrix
    ]
    return spa_disclosure(excess_matrix).p_value


# --- sentinel ----------------------------------------------------------------


def _shuffle_neut(
    panel: pd.DataFrame, seed: int, *, carry: Sequence[str] = CARRY_FACTORS
) -> pd.DataFrame:
    """Permute every ``*_neut`` column WITHIN each date (a no-signal control).

    A sentinel composite built on shuffled neutralized factors should carry no
    edge; if it scores a val IR ≥ the selected strategy's, the selection gate is
    not separating signal from noise (disclosed, not silently passed).
    """
    rng = np.random.default_rng(seed)
    out = panel.copy()
    neut_cols = [f"{f}_neut" for f in carry if f"{f}_neut" in out.columns]
    for _, idx in panel.groupby("date", sort=True).groups.items():
        rows = list(idx)
        for col in neut_cols:
            vals = out.loc[rows, col].to_numpy()
            out.loc[rows, col] = rng.permutation(vals)
    return out


def _sentinel_val_irs(
    val_panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    selected: Candidate,
    *,
    horizon: int,
    nonconst_cap: float,
    seeds: Sequence[int],
    carry: Sequence[str] = CARRY_FACTORS,
) -> list[float]:
    """Val IR of the selected config on shuffled-composite (sentinel) panels."""
    irs: list[float] = []
    equal = Candidate(
        selected.constraint,
        selected.k,
        selected.a_max,
        tuple(1.0 / len(carry) for _ in carry),
    )
    for seed in seeds:
        shuffled = _shuffle_neut(val_panel, seed, carry=carry)
        r = _run_candidate(
            shuffled,
            bench_asof,
            index_returns,
            equal,
            horizon=horizon,
            nonconst_cap=nonconst_cap,
            carry=carry,
        )
        irs.append(r.information_ratio)
    return irs


# --- search orchestration ----------------------------------------------------


def _run_all(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    candidates: list[Candidate],
    *,
    horizon: int,
    nonconst_cap: float,
    label: str,
    progress_every: int,
    carry: Sequence[str] = CARRY_FACTORS,
) -> list[BenchmarkRelativeResult]:
    """Backtest every candidate over one panel (with progress logging)."""
    out: list[BenchmarkRelativeResult] = []
    for i, c in enumerate(candidates):
        out.append(
            _run_candidate(
                panel,
                bench_asof,
                index_returns,
                c,
                horizon=horizon,
                nonconst_cap=nonconst_cap,
                carry=carry,
            )
        )
        if progress_every and (i + 1) % progress_every == 0:
            print(f"  [{label}] {i + 1}/{len(candidates)} candidates")
    return out


def search(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    manifest_path: str = DEFAULT_MANIFEST,
    split: LockedSplit | None = None,
    horizon: int = 5,
    progress_every: int = 100,
    carry: Sequence[str] = CARRY_FACTORS,
) -> Round2SearchResult:
    """Run the pre-declared search and return the single selected strategy + disclosure.

    ``panel`` must be NEUTRALIZED (carry ``*_neut`` columns) and train_val only;
    ``bench_asof`` / ``index_returns`` must already be restricted to ``<
    test_start`` by the caller (firewall). The unique winner is the best inner-val
    IR among the train-robust top-k finalists. ``carry`` is the factor order the
    weight vectors are positional over (round-2 default; round-3 passes the
    12-factor ``R3_CARRY_FACTORS``).
    """
    manifest = load_manifest(manifest_path, carry=carry)
    if split is None:
        split = LockedSplit.load()
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))  # firewall

    sel = manifest["search_design"]["selection"]
    cutoff = str(sel["inner_train_val_cutoff"])
    purge = int(sel["purge_rebalances"])
    top_k = int(sel["top_k_finalists"])
    nonconst_cap = float(manifest["degrees_of_freedom"]["nonconst_cap"]["value"])
    n_trials = int(manifest["search_design"]["n_trials_total"])
    # The DSR/MinBTL deflation count: the CUMULATIVE pre-declared trial count
    # across every round that has searched this carry lineage, not just this
    # round's grid (round-4 declares 2348 = 512+612+612+612). Absent (round-2/3
    # manifests) → equals n_trials, so their behavior is byte-identical. The grid
    # check below still uses the per-round n_trials; only the disclosure deflates
    # by the larger cumulative count (honest multiple-testing across rounds).
    deflation_n = int(
        manifest["search_design"].get("dsr_deflation_n_trials", n_trials)
    )

    train_panel, val_panel, train_dates, val_dates = split_train_val(
        panel, cutoff=cutoff, purge=purge
    )
    candidates = build_candidates(manifest, carry=carry)
    if len(candidates) != n_trials:
        raise ValueError(
            f"candidate count {len(candidates)} != manifest n_trials {n_trials} "
            "— the search space drifted from the frozen manifest (fail closed)."
        )

    train_results = _run_all(
        train_panel,
        bench_asof,
        index_returns,
        candidates,
        horizon=horizon,
        nonconst_cap=nonconst_cap,
        label="train",
        progress_every=progress_every,
        carry=carry,
    )
    val_results = _run_all(
        val_panel,
        bench_asof,
        index_returns,
        candidates,
        horizon=horizon,
        nonconst_cap=nonconst_cap,
        label="val",
        progress_every=progress_every,
        carry=carry,
    )
    train_irs = [r.information_ratio for r in train_results]
    val_irs = [r.information_ratio for r in val_results]
    finalists, selected = _select(train_irs, val_irs, top_k=top_k)
    sel_cand = candidates[selected]

    # Disclosure over the FULL searched pool (val excess aligned to common dates).
    # DSR/MinBTL deflate by the CUMULATIVE deflation_n (cross-round multiple
    # testing); PBO/SPA are over THIS round's actual pool (not n_trials-keyed).
    disc = _disclose_and_robustness(
        panel,
        val_panel,
        val_results,
        sel_cand,
        selected=selected,
        deflation_n=deflation_n,
        bench_asof=bench_asof,
        index_returns=index_returns,
        horizon=horizon,
        nonconst_cap=nonconst_cap,
        carry=carry,
    )
    return _assemble_result(
        candidates,
        sel_cand,
        disc,
        selected=selected,
        finalists=finalists,
        train_results=train_results,
        val_results=val_results,
        train_irs=train_irs,
        val_irs=val_irs,
        n_trials=n_trials,
        cutoff=cutoff,
        n_train_dates=len(train_dates),
        n_val_dates=len(val_dates),
        nonconst_cap=nonconst_cap,
        carry=carry,
    )


@dataclass(frozen=True)
class _Disclosure:
    """The selected strategy's multiple-testing + robustness disclosure bundle."""

    report: DisclosureReport
    spa_p_vs_momentum: float
    spa_p_vs_round1: float
    sentinel_max_val_ir: float
    wf: WalkForwardReport


def _disclose_and_robustness(
    panel: pd.DataFrame,
    val_panel: pd.DataFrame,
    val_results: list[BenchmarkRelativeResult],
    sel_cand: Candidate,
    *,
    selected: int,
    deflation_n: int,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    horizon: int,
    nonconst_cap: float,
    carry: Sequence[str] = CARRY_FACTORS,
) -> _Disclosure:
    """DSR/PBO/SPA over the full pool + sentinel + anchored-WF/CPCV robustness.

    ``deflation_n`` is the cumulative pre-declared trial count used to deflate the
    DSR / MinBTL (cross-round multiple-testing correction). PBO and SPA are
    computed over THIS round's actual candidate pool and are NOT keyed on it.
    """
    date_sets = [set(r.dates) for r in val_results]
    common = sorted(set.intersection(*date_sets)) if date_sets else []
    candidate_matrix = [_align(r.excess_returns, r.dates, common) for r in val_results]
    report = disclose(
        selected_net_rets=candidate_matrix[selected],
        candidate_return_matrix=candidate_matrix,
        incumbent_excess_matrix=candidate_matrix,  # passive CSI300 incumbent = 0
        n_trials=deflation_n,
        n_observations=len(common),
    )
    mom = _longonly_excess_by_date(
        val_panel,
        {"ret_20d": 1.0},
        index_returns,
        horizon=horizon,
        top_n=5,
        orient={"ret_20d": True},
    )
    r1 = _longonly_excess_by_date(
        val_panel, ROUND1_FROZEN_WEIGHTS, index_returns, horizon=horizon, top_n=5
    )
    sent_irs = _sentinel_val_irs(
        val_panel,
        bench_asof,
        index_returns,
        sel_cand,
        horizon=horizon,
        nonconst_cap=nonconst_cap,
        seeds=SENTINEL_SEEDS,
        carry=carry,
    )
    full = _run_candidate(
        panel,
        bench_asof,
        index_returns,
        sel_cand,
        horizon=horizon,
        nonconst_cap=nonconst_cap,
        carry=carry,
    )
    return _Disclosure(
        report=report,
        spa_p_vs_momentum=_spa_p(candidate_matrix, common, mom),
        spa_p_vs_round1=_spa_p(candidate_matrix, common, r1),
        sentinel_max_val_ir=max(sent_irs) if sent_irs else 0.0,
        wf=wf_report(full, horizon=horizon),
    )


def _assemble_result(
    candidates: list[Candidate],
    sel_cand: Candidate,
    disc: _Disclosure,
    *,
    selected: int,
    finalists: list[int],
    train_results: list[BenchmarkRelativeResult],
    val_results: list[BenchmarkRelativeResult],
    train_irs: list[float],
    val_irs: list[float],
    n_trials: int,
    cutoff: str,
    n_train_dates: int,
    n_val_dates: int,
    nonconst_cap: float,
    carry: Sequence[str] = CARRY_FACTORS,
) -> Round2SearchResult:
    """Plain field-plumbing of the search outputs into the result record."""
    sel_val_ir = val_irs[selected]
    wf = disc.wf
    return Round2SearchResult(
        selected_constraint=sel_cand.constraint,
        selected_k=sel_cand.k,
        selected_a_max=sel_cand.a_max,
        selected_nonconst_cap=nonconst_cap,
        selected_weights=_weights_dict(sel_cand.weights, carry=carry),
        n_trials=n_trials,
        cutoff=cutoff,
        n_train_dates=n_train_dates,
        n_val_dates=n_val_dates,
        train=_summary(train_results[selected]),
        val=_summary(val_results[selected]),
        disclosure=asdict(disc.report),
        spa_p_vs_passive=disc.report.spa_p_value,
        spa_p_vs_momentum=disc.spa_p_vs_momentum,
        spa_p_vs_round1=disc.spa_p_vs_round1,
        sentinel_max_val_ir=disc.sentinel_max_val_ir,
        sentinel_selected_val_ir=sel_val_ir,
        sentinel_passes=sel_val_ir > disc.sentinel_max_val_ir,
        walk_forward={
            "n_periods": float(wf.n_periods),
            "cpcv_ir_mean": wf.cpcv_ir_mean,
            "cpcv_ir_min": wf.cpcv_ir_min,
            "cpcv_ir_frac_positive": wf.cpcv_ir_frac_positive,
            "anchored_final_ir": (
                wf.anchored[-1].information_ratio if wf.anchored else 0.0
            ),
        },
        finalists=[
            {
                "constraint": _CONSTRAINT_CODE[candidates[gi].constraint],
                "k": candidates[gi].k,
                "a_max": candidates[gi].a_max,
                "train_ir": train_irs[gi],
                "val_ir": val_irs[gi],
            }
            for gi in finalists
        ],
    )


# Numeric code so the finalists table stays a flat dict[str, float] (mypy-clean).
_CONSTRAINT_CODE: dict[str, float] = {
    "unconstrained": 0.0,
    "constituent_only": 1.0,
    "size_neutral": 2.0,
    "capped_nonconstituent": 3.0,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument(
        "--carry",
        choices=("r2", "r3", "r4"),
        default="r2",
        help="r2 = round-2 eleven (CARRY_FACTORS); r3 = round-2 eleven + accr "
        "(R3_CARRY_FACTORS, the R3-3 survivor); r4 = round-3 twelve + the four "
        "R4-4 analyst-revision survivors (R4_CARRY_FACTORS). Selects the matching "
        "panel / manifest / out defaults unless overridden.",
    )
    # Default empty so the per-round default is resolved from --carry; an
    # explicit value still overrides it (codex R3-4 P2: `--carry r3` alone must
    # pick the r3 panel/manifest, not the r2 ones, which lack the accr column).
    parser.add_argument("--panel", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    carry, panel_path, manifest_path, out_path = resolve_carry_inputs(
        args.carry, panel=args.panel, manifest=args.manifest, out=args.out
    )
    panel = pd.read_csv(panel_path, dtype={"date": str, "code": str, "ts_code": str})
    panel = neutralize_panel(panel, list(carry), winsor_quantile=WINSOR_QUANTILE)
    split = LockedSplit.load(args.lock, args.snapshot_root)
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))
    dates = sorted(panel["date"].astype(str).unique())
    from backend.marketdata_snapshot.store import SnapshotStore

    # Firewall: the shared single-construction-point helper restricts the
    # benchmark weights AND CSI300 closes to STRICTLY before test_start.
    bench_pit, index_returns = pretest_benchmark_inputs(
        SnapshotStore(args.snapshot_root),
        args.snapshot_root,
        args.benchmark,
        split.test_dates[0],
        dates,
        args.horizon,
    )

    result = search(
        panel,
        bench_pit.asof,
        index_returns,
        manifest_path=manifest_path,
        split=split,
        horizon=args.horizon,
        carry=carry,
    )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

    w = ", ".join(f"{f}={result.selected_weights[f]:.3f}" for f in carry)
    d = result.disclosure
    print("=" * 64)
    print("R2-4 SEARCH — selected benchmark-relative strategy (DEV evidence)")
    print("=" * 64)
    print(
        f"constraint={result.selected_constraint} k={result.selected_k} "
        f"a_max={result.selected_a_max} cap={result.selected_nonconst_cap}"
    )
    print(f"weights: {w}")
    print(
        f"val: IR={result.val['information_ratio']:+.2f} "
        f"excess={result.val['total_excess']:+.2%} "
        f"TE={result.val['tracking_error']:.2%} "
        f"size_active={result.val['mean_size_active']:+.3f} "
        f"forcedUW={result.val['avg_forced_underweight']:.1%}"
    )
    print(
        f"disclosure: DSR={d['dsr']:.3f} (pass>={DSR_FLOOR}: {bool(d['dsr_passes'])}) "
        f"PBO={d['pbo']:.3f} n_trials={int(d['n_trials'])} "
        f"n_obs={int(d['n_observations'])}"
    )
    print(
        f"SPA p-value vs: passive={result.spa_p_vs_passive:.3f} "
        f"momentum={result.spa_p_vs_momentum:.3f} round1={result.spa_p_vs_round1:.3f}"
    )
    print(
        f"sentinel: selected_val_IR={result.sentinel_selected_val_ir:+.2f} "
        f"max_sentinel_IR={result.sentinel_max_val_ir:+.2f} "
        f"passes={result.sentinel_passes}"
    )
    wf = result.walk_forward
    print(
        f"CPCV: IR_mean={wf['cpcv_ir_mean']:+.2f} IR_min={wf['cpcv_ir_min']:+.2f} "
        f"frac_positive={wf['cpcv_ir_frac_positive']:.2f}"
    )
    print(f"-> {out}")
    print(
        "NOTE: DEVELOPMENT EVIDENCE + the single selected strategy, NOT a verdict. "
        "The four-gate PASS/FAIL is the one-shot R2-6 locked test."
    )


__all__ = [
    "Candidate",
    "Round2SearchResult",
    "build_candidates",
    "build_weight_vectors",
    "load_manifest",
    "resolve_carry_inputs",
    "search",
]


if __name__ == "__main__":
    main()
