"""Tests for the IC study (sign detection + aggregation)."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.factor_ic_study import (
    factor_correlation,
    rank_ic_series,
    study,
    summarize_ic,
)
from scripts.factor_research.factor_lib import FACTOR_NAMES


def _panel() -> pd.DataFrame:
    # 2 dates x 25 codes. fwd_ret_5d = -i (higher i -> lower forward return),
    # ret_5d = i  -> rank IC(ret_5d, fwd_5d) = -1 (reversal, matches prior -1)
    # ep_ttm = -i -> rank IC(ep_ttm, fwd_5d) = +1 (value,    matches prior +1)
    rows = []
    for date in ("20200101", "20200108"):
        for i in range(25):
            row = {"date": date, "code": f"6000{i:02d}"}
            for f in FACTOR_NAMES:
                row[f] = float(i)
            row["ep_ttm"] = float(-i)
            row["fwd_ret_5d"] = float(-i)
            row["fwd_ret_10d"] = float(-i)
            row["fwd_ret_20d"] = float(-i)
            rows.append(row)
    return pd.DataFrame(rows)


def test_rank_ic_detects_reversal_and_value_signs() -> None:
    panel = _panel()
    rev = summarize_ic(
        "ret_5d", "fwd_ret_5d", rank_ic_series(panel, "ret_5d", "fwd_ret_5d")
    )
    val = summarize_ic(
        "ep_ttm", "fwd_ret_5d", rank_ic_series(panel, "ep_ttm", "fwd_ret_5d")
    )
    assert rev.ic_mean == pytest.approx(-1.0)  # reversal
    assert rev.expected_sign == -1  # aligned with prior
    assert val.ic_mean == pytest.approx(1.0)  # value
    assert val.expected_sign == 1
    assert rev.n_dates == 2


def test_summarize_empty_series_is_safe() -> None:
    s = summarize_ic("ret_5d", "fwd_ret_5d", [])
    assert s.n_dates == 0
    assert s.ic_mean == 0.0
    assert s.icir == 0.0


def test_study_covers_all_factor_horizon_pairs() -> None:
    panel = _panel()
    summaries = study(panel)
    assert len(summaries) == len(FACTOR_NAMES) * 3  # 7 factors x 3 horizons


def test_factor_correlation_shape() -> None:
    panel = _panel()
    corr = factor_correlation(panel)
    assert set(corr.columns) == set(FACTOR_NAMES)
    # ret_5d and (say) vol_20d both == i -> perfectly rank-correlated here
    assert corr.loc["ret_5d", "vol_20d"] == pytest.approx(1.0)
