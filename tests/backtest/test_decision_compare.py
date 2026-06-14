"""Tests for AE-003 fixed-point decision comparison.

The headline property (amendment §2.4 / acceptance "定点决策跨 numpy 版本恒等"):
a decision is computed in an integer domain so it cannot flip on float-repr or
numpy-version (NEP 50) differences.
"""

from __future__ import annotations

import pytest

from backend.utils.decision_compare import (
    CENTS_SCALE,
    DEFAULT_RATIO_SCALE,
    decision_compare,
    quantize,
    to_cents,
    to_shares,
)


def test_float_repr_noise_collapsed() -> None:
    # The canonical float-equality trap: 0.1 + 0.2 != 0.3 in bare float.
    assert (0.1 + 0.2) != 0.3
    # In the fixed-point domain it is equal (both -> 300000000 at 1e-9).
    assert decision_compare(0.1 + 0.2, 0.3, "==")
    assert decision_compare(0.1 + 0.2, 0.3, ">=")
    assert decision_compare(0.1 + 0.2, 0.3, "<=")
    assert not decision_compare(0.1 + 0.2, 0.3, ">")


@pytest.mark.parametrize(
    ("a", "b", "op", "expected"),
    [
        (0.75, 0.75, ">=", True),
        (0.7499, 0.75, ">=", False),  # below by 1e-4 → a real difference
        (0.74999999, 0.75, ">=", False),  # below by 1e-8 (> 1e-9 precision)
        (0.40, 0.40, "<=", True),
        (0.6, 0.5, ">", True),
        (0.5, 0.6, "<", True),
        (0.3, 0.3, "!=", False),
    ],
)
def test_basic_ops(a: float, b: float, op: str, expected: bool) -> None:
    assert decision_compare(a, b, op) is expected


def test_quantize_strips_noise_at_scale() -> None:
    # A difference finer than the scale's precision collapses to equality.
    assert quantize(0.5000000001) == quantize(0.5)  # 1e-10 < 1e-9 precision
    assert quantize(0.5000001) != quantize(0.5)  # 1e-7 > 1e-9 precision


def test_quantize_deterministic_for_equal_floats() -> None:
    # Two arithmetic paths to the same value quantise identically — this is
    # the cross-version invariance guarantee (float() strips numpy dtype).
    left = sum(0.1 for _ in range(10))  # 0.9999999999999999
    assert quantize(left) == quantize(1.0)


def test_to_cents_and_shares() -> None:
    assert to_cents(12.34) == 1234
    assert to_cents(12.345) == 1234  # round-half-to-even: 1234.5 -> 1234
    assert to_cents(12.355) == 1236  # 1235.5 -> 1236 (even)
    assert to_shares(199.9999999) == 200
    assert CENTS_SCALE == 100 and DEFAULT_RATIO_SCALE == 10**9


def test_non_finite_fails_closed() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        quantize(float("nan"))
    with pytest.raises(ValueError, match="non-finite"):
        decision_compare(float("inf"), 1.0, ">")


def test_unknown_op_raises() -> None:
    with pytest.raises(KeyError):
        decision_compare(1.0, 2.0, "=<")
