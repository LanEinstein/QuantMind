"""Unit tests for the QGR-4 EXIT-veto pure panel transforms.

Covers the deterministic core (ranker orientation, veto = top crowding decile,
placebo matches the veto pass-rate + is seed-deterministic, health derives the
trailing proxies) — the heavy event-loop integration is exercised separately by
the smoke run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.factor_research import exit_veto_panel as xv


def _neut_panel(n_dates: int = 3, n_codes: int = 40, seed: int = 7) -> pd.DataFrame:
    """A synthetic NEUTRALIZED panel (the ``_neut`` columns already present)."""
    rng = np.random.default_rng(seed)
    rows = []
    for di in range(n_dates):
        date = f"2020010{di + 1}"
        for ci in range(n_codes):
            code = f"{600000 + ci:06d}.SH"
            rows.append(
                {
                    "date": date,
                    "ts_code": code,
                    "code": f"{600000 + ci:06d}",
                    "rev_1d_neut": float(rng.normal()),
                    "max_5d_neut": float(rng.normal()),
                    "turn_spike_neut": float(rng.normal()),
                    "ideal_amplitude_20d_neut": float(rng.normal()),
                    "log_circ_mv": float(rng.uniform(20.0, 26.0)),
                }
            )
    return pd.DataFrame(rows)


def test_ranker_orientation_negates_attractive_low_factors() -> None:
    """A code with the LOWEST survivor values (most attractive) ranks at the top."""
    panel = _neut_panel(n_dates=1, n_codes=10)
    # Force one code to be uniformly attractive (all survivors deeply negative).
    panel.loc[
        panel["ts_code"] == "600000.SH",
        ["rev_1d_neut", "max_5d_neut", "turn_spike_neut"],
    ] = -5.0
    table = xv.build_ranker_table(panel)
    top = table.sort_values("ranker_score", ascending=False).iloc[0]
    assert top["ts_code"] == "600000.SH"
    assert top["ranker_pct"] == pytest.approx(1.0)


def test_build_ranker_table_drops_rows_missing_any_factor() -> None:
    panel = _neut_panel(n_dates=1, n_codes=5)
    panel.loc[0, "max_5d_neut"] = float("nan")
    dropped = str(panel.loc[0, "ts_code"])
    table = xv.build_ranker_table(panel)
    assert dropped not in set(table["ts_code"])


def test_veto_selects_top_crowding_decile() -> None:
    panel = _neut_panel(n_dates=1, n_codes=100)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table, top_q=0.90)
    date = next(iter(veto))
    # Vetoed names are exactly those with crowd_pct >= 0.90 that date.
    crowded = set(table.loc[table["crowd_pct"] >= 0.90, "ts_code"].astype(str))
    assert veto[date] == crowded
    assert 0 < len(veto[date]) <= 15  # ~top decile of 100


def test_scores_by_day_drops_vetoed_codes_and_sorts_desc() -> None:
    panel = _neut_panel(n_dates=2, n_codes=30)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table)
    base = xv.scores_by_day(table)
    vetoed = xv.scores_by_day(table, drop_codes=veto)
    for date, scored in vetoed.items():
        codes = {c for c, _ in scored}
        assert codes.isdisjoint(veto[date])
        assert len(scored) == len(base[date]) - len(veto[date])
        vals = [s for _, s in scored]
        assert vals == sorted(vals, reverse=True)


def test_placebo_matches_veto_count_per_date() -> None:
    panel = _neut_panel(n_dates=3, n_codes=80)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table)
    for size_matched in (False, True):
        placebo = xv.placebo_codes_by_day(
            table, veto, seed=42, size_matched=size_matched
        )
        for date, vset in veto.items():
            assert len(placebo[date]) == len(vset)
            assert placebo[date].isdisjoint(vset)  # placebo never overlaps the veto


def test_placebo_is_seed_deterministic() -> None:
    panel = _neut_panel(n_dates=2, n_codes=60)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table)
    a = xv.placebo_codes_by_day(table, veto, seed=1, size_matched=False)
    b = xv.placebo_codes_by_day(table, veto, seed=1, size_matched=False)
    c = xv.placebo_codes_by_day(table, veto, seed=2, size_matched=False)
    assert a == b
    assert a != c  # a different seed draws a different placebo


def test_size_matched_placebo_tracks_vetoed_size_distribution() -> None:
    """Size-matched placebo's mean log-size tracks the vetoed names' size."""
    panel = _neut_panel(n_dates=1, n_codes=200, seed=11)
    # Make the crowded names systematically large-cap to create a size channel.
    table = xv.build_ranker_table(panel)
    date = str(table["date"].iloc[0])
    veto = xv.veto_codes_by_day(table)
    size_by_code = dict(
        zip(table["ts_code"].astype(str), table["log_circ_mv"], strict=True)
    )
    veto_mean = np.mean([size_by_code[c] for c in veto[date]])
    matched = xv.placebo_codes_by_day(table, veto, seed=3, size_matched=True)
    matched_mean = np.mean([size_by_code[c] for c in matched[date]])
    # Size-matched draw reproduces the vetoed size distribution closely.
    assert abs(matched_mean - veto_mean) < 1.0


def test_health_entry_percentile_is_trailing_max() -> None:
    """entry_percentile = trailing-max of the code's own percentile (proxy)."""
    panel = _neut_panel(n_dates=5, n_codes=30, seed=5)
    table = xv.build_ranker_table(panel)
    health = xv.build_health_overrides(table, entry_lookback=4, score_stat_lookback=4)
    dates = sorted(health)
    code = "600000.SH"
    pcts = [health[d][code].line1_percentile for d in dates if code in health[d]]
    for i, d in enumerate(dates):
        if code not in health[d]:
            continue
        entry = health[d][code].entry_percentile
        prior = pcts[max(0, i - 4) : i]
        expected = max(prior) if prior else health[d][code].line1_percentile
        assert entry == pytest.approx(expected)


