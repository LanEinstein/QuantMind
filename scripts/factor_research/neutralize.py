"""Cross-sectional industry + size factor neutralization (R2-2 / S4).

The benchmark-relative arm's core defence against the round-1 failure mode (a
defensive book that systematically lags a cap-weighted index when large-cap
sectors lead): before composing factors, residualise each one against the
**point-in-time industry** (SW L1 dummies) and **log market cap**. The residual
keeps only the part of the factor orthogonal to a name's sector and size, so a
tilt on the composite is industry- and size-neutral by construction rather than
a hidden bet on a sector or the size factor.

``neutralize_cross_section`` is one date's OLS residualisation:
``factor ~ 1 + industry_dummies(drop-first) + log_size``. It is deterministic
(``numpy.linalg.lstsq``) and fail-closed — a name missing its factor, industry,
or size is dropped to ``None`` (never an invented bucket), and a cross-section
with fewer than ``min_obs`` usable names yields all ``None`` (too thin to
residualise honestly). Residuals are the orthogonal projection, well-defined
even under a rank-deficient design.

``neutralize_panel`` applies it per rebalance date over a tidy panel, adding a
``<factor>_neut`` column. An optional ``winsor_quantile`` clips each factor's
cross-section before the fit so a single extreme value (e.g. a +18000% earnings
YoY) cannot leverage every other name's residual — off by default (the round-2
search manifest owns that degree of freedom; the diagnostic enables it).

Pure numpy/stdlib; no ``backend`` import.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray

DEFAULT_MIN_OBS: int = 20


def _is_finite_number(value: object) -> bool:
    """True iff ``value`` is a finite real number (not bool, not NaN/inf)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _clean_industry(value: object) -> str | None:
    """Normalise an industry cell to a non-empty label or ``None`` (missing).

    Handles every missing shape a panel can carry: Python ``None``, float NaN,
    and pandas nullable ``pd.NA`` / ``NaT`` (codex P3 — a nullable-dtype column
    hands ``pd.NA``, whose ``str()`` is ``"<NA>"``; the reject set below catches
    its (and NaN/NaT's) stringified form so it fails closed to ``None`` instead
    of being mistaken for a real industry).
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "<na>", "none", "nat"}:
        return None
    return text


def _winsorize(values: list[float], quantile: float) -> list[float]:
    """Clip ``values`` to their ``[q, 1-q]`` quantiles (deterministic)."""
    if quantile <= 0.0 or len(values) < 2:
        return values
    arr = np.asarray(values, dtype=np.float64)
    lo = float(np.quantile(arr, quantile))
    hi = float(np.quantile(arr, 1.0 - quantile))
    return [min(max(v, lo), hi) for v in values]


def neutralize_cross_section(
    industry: Sequence[object],
    log_size: Sequence[object],
    values: Sequence[object],
    *,
    min_obs: int = DEFAULT_MIN_OBS,
    winsor_quantile: float = 0.0,
) -> list[float | None]:
    """Residualise one cross-section: ``factor ~ 1 + industry + log_size``.

    Returns a residual per input row aligned to ``values`` (``None`` for any row
    missing its factor / industry / size, and all ``None`` when fewer than
    ``min_obs`` rows are usable — fail-closed).
    """
    n = len(values)
    if not (len(industry) == len(log_size) == n):
        raise ValueError("industry / log_size / values must be the same length")

    industries = [_clean_industry(industry[i]) for i in range(n)]
    valid = [
        i
        for i in range(n)
        if industries[i] is not None
        and _is_finite_number(log_size[i])
        and _is_finite_number(values[i])
    ]
    if len(valid) < min_obs:
        return [None] * n

    # ``valid`` guarantees these are present + finite; cast for the type checker.
    valid_ind: list[str] = [cast("str", industries[i]) for i in valid]
    y_raw = [cast("float", values[i]) for i in valid]
    y = np.asarray(_winsorize(y_raw, winsor_quantile), dtype=np.float64)
    sizes = np.asarray([cast("float", log_size[i]) for i in valid], dtype=np.float64)

    # Industry dummies, drop-first (the first sorted industry is the reference,
    # absorbed into the intercept) — avoids the dummy-variable trap.
    present = sorted(set(valid_ind))
    dummy_cols = present[1:]
    n_valid = len(valid)
    cols: list[NDArray[np.float64]] = [np.ones(n_valid, dtype=np.float64)]
    for ind in dummy_cols:
        cols.append(
            np.asarray(
                [1.0 if vi == ind else 0.0 for vi in valid_ind], dtype=np.float64
            )
        )
    cols.append(sizes)
    design = np.column_stack(cols)

    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta

    out: list[float | None] = [None] * n
    for pos, i in enumerate(valid):
        r = float(resid[pos])
        out[i] = r if math.isfinite(r) else None
    return out


def neutralize_panel(
    panel: pd.DataFrame,
    factors: Sequence[str],
    *,
    industry_col: str = "industry_l1",
    size_col: str = "log_circ_mv",
    date_col: str = "date",
    min_obs: int = DEFAULT_MIN_OBS,
    winsor_quantile: float = 0.0,
) -> pd.DataFrame:
    """Add a ``<factor>_neut`` column for each factor, residualised per date.

    Each rebalance date's cross-section is neutralised independently. A
    new-frame copy is returned (the input panel is never mutated).
    """
    out = panel.copy()
    for factor in factors:
        out[f"{factor}_neut"] = float("nan")
    for _, idx in panel.groupby(date_col, sort=True).groups.items():
        sub = panel.loc[idx]
        industry = sub[industry_col].tolist()
        log_size = sub[size_col].tolist()
        for factor in factors:
            resid = neutralize_cross_section(
                industry,
                log_size,
                sub[factor].tolist(),
                min_obs=min_obs,
                winsor_quantile=winsor_quantile,
            )
            out.loc[idx, f"{factor}_neut"] = pd.Series(resid, index=idx, dtype=float)
    return out


__all__ = [
    "DEFAULT_MIN_OBS",
    "neutralize_cross_section",
    "neutralize_panel",
]
