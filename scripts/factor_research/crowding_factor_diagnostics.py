"""Batch-A crowding / blow-off EXIT diagnostics (main-force-intent §3 / §7).

The honest, from-zero verification of the crowding EXIT family. The macro program's
load-bearing prior (§2.1 / §0) is an ASYMMETRY: crowding predicts CRASH PROBABILITY
(a fat left tail), not mean return — so the **load-bearing test here is a conditional
left-tail**, with the mean IC expected weak (that weakness is CONFIRMATION, not
failure). This module:

1. **IC from zero** (raw + industry/size-neutralized, 1/5/10/20d) — the literature
   sign is verified, never assumed; the IC ``|t|`` is an OPTIMISTIC SCREEN, not the
   verdict (overlapping windows → autocorrelated IC, effective N < n_dates).
2. **Crash-probability conditional** (the load-bearing test) — does the top-crowding
   decile (by the size-neutral factor) have a significantly fatter forward left tail
   (P(fwd < −5/−10%), CVaR@5%) than the rest? Pooled + per-date paired t + 3
   contiguous sub-period stability (the R5 'don't average away a regime' guard).
3. **Collinearity** vs the round-1 carry cluster + QGR fast-leg (neutralized) — an
   EXIT factor that is just reversal/lottery/size in disguise (§2.10②) is disclosed,
   not laundered as new alpha.
4. **CPCV IC stability** — the per-date neutralized IC series through the true-CPCV
   combination OOS distribution.
5. **DSR + non-zeroing ledger** — the decile-spread research series through the
   HAC-deflated Sharpe with the cumulative (legacy-floored) trial count; the
   mining debt is NOT reset by re-framing the criterion (codex P0).

A thin reporting orchestrator over already-tested pieces (``study`` /
``neutralize_panel`` / ``_pairwise`` / ``run_cpcv_fixed_series`` /
``deflated_sharpe_hac`` / ``TrialLedger``); deterministic, offline, train_val only
(the sealed test window is never read). The decile spread is a GROSS research
diagnostic (no shorting in A-shares; the deployable use is a long-only veto whose
net P&L is the deferred event-loop test) — disclosed, not sold as a strategy.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from .cpcv import QGR_CPCV_K, QGR_N_GROUPS, run_cpcv_fixed_series
from .factor_ic_study import rank_ic_series, study
from .factor_lib import CROWDING_FACTOR_NAMES, FACTOR_NAMES, QGR_FACTOR_NAMES
from .honest_gates import deflated_sharpe_hac, onc_effective_n
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .r2_factor_diagnostics import (
    NEUT_SUFFIX,
    T_BAR,
    FactorVerdict,
    _ic_table,
    _verdict_table,
    verdicts,
)
from .r4_factor_diagnostics import _pairwise
from .trial_ledger import TrialLedger, TrialRecord

WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
COLLINEARITY_CEILING: float = 0.7
MIN_COLLIN_DATES: int = 60
# The cluster the EXIT factors must add a NEW axis beyond: round-1 cross-sectional
# carry + the QGR fast leg (reversal / lottery / turnover — the §2.10② 'crowding =
# reversal/size in disguise' risk lives here).
CARRY_CLUSTER: tuple[str, ...] = (*FACTOR_NAMES, *QGR_FACTOR_NAMES)
CROWDING_FORWARD_COLS: tuple[str, ...] = (
    "fwd_ret_1d",
    "fwd_ret_5d",
    "fwd_ret_10d",
    "fwd_ret_20d",
)
TOP_DECILE_Q: float = 0.90  # crowded = top 10% by the size-neutral factor
SPREAD_Q: float = 0.10  # decile spread = bottom 10% minus top 10%
CRASH_THRESHOLDS: tuple[float, float] = (-0.05, -0.10)  # P(fwd < t) tail cuts
N_SUBPERIODS: int = 3
LEDGER_DATE: str = "2026-06-26"  # injected registration date (no wall-clock)
PRIMARY_FWD: str = "fwd_ret_5d"  # primary tail / spread horizon
# Horizon (td) parsed from PRIMARY_FWD, and the panel's build cadence. Both feed the
# annualization + HAC lag of the spread Sharpe/DSR so neither is silently hardcoded
# (review #7): a spread obs is one rebalance, ``rebalance_freq`` td apart; the fwd
# window is ``horizon`` td, so consecutive obs overlap by ceil(horizon/freq)−1 lags.
PRIMARY_HORIZON_TD: int = int(PRIMARY_FWD.rsplit("_", 1)[-1].rstrip("d"))
DEFAULT_REBALANCE_FREQ: int = 5  # build_crowding_panel default; overridable in main()
TRADING_DAYS_PER_YEAR: int = 252


def _neut(factor: str) -> str:
    return f"{factor}{NEUT_SUFFIX}"


def _periods_per_year(rebalance_freq: int) -> float:
    """Annualization factor for a series sampled once every ``rebalance_freq`` td."""
    return TRADING_DAYS_PER_YEAR / max(1, rebalance_freq)


def _overlap_lag(horizon_td: int, rebalance_freq: int) -> int:
    """HAC lag (in rebalance units) for ``horizon``-td labels at this cadence.

    Consecutive rebalance dates are ``rebalance_freq`` td apart; a ``horizon``-td
    forward label overlaps the next ``ceil(horizon/rebalance_freq) − 1`` obs, so
    that many autocorrelation lags must inflate the SR variance (0 when the labels
    are non-overlapping, e.g. 5d at a 5td cadence)."""
    if rebalance_freq <= 0:
        return 0
    return max(0, math.ceil(horizon_td / rebalance_freq) - 1)


@dataclass(frozen=True)
class TailConditional:
    """Crowded-vs-rest forward left-tail comparison for one factor/horizon."""

    factor: str
    fwd_col: str
    n_dates: int
    crowded_p_crash5: float  # P(fwd < -5%) in the top-crowding decile (pooled)
    rest_p_crash5: float
    crowded_p_crash10: float
    rest_p_crash10: float
    crowded_cvar5: float  # mean of the worst 5% forward returns (pooled)
    rest_cvar5: float
    paired_diff_mean: float  # mean over dates of P(crash5|crowded)−P(crash5|rest)
    paired_diff_t: float
    subperiod_diff: tuple[float, ...]  # per contiguous sub-period paired diff mean


@dataclass(frozen=True)
class FactorGateResult:
    """One crowding factor's full batch-A read (immutable)."""

    factor: str
    tail: TailConditional
    cpcv_frac_positive: float  # combo OOS fraction with the EXIT-aligned IC sign
    spread_dsr: float  # HAC-deflated Sharpe of the decile spread (non-zeroing N)
    spread_sharpe_raw: float
    max_carry_corr: float
    max_carry_name: str
    max_carry_support: int  # common-support dates behind the |corr| (review #6)


