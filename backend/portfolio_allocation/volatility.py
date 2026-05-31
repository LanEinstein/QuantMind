"""Inverse-volatility portfolio weights (Phase P P-002).

Deterministic, pure-stdlib weighting that maps each candidate's realized
volatility (σ, the population stdev of trailing daily returns — same
``volatility_20d`` the screener already computes) to a portfolio weight
``w_i = (1/σ_i) / Σ(1/σ_j)``. Low-volatility names get a larger weight so
each name contributes a more even share of portfolio risk (risk parity's
simplest form). σ is taken from the PIT-pinned frame's ``closes`` upstream,
so identical inputs always yield identical weights (R0 §2.0 PIT replay).

Equal-weight fallback (P0-7-amendment-2026-05-30 §2.1): any σ that is
``None`` (insufficient history) or ``≤ eps`` (frozen / limit-locked → a
``1/σ`` blow-up) is treated as *missing* and never produces ``inf``:

* all names missing → strict equal weight ``1/N``;
* some names missing → the missing names are assigned the **mean of the
  valid ``1/σ`` values** (neutral: neither favored nor dropped), then the
  whole vector is renormalized to sum to 1.

No ``import backend.{llm,agents,mirofish}`` (redline ``[P-002]``).
"""

from __future__ import annotations

import math

__all__ = ["inverse_vol_weights"]


def inverse_vol_weights(
    sigma_by_code: dict[str, float | None], *, eps: float = 1e-9
) -> dict[str, float]:
    """Map ``{code: σ}`` to deterministic inverse-volatility weights summing ≈1.

    A σ that is ``None``, non-finite, or ``≤ eps`` is treated as *missing*
    (equal-weight fallback) so the ``1/σ`` term can never blow up.

    Args:
        sigma_by_code: Per-code realized volatility (``None`` if N/A).
        eps: Floor below which a σ is treated as missing (avoids ``1/0``).

    Returns:
        ``{code: weight}`` with weights ≥ 0 summing to ≈ 1.0 (empty input →
        empty dict). Order follows ``sigma_by_code`` insertion order.
    """
    codes = list(sigma_by_code)
    if not codes:
        return {}

    inv: dict[str, float | None] = {}
    valid: list[float] = []
    for code in codes:
        sigma = sigma_by_code[code]
        if (
            sigma is not None
            and isinstance(sigma, int | float)
            and math.isfinite(sigma)
            and sigma > eps
        ):
            value = 1.0 / float(sigma)
            inv[code] = value
            valid.append(value)
        else:
            inv[code] = None

    # All missing → strict equal weight (never 1/σ on a frozen name).
    if not valid:
        weight = 1.0 / len(codes)
        return {code: weight for code in codes}

    # Partial missing → fill missing names with the mean valid 1/σ (neutral),
    # then renormalize the whole vector to sum to 1.
    mean_valid = sum(valid) / len(valid)
    filled = {
        code: (inv[code] if inv[code] is not None else mean_valid) for code in codes
    }
    total = sum(filled.values())
    if total <= eps:  # pragma: no cover - guarded by `valid` non-empty above
        weight = 1.0 / len(codes)
        return {code: weight for code in codes}
    return {code: filled[code] / total for code in codes}
