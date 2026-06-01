"""V-002 — deterministic rotation scoring (7-condition weak + 4-condition margin).

Adversarial-first: every one of the 7 ``incumbent_independently_weak``
conditions is proven necessary (flip one → not weak), the challenger margin is
proven to require all four parts, and corrupt (non-finite / out-of-range)
inputs fail closed toward *inaction* (never weak, never winning).
"""

from __future__ import annotations

import math

import pytest

from backend.slot_portfolio.scoring import (
    ChallengerMarginConfig,
    ChallengerState,
    IncumbentState,
    IncumbentWeakConfig,
    evaluate_challenger_margin,
    evaluate_incumbent_weakness,
)

WEAK_CONFIG = IncumbentWeakConfig(
    min_holding_age_trading_days=5,
    max_line1_percentile=0.40,
    min_rank_deterioration_pct=0.20,
    score_below_median_mad_mult=0.75,
    drawdown_soft_threshold=0.08,
)

MARGIN_CONFIG = ChallengerMarginConfig(
    min_percentile=0.75,
    min_rank_lead_pct=0.25,
    min_composite_score_margin=0.10,
)


def _weak_incumbent(**overrides: object) -> IncumbentState:
    """A baseline incumbent that IS independently weak (all 7 conditions hold).

    Confirmation fires via 6c (drawdown). Each test flips exactly one field to
    prove that condition is necessary.
    """
    base = dict(
        code="600001",
        line1_percentile=0.30,      # cond 4 — <= P40
        composite_score=0.30,
        entry_percentile=0.70,      # cond 5 — deteriorated 0.40 >= 0.20
        holding_age_trading_days=10,  # cond 3 — >= 5
        protective_stop_active=False,  # cond 1
        hard_exit_pending=False,       # cond 2
        score_median_20d=0.50,
        score_mad_20d=0.0,          # 6a off (no dispersion)
        anomaly_flag_active=False,  # 6b off
        drawdown_from_local_high=0.12,  # 6c on (>= 0.08)
        suspended=False,            # cond 7
        limit_down_unsellable=False,
        corporate_action_unsafe=False,
    )
    base.update(overrides)
    return IncumbentState(**base)  # type: ignore[arg-type]


class TestIncumbentWeakBaseline:
    def test_baseline_is_independently_weak(self) -> None:
        w = evaluate_incumbent_weakness(_weak_incumbent(), WEAK_CONFIG)
        assert w.independently_weak
        assert w.drawdown_confirmation and not w.anomaly_confirmation