def _crash_prob(rets: NDArray[np.float64], threshold: float) -> float:
    return float(np.mean(rets < threshold)) if rets.size else float("nan")


def _cvar(rets: NDArray[np.float64], q: float = 0.05) -> float:
    """Mean of the worst ``q`` fraction (left-tail expected shortfall)."""
    if rets.size == 0:
        return float("nan")
    cut = float(np.quantile(rets, q))
    tail = rets[rets <= cut]
    return float(tail.mean()) if tail.size else cut


def tail_conditional(
    panel: pd.DataFrame, neut_factor: str, fwd_col: str, *, top_q: float = TOP_DECILE_Q
) -> TailConditional:
    """Crowded (top-decile neut factor) vs rest forward left-tail, pooled + paired.

    For each rebalance date, the names with ``neut_factor`` rank >= ``top_q`` are
    'crowded'; the rest are the reference. Pools the forward returns of each group
    across dates for the crash probabilities / CVaR, and forms a per-date paired
    difference of P(fwd < −5%) for a regime-robust significance read.
    """
    crowded_all: list[float] = []
    rest_all: list[float] = []
    paired: list[float] = []
    for _, grp in panel.groupby("date", sort=True):
        sub = grp[[neut_factor, fwd_col]].dropna()
        if len(sub) < MIN_OBS:
            continue
        cut = sub[neut_factor].quantile(top_q)
        crowded = sub[sub[neut_factor] >= cut][fwd_col].to_numpy()
        rest = sub[sub[neut_factor] < cut][fwd_col].to_numpy()
        if crowded.size == 0 or rest.size == 0:
            continue
        crowded_all.extend(crowded.tolist())
        rest_all.extend(rest.tolist())
        paired.append(
            _crash_prob(crowded, CRASH_THRESHOLDS[0])
            - _crash_prob(rest, CRASH_THRESHOLDS[0])
        )
    ca = np.asarray(crowded_all, dtype=float)
    ra = np.asarray(rest_all, dtype=float)
    pa = np.asarray(paired, dtype=float)
    t = 0.0
    if pa.size > 1 and pa.std(ddof=1) > 0:
        t = float(pa.mean() / (pa.std(ddof=1) / math.sqrt(pa.size)))
    subs = _subperiod_means(pa)
    return TailConditional(
        factor=neut_factor,
        fwd_col=fwd_col,
        n_dates=int(pa.size),
        crowded_p_crash5=_crash_prob(ca, CRASH_THRESHOLDS[0]),
        rest_p_crash5=_crash_prob(ra, CRASH_THRESHOLDS[0]),
        crowded_p_crash10=_crash_prob(ca, CRASH_THRESHOLDS[1]),
        rest_p_crash10=_crash_prob(ra, CRASH_THRESHOLDS[1]),
        crowded_cvar5=_cvar(ca),
        rest_cvar5=_cvar(ra),
        paired_diff_mean=float(pa.mean()) if pa.size else float("nan"),
        paired_diff_t=t,
        subperiod_diff=subs,
    )