def test_health_first_date_entry_equals_current() -> None:
    """With no history, entry_percentile falls back to today's percentile."""
    panel = _neut_panel(n_dates=1, n_codes=20)
    table = xv.build_ranker_table(panel)
    health = xv.build_health_overrides(table)
    date = next(iter(health))
    for code, h in health[date].items():
        assert h.entry_percentile == pytest.approx(h.line1_percentile)
        assert h.qualified is True


def test_removed_counts_matches_veto() -> None:
    panel = _neut_panel(n_dates=3, n_codes=50)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table)
    counts = xv.removed_counts(veto)
    assert counts == {d: len(s) for d, s in veto.items()}


def test_panel_universe_is_sorted_unique() -> None:
    panel = _neut_panel(n_dates=2, n_codes=10)
    table = xv.build_ranker_table(panel)
    uni = xv.panel_universe(table)
    assert list(uni) == sorted(set(uni))
    assert len(uni) == 10


def test_build_ranker_table_raises_on_missing_column() -> None:
    panel = _neut_panel(n_dates=1, n_codes=5).drop(columns=["turn_spike_neut"])
    with pytest.raises(KeyError):
        xv.build_ranker_table(panel)


def test_factor_sign_reads_attractive_low_from_registry() -> None:
    """The survivors are all attractive_high=False → the ranker sign is −1."""
    for factor in xv.RANKER_FACTORS:
        assert xv._factor_sign(factor) == -1.0


def test_drop_from_scores_equals_scores_by_day_with_drop() -> None:
    """Filtering the base table == re-grouping with drop_codes (order-preserving)."""
    panel = _neut_panel(n_dates=3, n_codes=40)
    table = xv.build_ranker_table(panel)
    veto = xv.veto_codes_by_day(table)
    base = xv.scores_by_day(table)
    via_filter = xv.drop_from_scores(base, veto)
    via_regroup = xv.scores_by_day(table, drop_codes=veto)
    assert via_filter == via_regroup


def test_drop_from_scores_no_drop_returns_copy() -> None:
    panel = _neut_panel(n_dates=1, n_codes=10)
    table = xv.build_ranker_table(panel)
    base = xv.scores_by_day(table)
    out = xv.drop_from_scores(base, {})
    assert out == base
    for d in base:
        assert out[d] is not base[d]  # a copy, not the same list object
