"""Unit tests for the sleeve confirmatory science-gate ablation's pure helpers.

Pins the simplest-rule ranker (D1 defensive-universe exclusion gates → dv_ratio top-5)
and the risk-property science-gate read. The heavy event-loop run is exercised by
``main --smoke-periods`` + the full build.
"""

from __future__ import annotations

import pandas as pd

from scripts.factor_research.defensive_d1_ablation import DefensiveArm
from scripts.factor_research.defensive_sleeve_science_gate import (
    _read,
    build_sleeve_ranker_table,
)


def _panel() -> pd.DataFrame:
    # 10 names, one date. D1 gates: lottery drops top max_20d decile, gpm drops bottom
    # decile, dividend keeps dv_ratio ≥ median. max_20d/gpm are anti-correlated with
    # dv_ratio so only c0 is gate-dropped there; the median dividend gate keeps c5..c9.
    n = 10
    return pd.DataFrame(
        {
            "date": ["20180102"] * n,
            "ts_code": [f"c{i}.SH" for i in range(n)],
            "log_circ_mv": [10.0 + i for i in range(n)],
            "max_20d": [0.09 - 0.01 * i for i in range(n)],  # c0 top → lottery drops c0
            "roe": [5.0] * n,  # all > 0 (roe floor keeps all)
            "gpm": [30.0 + i for i in range(n)],  # c0 lowest → gpm decile drops c0
            "dv_ratio": [float(i) for i in range(n)],  # 0..9 (median 4.5 → keep c5..c9)
        }
    )


def test_ranker_dv_ratio_top_within_defensive_universe() -> None:
    table = build_sleeve_ranker_table(_panel())
    assert list(table.columns) == [
        "date",
        "ts_code",
        "ranker_score",
        "ranker_pct",
        "log_circ_mv",
    ]
    # The dividend gate keeps dv_ratio ≥ median (c5..c9); dv_ratio is the score, so the
    # highest-dividend name ranks top.
    kept = set(table["ts_code"])
    assert kept == {"c5.SH", "c6.SH", "c7.SH", "c8.SH", "c9.SH"}
    top = table.sort_values("ranker_score", ascending=False).iloc[0]["ts_code"]
    assert top == "c9.SH"


def test_ranker_empty_when_universe_empty() -> None:
    panel = _panel()
    panel["roe"] = -1.0  # roe floor drops everyone (roe ≤ 0)
    table = build_sleeve_ranker_table(panel)
    assert table.empty


# --------------------------------------------------------------------------- #
# Risk-property science-gate read.                                            #
# --------------------------------------------------------------------------- #


def _arm(
    label: str, net: float, mdd: float, returns: tuple[float, ...]
) -> DefensiveArm:
    return DefensiveArm(
        label=label,
        slots=5,
        cap_percent=8,
        net_pnl_yuan=net,
        max_drawdown_pct=mdd,
        monthly_turnover=0.1,
        fill_count=20,
        avg_exposure=0.4,
        conservation_ok=True,
        dsr=0.01,
        period_returns=returns,
    )


def _regime(labels: list[str], bear: float, *, n: int = 4):  # noqa: ANN202
    return {lb: {"bear": {"n": float(n), "sum_return": bear}} for lb in labels}


def _crash(labels: list[str], cum: float, *, n: int = 4):  # noqa: ANN202
    return {lb: {"slice1": {"n": float(n), "cum_return": cum}} for lb in labels}


def _arms(*, net: float, mdd: float, bear: float) -> dict[str, DefensiveArm]:
    ret = (0.01, 0.0, -0.01, 0.02, 0.0, 0.01, 0.0, -0.005)
    return {
        "sleeve_buf40_5": _arm("sleeve_buf40_5", net, mdd, ret),
        "sleeve_eq_5": _arm("sleeve_eq_5", net * 2, mdd + 0.15, ret),
        "placebo_random_buf40_5": _arm("placebo_random_buf40_5", net, mdd, ret),
        "placebo_sizematched_buf40_5": _arm(
            "placebo_sizematched_buf40_5", net, mdd, ret
        ),
    }


def test_science_gate_pass_when_net_positive_and_bear_nonneg() -> None:
    arms = _arms(net=100.0, mdd=0.15, bear=0.05)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.05), _crash(labels, 0.1))
    assert read["net_pnl_positive"] is True
    assert read["bear_regime_nonneg"] is True
    assert read["science_gate_pass"] is True
    assert read["mdd_within_bound"] is True  # 0.15 <= 0.20 bound
    assert read["buffer_mdd_reduction"] > 0.0  # eq_5 MDD higher than buf40_5


def test_science_gate_fail_when_bear_negative() -> None:
    arms = _arms(net=100.0, mdd=0.15, bear=-0.05)
    labels = list(arms)
    read = _read(arms, _regime(labels, -0.05), _crash(labels, 0.1))
    assert read["bear_regime_nonneg"] is False
    assert read["science_gate_pass"] is False


def test_empty_bear_bucket_is_untested_not_pass() -> None:
    arms = _arms(net=100.0, mdd=0.15, bear=0.0)
    labels = list(arms)
    read = _read(arms, _regime(labels, 0.0, n=0), _crash(labels, 0.0, n=0))
    assert read["bear_regime_nonneg"] is False  # n=0 → untested
    assert read["all_crash_slices_nonneg"] is False
    assert read["science_gate_pass"] is False
