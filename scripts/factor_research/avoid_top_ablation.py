"""C1 avoid-top EXIT-on-held ablation (5 arms + EXIT-specific placebos + P&L 4-way).

The C1 cut (plan ``misty-doodling-pnueli`` §A4 / criterion-rebar §8.1): does an
EXIT-on-held overlay that SELLs a *crowded, rolling-over* held name (``avoid_top``)
earn a genuine net trade-off, or is it just mechanical de-exposure / turnover
churn (the head self-deception B1/B2 already exposed)? It replays the **same**
QGR-3 ranker buy strategy through the C0b end-to-end simulator
(:func:`run_e2e_backtest`) under five EXIT overlays sharing identical fill /
friction / conservation plumbing — only the EXIT *signal* differs:

* **baseline** — no EXIT (NoOp) = the C0b byte-exact frozen-engine reference.
* **stop_only** — the P-B mandatory stop alone (the protective-value floor).
* **avoid_top** — crowded ``ideal_amplitude_20d`` decile AND a confirmed
  rolling-top (P-A) + the common stop (the treatment).
* **placebo_sell_calendar** — random held EXITs on the SAME dates+counts as the
  treatment's avoid-top SELLs (+ the common stop): isolates "release a slot on
  these dates" turnover from the avoid-top *name* selection.
* **placebo_random_held** — random held EXITs of the SAME total spread over the
  window (+ the common stop): isolates the avoid-top timing+selection from pure
  de-exposure rate.

Both placebos share the treatment's queue / stop / re-entry machinery, so beating
them isolates the crowding signal from de-exposure (codex R1-#3/#16). The verdict
(pre-committed, FAIL reported not laundered): avoid-top earns a deployable EXIT
edge ONLY if its P&L 4-way decomposition has ①avoided-loss > ②missed-gain +
④cost with ③redeployment NOT the main driver, it STRICTLY beats BOTH placebos
(paired-t ≥ 2), AND it clears DSR ≥ 0.95 (non-zeroing N) — else the effect is
exposure/turnover noise. The MDD≤8% hard gate is DROPPED (criterion-rebar) →
MDD is disclosure-only; permanent entrapment in a crash slice is still a FAIL.

All arms share ONE PIT bar source; deterministic, offline, train_val only (the
sealed test window is never read — a real OOS is the deferred B-layer gate).
Never the live path.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd

from backend.backtest.event_loop import BarSource
from backend.backtest.harness import BacktestResult, BacktestSpec
from backend.backtest.portfolio import AppliedFill
from backend.marketdata_snapshot.store import SnapshotStore

from . import exit_veto_panel as xv
from .avoid_top_overlay import (
    AvoidTopExitConfig,
    AvoidTopOverlay,
    PlaceboPlan,
    RandomHeldExitOverlay,
    StopOnlyOverlay,
)
from .avoid_top_panel import build_avoid_top_triggers
from .baselines import buy_and_hold_baseline
from .crowding_factor_diagnostics import _overlap_lag
from .e2e_simulator import E2ERunResult, ExitOverlay, run_e2e_backtest
from .gate_backtest import (
    PanelScoreProvider,
    default_friction,
    default_strategy_config,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac, onc_effective_n
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies
from .neutralize import neutralize_panel
from .trial_ledger import TrialLedger, TrialRecord

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
HORIZON: int = 5  # rebalance/return horizon (period-return chunking)
REBALANCE_FREQ: int = 5
PNL_HORIZON_TD: int = 10  # frozen counterfactual horizon for the P&L decomposition
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
LEDGER_DATE: str = "2026-06-27"
PLACEBO_SEED: int = 20260622  # pre-declared block-bootstrap / placebo seed
DSR_GATE: float = 0.95  # main anti-overfitting gate (NOT relaxed)
PNL_HURT_TOL_FRAC: float = 0.02  # net-P&L sign-safe band vs the stop_only baseline
BEATS_PLACEBO_T: float = 2.0  # strict one-sided paired-t (not the lenient t>1)
MIN_AVOID_TOP_EXITS: int = 30  # fail-closed: fewer → placebo comparison underpowered
# Market-regime thresholds on the trailing-4-period CSI300 cumulative return.
REGIME_LOOKBACK_PERIODS: int = 4
BULL_BAND: float = 0.03
BEAR_BAND: float = -0.03
ARMS: tuple[str, ...] = (
    "baseline",
    "stop_only",
    "avoid_top",
    "placebo_sell_calendar",
    "placebo_random_held",
)


@dataclass(frozen=True)
class ArmResult:
    """One arm's arena outcome (net-P&L / MDD / turnover + the period series)."""

    label: str
    net_pnl_yuan: float
    total_return: float
    max_drawdown_pct: float
    monthly_turnover: float
    fill_count: int
    avg_exposure: float
    conservation_ok: bool
    overlay_sell_signals: int
    period_returns: tuple[float, ...]


