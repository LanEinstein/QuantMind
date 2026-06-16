"""Sobol pre-declared-N weight search + honest single-strategy selection (Phase 3).

The closing Phase-3 module. It searches the 7-factor non-negative weight
*simplex* (weights ≥ 0, sum = 1) with a **pre-declared** Sobol budget, picks the
**single** strategy that will be carried into the one-shot Phase-4 locked-test
evaluation, and attaches the full multiple-testing disclosure.

Honesty discipline (this is the whole point — see the research brief §0):

* **Selection never touches test.** The panel is split *inside* train_val at
  ``TRAIN_VAL_CUTOFF`` into an inner train (search) and an inner val (select),
  with a purge gap so a train forward-label cannot leak across the boundary.
  Weights are *searched* on inner-train Sharpe and the unique winner is *picked*
  on inner-val Sharpe — the sacred test window (Phase 4) is physically
  unreachable here and every panel date is re-checked through
  :meth:`LockedSplit.assert_all_not_test`.
* **Pre-declared trial count.** ``SEARCH_N`` is fixed before the run and recorded
  as the Deflated-Sharpe ``n_trials`` — the search cannot be silently widened to
  manufacture a winner.
* **Disclosure, not a gate that fabricates alpha.** DSR (main gate, deflated for
  the trial count) / PBO-CSCV / Hansen SPA vs the live *momentum* incumbent are
  reported to quantify confidence; they only lower the false-positive rate. The
  real verdict on profitability is the locked test set, evaluated **once**, in
  Phase 4 — a low DSR here is reported, not papered over.
* **Economic-mechanism provenance.** Every factor carries a registered mechanism
  (``factor_lib.FACTORS``), so any selected weighting is mechanism-backed by
  construction — no pure data-mined winner can survive.

Reads only the train_val panel; deterministic (Sobol + SPA both fixed-seed).
``backend.strategy_evolution`` (via ``stats_disclosure``) is import-allowed;
``backend.{llm,agents,mirofish}`` is not (numeric strategy is LLM-free).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from scipy.stats import qmc

# ``_kraemer_simplex`` is the shared sorted-spacings simplex map already used by
# the live evolution lane — reused here (not re-inlined) so the construction has
# one implementation. ``backend.strategy_evolution`` is import-allowed.
from backend.strategy_evolution.quant_param_search import _kraemer_simplex

from .factor_lib import FACTOR_NAMES, FACTORS_BY_NAME
from .locked_split import LockedSplit
from .portfolio_backtest import (
    BacktestResult,
    backtest,
    group_by_date,
    load_benchmark,
)
from .stats_disclosure import DisclosureReport, disclose

# --- Pre-declared search constants (locked; never widened mid-run) -----------
SEARCH_N: int = 512  # power of 2 (Sobol balance); recorded as DSR n_trials
SEARCH_SEED: int = 20260616
# Sobol weight vectors map to factors positionally by this pinned order; a
# silent reorder of factor_lib.FACTORS would remap every searched weight, so we
# fail closed on a mismatch (mirroring locked_split's dates_sha256 discipline).
EXPECTED_FACTOR_ORDER: tuple[str, ...] = (
    "ret_5d",
    "ret_20d",
    "vol_20d",
    "max_20d",
    "ep_ttm",
    "turn_20d",
    "amihud_20d",
)
# Inner split inside train_val (both windows are train_val — never test):
# train = panel date <= cutoff (~2015-2022, 8y); val = date > cutoff (~2023-01
# .. 2025-04-25, 2.3y). The purge drops the first few val rebalances so a
# train date's forward label (<= 20 td ahead) cannot overlap a kept val date.
TRAIN_VAL_CUTOFF: str = "20221230"
PURGE_REBALANCES: int = 4  # ~20 td at the panel's 5-td rebalance cadence
TOP_K_FINALISTS: int = 16  # train-robust shortlist re-scored on val
# Production book is ≤5 slots (V-001 max_total_positions=5); top_n=10 is a
# robustness cross-check only, never the deployed configuration.
HORIZON: int = 5
TOP_N: int = 5
ROBUSTNESS_TOP_N: int = 10


@dataclass(frozen=True)
class FinalistRecord:
    """One train-robust finalist's weights + its train/val Sharpe."""

    weights: dict[str, float]
    train_sharpe: float
    val_sharpe: float