class TestEachConditionNecessary:
    def test_protective_stop_blocks_weak(self) -> None:
        # cond 1: a protective stop active → never rotated (it protects itself).
        w = evaluate_incumbent_weakness(
            _weak_incumbent(protective_stop_active=True), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.no_protective_stop

    def test_hard_exit_pending_blocks_weak(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(hard_exit_pending=True), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.no_hard_exit

    def test_too_young_blocks_weak(self) -> None:
        # cond 3: held < min hold period → not rotatable yet.
        w = evaluate_incumbent_weakness(
            _weak_incumbent(holding_age_trading_days=4), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.aged_enough

    def test_age_exactly_min_is_aged_enough(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(holding_age_trading_days=5), WEAK_CONFIG
        )
        assert w.aged_enough and w.independently_weak

    def test_strong_percentile_blocks_weak(self) -> None:
        # cond 4: percentile above P40 → not in the weak band.
        w = evaluate_incumbent_weakness(
            _weak_incumbent(line1_percentile=0.41, entry_percentile=0.99),
            WEAK_CONFIG,
        )
        assert not w.independently_weak
        assert not w.percentile_weak

    def test_percentile_exactly_p40_is_weak_band(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(line1_percentile=0.40, entry_percentile=0.65),
            WEAK_CONFIG,
        )
        assert w.percentile_weak and w.independently_weak

    def test_no_rank_deterioration_blocks_weak(self) -> None:
        # cond 5: entered already weak, no deterioration → not rotated.
        w = evaluate_incumbent_weakness(
            _weak_incumbent(entry_percentile=0.35), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.rank_deteriorated

    def test_no_confirmation_blocks_weak(self) -> None:
        # cond 6: all three confirmations off → not weak (weak band alone isn't
        # enough — needs a corroborating deterioration signal).
        w = evaluate_incumbent_weakness(
            _weak_incumbent(drawdown_from_local_high=0.0), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.has_confirmation

    def test_veto_suspended_blocks_weak(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(suspended=True), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.no_veto

    def test_veto_limit_down_blocks_weak(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(limit_down_unsellable=True), WEAK_CONFIG
        )
        assert not w.independently_weak

    def test_veto_corporate_action_blocks_weak(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(corporate_action_unsafe=True), WEAK_CONFIG
        )
        assert not w.independently_weak


class TestConfirmationChannels:
    def test_anomaly_alone_confirms(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(drawdown_from_local_high=0.0, anomaly_flag_active=True),
            WEAK_CONFIG,
        )
        assert w.anomaly_confirmation and w.has_confirmation and w.independently_weak

    def test_mad_alone_confirms(self) -> None:
        # 6a: score 0.30 below median 0.50 by 0.20 >= 0.75·MAD(0.10)=0.075.
        w = evaluate_incumbent_weakness(
            _weak_incumbent(
                drawdown_from_local_high=0.0,
                score_median_20d=0.50, score_mad_20d=0.10, composite_score=0.30,
            ),
            WEAK_CONFIG,
        )
        assert w.score_below_median_mad and w.independently_weak

    def test_zero_mad_never_confirms(self) -> None:
        # No dispersion → 6a cannot fire (fail-closed), even if score < median.
        w = evaluate_incumbent_weakness(
            _weak_incumbent(
                drawdown_from_local_high=0.0,
                score_median_20d=0.50, score_mad_20d=0.0, composite_score=0.10,
            ),
            WEAK_CONFIG,
        )
        assert not w.score_below_median_mad and not w.has_confirmation


class TestIncumbentFailClosed:
    @pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, 1.5])
    def test_bad_percentile_is_not_weak(self, bad: float) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(line1_percentile=bad), WEAK_CONFIG
        )
        assert not w.independently_weak
        assert not w.percentile_weak

    def test_bad_entry_percentile_not_deteriorated(self) -> None:
        w = evaluate_incumbent_weakness(
            _weak_incumbent(entry_percentile=math.nan), WEAK_CONFIG
        )
        assert not w.rank_deteriorated and not w.independently_weak


def _challenger(**overrides: object) -> ChallengerState:
    base = dict(
        code="000002", qualified=True, line1_percentile=0.90, composite_score=0.90
    )
    base.update(overrides)
    return ChallengerState(**base)  # type: ignore[arg-type]


class TestChallengerMargin:
    def test_baseline_wins(self) -> None:
        m = evaluate_challenger_margin(
            _challenger(), _weak_incumbent(), MARGIN_CONFIG
        )
        assert m.wins_by_margin

    def test_unqualified_never_wins(self) -> None:
        # A strong-but-unqualified candidate must never displace an incumbent.
        m = evaluate_challenger_margin(
            _challenger(qualified=False), _weak_incumbent(), MARGIN_CONFIG
        )
        assert not m.wins_by_margin
        assert m.percentile_strong  # strong on every axis except qualification

    def test_below_p75_does_not_win(self) -> None:
        m = evaluate_challenger_margin(
            _challenger(line1_percentile=0.74, composite_score=0.90),
            _weak_incumbent(line1_percentile=0.30),
            MARGIN_CONFIG,
        )
        assert not m.percentile_strong and not m.wins_by_margin

    def test_insufficient_rank_lead_does_not_win(self) -> None:
        # challenger 0.78 − incumbent 0.60 = 0.18 < 0.25 lead.
        m = evaluate_challenger_margin(
            _challenger(line1_percentile=0.78, composite_score=0.95),
            _weak_incumbent(line1_percentile=0.60, composite_score=0.60),
            MARGIN_CONFIG,
        )
        assert not m.rank_lead_sufficient and not m.wins_by_margin

    def test_insufficient_composite_margin_does_not_win(self) -> None:
        # Big rank lead but composite margin 0.05 < 0.10 (rank alone insufficient).
        m = evaluate_challenger_margin(
            _challenger(line1_percentile=0.95, composite_score=0.35),
            _weak_incumbent(line1_percentile=0.30, composite_score=0.30),
            MARGIN_CONFIG,
        )
        assert not m.composite_margin_sufficient and not m.wins_by_margin

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_bad_challenger_score_fails_closed(self, bad: float) -> None:
        m = evaluate_challenger_margin(
            _challenger(composite_score=bad), _weak_incumbent(), MARGIN_CONFIG
        )
        assert not m.wins_by_margin


class TestDeterminism:
    def test_same_inputs_same_result(self) -> None:
        inc = _weak_incumbent()
        ch = _challenger()
        a1 = evaluate_incumbent_weakness(inc, WEAK_CONFIG)
        a2 = evaluate_incumbent_weakness(inc, WEAK_CONFIG)
        b1 = evaluate_challenger_margin(ch, inc, MARGIN_CONFIG)
        b2 = evaluate_challenger_margin(ch, inc, MARGIN_CONFIG)
        assert a1 == a2 and b1 == b2
