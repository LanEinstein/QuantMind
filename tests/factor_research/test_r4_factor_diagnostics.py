"""Tests for the round-4 factor diagnostics (R4-4).

Cover the pure carry-decision gate (IC + carry-redundancy + mutual-dedup), the
pairwise collinearity helper, and a smoke build of the full Markdown report over
a synthetic r4 panel.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.benchmark_relative import R3_CARRY_FACTORS
from scripts.factor_research.factor_lib import R4_FACTOR_NAMES
from scripts.factor_research.r2_factor_diagnostics import FactorVerdict
from scripts.factor_research.r4_factor_diagnostics import (
    COLLINEARITY_CEILING,
    build_report,
    decide_carry,
    max_carry_collinearity,
    pairwise_collinearity,
)


def _v(
    factor: str, t: float, *, aligned: bool = True, signal: bool = True
) -> FactorVerdict:
    return FactorVerdict(
        factor=factor,
        best_horizon="fwd_ret_20d",
        best_ic=0.02,
        best_t=t,
        aligned=aligned,
        has_signal=signal,
    )


def _no_collin() -> dict[frozenset[str], tuple[float, int]]:
    return {}


def _carry_clean() -> dict[str, tuple[str, float, int]]:
    return {f: ("ep_ttm", 0.1, 400) for f in R4_FACTOR_NAMES}


class TestDecideCarry:
    def test_ic_gate_drops_weak_and_misaligned(self) -> None:
        vs = [
            _v("np_rev_neut", 4.0, aligned=True, signal=True),  # kept
            _v("eps_rev_neut", 5.0, aligned=False, signal=True),  # misaligned → drop
            _v("disp_neut", 1.0, aligned=True, signal=False),  # weak → drop
        ]
        d = decide_carry(vs, carry_collin=_carry_clean(), mutual=_no_collin())
        assert d.survivors == ("np_rev",)
        assert "eps_rev" in d.no_signal and "disp" in d.no_signal

    def test_carry_redundancy_drops_even_if_ic_passes(self) -> None:
        vs = [_v("np_rev_neut", 4.0), _v("eps_rev_neut", 4.5)]
        # eps_rev redundant with a carry factor (|corr| 0.85 > ceiling)
        carry = {**_carry_clean(), "eps_rev": ("ep_ttm", 0.85, 400)}
        d = decide_carry(vs, carry_collin=carry, mutual=_no_collin())
        assert "eps_rev" in d.carry_redundant
        assert d.survivors == ("np_rev",)

    def test_mutual_dedup_keeps_stronger_t(self) -> None:
        # np_rev (t 4.0) and eps_rev (t 5.0) are mutually collinear → keep eps_rev
        # (stronger |t|), drop np_rev as mutually redundant.
        vs = [_v("np_rev_neut", 4.0), _v("eps_rev_neut", 5.0)]
        mutual = {frozenset(("np_rev", "eps_rev")): (0.9, 400)}
        d = decide_carry(vs, carry_collin=_carry_clean(), mutual=mutual)
        assert d.survivors == ("eps_rev",)
        assert d.mutual_redundant == ("np_rev",)

    def test_low_support_flag(self) -> None:
        # a survivor whose carry-collinearity support is THIN is flagged (carried).
        vs = [_v("np_rev_neut", 4.0)]
        carry = {
            **_carry_clean(),
            "np_rev": ("ep_ttm", 0.1, 10),
        }  # 10 < MIN_COLLIN_DATES
        d = decide_carry(vs, carry_collin=carry, mutual=_no_collin())
        assert d.survivors == ("np_rev",)
        assert d.low_support == ("np_rev",)

    def test_orthogonal_factors_all_survive(self) -> None:
        vs = [_v(f"{f}_neut", 4.0 + i * 0.1) for i, f in enumerate(R4_FACTOR_NAMES)]
        d = decide_carry(vs, carry_collin=_carry_clean(), mutual=_no_collin())
        assert set(d.survivors) == set(R4_FACTOR_NAMES)
        # survivors preserve registry order
        assert d.survivors == R4_FACTOR_NAMES

    def test_all_dropped_yields_empty_survivors(self) -> None:
        vs = [_v(f"{f}_neut", 1.0, signal=False) for f in R4_FACTOR_NAMES]
        d = decide_carry(vs, carry_collin=_carry_clean(), mutual=_no_collin())
        assert d.survivors == ()
        assert set(d.no_signal) == set(R4_FACTOR_NAMES)


def _synthetic_r4_panel() -> pd.DataFrame:
    """3 dates × 25 codes with every carry + R4 + neutralization column present."""
    rows = []
    for di, date in enumerate(("20200110", "20200117", "20200124")):
        for i in range(25):
            row: dict[str, object] = {
                "date": date,
                "code": f"6000{i:02d}",
                "ts_code": f"6000{i:02d}.SH",
                "industry_l1": "801080.SI" if i % 2 == 0 else "801150.SI",
                "circ_mv": 1e5 + i * 1e4,
                "log_circ_mv": float(11 + i * 0.05),
                "fwd_ret_5d": (i - 12) * 0.001 + di * 0.0001,
                "fwd_ret_10d": (i - 12) * 0.0012,
                "fwd_ret_20d": (i - 12) * 0.0015,
            }
            for f in (*R3_CARRY_FACTORS, *R4_FACTOR_NAMES):
                row[f] = float((i * 7 + di * 3 + hash(f) % 11) % 17) - 8.0
            rows.append(row)
    return pd.DataFrame(rows)


def test_pairwise_collinearity_two_way_support() -> None:
    panel = _synthetic_r4_panel()
    # a factor is perfectly correlated with itself; a constant-shifted copy too.
    panel["np_rev_copy"] = panel["np_rev"]
    assert pairwise_collinearity(panel, "np_rev", "np_rev_copy") == pytest.approx(1.0)


def test_max_carry_collinearity_returns_pair() -> None:
    panel = _synthetic_r4_panel()
    neut_cols = [f"{f}_neut" for f in (*R3_CARRY_FACTORS, *R4_FACTOR_NAMES)]
    for c in neut_cols:
        base = c[: -len("_neut")]
        panel[c] = panel[base]  # stand-in neut columns for the helper's lookups
    name, val, support = max_carry_collinearity(panel, "np_rev")
    assert name in R3_CARRY_FACTORS
    assert 0.0 <= val <= 1.0
    assert support >= 0


def test_build_report_smoke() -> None:
    report = build_report(_synthetic_r4_panel(), params_note="staleness=90/lookback=90")
    assert "# Round-4 factor diagnostics" in report
    assert "Carry decision" in report
    assert "Analyst coverage" in report
    for f in R4_FACTOR_NAMES:
        assert f in report
    # R4_CARRY line always lists the round-3 carry cluster
    assert "ret_5d" in report and "accr" in report
    assert COLLINEARITY_CEILING == pytest.approx(0.7)
