"""Benchmark-relative long-only construction + excess backtest (R2-3 / T2).

The deployable primary arm. R2-2's honest finding: the round-1 "defensive book
can't track a cap-weighted bull" failure has NO cross-sectional-factor fix — it
must come from the construction. This builds an enhanced-index portfolio that
starts at the CSI300 constituent weights and adds a BOUNDED active overlay from
the neutralized carry factors, so beta ≈ 1 and the active leg is net-zero by
construction.

Per rebalance date d:
  1. composite score = oriented blend of the carry factors' INDUSTRY+SIZE
     NEUTRALIZED columns (``*_neut``); a name missing any carry factor → NaN →
     no tilt (held at its benchmark weight, fail-closed).
  2. active a_i = clip(k · z(score_i), [-a_max, +a_max]) over the scored names.
  3. raw weight w_i = w_bench_i + a_i, long-only floored to >= 0, then
     RENORMALISED to sum 1. Because the benchmark weights sum to 1, a fully
     invested book (Σw = 1) makes the active leg net-zero (Σ(w - w_bench) = 0)
     automatically — no iterative projection needed.
  4. excess = Σ w_i·r_i − r_bench, net of a conservative BUY/SELL-SPLIT cost
     on the turned-over weight (buy ≈ 3bp, sell ≈ 13bp incl. stamp). The cost
     is charged on every backtest (never gross-gated).

A CSI300 constituent that is NOT in the investable universe (excluded by the
board/ST/liquidity/price/bottom-30%-size chain) is FORCED to weight 0 — a
forced underweight (active = −w_bench) that the index return leg penalises and
the disclosure reports separately. Exposure disclosure (net active ≈ 0,
gross/forced active, size-active, max industry-active, realised TE, IR) lets a
reader confirm the tilt is genuinely industry/size/beta-neutral rather than a
hidden bet.

This is DEVELOPMENT evidence, never a PASS/FAIL verdict (that is R2-6 forward).
Deterministic, train_val only, LLM-zero. ``k``/``a_max``/``weights`` are
parameters R2-4 will search; R2-3 measures realised TE/IR, it does not hard-solve
a TE target.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd

from .exposure_constraints import (
    DEFAULT_NONCONST_CAP,
    cap_nonconstituent_weights,
    filter_constituents,
    size_neutralize_active,
    validate_constraint,
)
from .factor_lib import ALL_FACTORS_BY_NAME
from .portfolio_backtest import group_by_date

# The R2-2 carry-forward set: round-1 seven + quality + growth. Composed from
# their industry/size-neutralized (``*_neut``) columns.
CARRY_FACTORS: tuple[str, ...] = (
    "ret_5d",
    "ret_20d",
    "vol_20d",
    "max_20d",
    "ep_ttm",
    "turn_20d",
    "amihud_20d",
    "roe",
    "gpm",
    "np_yoy",
    "rev_yoy",
)

# Conservative A-share one-way costs (≈ broker.yaml), buy/sell split:
# buy = commission 0.015% + ~1.5bp slippage; sell adds 0.1% stamp duty.
BUY_COST: float = 0.0003
SELL_COST: float = 0.0013
DEFAULT_K: float = 0.10
DEFAULT_A_MAX: float = 0.02
_PERIODS_PER_YEAR_BASE: int = 252

# Orientation override for the NEUTRALIZED composite where the residual sign
# differs from the raw-factor registry prior (codex P2). R2-2 found
# ``amihud_20d`` flips POSITIVE after size neutralization (raw amihud is largely
# a size proxy; the size-orthogonal residual is the classic illiquidity *premium*
# — illiquid → higher return). Using the raw attractive-low orientation here
# would bet AGAINST that residual. R2-4's search formally re-derives every
# factor's sign/weight; this is the mechanistically-correct default for the
# equal-weight demonstration composite.
NEUT_ORIENTATION_OVERRIDE: dict[str, bool] = {"amihud_20d": True}


def composite_score(group: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """Oriented blend of the carry factors' ``*_neut`` columns, indexed ts_code.

    ``weights`` keys are base factor names (e.g. ``roe``); each reads the
    ``<base>_neut`` column with the registry orientation (attractive-low →
    inverted rank), except where :data:`NEUT_ORIENTATION_OVERRIDE` corrects a
    residual whose sign flips under neutralization. A name missing ANY weighted
    factor gets a ``NaN`` composite (require the full vector — never a biased
    partial blend).
    """
    ts_index = pd.Index(group["ts_code"].astype(str), name="ts_code")
    score = pd.Series(0.0, index=ts_index)
    valid = pd.Series(True, index=ts_index)
    for base, w in weights.items():
        if w == 0:
            continue
        col = f"{base}_neut"
        if col not in group.columns:
            # A weighted factor whose neutralized column is entirely absent is a
            # malformed panel — fail closed loudly rather than silently rank on a
            # biased partial vector (codex P2; honors the full-vector contract).
            raise ValueError(
                f"composite weight references {col!r} but it is absent from the "
                "panel — run neutralize_panel(panel, CARRY_FACTORS) first"
            )
        vals = pd.Series(group[col].to_numpy(dtype=float), index=ts_index)
        ranks = vals.rank(pct=True)
        attractive_high = NEUT_ORIENTATION_OVERRIDE.get(
            base, ALL_FACTORS_BY_NAME[base].attractive_high
        )
        if not attractive_high:
            ranks = 1.0 - ranks
        score = score + float(w) * ranks
        valid = valid & vals.notna()
    score[~valid] = np.nan
    return score


def _sizes_from_group(group: pd.DataFrame) -> dict[str, float]:
    """``{ts_code: log_circ_mv}`` for the size-neutral exposure constraint."""
    if "log_circ_mv" not in group.columns or "ts_code" not in group.columns:
        return {}
    sizes: dict[str, float] = {}
    for code, lm in zip(
        group["ts_code"].astype(str), group["log_circ_mv"], strict=True
    ):
        lm_f = float(lm) if lm == lm else float("nan")  # NaN-safe
        if np.isfinite(lm_f):
            sizes[str(code)] = lm_f
    return sizes


def build_active_weights(
    group: pd.DataFrame,
    w_bench: Mapping[str, float],
    score: pd.Series,
    *,
    k: float,
    a_max: float,
    exposure_constraint: str = "unconstrained",
    nonconst_cap: float = DEFAULT_NONCONST_CAP,
) -> dict[str, float]:
    """Construct the long-only, net-zero-active, fully-invested weight book.

    Returns ``{ts_code: weight}`` over the investable names (summing to 1). A
    benchmark constituent absent from ``group`` is implicitly forced to 0 (the
    caller reports it as a forced underweight). ``{}`` if degenerate.

    The overlay is applied ONLY to scored names: their active is the box-
    constrained ``clip(k·z, ±a_max)``; the scored sleeve is then scaled so the
    whole book sums to 1 — which redistributes the forced-underweight cash
    through the scored sleeve while holding UNSCORED benchmark constituents at
    exactly their benchmark weight (codex P2: the earlier whole-book
    renormalize scaled unscored names off benchmark). Net active is 0 by
    construction (Σw = Σw_bench = 1). ``a_max`` bounds the pre-floor/scale
    active; the realized active (disclosed) may differ after scaling.

    ``exposure_constraint`` (R2-4, the R2-3 size-drift fix) bounds the
    off-benchmark tilt: ``constituent_only`` ranks only CSI300 constituents (a
    score pre-filter → z-score within the index); ``size_neutral`` removes the
    active vector's size projection; ``capped_nonconstituent`` caps the
    non-constituent gross active at ``nonconst_cap``. ``unconstrained`` (default)
    is byte-identical to the R2-3 construction.
    """
    validate_constraint(exposure_constraint)
    if exposure_constraint == "constituent_only":
        score = filter_constituents(score, w_bench)
    codes = [str(c) for c in score.index]
    finite = score.dropna()
    scored = {str(c) for c in finite.index}
    if len(finite) >= 2 and float(finite.std(ddof=0)) > 0:
        z = (finite - float(finite.mean())) / float(finite.std(ddof=0))
        # Box-constrained active in [-a_max, +a_max]; net-zero is enforced by the
        # final Σw=1 renormalization below (NOT by demeaning, which would push
        # names outside the a_max box — codex P2).
        active = (k * z).clip(lower=-a_max, upper=a_max)
        active_map = {str(c): float(a) for c, a in active.items()}
    else:
        active_map = {}

    # size_neutral acts on the active overlay BEFORE the long-only floor, then
    # SCALES (not clips) it into the box: the size projection can push a name
    # outside [-a_max, a_max] (codex P2), so shrink the whole vector uniformly to
    # honour the advertised per-name bound while PRESERVING size-orthogonality (a
    # clip would re-introduce a size tilt; uniform scaling keeps Σ active·z=0).
    # The long-only floor may still leave a small residual (disclosed by the
    # size-active column). capped_nonconstituent is applied to the FINAL book
    # below (a realised cap, not a pre-floor one the renormalize re-inflates).
    if active_map and exposure_constraint == "size_neutral":
        active_map = size_neutralize_active(active_map, _sizes_from_group(group))
        peak = max((abs(a) for a in active_map.values()), default=0.0)
        if peak > a_max:
            shrink = a_max / peak
            active_map = {c: a * shrink for c, a in active_map.items()}

    w_scored: dict[str, float] = {}
    w_unscored: dict[str, float] = {}
    for code in codes:
        wb = float(w_bench.get(code, 0.0))
        if code in scored and code in active_map:
            w_scored[code] = max(wb + active_map[code], 0.0)  # long-only floor
        else:
            w_unscored[code] = wb  # held at benchmark (fail-closed, no tilt)

    sum_unscored = sum(w_unscored.values())
    sum_scored = sum(w_scored.values())
    if not w_scored:
        # No scored sleeve → all benchmark; renormalise unscored to full weight.
        return (
            {c: w / sum_unscored for c, w in w_unscored.items()}
            if sum_unscored > 0
            else {}
        )
    gap = 1.0 - sum_unscored  # weight the scored sleeve must fill (incl forced-UW)
    if gap <= 0 or sum_scored <= 0:
        # No room for the scored sleeve (the benchmark sleeve already fills the
        # book) or the scored sleeve floored to zero → hold EVERY investable
        # benchmark constituent (scored or not) at its relative PIT weight and
        # add NO off-benchmark active. Fail-closed: missing/degenerate coverage
        # must not create active bets, and must not drop a scored constituent to
        # 0 (that would itself be a forced underweight — review finding).
        bench_sleeve = {c: float(w_bench.get(c, 0.0)) for c in codes}
        total_b = sum(v for v in bench_sleeve.values() if v > 0)
        return (
            {c: w / total_b for c, w in bench_sleeve.items() if w > 0}
            if total_b > 0
            else {}
        )
    scale = gap / sum_scored
    book = {**{c: w * scale for c, w in w_scored.items()}, **w_unscored}
    if exposure_constraint == "capped_nonconstituent":
        # Bound the REALISED non-constituent active (the renormalize above can
        # re-inflate a pre-floor cap — codex P2). Freed weight is redistributed
        # ONLY into scored constituents, so UNSCORED benchmark constituents stay
        # at their exact benchmark weight (codex P2). The earlier all-benchmark
        # return paths hold no non-constituent weight, so this is the only path
        # that can breach the cap.
        book = cap_nonconstituent_weights(
            book, w_bench, nonconst_cap, redistribute_into=frozenset(scored)
        )
    return book


def drift_weights(
    w: Mapping[str, float], rets: Mapping[str, float], port_ret: float
) -> dict[str, float]:
    """End-of-period holdings after a book ``w`` drifts by realised returns.

    ``w_i·(1+r_i) / (1+port_ret)`` (normalised so the drifted book sums to 1).
    Used so the next rebalance's turnover trades back from the drifted holdings,
    not the old target — otherwise an unchanged target reports zero cost even
    though the holdings have moved (codex P2). Falls back to the target on a
    degenerate (≤-100%) period.
    """
    gross = 1.0 + port_ret
    if gross <= 0:
        return dict(w)
    return {c: w[c] * (1.0 + rets.get(c, 0.0)) / gross for c in w}


def weight_turnover(
    prev_w: Mapping[str, float], new_w: Mapping[str, float]
) -> tuple[float, float]:
    """(buy, sell) fractions between two weight books.

    Computed on the WEIGHT vectors (not set membership) so a name retained at a
    different weight is correctly charged its resize — e.g. two names going from
    50% each to 25% each is a 50% sell even though neither name leaves the book.
    """
    names = set(prev_w) | set(new_w)
    buy = sum(max(new_w.get(c, 0.0) - prev_w.get(c, 0.0), 0.0) for c in names)
    sell = sum(max(prev_w.get(c, 0.0) - new_w.get(c, 0.0), 0.0) for c in names)
    return buy, sell


def _size_active(group: pd.DataFrame, active: Mapping[str, float]) -> float:
    """Active exposure to size = Σ active_i · z(log_circ_mv) over INVESTABLES only.

    Forced-underweight constituents (excluded from ``group``) have no size in the
    panel, so their negative active is NOT captured here (codex P2 — disclosed as
    a limitation; the separate forced-underweight column bounds that residual).
    """
    if "log_circ_mv" not in group.columns:
        return 0.0
    sub = group[["ts_code", "log_circ_mv"]].dropna()
    sizes = pd.to_numeric(sub["log_circ_mv"], errors="coerce").to_numpy(dtype=float)
    if len(sizes) < 2 or float(sizes.std()) == 0:
        return 0.0
    z = (sizes - sizes.mean()) / sizes.std()
    codes = sub["ts_code"].astype(str).tolist()
    return float(sum(active.get(c, 0.0) * zi for c, zi in zip(codes, z, strict=True)))


def _max_industry_active(group: pd.DataFrame, active: Mapping[str, float]) -> float:
    """Largest abs net active to any SW L1 industry, over INVESTABLES only.

    Forced-underweight constituents (absent from ``group``) have no industry in
    the panel, so their negative active is not attributed to an industry here
    (codex P2 — disclosed limitation; forced-underweight reported separately).
    """
    if "industry_l1" not in group.columns:
        return 0.0
    by_ind: dict[str, float] = {}
    for code, ind in zip(
        group["ts_code"].astype(str), group["industry_l1"].astype(str), strict=True
    ):
        if not ind or ind.lower() in {"nan", "<na>", "none"}:
            continue
        by_ind[ind] = by_ind.get(ind, 0.0) + active.get(code, 0.0)
    return max((abs(v) for v in by_ind.values()), default=0.0)


@dataclass(frozen=True)
class BenchmarkRelativeResult:
    """Benchmark-relative excess outcome + exposure disclosure (immutable)."""

    n_periods: int
    total_excess: float
    annual_excess: float
    tracking_error: float  # annualised std of per-period excess
    information_ratio: float  # mean per-period excess / std × √ppy (standard
    # arithmetic IR; differs from annual_excess/TE since annual_excess is geometric)
    avg_turnover: float  # one-way (buy) fraction per rebalance
    avg_gross_active: float  # Σ|active|/2 = active share
    avg_forced_underweight: float  # Σ w_bench of excluded constituents
    mean_net_active: float  # should be ~0 (beta ~ 1)
    mean_size_active: float
    mean_max_industry_active: float
    excess_returns: tuple[float, ...]
    dates: tuple[str, ...]


def benchmark_relative_backtest(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    horizon: int = 5,
    k: float = DEFAULT_K,
    a_max: float = DEFAULT_A_MAX,
    buy_cost: float = BUY_COST,
    sell_cost: float = SELL_COST,
    exposure_constraint: str = "unconstrained",
    nonconst_cap: float = DEFAULT_NONCONST_CAP,
) -> BenchmarkRelativeResult:
    """Run the benchmark-relative tilt over the panel; return excess + exposures.

    ``bench_asof(d)`` returns the PIT benchmark weights known as of date ``d``
    (``{}`` → skip, e.g. pre-2016). ``index_returns[d]`` is the CSI300 return
    over the same ``horizon`` bars (precomputed by the caller). A date with no
    benchmark weights or no index return is skipped. ``exposure_constraint`` /
    ``nonconst_cap`` (R2-4) bound the off-benchmark tilt (see
    :func:`build_active_weights`); the default reproduces the R2-3 construction.
    """
    validate_constraint(exposure_constraint)
    fwd_col = f"fwd_ret_{horizon}d"
    groups = group_by_date(panel)
    prev_w: dict[str, float] = {}
    periods: list[_Period] = []

    for d in sorted(groups):
        period, prev_w = _run_period(
            d,
            groups[d],
            bench_asof(d),
            index_returns.get(d),
            weights=weights,
            prev_w=prev_w,
            fwd_col=fwd_col,
            k=k,
            a_max=a_max,
            buy_cost=buy_cost,
            sell_cost=sell_cost,
            exposure_constraint=exposure_constraint,
            nonconst_cap=nonconst_cap,
        )
        if period is not None:
            periods.append(period)

    return _summarize_relative(
        [p.excess for p in periods],
        [p.turnover for p in periods],
        [p.gross_active for p in periods],
        [p.forced for p in periods],
        [p.net_active for p in periods],
        [p.size_active for p in periods],
        [p.ind_active for p in periods],
        [p.date for p in periods],
        horizon,
    )


class _Period(NamedTuple):
    """One rebalance period's excess + exposure stats."""

    date: str
    excess: float
    turnover: float
    gross_active: float
    forced: float
    net_active: float
    size_active: float
    ind_active: float


