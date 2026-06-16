"""Information-Coefficient study over the train_val factor panel (Phase 3).

The first empirical diagnostic: for each factor and forward horizon, the
cross-sectional rank Information Coefficient (per-rebalance Spearman
correlation between the factor and the forward return), aggregated to
IC mean / ICIR / t-stat / hit-rate. This measures raw predictiveness on
real A-share data and, critically, confirms or REFUTES the Phase-1
literature priors — above all whether ``ret_20d`` carries a **negative** IC
(A-share short-term reversal) versus the live screener's positive
``momentum_20d`` weight.

This is exploratory (no promotion decision here); the honest multiple-testing
gates (DSR / PBO / SPA) live in ``stats_disclosure``. Overlapping forward
windows make the naive IC t-stat optimistic — reported with that caveat; the
sign and ICIR are the robust takeaways.

Reads only the train_val panel (built under the sacred-split guard), never the
test set. Deterministic.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .factor_lib import FACTOR_NAMES, FACTORS_BY_NAME

FORWARD_COLS: tuple[str, ...] = ("fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d")
_MIN_CROSS_SECTION = 20  # need at least this many code-pairs to rank a date


@dataclass(frozen=True)
class ICSummary:
    """Aggregated IC statistics for one (factor, horizon)."""

    factor: str
    horizon: str
    ic_mean: float
    ic_std: float
    icir: float
    t_stat: float
    hit_rate: float  # fraction of dates with same-sign IC as the mean
    n_dates: int
    expected_sign: int  # literature prior from the registry


def rank_ic_series(panel: pd.DataFrame, factor: str, fwd_col: str) -> list[float]:
    """Per-rebalance-date cross-sectional rank IC (Spearman), NaN-dropped."""
    ics: list[float] = []
    for _, group in panel.groupby("date", sort=True):
        pair = group[[factor, fwd_col]].dropna()
        if len(pair) < _MIN_CROSS_SECTION:
            continue
        # Degenerate (all-equal) factor or label → undefined rank corr; skip.
        if pair[factor].nunique() < 2 or pair[fwd_col].nunique() < 2:
            continue
        rho, _ = spearmanr(pair[factor].to_numpy(), pair[fwd_col].to_numpy())
        if rho == rho:  # not NaN
            ics.append(float(rho))
    return ics


def summarize_ic(factor: str, fwd_col: str, ics: list[float]) -> ICSummary:
    """Aggregate an IC series into mean / ICIR / t-stat / hit-rate."""
    n = len(ics)
    expected = FACTORS_BY_NAME[factor].expected_ic_sign
    if n == 0:
        return ICSummary(factor, fwd_col, 0.0, 0.0, 0.0, 0.0, 0.0, 0, expected)
    arr = np.array(ics, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    icir = mean / std if std > 0 else 0.0
    t_stat = icir * np.sqrt(n) if std > 0 else 0.0
    same_sign = float(np.mean(np.sign(arr) == np.sign(mean))) if mean != 0 else 0.0
    return ICSummary(
        factor=factor,
        horizon=fwd_col,
        ic_mean=mean,
        ic_std=std,
        icir=icir,
        t_stat=float(t_stat),
        hit_rate=same_sign,
        n_dates=n,
        expected_sign=expected,
    )


def factor_correlation(panel: pd.DataFrame) -> pd.DataFrame:
    """Mean cross-sectional rank correlation between factors (collinearity)."""
    factors = [f for f in FACTOR_NAMES if f in panel.columns]
    per_date: list[pd.DataFrame] = []
    for _, group in panel.groupby("date", sort=True):
        sub = group[factors].dropna()
        if len(sub) < _MIN_CROSS_SECTION:
            continue
        per_date.append(sub.rank().corr())
    if not per_date:
        return pd.DataFrame(index=factors, columns=factors, dtype=float)
    # Mean of the per-date factor×factor correlation matrices.
    return pd.concat(per_date).groupby(level=0).mean().loc[factors, factors]


def study(panel: pd.DataFrame) -> list[ICSummary]:
    """Compute IC summaries for every factor × forward horizon."""
    out: list[ICSummary] = []
    for factor in FACTOR_NAMES:
        if factor not in panel.columns:
            continue
        for fwd in FORWARD_COLS:
            if fwd not in panel.columns:
                continue
            out.append(summarize_ic(factor, fwd, rank_ic_series(panel, factor, fwd)))
    return out


def _fmt_report(summaries: list[ICSummary], corr: pd.DataFrame) -> str:
    lines = ["# Factor IC study (train_val)\n"]
    lines.append(
        "| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        empirical_sign = int(np.sign(s.ic_mean))
        aligned = "yes" if empirical_sign == s.expected_sign else "**NO**"
        lines.append(
            f"| {s.factor} | {s.horizon} | {s.ic_mean:+.4f} | {s.icir:+.3f} | "
            f"{s.t_stat:+.2f} | {s.hit_rate:.2f} | {s.n_dates} | "
            f"{s.expected_sign:+d} | {aligned} |"
        )
    lines.append("\n## Factor cross-correlation (mean cross-sectional rank corr)\n")
    lines.append(corr.round(2).to_string())
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val.parquet"
    )
    parser.add_argument("--out", default="data/factor_research/ic_study.md")
    args = parser.parse_args()

    panel = pd.read_parquet(args.panel)
    summaries = study(panel)
    corr = factor_correlation(panel)
    report = _fmt_report(summaries, corr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()
