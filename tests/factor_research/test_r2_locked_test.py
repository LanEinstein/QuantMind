"""Tests for the R2-6 one-shot locked-test verdict logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.factor_research.benchmark_relative import BenchmarkRelativeResult
from scripts.factor_research.r2_locked_test import (
    FROZEN_R2_CONSTRAINT,
    evaluate,
    load_frozen_strategy,
)


def _result(excess: list[float], dates: list[str]) -> BenchmarkRelativeResult:
    return BenchmarkRelativeResult(
        n_periods=len(excess),
        total_excess=0.0,
        annual_excess=0.0,
        tracking_error=0.05,
        information_ratio=0.3,
        avg_turnover=0.2,
        avg_gross_active=0.4,
        avg_forced_underweight=0.16,
        mean_net_active=0.0,
        mean_size_active=-0.15,
        mean_max_industry_active=0.04,
        excess_returns=tuple(excess),
        dates=tuple(dates),
    )


def _evaluate(excess: list[float], index: dict[str, float]):  # noqa: ANN202
    res = _result(excess, list(index))
    return evaluate(
        res,
        index,
        constraint="constituent_only",
        k=0.1,
        a_max=0.02,
        nonconst_cap=0.10,
        weights={"ep_ttm": 1.0},
    )


def test_portfolio_net_is_excess_plus_benchmark() -> None:
    # excess = port − bench − cost; so port_net = excess + bench.
    v = _evaluate([0.03, 0.01], {"20250604": 0.0, "20250611": 0.0})
    # net_total = (1.03)(1.01) − 1
    assert v.net_total_return == pytest.approx(1.03 * 1.01 - 1.0)
    assert v.bench_total_return == pytest.approx(0.0)


def test_all_four_gates_pass() -> None:
    v = _evaluate([0.03, 0.01], {"20250604": 0.0, "20250611": 0.0})
    assert v.criteria["net_positive"] is True
    assert v.criteria["beats_csi300"] is True
    assert v.criteria["drawdown_within_15pct"] is True
    assert v.criteria["sharpe_at_least_0.5"] is True
    assert v.passed is True


def test_positive_net_but_loses_to_strong_index_fails_beats_gate() -> None:
    # The round-1 failure mode: positive net return but the index ran harder →
    # cumulative excess negative → beats_csi300 FAILS even though net > 0.
    v = _evaluate([-0.05, -0.05], {"20250604": 0.10, "20250611": 0.10})
    assert v.net_total_return > 0.0  # net positive (port_net = +0.05 each)
    assert v.criteria["net_positive"] is True
    assert v.excess_vs_bench < 0.0
    assert v.criteria["beats_csi300"] is False
    assert v.passed is False


def test_load_frozen_refuses_until_constants_filled(tmp_path: Path) -> None:
    # Until R2-5 fills the freeze constants, the firewall fails closed (must never
    # burn the sacred window against an unfilled pre-commitment).
    assert FROZEN_R2_CONSTRAINT == "PLACEHOLDER_FILLED_IN_R2_5"
    p = tmp_path / "search.json"
    p.write_text(json.dumps({"selected_constraint": "x", "selected_weights": {}}))
    with pytest.raises(ValueError, match="not filled"):
        load_frozen_strategy(str(p))


def _fill_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the R2-5 freeze so the firewall logic can be exercised filled."""
    import scripts.factor_research.r2_locked_test as m

    monkeypatch.setattr(m, "FROZEN_R2_CONSTRAINT", "constituent_only")
    monkeypatch.setattr(m, "FROZEN_R2_K", 0.1)
    monkeypatch.setattr(m, "FROZEN_R2_A_MAX", 0.02)
    monkeypatch.setattr(m, "FROZEN_R2_NONCONST_CAP", 0.10)
    monkeypatch.setattr(m, "FROZEN_R2_WEIGHTS_3DP", {"ep_ttm": 0.6, "roe": 0.4})


def _artifact(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "selected_constraint": "constituent_only",
        "selected_k": 0.1,
        "selected_a_max": 0.02,
        "selected_nonconst_cap": 0.10,
        "selected_weights": {"ep_ttm": 0.6, "roe": 0.4},
    }
    base.update(over)
    return base


def test_load_frozen_accepts_matching_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fill_freeze(monkeypatch)
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(_artifact()))
    constraint, k, a_max, cap, weights = load_frozen_strategy(str(p))
    assert constraint == "constituent_only"
    assert weights == {"ep_ttm": 0.6, "roe": 0.4}


def test_load_frozen_fails_closed_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fill_freeze(monkeypatch)
    cases = [
        ({"selected_constraint": "unconstrained"}, "constraint"),
        ({"selected_k": 0.2}, "selected_k"),
        ({"selected_nonconst_cap": 0.20}, "selected_nonconst_cap"),
        # an EXTRA factor must not slip through (weight-set pinning)
        ({"selected_weights": {"ep_ttm": 0.6, "roe": 0.4, "vol_20d": 0.0}}, "set"),
        # a drifted weight value
        ({"selected_weights": {"ep_ttm": 0.7, "roe": 0.4}}, "drifted"),
    ]
    for over, match in cases:
        p = tmp_path / "drift.json"
        p.write_text(json.dumps(_artifact(**over)))
        with pytest.raises(ValueError, match=match):
            load_frozen_strategy(str(p))