def _run_period(
    d: str,
    group: pd.DataFrame,
    w_bench: dict[str, float],
    bench_ret: float | None,
    *,
    weights: Mapping[str, float],
    prev_w: dict[str, float],
    fwd_col: str,
    k: float,
    a_max: float,
    buy_cost: float,
    sell_cost: float,
    exposure_constraint: str = "unconstrained",
    nonconst_cap: float = DEFAULT_NONCONST_CAP,
) -> tuple[_Period | None, dict[str, float]]:
    """Build + evaluate one rebalance date; return its stats + drifted holdings.

    ``(None, prev_w)`` (prev_w unchanged) when the date is skipped (no benchmark
    weights / no index return / empty cross-section / degenerate book).
    """
    if not w_bench or bench_ret is None:
        return None, prev_w
    g = group.dropna(subset=[fwd_col])
    if g.empty:
        return None, prev_w
    score = composite_score(g, weights)
    w = build_active_weights(
        g,
        w_bench,
        score,
        k=k,
        a_max=a_max,
        exposure_constraint=exposure_constraint,
        nonconst_cap=nonconst_cap,
    )
    if not w:
        return None, prev_w

    fwd = {
        str(c): float(v)
        for c, v in zip(g["ts_code"].astype(str), g[fwd_col], strict=True)
    }
    port_ret = sum(w[c] * fwd[c] for c in w if c in fwd)
    buy, sell = weight_turnover(prev_w, w)
    cost = buy * buy_cost + sell * sell_cost

    union = set(w) | set(w_bench)
    active = {c: w.get(c, 0.0) - w_bench.get(c, 0.0) for c in union}
    period = _Period(
        date=d,
        excess=port_ret - bench_ret - cost,
        turnover=buy,
        gross_active=sum(abs(a) for a in active.values()) / 2.0,
        forced=sum(w_bench[c] for c in set(w_bench) - set(w)),
        net_active=sum(active.values()),
        size_active=_size_active(g, active),
        ind_active=_max_industry_active(g, active),
    )
    # Next turnover trades from the DRIFTED end-of-period holdings, not the old
    # target (codex P2: target-to-target understates cost when holdings drift).
    return period, drift_weights(w, fwd, port_ret)


