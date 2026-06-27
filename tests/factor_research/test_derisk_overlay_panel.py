"""Unit tests for the batch-B1 regime de-risk overlay transforms.

Covers the deterministic core: the synthetic cash bar is flat + always tradable,
the augmented bar source overlays cash without dropping real bars, the placebo
schedules match the regime arm's treated-date COUNT (and the random one is
seed-deterministic), cash scores/health are injected ONLY on treated dates, and —
the key behavioural proof — a treated-date stock incumbent is rotated into a cash
sleeve by the REAL rotation engine (the mechanism is not vacuous).
"""

from __future__ import annotations

import pandas as pd

from backend.backtest.event_loop import DayBar
from backend.backtest.strategy import _incumbent
from backend.slot_portfolio import load_rotation_policy_config
from backend.slot_portfolio.policy import propose_rotation
from backend.slot_portfolio.scoring import ChallengerState, evaluate_incumbent_weakness
from scripts.factor_research import derisk_overlay_panel as do


def _ranker_table(n_dates: int = 4, n_codes: int = 8) -> pd.DataFrame:
    rows = []
    for di in range(n_dates):
        date = f"2020010{di + 1}"
        for ci in range(n_codes):
            code = f"{600000 + ci:06d}.SH"
            rows.append(
                {
                    "date": date,
                    "ts_code": code,
                    "ranker_score": float(ci) - 4.0,
                    "ranker_pct": ci / (n_codes - 1),
                    "crowd_pct": ci / (n_codes - 1),
                    "log_circ_mv": 22.0 + ci * 0.1,
                }
            )
    return pd.DataFrame(rows)


class _FakeBarSource:
    def __init__(self, days: tuple[str, ...]) -> None:
        self._days = days

    def trading_days(self) -> tuple[str, ...]:
        return self._days

    def bars_on(self, day: str):
        return {
            "600000.SH": DayBar(
                code="600000.SH", trade_date=day, open_cents=1000, high_cents=1000,
                low_cents=1000, close_cents=1000, adv_volume=1e9,
                limit_up_cents=2000, limit_down_cents=500, board="sh_main",
                transfer_fee_applies=False,
            )
        }


def test_cash_bar_is_flat_and_tradable() -> None:
    bar = do.cash_bar("CASH1.SH", "20200101")
    assert bar.open_cents == bar.close_cents == do.CASH_PRICE_CENTS
    assert not bar.at_limit_up  # buyable
    assert not bar.at_limit_down  # sellable
    assert bar.board == do.CASH_BOARD


def test_augmented_bar_source_overlays_cash_keeps_real() -> None:
    src = do.CashAugmentedBarSource(_FakeBarSource(("20200101",)))
    bars = src.bars_on("20200101")
    assert "600000.SH" in bars  # real bar preserved
    for cash in do.CASH_CODES:
        assert cash in bars and bars[cash].close_cents == do.CASH_PRICE_CENTS


def test_placebo_schedules_match_treated_count() -> None:
    rebs = [f"2020{m:02d}01" for m in range(1, 13)]
    const = do.constant_cash_dates(rebs, 4)
    rand = do.random_cash_dates(rebs, 4, seed=1)
    assert len(const) == 4 and len(rand) == 4
    assert set(const) <= set(rebs) and set(rand) <= set(rebs)


def test_random_cash_dates_is_seed_deterministic() -> None:
    rebs = [f"2020{m:02d}01" for m in range(1, 13)]
    first = do.random_cash_dates(rebs, 5, seed=7)
    assert first == do.random_cash_dates(rebs, 5, seed=7)


def test_inject_cash_scores_only_on_treated_dates() -> None:
    base = {"20200101": [("600005.SH", 1.0)], "20200102": [("600005.SH", 1.0)]}
    out = do.inject_cash_scores(base, ["20200101"])
    treated = out["20200101"]
    assert treated[0][0] in do.CASH_CODES  # cash tops the candidate list
    assert all(c not in do.CASH_CODES for c, _ in out["20200102"])  # untreated: no cash