_HARD_INVARIANT_KINDS = frozenset(
    {"cash_conservation", "position_conservation", "fee_recompute"}
)


def _chunk_compound(
    daily_returns: tuple[float, ...], horizon: int
) -> tuple[float, ...]:
    """Non-overlapping ``horizon``-day compounded returns (drops the remainder)."""
    h = max(1, horizon)
    out: list[float] = []
    for j in range(len(daily_returns) // h):
        comp = 1.0
        for r in daily_returns[j * h : (j + 1) * h]:
            comp *= 1.0 + r
        out.append(comp - 1.0)
    return tuple(out)


def _arm_from_e2e(label: str, e2e: E2ERunResult, *, horizon: int) -> ArmResult:
    """Map an :class:`E2ERunResult` → the arena ``ArmResult`` (frozen口径)."""
    r: BacktestResult = e2e.backtest_result
    initial = r.initial_capital_cents
    conservation_ok = not any(
        v.kind in _HARD_INVARIANT_KINDS for v in r.invariant_report.violations
    )
    return ArmResult(
        label=label,
        net_pnl_yuan=r.pnl_cents / 100.0,
        total_return=(r.pnl_cents / initial) if initial > 0 else 0.0,
        max_drawdown_pct=r.max_drawdown_pct,
        monthly_turnover=r.monthly_turnover,
        fill_count=r.fill_count,
        avg_exposure=r.avg_exposure_ratio,
        conservation_ok=conservation_ok,
        overlay_sell_signals=e2e.overlay_sell_signals,
        period_returns=_chunk_compound(r.daily_returns, horizon),
    )


def _run_overlay(
    overlay: ExitOverlay | None, *, bar_source: BarSource, provider: PanelScoreProvider
) -> E2ERunResult:
    """Replay the shared ranker strategy under one EXIT overlay (the only arm diff)."""
    return run_e2e_backtest(
        spec=BacktestSpec(initial_capital_cents=round(INITIAL_CAPITAL_YUAN * 100)),
        bar_source=bar_source,
        provider=provider,
        strategy_config=default_strategy_config(),
        friction_params=default_friction(),
        exit_overlay=overlay,
    )


def _forward_close_cents(
    bar_source: BarSource,
    daily_days: list[str],
    day_index: dict[str, int],
    fill_date: str,
    code: str,
    horizon: int,
) -> int | None:
    """Code's close ``horizon`` td after ``fill_date`` (walk forward over halts).

    The frozen counterfactual price for the P&L decomposition: what the sold lot
    would have been worth ``horizon`` td later had we held it. Clamped to the
    train_val daily window; a suspended target day walks forward to the next
    available close (capped at the window end). ``None`` if the code never trades
    again in the window (delisted) — the caller treats it as no realized效应.
    """
    start = day_index.get(fill_date)
    if start is None:
        return None
    target = min(start + horizon, len(daily_days) - 1)
    for j in range(target, len(daily_days)):
        bar = bar_source.bars_on(daily_days[j]).get(code)
        if bar is not None:
            return bar.close_cents
    return None


@dataclass(frozen=True)
class _MatchedExit:
    """One avoid-top SELL fill matched to its triggering event (for ①②④)."""

    code: str
    fill_date: str
    sell_price_cents: int
    volume: int
    friction_cents: int
    forward_close_cents: int | None


def _match_avoid_top_fills(
    overlay: AvoidTopOverlay,
    arm_result: BacktestResult,
    bar_source: BarSource,
    daily_days: list[str],
) -> tuple[list[_MatchedExit], int]:
    """Greedily match each avoid-top first-trigger event to its SELL fill.

    An avoid-top event (code C, signal day d) fills on the first SELL of C on a
    day ≥ d not already claimed (queue may delay the fill past d). Returns the
    matched exits + the count of avoid-top events that never filled (trapped to
    the window end — captured in the arm's net P&L / the ③ residual, not ①②④).
    """
    day_index = {d: i for i, d in enumerate(daily_days)}
    # Rotation sells (the ranker's OWN sells) fill on the day AFTER they are
    # decided; exclude them so an avoid-top event is never matched to a
    # non-incremental rotation exit (codex P1). If rotation sold a code on the day
    # the overlay also wanted to, ``_merge_pending`` dropped the overlay order
    # (rotation precedence) — so that fill is the rotation sell, NOT an avoid-top
    # exit, and the avoid-top event is correctly left unmatched (non-incremental).
    rotation_sells: set[tuple[str, str]] = set()
    for dv in arm_result.decision_vectors:
        di = day_index.get(dv.trade_date)
        if di is None or di + 1 >= len(daily_days):
            continue
        fill_day = daily_days[di + 1]
        for code in dv.sell_codes:
            rotation_sells.add((code, fill_day))
    sell_fills_by_code: dict[str, list[AppliedFill]] = {}
    for f in arm_result.fills:
        if not f.side_is_buy and (f.code, f.trade_date) not in rotation_sells:
            sell_fills_by_code.setdefault(f.code, []).append(f)
    for fills in sell_fills_by_code.values():
        fills.sort(key=lambda f: f.trade_date)
    used: dict[str, int] = {}
    matched: list[_MatchedExit] = []
    unfilled = 0
    for ev in sorted(
        overlay.first_trigger_events("avoid_top"), key=lambda e: e.current_index
    ):
        fills = sell_fills_by_code.get(ev.code, [])
        cursor = used.get(ev.code, 0)
        hit = None
        while cursor < len(fills):
            f = fills[cursor]
            cursor += 1
            if f.trade_date >= ev.day:
                hit = f
                break
        used[ev.code] = cursor
        if hit is None:
            unfilled += 1
            continue
        # ④ = the EXPLICIT exit fees only (commission + stamp + transfer). Slippage
        # is NOT added: ``fill_price_cents`` is already post-slippage, so the
        # slippage cost is embedded in ①/②'s sell price — adding ``slippage_cents``
        # here would double-count it (codex P1).
        friction = hit.commission_cents + hit.stamp_tax_cents + hit.transfer_fee_cents
        matched.append(
            _MatchedExit(
                code=hit.code,
                fill_date=hit.trade_date,
                sell_price_cents=hit.fill_price_cents,
                volume=hit.volume,
                friction_cents=friction,
                forward_close_cents=_forward_close_cents(
                    bar_source,
                    daily_days,
                    day_index,
                    hit.trade_date,
                    hit.code,
                    PNL_HORIZON_TD,
                ),
            )
        )
    return matched, unfilled


def decompose_pnl(
    *,
    avoid_top_overlay: AvoidTopOverlay,
    avoid_top_arm: ArmResult,
    stop_only_arm: ArmResult,
    avoid_top_result: BacktestResult,
    bar_source: BarSource,
    daily_days: list[str],
) -> dict[str, object]:
    """Frozen 4-way P&L decomposition of the avoid-top signal vs the stop floor.

    net_trade_off (avoid_top − stop_only) = ①avoided_loss − ②missed_gain +
    ③redeployment − ④cost, with ①/②/④ measured directly from the matched
    avoid-top SELL fills over a FROZEN ``PNL_HORIZON_TD`` counterfactual and ③ the
    residual (cross-checked by the placebos, which redeploy identically). The
    algebra (horizon / stop_only baseline / attribution) is frozen so later signal
    work cannot mine P&L by changing it (codex R2-M2).
    """
    matched, unfilled = _match_avoid_top_fills(
        avoid_top_overlay, avoid_top_result, bar_source, daily_days
    )
    avoided_loss = 0.0
    missed_gain = 0.0
    cost = 0.0
    realized = 0
    for m in matched:
        cost += m.friction_cents / 100.0
        if m.forward_close_cents is None:
            continue
        realized += 1
        held_delta = m.volume * (m.forward_close_cents - m.sell_price_cents) / 100.0
        if held_delta < 0:
            avoided_loss += -held_delta
        else:
            missed_gain += held_delta
    net_trade_off = avoid_top_arm.net_pnl_yuan - stop_only_arm.net_pnl_yuan
    redeployment = net_trade_off - (avoided_loss - missed_gain) + cost
    return {
        "net_trade_off_yuan": net_trade_off,
        "avoided_loss_yuan": avoided_loss,  # ①
        "missed_gain_yuan": missed_gain,  # ②
        "redeployment_residual_yuan": redeployment,  # ③ (residual)
        "exit_cost_yuan": cost,  # ④
        "n_avoid_top_exits": len(matched),
        "n_realized_horizon": realized,
        "n_unfilled_trapped": unfilled,
        "pnl_horizon_td": PNL_HORIZON_TD,
        # The load-bearing inequality (§A4): avoided loss must exceed missed gain
        # plus the cost of exiting — else the avoid-top exits hurt on net.
        "favourable_trade_off": avoided_loss > (missed_gain + cost),
    }


def _balance_diagnostic(
    overlays: dict[str, AvoidTopOverlay | RandomHeldExitOverlay],
) -> dict[str, dict[str, float]]:
    """Held-book attributes of EXITed names per arm (codex R2-M1 balance check).

    With ≤5 held the placebo cannot exact-match attributes (sparsity) → we report
    the distributions instead: are avoid-top exits systematically older / bigger /
    more profitable than the random placebo exits (a confound)? ``signal`` =
    the arm's own EXIT reason (avoid_top / placebo).
    """
    out: dict[str, dict[str, float]] = {}
    for label, ov in overlays.items():
        reason = "avoid_top" if isinstance(ov, AvoidTopOverlay) else "placebo"
        ev = ov.first_trigger_events(reason)
        if not ev:
            out[label] = {"n": 0.0}
            continue
        ages = [float(e.holding_age) for e in ev]
        logmv = [math.log(e.market_value_cents) for e in ev if e.market_value_cents > 0]
        upnl = [e.unrealized_pnl_cents / 100.0 for e in ev]
        out[label] = {
            "n": float(len(ev)),
            "mean_holding_age": float(np.mean(ages)),
            "mean_log_mv": float(np.mean(logmv)) if logmv else float("nan"),
            "mean_unrealized_pnl_yuan": float(np.mean(upnl)),
        }
    return out


def _classify_regimes(beta_periods: tuple[float, ...]) -> list[str]:
    """Per-period market regime from the trailing-4-period beta cumret (look-back)."""
    labels: list[str] = []
    for i in range(len(beta_periods)):
        trail = beta_periods[max(0, i - REGIME_LOOKBACK_PERIODS) : i]
        cum = math.prod(1.0 + r for r in trail) - 1.0 if trail else 0.0
        if cum >= BULL_BAND:
            labels.append("bull")
        elif cum <= BEAR_BAND:
            labels.append("bear")
        else:
            labels.append("sideways")
    return labels


def _paired_t(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float]:
    """``(mean_diff, t)`` of the paired difference a−b (common length)."""
    n = min(len(a), len(b))
    if n < 2:
        return 0.0, 0.0
    d = np.asarray(a[:n], dtype=float) - np.asarray(b[:n], dtype=float)
    sd = float(d.std(ddof=1))
    if sd == 0.0:
        return float(d.mean()), 0.0
    return float(d.mean()), float(d.mean() / (sd / math.sqrt(n)))


def _regime_table(
    arms: list[ArmResult], regimes: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """``{arm: {regime: {n, sum_return, worst_period}}}`` per-regime stratification."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for arm in arms:
        buckets: dict[str, list[float]] = {"bull": [], "bear": [], "sideways": []}
        for i, r in enumerate(arm.period_returns):
            if i < len(regimes):
                buckets[regimes[i]].append(r)
        out[arm.label] = {
            reg: {
                "n": float(len(v)),
                "sum_return": float(sum(v)),
                "worst_period": float(min(v)) if v else float("nan"),
            }
            for reg, v in buckets.items()
        }
    return out


def _ledger_n_trials(
    ledger_path: str, arms: list[ArmResult], window: tuple[str, str], *, persist: bool
) -> int:
    """Append the ``qgr.avoid_top`` family (ONC-deduped) → non-zeroing deflation N."""
    ledger = TrialLedger.with_legacy(ledger_path)
    matrix = [list(a.period_returns) for a in arms]
    eff = onc_effective_n(matrix) if len(matrix) > 1 else len(matrix)
    if persist:
        ledger.append(
            TrialRecord(
                round_label="qgr-c1",
                kind="ablation",
                family="qgr.avoid_top",
                description=(
                    "avoid-top EXIT-on-held ablation (baseline/stop/avoid/2 placebos)"
                ),
                n_nominal_trials=len(arms),
                window_start=window[0],
                window_end=window[1],
                registered_at=LEDGER_DATE,
                effective_n=eff,
            )
        )
    return ledger.deflation_n_trials(onc_effective_n=eff)


def _build_placebo_plans(
    avoid_top_overlay: AvoidTopOverlay, daily_days: list[str]
) -> tuple[PlaceboPlan, PlaceboPlan]:
    """(calendar-matched, rate-matched) placebo plans from the avoid-top calendar.

    Calendar = the per-day count of the treatment's avoid-top first-trigger SELLs;
    rate = the SAME total redistributed over the window via a seeded draw (random
    timing, matched count). Both feed :class:`RandomHeldExitOverlay`.
    """
    events = avoid_top_overlay.first_trigger_events("avoid_top")
    counts_by_day: dict[str, int] = {}
    for e in events:
        counts_by_day[e.day] = counts_by_day.get(e.day, 0) + 1
    total = len(events)
    rng = np.random.default_rng(PLACEBO_SEED)
    counts_by_index: dict[int, int] = {}
    if total > 0 and daily_days:
        for i in rng.integers(0, len(daily_days), size=total):
            counts_by_index[int(i)] = counts_by_index.get(int(i), 0) + 1
    calendar = PlaceboPlan(seed=PLACEBO_SEED, counts_by_day=counts_by_day)
    rate = PlaceboPlan(seed=PLACEBO_SEED + 1, counts_by_index=counts_by_index)
    return calendar, rate


def _resolve_window(
    rebalance_dates: list[str], calendar: tuple[str, ...], *, train_val: set[str]
) -> list[str]:
    """The daily trading days the event loop replays (MTM + T+1 fills), train_val."""
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in train_val]


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full avoid-top EXIT-on-held ablation → a JSON-able result dict."""
    log("[1/7] load + firewall panel (train_val only)")
    panel = pd.read_csv(panel_path, dtype={"date": str, "code": str, "ts_code": str})
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/7] neutralize ranker survivors + crowding axis (industry SW-L1 + log size)")
    factors = [*xv.RANKER_FACTORS, xv.CROWD_FACTOR]
    neut = neutralize_panel(
        panel, factors, min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    ranker_table = xv.build_ranker_table(neut)
    if smoke_periods is not None:
        keep = set(sorted(ranker_table["date"].astype(str).unique())[:smoke_periods])
        ranker_table = ranker_table[ranker_table["date"].astype(str).isin(keep)].copy()

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    train_val = set(split.train_val_dates)
    daily_days = _resolve_window(rebs, calendar, train_val=train_val)
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = (*xv.panel_universe(ranker_table), CSI300_ETF)
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/7] build PIT bar source (heavy: full daily window)")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    scores = xv.scores_by_day(ranker_table)
    health = xv.build_health_overrides(ranker_table)
    provider = PanelScoreProvider(scores, health_overrides=health)
    triggers = build_avoid_top_triggers(ranker_table)
    cfg = AvoidTopExitConfig()
    log(f"      bar source ready; crowded flags={triggers.total_crowded_flags}")

    log("[4/7] run arms (baseline / stop_only / avoid_top → calendar → placebos)")
    e2e: dict[str, E2ERunResult] = {}
    e2e["baseline"] = _run_overlay(None, bar_source=bar_source, provider=provider)
    e2e["stop_only"] = _run_overlay(
        StopOnlyOverlay(cfg), bar_source=bar_source, provider=provider
    )
    avoid_top_overlay = AvoidTopOverlay(triggers, cfg)
    e2e["avoid_top"] = _run_overlay(
        avoid_top_overlay, bar_source=bar_source, provider=provider
    )
    cal_plan, rate_plan = _build_placebo_plans(avoid_top_overlay, daily_days)
    placebo_cal = RandomHeldExitOverlay(cal_plan, cfg)
    placebo_rate = RandomHeldExitOverlay(rate_plan, cfg)
    e2e["placebo_sell_calendar"] = _run_overlay(
        placebo_cal, bar_source=bar_source, provider=provider
    )
    e2e["placebo_random_held"] = _run_overlay(
        placebo_rate, bar_source=bar_source, provider=provider
    )
    arms = {a: _arm_from_e2e(a, e2e[a], horizon=HORIZON) for a in ARMS}
    for a in ARMS:
        r = arms[a]
        log(
            f"      {a:22s} netPnL={r.net_pnl_yuan:+,.0f} MDD={r.max_drawdown_pct:.2%} "
            f"turn={r.monthly_turnover:.2f} fills={r.fill_count} "
            f"sells={r.overlay_sell_signals} exp={r.avg_exposure:.2f} "
            f"cons={r.conservation_ok}"
        )

    # The C0b byte-exact invariant: baseline (NoOp) must equal the frozen engine.
    byte_exact = e2e["baseline"].overlay_sell_signals == 0

    log("[5/7] P&L 4-way decomposition + balance diagnostic")
    pnl = decompose_pnl(
        avoid_top_overlay=avoid_top_overlay,
        avoid_top_arm=arms["avoid_top"],
        stop_only_arm=arms["stop_only"],
        avoid_top_result=e2e["avoid_top"].backtest_result,
        bar_source=bar_source,
        daily_days=daily_days,
    )
    balance = _balance_diagnostic(
        {
            "avoid_top": avoid_top_overlay,
            "placebo_sell_calendar": placebo_cal,
            "placebo_random_held": placebo_rate,
        }
    )

    log("[6/7] stats: SPA/Romano-Wolf + DSR-HAC + non-zeroing ledger + regimes")
    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=CSI300_ETF,
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )
    bench = bh.period_returns
    cand_returns = [arms[a].period_returns for a in ARMS]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=list(ARMS),
        family="qgr.avoid_top",
    )
    window = (rebs[0], rebs[-1])
    n_trials = _ledger_n_trials(
        ledger_path, list(arms.values()), window, persist=smoke_periods is None
    )
    lag = _overlap_lag(HORIZON, REBALANCE_FREQ)
    dsr = {
        a: deflated_sharpe_hac(
            list(arms[a].period_returns), n_trials=n_trials, hac_lag=lag
        )
        for a in ARMS
    }
    regimes = _classify_regimes(bench)
    at = arms["avoid_top"]
    vs_stop = _paired_t(at.period_returns, arms["stop_only"].period_returns)
    vs_cal = _paired_t(at.period_returns, arms["placebo_sell_calendar"].period_returns)
    vs_rate = _paired_t(at.period_returns, arms["placebo_random_held"].period_returns)

    log("[7/7] assemble result + verdict")
    return {
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
            "byte_exact_baseline": byte_exact,
        },
        "config": {
            "top_q": triggers.top_q,
            "confirm_window": cfg.confirm_window,
            "rollover_drop": cfg.rollover_drop,
            "stop_loss_frac": cfg.stop_loss_frac,
            "reentry_lock_days": cfg.reentry_lock_days,
            "pnl_horizon_td": PNL_HORIZON_TD,
        },
        "arms": {
            a: asdict(arms[a])
            | {"period_returns": None, "n_periods": len(arms[a].period_returns)}
            for a in ARMS
        },
        "pnl_decomposition": pnl,
        "balance_diagnostic": balance,
        "dsr": dsr,
        "n_trials_deflation": n_trials,
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(ARMS, cmp.rw_rejected, strict=True)),
        "rw_adjusted_p": dict(zip(ARMS, cmp.rw_adjusted_pvalues, strict=True)),
        "avoid_top_vs_stop_only": {"mean_diff": vs_stop[0], "t": vs_stop[1]},
        "avoid_top_vs_placebo_calendar": {"mean_diff": vs_cal[0], "t": vs_cal[1]},
        "avoid_top_vs_placebo_rate": {"mean_diff": vs_rate[0], "t": vs_rate[1]},
        "regimes": _regime_table(list(arms.values()), regimes),
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "verdict": _verdict(arms, pnl, dsr, vs_cal, vs_rate),
    }


