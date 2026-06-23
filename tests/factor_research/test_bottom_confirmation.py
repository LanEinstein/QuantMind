"""Tests for the §3.8B bottom-confirmation gate (QGR-3 ⑧ slow leg).

The gate is an OVERLAY (a multi-indicator confirm flag), not a rankable factor:
each condition is a pure bool|None (None = cannot evaluate → fail-closed), the
composite uses one-veto-is-enough logic, and the cyq_perf cost-band component is
kept SEPARATE from the clean-PIT core so it can be ablated.
"""

from __future__ import annotations

from scripts.factor_research.bottom_confirmation import (
    BOTTOM_CONFIRM_CONDITIONS,
    CORE_CONDITION_NAMES,
    above_cost_band,
    compute_bottom_confirmation,
    gate_all,
    no_breakdown,
    no_distress,
    quality_floor,
    vol_dryup,
)
from scripts.factor_research.cyq_perf_pit import ChipRecord


class TestVolumeDryup:
    def test_recent_below_baseline_is_dryup(self) -> None:
        rates = [5.0] * 20 + [2.0] * 5  # recent 5d well below the prior-20d baseline
        assert vol_dryup(rates) is True

    def test_recent_above_baseline_is_not_dryup(self) -> None:
        rates = [2.0] * 20 + [5.0] * 5
        assert vol_dryup(rates) is False

    def test_insufficient_history_none(self) -> None:
        assert vol_dryup([2.0] * 10) is None


class TestNoBreakdown:
    def test_holds_above_recent_trough(self) -> None:
        closes = [10.0 - i * 0.1 for i in range(20)] + [9.0]  # bounced off the low
        assert no_breakdown(closes) is True

    def test_fresh_low_is_breakdown(self) -> None:
        closes = [10.0 - i * 0.1 for i in range(21)]  # monotone decline → new low today
        assert no_breakdown(closes) is False

    def test_insufficient_history_none(self) -> None:
        assert no_breakdown([10.0, 9.9]) is None


class TestNoDistress:
    def test_st_is_distress(self) -> None:
        assert no_distress(is_st=True) is False

    def test_non_st_ok(self) -> None:
        assert no_distress(is_st=False) is True

    def test_unknown_none(self) -> None:
        assert no_distress(is_st=None) is None


class TestQualityFloor:
    def test_profitable_passes(self) -> None:
        assert quality_floor(roe=12.0, gpm=30.0, ep_ttm=0.05) is True

    def test_negative_roe_fails(self) -> None:
        assert quality_floor(roe=-3.0, gpm=30.0, ep_ttm=0.05) is False

    def test_loss_maker_negative_ep_fails(self) -> None:
        assert quality_floor(roe=2.0, gpm=10.0, ep_ttm=-0.02) is False

    def test_missing_fundamental_none(self) -> None:
        assert quality_floor(roe=None, gpm=30.0, ep_ttm=0.05) is None


class TestAboveCostBand:
    def test_price_above_median_cost(self) -> None:
        chip = ChipRecord(1.0, 1.5, 2.0, 2.5, 3.0, 2.0, 60.0)
        assert above_cost_band(raw_close=2.4, chip=chip) is True

    def test_price_below_median_cost(self) -> None:
        chip = ChipRecord(1.0, 1.5, 2.0, 2.5, 3.0, 2.0, 40.0)
        assert above_cost_band(raw_close=1.8, chip=chip) is False

    def test_missing_chip_none(self) -> None:
        assert above_cost_band(raw_close=2.4, chip=None) is None


class TestGateAll:
    def test_all_true(self) -> None:
        assert gate_all([True, True, True]) is True

    def test_one_false_vetoes_even_with_none(self) -> None:
        assert gate_all([True, False, None]) is False

    def test_none_without_false_is_unknown(self) -> None:
        assert gate_all([True, None, True]) is None