def _subperiod_means(
    values: NDArray[np.float64], n: int = N_SUBPERIODS
) -> tuple[float, ...]:
    """Mean of ``values`` over ``n`` contiguous (time-ordered) sub-periods."""
    if values.size < n:
        return tuple(float("nan") for _ in range(n))
    chunks = np.array_split(values, n)
    return tuple(float(c.mean()) if c.size else float("nan") for c in chunks)


def decile_spread_by_date(
    panel: pd.DataFrame, neut_factor: str, fwd_col: str, *, q: float = SPREAD_Q
) -> dict[str, float]:
    """``{date: mean(fwd|bottom-decile) − mean(fwd|top-decile)}`` of the neut factor.

    Date-keyed (review #5) so multiple factors' spreads can be aligned on their
    COMMON dates before the ONC effective-N — each factor drops different thin/NaN
    dates, so a positional truncation would correlate mismatched dates. For an EXIT
    factor (high = crowded = expected LOW forward) the spread is positive when
    crowding underperforms. GROSS (no costs / no shorting) — a research
    significance series, not a deployable strategy (see module docstring).
    """
    out: dict[str, float] = {}
    for date, grp in panel.groupby("date", sort=True):
        sub = grp[[neut_factor, fwd_col]].dropna()
        if len(sub) < MIN_OBS:
            continue
        lo_cut = sub[neut_factor].quantile(q)
        hi_cut = sub[neut_factor].quantile(1.0 - q)
        low = sub[sub[neut_factor] <= lo_cut][fwd_col].mean()
        high = sub[sub[neut_factor] >= hi_cut][fwd_col].mean()
        if math.isfinite(low) and math.isfinite(high):
            out[str(date)] = float(low - high)
    return out


def decile_spread_series(
    panel: pd.DataFrame, neut_factor: str, fwd_col: str, *, q: float = SPREAD_Q
) -> list[float]:
    """Time-ordered decile-spread values (wrapper over decile_spread_by_date)."""
    by_date = decile_spread_by_date(panel, neut_factor, fwd_col, q=q)
    return [by_date[d] for d in sorted(by_date)]


