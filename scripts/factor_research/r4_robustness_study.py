"""R5 train_val robustness study of the frozen round-4 strategy (DEV evidence).

The round-4 locked test PASSed all four gates (+2.68% excess), but the dev
anti-overfit gates did NOT confirm it (DSR 0.007, and the R4-5 shuffled-composite
SENTINEL FAILED: a noise val IR 1.34 > the selected 0.71). The search also picked
the MOST AGGRESSIVE tilt in the grid (k=0.20, a_max=0.04). This module probes —
on the train_val panel ONLY, the sealed test never touched — whether the
provisional PASS is a robust small analyst edge or a fragile artifact of that
aggressive tilt:

* **Tilt-strength sweep** (k ∈ {0.05, 0.10, 0.20}): does the frozen composite's
  positive train_val excess/IR survive weaker tilts, or only the aggressive one?
* **Sentinel at each k**: is the R4-5 sentinel failure specific to k=0.20 (the
  aggressive tilt amplifying noise) — i.e. does a shuffled-composite control stop
  beating the real composite once the tilt is moderate?
* **Factor ablation** at the frozen k: drop `rev_diff` (the 0.216-dominant
  analyst factor → single-factor concentration?) and zero the whole analyst block
  (its marginal train_val contribution vs the round-3 twelve).

This is DEVELOPMENT EVIDENCE, NEVER a verdict. It does not re-touch the sealed
test window and does not change the frozen strategy; the four-gate verdict stands
at R4-6 and the binding confirmation is the forward window. Deterministic;
benchmark inputs are firewall-restricted to ``< test_start`` (train_val only).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from .benchmark_relative import (
    R4_CARRY_FACTORS,
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .round2_search import _shuffle_neut

WINSOR_QUANTILE: float = 0.01
# The aggressive frozen tilt the R4-5 search picked, plus two weaker tilts.
K_SWEEP: tuple[float, ...] = (0.05, 0.10, 0.20)
# Sentinel shuffle seeds (a no-signal control; same construction as round2_search).
SENTINEL_SEEDS: tuple[int, ...] = (101, 202, 303, 404, 505)
# The four R4-4 analyst-revision survivors (the round-4 alpha block).
ANALYST_FACTORS: tuple[str, ...] = ("np_rev", "rev_diff", "tp_impl", "cover_chg")
# The dominant analyst factor in the frozen composite (weight 0.216).
DOMINANT_ANALYST: str = "rev_diff"


def _summary(r: BenchmarkRelativeResult) -> dict[str, float]:
    """Scalar train_val summary of one benchmark-relative backtest."""
    return {
        "n_periods": float(r.n_periods),
        "total_excess": r.total_excess,
        "information_ratio": r.information_ratio,
        "tracking_error": r.tracking_error,
        "avg_turnover": r.avg_turnover,
        "mean_size_active": r.mean_size_active,
    }


def run_variant(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    k: float,
    a_max: float,
    constraint: str,
    nonconst_cap: float,
    horizon: int,
) -> BenchmarkRelativeResult:
    """Backtest one (weights, k) variant over the train_val panel."""
    return benchmark_relative_backtest(
        panel,
        bench_asof,
        index_returns,
        weights=weights,
        horizon=horizon,
        k=k,
        a_max=a_max,
        exposure_constraint=constraint,
        nonconst_cap=nonconst_cap,
    )


def tilt_sweep(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    a_max: float,
    constraint: str,
    nonconst_cap: float,
    horizon: int,
    ks: Sequence[float] = K_SWEEP,
) -> dict[str, dict[str, float]]:
    """The frozen composite at each tilt strength k (does positive excess survive?)."""
    out: dict[str, dict[str, float]] = {}
    for k in ks:
        res = run_variant(
            panel,
            bench_asof,
            index_returns,
            weights=weights,
            k=k,
            a_max=a_max,
            constraint=constraint,
            nonconst_cap=nonconst_cap,
            horizon=horizon,
        )
        out[f"k={k:.2f}"] = _summary(res)
    return out


def sentinel_ir_at_k(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    k: float,
    a_max: float,
    constraint: str,
    nonconst_cap: float,
    horizon: int,
    seeds: Sequence[int] = SENTINEL_SEEDS,
) -> float:
    """Max IR of an equal-weight composite on SHUFFLED neut columns at tilt k.

    A no-signal control: if this matches/exceeds the real composite's IR at the
    same k, the construction is not separating signal from noise at that tilt.
    """
    equal = {f: 1.0 for f in R4_CARRY_FACTORS}
    irs: list[float] = []
    for seed in seeds:
        shuffled = _shuffle_neut(panel, seed, carry=R4_CARRY_FACTORS)
        res = run_variant(
            shuffled,
            bench_asof,
            index_returns,
            weights=equal,
            k=k,
            a_max=a_max,
            constraint=constraint,
            nonconst_cap=nonconst_cap,
            horizon=horizon,
        )
        irs.append(res.information_ratio)
    return max(irs) if irs else 0.0


def factor_ablation(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    k: float,
    a_max: float,
    constraint: str,
    nonconst_cap: float,
    horizon: int,
) -> dict[str, dict[str, float]]:
    """Frozen vs drop-dominant-analyst vs zero-analyst-block (marginal contribution)."""
    drop_dominant = {
        f: (0.0 if f == DOMINANT_ANALYST else w) for f, w in weights.items()
    }
    no_analyst = {
        f: (0.0 if f in ANALYST_FACTORS else w) for f, w in weights.items()
    }
    variants = {
        "frozen_full": dict(weights),
        "drop_rev_diff": drop_dominant,
        "no_analyst_block": no_analyst,
    }
    out: dict[str, dict[str, float]] = {}
    for name, w in variants.items():
        res = run_variant(
            panel,
            bench_asof,
            index_returns,
            weights=w,
            k=k,
            a_max=a_max,
            constraint=constraint,
            nonconst_cap=nonconst_cap,
            horizon=horizon,
        )
        out[name] = _summary(res)
    return out


@dataclass(frozen=True)
class RobustnessReport:
    """The full train_val robustness bundle for the frozen round-4 strategy."""

    frozen_k: float
    frozen_a_max: float
    frozen_constraint: str
    n_train_val_dates: int
    tilt_sweep: dict[str, dict[str, float]]
    sentinel_ir_by_k: dict[str, float]
    frozen_ir_by_k: dict[str, float]
    sentinel_beaten_by_k: dict[str, bool]
    ablation: dict[str, dict[str, float]]


def build_report(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    k: float,
    a_max: float,
    constraint: str,
    nonconst_cap: float,
    horizon: int,
    ks: Sequence[float] = K_SWEEP,
    seeds: Sequence[int] = SENTINEL_SEEDS,
) -> RobustnessReport:
    """Run the full robustness study (tilt sweep + sentinel-by-k + ablation)."""
    sweep = tilt_sweep(
        panel,
        bench_asof,
        index_returns,
        weights=weights,
        a_max=a_max,
        constraint=constraint,
        nonconst_cap=nonconst_cap,
        horizon=horizon,
        ks=ks,
    )
    frozen_ir = {f"k={k_:.2f}": sweep[f"k={k_:.2f}"]["information_ratio"] for k_ in ks}
    sentinel_ir: dict[str, float] = {}
    beaten: dict[str, bool] = {}
    for k_ in ks:
        s_ir = sentinel_ir_at_k(
            panel,
            bench_asof,
            index_returns,
            k=k_,
            a_max=a_max,
            constraint=constraint,
            nonconst_cap=nonconst_cap,
            horizon=horizon,
            seeds=seeds,
        )
        key = f"k={k_:.2f}"
        sentinel_ir[key] = s_ir
        # "beaten" = the real composite's IR exceeds the best noise IR (sentinel
        # PASSES at this k); False = noise matched/beat the real signal (fails).
        beaten[key] = frozen_ir[key] > s_ir
    ablation = factor_ablation(
        panel,
        bench_asof,
        index_returns,
        weights=weights,
        k=k,
        a_max=a_max,
        constraint=constraint,
        nonconst_cap=nonconst_cap,
        horizon=horizon,
    )
    return RobustnessReport(
        frozen_k=k,
        frozen_a_max=a_max,
        frozen_constraint=constraint,
        n_train_val_dates=int(panel["date"].nunique()),
        tilt_sweep=sweep,
        sentinel_ir_by_k=sentinel_ir,
        frozen_ir_by_k=frozen_ir,
        sentinel_beaten_by_k=beaten,
        ablation=ablation,
    )


def main() -> None:
    from backend.marketdata_snapshot.store import SnapshotStore

    from .r2_benchmark_relative_diagnostics import pretest_benchmark_inputs
    from .round4_locked_test import (
        FROZEN_R4_A_MAX,
        FROZEN_R4_CONSTRAINT,
        FROZEN_R4_K,
        FROZEN_R4_NONCONST_CAP,
        FROZEN_R4_WEIGHTS_3DP,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_r4.csv"
    )
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument(
        "--out", default="data/factor_research/round4_robustness_study.json"
    )
    args = parser.parse_args()

    panel = pd.read_csv(
        args.panel, dtype={"date": str, "code": str, "ts_code": str}
    )
    panel = neutralize_panel(
        panel, list(R4_CARRY_FACTORS), winsor_quantile=WINSOR_QUANTILE
    )
    split = LockedSplit.load(args.lock, args.snapshot_root)
    dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(dates)  # firewall: train_val ONLY

    # Firewall: benchmark weights + CSI300 closes STRICTLY before test_start.
    bench_pit, index_returns = pretest_benchmark_inputs(
        SnapshotStore(args.snapshot_root),
        args.snapshot_root,
        args.benchmark,
        split.test_dates[0],
        dates,
        args.horizon,
    )

    report = build_report(
        panel,
        bench_pit.asof,
        index_returns,
        weights=FROZEN_R4_WEIGHTS_3DP,
        k=FROZEN_R4_K,
        a_max=FROZEN_R4_A_MAX,
        constraint=FROZEN_R4_CONSTRAINT,
        nonconst_cap=FROZEN_R4_NONCONST_CAP,
        horizon=args.horizon,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    _print_report(report, out)


def _print_report(r: RobustnessReport, out: Path) -> None:
    print("=" * 68)
    print("R5 ROBUSTNESS STUDY — frozen round-4 strategy (train_val DEV evidence)")
    print("=" * 68)
    print(
        f"frozen: {r.frozen_constraint} k={r.frozen_k} a_max={r.frozen_a_max} "
        f"| train_val dates={r.n_train_val_dates}"
    )
    print("-" * 68)
    print("TILT SWEEP (does positive excess survive weaker tilts?)")
    for key, s in r.tilt_sweep.items():
        print(
            f"  {key}: excess={s['total_excess']:+.2%} "
            f"IR={s['information_ratio']:+.2f} "
            f"TE={s['tracking_error']:.2%} size_active={s['mean_size_active']:+.3f}"
        )
    print("-" * 68)
    print("SENTINEL by k (real IR vs best shuffled-noise IR; beaten=real>noise=PASS)")
    for key in r.frozen_ir_by_k:
        print(
            f"  {key}: real_IR={r.frozen_ir_by_k[key]:+.2f} "
            f"noise_IR={r.sentinel_ir_by_k[key]:+.2f} "
            f"beaten={r.sentinel_beaten_by_k[key]}"
        )
    print("-" * 68)
    print(f"FACTOR ABLATION (at frozen k={r.frozen_k})")
    for name, s in r.ablation.items():
        print(
            f"  {name:18s}: excess={s['total_excess']:+.2%} "
            f"IR={s['information_ratio']:+.2f}"
        )
    print("-" * 68)
    print(
        "DEV EVIDENCE ONLY — train_val, sealed test never touched, frozen strategy "
        "unchanged. The binding confirmation is the forward window."
    )
    print(f"-> {out}")


__all__ = [
    "ANALYST_FACTORS",
    "DOMINANT_ANALYST",
    "K_SWEEP",
    "RobustnessReport",
    "build_report",
    "factor_ablation",
    "run_variant",
    "sentinel_ir_at_k",
    "tilt_sweep",
]


if __name__ == "__main__":
    main()
