"""Off-benchmark exposure constraints for the benchmark-relative tilt (R2-4 / S1).

R2-3's decisive diagnostic: the unconstrained benchmark-relative book has a
SYSTEMATIC small-cap drift (size active −0.60…−0.71 std, gross active 43–47%).
The tilt spans the ~1700-name investable universe but starts from 300 CSI300
weights, so high-composite NON-constituents (mostly small/mid caps, w_bench=0)
collect positive active — the disclosed +15–17% excess / IR ~0.3 is contaminated
by a size bet rather than a clean factor tilt.

This module supplies the three fixes R2-4 searches over (all deterministic, pure,
no ``backend`` import); the benchmark-relative builder applies the chosen one
between the box-clip and the long-only-floor/renormalize:

* ``constituent_only`` — tilt ONLY CSI300 constituents (a true enhanced-index
  product). Implemented as a score pre-filter: non-constituent scores → NaN, so
  the z-score and active overlay rank WITHIN the benchmark; non-members are held
  at 0. This is the cleanest, most deployable fix.
* ``size_neutral`` — remove the active vector's projection onto z(log size) so
  ``Σ active·z(size) ≈ 0`` (portfolio-level size neutrality) before the floor.
* ``capped_nonconstituent`` — scale the non-constituent active down so
  ``Σ|non-const active| ≤ cap``, redistributing capacity into constituent tilt.

The realised (post-floor/renormalize) exposure may differ from the constrained
pre-floor active; the builder discloses the realised size/net/forced-UW active,
so a reader can confirm the residual drift is bounded.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

# The four exposure-constraint schemes the R2-4 search enumerates. The default
# ``unconstrained`` reproduces the R2-3 construction byte-for-byte.
CONSTRAINTS: frozenset[str] = frozenset(
    {"unconstrained", "constituent_only", "size_neutral", "capped_nonconstituent"}
)
DEFAULT_NONCONST_CAP: float = 0.10


def validate_constraint(name: str) -> None:
    """Fail closed on an unknown exposure-constraint name."""
    if name not in CONSTRAINTS:
        raise ValueError(
            f"unknown exposure_constraint {name!r}; "
            f"expected one of {sorted(CONSTRAINTS)}"
        )


def filter_constituents(score: pd.Series, w_bench: Mapping[str, float]) -> pd.Series:
    """Return ``score`` with non-constituent (``w_bench <= 0`` / absent) → NaN.

    The benchmark-relative builder then z-scores and tilts only the surviving
    constituent names — a true enhanced-index overlay ranked WITHIN the index.
    A new Series is returned (the input is never mutated).
    """
    out = score.copy()
    keep = pd.Index([str(c) for c in out.index]).map(
        lambda c: float(w_bench.get(c, 0.0)) > 0.0
    )
    out[~pd.Series(keep, index=out.index)] = float("nan")
    return out


def size_neutralize_active(
    active: Mapping[str, float], sizes: Mapping[str, float]
) -> dict[str, float]:
    """Project the active vector onto the orthogonal complement of ``{1, z(size)}``.

    ``a'_i = a_i − ᾱ − β·z_i`` where ``ᾱ = mean(a)`` and ``β = Σ(a·z)/Σ(z²)`` over
    names with a finite size — so over those names BOTH ``Σ a' = 0`` (net-zero)
    AND ``Σ(a'·z) = 0`` (size-orthogonal). Removing the mean too (codex P2) keeps
    the scored-sleeve renormalize from amplifying a non-zero-sum overlay back into
    a size bet. Names without a finite size keep their active unchanged (they do
    not enter the calculation); a degenerate (≤1 name or zero-variance) size
    cross-section returns the active unchanged (cannot neutralise).

    NOTE: this neutralises the pre-floor OVERLAY. The long-only floor and the
    forced-underweight gap-fill (redistribution of excluded-constituent weight
    through the scored sleeve) can still leave a realised size residual that the
    backtest's ``mean_size_active`` discloses — which is exactly why
    ``constituent_only`` / ``capped_nonconstituent`` are the effective fixes and
    ``size_neutral`` is the weak one (see the R2-4 diagnostic).
    """
    sized = [
        c for c in active if c in sizes and _finite(sizes[c]) and _finite(active[c])
    ]
    if len(sized) < 2:
        return dict(active)
    s = [float(sizes[c]) for c in sized]
    mean_size = sum(s) / len(s)
    var = sum((x - mean_size) ** 2 for x in s) / len(s)
    if var <= 0.0:
        return dict(active)
    std = math.sqrt(var)
    z = {c: (float(sizes[c]) - mean_size) / std for c in sized}
    mean_active = sum(float(active[c]) for c in sized) / len(sized)
    dot = sum(float(active[c]) * z[c] for c in sized)
    zz = sum(z[c] ** 2 for c in sized)
    if zz <= 0.0:
        return dict(active)
    beta = dot / zz
    out = {c: float(v) for c, v in active.items()}
    for c in sized:
        out[c] = float(active[c]) - mean_active - beta * z[c]
    return out


def cap_nonconstituent_weights(
    weights: Mapping[str, float],
    w_bench: Mapping[str, float],
    cap: float,
    *,
    redistribute_into: frozenset[str] | None = None,
) -> dict[str, float]:
    """Bound the REALISED non-constituent active of a FINAL long-only book.

    Applied to the post-floor/renormalize weight book (not the pre-floor active)
    so the advertised cap holds on what is actually held (codex P2: a pre-floor
    cap is re-inflated by the scored-sleeve scaling). Non-constituents
    (``w_bench <= 0`` / absent) hold realised active = their weight (benchmark
    weight 0). If their gross exceeds ``cap`` they are scaled to ``cap`` and the
    freed weight is redistributed into the SCORED constituent sleeve pro-rata, so
    the book stays fully invested (Σw = 1), long-only, and the realised
    off-benchmark active is exactly ``cap``.

    ``redistribute_into`` (the scored codes) protects the builder invariant that
    UNSCORED benchmark constituents stay at their exact benchmark weight (codex
    P2): the freed weight goes only into scored constituents, never the
    fail-closed-held names. When ``None`` (or no scored constituent can absorb
    it) it falls back to all constituents. A book already within ``cap`` (or with
    no constituent sleeve at all) is returned unchanged.
    """
    nonconst = {c for c in weights if float(w_bench.get(c, 0.0)) <= 0.0}
    gross = sum(float(weights[c]) for c in nonconst)
    if gross <= cap or gross <= 0.0:
        return {c: float(v) for c, v in weights.items()}
    constituents = [c for c in weights if c not in nonconst]
    targets = (
        [c for c in constituents if c in redistribute_into]
        if redistribute_into is not None
        else list(constituents)
    )
    target_total = sum(float(weights[c]) for c in targets)
    if target_total <= 0.0:  # no scored constituent sleeve → fall back to all
        targets = constituents
        target_total = sum(float(weights[c]) for c in targets)
        if target_total <= 0.0:
            return {c: float(v) for c, v in weights.items()}
    target_set = set(targets)
    freed = gross - cap
    scale = cap / gross
    out: dict[str, float] = {}
    for c, v in weights.items():
        if c in nonconst:
            out[c] = float(v) * scale
        elif c in target_set:
            out[c] = float(v) + freed * (float(v) / target_total)
        else:  # unscored constituent held fail-closed at benchmark — untouched
            out[c] = float(v)
    return out


def _finite(value: object) -> bool:
    """True iff ``value`` is a finite real number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


__all__ = [
    "CONSTRAINTS",
    "DEFAULT_NONCONST_CAP",
    "cap_nonconstituent_weights",
    "filter_constituents",
    "size_neutralize_active",
    "validate_constraint",
]