@dataclass(frozen=True)
class WeightSearchResult:
    """The single selected strategy + its full honest disclosure."""

    selected_weights: dict[str, float]
    factor_names: tuple[str, ...]  # positional order the search mapped weights by
    mechanisms: dict[str, str]
    n_trials: int
    cutoff: str
    purge_rebalances: int
    n_train_dates: int
    n_val_dates: int
    train: dict[str, float]
    val: dict[str, float]
    val_robustness_top_n_10: dict[str, float]
    incumbent_momentum_val: dict[str, float]
    disclosure: dict[str, float]
    finalists: list[FinalistRecord]
    selected_val_net_returns: tuple[float, ...]


def simplex_sobol(n: int, dim: int, seed: int) -> list[tuple[float, ...]]:
    """``n`` Sobol points on the ``dim``-simplex (weights ≥ 0, sum = 1).

    Draws a scrambled Sobol sequence in ``[0, 1]^(dim-1)`` and maps each point
    to the simplex by the Kraemer sorted-spacings construction: sort the
    coordinates and take successive gaps (including 0→first and last→1), giving
    ``dim`` non-negative gaps that sum to 1. Uniform-ish, low-discrepancy
    coverage of the whole weight simplex.
    """
    if dim < 2:
        raise ValueError("dim must be >= 2")
    sampler = qmc.Sobol(d=dim - 1, scramble=True, seed=seed)
    points = sampler.random(n)
    return [tuple(_kraemer_simplex([float(x) for x in u])) for u in points]


