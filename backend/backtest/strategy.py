"""Deterministic daily-rhythm strategy adapter (AE-004 §2.2 / §2.3).

The harness replays the live *daily* decision rhythm: each day it runs the real
:class:`backend.candidate_selector.CandidateSelector` over the day's quant
candidates, then the real :func:`backend.slot_portfolio.propose_rotation`
≤5-slot rotation, and emits the resulting BUY/SELL order intents — which the
event loop fills on the *next* bar (zipline barrier). Everything is a pure
function of injected, look-ahead-free inputs.

§2.3 (codex J1, amendment-locked): **Line-2's 30s intraday monitoring
(stop-loss / take-profit / rotation yield) is non-alpha protective risk and does
NOT enter the evolution loop** — a daily backtest cannot faithfully replay a 30s
event stream, so a daily approximation would be an unvalidated new decision
layer. This adapter therefore models *only* the daily selection + rotation
rhythm; it never imports the Line-2 intraday path.

The rich per-code health each engine needs is supplied by an injected
:class:`ScoreProvider` (AE-004 tests feed fixtures; AE-005 wires the real factor
/ scoring layer). Position sizing is integer-分 + lot-floored; nothing on the
decision path is a bare float comparison.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.backtest.event_loop import DayBar
from backend.candidate_selector import (
    CandidateSelector,
    QuantCandidate,
)
from backend.slot_portfolio import (
    ChallengerState,
    IncumbentState,
    RotationPolicyConfig,
    propose_rotation,
)

_LOT_SIZE = 100
"""A-share minimum trading lot (mirrors RiskConfig.volume_lot_size)."""


@dataclass(frozen=True)
class CodeHealth:
    """Per-code market health for the selection + rotation engines.

    The union of what ``ChallengerState`` (qualified / percentile / score) and
    ``IncumbentState`` (the 7 independence-condition inputs) need. Incumbent-only
    fields default to "healthy" so a test can construct the common case tersely.
    """

    line1_percentile: float
    composite_score: float
    qualified: bool = True
    entry_percentile: float = 0.0
    score_median_20d: float = 0.0
    score_mad_20d: float = 0.0
    anomaly_flag_active: bool = False
    drawdown_from_local_high: float = 0.0
    protective_stop_active: bool = False
    hard_exit_pending: bool = False
    suspended: bool = False
    limit_down_unsellable: bool = False
    corporate_action_unsafe: bool = False


@dataclass(frozen=True)
class DailySignals:
    """One day's strategy inputs (look-ahead-free, injected)."""

    trade_date: str
    quant_candidates: tuple[QuantCandidate, ...]
    health: Mapping[str, CodeHealth] = field(default_factory=dict)


@runtime_checkable
class ScoreProvider(Protocol):
    """Injected as-of signal provider. Production wires the real scoring layer."""

    def signals_asof(self, day: str) -> DailySignals: ...


@dataclass(frozen=True)
class HeldPosition:
    """A current holding the loop hands the strategy (integer units)."""

    code: str
    volume: int
    holding_age_trading_days: int


@dataclass(frozen=True)
class PortfolioView:
    """The loop's view of the book at decision time (integer 分)."""

    trade_date: str
    total_equity_cents: int
    cash_cents: int
    holdings: tuple[HeldPosition, ...] = ()


@dataclass(frozen=True)
class OrderIntent:
    """One decided order — filled on the next bar (immutable)."""

    code: str
    side_is_buy: bool
    volume: int


@dataclass(frozen=True)
class DayDecision:
    """A day's decided orders + the decision trace for the golden-vector oracle.

    ``scores`` carries the quant score that drove each shortlisted code so the
    Lane-2 golden-vector oracle can pin the *scores*, not just the code tuples.
    """

    trade_date: str
    orders: tuple[OrderIntent, ...]
    shortlist: tuple[str, ...]
    sell_codes: tuple[str, ...]
    buy_codes: tuple[str, ...]
    scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyConfig:
    """Bundles the selector + rotation configs + the slot cap (immutable)."""

    selector: CandidateSelector
    rotation: RotationPolicyConfig
    max_total_positions: int
    single_stock_cap_percent: int = 15


def _incumbent(code: str, held: HeldPosition, health: CodeHealth) -> IncumbentState:
    return IncumbentState(
        code=code,
        line1_percentile=health.line1_percentile,
        composite_score=health.composite_score,
        entry_percentile=health.entry_percentile,
        holding_age_trading_days=held.holding_age_trading_days,
        protective_stop_active=health.protective_stop_active,
        hard_exit_pending=health.hard_exit_pending,
        score_median_20d=health.score_median_20d,
        score_mad_20d=health.score_mad_20d,
        anomaly_flag_active=health.anomaly_flag_active,
        drawdown_from_local_high=health.drawdown_from_local_high,
        suspended=health.suspended,
        limit_down_unsellable=health.limit_down_unsellable,
        corporate_action_unsafe=health.corporate_action_unsafe,
    )


