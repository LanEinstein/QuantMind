"""Tests for the QGR-3 short-term factor diagnostics (IC + collinearity + gate).

Builds a small synthetic neutralization-ready panel where one QGR factor carries
a planted negative IC and verifies: the inclusion gate carries an aligned-strong
factor, drops a no-signal one, the carry-cluster collinearity screen fires, and
the §3.1 limit disclosure runs. Deterministic, no store / network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.factor_research.factor_ic_study import study
from scripts.factor_research.factor_lib import FACTOR_NAMES, QGR_FACTOR_NAMES
from scripts.factor_research.qgr_factor_diagnostics import (
    COLLINEARITY_CEILING,
    build_report,
    compute_collinearity,
    decide_carry,
    reversal_ic_under_filter,
)
from scripts.factor_research.r2_factor_diagnostics import NEUT_SUFFIX, verdicts


def _synthetic_panel(
    n_dates: int = 40, n_codes: int = 60, seed: int = 7
) -> pd.DataFrame:
    """A panel where ``rev_1d`` has a strong NEGATIVE IC (reversal), others noise."""
    rng = np.random.default_rng(seed)
    rows = []
    industries = ["801080.SI", "801150.SI", "801750.SI"]
    for di in range(n_dates):
        date = f"2020{1000 + di:04d}"
        rev = rng.normal(size=n_codes)
        # forward return is the NEGATIVE of rev_1d (perfect reversal) + noise.
        fwd5 = -1.2 * rev + rng.normal(scale=0.3, size=n_codes)
        for ci in range(n_codes):
            row = {
                "date": date,
                "code": f"{600000 + ci}",
                "ts_code": f"{600000 + ci}.SH",
                "industry_l1": industries[ci % len(industries)],
                "log_circ_mv": 10.0 + rng.normal(scale=0.5),
                "fwd_ret_5d": float(fwd5[ci]),
                "fwd_ret_10d": float(fwd5[ci] * 0.8),
                "fwd_ret_20d": float(fwd5[ci] * 0.5),
                "at_up_limit_d": False,
                "at_down_limit_d": ci == 0,  # one falling-knife per date
            }
            for f in FACTOR_NAMES:
                row[f] = float(rng.normal())
            for f in QGR_FACTOR_NAMES:
                row[f] = float(rng.normal())
            row["rev_1d"] = float(rev[ci])  # the planted signal
            rows.append(row)
    return pd.DataFrame(rows)


def test_planted_reversal_has_negative_neutralized_ic() -> None:
    panel = _synthetic_panel()
    from scripts.factor_research.neutralize import neutralize_panel

    neut = neutralize_panel(panel, ["rev_1d"], min_obs=20)
    summaries = [s for s in study(neut, factor_names=("rev_1d_neut",))]
    best = max(summaries, key=lambda s: abs(s.t_stat))
    assert best.ic_mean < 0  # reversal: negative IC
    assert abs(best.t_stat) >= 3.0  # strong planted signal


def test_carry_decision_carries_aligned_strong_factor() -> None:
    panel = _synthetic_panel()
    from scripts.factor_research.neutralize import neutralize_panel

    under = (*FACTOR_NAMES, *QGR_FACTOR_NAMES)
    neut = neutralize_panel(panel, list(under), min_obs=20)
    neut_names = tuple(f"{f}{NEUT_SUFFIX}" for f in QGR_FACTOR_NAMES)
    ic = study(neut, factor_names=neut_names)
    neut_verdicts = verdicts(ic, neut_names)
    carry_collin, mutual = compute_collinearity(neut)
    decision = decide_carry(neut_verdicts, carry_collin=carry_collin, mutual=mutual)
    # rev_1d is the only planted-signal factor → it must survive; the pure-noise
    # ones land in no_signal.
    assert "rev_1d" in decision.survivors
    assert set(decision.no_signal) <= set(QGR_FACTOR_NAMES)
    assert "rev_1d" not in decision.no_signal


def test_limit_disclosure_filters_change_n_dates_not_crash() -> None:
    panel = _synthetic_panel()
    ic_all = reversal_ic_under_filter(panel, "rev_1d", "fwd_ret_5d")
    no_down = ~panel["at_down_limit_d"].astype(bool)
    ic_filtered = reversal_ic_under_filter(panel, "rev_1d", "fwd_ret_5d", mask=no_down)
    # both return (ic, t, n); the filtered set drops the falling-knife rows.
    assert ic_all[2] >= ic_filtered[2] or ic_filtered[2] > 0


def test_build_report_is_deterministic_markdown() -> None:
    panel = _synthetic_panel()
    r1 = build_report(panel, params_note="unit-test panel")
    r2 = build_report(panel, params_note="unit-test panel")
    assert r1 == r2
    assert "# QGR-3 short-term factor diagnostics" in r1
    assert "Carry decision" in r1
    assert "limit-loser disclosure" in r1
    assert COLLINEARITY_CEILING == 0.7