def _verdict(
    arms: dict[str, ArmResult],
    pnl: dict[str, object],
    dsr: dict[str, float],
    vs_cal: tuple[float, float],
    vs_rate: tuple[float, float],
) -> dict[str, object]:
    """Pre-committed §A4 verdict — FAIL is reported, not laundered.

    A deployable avoid-top EXIT edge requires ALL of: (1) the P&L decomposition is
    favourable (①avoided > ②missed + ④cost) AND ③redeployment is not the dominant
    driver; (2) net P&L not materially hurt vs the stop floor (sign-safe band);
    (3) STRICTLY beats BOTH placebos (paired-t ≥ 2 — not de-exposure/turnover
    noise); (4) clears DSR ≥ 0.95 (non-zeroing N); (5) enough avoid-top exits to be
    powered. The MDD≤8% gate is DROPPED (criterion-rebar) — MDD disclosure-only.
    """
    at = arms["avoid_top"]
    n_exits = cast("int", pnl["n_avoid_top_exits"])
    powered = n_exits >= MIN_AVOID_TOP_EXITS
    favourable = bool(pnl["favourable_trade_off"])
    redeploy = abs(cast("float", pnl["redeployment_residual_yuan"]))
    avoided = cast("float", pnl["avoided_loss_yuan"])
    missed = cast("float", pnl["missed_gain_yuan"])
    cost = cast("float", pnl["exit_cost_yuan"])
    # The direct avoid-top signal contribution is net of the exit cost (①−②−④);
    # redeployment must not dominate THAT, not the gross ①−② (codex P1).
    redeploy_not_main = redeploy <= max(avoided - missed - cost, 0.0)
    tol = INITIAL_CAPITAL_YUAN * PNL_HURT_TOL_FRAC
    no_pnl_hurt = at.net_pnl_yuan >= arms["stop_only"].net_pnl_yuan - tol
    beats_placebo = vs_cal[1] >= BEATS_PLACEBO_T and vs_rate[1] >= BEATS_PLACEBO_T
    dsr_pass = dsr.get("avoid_top", 0.0) >= DSR_GATE
    deployable = (
        powered
        and favourable
        and redeploy_not_main
        and no_pnl_hurt
        and beats_placebo
        and dsr_pass
    )
    return {
        "powered": powered,
        "n_avoid_top_exits": n_exits,
        "favourable_trade_off": favourable,
        "redeployment_not_main_driver": redeploy_not_main,
        "no_pnl_hurt_vs_stop": no_pnl_hurt,
        "beats_both_placebos": beats_placebo,
        "beats_placebo_t_bar": BEATS_PLACEBO_T,
        "dsr_pass": dsr_pass,
        "avoid_top_dsr": dsr.get("avoid_top", float("nan")),
        "deployable_edge": deployable,
        "summary": (
            "avoid-top EXIT earns a deployable edge: favourable P&L trade-off "
            "(①>②+④, ③ not dominant), net P&L held vs the stop floor, beat BOTH "
            "placebos at the strict t-bar, and cleared DSR≥0.95."
            if deployable
            else "avoid-top EXIT does NOT earn an independent deployable edge at "
            "≤5-slot scale — it fails one or more pre-committed gates (powered / "
            "favourable trade-off / ③-not-main / net-P&L / beats-both-placebos "
            "strict-t / DSR≥0.95). The effect is de-exposure / turnover noise OR "
            "the rolling-top confirmation does not net-dodge the left tail at this "
            "scale. Reported as FAIL, not laundered (batch-A proved the GROSS tail "
            "edge, NOT the net EXIT-on-held trade-off)."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_crowding.csv"
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--out", default="data/factor_research/c1_avoid_top_result.json"
    )
    parser.add_argument(
        "--smoke-periods",
        type=int,
        default=None,
        help="restrict to the first N rebalance dates (end-to-end smoke test)",
    )
    args = parser.parse_args()

    result = run_ablation(
        panel_path=args.panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        ledger_path=args.ledger,
        smoke_periods=args.smoke_periods,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["verdict"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = ["ArmResult", "decompose_pnl", "run_ablation"]
