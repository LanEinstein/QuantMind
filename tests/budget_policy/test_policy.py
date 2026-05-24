"""Tests for backend.budget_policy.policy (P0-7-amendment-2026-05-24)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.budget_policy.policy import (
    NO_COMPLIANT_TRADE,
    AffordabilityOutcome,
    BudgetCandidate,
    BudgetPolicyError,
    BudgetTier,
    BudgetTierConfig,
    BudgetTierPolicy,
    load_budget_tier_config,
)

ETF = "510300"  # whitelisted broad ETF
STOCK = "600519"  # individual stock (not whitelisted)


def _config(
    *,
    micro: float = 2000.0,
    small: float = 10000.0,
    pct: float = 0.15,
    lot: int = 100,
    whitelist: frozenset[str] = frozenset({"510300", "510500", "159949"}),
) -> BudgetTierConfig:
    return BudgetTierConfig(
        micro_max_cash_yuan=micro,
        small_max_cash_yuan=small,
        max_single_stock_pct=pct,
        lot_size=lot,
        etf_whitelist=whitelist,
    )


def _policy(**kw) -> BudgetTierPolicy:
    return BudgetTierPolicy(_config(**kw))


class TestClassifyTier:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("cash", "tier"),
        [
            (500.0, BudgetTier.MICRO),
            (1999.99, BudgetTier.MICRO),
            (2000.0, BudgetTier.SMALL),
            (9999.99, BudgetTier.SMALL),
            (10000.0, BudgetTier.NORMAL),
            (100000.0, BudgetTier.NORMAL),
        ],
    )
    def test_boundaries(self, cash: float, tier: BudgetTier) -> None:
        assert _policy().classify_tier(cash) == tier


class TestMicroTier:
    @pytest.mark.unit
    def test_micro_individual_stock_excluded_from_universe(self) -> None:
        v = _policy().assess_candidate(500.0, BudgetCandidate(STOCK, 90.0))
        assert v.outcome == AffordabilityOutcome.EXCLUDED_TIER_UNIVERSE
        assert not v.outcome.is_tradable

    @pytest.mark.unit
    def test_micro_etf_only_affordable_with_exception(self) -> None:
        # ¥500 cash, ETF 1-lot ¥400 > 15% (¥75) but ≤ cash → exception.
        v = _policy().assess_candidate(500.0, BudgetCandidate(ETF, 400.0))
        assert v.outcome == AffordabilityOutcome.AFFORDABLE_WITH_EXCEPTION
        assert v.concentration_exception is True
        assert v.requires_feishu_confirm is True

    @pytest.mark.unit
    def test_micro_500yuan_individual_stock_no_compliant_trade(self) -> None:
        # Acceptance: a few-hundred-yuan account with only an individual
        # stock yields NO_COMPLIANT_TRADE (first-class outcome).
        result = _policy().assess(500.0, [BudgetCandidate(STOCK, 900.0)])
        assert result.no_compliant_trade is True
        assert result.outcome == NO_COMPLIANT_TRADE
        assert result.affordable == ()

    @pytest.mark.unit
    def test_micro_etf_falls_to_etf_only(self) -> None:
        # Mixed candidates in Micro: only the whitelisted ETF survives.
        result = _policy().assess(
            1500.0,
            [BudgetCandidate(STOCK, 200.0), BudgetCandidate(ETF, 200.0)],
        )
        assert [c.code for c in result.affordable] == [ETF]
        assert result.outcome == "OK"


class TestConcentrationException:
    @pytest.mark.unit
    def test_individual_stock_never_gets_exception(self) -> None:
        # Small tier, stock 1-lot ¥4000 > 15% (¥750 of ¥5000) → excluded,
        # NOT an exception (个股不享有).
        v = _policy().assess_candidate(5000.0, BudgetCandidate(STOCK, 4000.0))
        assert v.outcome == AffordabilityOutcome.EXCLUDED_CONCENTRATION
        assert v.concentration_exception is False

    @pytest.mark.unit
    def test_whitelisted_etf_gets_exception_over_15pct(self) -> None:
        v = _policy().assess_candidate(5000.0, BudgetCandidate(ETF, 4000.0))
        assert v.outcome == AffordabilityOutcome.AFFORDABLE_WITH_EXCEPTION
        assert v.concentration_exception is True

    @pytest.mark.unit
    def test_unaffordable_when_lot_exceeds_cash(self) -> None:
        v = _policy().assess_candidate(5000.0, BudgetCandidate(ETF, 6000.0))
        assert v.outcome == AffordabilityOutcome.UNAFFORDABLE

    @pytest.mark.unit
    def test_nonpositive_lot_cost_fails_closed(self) -> None:
        v = _policy().assess_candidate(5000.0, BudgetCandidate(ETF, 0.0))
        assert v.outcome == AffordabilityOutcome.UNAFFORDABLE

    @pytest.mark.unit
    def test_nan_lot_cost_fails_closed(self) -> None:
        # A missing/corrupt 1-lot cost (NaN) must never slip into the
        # exception branch — comparisons with NaN are all False (codex P2).
        v = _policy().assess_candidate(5000.0, BudgetCandidate(ETF, float("nan")))
        assert v.outcome == AffordabilityOutcome.UNAFFORDABLE

    @pytest.mark.unit
    def test_inf_lot_cost_fails_closed(self) -> None:
        v = _policy().assess_candidate(5000.0, BudgetCandidate(ETF, float("inf")))
        assert v.outcome == AffordabilityOutcome.UNAFFORDABLE

    @pytest.mark.unit
    def test_nonfinite_or_nonpositive_cash_fails_closed(self) -> None:
        for cash in (float("nan"), float("inf"), 0.0, -100.0):
            v = _policy().assess_candidate(cash, BudgetCandidate(ETF, 400.0))
            assert v.outcome == AffordabilityOutcome.UNAFFORDABLE


class TestNormalTierP07Unchanged:
    @pytest.mark.unit
    def test_stock_within_15pct_affordable(self) -> None:
        # ¥50k Normal, stock 1-lot ¥7000 ≤ 15% (¥7500) → affordable, no exc.
        v = _policy().assess_candidate(50000.0, BudgetCandidate(STOCK, 7000.0))
        assert v.outcome == AffordabilityOutcome.AFFORDABLE
        assert v.concentration_exception is False

    @pytest.mark.unit
    def test_stock_over_15pct_excluded(self) -> None:
        # ¥50k Normal, stock 1-lot ¥8000 > 15% (¥7500) → excluded (P0-7 15%).
        v = _policy().assess_candidate(50000.0, BudgetCandidate(STOCK, 8000.0))
        assert v.outcome == AffordabilityOutcome.EXCLUDED_CONCENTRATION

    @pytest.mark.unit
    def test_max_lot_cost_is_15pct_of_cash(self) -> None:
        v = _policy().assess_candidate(50000.0, BudgetCandidate(STOCK, 100.0))
        assert v.max_lot_cost == pytest.approx(7500.0)

    @pytest.mark.unit
    def test_whitelisted_etf_no_exception_in_normal(self) -> None:
        # Normal tier keeps the P0-7 15% rule strict — even a whitelisted
        # ETF over 15% is excluded, NOT granted a concentration exception
        # (the exception is a Micro/Small-only accommodation).
        v = _policy().assess_candidate(50000.0, BudgetCandidate(ETF, 8000.0))
        assert v.outcome == AffordabilityOutcome.EXCLUDED_CONCENTRATION
        assert v.concentration_exception is False


class TestAssessAggregate:
    @pytest.mark.unit
    def test_no_compliant_trade_when_all_excluded(self) -> None:
        result = _policy().assess(
            50000.0,
            [BudgetCandidate(STOCK, 9000.0), BudgetCandidate(STOCK, 8000.0)],
        )
        assert result.no_compliant_trade is True
        assert result.outcome == NO_COMPLIANT_TRADE

    @pytest.mark.unit
    def test_ok_when_one_affordable(self) -> None:
        result = _policy().assess(
            50000.0,
            [BudgetCandidate(STOCK, 9000.0), BudgetCandidate(STOCK, 100.0)],
        )
        assert result.no_compliant_trade is False
        assert result.outcome == "OK"
        assert [c.code for c in result.affordable] == ["600519"]

    @pytest.mark.unit
    def test_empty_candidates_is_no_compliant_trade(self) -> None:
        result = _policy().assess(50000.0, [])
        assert result.no_compliant_trade is True


class TestConfigLoader:
    @pytest.mark.unit
    def test_loads_shipped_risk_yaml(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        shipped = repo_root / "config" / "risk.yaml"
        cfg = load_budget_tier_config(shipped)
        assert cfg.micro_max_cash_yuan == 2000.0
        assert cfg.small_max_cash_yuan == 10000.0
        assert cfg.max_single_stock_pct == 0.15  # P0-7 single source
        assert cfg.lot_size == 100
        assert cfg.etf_whitelist == frozenset({"510300", "510500", "159949"})

    @pytest.mark.unit
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_budget_tier_config(tmp_path / "nope.yaml")

    @pytest.mark.unit
    def test_micro_must_be_below_small(self, tmp_path: Path) -> None:
        bad = tmp_path / "risk.yaml"
        bad.write_text(
            "position_limits:\n  max_single_stock_pct: 0.15\n  volume_lot_size: 100\n"
            "budget_tiers:\n  micro_max_cash_yuan: 10000.0\n"
            "  small_max_cash_yuan: 2000.0\n  etf_whitelist: ['510300']\n",
            encoding="utf-8",
        )
        with pytest.raises(BudgetPolicyError, match="must be <"):
            load_budget_tier_config(bad)

    @pytest.mark.unit
    def test_empty_whitelist_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "risk.yaml"
        bad.write_text(
            "position_limits:\n  max_single_stock_pct: 0.15\n  volume_lot_size: 100\n"
            "budget_tiers:\n  micro_max_cash_yuan: 2000.0\n"
            "  small_max_cash_yuan: 10000.0\n  etf_whitelist: []\n",
            encoding="utf-8",
        )
        with pytest.raises(BudgetPolicyError, match="etf_whitelist"):
            load_budget_tier_config(bad)

    @pytest.mark.unit
    def test_missing_budget_tiers_section_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "risk.yaml"
        bad.write_text(
            "position_limits:\n  max_single_stock_pct: 0.15\n  volume_lot_size: 100\n",
            encoding="utf-8",
        )
        with pytest.raises(BudgetPolicyError, match="budget_tiers"):
            load_budget_tier_config(bad)

    @pytest.mark.unit
    def test_bad_pct_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "risk.yaml"
        bad.write_text(
            "position_limits:\n  max_single_stock_pct: 1.5\n  volume_lot_size: 100\n"
            "budget_tiers:\n  micro_max_cash_yuan: 2000.0\n"
            "  small_max_cash_yuan: 10000.0\n  etf_whitelist: ['510300']\n",
            encoding="utf-8",
        )
        with pytest.raises(BudgetPolicyError, match="max_single_stock_pct"):
            load_budget_tier_config(bad)


class TestImmutability:
    @pytest.mark.unit
    def test_config_frozen(self) -> None:
        cfg = _config()
        with pytest.raises(FrozenInstanceError):
            cfg.micro_max_cash_yuan = 1.0  # type: ignore[misc]

    @pytest.mark.unit
    def test_candidate_affordability_frozen(self) -> None:
        v = _policy().assess_candidate(50000.0, BudgetCandidate(STOCK, 100.0))
        with pytest.raises(FrozenInstanceError):
            v.outcome = AffordabilityOutcome.UNAFFORDABLE  # type: ignore[misc]