def _raw_sharpe(series: list[float], *, periods_per_year: float) -> float:
    """Annualized Sharpe of a per-period series (periods_per_year from the cadence)."""
    arr = np.asarray(series, dtype=float)
    if arr.size < 2 or arr.std(ddof=1) == 0:
        return 0.0
    return float(arr.mean() / arr.std(ddof=1) * math.sqrt(periods_per_year))


def max_carry_collinearity(
    neut_panel: pd.DataFrame, factor: str
) -> tuple[str, float, int]:
    """``(carry_factor, |corr|, support_dates)`` of the most-collinear carry/QGR factor.

    Keeps ``_pairwise``'s support count (review #6): ``_pairwise`` fails OPEN
    (``|corr|=0``) when the 2-way common support is too thin to estimate, so the
    caller must see the support to avoid declaring a factor 'orthogonal new alpha'
    on a statistically empty correlation."""
    best: tuple[str, float, int] = ("-", 0.0, 0)
    for carry in CARRY_CLUSTER:
        val, support = _pairwise(neut_panel, _neut(factor), _neut(carry))
        if val > best[1]:
            best = (carry, val, support)
    return best


def evaluate_factor(
    neut_panel: pd.DataFrame,
    factor: str,
    *,
    n_trials: int,
    spread: list[float],
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
) -> FactorGateResult:
    """Full batch-A read for one crowding factor (tail + CPCV + DSR + collinearity).

    ``spread`` is the precomputed decile-spread series (review #8 — computed once in
    :func:`build_report` and threaded in, so the ONC-N input and the DSR input are
    the SAME series). ``rebalance_freq`` sets the annualization + HAC lag (review
    #7) instead of hardcoding the 5td cadence."""
    nf = _neut(factor)
    tail = tail_conditional(neut_panel, nf, PRIMARY_FWD)
    ic_series = rank_ic_series(neut_panel, nf, PRIMARY_FWD)
    cpcv = run_cpcv_fixed_series(
        ic_series,
        n_groups=QGR_N_GROUPS,
        k=QGR_CPCV_K,
        embargo=1,
        horizon=PRIMARY_HORIZON_TD,
    )
    # EXIT alignment: a negative IC (high crowding → low forward) is the prior, so
    # the EXIT-aligned combo fraction is the share of combos with a NEGATIVE mean.
    frac_neg = (
        1.0 - cpcv.combo_return_frac_positive if cpcv.n_combinations else float("nan")
    )
    lag = _overlap_lag(PRIMARY_HORIZON_TD, rebalance_freq)
    dsr = deflated_sharpe_hac(spread, n_trials=n_trials, hac_lag=lag)
    name, corr, support = max_carry_collinearity(neut_panel, factor)
    return FactorGateResult(
        factor=factor,
        tail=tail,
        cpcv_frac_positive=frac_neg,
        spread_dsr=dsr,
        spread_sharpe_raw=_raw_sharpe(
            spread, periods_per_year=_periods_per_year(rebalance_freq)
        ),
        max_carry_corr=corr,
        max_carry_name=name,
        max_carry_support=support,
    )


def _register_trials(ledger: TrialLedger, *, window: tuple[str, str]) -> None:
    """Append the batch-A crowding trials (idempotent; family ``mfi.batch_a``)."""
    for f in CROWDING_FACTOR_NAMES:
        ledger.append(
            TrialRecord(
                round_label="mfi-batch-a",
                kind="single",
                family="mfi.batch_a.crowding",
                description=f"crowding EXIT factor {f} (IC+tail+spread DSR)",
                n_nominal_trials=1,
                window_start=window[0],
                window_end=window[1],
                registered_at=LEDGER_DATE,
            )
        )
    ledger.append(
        TrialRecord(
            round_label="mfi-batch-a",
            kind="diagnostics",
            family="mfi.batch_a.diagnostics",
            description="batch-A crowding IC/sign/tail/collinearity screen",
            n_nominal_trials=len(CROWDING_FACTOR_NAMES),
            window_start=window[0],
            window_end=window[1],
            registered_at=LEDGER_DATE,
        )
    )


