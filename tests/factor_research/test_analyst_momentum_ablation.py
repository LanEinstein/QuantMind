"""Unit tests for the analyst-momentum ablation's pure helpers.

Pins the committed equal-weight signed-z ranker (dropna all 3 analyst factors → covered
cross-section only), and the diagnostic read (selection gate + owner gates + the n>0
UNTESTED guard on empty crash/bear buckets). The heavy event-loop run is exercised by
``main --smoke-periods`` + the full build.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.factor_research.analyst_momentum_ablation import (
    _read,
    build_analyst_ranker_table,
)
from scripts.factor_research.defensive_d1_ablation import DefensiveArm

_NEUT = ("np_rev_neut", "rev_diff_neut", "cover_chg_neut")


def _neut_frame() -> pd.DataFrame:
    n = 6
    return pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"c{i}.SH" for i in range(n)],
            "log_circ_mv": [10.0 + i for i in range(n)],
            "np_rev_neut": [float(i) for i in range(n)],  # ascending
            "rev_diff_neut": [float(i) for i in range(n)],
            "cover_chg_neut": [float(i) for i in range(n)],
        }
    )


def test_ranker_equal_weight_signed_zscore() -> None:
    table = build_analyst_ranker_table(_neut_frame())
    assert list(table.columns) == [
        "date",
        "ts_code",
        "ranker_score",
        "ranker_pct",
        "log_circ_mv",
    ]
    # All three factors +1 and perfectly aligned → the highest-index name ranks top.
    top = table.sort_values("ranker_score", ascending=False).iloc[0]["ts_code"]
    assert top == "c5.SH"
    # ranker_pct is a within-date percentile in [0, 1].
    assert table["ranker_pct"].min() >= 0.0
    assert table["ranker_pct"].max() <= 1.0


def test_ranker_drops_names_missing_any_analyst_factor() -> None:
    frame = _neut_frame()
    frame.loc[0, "np_rev_neut"] = np.nan  # c0 missing one factor → dropped
    frame.loc[1, "cover_chg_neut"] = np.nan  # c1 missing one factor → dropped
    table = build_analyst_ranker_table(frame)
    covered = set(table["ts_code"])
    assert "c0.SH" not in covered
    assert "c1.SH" not in covered
    assert covered == {"c2.SH", "c3.SH", "c4.SH", "c5.SH"}


def test_ranker_empty_when_no_covered_names() -> None:
    frame = _neut_frame()
    frame["cover_chg_neut"] = np.nan  # nobody has all 3 → empty
    table = build_analyst_ranker_table(frame)
    assert table.empty


# --------------------------------------------------------------------------- #
# Diagnostic read: selection gate + owner gates + UNTESTED guard.             #
# --------------------------------------------------------------------------- #


def _arm(label: str, net: float, returns: tuple[float, ...]) -> DefensiveArm:
    return DefensiveArm(
        label=label,
        slots=5,
        cap_percent=100,
        net_pnl_yuan=net,
        max_drawdown_pct=0.2,
        monthly_turnover=1.0,
        fill_count=10,
        avg_exposure=0.7,
        conservation_ok=True,
        dsr=0.01,
        period_returns=returns,
    )


def _regime(labels: list[str], bear: float, *, n: int = 4):  # noqa: ANN202
    return {lb: {"bear": {"n": float(n), "sum_return": bear}} for lb in labels}


def _crash(labels: list[str], cum: float, *, n: int = 4):  # noqa: ANN202
    return {lb: {"slice1": {"n": float(n), "cum_return": cum}} for lb in labels}


def _arms(*, edge: bool, net: float) -> dict[str, DefensiveArm]:
    strong = (0.02, 0.021, 0.019, 0.022, 0.018, 0.02, 0.021, 0.019)
    flat = (0.0,) * 8
    am_ret = strong if edge else flat
    arms: dict[str, DefensiveArm] = {}
    for c in ("eq_5", "buf40_5"):
        arms[f"am_{c}"] = _arm(f"am_{c}", net, am_ret)
        arms[f"placebo_random_{c}"] = _arm(f"placebo_random_{c}", 0.0, flat)
        arms[f"placebo_sizematched_{c}"] = _arm(f"placebo_sizematched_{c}", 0.0, flat)
    return arms


def test_candidate_edge_true_when_beats_random_and_owner_gates_pass() -> None:
    arms = _arms(edge=True, net=100.0)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.05), _crash(labels, 0.1))
    assert read["beats_own_random_joint"] is True
    assert read["owner_gates_pass"] is True
    assert read["candidate_edge"] is True


def test_candidate_edge_false_when_loses_to_random() -> None:
    arms = _arms(edge=False, net=100.0)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.05), _crash(labels, 0.1))
    assert read["beats_own_random_joint"] is False
    assert read["candidate_edge"] is False


def test_empty_bucket_is_untested_not_a_pass() -> None:
    arms = _arms(edge=True, net=100.0)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.0, n=0), _crash(labels, 0.0, n=0))
    eq = read["containers"]["eq_5"]  # type: ignore[index]
    assert eq["bear_regime_nonneg"] is False  # n=0 → untested
    assert eq["all_crash_slices_nonneg"] is False
    assert read["owner_gates_pass"] is False
    assert read["candidate_edge"] is False
