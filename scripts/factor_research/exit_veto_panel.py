"""Pure panel transforms for the QGR-4 EXIT-veto event-loop ablation.

The deterministic, offline core that turns the neutralised batch-A panel into the
three arena arms (main-force-intent §6.6 / lowbase-transition-system-design §6.6):

* **ranker** — the QGR-3 fast-leg survivors ``{rev_1d, max_5d, turn_spike}``
  (all ``attractive_high=False`` / IC −1: low value = attractive) combined into a
  buy score = equal-weight mean of the **negated** per-date z-scores of the
  industry+size-neutral factors (higher score = more attractive to BUY).
* **EXIT-veto** — drop the top crowding decile (``ideal_amplitude_20d_neut``, the
  batch-A orthogonal winner) from the BUY candidate set: a long-only veto.
* **placebo** — drop the SAME per-date count, either uniformly random or
  size-matched, so a net-P&L / drawdown change from the veto can be told apart
  from a mere "buy fewer names → less exposure" artifact (codex Q-A/Q-D, §6.6).

The arena's :class:`backend.backtest.strategy.CodeHealth` overrides are derived
here from the panel so the **real** sticky ≤1-rotation/day weakness gate
(``config/slot_rotation_policy.yaml``) can fire — without panel-driven health the
gate never rotates and every arm degenerates to buy-and-hold the day-1 top-5
(``gate_backtest`` docstring). ``entry_percentile`` / ``score_median_20d`` /
``score_mad_20d`` are **stateless trailing proxies** over prior rebalance dates
(the event loop does not feed entry state back to the provider — a documented
proxy boundary, like the §4.4 exposure-cap proxies).

Pure functions of injected look-ahead-free inputs: no IO, no wall-clock; the only
pseudo-randomness is the explicitly-seeded placebo draw (deterministic for a
fixed seed). Never imports the live path.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.backtest.strategy import CodeHealth

from .factor_lib import QGR_FACTORS_BY_NAME

# The QGR-3 fast-leg survivors. Their attractive direction is read from the
# factor_lib registry (the single source of truth), NOT hard-coded: each is
# attractive_high=False (reversal / lottery / turnover spike, low value =
# attractive), so the composite flips its sign (``_factor_sign`` below). Reading
# the registry means a future survivor with a different orientation cannot
# silently invert the buy score.
RANKER_FACTORS: tuple[str, ...] = ("rev_1d", "max_5d", "turn_spike")
# The batch-A orthogonal size-neutral EXIT winner (A2 PASS) — the veto axis.
CROWD_FACTOR: str = "ideal_amplitude_20d"
NEUT_SUFFIX: str = "_neut"

TOP_CROWD_Q: float = 0.90  # crowded = top 10% (matches the batch-A §3 conditional)
# Trailing-proxy windows in REBALANCE steps (5td each ⇒ 4 ≈ 20td, the panel's
# rolling-stat horizon and ~the rotation min-hold band). Pre-committed.
ENTRY_LOOKBACK: int = 4
SCORE_STAT_LOOKBACK: int = 4


def _neut(factor: str) -> str:
    return f"{factor}{NEUT_SUFFIX}"


def _factor_sign(factor: str) -> float:
    """+1 if a HIGH factor value is attractive to buy, −1 if a LOW value is.

    Read from the factor_lib registry (``attractive_high``) so the buy-score
    orientation has a single source of truth — never a hard-coded negation.
    """
    return 1.0 if QGR_FACTORS_BY_NAME[factor].attractive_high else -1.0


def _zscore(values: pd.Series) -> pd.Series:
    """Cross-sectional z-score; a degenerate (zero-variance) slice → all zeros."""
    std = values.std(ddof=0)
    if not np.isfinite(std) or std == 0.0:
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def build_ranker_table(neut_panel: pd.DataFrame) -> pd.DataFrame:
    """``(date, ts_code, ranker_score, ranker_pct, crowd_pct, log_circ_mv)``.

    A row survives only when all three survivor neut factors AND the crowding neut
    factor are present (a consistent candidate set for ranking + veto). ``ranker_score``
    = mean of each factor's registry-signed per-date z-score (higher = more attractive);
    ``ranker_pct`` / ``crowd_pct`` are within-date ranks in [0, 1] (higher = stronger /
    more crowded).
    """
    need = [_neut(f) for f in RANKER_FACTORS] + [_neut(CROWD_FACTOR)]
    missing = [
        c for c in [*need, "date", "ts_code", "log_circ_mv"] if c not in neut_panel
    ]
    if missing:
        raise KeyError(f"neut_panel missing columns: {missing}")
    sub = neut_panel.dropna(subset=need).copy()
    rows: list[pd.DataFrame] = []
    for _date, grp in sub.groupby("date", sort=True):
        g = grp.copy()
        # buy score = mean of registry-signed z-scores (attractive-LOW → sign −1).
        z = pd.concat(
            [_factor_sign(f) * _zscore(g[_neut(f)]) for f in RANKER_FACTORS], axis=1
        ).mean(axis=1)
        g["ranker_score"] = z.to_numpy()
        g["ranker_pct"] = g["ranker_score"].rank(pct=True, method="average")
        g["crowd_pct"] = g[_neut(CROWD_FACTOR)].rank(pct=True, method="average")
        rows.append(
            g[
                [
                    "date",
                    "ts_code",
                    "ranker_score",
                    "ranker_pct",
                    "crowd_pct",
                    "log_circ_mv",
                ]
            ]
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "date",
                "ts_code",
                "ranker_score",
                "ranker_pct",
                "crowd_pct",
                "log_circ_mv",
            ]
        )
    return pd.concat(rows, ignore_index=True)


ScoredDay = list[tuple[str, float]]


def scores_by_day(
    ranker_table: pd.DataFrame, *, drop_codes: Mapping[str, set[str]] | None = None
) -> dict[str, ScoredDay]:
    """``{date: [(ts_code, ranker_score), ...]}`` sorted by score desc (ties by code).

    ``drop_codes[date]`` removes those codes from that date's candidate set (the
    veto / placebo buy-set removal). The selector then takes the top-N of what's left.
    """
    drop = drop_codes or {}
    out: dict[str, ScoredDay] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        removed = drop.get(str(date), set())
        scored = [
            (str(code), float(score))
            for code, score in zip(grp["ts_code"], grp["ranker_score"], strict=True)
            if str(code) not in removed
        ]
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        out[str(date)] = scored
    return out


def drop_from_scores(
    base: Mapping[str, ScoredDay], drop_codes: Mapping[str, set[str]]
) -> dict[str, ScoredDay]:
    """Filter ``drop_codes[date]`` out of an already-sorted ``base`` scores table.

    Removal preserves the base sort order, so the veto / placebo arms are derived from
    the single base groupby+sort instead of re-grouping the panel once per arm.
    """
    out: dict[str, ScoredDay] = {}
    for date, scored in base.items():
        removed = drop_codes.get(date, set())
        if removed:
            out[date] = [item for item in scored if item[0] not in removed]
        else:
            out[date] = list(scored)
    return out


def veto_codes_by_day(
    ranker_table: pd.DataFrame, *, top_q: float = TOP_CROWD_Q
) -> dict[str, set[str]]:
    """``{date: {crowded ts_codes}}`` — names with ``crowd_pct >= top_q`` (top 10%)."""
    out: dict[str, set[str]] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        crowded = grp.loc[grp["crowd_pct"] >= top_q, "ts_code"].astype(str)
        out[str(date)] = set(crowded.tolist())
    return out


def placebo_codes_by_day(
    ranker_table: pd.DataFrame,
    veto_codes: Mapping[str, set[str]],
    *,
    seed: int,
    size_matched: bool,
) -> dict[str, set[str]]:
    """Per-date placebo removal of the SAME count as the veto (§6.6 control).

    ``size_matched=False`` draws uniformly at random (seeded) from the non-vetoed
    pool — controls "buy fewer names → less exposure". ``size_matched=True`` greedily
    pairs each vetoed name to the nearest-``log_circ_mv`` non-vetoed name — additionally
    controls the size channel (the project's recurring size-tilt trap, R5).
    Deterministic for a fixed seed.
    """
    out: dict[str, set[str]] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        d = str(date)
        vetoed = veto_codes.get(d, set())
        k = len(vetoed)
        pool = grp[~grp["ts_code"].astype(str).isin(vetoed)]
        if k == 0 or pool.empty:
            out[d] = set()
            continue
        if size_matched:
            out[d] = _size_matched_draw(grp, vetoed, pool, k)
        else:
            # Per-date seed mixes the global seed with the date so different dates
            # draw independently (no shared permutation artifact).
            rng = np.random.default_rng(seed + int(d))
            codes = sorted(pool["ts_code"].astype(str).tolist())
            idx = rng.choice(len(codes), size=min(k, len(codes)), replace=False)
            out[d] = {codes[i] for i in idx}
    return out


def _size_matched_draw(
    grp: pd.DataFrame, vetoed: set[str], pool: pd.DataFrame, k: int
) -> set[str]:
    """Greedy nearest-``log_circ_mv`` match: one non-vetoed name per vetoed name.

    ``cand`` is sorted by size once; each vetoed size bisects to its insertion point
    and expands outward to the nearest UNUSED neighbour (skipping consumed indices),
    so the per-name lookup is a local probe rather than a full O(n) rescan.
    """
    vetoed_sizes = sorted(
        grp.loc[grp["ts_code"].astype(str).isin(vetoed), "log_circ_mv"]
        .dropna()
        .tolist()
    )
    cand = sorted(
        (float(size), str(code))
        for code, size in zip(pool["ts_code"], pool["log_circ_mv"], strict=True)
        if np.isfinite(size)
    )
    sizes = [c[0] for c in cand]
    used = [False] * len(cand)
    chosen: set[str] = set()
    for vs in vetoed_sizes:
        if len(chosen) >= k:
            break
        pick = _nearest_unused(sizes, used, vs)
        if pick is not None:
            used[pick] = True
            chosen.add(cand[pick][1])
    return chosen


def _nearest_unused(sizes: list[float], used: list[bool], target: float) -> int | None:
    """Index of the nearest not-yet-used entry in the sorted ``sizes`` to ``target``."""
    pos = bisect.bisect_left(sizes, target)
    lo, hi = pos - 1, pos
    while lo >= 0 and used[lo]:
        lo -= 1
    while hi < len(sizes) and used[hi]:
        hi += 1
    left = (target - sizes[lo]) if lo >= 0 else None
    right = (sizes[hi] - target) if hi < len(sizes) else None
    if left is None and right is None:
        return None
    if right is None or (left is not None and left <= right):
        return lo
    return hi


@dataclass(frozen=True)
class _CodeStat:
    """One code's trailing rank/score history across rebalance dates."""

    pcts: list[float]
    scores: list[float]


def build_health_overrides(
    ranker_table: pd.DataFrame,
    *,
    entry_lookback: int = ENTRY_LOOKBACK,
    score_stat_lookback: int = SCORE_STAT_LOOKBACK,
) -> dict[str, dict[str, CodeHealth]]:
    """``{date: {ts_code: CodeHealth}}`` driving the real weakness+margin gate.

    ``line1_percentile`` / ``composite_score`` are today's; ``entry_percentile`` is a
    trailing-max of the code's own percentile over the prior ``entry_lookback``
    rebalance dates (a stateless proxy for "entered when strong, since deteriorated");
    ``score_median_20d`` / ``score_mad_20d`` are trailing median / MAD over the prior
    ``score_stat_lookback`` dates (the cond-6a confirmation).
    ``drawdown_from_local_high`` is left 0 (no price-path in the static panel; cond-6a
    carries the confirmation) — a documented proxy boundary.
    """
    hist: dict[str, _CodeStat] = {}
    out: dict[str, dict[str, CodeHealth]] = {}
    # One groupby (date-ordered) instead of an O(D·N) per-date boolean re-scan.
    for date, grp in ranker_table.groupby("date", sort=True):
        day: dict[str, CodeHealth] = {}
        for code_raw, pct_raw, score_raw in zip(
            grp["ts_code"], grp["ranker_pct"], grp["ranker_score"], strict=True
        ):
            code = str(code_raw)
            pct, score = float(pct_raw), float(score_raw)
            stat = hist.get(code, _CodeStat(pcts=[], scores=[]))
            entry_pct = max(stat.pcts[-entry_lookback:], default=pct)
            window = stat.scores[-score_stat_lookback:]
            if window:
                median = float(np.median(window))
                mad = float(np.median(np.abs(np.asarray(window) - median)))
            else:
                median, mad = score, 0.0
            day[code] = CodeHealth(
                line1_percentile=pct,
                composite_score=score,
                qualified=True,
                entry_percentile=float(entry_pct),
                score_median_20d=median,
                score_mad_20d=mad,
            )
            stat.pcts.append(pct)
            stat.scores.append(score)
            hist[code] = stat
        out[str(date)] = day
    return out


def universe_by_day(ranker_table: pd.DataFrame) -> dict[str, list[str]]:
    """``{date: [ts_codes]}`` — the per-date candidate universe (for baselines)."""
    out: dict[str, list[str]] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        out[str(date)] = sorted(grp["ts_code"].astype(str).tolist())
    return out


def panel_universe(ranker_table: pd.DataFrame) -> tuple[str, ...]:
    """All ts_codes the gate may ever hold/buy (bounds the bar source memory)."""
    return tuple(sorted(ranker_table["ts_code"].astype(str).unique()))


def removed_counts(veto_codes: Mapping[str, set[str]]) -> dict[str, int]:
    """``{date: n_vetoed}`` — the per-date veto pass-rate the placebo must match."""
    return {d: len(s) for d, s in veto_codes.items()}


__all__ = [
    "CROWD_FACTOR",
    "ENTRY_LOOKBACK",
    "RANKER_FACTORS",
    "SCORE_STAT_LOOKBACK",
    "TOP_CROWD_Q",
    "ScoredDay",
    "build_health_overrides",
    "build_ranker_table",
    "drop_from_scores",
    "panel_universe",
    "placebo_codes_by_day",
    "removed_counts",
    "scores_by_day",
    "universe_by_day",
    "veto_codes_by_day",
]