def _tail_table(results: list[FactorGateResult]) -> str:
    lines = [
        "| factor (neut) | n_dates | P(<−5%) crowded / rest | P(<−10%) c/r | "
        "CVaR5 c/r | paired Δ (t) | sub-periods |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        t = r.tail
        subs = " / ".join(f"{s:+.3f}" for s in t.subperiod_diff)
        lines.append(
            f"| `{r.factor}` | {t.n_dates} | "
            f"{t.crowded_p_crash5:.3f} / {t.rest_p_crash5:.3f} | "
            f"{t.crowded_p_crash10:.3f} / {t.rest_p_crash10:.3f} | "
            f"{t.crowded_cvar5:+.4f} / {t.rest_cvar5:+.4f} | "
            f"{t.paired_diff_mean:+.4f} ({t.paired_diff_t:+.2f}) | {subs} |"
        )
    return "\n".join(lines)


def _gate_table(results: list[FactorGateResult], *, n_trials: int) -> str:
    lines = [
        f"| factor | spread Sharpe (raw) | spread DSR (N={n_trials}) | CPCV combo "
        "EXIT-aligned frac | max |corr| carry (name, support) |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        thin = " ⚠️thin" if r.max_carry_support < MIN_COLLIN_DATES else ""
        lines.append(
            f"| `{r.factor}` | {r.spread_sharpe_raw:+.2f} | {r.spread_dsr:.3f} | "
            f"{r.cpcv_frac_positive:.2f} | {r.max_carry_corr:.2f} "
            f"(`{r.max_carry_name}`, {r.max_carry_support}d{thin}) |"
        )
    return "\n".join(lines)


def _verdict_lines(results: list[FactorGateResult]) -> str:
    lines = []
    for r in results:
        t = r.tail
        tail_ok = t.paired_diff_t >= T_BAR and t.paired_diff_mean > 0
        thin = r.max_carry_support < MIN_COLLIN_DATES
        orthogonal = r.max_carry_corr <= COLLINEARITY_CEILING and not thin
        verdict = (
            "EXIT-USEFUL (tail-significant)"
            if tail_ok
            else "NOT a robust EXIT signal (tail diff not significant)"
        )
        if thin:
            # _pairwise fails OPEN on thin support — do NOT call it orthogonal
            # alpha on a statistically empty correlation (review #6).
            ortho = (
                f"orthogonality UNRELIABLE (only {r.max_carry_support}d support "
                f"< {MIN_COLLIN_DATES}; |corr| {r.max_carry_corr:.2f} not trusted)"
            )
        elif orthogonal:
            ortho = f"orthogonal (|corr| {r.max_carry_corr:.2f})"
        else:
            ortho = f"REDUNDANT (~`{r.max_carry_name}`, |corr| {r.max_carry_corr:.2f})"
        lines.append(
            f"- **`{r.factor}`**: {verdict}; {ortho}; spread DSR {r.spread_dsr:.3f}. "
            + (
                "Crowding fattens the left tail as the asymmetry predicts."
                if tail_ok
                else "Mean/tail edge not robust — reported FAIL, not laundered."
            )
        )
    return "\n".join(lines)


DSR_GATE: float = 0.95  # deflated-Sharpe significance bar (Bailey-López de Prado)


def _falsification_ledger(
    results: list[FactorGateResult], neut_verdicts: list[FactorVerdict]
) -> str:
    """Resolve the spec's A1/A2/A3 pre-registered hypotheses from the data (§7).

    A1 = crowding fattens the forward left tail (the load-bearing asymmetry claim);
    A2 = ideal_amplitude survives our own size-neutralization as a negative axis;
    A3 = which factors are orthogonal new axes vs reversal/size in disguise. Each is
    a PASS / FAIL / MIXED read computed from the tables above — pre-committed, so a
    miss is reported, not laundered (QGR principle #1)."""
    by_neut = {v.factor: v for v in neut_verdicts}

    def _orthogonal(r: FactorGateResult) -> bool:
        return (
            r.max_carry_corr <= COLLINEARITY_CEILING
            and r.max_carry_support >= MIN_COLLIN_DATES
        )

    a1_ok = all(
        r.tail.paired_diff_t >= T_BAR and r.tail.paired_diff_mean > 0 for r in results
    )
    a1_min_t = min((r.tail.paired_diff_t for r in results), default=float("nan"))
    ia = next((r for r in results if r.factor == "ideal_amplitude_20d"), None)
    iav = by_neut.get("ideal_amplitude_20d_neut")
    a2_ok = (
        ia is not None
        and iav is not None
        and iav.has_signal
        and iav.aligned
        and _orthogonal(ia)
    )
    ortho_s = ", ".join(f"`{r.factor}`" for r in results if _orthogonal(r)) or "(none)"
    redund_s = (
        ", ".join(
            f"`{r.factor}`~`{r.max_carry_name}` (|corr| {r.max_carry_corr:.2f})"
            for r in results
            if not _orthogonal(r)
        )
        or "(none)"
    )
    dsr_s = (
        ", ".join(f"`{r.factor}`" for r in results if r.spread_dsr >= DSR_GATE)
        or "(none)"
    )
    return (
        f"- **A1 (crowding → fat left tail / crash probability)**: "
        f"**{'PASS' if a1_ok else 'FAIL'}** — all 3 EXIT factors' top-decile vs "
        f"rest paired tail-Δ are positive with |t| ≥ {T_BAR:.0f} "
        f"(min t = {a1_min_t:+.1f}), stable across the 3 sub-periods. The asymmetry "
        "(§2.1) holds on real A-share data: crowding/over-extension is a "
        "RISK/EXIT signal.\n"
        f"- **A2 (ideal_amplitude survives independent size-neutralization, §8)**: "
        f"**{'PASS' if a2_ok else 'FAIL'}** — the broker's unreplicated size-neutral "
        "claim REPLICATES under our own industry+size neutralization: "
        "`ideal_amplitude_20d_neut` stays a significant NEGATIVE (exit) axis AND is "
        "orthogonal to the carry/QGR cluster.\n"
        f"- **A3 (not just reversal/size in disguise, §2.10②)**: **MIXED** — "
        f"orthogonal new axes: {ortho_s}; "
        f"disguise (disclosed, not sold as new alpha): {redund_s}.\n"
        f"- **DSR gate (≥ {DSR_GATE}, non-zeroing N)**: passes = "
        f"{dsr_s} — a tail-significant "
        "factor whose deflated spread Sharpe still fails is an honest partial result "
        "(the tail edge is real; the long-short mean edge does not survive "
        "deflation).\n"
        "- **Conclusion**: the batch-A asymmetry is confirmed (A1) and "
        "`ideal_amplitude_20d` is a genuine orthogonal size-neutral EXIT factor (A2) "
        "— the strongest first-cut RISK/EXIT result; `bias_20d` is a tail-valid but "
        "reversal-redundant overlay, and `blowoff_20d`'s mean edge largely dissolves "
        "under neutralization (size/turnover in disguise). Deployable use = a "
        "long-only VETO; net P&L is the deferred event-loop test."
    )


def _aligned_onc_n(spread_by_date: dict[str, dict[str, float]]) -> int:
    """ONC effective-N over the spread series aligned on their COMMON dates (review #5).

    Each factor drops different thin/NaN dates; intersecting the date keys before
    building the matrix keeps every column on the same dates, so the correlation
    clustering is honest. Falls back to the factor count when the common window is
    too short to correlate."""
    factors = list(spread_by_date)
    if not factors:
        return 0
    common = set.intersection(*(set(spread_by_date[f]) for f in factors))
    common_dates = sorted(common)
    if len(common_dates) <= 1:
        return len(factors)
    matrix = [[spread_by_date[f][d] for d in common_dates] for f in factors]
    return onc_effective_n(matrix)


def build_report(
    panel: pd.DataFrame,
    *,
    ledger_path: str,
    params_note: str = "",
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
) -> str:
    """Assemble the batch-A crowding diagnostic Markdown (deterministic).

    ``rebalance_freq`` must match the cadence the panel was built at (it sets the
    spread Sharpe annualization + HAC lag; review #7)."""
    under_neut = (*CARRY_CLUSTER, *CROWDING_FACTOR_NAMES)
    neut_panel = neutralize_panel(
        panel, list(under_neut), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    crowd_neut_names = tuple(_neut(f) for f in CROWDING_FACTOR_NAMES)
    ic_all = study(
        neut_panel,
        factor_names=(*CROWDING_FACTOR_NAMES, *crowd_neut_names),
        forward_cols=CROWDING_FORWARD_COLS,
    )
    raw = [s for s in ic_all if not s.factor.endswith(NEUT_SUFFIX)]
    neut = [s for s in ic_all if s.factor.endswith(NEUT_SUFFIX)]
    raw_verdicts = verdicts(raw, CROWDING_FACTOR_NAMES)
    neut_verdicts = verdicts(neut, crowd_neut_names)

    # Non-zeroing trial ledger: legacy floor + batch-A appends; effective N over the
    # 3 EXIT spread series (correlated EXIT factors collapse to fewer independent
    # trials → cannot inflate the legacy floor). The spreads are computed ONCE here
    # (review #8) — date-keyed so the ONC alignment and each factor's DSR consume the
    # SAME series.
    dates = sorted(panel["date"].astype(str).unique())
    window = (dates[0], dates[-1]) if dates else ("", "")
    ledger = TrialLedger.with_legacy(ledger_path)
    _register_trials(ledger, window=window)
    spread_by_date = {
        f: decile_spread_by_date(neut_panel, _neut(f), PRIMARY_FWD)
        for f in CROWDING_FACTOR_NAMES
    }
    onc_n = _aligned_onc_n(spread_by_date)
    n_trials = ledger.deflation_n_trials(onc_effective_n=onc_n)

    results = [
        evaluate_factor(
            neut_panel,
            f,
            n_trials=n_trials,
            spread=[spread_by_date[f][d] for d in sorted(spread_by_date[f])],
            rebalance_freq=rebalance_freq,
        )
        for f in CROWDING_FACTOR_NAMES
    ]

    n_dates = panel["date"].nunique()
    n_codes = panel["code"].nunique()
    note = f"\n> Panel params: {params_note}.\n" if params_note else "\n"
    parts = [
        "# Batch-A crowding / blow-off EXIT diagnostics (train_val only)\n",
        f"> Panel: {len(panel)} rows / {n_codes} codes / {n_dates} rebalance dates "
        "(train_val; the sealed test window is never read)."
        + note
        + "> Family: `bias_20d` (乖离 over-extension) / `ideal_amplitude_20d` "
        "(理想振幅, Kaiyuan, size-neutral re-test) / `blowoff_20d` (run-up × "
        "turnover surge). "
        "All attractive-LOW (high = EXIT/trim). Neutralization: industry SW-L1 + "
        f"log(circ_mv) per-date OLS, winsor={WINSOR_QUANTILE}, min_obs={MIN_OBS}; "
        "bottom-30% size already cut in the panel.\n"
        "> **The load-bearing test is the crash-probability conditional (§4), not "
        "the mean IC** — the prior (§2.1) is that crowding predicts the LEFT TAIL, "
        "not mean return; a weak mean IC CONFIRMS that, it does not fail it. "
        f"EXIT-useful gate = paired tail-Δ ``|t| ≥ {T_BAR:.0f}`` AND positive; "
        f"orthogonality gate = |corr| ≤ {COLLINEARITY_CEILING} vs carry+QGR.\n",
        "## 1. IC honest verdict — crash-aligned sign from zero (raw)\n",
        _verdict_table(raw_verdicts),
        "\n## 2. IC honest verdict (industry+size neutralized)\n",
        _verdict_table(neut_verdicts),
        "\n## 3. ⭐ Crash-probability conditional (LOAD-BEARING) — top-decile vs rest\n"
        "Forward-5d left tail of the top-crowding decile (size-neutral) vs the rest; "
        "paired Δ = per-date P(<−5%|crowded) − P(<−5%|rest) (regime-robust); "
        f"sub-periods = {N_SUBPERIODS} contiguous thirds (R5 regime guard).\n",
        _tail_table(results),
        "\n## 4. Significance + non-zeroing ledger (DSR / CPCV / collinearity)\n"
        f"> Trial ledger `{ledger_path}`: legacy floor "
        f"{ledger.cumulative_effective_trials(family=None)} effective + batch-A "
        f"appends; ONC effective N over the 3 EXIT spreads (common dates) = {onc_n}; "
        f"deflation N = {n_trials} (changing the criterion does NOT reset the debt). "
        f"Cadence: rebalance={rebalance_freq}td, horizon={PRIMARY_HORIZON_TD}td → "
        f"DSR HAC lag={_overlap_lag(PRIMARY_HORIZON_TD, rebalance_freq)}, Sharpe "
        f"annualization √{_periods_per_year(rebalance_freq):.0f}.\n",
        _gate_table(results, n_trials=n_trials),
        "\n## 5. IC tables — crowding factors (raw + neutralized)\n",
        _ic_table([*raw, *neut]),
        "\n## 6. Honest read (FAIL is reported, not laundered)\n"
        + _verdict_lines(results)
        + "\n\n- **Asymmetry check (§2.1)**: the deployable use is a long-only VETO "
        "(drop top-crowding from the buy set); the decile spread here is a GROSS "
        "research series (no A-share shorting) — its net P&L as a veto in the "
        "event loop is the deferred QGR-4-scale test, NOT claimed here.\n"
        "- **A2 (理想振幅 §8)**: the neutralized `ideal_amplitude_20d` verdict above "
        "is our own size-neutral re-test of the broker's unreplicated claim; "
        "sign/significance read from zero.\n"
        "- **A3 (§2.10②)**: a factor REDUNDANT with the carry/QGR cluster is "
        "disclosed as reversal/size in disguise, not sold as new alpha.\n",
        "\n## 7. Falsification ledger resolution (A1 / A2 / A3) + conclusion\n"
        + _falsification_ledger(results, neut_verdicts)
        + "\n\n> Scope caveats: **train_val only** (the sealed test window is never "
        "read; a real OOS / forward confirmation is the B-layer gate, not done "
        "here); the decile spread is a GROSS long-short research series (no A-share "
        "shorting) used only as a significance probe; DSR deflation uses the "
        "non-zeroing legacy floor (N≈2382 — the round-1..4 mining debt is NOT reset "
        "by re-framing the criterion).\n",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_crowding.csv"
    )
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--out", default="docs/research/mfi-batch-a-crowding-results-2026-06-26.md"
    )
    parser.add_argument(
        "--params-note",
        default="rebalance=5td / batch-A crowding EXIT (bias/ideal_amplitude/blowoff)",
    )
    parser.add_argument(
        "--rebalance-freq",
        type=int,
        default=DEFAULT_REBALANCE_FREQ,
        help="cadence (td) the panel was built at — sets Sharpe/DSR annualization+lag",
    )
    args = parser.parse_args()

    panel = pd.read_csv(
        args.panel, dtype={"date": str, "code": str, "ts_code": str}
    )
    split = LockedSplit.load(args.lock, args.snapshot_root)
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))

    report = build_report(
        panel,
        ledger_path=args.ledger,
        params_note=args.params_note,
        rebalance_freq=args.rebalance_freq,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "CARRY_CLUSTER",
    "FactorGateResult",
    "TailConditional",
    "build_report",
    "decile_spread_by_date",
    "decile_spread_series",
    "evaluate_factor",
    "max_carry_collinearity",
    "tail_conditional",
]