def test_build_arm_health_treated_vs_untreated() -> None:
    table = _ranker_table()
    base_health = {
        d: {} for d in sorted(table["date"].astype(str).unique())
    }
    health = do.build_arm_health(table, base_health, ["20200101"])
    treated = health["20200101"]
    # treated: cash strong + present, stocks forced weak (anomaly flag set)
    assert treated["CASH1.SH"].composite_score == do.CASH_SCORE
    assert treated["600000.SH"].anomaly_flag_active is True
    # untreated: cash weak (so a held sleeve unwinds)
    untreated = health["20200102"]
    assert untreated["CASH1.SH"].composite_score == -do.CASH_SCORE
    assert untreated["CASH1.SH"].anomaly_flag_active is True


def test_treated_stock_rotates_into_cash_real_engine() -> None:
    """The KEY behavioural proof: on a treated date the real rotation engine sells
    a forced-weak stock incumbent and buys a cash sleeve (mechanism not vacuous)."""
    table = _ranker_table()
    health = do.build_arm_health(table, {"20200101": {}}, ["20200101"])["20200101"]
    config = load_rotation_policy_config("config/slot_rotation_policy.yaml")

    # A held stock, aged past the min-hold gate, with treated-date forced-weak health.
    from backend.backtest.strategy import HeldPosition

    held = HeldPosition(code="600000.SH", volume=100, holding_age_trading_days=99)
    incumbent = _incumbent("600000.SH", held, health["600000.SH"])
    weakness = evaluate_incumbent_weakness(incumbent, config.incumbent_weak)
    assert weakness.independently_weak

    cash_challenger = ChallengerState(
        code="CASH1.SH",
        qualified=health["CASH1.SH"].qualified,
        line1_percentile=health["CASH1.SH"].line1_percentile,
        composite_score=health["CASH1.SH"].composite_score,
    )
    proposal = propose_rotation([incumbent], [cash_challenger], config)
    assert proposal.should_rotate is True
    assert proposal.challenger_code == "CASH1.SH"
    assert proposal.incumbent_code == "600000.SH"


def test_cash_buy_intent_count_counts_cash_buys() -> None:
    class _DV:
        def __init__(self, buys: tuple[str, ...]) -> None:
            self.buy_codes = buys

    vectors = [_DV(("CASH1.SH", "600000.SH")), _DV(("CASH2.SH",)), _DV(())]
    assert do.cash_buy_intent_count(vectors) == 2


# -- ablation integrity guards (codex #3/#4/#5) -------------------------------


def test_market_coverage_guard_raises_on_gap() -> None:
    """A sparse regime market series fails closed (data gap ≠ scientific FAIL)."""
    import pytest

    from scripts.factor_research import derisk_regime_ablation as ab

    assert ab._assert_market_coverage(95, 100) >= 0.90  # full enough → returns frac
    with pytest.raises(ValueError, match="fail-closed"):
        ab._assert_market_coverage(50, 100)  # 50% coverage → raise


def test_treatment_fired_guard_raises_on_vacuous_mechanism() -> None:
    """Treated dates with zero cash buys means the mechanism silently didn't fire."""
    import pytest

    from scripts.factor_research import derisk_regime_ablation as ab

    ab._assert_treatment_fired(0, 0)  # no treated dates → no expectation, ok
    ab._assert_treatment_fired(12, 7)  # treated + fired → ok
    with pytest.raises(RuntimeError, match="did not fire"):
        ab._assert_treatment_fired(12, 0)  # treated but vacuous → raise


def test_enough_periods_guard_raises_on_degenerate_window() -> None:
    import pytest

    from scripts.factor_research import derisk_regime_ablation as ab

    ab._assert_enough_periods(99)  # healthy
    with pytest.raises(ValueError, match="degenerate"):
        ab._assert_enough_periods(1)
