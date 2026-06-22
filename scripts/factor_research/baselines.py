"""Baseline panel — the bar a candidate gate must clear (QGR-2 build-new ⑥).

A long-only basket in a strong-beta year can "look profitable" with no skill
(QGR plan §2.2 / §4.1, codex P1). A candidate gate is only credible if it
**stably beats** a panel of deployable, skill-free baselines:

* **random_top5** — a random (no-skill) signal run through the real ≤5-slot
  rotation mechanics. NOTE: the live rotation is sticky (it only churns an
  independently-weak incumbent), so with default health this fills with a random
  basket and largely *holds* it — a real beta hurdle, but not literally
  re-sampled each rebalance. A fully-rebalanced random basket (with turnover) is
  a QGR-4 refinement once the factor panel + per-rebalance health are wired.
* **live_momentum_0p40** — the live screener's hand-set ``ret_20d`` 0.40 bet.
* **pure_liquidity** — rank purely by turnover/liquidity (no edge factor).
* **etf_only_510300** — hold the CSI300 ETF inside the ≤5-slot mechanics.
* **csi300_etf_hold** — buy-and-hold the CSI300 ETF (the beta itself).

Each baseline is realized as a :class:`ScoreProvider` and run through the *same*
:func:`run_gate_backtest` arena, so its net P&L / MDD / turnover are measured on
identical mechanics; the candidate's per-period returns are then compared against
each baseline via :mod:`multi_strategy_compare`. The two factor-driven baselines
(``live_momentum_0p40`` / ``pure_liquidity``) are wired with QGR-3 factor scores
through :class:`PanelScoreProvider`; the three skill-free ones run with no factor
panel (here). Deterministic (seeded hash scores); offline; never the live path.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.backtest.event_loop import BarSource, DayBar
from backend.backtest.friction import (
    FrictionParams,
    apply_board_slippage_cents,
    compute_fill_economics,
)

from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    default_friction,
    default_strategy_config,
    run_gate_backtest,
)

DEFAULT_TOP_N = 5
_LOT = 100
ScoreTable = dict[str, list[tuple[str, float]]]
BaselineResult = "GateBacktestResult | BuyAndHoldResult"


@dataclass(frozen=True)
class BaselineSpec:
    """One baseline's identity + whether it needs the QGR-3 factor panel."""

    name: str
    needs_factor_panel: bool
    description: str


# The codex-P1 baseline set (names frozen for the comparison family).
BASELINE_PANEL: tuple[BaselineSpec, ...] = (
    BaselineSpec(
        "random_top5", False,
        "random no-skill signal through ≤5-slot mechanics (held basket; "
        "fully-rebalanced variant is a QGR-4 refinement)",
    ),
    BaselineSpec("live_momentum_0p40", True, "live screener ret_20d 0.40 weight"),
    BaselineSpec("pure_liquidity", True, "rank by turnover/liquidity only"),
    BaselineSpec("etf_only_510300", False, "hold the CSI300 ETF in ≤5-slot mechanics"),
    BaselineSpec("csi300_etf_hold", False, "buy-and-hold the CSI300 ETF (beta)"),
)


def _hash_score(seed: int, day: str, code: str) -> float:
    """Deterministic pseudo-random score in [0, 1) from (seed, day, code)."""
    digest = hashlib.sha256(f"{seed}|{day}|{code}".encode()).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000


def random_top_n_scores(
    universe_by_day: Mapping[str, Sequence[str]],
    *,
    seed: int,
    top_n: int = DEFAULT_TOP_N,
) -> ScoreTable:
    """Deterministic seeded random scores (the selector then takes the top-N)."""
    out: ScoreTable = {}
    for day, codes in universe_by_day.items():
        scored = [(c, _hash_score(seed, day, c)) for c in codes]
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        out[day] = scored if top_n <= 0 else scored[:top_n]
    return out


def single_asset_scores(
    days: Sequence[str], asset_code: str
) -> ScoreTable:
    """Score one asset high every day (the ETF-only ≤5-slot baseline)."""
    return {day: [(asset_code, 1.0)] for day in days}


@dataclass(frozen=True)
class BuyAndHoldResult:
    """Full-invested buy-and-hold beta — the hardest baseline (codex P1).

    Computed directly from the asset's bars (NOT the ≤5-slot capped gate, since
    buy-and-hold beta is not a slot strategy): buy as many lots as the capital
    affords at the first bar's open, hold to the last close. Conservation holds
    by construction (pure integer-分 arithmetic)."""

    trading_days: int
    initial_capital_yuan: float
    final_equity_yuan: float
    net_pnl_yuan: float
    total_return: float
    max_drawdown_pct: float
    fill_count: int
    horizon: int
    invested_fraction: float
    daily_returns: tuple[float, ...]
    period_returns: tuple[float, ...]
    conservation_ok: bool = True
    exposure_cap_violations: int = 0


