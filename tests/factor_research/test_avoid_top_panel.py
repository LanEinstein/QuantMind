"""Tests for the avoid-top trigger table (forward-fill + crowded-set selection)."""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.avoid_top_panel import (
    AVOID_TOP_TOP_Q,
    AvoidTopTriggerTable,
    build_avoid_top_triggers,
)


def _ranker_table() -> pd.DataFrame:
    # Two rebalance dates; crowd_pct decile selects the top 10% as crowded.
    rows = []
    for date in ("20200101", "20200108"):
        for i in range(10):
            rows.append(
                {
                    "date": date,
                    "ts_code": f"00000{i}.SZ",
                    "ranker_score": float(i),
                    "ranker_pct": i / 9.0,
                    "crowd_pct": i / 9.0,  # code 9 = most crowded (pct ~1.0)
                    "log_circ_mv": 10.0,
                }
            )
    return pd.DataFrame(rows)


def test_build_selects_top_decile_crowded() -> None:
    table = build_avoid_top_triggers(_ranker_table(), top_q=0.90)
    assert table.top_q == 0.90
    # crowd_pct >= 0.90 → only code 9 (pct 1.0) per date.
    assert table.crowded_by_date["20200101"] == frozenset({"000009.SZ"})
    assert table.crowded_by_date["20200108"] == frozenset({"000009.SZ"})
    assert table.rebalance_dates == ("20200101", "20200108")
    assert table.total_crowded_flags == 2


def test_default_top_q_is_batch_a_committed_decile() -> None:
    assert AVOID_TOP_TOP_Q == 0.90  # batch-A §3 pre-committed; not re-searched


def test_crowded_asof_forward_fills_and_is_empty_before_first() -> None:
    table = AvoidTopTriggerTable(
        rebalance_dates=("20200101", "20200108"),
        crowded_by_date={
            "20200101": frozenset({"A"}),
            "20200108": frozenset({"B"}),
        },
        top_q=0.90,
    )
    # Before the first rebalance: nothing crowded (no info yet, fail-open).
    assert table.crowded_asof("20191231") == frozenset()
    # On / after the first, before the second: forward-fill the first set.
    assert table.crowded_asof("20200101") == frozenset({"A"})
    assert table.crowded_asof("20200105") == frozenset({"A"})
    # On / after the second: the second set.
    assert table.crowded_asof("20200108") == frozenset({"B"})
    assert table.crowded_asof("20200201") == frozenset({"B"})


def test_crowded_asof_missing_date_key_is_empty() -> None:
    table = AvoidTopTriggerTable(
        rebalance_dates=("20200101",),
        crowded_by_date={},  # date listed but no set → empty (fail-open)
        top_q=0.90,
    )
    assert table.crowded_asof("20200101") == frozenset()
