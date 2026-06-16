"""Tests for the Sobol weight search + honest single-strategy selection."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from scripts.factor_research.factor_lib import FACTOR_NAMES
from scripts.factor_research.locked_split import LockedSplit, SacredTestAccessError
from scripts.factor_research.weight_search import (
    TRAIN_VAL_CUTOFF,
    WeightSearchResult,
    search,
    simplex_sobol,
    split_train_val,
)

# A split whose sacred test window is empty — the synthetic dates below are
# never "test", so the search's assert_all_not_test guard passes hermetically.
_OPEN_SPLIT = LockedSplit(train_val_dates=(), embargo_dates=(), test_dates=())


def _predictive_panel(n_codes: int = 30) -> pd.DataFrame:
    """Panel where ep_ttm (attractive-high) is the only real return signal.

    Other factors carry ep_ttm-uncorrelated (scrambled) values, so a weighting
    that loads ep_ttm picks the high-ep_ttm/high-return names; a per-date common
    shock gives each period's basket return genuine cross-period variance (so
    Sharpe is well-defined and rewards the higher-mean ep_ttm baskets).
    """
    train_dates = [f"2021{i:04d}" for i in range(1, 31)]  # < cutoff 20221230
    val_dates = [f"2023{i:04d}" for i in range(1, 31)]  # > cutoff
    rows = []
    for di, date in enumerate([*train_dates, *val_dates]):
        wiggle = 0.01 * ((di % 5) - 2)  # date-level common shock → period variance
        for i in range(n_codes):
            row: dict[str, object] = {"date": date, "code": f"6000{i:02d}"}
            row["ep_ttm"] = float(i)  # attractive-high signal
            for k, f in enumerate(FACTOR_NAMES):
                if f != "ep_ttm":
                    row[f] = float((i * (k + 3)) % n_codes)  # scrambled noise
            ret = i * 0.002 + wiggle
            row["fwd_ret_5d"] = ret
            row["fwd_ret_10d"] = ret
            row["fwd_ret_20d"] = ret
            rows.append(row)
    return pd.DataFrame(rows)


def test_simplex_sobol_is_a_simplex() -> None:
    pts = simplex_sobol(32, len(FACTOR_NAMES), seed=20260616)
    assert len(pts) == 32
    for w in pts:
        assert len(w) == len(FACTOR_NAMES)
        assert all(x >= 0.0 for x in w)
        assert math.isclose(sum(w), 1.0, abs_tol=1e-9)


def test_simplex_sobol_is_deterministic() -> None:
    a = simplex_sobol(16, len(FACTOR_NAMES), seed=20260616)
    b = simplex_sobol(16, len(FACTOR_NAMES), seed=20260616)
    assert a == b
    # A different seed yields a different scramble.
    c = simplex_sobol(16, len(FACTOR_NAMES), seed=1)
    assert c != a


def test_split_train_val_brackets_cutoff_and_purges() -> None:
    panel = _predictive_panel()
    train, val, train_dates, val_dates = split_train_val(panel, purge=4)
    assert train_dates and val_dates
    assert all(d <= TRAIN_VAL_CUTOFF for d in train_dates)
    assert all(d > TRAIN_VAL_CUTOFF for d in val_dates)
    # purge drops the first 4 post-cutoff rebalances.
    dates = panel["date"].astype(str).unique()
    all_val = sorted(d for d in dates if d > TRAIN_VAL_CUTOFF)
    assert val_dates == all_val[4:]
    assert set(train["date"]) == set(train_dates)
    assert set(val["date"]) == set(val_dates)


def test_search_selects_unique_strategy_and_discloses() -> None:
    panel = _predictive_panel()
    res = search(panel, split=_OPEN_SPLIT, n=64, top_n=5)
    assert isinstance(res, WeightSearchResult)
    # weights are a valid simplex over all factors.
    assert set(res.selected_weights) == set(FACTOR_NAMES)
    assert math.isclose(sum(res.selected_weights.values()), 1.0, abs_tol=1e-9)
    # selection found the ep_ttm signal (above the 1/7 simplex average).
    assert res.selected_weights["ep_ttm"] > 1.0 / len(FACTOR_NAMES)
    assert res.val["total_return"] > 0
    # selected val Sharpe is the best among finalists (selection invariant).
    assert res.val["sharpe"] == pytest.approx(
        max(f.val_sharpe for f in res.finalists)
    )
    # disclosure is populated and self-consistent.
    assert res.n_trials == 64
    assert res.disclosure["n_trials"] == 64
    assert 0.0 <= res.disclosure["pbo"] <= 1.0
    assert 0.0 <= res.disclosure["spa_p_value"] <= 1.0
    assert len(res.selected_val_net_returns) == res.disclosure["n_observations"]
    # every non-zero factor carries a registered economic mechanism.
    assert set(res.mechanisms) == {f for f, w in res.selected_weights.items() if w > 0}


def test_search_is_deterministic() -> None:
    panel = _predictive_panel()
    a = search(panel, split=_OPEN_SPLIT, n=64, top_n=5)
    b = search(panel, split=_OPEN_SPLIT, n=64, top_n=5)
    assert a.selected_weights == b.selected_weights
    assert a.disclosure == b.disclosure


def test_search_fails_closed_on_factor_order_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sobol weight vectors map to factors positionally; a reorder of
    # factor_lib.FACTORS must fail closed rather than silently remap weights.
    import scripts.factor_research.weight_search as ws

    monkeypatch.setattr(ws, "FACTOR_NAMES", tuple(reversed(ws.FACTOR_NAMES)))
    with pytest.raises(ValueError, match="factor-order drift"):
        search(_predictive_panel(), split=_OPEN_SPLIT, n=16, top_n=5)


def test_search_guards_against_test_dates() -> None:
    panel = _predictive_panel()
    # A split that declares one of the panel's dates as sacred test → must fail.
    poisoned = LockedSplit(
        train_val_dates=(), embargo_dates=(), test_dates=("20230001",)
    )
    with pytest.raises(SacredTestAccessError):
        search(panel, split=poisoned, n=16, top_n=5)