def _chunk_compound(daily_returns: Sequence[float], horizon: int) -> tuple[float, ...]:
    h = max(1, horizon)
    out: list[float] = []
    for j in range(len(daily_returns) // h):
        comp = 1.0
        for r in daily_returns[j * h : (j + 1) * h]:
            comp *= 1.0 + r
        out.append(comp - 1.0)
    return tuple(out)


def _max_drawdown(equity_cents: Sequence[int], initial: int) -> float:
    peak = initial
    mdd = 0.0
    for eq in equity_cents:
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd


def buy_and_hold_baseline(
    *,
    bar_source: BarSource,
    asset_code: str,
    initial_capital_yuan: float,
    horizon: int = 5,
    friction: FrictionParams | None = None,
) -> BuyAndHoldResult:
    """Full-invested buy-and-hold of ``asset_code`` over the window (beta hurdle)."""
    fr = friction or default_friction()
    days = list(bar_source.trading_days())
    initial = round(initial_capital_yuan * 100)
    buy_idx, buy_bar = _first_bar(bar_source, days, asset_code)
    shares, cash = _size_full_buy(buy_bar, initial, fr) if buy_bar else (0, initial)

    equity_cents: list[int] = []
    last_close = buy_bar.open_cents if buy_bar else 0
    for i, day in enumerate(days):
        if i < buy_idx or buy_bar is None:
            equity_cents.append(initial)
            continue
        bar = bar_source.bars_on(day).get(asset_code)
        if bar is not None:
            last_close = bar.close_cents
        equity_cents.append(cash + shares * last_close)

    final = equity_cents[-1] if equity_cents else initial
    daily = _daily_returns(equity_cents, initial)
    invested = (shares * buy_bar.open_cents / initial) if buy_bar and initial else 0.0
    return BuyAndHoldResult(
        trading_days=len(days),
        initial_capital_yuan=initial / 100.0,
        final_equity_yuan=final / 100.0,
        net_pnl_yuan=(final - initial) / 100.0,
        total_return=(final - initial) / initial if initial else 0.0,
        max_drawdown_pct=_max_drawdown(equity_cents, initial),
        fill_count=1 if shares > 0 else 0,
        horizon=horizon,
        invested_fraction=invested,
        daily_returns=daily,
        period_returns=_chunk_compound(daily, horizon),
    )


def _first_bar(
    bar_source: BarSource, days: Sequence[str], asset_code: str
) -> tuple[int, DayBar | None]:
    """First bar whose open is tradable for a BUY (not at the upper limit).

    A limit-up open is unfillable for a BUY in the event-loop mechanics
    (``DayBar.at_limit_up``); the buy-and-hold entry must honour the same
    tradability constraint so the baseline hurdle is measured on equal terms.
    """
    for i, day in enumerate(days):
        bar = bar_source.bars_on(day).get(asset_code)
        if bar is not None and not bar.at_limit_up:
            return i, bar
    return len(days), None


def _size_full_buy(
    bar: DayBar, initial: int, fr: FrictionParams
) -> tuple[int, int]:
    """Largest lot-multiple affordable at the open (incl. friction); ≤ initial."""
    fill_px = apply_board_slippage_cents(
        order_price_cents=bar.open_cents, side_is_buy=True,
        slippage_bps=fr.slippage_bps(bar.board),
    )
    shares = (initial // fill_px) // _LOT * _LOT
    while shares > 0:
        econ = compute_fill_economics(
            side_is_buy=True, order_price_cents=bar.open_cents, volume=shares,
            board=bar.board, transfer_fee_applies=bar.transfer_fee_applies,
            params=fr, apply_board_slippage=True,
        )
        if econ.net_cents <= initial:
            return shares, initial - econ.net_cents
        shares -= _LOT
    return 0, initial


def _daily_returns(equity_cents: Sequence[int], initial: int) -> tuple[float, ...]:
    out: list[float] = []
    prev = initial
    for eq in equity_cents:
        out.append((eq - prev) / prev if prev > 0 else 0.0)
        prev = eq
    return tuple(out)


def run_baselines(
    *,
    bar_source: BarSource,
    universe_by_day: Mapping[str, Sequence[str]],
    etf_code: str,
    initial_capital_yuan: float,
    horizon: int = 5,
    seed: int = 20260622,
) -> dict[str, GateBacktestResult | BuyAndHoldResult]:
    """Run the no-factor baselines → ``{name: result}``.

    ``random_top5`` / ``etf_only_510300`` run through the ≤5-slot gate mechanics;
    ``csi300_etf_hold`` is a full-invested buy-and-hold (the beta hurdle), NOT the
    capped slot. The factor-driven baselines (``live_momentum_0p40`` /
    ``pure_liquidity``) are NOT run here — they need the QGR-3 factor panel; the
    caller runs them via :class:`PanelScoreProvider` + ``run_gate_backtest``.
    """
    days = sorted(universe_by_day)
    config = default_strategy_config()
    gate_providers: dict[str, PanelScoreProvider] = {
        "random_top5": PanelScoreProvider(
            random_top_n_scores(universe_by_day, seed=seed)
        ),
        "etf_only_510300": PanelScoreProvider(single_asset_scores(days, etf_code)),
    }
    out: dict[str, GateBacktestResult | BuyAndHoldResult] = {
        name: run_gate_backtest(
            bar_source=bar_source,
            provider=provider,
            strategy_config=config,
            initial_capital_yuan=initial_capital_yuan,
            horizon=horizon,
        )
        for name, provider in gate_providers.items()
    }
    out["csi300_etf_hold"] = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=etf_code,
        initial_capital_yuan=initial_capital_yuan,
        horizon=horizon,
    )
    return out


__all__ = [
    "BASELINE_PANEL",
    "DEFAULT_TOP_N",
    "BaselineSpec",
    "BuyAndHoldResult",
    "buy_and_hold_baseline",
    "random_top_n_scores",
    "run_baselines",
    "single_asset_scores",
]
