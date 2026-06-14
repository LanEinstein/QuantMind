"""Fixed-point, version-invariant comparison for decision thresholds (AE-003).

NumPy 2.0 (NEP 50) changed scalar type promotion: a mixed numpy/Python float
threshold comparison can produce a value that differs by a sub-ULP amount
across numpy versions, which can *flip* a borderline ``score >= gate`` decision
and therefore change a trade. The backtest's golden-vector oracle and any
replayable decision must be **deterministic**: the same inputs must yield the
same decision on any numpy version, any platform.

The fix (amendment P2-2-amendment-2026-06-14 §2.4 "决策阈值定点化"): compare in
a **fixed-point integer domain**. Each operand is coerced to a Python ``float``
(stripping the numpy scalar type), scaled, and rounded to an ``int`` with
deterministic round-half-to-even; the comparison is then a pure-integer
comparison that no float-repr / promotion change can perturb.

This also makes ``0.1 + 0.2 == 0.3`` behave (both quantise to ``300000000`` at
the 1e-9 ratio scale) — float-representation noise below the chosen precision
is collapsed before the decision.

Money / volume have their own natural scales (cents / shares); ratios and
percentiles use :data:`DEFAULT_RATIO_SCALE` (1e-9 precision — far finer than
any real percentile gate, so it never changes a genuine decision, only kills
sub-ULP noise).
"""

from __future__ import annotations

import operator
from collections.abc import Callable

DEFAULT_RATIO_SCALE = 10**9
"""Fixed-point scale for ratio / percentile comparisons (1e-9 precision)."""

CENTS_SCALE = 100
"""Money / price scale: 1 unit = 1 分 (RMB cent)."""

_OPS: dict[str, Callable[[int, int], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def quantize(value: float, scale: int = DEFAULT_RATIO_SCALE) -> int:
    """Coerce ``value`` to a Python float, scale, and round to ``int``.

    ``float(value)`` strips a numpy scalar's dtype so the result is identical
    across numpy versions; ``round`` is deterministic round-half-to-even.

    Raises:
        ValueError: ``value`` is NaN / infinite (a quantised threshold must be
            finite — a non-finite operand is a fail-closed caller bug).
    """
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):  # NaN or ±inf
        raise ValueError(f"cannot quantize non-finite value {value!r}")
    return round(f * scale)


def decision_compare(
    a: float,
    b: float,
    op: str,
    *,
    scale: int = DEFAULT_RATIO_SCALE,
) -> bool:
    """Compare ``a <op> b`` in a fixed-point domain (deterministic).

    Args:
        a: Left operand (the computed value).
        b: Right operand (the threshold).
        op: One of ``< <= > >= == !=``.
        scale: Fixed-point scale (default :data:`DEFAULT_RATIO_SCALE`). Use
            :data:`CENTS_SCALE` for money/price.

    Returns:
        The boolean result of the quantised comparison.

    Raises:
        KeyError: unknown ``op``.
        ValueError: a non-finite operand (via :func:`quantize`).
    """
    return _OPS[op](quantize(a, scale), quantize(b, scale))


def to_cents(yuan: float) -> int:
    """RMB yuan -> integer 分 (deterministic round-half-to-even)."""
    return quantize(yuan, CENTS_SCALE)


def cents_to_yuan(cents: int) -> float:
    """Integer 分 -> yuan (display/serialisation only)."""
    return cents / CENTS_SCALE


def to_shares(quantity: float) -> int:
    """Round a share quantity to a whole-share integer (A-share lots are
    handled by the caller; this only removes float noise)."""
    return quantize(quantity, 1)


__all__ = [
    "CENTS_SCALE",
    "DEFAULT_RATIO_SCALE",
    "cents_to_yuan",
    "decision_compare",
    "quantize",
    "to_cents",
    "to_shares",
]
