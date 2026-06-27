"""Slot-count × sizing frontier for the PURE QGR-3 reversal ranker (no overlays).

After C1a proved every EXIT / de-risk overlay is net-harmful on the ≤5-slot
reversal book (the overlay fights the reversal alpha) AND that the pure baseline
itself is +459k but with a structural 54% MDD / DSR 0.005, the open framing
question (codex strategy review 2026-06-27) is: **is ≤5-slot CONCENTRATION the
binding constraint?** I.e. does drawdown-control belong at the PORTFOLIO layer
(more slots / a cash buffer) rather than the stock-gate layer (the failed
overlays)? This sweep answers it with data — the (A) concentrated vs (B)
diversified philosophy fork the owner must choose.

It runs the SAME ranker (``{rev_1d, max_5d, turn_spike}`` size/industry-neutral,
the C1a baseline) through the frozen ``run_gate_backtest`` event loop (T+1 /
per-board slippage / ¥5 min commission / ≤1 rotation/day) under a grid of:
  * **diversification** — N = 5/10/20/30/50 equal-weight (cap=100, ~full
    investment): isolates concentration at ~constant gross.
  * **cash buffer at ≤5** — N=5 at gross 100/80/60/40% (via the per-name cap):
    the (A) concentrated path's protection mechanism (a crude proxy for the
    owner's P-E cash-floor; it does NOT model P-E's confidence-CONCENTRATION,
    which needs a separate confidence-weighted sizing layer — a documented gap).
  * **diversified + buffer** — N=30 at 60% gross: the (B) path with a buffer.
Plus the C1a reference (N=5, cap=15, ~75% gross) to anchor against the +459k/54%.

Reports net P&L / MDD / turnover / realized exposure / DSR (non-zeroing N) per
config + SPA/Romano-Wolf across the diversification sweep + regime + crash-slice
stratification. Sizing-config sweep DEBITS the trial ledger (family
``qgr.slot_frontier``) so any surfaced DSR is deflated for the search. Train_val
only (sealed test never read); deterministic; offline; never the live path. This
is an EXPLORATORY framing probe, not a candidate promotion — nothing here clears
to "deployable" without the usual gates + a look-once forward.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from backend.backtest.strategy import StrategyConfig
from backend.marketdata_snapshot.store import SnapshotStore
from backend.slot_portfolio import load_rotation_policy_config

from . import exit_veto_panel as xv
from .avoid_top_ablation import (
    CRASH_SLICES,  # noqa: F401  (re-exported for the report's slice list)
    _classify_regimes,
    _crash_slice_table,
    _regime_table,
)
from .baselines import buy_and_hold_baseline
from .crowding_factor_diagnostics import _overlap_lag
from .gate_backtest import (
    DEFAULT_ROTATION_CONFIG_PATH,
    GateBacktestResult,
    PanelScoreProvider,
    default_selector,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac, onc_effective_n
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies
from .neutralize import neutralize_panel
from .trial_ledger import TrialLedger, TrialRecord

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
HORIZON: int = 5
REBALANCE_FREQ: int = 5
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
LEDGER_DATE: str = "2026-06-27"
DSR_GATE: float = 0.95


@dataclass(frozen=True)
class FrontierConfig:
    """One frontier point: a slot count + per-name cap (the sizing knobs)."""

    label: str
    slots: int
    cap_percent: int
    note: str


# The grid. equal-weight uses cap=100 (never binds → 1/N each ≈ full investment);
# a cash buffer is a per-name cap C with N×C ≈ target gross %.
FRONTIER: tuple[FrontierConfig, ...] = (
    # diversification axis (equal-weight, ~full investment)
    FrontierConfig("eq_5", 5, 100, "equal-weight 1/5=20% ~100% gross"),
    FrontierConfig("eq_10", 10, 100, "equal-weight 1/10=10%"),
    FrontierConfig("eq_20", 20, 100, "equal-weight 1/20=5%"),
    FrontierConfig("eq_30", 30, 100, "equal-weight 1/30=3.3%"),
    FrontierConfig("eq_50", 50, 100, "equal-weight 1/50=2%"),
    # C1a reference (anchors to the +459k / 54% MDD baseline)
    FrontierConfig(
        "ref_c1a_5_cap15", 5, 15, "C1a baseline: 5 slots, 15% cap ~75% gross"
    ),
    # cash-buffer axis at ≤5 (the (A) concentrated path's protection)
    FrontierConfig("buf80_5", 5, 16, "5 slots ~80% gross (20% buffer)"),
    FrontierConfig("buf60_5", 5, 12, "5 slots ~60% gross (40% buffer)"),
    FrontierConfig("buf40_5", 5, 8, "5 slots ~40% gross (60% buffer)"),
    # diversified + buffer (the (B) path with a buffer)
    FrontierConfig("buf60_30", 30, 2, "30 slots ~60% gross (40% buffer)"),
)
DIVERSIFICATION_LABELS: tuple[str, ...] = ("eq_5", "eq_10", "eq_20", "eq_30", "eq_50")


@dataclass(frozen=True)
class FrontierResult:
    """One config's arena outcome (the frontier row)."""

    label: str
    slots: int
    cap_percent: int
    net_pnl_yuan: float
    max_drawdown_pct: float
    monthly_turnover: float
    fill_count: int
    avg_exposure: float
    conservation_ok: bool
    dsr: float
    period_returns: tuple[float, ...]


