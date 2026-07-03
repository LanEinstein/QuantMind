"""Block-weighted defensive ranker + committed exclusion gates for candidate D1.

Turns the neutralised D1 panel (``defensive_d1_panel``) into the per-date defensive
score the ≤5-slot arena ranks on. Two committed steps, both frozen in
``defensive_d1_spec`` (hashed before evaluation — never fit in-sample):

1. **Exclusion gates** (``UNIVERSE_FILTERS``), applied per date on the RAW factor
   values BEFORE ranking: drop the top-decile lottery (``max_20d`` ≥ 0.90 quantile),
   the value-trap quality floor (``roe`` ≤ 0 or ``gpm`` ≤ its 0.10 quantile), and
   the anti-crowding valuation anchor (``dv_ratio`` < its median). A name missing a
   gate value is not dropped by that gate (it still must pass the neut dropna below).

2. **Block-weighted z-blend** on the surviving set: for each committed block, the
   mean of its factors' ``factor_sign · zscore(<factor>_neut)``, weighted by the
   committed ``block_weight`` (low_vol 0.35 / dividend 0.35 / quality_safety 0.20 /
   tail 0.10). Higher score = more defensive-attractive to BUY. The industry+size
   residualisation happened upstream (``neutralize_panel``); the z-normalisation
   here is over the SURVIVING (post-exclusion, all-7-neut-present) cross-section, so
   a name enters the blend only with a complete, comparable defensive signal.

Pure functions of the injected neutralised panel — no IO, no wall-clock, no
``backend`` import beyond the reused ``exit_veto_panel`` helpers. Never the live path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .defensive_d1_spec import (
    BLOCK_NAMES,
    RANKER_FACTORS,
    UNIVERSE_FILTERS,
    block_weight,
    factor_sign,
    factors_in_block,
)

# Reuse the panel→provider plumbing (identical contract: a table with
# date / ts_code / ranker_score / ranker_pct / log_circ_mv columns).
from .exit_veto_panel import (
    build_health_overrides,
    panel_universe,
    scores_by_day,
    universe_by_day,
)

NEUT_SUFFIX: str = "_neut"

# The RAW columns the committed exclusion gates read (candidate doc §2).
_LOTTERY_COL: str = "max_20d"
_ROE_COL: str = "roe"
_GPM_COL: str = "gpm"
_DIV_COL: str = "dv_ratio"

RANKER_TABLE_COLUMNS: tuple[str, ...] = (
    "date",
    "ts_code",
    "ranker_score",
    "ranker_pct",
    "log_circ_mv",
)


def _neut(factor: str) -> str:
    return f"{factor}{NEUT_SUFFIX}"


def _zscore(values: pd.Series) -> pd.Series:
    """Cross-sectional z-score; a degenerate (zero-variance) slice → all zeros."""
    std = values.std(ddof=0)
    if not np.isfinite(std) or std == 0.0:
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def apply_exclusion_gates(group: pd.DataFrame) -> pd.DataFrame:
    """Drop the committed exclusion names within one date's cross-section (RAW values).

    ``max_20d`` top decile (lottery), ``roe`` ≤ 0 or ``gpm`` bottom decile (value-trap
    quality floor), and ``dv_ratio`` below its median (anti-crowding valuation
    anchor). Thresholds are per-date quantiles of the present (non-NaN) values; a
    name missing a gate value is not excluded by that gate (``NaN`` comparisons are
    ``False``) — it must still carry all seven neutralised factors to be ranked.
    """
    keep = pd.Series(True, index=group.index)
    lottery = group[_LOTTERY_COL]
    if lottery.notna().any():
        thr = float(lottery.quantile(UNIVERSE_FILTERS.max_lottery_exclude_quantile))
        keep &= ~(lottery >= thr)
    # ROE floor: drop ROE ≤ 0 (NaN ≤ 0 is False → kept, dropna handles it later).
    keep &= ~(group[_ROE_COL] <= UNIVERSE_FILTERS.roe_floor)
    gpm = group[_GPM_COL]
    if gpm.notna().any():
        gthr = float(gpm.quantile(UNIVERSE_FILTERS.gpm_floor_quantile))
        keep &= ~(gpm <= gthr)
    div = group[_DIV_COL]
    if div.notna().any():
        dthr = float(div.quantile(UNIVERSE_FILTERS.dividend_min_percentile))
        keep &= ~(div < dthr)
    return group[keep]


def _block_weighted_score(group: pd.DataFrame) -> pd.Series:
    """The committed block-weighted defensive z-blend over one surviving group.

    ``score = Σ_block block_weight(block) · mean_{f in block}(sign_f · zscore(neut_f))``
    — z-normalisation over the SURVIVING set (the input ``group`` is already
    exclusion-filtered + all-7-neut-present).
    """
    score = pd.Series(0.0, index=group.index)
    for block in BLOCK_NAMES:
        facs = factors_in_block(block)
        block_z = pd.concat(
            [factor_sign(f) * _zscore(group[_neut(f)]) for f in facs], axis=1
        ).mean(axis=1)
        score = score + block_weight(block) * block_z
    return score


def build_defensive_ranker_table(neut_panel: pd.DataFrame) -> pd.DataFrame:
    """``(date, ts_code, ranker_score, ranker_pct, log_circ_mv)`` for the D1 ranker.

    Per date: apply the committed exclusion gates on the RAW columns, drop any name
    missing one of the seven ``<factor>_neut`` columns, then rank on the committed
    block-weighted z-blend. ``ranker_pct`` is the within-date percentile rank in
    [0, 1] (higher = more defensive-attractive). An empty date (all excluded) simply
    contributes no rows.
    """
    neut_cols = [_neut(f.name) for f in RANKER_FACTORS]
    raw_gate_cols = [_LOTTERY_COL, _ROE_COL, _GPM_COL, _DIV_COL]
    need = [*neut_cols, *raw_gate_cols, "date", "ts_code", "log_circ_mv"]
    missing = [c for c in need if c not in neut_panel.columns]
    if missing:
        raise KeyError(f"neut_panel missing columns: {missing}")

    rows: list[pd.DataFrame] = []
    for _date, grp in neut_panel.groupby("date", sort=True):
        survivors = apply_exclusion_gates(grp).dropna(subset=neut_cols).copy()
        if survivors.empty:
            continue
        survivors["ranker_score"] = _block_weighted_score(survivors).to_numpy()
        # Fail-closed guard (codex P1): after dropna-all-7 every neut value is finite,
        # so a finite std yields a finite z and a finite blend; a zero-variance factor
        # correctly contributes 0 (no cross-sectional info). This drops any residual
        # non-finite score so a name is never ranked on a NaN.
        survivors = survivors[np.isfinite(survivors["ranker_score"])].copy()
        if survivors.empty:
            continue
        survivors["ranker_pct"] = survivors["ranker_score"].rank(
            pct=True, method="average"
        )
        rows.append(survivors[list(RANKER_TABLE_COLUMNS)])
    if not rows:
        return pd.DataFrame(columns=list(RANKER_TABLE_COLUMNS))
    return pd.concat(rows, ignore_index=True)


__all__ = [
    "RANKER_TABLE_COLUMNS",
    "apply_exclusion_gates",
    "build_defensive_ranker_table",
    "build_health_overrides",
    "panel_universe",
    "scores_by_day",
    "universe_by_day",
]