def split_train_val(
    panel: pd.DataFrame,
    *,
    cutoff: str = TRAIN_VAL_CUTOFF,
    purge: int = PURGE_REBALANCES,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """Inner train/val split of the train_val panel with a post-cutoff purge."""
    dates = sorted(panel["date"].astype(str).unique())
    train_dates = [d for d in dates if d <= cutoff]
    val_dates = [d for d in dates if d > cutoff][purge:]
    train_set, val_set = set(train_dates), set(val_dates)
    date_str = panel["date"].astype(str)
    train = panel[date_str.isin(train_set)]
    val = panel[date_str.isin(val_set)]
    return train, val, train_dates, val_dates


def _weights_dict(w: Sequence[float]) -> dict[str, float]:
    """Pair a weight vector with the canonical factor names."""
    return dict(zip(FACTOR_NAMES, (float(x) for x in w), strict=True))


def _summary(r: BacktestResult) -> dict[str, float]:
    """Scalar summary of a backtest (no big equity arrays) for JSON output."""
    return {
        "n_periods": float(r.n_periods),
        "total_return": r.total_return,
        "annual_return": r.annual_return,
        "sharpe": r.sharpe,
        "max_drawdown": r.max_drawdown,
        "bench_total_return": r.bench_total_return,
        "excess_vs_bench": r.excess_vs_bench,
        "avg_turnover": r.avg_turnover,
        "win_rate": r.win_rate,
    }


def _align(result: BacktestResult, dates: list[str]) -> list[float]:
    """Net returns of ``result`` over exactly ``dates`` (time-aligned)."""
    by_date = dict(zip(result.dates, result.net_returns, strict=True))
    return [by_date[d] for d in dates]


def _run_pass(
    candidates: list[tuple[float, ...]],
    panel: pd.DataFrame,
    *,
    benchmark: dict[str, float] | None,
    horizon: int,
    top_n: int,
) -> list[BacktestResult]:
    """Backtest every candidate over one panel (grouped once, reused)."""
    groups = group_by_date(panel)
    return [
        backtest(
            panel,
            _weights_dict(w),
            benchmark=benchmark,
            horizon=horizon,
            top_n=top_n,
            groups=groups,
        )
        for w in candidates
    ]


def _select(
    train_results: list[BacktestResult],
    val_results: list[BacktestResult],
    *,
    top_k: int,
) -> tuple[list[int], int]:
    """Train-robust shortlist + unique winner (best val Sharpe among them).

    Deterministic: shortlist by (−train Sharpe, index); winner by
    (val Sharpe, −index) so ties resolve to the lowest candidate index.
    Selecting the val-best from the *train*-robust top_k (not the global
    val-max) is the cross-validation discipline that curbs val-overfitting.
    """
    order = sorted(
        range(len(train_results)), key=lambda i: (-train_results[i].sharpe, i)
    )
    finalists = order[:top_k]
    selected = max(finalists, key=lambda i: (val_results[i].sharpe, -i))
    return finalists, selected


def _disclosure(
    val_results: list[BacktestResult],
    selected: int,
    incumbent_val: BacktestResult,
    *,
    n_trials: int,
) -> tuple[DisclosureReport, list[float]]:
    """Run the four honest-disclosure gates over the FULL searched pool.

    ``candidate_return_matrix`` is *every* searched candidate's val net returns
    (the full pool, never just the survivors — the ``disclose()`` contract), so
    PBO-CSCV reflects the real ``n_trials``-wide search rather than a flattering
    survivor subset. DSR deflates by ``n_trials``; SPA tests each candidate's
    val excess over the momentum incumbent. Series are intersected to a common
    date axis (equal-length, period-aligned excess).

    Note on MinBTL: ``minimum_backtest_length`` is daily-calibrated
    (periods_per_year=252) while these are 5-day rebalance periods, so
    ``minbtl_admits`` is a conservative floor a ~2.3y weekly val rarely clears —
    read DSR / PBO / SPA as the primary disclosures, and the locked test set
    (Phase 4) as the verdict.
    """
    date_sets = [set(r.dates) for r in val_results] + [set(incumbent_val.dates)]
    common = sorted(set.intersection(*date_sets)) if date_sets else []
    candidate_matrix = [_align(r, common) for r in val_results]
    incumbent_vec = _align(incumbent_val, common)
    excess_matrix = [
        [c - i for c, i in zip(row, incumbent_vec, strict=True)]
        for row in candidate_matrix
    ]
    selected_net = candidate_matrix[selected] if candidate_matrix else []
    report = disclose(
        selected_net_rets=selected_net,
        candidate_return_matrix=candidate_matrix,
        incumbent_excess_matrix=excess_matrix,
        n_trials=n_trials,
        n_observations=len(common),
    )
    return report, selected_net


def _assemble_result(
    *,
    selected_w: dict[str, float],
    candidates: list[tuple[float, ...]],
    finalists: list[int],
    selected: int,
    train_results: list[BacktestResult],
    val_results: list[BacktestResult],
    incumbent_val: BacktestResult,
    val_robust: BacktestResult,
    report: DisclosureReport,
    selected_net: list[float],
    n: int,
    cutoff: str,
    purge: int,
    n_train_dates: int,
    n_val_dates: int,
) -> WeightSearchResult:
    """Plain field-plumbing of the search outputs into the result record."""
    return WeightSearchResult(
        selected_weights=selected_w,
        factor_names=EXPECTED_FACTOR_ORDER,
        mechanisms={
            f: FACTORS_BY_NAME[f].mechanism for f, w in selected_w.items() if w > 0
        },
        n_trials=n,
        cutoff=cutoff,
        purge_rebalances=purge,
        n_train_dates=n_train_dates,
        n_val_dates=n_val_dates,
        train=_summary(train_results[selected]),
        val=_summary(val_results[selected]),
        val_robustness_top_n_10=_summary(val_robust),
        incumbent_momentum_val=_summary(incumbent_val),
        disclosure=asdict(report),
        finalists=[
            FinalistRecord(
                weights=_weights_dict(candidates[gi]),
                train_sharpe=train_results[gi].sharpe,
                val_sharpe=val_results[gi].sharpe,
            )
            for gi in finalists
        ],
        selected_val_net_returns=tuple(selected_net),
    )


def search(
    panel: pd.DataFrame,
    benchmark: dict[str, float] | None = None,
    *,
    split: LockedSplit | None = None,
    n: int = SEARCH_N,
    seed: int = SEARCH_SEED,
    cutoff: str = TRAIN_VAL_CUTOFF,
    purge: int = PURGE_REBALANCES,
    top_k: int = TOP_K_FINALISTS,
    horizon: int = HORIZON,
    top_n: int = TOP_N,
) -> WeightSearchResult:
    """Run the Sobol search and return the single selected strategy + disclosure.

    Scores all ``n`` simplex weightings on inner-train AND inner-val, picks the
    unique winner (best val Sharpe among the ``top_k`` train-robust finalists),
    and discloses DSR / PBO / SPA over the *full* searched pool vs the momentum
    incumbent. ``split`` defaults to the on-disk locked split (used only to
    re-assert no panel date is in the sacred test window).
    """
    if tuple(FACTOR_NAMES) != EXPECTED_FACTOR_ORDER:
        raise ValueError(
            f"factor-order drift: {FACTOR_NAMES} != pinned {EXPECTED_FACTOR_ORDER} "
            "— searched weight vectors would remap; refusing (fail closed)."
        )
    if split is None:
        split = LockedSplit.load()
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))  # red line 1

    train, val, train_dates, val_dates = split_train_val(
        panel, cutoff=cutoff, purge=purge
    )
    candidates = simplex_sobol(n, len(FACTOR_NAMES), seed)
    train_results = _run_pass(
        candidates, train, benchmark=benchmark, horizon=horizon, top_n=top_n
    )
    val_results = _run_pass(
        candidates, val, benchmark=benchmark, horizon=horizon, top_n=top_n
    )
    finalists, selected = _select(train_results, val_results, top_k=top_k)

    # Momentum incumbent = top-5 by HIGH ret_20d (the live FACTOR_WEIGHTS bet).
    incumbent_val = backtest(
        val, {"ret_20d": 1.0}, benchmark=benchmark,
        horizon=horizon, top_n=top_n, orient={"ret_20d": True},
    )
    report, selected_net = _disclosure(val_results, selected, incumbent_val, n_trials=n)
    val_robust = backtest(
        val, _weights_dict(candidates[selected]), benchmark=benchmark,
        horizon=horizon, top_n=ROBUSTNESS_TOP_N,
    )
    return _assemble_result(
        selected_w=_weights_dict(candidates[selected]),
        candidates=candidates,
        finalists=finalists,
        selected=selected,
        train_results=train_results,
        val_results=val_results,
        incumbent_val=incumbent_val,
        val_robust=val_robust,
        report=report,
        selected_net=selected_net,
        n=n,
        cutoff=cutoff,
        purge=purge,
        n_train_dates=len(train_dates),
        n_val_dates=len(val_dates),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", default="data/factor_research/panel_train_val.csv")
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--n", type=int, default=SEARCH_N)
    parser.add_argument("--top-n", type=int, default=TOP_N)
    parser.add_argument(
        "--out", default="data/factor_research/weight_search_result.json"
    )
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"date": str, "code": str})
    bench = load_benchmark(args.benchmark)
    result = search(panel, bench, n=args.n, top_n=args.top_n)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")

    w = ", ".join(f"{f}={result.selected_weights[f]:.3f}" for f in FACTOR_NAMES)
    d = result.disclosure
    print(f"selected weights: {w}")
    print(
        f"train: sharpe={result.train['sharpe']:+.2f} "
        f"total={result.train['total_return']:+.2%}"
    )
    print(
        f"val:   sharpe={result.val['sharpe']:+.2f} "
        f"total={result.val['total_return']:+.2%} "
        f"excess={result.val['excess_vs_bench']:+.2%} "
        f"mdd={result.val['max_drawdown']:.2%} turn={result.val['avg_turnover']:.2f}"
    )
    robust = result.val_robustness_top_n_10
    incumbent = result.incumbent_momentum_val
    print(
        f"val top_n=10 (robustness): sharpe={robust['sharpe']:+.2f} "
        f"total={robust['total_return']:+.2%}"
    )
    print(
        f"incumbent momentum val: sharpe={incumbent['sharpe']:+.2f} "
        f"total={incumbent['total_return']:+.2%}"
    )
    print(
        f"disclosure: DSR={d['dsr']:.3f} (pass>={d['dsr_passes']}) "
        f"PBO={d['pbo']:.3f} SPA_p={d['spa_p_value']:.3f} "
        f"MinBTL_admits={d['minbtl_admits']} n_trials={d['n_trials']} "
        f"n_obs={d['n_observations']}"
    )
    print(f"-> {out}")
    print(
        "NOTE: these are inner-val (in-sample) results + honest disclosure; the "
        "PASS/FAIL verdict is the locked test set, evaluated once, in Phase 4."
    )


if __name__ == "__main__":
    main()
