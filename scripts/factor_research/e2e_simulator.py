"""End-to-end research simulator (C0b) — the deterministic EXIT/do-T twin.

WHY this exists (plan ``misty-doodling-pnueli`` §A3 + outline §6): the live
system's real avoid-top SELL is the **Line-2 monitoring path** (a direct SELL of
a held position on a deterministic trigger), but the frozen
:func:`backend.backtest.harness.run_backtest` event loop has **only one** SELL
path — ``propose_rotation`` (≤1 rotation/day, needs a challenger). B1/B2 routed
EXIT *through* rotation via health overrides and it "barely bit"; that is the
structural wall. To evaluate avoid-top / dynamic-exit / do-T faithfully we add a
**research-side direct-SELL/do-T overlay** that emits extra
:class:`~backend.backtest.strategy.OrderIntent` s appended to the day's pending
orders — they then fill on the next bar through the *exact same frozen barrier*
(T+1 / harsh-fill / limit-down veto / friction / integer-分 accounting).

The simulator **re-uses the frozen harness internals by import** (``_fill_pending``
/ ``_record_buy_exposures`` / ``_close_marks_for_holdings`` / ``_assemble_result``)
rather than re-implementing them, so the plumbing is byte-identical to the frozen
engine *by construction* and tracks it if it changes. The load-bearing guard is
the **overlay-disabled ≡ frozen engine byte-exact invariant** (codex R1-#1):
``run_e2e_backtest`` with a no-op overlay must field-equal ``run_backtest`` — the
overlay is then a *pure addition* on top of validated machinery, never a hidden
re-write of the baseline.

Execution semantics are pinned by the frozen EXIT execution contract
(``docs/research/qgr-c0-exit-execution-contract-2026-06-27.md`` / §A3.1): signal
as-of close T → order T+1 → un-fillable (limit-down/suspend) lapses and the
overlay re-emits next day (queue; trapped MTM accrues via continued marking) →
mandatory stop hard-triggers without waiting for confirmation (P-B). Offline,
pure, deterministic; never touches the live path; no ``backend.{llm,agents,
mirofish,risk}`` imports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from backend.backtest.event_loop import BacktestClock, BarSource, DayBar
from backend.backtest.friction import FrictionParams
from backend.backtest.golden_vector import DecisionVector
from backend.backtest.harness import (
    BacktestResult,
    BacktestSpec,
    _assemble_result,
    _close_marks_for_holdings,
    _fill_pending,
    _record_buy_exposures,
)
from backend.backtest.invariants import ExposureObservation
from backend.backtest.portfolio import AppliedFill, BacktestPortfolio, EquitySnapshot
from backend.backtest.strategy import (
    HeldPosition,
    OrderIntent,
    PortfolioView,
    ScoreProvider,
    StrategyConfig,
    decide_day,
)
from backend.strategy_evolution.harsh_fill_model import HarshFillConfig

_LOT_SIZE = 100
"""A-share minimum trading lot (mirrors ``strategy._LOT_SIZE`` / RiskConfig)."""


@dataclass(frozen=True)
class ExitExecutionContract:
    """Frozen EXIT/do-T execution semantics (the *plumbing*, not the signal).

    Pins HOW a SELL/do-T order flows so later signal work (C1/C3) cannot mine
    P&L by quietly changing execution assumptions (codex R1-#2). Any change here
    is an amendment + a non-zeroing-ledger debit. ``contract_id`` is a
    deterministic SHA256 over the sorted fields (no wall-clock / no RNG).
    """

    lot_size: int = _LOT_SIZE
    """EXIT/do-T volumes are floored to whole lots (mirrors live sizing)."""
    queue_unfilled_exits: bool = True
    """An un-fillable EXIT (limit-down/suspend) is re-emitted next day, never
    dropped; trapped MTM accrues because the position stays marked (codex R1-#8)."""
    mandatory_stop_bypasses_confirmation: bool = True
    """P-B: a hard stop fires immediately; only entry / take-profit EXIT waits
    for the P-A confirmation gate."""
    do_t_requires_settled_t1: bool = True
    """Do-T high-sell only touches already-settled (prior-cycle) inventory; the
    do-T low-buy lot is sellable only the next day (守 T+1)."""
    do_t_requires_positive_unrealized: bool = True
    """P-C: do-T only on a position with positive unrealized P&L — never to
    average down a loser."""
    reentry_lock: bool = True
    """After an EXIT, the name is not re-bought immediately; re-entry must clear
    the P-A confirmation gate + a re-entry lock window (window param set in C1)."""

    @property
    def contract_id(self) -> str:
        """Deterministic content hash of the frozen execution semantics."""
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class HeldContext:
    """Per-held-position context the overlay needs (cost basis + mark + age).

    Cost basis and the current close mark come from the day's
    :class:`EquitySnapshot` (``PositionMark``), so the overlay can compute
    unrealized P&L (do-T profit-gate / stop) without re-threading prices.
    """

    code: str
    volume: int
    cost_cents: int
    market_value_cents: int
    holding_age_trading_days: int

    @property
    def unrealized_pnl_cents(self) -> int:
        """Mark-to-market minus cost basis for the whole lot (signed)."""
        return self.market_value_cents - self.cost_cents * self.volume


@dataclass(frozen=True)
class ExitOverlayContext:
    """Everything a deterministic EXIT/do-T overlay sees on day *T*'s close.

    The overlay decides on this (close-T) view and returns
    :class:`OrderIntent` s that fill on *T+1* (contract §1). ``bars`` is today's
    bars only (look-ahead-free); a stateful overlay accumulates history itself.
    """

    day: str
    current_index: int
    view: PortfolioView
    bars: Mapping[str, DayBar]
    rotation_decision_orders: tuple[OrderIntent, ...]
    held: tuple[HeldContext, ...]

    @property
    def held_by_code(self) -> dict[str, HeldContext]:
        return {h.code: h for h in self.held}


@runtime_checkable
class ExitOverlay(Protocol):
    """Deterministic EXIT/do-T overlay (zero LLM; research-side).

    ``orders_for_day`` returns extra SELL (avoid-top / stop / do-T high) and/or
    BUY (do-T low) intents to append to the day's pending orders. Returns ``()``
    when nothing fires — then the loop is byte-identical to ``run_backtest``.
    Implementations are stateful (accumulate bar history, track queued exits /
    re-entry locks) and MUST honour the :class:`ExitExecutionContract`.
    """

    def orders_for_day(self, ctx: ExitOverlayContext) -> tuple[OrderIntent, ...]: ...


class NoOpExitOverlay:
    """The disabled overlay — emits nothing (byte-exact-invariant reference)."""

    def orders_for_day(self, ctx: ExitOverlayContext) -> tuple[OrderIntent, ...]:
        return ()


@dataclass(frozen=True)
class OverlayOrderRecord:
    """One order the overlay emitted on a day (for signal-hit vs fillable-hit)."""

    trade_date: str
    code: str
    side_is_buy: bool
    volume: int


@dataclass(frozen=True)
class E2ERunResult:
    """Result of an end-to-end run: the frozen-engine result + overlay trace.

    ``backtest_result`` is the exact :class:`BacktestResult` type the frozen
    engine returns, so the byte-exact invariant compares it field-by-field.
    ``overlay_orders`` is what the overlay *signalled* (reconcile against
    ``backtest_result.fills`` for fillable-hit accounting, contract §2).
    """

    backtest_result: BacktestResult
    overlay_orders: tuple[OverlayOrderRecord, ...]
    contract_id: str
    overlay_sell_signals: int
    overlay_buy_signals: int


def _held_context(
    *,
    snapshot: EquitySnapshot,
    entry_index: Mapping[str, int],
    current_index: int,
) -> tuple[HeldContext, ...]:
    """Build per-held context from the close-marked snapshot (cost + mark + age)."""
    out: list[HeldContext] = []
    for pos in snapshot.positions:
        out.append(
            HeldContext(
                code=pos.code,
                volume=pos.volume,
                cost_cents=pos.cost_cents,
                market_value_cents=pos.market_value_cents,
                holding_age_trading_days=current_index - entry_index.get(
                    pos.code, current_index
                ),
            )
        )
    return tuple(out)


def _merge_pending(
    *,
    rotation_orders: tuple[OrderIntent, ...],
    overlay_orders: tuple[OrderIntent, ...],
) -> tuple[OrderIntent, ...]:
    """Append overlay orders to the rotation orders, rotation taking precedence.

    A code already ordered by ``decide_day`` (rotation SELL or BUY) keeps that
    order; the overlay order for that code is dropped (avoids double-selling /
    contradictory same-day orders). With an empty overlay this returns
    ``rotation_orders`` unchanged — the byte-exact path. C1 refines precedence
    (e.g. a P-B mandatory stop pre-empting a rotation BUY) under amendment.
    """
    if not overlay_orders:
        return rotation_orders
    claimed = {o.code for o in rotation_orders}
    extra = tuple(o for o in overlay_orders if o.code not in claimed)
    return rotation_orders + extra


def run_e2e_backtest(
    *,
    spec: BacktestSpec,
    bar_source: BarSource,
    provider: ScoreProvider,
    strategy_config: StrategyConfig,
    friction_params: FrictionParams,
    exit_overlay: ExitOverlay | None = None,
    contract: ExitExecutionContract | None = None,
    harsh_config: HarshFillConfig | None = None,
) -> E2ERunResult:
    """Replay the strategy + EXIT/do-T overlay over the PIT window (pure).

    Re-hosts ``run_backtest``'s daily loop (re-using its frozen internals) with
    one added seam: after ``decide_day`` produces the rotation orders, the
    overlay appends EXIT/do-T orders, which fill on the next bar through the
    same frozen barrier. With ``exit_overlay=None`` (or a no-op overlay) the
    result's ``backtest_result`` is byte-identical to ``run_backtest`` — the
    overlay-disabled ≡ frozen-engine invariant (codex R1-#1).
    """
    overlay = exit_overlay if exit_overlay is not None else NoOpExitOverlay()
    contract = contract if contract is not None else ExitExecutionContract()

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
    last_close: dict[str, int] = {
        lot.code: lot.cost_cents for lot in spec.opening_positions
    }
    pending: tuple[OrderIntent, ...] = ()
    overlay_records: list[OverlayOrderRecord] = []
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
        decision = decide_day(
            signals=provider.signals_asof(day),
            view=view,
            bars=bars,
            config=strategy_config,
        )

        overlay_orders = overlay.orders_for_day(
            ExitOverlayContext(
                day=day,
                current_index=idx,
                view=view,
                bars=bars,
                rotation_decision_orders=decision.orders,
                held=_held_context(
                    snapshot=snapshot, entry_index=entry_index, current_index=idx
                ),
            )
        )
        for o in overlay_orders:
            overlay_records.append(
                OverlayOrderRecord(
                    trade_date=day,
                    code=o.code,
                    side_is_buy=o.side_is_buy,
                    volume=o.volume,
                )
            )
        pending = _merge_pending(
            rotation_orders=decision.orders, overlay_orders=overlay_orders
        )
        if pending:
            signal_days += 1

        # DecisionVector mirrors the frozen loop's trace (rotation-only fields);
        # overlay orders are traced separately in ``overlay_records`` so the
        # frozen golden-vector oracle stays comparable.
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

    backtest_result = _assemble_result(
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
        golden_vectors=None,
    )
    return E2ERunResult(
        backtest_result=backtest_result,
        overlay_orders=tuple(overlay_records),
        contract_id=contract.contract_id,
        overlay_sell_signals=sum(1 for r in overlay_records if not r.side_is_buy),
        overlay_buy_signals=sum(1 for r in overlay_records if r.side_is_buy),
    )


__all__ = [
    "E2ERunResult",
    "ExitExecutionContract",
    "ExitOverlay",
    "ExitOverlayContext",
    "HeldContext",
    "NoOpExitOverlay",
    "OverlayOrderRecord",
    "run_e2e_backtest",
]
