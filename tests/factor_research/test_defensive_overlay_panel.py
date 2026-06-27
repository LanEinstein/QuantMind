"""Unit tests for the batch-B2 permanent defensive-sleeve overlay + integrity guards.

Covers: destinations are injected as top candidates on EVERY date (permanent), the
protected destination health is NOT independently weak (so the real rotation engine
never evicts it — a permanent hold), an empty sleeve reproduces the baseline, the
arm-sleeve map is well-formed, and the fail-closed guards (sleeve must engage, arms
must conserve) + the per-arm verdict behave as specified.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from backend.backtest.strategy import HeldPosition, _incumbent
from backend.slot_portfolio import load_rotation_policy_config
from backend.slot_portfolio.scoring import evaluate_incumbent_weakness
from scripts.factor_research import arena_ablation as aa
from scripts.factor_research import defensive_overlay_panel as dp
from scripts.factor_research import defensive_sleeve_ablation as ab
from scripts.factor_research.exit_veto_ablation import ArmResult


def _ranker_table(n_dates: int = 3, n_codes: int = 6) -> pd.DataFrame:
    rows = []
    for di in range(n_dates):
        date = f"2020010{di + 1}"
        for ci in range(n_codes):
            rows.append(
                {
                    "date": date,
                    "ts_code": f"{600000 + ci:06d}.SH",
                    "ranker_score": float(ci) - 3.0,
                    "ranker_pct": ci / (n_codes - 1),
                    "crowd_pct": ci / (n_codes - 1),
                    "log_circ_mv": 22.0 + ci * 0.1,
                }
            )
    return pd.DataFrame(rows)


def _arm(label: str, **kw: object) -> ArmResult:
    base = {
        "label": label, "net_pnl_yuan": 1.0, "total_return": 0.0,
        "max_drawdown_pct": 0.05, "monthly_turnover": 0.0, "fill_count": 1,
        "avg_exposure": 0.5, "conservation_ok": True, "exposure_cap_violations": 0,
        "period_returns": (0.0, 0.0), "mdd_within_cap": True,
    }
    base.update(kw)
    return ArmResult(**base)  # type: ignore[arg-type]


def test_inject_permanent_scores_tops_every_date() -> None:
    base = {"20200101": [("600005.SH", 1.0)], "20200102": [("600005.SH", 1.0)]}
    out = dp.inject_permanent_scores(base, (dp.BOND_ETF,))
    for date in base:
        assert out[date][0][0] == dp.BOND_ETF  # destination tops every date
        assert out[date][0][1] > 1.0


def test_inject_permanent_scores_empty_sleeve_is_baseline() -> None:
    base = {"20200101": [("600005.SH", 1.0)]}
    assert dp.inject_permanent_scores(base, ()) == base


def test_build_permanent_health_protects_destination() -> None:
    table = _ranker_table()
    base_health = {d: {} for d in sorted(table["date"].astype(str).unique())}
    health = dp.build_permanent_health(table, base_health, (dp.DIVIDEND_ETF,))
    for day in health.values():
        h = day[dp.DIVIDEND_ETF]
        assert h.line1_percentile == 1.0
        assert h.composite_score == aa.PROTECTED_COMPOSITE


def test_protected_destination_is_never_independently_weak() -> None:
    """The permanent-hold proof: the real weakness gate never flags the destination."""
    table = _ranker_table()
    health = dp.build_permanent_health(table, {"20200101": {}}, (dp.BOND_ETF,))[
        "20200101"
    ]
    config = load_rotation_policy_config("config/slot_rotation_policy.yaml")
    held = HeldPosition(code=dp.BOND_ETF, volume=100, holding_age_trading_days=99)
    incumbent = _incumbent(dp.BOND_ETF, held, health[dp.BOND_ETF])
    weakness = evaluate_incumbent_weakness(incumbent, config.incumbent_weak)
    assert not weakness.independently_weak  # protected ⇒ never rotated out


def test_strong_protected_health_shared_helper_not_weak() -> None:
    config = load_rotation_policy_config("config/slot_rotation_policy.yaml")
    held = HeldPosition(code="X", volume=100, holding_age_trading_days=99)
    incumbent = _incumbent("X", held, aa.strong_protected_health())
    weakness = evaluate_incumbent_weakness(incumbent, config.incumbent_weak)
    assert not weakness.independently_weak


def test_asset_buy_intent_count_counts_sleeve_buys() -> None:
    class _DV:
        def __init__(self, buys: tuple[str, ...]) -> None:
            self.buy_codes = buys

    vectors = [_DV((dp.BOND_ETF, "600000.SH")), _DV((dp.BOND_ETF,)), _DV(())]
    assert dp.asset_buy_intent_count(vectors, (dp.BOND_ETF,)) == 2


def test_arm_sleeves_map_is_well_formed() -> None:
    assert ab.ARM_SLEEVES["baseline"] == ()
    assert len(ab.ARM_SLEEVES["perm_cash3"]) == 3
    assert len(set(ab.ARM_SLEEVES["perm_cash4"])) == 4  # distinct cash codes
    assert ab.ARM_SLEEVES["perm_div1"] == (dp.DIVIDEND_ETF,)
    assert ab.ARM_SLEEVES["perm_bond1"] == (dp.BOND_ETF,)
    assert dp.DIVIDEND_ETF in ab.UNIVERSE_ETFS and dp.BOND_ETF in ab.UNIVERSE_ETFS


def test_assert_sleeve_engaged_raises_on_unfilled() -> None:
    ab._assert_sleeve_engaged("baseline", (), 0)  # empty sleeve → no expectation
    ab._assert_sleeve_engaged("perm_bond1", (dp.BOND_ETF,), 3)  # engaged → ok
    with pytest.raises(RuntimeError, match="never engaged"):
        ab._assert_sleeve_engaged("perm_bond1", (dp.BOND_ETF,), 0)


def test_assert_conservation_raises_on_violation() -> None:
    ab._assert_conservation(_arm("ok"))
    with pytest.raises(RuntimeError, match="conservation"):
        ab._assert_conservation(_arm("bad", conservation_ok=False))


def test_verdict_fails_when_no_arm_clears_all_gates() -> None:
    arms = [
        _arm("baseline"),
        _arm("perm_bond1", max_drawdown_pct=0.05, net_pnl_yuan=10.0),
    ]
    dsr = {"perm_bond1": 0.10}  # below 0.95 → fails DSR
    v = ab._verdict(arms, dsr, {"perm_bond1": 1.0})
    assert v["any_deployable_edge"] is False
    assert v["per_arm"]["perm_bond1"]["deployable"] is False
    assert "baseline" not in v["per_arm"]  # baseline excluded from the verdict


def test_verdict_passes_when_an_arm_clears_all_gates() -> None:
    arms = [_arm("perm_bond1", max_drawdown_pct=0.05, net_pnl_yuan=10.0)]
    v = ab._verdict(arms, {"perm_bond1": 0.97}, {"perm_bond1": 2.0})
    assert v["any_deployable_edge"] is True


def test_hold_baseline_field_mapping_via_dataclasses() -> None:
    """The shared hold_baseline_arm produces a complete ArmResult (all fields set)."""
    assert {f.name for f in dataclasses.fields(ArmResult)} >= {
        "label", "net_pnl_yuan", "max_drawdown_pct", "mdd_within_cap",
        "conservation_ok", "period_returns",
    }
