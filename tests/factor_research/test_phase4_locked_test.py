"""Tests for the Phase 4 locked-test verdict logic (no sacred-data access)."""

from __future__ import annotations

import json

import pytest

from scripts.factor_research.phase4_locked_test import (
    FROZEN_WEIGHTS_3DP,
    evaluate,
    load_frozen_weights,
)
from scripts.factor_research.portfolio_backtest import BacktestResult
from scripts.factor_research.weight_search import EXPECTED_FACTOR_ORDER


def _result(
    *,
    total: float = 0.20,
    sharpe: float = 0.8,
    mdd: float = 0.10,
    excess: float = 0.05,
    dates: tuple[str, ...] = ("20250604", "20250611"),
    net: tuple[float, ...] = (0.1, 0.09),
) -> BacktestResult:
    return BacktestResult(
        n_periods=len(net),
        total_return=total,
        annual_return=total,
        sharpe=sharpe,
        max_drawdown=mdd,
        bench_total_return=total - excess,
        excess_vs_bench=excess,
        avg_turnover=0.4,
        win_rate=1.0,
        equity=(1.0,),
        bench_equity=(1.0,),
        dates=dates,
        net_returns=net,
    )


def test_evaluate_pass_when_all_four_criteria_hold() -> None:
    v = evaluate(_result(total=0.2, sharpe=0.8, mdd=0.10, excess=0.05), {})
    assert v.passed
    assert all(v.criteria.values())


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"total": -0.01}, "net_positive"),
        ({"excess": -0.01}, "beats_csi300"),
        ({"mdd": 0.16}, "drawdown_within_15pct"),
        ({"sharpe": 0.49}, "sharpe_at_least_0.5"),
    ],
)
def test_evaluate_fails_each_criterion(kwargs: dict[str, float], failing: str) -> None:
    v = evaluate(_result(**kwargs), {})
    assert not v.passed
    assert v.criteria[failing] is False


def test_boundary_values_pass() -> None:
    # excess == 0, mdd == 0.15, sharpe == 0.5 are all inclusive PASS.
    v = evaluate(_result(total=0.01, excess=0.0, mdd=0.15, sharpe=0.5), {})
    assert v.passed


def test_per_year_breakdown_compounds_within_year() -> None:
    res = _result(
        dates=("20250604", "20250611", "20260105"),
        net=(0.10, 0.10, 0.05),
    )
    v = evaluate(res, {})
    assert set(v.per_year) == {"2025", "2026"}
    assert v.per_year["2025"]["n_periods"] == 2.0
    assert v.per_year["2025"]["total_return"] == pytest.approx(1.10 * 1.10 - 1.0)
    assert v.per_year["2026"]["total_return"] == pytest.approx(0.05)


def _artifact(weights: dict[str, float], factor_names: list[str]) -> dict[str, object]:
    return {"selected_weights": weights, "factor_names": factor_names}


def test_load_frozen_weights_accepts_committed(tmp_path) -> None:  # noqa: ANN001
    full = {
        "ret_5d": 0.08924612682312727,
        "ret_20d": 0.01823263894766569,
        "vol_20d": 0.1631974307820201,
        "max_20d": 0.16300398018211126,
        "ep_ttm": 0.21083936374634504,
        "turn_20d": 0.1728945318609476,
        "amihud_20d": 0.18258592765778303,
    }
    p = tmp_path / "w.json"
    p.write_text(json.dumps(_artifact(full, list(EXPECTED_FACTOR_ORDER))))
    loaded = load_frozen_weights(str(p))
    assert loaded == full
    for f, v3 in FROZEN_WEIGHTS_3DP.items():
        assert abs(loaded[f] - v3) <= 5e-4


def test_load_frozen_weights_rejects_drift(tmp_path) -> None:  # noqa: ANN001
    drifted = dict.fromkeys(EXPECTED_FACTOR_ORDER, 1.0 / len(EXPECTED_FACTOR_ORDER))
    p = tmp_path / "w.json"
    p.write_text(json.dumps(_artifact(drifted, list(EXPECTED_FACTOR_ORDER))))
    with pytest.raises(ValueError, match="drifted"):
        load_frozen_weights(str(p))


def test_load_frozen_weights_rejects_factor_order(tmp_path) -> None:  # noqa: ANN001
    full = dict(FROZEN_WEIGHTS_3DP)
    p = tmp_path / "w.json"
    p.write_text(json.dumps(_artifact(full, list(reversed(EXPECTED_FACTOR_ORDER)))))
    with pytest.raises(ValueError, match="factor order"):
        load_frozen_weights(str(p))
