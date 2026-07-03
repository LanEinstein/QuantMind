"""Unit tests for the D1 block-weighted defensive ranker + exclusion gates.

Pin the committed scoring behaviour on tiny synthetic cross-sections: the
block-weighted z-blend matches a hand computation, the committed exclusion gates
drop exactly the right names, a name missing one neutralised factor is dropped, and
the committed prior signs are applied in the right direction (low vol / low beta /
low accruals → higher defensive score).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.factor_research import defensive_d1_ranker as d1r

_NEUT_COLS = (
    "vol_20d_neut",
    "dv_ratio_neut",
    "roe_neut",
    "gpm_neut",
    "accr_neut",
    "beta_neut",
    "tail_beta_neut",
)


def _two_name_group() -> pd.DataFrame:
    """Two names A/B with every neut factor 1.0 vs 0.0 (z ∈ {+1, −1})."""
    return pd.DataFrame(
        {
            "date": ["20180102", "20180102"],
            "ts_code": ["A.SH", "B.SH"],
            "log_circ_mv": [10.0, 10.0],
            **{col: [1.0, 0.0] for col in _NEUT_COLS},
        }
    )


def test_block_weighted_score_matches_hand_computation() -> None:
    group = _two_name_group()
    score = d1r._block_weighted_score(group)
    # For [1, 0] the population z-score is +1 (A) / −1 (B). Applying committed signs
    # and block weights (low_vol .35 · −z, dividend .35 · +z, quality .20 · mean(+roe,
    # +gpm, −accr), tail .10 · mean(−beta, −tail_beta)):
    #   A = .35(−1) + .35(+1) + .20(1/3) + .10(−1) = −1/30
    #   B = .35(+1) + .35(−1) + .20(−1/3) + .10(+1) = +1/30
    assert score.iloc[0] == pytest.approx(-1.0 / 30.0)
    assert score.iloc[1] == pytest.approx(1.0 / 30.0)
    # The defensive name (lower vol/beta/accr, higher div/roe/gpm = B) ranks higher.
    assert score.iloc[1] > score.iloc[0]


def test_low_vol_sign_is_defensive() -> None:
    # Only vol differs: the lower-vol name must score strictly higher (sign −1).
    group = pd.DataFrame(
        {
            "date": ["d", "d"],
            "ts_code": ["LOWVOL", "HIVOL"],
            "log_circ_mv": [10.0, 10.0],
            "vol_20d_neut": [0.0, 1.0],
            **{col: [0.5, 0.5] for col in _NEUT_COLS if col != "vol_20d_neut"},
        }
    )
    score = d1r._block_weighted_score(group)
    assert score.iloc[0] > score.iloc[1]


def _gate_frame() -> pd.DataFrame:
    """Ten names engineered so each committed gate drops one distinct name."""
    n = 10
    return pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"n{i}.SH" for i in range(n)],
            "log_circ_mv": [10.0] * n,
            # lottery: n9 is the top-decile MAX name.
            "max_20d": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.90],
            # ROE floor: n2 ≤ 0.
            "roe": [0.10, 0.10, -0.02, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10],
            # GPM bottom decile: n1.
            "gpm": [0.30, 0.05, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30],
            # Dividend below median: n3 (1.0 « the median 9.0).
            "dv_ratio": [9.0, 9.0, 9.0, 1.0, 9.0, 9.0, 9.0, 9.0, 9.0, 9.0],
            **{col: [0.5] * n for col in _NEUT_COLS},
        }
    )


def test_exclusion_gates_drop_the_committed_names() -> None:
    survivors = d1r.apply_exclusion_gates(_gate_frame())
    got = set(survivors["ts_code"])
    # lottery→n9, roe→n2, gpm→n1, dividend→n3 removed.
    assert got == {"n0.SH", "n4.SH", "n5.SH", "n6.SH", "n7.SH", "n8.SH"}


def test_roe_exactly_zero_is_dropped() -> None:
    frame = _gate_frame().copy()
    frame.loc[frame["ts_code"] == "n4.SH", "roe"] = 0.0  # ≤ floor 0.0
    survivors = d1r.apply_exclusion_gates(frame)
    assert "n4.SH" not in set(survivors["ts_code"])


def test_missing_gate_value_not_dropped_by_that_gate() -> None:
    # A NaN max_20d cannot be confirmed as a lottery name → the lottery gate keeps it
    # (it must still pass the neut dropna in the full ranker build).
    frame = _gate_frame().copy()
    frame.loc[frame["ts_code"] == "n9.SH", "max_20d"] = np.nan
    survivors = d1r.apply_exclusion_gates(frame)
    assert "n9.SH" in set(survivors["ts_code"])


def _ranker_panel(n: int = 8) -> pd.DataFrame:
    """A single-date neut panel of ``n`` names that all clear the gates."""
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"c{i}.SH" for i in range(n)],
            "log_circ_mv": rng.uniform(9.0, 12.0, n),
            "max_20d": rng.uniform(0.02, 0.05, n),  # none in a lottery tail
            "roe": rng.uniform(0.05, 0.20, n),  # all > 0
            "gpm": rng.uniform(0.20, 0.40, n),  # none in a bottom decile gap
            "dv_ratio": rng.uniform(6.0, 10.0, n),  # all above a tight median
            **{col: rng.normal(0.0, 1.0, n) for col in _NEUT_COLS},
        }
    )


def test_build_ranker_table_shape_and_pct() -> None:
    table = d1r.build_defensive_ranker_table(_ranker_panel())
    assert list(table.columns) == list(d1r.RANKER_TABLE_COLUMNS)
    assert len(table) > 0
    # ranker_pct is a within-date percentile in (0, 1].
    assert table["ranker_pct"].between(0.0, 1.0).all()
    assert table["ranker_pct"].max() == pytest.approx(1.0)


def test_build_ranker_table_drops_incomplete_neut_rows() -> None:
    # Relative to a clean build (the committed dividend/quality gates legitimately
    # drop names too): NaN-ing one survivor's beta_neut removes exactly that name.
    panel = _ranker_panel()
    base_codes = set(d1r.build_defensive_ranker_table(panel)["ts_code"])
    assert base_codes  # some names clear every gate
    victim = sorted(base_codes)[0]
    panel2 = panel.copy()
    panel2.loc[panel2["ts_code"] == victim, "beta_neut"] = np.nan
    after_codes = set(d1r.build_defensive_ranker_table(panel2)["ts_code"])
    assert base_codes - after_codes == {victim}


def test_build_ranker_table_requires_columns() -> None:
    with pytest.raises(KeyError):
        d1r.build_defensive_ranker_table(pd.DataFrame({"date": ["d"]}))


def test_empty_after_exclusion_returns_empty_table() -> None:
    panel = _ranker_panel(6)
    panel["roe"] = -1.0  # every name fails the ROE floor
    table = d1r.build_defensive_ranker_table(panel)
    assert table.empty
    assert list(table.columns) == list(d1r.RANKER_TABLE_COLUMNS)
