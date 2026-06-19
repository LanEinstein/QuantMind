"""Tests for the R2-4 off-benchmark exposure constraints (the R2-3 size-drift fix)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from scripts.factor_research.exposure_constraints import (
    CONSTRAINTS,
    DEFAULT_NONCONST_CAP,
    cap_nonconstituent_weights,
    filter_constituents,
    size_neutralize_active,
    validate_constraint,
)


def test_constraints_membership_and_validation() -> None:
    assert {
        "unconstrained",
        "constituent_only",
        "size_neutral",
        "capped_nonconstituent",
    } == set(CONSTRAINTS)
    for name in CONSTRAINTS:
        validate_constraint(name)  # no raise
    with pytest.raises(ValueError):
        validate_constraint("not_a_constraint")


def test_default_cap_is_a_fraction() -> None:
    assert 0.0 < DEFAULT_NONCONST_CAP < 1.0


# --- filter_constituents ----------------------------------------------------


def test_filter_constituents_nans_non_members() -> None:
    score = pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0})
    w_bench = {"A": 0.5, "B": 0.5, "C": 0.0}  # C zero, D absent → both non-members
    out = filter_constituents(score, w_bench)
    assert out["A"] == 1.0
    assert out["B"] == 2.0
    assert math.isnan(out["C"])
    assert math.isnan(out["D"])
    # input not mutated
    assert score["C"] == 3.0


def test_filter_constituents_empty_bench_nans_all() -> None:
    score = pd.Series({"A": 1.0, "B": 2.0})
    out = filter_constituents(score, {})
    assert out.isna().all()


# --- size_neutralize_active -------------------------------------------------


def test_size_neutralize_removes_size_projection_and_mean() -> None:
    # active correlated with size → both the size projection AND the mean removed
    # (codex P2: net-zero so the later renormalize cannot amplify a non-zero sum).
    sizes = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0}
    active = {c: s + 0.7 for c, s in sizes.items()}  # affine in size (non-zero mean)
    out = size_neutralize_active(active, sizes)
    mean = sum(sizes.values()) / len(sizes)
    std = (sum((s - mean) ** 2 for s in sizes.values()) / len(sizes)) ** 0.5
    dot = sum(out[c] * (sizes[c] - mean) / std for c in sizes)
    assert abs(dot) < 1e-9  # size-orthogonal
    assert abs(sum(out.values())) < 1e-9  # net-zero


def test_size_neutralize_keeps_names_without_size() -> None:
    sizes = {"A": 1.0, "B": 2.0, "C": 3.0}
    active = {"A": 0.1, "B": -0.1, "C": 0.0, "Z": 0.05}  # Z has no size
    out = size_neutralize_active(active, sizes)
    assert out["Z"] == 0.05  # untouched
    assert set(out) == set(active)


def test_size_neutralize_degenerate_size_unchanged() -> None:
    sizes = {"A": 2.0, "B": 2.0, "C": 2.0}  # zero variance
    active = {"A": 0.1, "B": -0.2, "C": 0.3}
    out = size_neutralize_active(active, sizes)
    assert out == active


# --- cap_nonconstituent_weights (realised post-build cap) -------------------


def test_cap_weights_bounds_realised_and_redistributes() -> None:
    w_bench = {"A": 0.5, "B": 0.5}  # A,B constituents; X,Y non-members
    book = {"A": 0.45, "B": 0.45, "X": 0.06, "Y": 0.04}  # nonconst gross = 0.10
    out = cap_nonconstituent_weights(book, w_bench, cap=0.05)
    # realised non-constituent active scaled to exactly the cap
    assert out["X"] + out["Y"] == pytest.approx(0.05)
    assert out["X"] == pytest.approx(0.03)
    assert out["Y"] == pytest.approx(0.02)
    # freed 0.05 redistributed into constituents pro-rata (equal here)
    assert out["A"] == pytest.approx(0.475)
    assert out["B"] == pytest.approx(0.475)
    # fully invested + long-only preserved
    assert sum(out.values()) == pytest.approx(1.0)
    assert all(v >= 0 for v in out.values())


def test_cap_weights_noop_when_under_cap() -> None:
    w_bench = {"A": 1.0}
    book = {"A": 0.97, "X": 0.03}
    out = cap_nonconstituent_weights(book, w_bench, cap=0.10)
    assert out == book


def test_cap_weights_no_constituent_sleeve_unchanged() -> None:
    # all non-members → nowhere to redistribute the freed weight → unchanged
    book = {"X": 0.6, "Y": 0.4}
    out = cap_nonconstituent_weights(book, {}, cap=0.05)
    assert out == book


def test_cap_weights_redistributes_only_into_scored() -> None:
    # U is an UNSCORED constituent held at benchmark → freed weight must NOT touch
    # it (codex P2: preserve the held-at-benchmark invariant).
    w_bench = {"A": 0.4, "U": 0.4}  # A scored constituent, U unscored constituent
    book = {"A": 0.30, "U": 0.40, "X": 0.30}  # X non-member, over the cap; sums to 1
    out = cap_nonconstituent_weights(
        book, w_bench, cap=0.10, redistribute_into=frozenset({"A", "X"})
    )
    assert out["U"] == pytest.approx(0.40)  # unscored constituent untouched
    assert out["X"] == pytest.approx(0.10)  # non-member capped to cap
    assert out["A"] == pytest.approx(0.50)  # all freed (0.20) into the one scored
    assert sum(out.values()) == pytest.approx(1.0)
