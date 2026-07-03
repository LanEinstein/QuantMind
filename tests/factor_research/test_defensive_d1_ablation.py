"""Unit tests for the D1 ablation's pure helpers (size-matched placebo + paired-t).

The heavy event-loop run is exercised by the ``main --smoke-periods`` smoke; here we
pin the size-matched placebo draw (a size control that must NOT reuse the D1 top-N)
and the paired-t used in the criterion read.
"""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.defensive_d1_ablation import (
    _paired_t,
    size_matched_scores,
)


def _ranker_table() -> pd.DataFrame:
    # 10 names, distinct scores (top-5 = c9..c5) and distinct sizes.
    return pd.DataFrame(
        {
            "date": ["20180102"] * 10,
            "ts_code": [f"c{i}.SH" for i in range(10)],
            "ranker_score": [float(i) for i in range(10)],
            "ranker_pct": [i / 9 for i in range(10)],
            "log_circ_mv": [10.0 + i for i in range(10)],
        }
    )


def test_size_matched_scores_picks_non_selected_names() -> None:
    scores = size_matched_scores(_ranker_table(), top_n=5)
    picks = {code for code, _ in scores["20180102"]}
    top5 = {f"c{i}.SH" for i in range(5, 10)}  # highest ranker_score
    assert len(picks) == 5
    assert picks.isdisjoint(top5)  # placebo never reuses the D1 selection
    assert picks <= {f"c{i}.SH" for i in range(5)}  # drawn from the non-selected pool


def test_size_matched_scores_deterministic() -> None:
    table = _ranker_table()
    assert size_matched_scores(table, top_n=5) == size_matched_scores(table, top_n=5)


def test_paired_t_zero_mean_difference() -> None:
    a = (0.1, 0.2, 0.3)
    mean_diff, t = _paired_t(a, a)
    assert mean_diff == 0.0
    assert t == 0.0


def test_paired_t_positive_edge() -> None:
    a = (0.2, 0.3, 0.4, 0.5)
    b = (0.1, 0.1, 0.1, 0.1)
    mean_diff, t = _paired_t(a, b)
    assert mean_diff > 0.0
    assert t > 0.0


def test_paired_t_too_short() -> None:
    assert _paired_t((0.1,), (0.2,)) == (0.0, 0.0)