class TestComputeBottomConfirmation:
    def _series(self) -> dict[str, list[float]]:
        # 20d gentle decline then a bounce → no breakdown; turnover dries up.
        closes = [10.0 - i * 0.05 for i in range(25)] + [9.2]
        turn = [4.0] * 21 + [1.5] * 5
        return {"adj_closes": closes, "turnover_rates": turn}

    def test_full_confirm_when_all_conditions_pass(self) -> None:
        s = self._series()
        chip = ChipRecord(7.0, 8.0, 9.0, 10.0, 11.0, 9.0, 65.0)
        out = compute_bottom_confirmation(
            adj_closes=s["adj_closes"],
            turnover_rates=s["turnover_rates"],
            raw_close=9.5,  # above cost_50pct 9.0
            is_st=False,
            roe=10.0,
            gpm=25.0,
            ep_ttm=0.04,
            chip=chip,
        )
        assert out["bc_vol_dryup"] == 1.0
        assert out["bc_no_breakdown"] == 1.0
        assert out["bc_no_distress"] == 1.0
        assert out["bc_quality_floor"] == 1.0
        assert out["bc_above_cost_band"] == 1.0
        assert out["bc_core_confirmed"] == 1.0
        assert out["bc_full_confirmed"] == 1.0
        assert out["bc_cost_premium"] is not None
        assert out["bc_winner_rate"] == 65.0

    def test_st_vetoes_core_and_full(self) -> None:
        s = self._series()
        chip = ChipRecord(7.0, 8.0, 9.0, 10.0, 11.0, 9.0, 65.0)
        out = compute_bottom_confirmation(
            adj_closes=s["adj_closes"],
            turnover_rates=s["turnover_rates"],
            raw_close=9.5,
            is_st=True,  # distress veto
            roe=10.0,
            gpm=25.0,
            ep_ttm=0.04,
            chip=chip,
        )
        assert out["bc_no_distress"] == 0.0
        assert out["bc_core_confirmed"] == 0.0
        assert out["bc_full_confirmed"] == 0.0  # one veto is enough

    def test_no_cyq_keeps_core_but_full_unknown(self) -> None:
        s = self._series()
        out = compute_bottom_confirmation(
            adj_closes=s["adj_closes"],
            turnover_rates=s["turnover_rates"],
            raw_close=9.5,
            is_st=False,
            roe=10.0,
            gpm=25.0,
            ep_ttm=0.04,
            chip=None,  # pre-2018 / missing cyq_perf
        )
        assert out["bc_core_confirmed"] == 1.0
        assert out["bc_above_cost_band"] is None
        assert out["bc_full_confirmed"] is None  # core True but cyq unknown
        assert out["bc_cost_premium"] is None
        assert out["bc_winner_rate"] is None

    def test_registry_core_membership(self) -> None:
        core = {c.name for c in BOTTOM_CONFIRM_CONDITIONS if c.in_core}
        assert core == set(CORE_CONDITION_NAMES)
        assert "above_cost_band" not in core  # cyq_perf is ablatable, not core

    def test_registry_names_match_emitted_columns(self) -> None:
        # Guard against a registry-name vs emitted-column drift (the diagnostic
        # derives per-condition columns as `bc_{name}` — a mismatch silently drops
        # a condition from the coverage / marginal disclosure).
        from scripts.factor_research.bottom_confirmation import (
            BOTTOM_CONFIRM_COLUMNS,
            compute_bottom_confirmation,
        )

        derived = {f"bc_{c.name}" for c in BOTTOM_CONFIRM_CONDITIONS}
        assert derived <= set(BOTTOM_CONFIRM_COLUMNS)
        # and every emitted key is actually produced by compute_bottom_confirmation
        out = compute_bottom_confirmation(
            adj_closes=[10.0] * 25,
            turnover_rates=[2.0] * 25,
            raw_close=10.0,
            is_st=False,
            roe=1.0,
            gpm=1.0,
            ep_ttm=0.05,
            chip=None,
        )
        assert set(BOTTOM_CONFIRM_COLUMNS) == set(out)