def _lot_floored_volume(*, alloc_cents: int, price_cents: int) -> int:
    """Largest lot-multiple of shares affordable at ``alloc_cents`` (≥0)."""
    if alloc_cents <= 0 or price_cents <= 0:
        return 0
    raw = alloc_cents // price_cents
    return (raw // _LOT_SIZE) * _LOT_SIZE


def decide_day(
    *,
    signals: DailySignals,
    view: PortfolioView,
    bars: Mapping[str, DayBar],
    config: StrategyConfig,
) -> DayDecision:
    """Run selection + rotation for one day → the decided order intents (pure).

    SELL: the rotation engine's weakest-incumbent pick (when its dual-condition
    gate fires). BUY: fill open slots with the top shortlist codes not held,
    equal-weight sized and capped at the single-stock limit, lot-floored and
    bounded by available cash.
    """
    selection = config.selector.select(signals.quant_candidates)
    shortlist = selection.shortlist
    held_by_code = {h.code: h for h in view.holdings}
    quant_score = {c.code: c.score for c in signals.quant_candidates}

    sell_codes = _decide_sells(
        shortlist=shortlist,
        held_by_code=held_by_code,
        health=signals.health,
        config=config,
    )

    sold = set(sell_codes)
    held_after_sell = {c for c in held_by_code if c not in sold}
    # A code sold today is not re-bought today (no same-day churn); the freed
    # slot is filled by the rotation challenger, not the evicted incumbent.
    blocked_from_buy = held_after_sell | sold
    buy_codes = _decide_buys(
        shortlist=shortlist,
        blocked_from_buy=blocked_from_buy,
        open_slots=config.max_total_positions - len(held_after_sell),
        bars=bars,
        view=view,
        config=config,
    )

    orders = tuple(
        OrderIntent(code=c, side_is_buy=False, volume=held_by_code[c].volume)
        for c in sell_codes
    ) + tuple(
        OrderIntent(code=code, side_is_buy=True, volume=volume)
        for code, volume in buy_codes
    )
    return DayDecision(
        trade_date=signals.trade_date,
        orders=orders,
        shortlist=shortlist,
        sell_codes=sell_codes,
        buy_codes=tuple(code for code, _ in buy_codes),
        scores={code: quant_score[code] for code in shortlist if code in quant_score},
    )


def _decide_sells(
    *,
    shortlist: tuple[str, ...],
    held_by_code: Mapping[str, HeldPosition],
    health: Mapping[str, CodeHealth],
    config: StrategyConfig,
) -> tuple[str, ...]:
    incumbents = [
        _incumbent(code, held, health[code])
        for code, held in held_by_code.items()
        if code in health
    ]
    challengers = [
        ChallengerState(
            code=code,
            qualified=health[code].qualified,
            line1_percentile=health[code].line1_percentile,
            composite_score=health[code].composite_score,
        )
        for code in shortlist
        if code not in held_by_code and code in health
    ]
    if not incumbents or not challengers:
        return ()
    proposal = propose_rotation(incumbents, challengers, config.rotation)
    if proposal.should_rotate and proposal.incumbent_code is not None:
        return (proposal.incumbent_code,)
    return ()


def _decide_buys(
    *,
    shortlist: tuple[str, ...],
    blocked_from_buy: set[str],
    open_slots: int,
    bars: Mapping[str, DayBar],
    view: PortfolioView,
    config: StrategyConfig,
) -> tuple[tuple[str, int], ...]:
    if open_slots <= 0:
        return ()
    equal_weight = view.total_equity_cents // config.max_total_positions
    cap = view.total_equity_cents * config.single_stock_cap_percent // 100
    per_slot_cap = min(equal_weight, cap)
    cash_left = view.cash_cents
    buys: list[tuple[str, int]] = []
    for code in shortlist:
        if len(buys) >= open_slots:
            break
        if code in blocked_from_buy:
            continue
        bar = bars.get(code)
        if bar is None:
            continue
        alloc = min(per_slot_cap, cash_left)
        volume = _lot_floored_volume(alloc_cents=alloc, price_cents=bar.close_cents)
        if volume <= 0:
            continue
        buys.append((code, volume))
        cash_left -= volume * bar.close_cents
    return tuple(buys)


__all__ = [
    "CodeHealth",
    "DailySignals",
    "DayDecision",
    "HeldPosition",
    "OrderIntent",
    "PortfolioView",
    "ScoreProvider",
    "StrategyConfig",
    "decide_day",
]
