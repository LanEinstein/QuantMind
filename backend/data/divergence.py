"""Pure primary-vs-backup price divergence scoring.

P0-8 §1.1.3 locks the divergence signal as ``|primary - fallback| / primary
> divergence_threshold_pct`` (default ``0.003`` = 0.3%). The function
returns a :class:`DivergenceReport` so DataQualityProvider can include
the raw relative diff in the HOLD reason payload.

Fallback-missing semantics:
* ``fallback_price is None`` → ``relative_diff=None`` and
  ``is_divergent=False``. P0-8 §1.1.3 explicitly defers single-source
  failure to the staleness / quote-unavailable signals so the same
  underlying outage is not double-counted.
* ``primary_price <= 0`` → same as fallback-missing. A zero / negative
  primary cannot anchor a relative comparison, and the downstream
  ``quote_unavailable`` path will already flag the outage.

This module is part of the data-quality boundary (P0-8 §2 redline 8,
P1-2.B §2 redline 8): no ``backend.llm`` / ``backend.agents`` /
``backend.risk`` imports, no IO.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DivergenceReport:
    """Pure result of one primary/fallback divergence comparison.

    Attributes:
        code: 6-digit stock code being judged.
        primary_price: Price from the primary leg (``adata``).
        fallback_price: Price from the fallback leg (``akshare``); ``None``
            when the fallback could not be obtained.
        relative_diff: ``|primary - fallback| / primary`` ∈ [0.0, ∞);
            ``None`` when fallback is absent or primary is non-positive.
        threshold_pct: Threshold this evaluation used (P0-8 default 0.003).
        is_divergent: ``relative_diff > threshold_pct`` (False when
            ``relative_diff is None``).
    """

    code: str
    primary_price: float
    fallback_price: float | None
    relative_diff: float | None
    threshold_pct: float
    is_divergent: bool


def evaluate_divergence(
    *,
    code: str,
    primary_price: float,
    fallback_price: float | None,
    threshold_pct: float,
) -> DivergenceReport:
    """Score the primary vs fallback price gap against ``threshold_pct``.

    Args:
        code: 6-digit stock code (passed through, not validated).
        primary_price: ``adata`` quote.
        fallback_price: ``akshare`` quote, or ``None`` if it could not be
            fetched.
        threshold_pct: Relative-diff threshold. P0-8 locks the default
            at ``0.003`` (= 0.3%).

    Returns:
        DivergenceReport: Result with ``is_divergent`` and the raw
        relative diff for audit.
    """
    if (
        fallback_price is None
        or primary_price <= 0
        or not math.isfinite(primary_price)
        or not math.isfinite(fallback_price)
    ):
        # Non-finite (NaN / inf) inputs are folded into the
        # fallback-missing branch so the data-quality boundary cannot
        # silently pass a vendor brown-out via ``NaN > threshold == False``.
        # The downstream gate (DataQualityProvider) treats this as a
        # conservative breach (single-source view).
        return DivergenceReport(
            code=code,
            primary_price=primary_price,
            fallback_price=fallback_price,
            relative_diff=None,
            threshold_pct=threshold_pct,
            is_divergent=False,
        )
    rel = abs(primary_price - fallback_price) / primary_price
    return DivergenceReport(
        code=code,
        primary_price=primary_price,
        fallback_price=fallback_price,
        relative_diff=rel,
        threshold_pct=threshold_pct,
        is_divergent=rel > threshold_pct,
    )


__all__ = ["DivergenceReport", "evaluate_divergence"]
