"""Deterministic backtest harness — the assembler (AE-004 §2.2 / §2.4).

Ties the clean-room engine pieces into one day-by-day replay:

    nautilus monotonic clock → zipline next-bar barrier → harsh-fill matching
    (limit gate + ADV cap + lot floor) → Lean friction → integer-分 accounting
    → closed-form invariants → anti-gaming stats → (optional) golden-vector
    oracle

It runs the deterministic daily strategy (:mod:`backend.backtest.strategy`:
selector + ≤5-slot rotation) over an injected :class:`BarSource` + a
:class:`ScoreProvider`, and returns a pure-data :class:`BacktestResult`. The
result carries exactly what the promotion judgement consumes — the equity
curve, daily returns, fill flow, anti-gaming observations and the
invariant verdict — but the harness does **not** import the
``strategy_evolution`` promotion engine (the ``[BACKTEST]`` allowlist forbids
all of strategy_evolution except ``harsh_fill_model``); the AE-005 dispatcher
maps :class:`BacktestResult` → ``PromotionInputs`` at the seam, keeping the
promotion judgement pure.

:func:`to_acceptance_report` is the optional mapper to a P0-6 ``AcceptanceReport``
(it synthesises ideal stability counters — a deterministic replay has no
live-execution failures — and the three strategy metrics). ``now`` is injected,
never wall-clock: the harness is pure, no ``datetime.now()`` anywhere.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from backend.backtest.event_loop import BacktestClock, BarSource, DayBar
from backend.backtest.friction import FrictionParams, compute_fill_economics
from backend.backtest.golden_vector import (
    DecisionVector,
    GoldenVectorResult,
    verify_decision_vectors,
)
from backend.backtest.invariants import (
    ExposureObservation,
    InvariantReport,
    check_invariants,
)
from backend.backtest.portfolio import (
    AppliedFill,
    BacktestPortfolio,
    EquitySnapshot,
    OpeningLot,
    PortfolioError,
)
from backend.backtest.strategy import (
    DailySignals,
    DayDecision,
    HeldPosition,
    OrderIntent,
    PortfolioView,
    ScoreProvider,
    StrategyConfig,
    decide_day,
)
from backend.strategy_evolution.harsh_fill_model import (
    HarshFillConfig,
    ShadowBar,
    ShadowOrder,
    simulate_harsh_fill,
)
from backend.utils.decision_compare import cents_to_yuan, to_cents

if TYPE_CHECKING:
    from backend.services.acceptance_report import AcceptanceReport


class DecideFn(Protocol):
    """The per-day decision hook ``run_backtest`` drives (default: ``decide_day``).

    An ablation study may wrap :func:`decide_day` (e.g. the MD-1 P-B stop
    overlay) without touching the shared decision logic; the default keeps
    every existing caller byte-identical.
    """

    def __call__(
        self,
        *,
        signals: DailySignals,
        view: PortfolioView,
        bars: Mapping[str, DayBar],
        config: StrategyConfig,
    ) -> DayDecision: ...

_TRADING_DAYS_PER_MONTH = 21.0
"""Approximate A-share trading days / month — turnover annualisation only."""


@dataclass(frozen=True)
class BacktestSpec:
    """Harness-level run parameters (integer 分; immutable)."""

    initial_capital_cents: int
    frozen_cash_cents: int = 0
    opening_positions: tuple[OpeningLot, ...] = ()


@dataclass(frozen=True)
class BacktestResult:
    """Pure-data backtest output (the promotion judgement's inputs).

    No ``PromotionInputs`` here by construction — that pydantic lives in
    ``strategy_evolution`` (off the ``[BACKTEST]`` allowlist); the dispatcher
    maps this at the seam.
    """

    trading_days: int
    equity_curve: tuple[EquitySnapshot, ...]
    daily_returns: tuple[float, ...]
    fills: tuple[AppliedFill, ...]
    fill_count: int
    decision_vectors: tuple[DecisionVector, ...]
    invariant_report: InvariantReport
    avg_exposure_ratio: float
    signal_count: int
    monthly_turnover: float
    initial_capital_cents: int
    final_equity_cents: int
    max_drawdown_pct: float
    pnl_cents: int
    golden_vector_result: GoldenVectorResult | None = None


def _close_marks_for_holdings(
    portfolio: BacktestPortfolio,
    bars: Mapping[str, DayBar],
    last_close: Mapping[str, int],
) -> dict[str, int]:
    """Closing marks for every holding; carry forward last close on a halt.

    A held code absent from today's bars (suspended / halted) is marked at its
    last known close — never dropped to zero. If it has no last close either,
    ``portfolio.mark`` fails closed (a data gap, surfaced not swallowed).
    """
    marks: dict[str, int] = {}
    for code in portfolio.holdings_snapshot():
        bar = bars.get(code)
        if bar is not None:
            marks[code] = bar.close_cents
        elif code in last_close:
            marks[code] = last_close[code]
    return marks


def _max_drawdown_pct(equity_cents: Sequence[int]) -> float:
    """Peak-to-trough drawdown as a positive ratio (0.0 when monotone up)."""
    peak = 0
    max_dd = 0.0
    for eq in equity_cents:
        peak = max(peak, eq)
        if peak <= 0:
            continue
        dd = (peak - eq) / peak
        max_dd = max(max_dd, dd)
    return max_dd


def run_backtest(
    *,
    spec: BacktestSpec,
    bar_source: BarSource,
    provider: ScoreProvider,
    strategy_config: StrategyConfig,
    friction_params: FrictionParams,
    harsh_config: HarshFillConfig | None = None,
    golden_vectors: Sequence[DecisionVector] | None = None,
    decide_fn: DecideFn = decide_day,
) -> BacktestResult:
    """Replay the deterministic strategy over the PIT window (pure).

    The zipline barrier: orders decided on day *T*'s close fill on day *T+1*'s
    open. No look-ahead is physically possible — the monotonic clock drives the
    loop and the injected sources are as-of.
    """
    days = bar_source.trading_days()
    clock = BacktestClock(tuple(days))
    portfolio = BacktestPortfolio(
        initial_cash_cents=spec.initial_capital_cents,
        frozen_cash_cents=spec.frozen_cash_cents,
        opening_positions=spec.opening_positions,
    )

    fills: list[AppliedFill] = []
    exposures: list[ExposureObservation] = []
    snapshots: list[EquitySnapshot] = []
    decisions: list[DecisionVector] = []
    entry_index: dict[str, int] = {lot.code: 0 for lot in spec.opening_positions}
    # Seed the carry-forward mark with each opening lot's cost basis so a
    # position suspended on the very first day (no day-0 bar, no prior close)
    # is still markable — otherwise portfolio.mark would fail closed on day 0.
    last_close: dict[str, int] = {
        lot.code: lot.cost_cents for lot in spec.opening_positions
    }
    pending: tuple[OrderIntent, ...] = ()
    signal_days = 0
    total_traded_cents = 0

    idx = 0
    day: str | None = clock.current_day
    while day is not None:
        clock.assert_readable(day)
        bars = bar_source.bars_on(day)

        buy_codes_today, traded = _fill_pending(
            pending=pending,
            bars=bars,
            day=day,
            portfolio=portfolio,
            friction_params=friction_params,
            harsh_config=harsh_config,
            fills=fills,
            entry_index=entry_index,
            current_index=idx,
        )
        total_traded_cents += traded

        _record_buy_exposures(
            buy_codes_today=buy_codes_today,
            last_close=last_close,
            portfolio=portfolio,
            frozen_cash_cents=spec.frozen_cash_cents,
            day=day,
            exposures=exposures,
        )

        snapshot = portfolio.mark(
            trade_date=day,
            close_marks_cents=_close_marks_for_holdings(portfolio, bars, last_close),
        )
        snapshots.append(snapshot)
        for code, bar in bars.items():
            last_close[code] = bar.close_cents

        holding_positions = tuple(
            HeldPosition(
                code=code,
                volume=volume,
                holding_age_trading_days=idx - entry_index.get(code, idx),
            )
            for code, volume in sorted(portfolio.holdings_snapshot().items())
        )
        view = PortfolioView(
            trade_date=day,
            total_equity_cents=snapshot.total_equity_cents,
            cash_cents=portfolio.cash_cents,
            holdings=holding_positions,
        )
        decision = decide_fn(
            signals=provider.signals_asof(day),
            view=view,
            bars=bars,
            config=strategy_config,
        )
        pending = decision.orders
        if decision.orders:
            signal_days += 1
        decisions.append(
            DecisionVector(
                trade_date=day,
                shortlist=decision.shortlist,
                sell_codes=decision.sell_codes,
                buy_codes=decision.buy_codes,
                scores=dict(decision.scores),
            )
        )

        idx += 1
        day = clock.advance()

    return _assemble_result(
        spec=spec,
        snapshots=snapshots,
        fills=fills,
        decisions=decisions,
        exposures=exposures,
        friction_params=friction_params,
        portfolio=portfolio,
        strategy_config=strategy_config,
        signal_days=signal_days,
        total_traded_cents=total_traded_cents,
        golden_vectors=golden_vectors,
    )


def _fill_pending(
    *,
    pending: Sequence[OrderIntent],
    bars: Mapping[str, DayBar],
    day: str,
    portfolio: BacktestPortfolio,
    friction_params: FrictionParams,
    harsh_config: HarshFillConfig | None,
    fills: list[AppliedFill],
    entry_index: dict[str, int],
    current_index: int,
) -> tuple[list[str], int]:
    """Fill the prior day's pending orders at this day's open (next-bar barrier).

    Returns ``(buy_codes_filled, traded_cents)``.
    """
    buy_codes: list[str] = []
    traded = 0
    for order in pending:
        bar = bars.get(order.code)
        if bar is None:
            continue  # not tradable today → the order lapses
        harsh = simulate_harsh_fill(
            ShadowOrder(
                side_is_buy=order.side_is_buy,
                volume=order.volume,
                reference_price=cents_to_yuan(bar.open_cents),
            ),
            ShadowBar(
                adv_volume=bar.adv_volume,
                limit_up=bar.at_limit_up,
                limit_down=bar.at_limit_down,
                quote_age_s=0.0,
            ),
            config=harsh_config,
        )
        if not harsh.filled:
            continue
        # harsh_fill prices in pure-Python float (IEEE-754, no numpy → NEP-50
        # cannot perturb it across versions); ``to_cents`` is the authoritative
        # round-half-to-even quantisation back to the integer-分 domain, so the
        # fill is platform-deterministic and a zero-impact fill round-trips to
        # the exact open price.
        fill_price_cents = to_cents(harsh.fill_price)
        econ = compute_fill_economics(
            side_is_buy=order.side_is_buy,
            order_price_cents=fill_price_cents,
            volume=harsh.filled_volume,
            board=bar.board,
            transfer_fee_applies=bar.transfer_fee_applies,
            params=friction_params,
            apply_board_slippage=False,
        )
        # A BUY that can no longer be afforded at the (post-impact) fill price
        # lapses — the cash-only MockBroker would reject it; without this gate a
        # close-priced basket sized before a gap-up open could drive cash
        # negative and the purely-algebraic cash invariant would not notice.
        if order.side_is_buy and econ.net_cents > portfolio.cash_cents:
            continue
        applied = AppliedFill(
            trade_date=day,
            code=order.code,
            side_is_buy=order.side_is_buy,
            volume=harsh.filled_volume,
            fill_price_cents=fill_price_cents,
            gross_cents=econ.gross_cents,
            commission_cents=econ.commission_cents,
            stamp_tax_cents=econ.stamp_tax_cents,
            transfer_fee_cents=econ.transfer_fee_cents,
            # The harsh model's adverse impact moved the fill away from the bar
            # open; record that as the slippage (econ's own slippage is 0 here
            # because board slippage is disabled — see compute_fill_economics).
            slippage_cents=abs(fill_price_cents - bar.open_cents) * harsh.filled_volume,
            net_cents=econ.net_cents,
            board=bar.board,
            transfer_fee_applies=bar.transfer_fee_applies,
        )
        try:
            portfolio.apply(applied)
        except PortfolioError:
            continue  # defensive: a SELL beyond held lapses, never mints shares
        fills.append(applied)
        traded += econ.gross_cents
        if order.side_is_buy:
            buy_codes.append(order.code)
            entry_index.setdefault(order.code, current_index)
        elif portfolio.held_volume(order.code) == 0:
            entry_index.pop(order.code, None)
    return buy_codes, traded


def _record_buy_exposures(
    *,
    buy_codes_today: Sequence[str],
    last_close: Mapping[str, int],
    portfolio: BacktestPortfolio,
    frozen_cash_cents: int,
    day: str,
    exposures: list[ExposureObservation],
) -> None:
    """Record the post-buy exposure context for each BUY filled today.

    Holdings are valued at the **decision-day close** (``last_close``, which
    still holds the prior day's closes at fill time — the prices the strategy
    actually sized against), NOT the next-day fill price. A gap-up open would
    otherwise inflate a position the strategy sized correctly within the cap and
    spuriously flip the run to DIVERGENT; the cap is an order-time sizing
    constraint, so it is verified against the order-time prices.
    """
    if not buy_codes_today:
        return
    holdings = portfolio.holdings_snapshot()
    valuation: dict[str, int] = {}
    for code in holdings:
        price = last_close.get(code)
        if price is None:
            continue  # un-priceable holding → excluded (no prior close yet)
        valuation[code] = price
    holdings_value = sum(
        valuation[code] * volume
        for code, volume in holdings.items()
        if code in valuation
    )
    equity = portfolio.cash_cents + frozen_cash_cents + holdings_value
    for code in buy_codes_today:
        if code not in valuation:
            continue
        exposures.append(
            ExposureObservation(
                trade_date=day,
                code=code,
                position_value_cents=valuation[code] * holdings[code],
                total_holdings_value_cents=holdings_value,
                total_equity_cents=equity,
            )
        )


def _assemble_result(
    *,
    spec: BacktestSpec,
    snapshots: Sequence[EquitySnapshot],
    fills: Sequence[AppliedFill],
    decisions: Sequence[DecisionVector],
    exposures: Sequence[ExposureObservation],
    friction_params: FrictionParams,
    portfolio: BacktestPortfolio,
    strategy_config: StrategyConfig,
    signal_days: int,
    total_traded_cents: int,
    golden_vectors: Sequence[DecisionVector] | None,
) -> BacktestResult:
    equity_cents = [s.total_equity_cents for s in snapshots]
    initial = spec.initial_capital_cents + spec.frozen_cash_cents
    final = equity_cents[-1] if equity_cents else initial

    daily_returns = _daily_returns(equity_cents, initial)
    avg_exposure = _avg_exposure_ratio(snapshots)
    monthly_turnover = _monthly_turnover(
        total_traded_cents=total_traded_cents,
        snapshots=snapshots,
    )
    final_positions = tuple(sorted(portfolio.holdings_snapshot().items()))
    invariant_report = check_invariants(
        initial_cash_cents=spec.initial_capital_cents,
        fills=fills,
        final_cash_cents=portfolio.cash_cents,
        opening_positions=spec.opening_positions,
        final_positions=final_positions,
        params=friction_params,
        exposures=exposures,
        single_stock_cap_percent=strategy_config.single_stock_cap_percent,
    )
    golden_result = (
        verify_decision_vectors(tuple(decisions), tuple(golden_vectors))
        if golden_vectors is not None
        else None
    )
    return BacktestResult(
        trading_days=len(snapshots),
        equity_curve=tuple(snapshots),
        daily_returns=daily_returns,
        fills=tuple(fills),
        fill_count=len(fills),
        decision_vectors=tuple(decisions),
        invariant_report=invariant_report,
        avg_exposure_ratio=avg_exposure,
        signal_count=signal_days,
        monthly_turnover=monthly_turnover,
        initial_capital_cents=initial,
        final_equity_cents=final,
        max_drawdown_pct=_max_drawdown_pct(equity_cents),
        pnl_cents=final - initial,
        golden_vector_result=golden_result,
    )


def _daily_returns(equity_cents: Sequence[int], initial: int) -> tuple[float, ...]:
    out: list[float] = []
    prev = initial
    for eq in equity_cents:
        out.append((eq - prev) / prev if prev > 0 else 0.0)
        prev = eq
    return tuple(out)


def _avg_exposure_ratio(snapshots: Sequence[EquitySnapshot]) -> float:
    ratios = [
        s.market_value_cents / s.total_equity_cents
        for s in snapshots
        if s.total_equity_cents > 0
    ]
    return sum(ratios) / len(ratios) if ratios else 0.0


def _monthly_turnover(
    *, total_traded_cents: int, snapshots: Sequence[EquitySnapshot]
) -> float:
    equities = [s.total_equity_cents for s in snapshots if s.total_equity_cents > 0]
    if not equities:
        return 0.0
    avg_equity = sum(equities) / len(equities)
    months = len(snapshots) / _TRADING_DAYS_PER_MONTH
    if avg_equity <= 0 or months <= 0:
        return 0.0
    return total_traded_cents / avg_equity / months


def to_acceptance_report(
    result: BacktestResult,
    *,
    now: dt.datetime,
    benchmark_total_return: float = 0.0,
) -> AcceptanceReport:
    """Map a :class:`BacktestResult` → a P0-6 ``AcceptanceReport`` (pure).

    Stability counters are *ideal* (a deterministic replay has no instruction /
    report / data / LLM failures) so the gate's signal comes from the three
    strategy metrics. ``now`` is injected (no wall-clock). ``benchmark_total_return``
    is the CSI300 cumulative return over the same window (0.0 ⇒ excess == the
    portfolio's own return). A window shorter than 45 trading days yields
    ``INSUFFICIENT_DATA`` by the service's own arithmetic.
    """
    from backend.services.acceptance_report import (
        AcceptanceComputeInput,
        AcceptanceService,
        StabilityCounters,
        StrategyCounters,
    )

    portfolio_return = (
        result.pnl_cents / result.initial_capital_cents
        if result.initial_capital_cents > 0
        else 0.0
    )
    last_day = result.equity_curve[-1].trade_date if result.equity_curve else None
    trade_date = (
        dt.date(int(last_day[:4]), int(last_day[4:6]), int(last_day[6:8]))
        if last_day is not None
        else now.date()
    )
    days = max(result.signal_count, result.trading_days)
    stability = StabilityCounters(
        completed_instructions=result.fill_count,
        total_instructions=result.fill_count,
        accurate_reports=result.fill_count,
        total_reports=result.fill_count,
        data_missing_ticks=0,
        total_data_ticks=max(result.trading_days, 1),
        llm_timeout_calls=0,
        total_llm_calls=max(days, 1),
        generated_signal_days=result.trading_days,
        expected_signal_days=max(result.trading_days, 1),
    )
    strategy = StrategyCounters(
        max_drawdown_pct=result.max_drawdown_pct,
        pnl_cny=result.pnl_cents / 100.0,
        csi300_excess_pct=portfolio_return - benchmark_total_return,
    )
    payload = AcceptanceComputeInput(
        trade_date=trade_date,
        now=now,
        stability=stability,
        strategy=strategy,
    )
    return AcceptanceService().compute(payload)


__all__ = [
    "BacktestResult",
    "BacktestSpec",
    "DecideFn",
    "run_backtest",
    "to_acceptance_report",
]