def _strategy_config(slots: int, cap_percent: int) -> StrategyConfig:
    """Strategy config whose shortlist is WIDENED to ``slots`` candidates/day.

    The default selector surfaces only 5 names/day; left at 5 it would bottleneck
    the daily inflow and a 30-slot book could never actually fill (a confound that
    would mask true diversification). Setting ``final_shortlist_size = slots`` lets
    the ranker offer enough fresh candidates to fill ``slots`` open positions; at
    ``slots=5`` this is byte-identical to the C1a baseline selector.
    """
    return StrategyConfig(
        selector=default_selector(
            final_shortlist_size=slots, min_quant_slots=min(3, slots)
        ),
        rotation=load_rotation_policy_config(DEFAULT_ROTATION_CONFIG_PATH),
        max_total_positions=slots,
        single_stock_cap_percent=cap_percent,
    )


def _run_config(
    cfg: FrontierConfig,
    *,
    provider: PanelScoreProvider,
    bar_source: PitBarSource,
) -> GateBacktestResult:
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=_strategy_config(cfg.slots, cfg.cap_percent),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _resolve_window(
    rebalance_dates: list[str], calendar: tuple[str, ...], *, train_val: set[str]
) -> list[str]:
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in train_val]


def _ledger_n_trials(
    ledger_path: str,
    results: list[FrontierResult],
    window: tuple[str, str],
    *,
    persist: bool,
) -> int:
    """Append the ``qgr.slot_frontier`` family (ONC-deduped) → non-zeroing N."""
    ledger = TrialLedger.with_legacy(ledger_path)
    matrix = [list(r.period_returns) for r in results]
    eff = onc_effective_n(matrix) if len(matrix) > 1 else len(matrix)
    if persist:
        ledger.append(
            TrialRecord(
                round_label="qgr-c1-frontier",
                kind="sizing_sweep",
                family="qgr.slot_frontier",
                description="pure-ranker slot-count × cash-buffer sizing frontier",
                n_nominal_trials=len(results),
                window_start=window[0],
                window_end=window[1],
                registered_at=LEDGER_DATE,
                effective_n=eff,
            )
        )
    return ledger.deflation_n_trials(onc_effective_n=eff)


