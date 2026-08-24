"""Unit tests for the SLV-2 science-gate building blocks (no PIT store, no IO)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from scripts.factor_research.defensive_d1_ablation import DefensiveArm
from scripts.factor_research.defensive_sleeve2_science_gate import (
    _BUF,
    _EQ,
    _RAND,
    _S1,
    _SM,
    _overlap_report,
    _read,
    _sector_concentration,
    build_sleeve2_ranker_table,
    held_by_day,
    randomized_score_table,
    selected_books_by_date,
    sizematched_score_table,
    slv1_books_by_date,
    validate_rebalance_coverage,
)


def _panel() -> pd.DataFrame:
    """One date, 20 names, hand-computable gate outcome.

    Gates: lottery q0.9 drops A19/A20; gpm bottom decile drops A01/A02;
    dv_ratio < median (10.5) drops A01..A10 → survivors A11..A18 (8 names).
    SLV-1 book (dv desc top-5) = A18..A14 → SLV-2 candidates A11/A12/A13.
    """
    rows = []
    for i in range(1, 21):
        code = f"A{i:02d}.SZ"
        rows.append(
            {
                "date": "20200101",
                "ts_code": code,
                "max_20d": 1.0 if i == 20 else 0.001 * i,
                "roe": 0.1,
                "gpm": 0.03 * i,
                "dv_ratio": float(i),
                "log_circ_mv": 10.0 + 0.01 * i,
                "industry_l1": "食品饮料" if i % 2 else "医药",
            }
        )
    return pd.DataFrame(rows)


def test_slv1_books_are_dv_ratio_top5() -> None:
    books = slv1_books_by_date(_panel())
    assert books["20200101"] == (
        "A18.SZ",
        "A17.SZ",
        "A16.SZ",
        "A15.SZ",
        "A14.SZ",
    )


def test_sleeve2_table_excludes_slv1_book_and_ranks_by_gpm() -> None:
    table = build_sleeve2_ranker_table(_panel())
    codes = set(table["ts_code"])
    assert codes == {"A11.SZ", "A12.SZ", "A13.SZ"}
    books = selected_books_by_date(table)
    assert books["20200101"] == ("A13.SZ", "A12.SZ", "A11.SZ")  # gpm desc


def test_sleeve2_table_drops_nonfinite_gpm() -> None:
    panel = _panel()
    panel.loc[panel["ts_code"] == "A12.SZ", "gpm"] = np.inf
    table = build_sleeve2_ranker_table(panel)
    assert "A12.SZ" not in set(table["ts_code"])


def test_sleeve2_table_missing_column_fails_closed() -> None:
    with pytest.raises(KeyError, match="missing columns"):
        build_sleeve2_ranker_table(_panel().drop(columns=["gpm"]))


def test_overlap_report_asserts_disjoint() -> None:
    ok = _overlap_report({"d": ("A", "B")}, {"d": ("C", "D")})
    assert ok == {"dates_checked": 1, "target_book_overlap_total": 0}
    with pytest.raises(AssertionError, match="overlap"):
        _overlap_report({"d": ("A", "B")}, {"d": ("B", "C")})


def test_sector_concentration_disclosure() -> None:
    out = _sector_concentration(_panel(), {"20200101": ("A11.SZ", "A12.SZ", "A13.SZ")})
    assert out["available"] is True
    assert out["slot_total"] == 3
    assert out["top1_share"] == pytest.approx(2 / 3)


def test_validate_coverage_allows_leading_zero_dates_only() -> None:
    table = build_sleeve2_ranker_table(_panel())  # only date 20200101, 3 candidates
    # breadth 3 satisfies min_breadth=3; a leading zero date is skippable
    leading = validate_rebalance_coverage(
        ["20191202", "20200101"], table, min_breadth=3
    )
    assert leading == ["20191202"]
    # thin (nonzero but < breadth) FIRST date is NOT skippable
    with pytest.raises(ValueError, match="thinness"):
        validate_rebalance_coverage(["20200101"], table, min_breadth=4)
    # a gap AFTER evaluation started aborts
    with pytest.raises(ValueError, match="AFTER evaluation started"):
        validate_rebalance_coverage(
            ["20200101", "20200201"], table, min_breadth=3
        )
    # cap on leading skips
    many = [f"2019{m:02d}01" for m in range(1, 8)] + ["20200101"]
    with pytest.raises(ValueError, match="cap"):
        validate_rebalance_coverage(many, table, min_breadth=3)


def test_randomized_score_table_is_deterministic_full_pool() -> None:
    table = build_sleeve2_ranker_table(_panel())
    r1 = randomized_score_table(table, seed=42)
    r2 = randomized_score_table(table, seed=42)
    assert list(r1["ranker_score"]) == list(r2["ranker_score"])
    assert set(r1["ts_code"]) == set(table["ts_code"])  # FULL candidate pool
    assert list(randomized_score_table(table, seed=7)["ranker_score"]) != list(
        r1["ranker_score"]
    )
    assert r1["ranker_pct"].between(0, 1).all()


def test_sizematched_table_scores_matched_names_high() -> None:
    table = build_sleeve2_ranker_table(_panel())
    sm = sizematched_score_table(table, top_n=1)
    assert set(sm["ts_code"]) == set(table["ts_code"])
    assert set(sm["ranker_score"]) <= {0.0, 1.0}
    assert (sm["ranker_score"] == 1.0).sum() == 1  # one matched name per date


@dataclass(frozen=True)
class _Fill:
    trade_date: str
    code: str
    side_is_buy: bool
    volume: int


def test_held_by_day_reconstructs_positions() -> None:
    fills = [
        _Fill("d1", "A", True, 100),
        _Fill("d1", "B", True, 100),
        _Fill("d3", "A", False, 100),
    ]
    held = held_by_day(fills, ["d1", "d2", "d3", "d4"])
    assert held["d1"] == {"A", "B"}
    assert held["d2"] == {"A", "B"}  # carried between fill days
    assert held["d3"] == {"B"}
    assert held["d4"] == {"B"}


def _arm(label: str, returns: list[float], *, pnl: float, mdd: float) -> DefensiveArm:
    return DefensiveArm(
        label=label,
        slots=5,
        cap_percent=8,
        net_pnl_yuan=pnl,
        max_drawdown_pct=mdd,
        monthly_turnover=0.01,
        fill_count=5,
        avg_exposure=0.4,
        conservation_ok=True,
        dsr=0.1,
        period_returns=returns,
    )


def _read_fixture(*, pnl: float, mdd: float, bear_sum: float) -> dict[str, object]:
    strong = [0.05, 0.04, 0.06, 0.05, 0.04, 0.06, 0.05, 0.04]
    weak = [0.01, 0.0, 0.01, 0.0, 0.01, 0.0, 0.01, 0.0]
    arms = {
        _BUF: _arm(_BUF, strong, pnl=pnl, mdd=mdd),
        _EQ: _arm(_EQ, strong, pnl=pnl * 2, mdd=mdd * 1.7),
        _RAND: _arm(_RAND, weak, pnl=1.0, mdd=0.25),
        _SM: _arm(_SM, weak, pnl=2.0, mdd=0.22),
        _S1: _arm(_S1, strong, pnl=pnl, mdd=mdd),
    }
    regime_tbl = {_BUF: {"bear": {"n": 3.0, "sum_return": bear_sum}}}
    crash_tbl = {_BUF: {"2018": {"n": 2.0, "cum_return": -0.05}}}
    return _read(arms, regime_tbl, crash_tbl)


def test_read_all_four_criteria_pass() -> None:
    read = _read_fixture(pnl=100_000.0, mdd=0.15, bear_sum=0.1)
    assert read["criterion_1_net_pnl_positive"] is True
    assert read["criterion_2_bear_cum_nonneg"] is True
    assert read["criterion_3_mdd_within_bound"] is True
    assert read["criterion_4_beats_random"] is True  # strong vs weak paired-t
    assert read["science_gate_pass"] is True
    assert -1.0 <= read["sleeve1_period_return_correlation"] <= 1.0


def test_read_single_criterion_failure_fails_gate() -> None:
    for kwargs in (
        {"pnl": -1.0, "mdd": 0.15, "bear_sum": 0.1},  # criterion 1
        {"pnl": 100_000.0, "mdd": 0.15, "bear_sum": -0.02},  # criterion 2
        {"pnl": 100_000.0, "mdd": 0.21, "bear_sum": 0.1},  # criterion 3 (hard MDD)
    ):
        assert _read_fixture(**kwargs)["science_gate_pass"] is False