def _mean(values: list[float]) -> float:
    return float(np.mean(values)) if values else 0.0


def _summarize_relative(
    excess: list[float],
    turnovers: list[float],
    gross_actives: list[float],
    forced: list[float],
    net_actives: list[float],
    size_actives: list[float],
    ind_actives: list[float],
    dates: list[str],
    horizon: int,
) -> BenchmarkRelativeResult:
    n = len(excess)
    if n == 0:
        return BenchmarkRelativeResult(
            0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (), ()
        )
    arr = np.array(excess, dtype=float)
    total = float(np.cumprod(1.0 + arr)[-1] - 1.0)
    ppy = _PERIODS_PER_YEAR_BASE / horizon
    annual = float((1.0 + total) ** (ppy / n) - 1.0) if total > -1.0 else -1.0
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    te = std * float(np.sqrt(ppy))
    ir = float(arr.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    return BenchmarkRelativeResult(
        n_periods=n,
        total_excess=total,
        annual_excess=annual,
        tracking_error=te,
        information_ratio=ir,
        avg_turnover=_mean(turnovers),
        avg_gross_active=_mean(gross_actives),
        avg_forced_underweight=_mean(forced),
        mean_net_active=_mean(net_actives),
        mean_size_active=_mean(size_actives),
        mean_max_industry_active=_mean(ind_actives),
        excess_returns=tuple(float(x) for x in arr),
        dates=tuple(dates),
    )


__all__ = [
    "BUY_COST",
    "CARRY_FACTORS",
    "DEFAULT_A_MAX",
    "DEFAULT_K",
    "SELL_COST",
    "BenchmarkRelativeResult",
    "benchmark_relative_backtest",
    "build_active_weights",
    "composite_score",
    "drift_weights",
    "weight_turnover",
]