def run_frontier(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Sweep the frontier → a JSON-able result dict (the report's data)."""
    log("[1/6] load + firewall panel (train_val only)")
    panel = pd.read_csv(panel_path, dtype={"date": str, "code": str, "ts_code": str})
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/6] neutralize ranker survivors + crowding axis")
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

    log("[3/6] build PIT bar source (heavy) + shared ranker provider")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    scores = xv.scores_by_day(ranker_table)
    health = xv.build_health_overrides(ranker_table)
    provider = PanelScoreProvider(scores, health_overrides=health)

    log("[4/6] run frontier grid")
    window = (rebs[0], rebs[-1])
    n_trials = 1  # filled after the ledger append below
    results: list[FrontierResult] = []
    raw: dict[str, GateBacktestResult] = {}
    for cfg in FRONTIER:
        g = _run_config(cfg, provider=provider, bar_source=bar_source)
        raw[cfg.label] = g
        results.append(
            FrontierResult(
                label=cfg.label,
                slots=cfg.slots,
                cap_percent=cfg.cap_percent,
                net_pnl_yuan=g.net_pnl_yuan,
                max_drawdown_pct=g.max_drawdown_pct,
                monthly_turnover=g.monthly_turnover,
                fill_count=g.fill_count,
                avg_exposure=g.backtest_result.avg_exposure_ratio,
                conservation_ok=g.conservation_ok,
                dsr=0.0,  # filled after deflation N is known
                period_returns=g.period_returns,
            )
        )
        r = results[-1]
        log(
            f"      {cfg.label:18s} slots={cfg.slots:2d} cap={cfg.cap_percent:3d} "
            f"netPnL={r.net_pnl_yuan:+,.0f} MDD={r.max_drawdown_pct:.2%} "
            f"turn={r.monthly_turnover:.2f} exp={r.avg_exposure:.2f} "
            f"fills={r.fill_count}"
        )

    log("[5/6] deflation N (non-zeroing ledger) + DSR + SPA/RW + regimes/slices")
    n_trials = _ledger_n_trials(
        ledger_path, results, window, persist=smoke_periods is None
    )
    lag = _overlap_lag(HORIZON, REBALANCE_FREQ)
    results = [
        replace(
            r,
            dsr=deflated_sharpe_hac(
                list(r.period_returns), n_trials=n_trials, hac_lag=lag
            ),
        )
        for r in results
    ]
    by_label = {r.label: r for r in results}

    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=CSI300_ETF,
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )
    bench = bh.period_returns
    div = [by_label[label] for label in DIVERSIFICATION_LABELS]
    nmin = min(len(bench), *(len(r.period_returns) for r in div))
    cmp = compare_strategies(
        candidate_returns=[list(r.period_returns[:nmin]) for r in div],
        benchmark_returns=list(bench[:nmin]),
        labels=list(DIVERSIFICATION_LABELS),
        family="qgr.slot_frontier",
    )
    regimes = _classify_regimes(bench)
    n_periods = len(div[0].period_returns)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]

    # Reuse the avoid_top regime/crash tables (they read .label + .period_returns).
    regime_tbl = _regime_table(list(results), regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(list(results), period_dates)  # type: ignore[arg-type]

    log("[6/6] assemble result")
    return {
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
            "csi300_hold_net_pnl": bh.net_pnl_yuan,
            "csi300_hold_mdd": bh.max_drawdown_pct,
        },
        "n_trials_deflation": n_trials,
        "dsr_gate": DSR_GATE,
        "configs": {
            r.label: asdict(r)
            | {"period_returns": None, "n_periods": len(r.period_returns)}
            for r in results
        },
        "diversification_spa_p_value": cmp.spa_p_value,
        "diversification_rw_rejected": dict(
            zip(DIVERSIFICATION_LABELS, cmp.rw_rejected, strict=True)
        ),
        "regimes": regime_tbl,
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "crash_slices": crash_tbl,
        "read": _read(by_label),
    }


def _read(by_label: dict[str, FrontierResult]) -> dict[str, object]:
    """A terse machine read of the binding-constraint question (not a verdict).

    Diversification helps drawdown if MDD falls monotonically-ish as slots rise;
    it helps the edge if DSR rises. The cash buffer helps if MDD falls with gross.
    """
    eq = [by_label[label] for label in DIVERSIFICATION_LABELS]
    mdd_5, mdd_50 = eq[0].max_drawdown_pct, eq[-1].max_drawdown_pct
    dsr_5, dsr_50 = eq[0].dsr, eq[-1].dsr
    return {
        "diversification_mdd_5_to_50": [mdd_5, mdd_50],
        "diversification_mdd_fell": mdd_50 < mdd_5,
        "diversification_dsr_5_to_50": [dsr_5, dsr_50],
        "diversification_dsr_rose": dsr_50 > dsr_5,
        "any_config_clears_dsr_gate": any(r.dsr >= DSR_GATE for r in by_label.values()),
        "buffer_mdd_100_to_40_at_5": [
            by_label["eq_5"].max_drawdown_pct,
            by_label["buf40_5"].max_drawdown_pct,
        ],
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
        "--out", default="data/factor_research/slot_frontier_result.json"
    )
    parser.add_argument("--smoke-periods", type=int, default=None)
    args = parser.parse_args()

    result = run_frontier(
        panel_path=args.panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        ledger_path=args.ledger,
        smoke_periods=args.smoke_periods,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["read"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = ["FrontierConfig", "FrontierResult", "run_frontier"]
