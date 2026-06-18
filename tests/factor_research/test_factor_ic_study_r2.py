"""Tests for the round-2 IC study extension (R2-2 / S5)."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.factor_ic_study import (
    factor_correlation,
    rank_ic_series,
    study,
    summarize_ic,
)
from scripts.factor_research.factor_lib import FACTOR_NAMES, R2_FACTOR_NAMES


def _panel() -> pd.DataFrame:
    rows = []
    for date in ("20200101", "20200108"):
        for i in range(25):
            row: dict[str, object] = {"date": date, "code": f"6000{i:02d}"}
            for f in (*FACTOR_NAMES, *R2_FACTOR_NAMES):
                row[f] = float(i)
            # a neutralized variant column for a round-2 factor
            row["roe_neut"] = float(-i)
            row["fwd_ret_5d"] = float(i)
            row["fwd_ret_10d"] = float(i)
            row["fwd_ret_20d"] = float(i)
            rows.append(row)
    return pd.DataFrame(rows)


def test_summarize_ic_neut_inherits_raw_expected_sign() -> None:
    panel = _panel()
    ics = rank_ic_series(panel, "roe_neut", "fwd_ret_5d")
    s = summarize_ic("roe_neut", "fwd_ret_5d", ics)
    assert s.expected_sign == 1  # roe prior is +1; the _neut variant inherits it
    assert s.ic_mean == pytest.approx(-1.0)  # roe_neut = -i vs fwd = +i


def test_summarize_ic_unregistered_factor_zero_prior() -> None:
    panel = _panel()
    panel["mystery"] = panel["roe"]
    ics = rank_ic_series(panel, "mystery", "fwd_ret_5d")
    s = summarize_ic("mystery", "fwd_ret_5d", ics)
    assert s.expected_sign == 0


def test_study_over_round2_factor_list() -> None:
    panel = _panel()
    names = (*FACTOR_NAMES, *R2_FACTOR_NAMES, "roe_neut")
    summaries = study(panel, factor_names=names)
    assert len(summaries) == len(names) * 3  # each factor × 3 horizons
    by_key = {(s.factor, s.horizon): s for s in summaries}
    assert ("mom_12_1", "fwd_ret_5d") in by_key
    assert ("roe_neut", "fwd_ret_5d") in by_key


def test_factor_correlation_accepts_custom_names() -> None:
    panel = _panel()
    names = (*FACTOR_NAMES, *R2_FACTOR_NAMES)
    corr = factor_correlation(panel, factor_names=names)
    assert set(corr.columns) == set(names)


def test_round1_defaults_unchanged() -> None:
    # Backward-compat: no-arg study/correlation still cover only the round-1 set.
    panel = _panel()
    assert len(study(panel)) == len(FACTOR_NAMES) * 3
    assert set(factor_correlation(panel).columns) == set(FACTOR_NAMES)
