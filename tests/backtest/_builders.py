"""Shared deterministic builders for the AE-004 harness tests (not collected).

Tests are NOT subject to the ``[BACKTEST]`` import allowlist, so these helpers
may import broadly; they keep the test modules terse and the fixtures in one
place.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.backtest.event_loop import DayBar
from backend.backtest.friction import FrictionParams
from backend.backtest.strategy import (
    CodeHealth,
    DailySignals,
    StrategyConfig,
)
from backend.candidate_selector import (
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)
from backend.slot_portfolio import (
    ChallengerMarginConfig,
    ChurnConfig,
    ExpiryConfig,
    IncumbentWeakConfig,
    RotationPolicyConfig,
)

# config/broker.yaml friction (mock tier) + cost_calculator.TRANSFER_FEE_RATE_SZ.
BROKER_FRICTION = FrictionParams(
    commission_rate=0.00015,
    min_commission_cents=500,
    stamp_tax_rate=0.001,
    transfer_fee_rate=0.0000341,
    slippage_bps_by_board={
        "sh_main": 1.5,
        "sz_main": 1.5,
        "chuangye": 3.5,
        "etf": 1.5,
    },
)


def make_bar(
    code: str,
    date: str,
    *,
    open_cents: int,
    close_cents: int | None = None,
    high_cents: int | None = None,
    low_cents: int | None = None,
    adv_volume: float = 10_000_000.0,
    board: str = "sh_main",
    transfer_fee_applies: bool = False,
    limit_up_cents: int | None = None,
    limit_down_cents: int | None = None,
) -> DayBar:
    """A DayBar with wide synthetic limits unless overridden."""
    close = close_cents if close_cents is not None else open_cents
    high = high_cents if high_cents is not None else max(open_cents, close)
    low = low_cents if low_cents is not None else min(open_cents, close)
    return DayBar(
        code=code,
        trade_date=date,
        open_cents=open_cents,
        high_cents=high,
        low_cents=low,
        close_cents=close,
        adv_volume=adv_volume,
        limit_up_cents=(
            limit_up_cents if limit_up_cents is not None else open_cents * 10
        ),
        limit_down_cents=(limit_down_cents if limit_down_cents is not None else 1),
        board=board,
        transfer_fee_applies=transfer_fee_applies,
    )


class StaticBarSource:
    """A BarSource over a fixed ``{day: {code: DayBar}}`` map."""

    def __init__(self, bars_by_day: Mapping[str, Mapping[str, DayBar]]) -> None:
        self._bars = {d: dict(b) for d, b in bars_by_day.items()}
        self._days = tuple(sorted(self._bars))

    def trading_days(self) -> tuple[str, ...]:
        return self._days

    def bars_on(self, day: str) -> Mapping[str, DayBar]:
        return self._bars.get(day, {})


class StaticScoreProvider:
    """A ScoreProvider over a fixed ``{day: DailySignals}`` map."""

    def __init__(self, signals_by_day: Mapping[str, DailySignals]) -> None:
        self._signals = dict(signals_by_day)

    def signals_asof(self, day: str) -> DailySignals:
        return self._signals.get(day, DailySignals(trade_date=day, quant_candidates=()))


def make_selector(*, final: int = 5, min_quant: int = 1) -> CandidateSelector:
    return CandidateSelector(
        SelectorConfig(
            version="test",
            final_shortlist_size=final,
            min_quant_slots=min_quant,
            max_percentile_shift=0.2,
            advisory_weight=0.0,
            feature_def_hash="",
        )
    )


def make_rotation_config() -> RotationPolicyConfig:
    return RotationPolicyConfig(
        version="test",
        incumbent_weak=IncumbentWeakConfig(
            min_holding_age_trading_days=5,
            max_line1_percentile=0.4,
            min_rank_deterioration_pct=0.2,
            score_below_median_mad_mult=2.0,
            drawdown_soft_threshold=0.1,
        ),
        challenger_margin=ChallengerMarginConfig(
            min_percentile=0.75,
            min_rank_lead_pct=0.25,
            min_composite_score_margin=0.1,
        ),
        churn=ChurnConfig(
            max_rotations_per_day=1,
            max_open_intents=1,
            rotation_subcap=1,
            same_incumbent_cooldown_td=20,
            same_pair_cooldown_td=30,
        ),
        expiry=ExpiryConfig(max_trading_days=3),
        config_hash="0" * 64,
    )


def make_strategy_config(
    *, max_total_positions: int = 5, single_stock_cap_percent: int = 15
) -> StrategyConfig:
    return StrategyConfig(
        selector=make_selector(),
        rotation=make_rotation_config(),
        max_total_positions=max_total_positions,
        single_stock_cap_percent=single_stock_cap_percent,
    )


def weak_incumbent_health() -> CodeHealth:
    """Health that makes a held code pass all 7 independence conditions."""
    return CodeHealth(
        line1_percentile=0.30,
        composite_score=0.10,
        entry_percentile=0.60,
        drawdown_from_local_high=0.20,
    )


def strong_challenger_health() -> CodeHealth:
    return CodeHealth(line1_percentile=0.90, composite_score=0.90, qualified=True)


def candidate(code: str, score: float) -> QuantCandidate:
    return QuantCandidate(code=code, score=score)
