"""Batch-B2 — permanent defensive-sleeve event-loop ablation (destination + frontier).

B1 proved the rotation-only arena cannot de-risk to MDD<=8% and that regime TIMING
is negative skill. B2 drops timing and tests a **permanent defensive sleeve**
(batch-B2 spec §0): always reserve K slots for a defensive destination, swept to map
the honest best partial. Two questions:

* Q1 (destination): for a 1-slot sleeve, does the destination identity matter —
  synthetic cash vs red-dividend EQUITY (510880, which itself crashed ~45% in 2015)
  vs government bonds (511010, a true broad-crash hedge)?
* Q2 (intensity → best partial): sweep a permanent cash sleeve K=1..4 to map the
  MDD/return frontier — "how defensive must the book be to reach MDD<=8%, and at what
  return cost". The decisive partial for the owner (a heavy defensive book stops being
  a stock-selection gate).

Real defensive ETFs are priced by the PIT bar source (fund_daily); only the cash
sweep needs B1's CashAugmentedBarSource. Two fail-closed integrity guards (codex B2):
every permanent sleeve must actually engage (>0 destination buy intents — a missing
ETF PIT bar would otherwise silently collapse the arm into the baseline), and every
arm must conserve cash/positions. Pre-committed per-arm verdict (FAIL reported, not
laundered): a deployable arm needs MDD<=8% AND net P&L>0 AND DSR>=0.95 AND it beats
the deployable beta baselines. Deterministic, offline, train_val only (firewall
asserts bar-read ⊆ train_val); never the live path; engine bytes untouched.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from backend.backtest.event_loop import BarSource
from backend.backtest.strategy import CodeHealth
from backend.marketdata_snapshot.store import SnapshotStore

from . import defensive_overlay_panel as dp
from . import derisk_overlay_panel as do
from . import exit_veto_panel as xv
from .arena_ablation import firewalled_ranker_table, hold_baseline_arm, ledger_n_trials
from .crowding_factor_diagnostics import _overlap_lag
from .exit_veto_ablation import (
    CSI300_ETF,
    DSR_GATE,
    HORIZON,
    INITIAL_CAPITAL_YUAN,
    LEDGER_DATE,
    MDD_CAP,
    MIN_OBS,
    REBALANCE_FREQ,
    WINSOR_QUANTILE,
    ArmResult,
    _arm_from_gate,
    _classify_regimes,
    _regime_table,
)
from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    default_strategy_config,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac
from .multi_strategy_compare import compare_strategies

LEDGER_FAMILY: str = "qgr.defensive_sleeve"
ArmHealth = dict[str, dict[str, CodeHealth]]

# Each arm's permanent defensive sleeve (the codes locked into slots from day 1).
ARM_SLEEVES: dict[str, tuple[str, ...]] = {
    "baseline": (),
    "perm_cash1": do.CASH_CODES[:1],
    "perm_cash2": do.CASH_CODES[:2],
    "perm_cash3": do.CASH_CODES[:3],
    "perm_cash4": do.CASH_CODES[:4],
    "perm_div1": (dp.DIVIDEND_ETF,),
    "perm_bond1": (dp.BOND_ETF,),
}
ARM_LABELS: tuple[str, ...] = tuple(ARM_SLEEVES)
# Defensive ETFs that must be in the bar-source universe (cash is overlaid separately).
UNIVERSE_ETFS: tuple[str, ...] = (CSI300_ETF, dp.DIVIDEND_ETF, dp.BOND_ETF)


def _run_arm(
    scores: dict[str, xv.ScoredDay], health: ArmHealth, *, bar_source: BarSource
) -> GateBacktestResult:
    provider = PanelScoreProvider(scores, health_overrides=health)
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=default_strategy_config(),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _build_arms(
    ranker_table: pd.DataFrame,
) -> tuple[dict[str, dict[str, xv.ScoredDay]], dict[str, ArmHealth]]:
    """Per-arm permanent-sleeve scores + health (computed off ONE base pass)."""
    base_scores = xv.scores_by_day(ranker_table)
    base_health = xv.build_health_overrides(ranker_table)
    scores: dict[str, dict[str, xv.ScoredDay]] = {}
    health: dict[str, ArmHealth] = {}
    for label, sleeve in ARM_SLEEVES.items():
        scores[label] = dp.inject_permanent_scores(base_scores, sleeve)
        health[label] = dp.build_permanent_health(ranker_table, base_health, sleeve)
    return scores, health


def _assert_sleeve_engaged(label: str, sleeve: tuple[str, ...], intents: int) -> None:
    """Fail-closed: a non-empty permanent sleeve must actually buy its destination.

    A missing ``fund_daily`` PIT bar for a destination ETF (incremental update that
    did not backfill) would leave the injected asset unfilled, silently collapsing the
    arm into the stock-only baseline while still writing a clean FAIL — a data gap
    laundered into a (confidently wrong) destination conclusion (codex B2 #1).
    """
    if sleeve and intents == 0:
        raise RuntimeError(
            f"arm {label} has a permanent sleeve {sleeve} but ZERO destination buy "
            f"intents — the sleeve never engaged (check the destination's fund_daily "
            f"PIT coverage); refusing to launder it into a FAIL verdict"
        )


def _assert_conservation(arm: ArmResult) -> None:
    """Fail-closed: every arm must conserve cash/positions (the synthetic cash overlay
    must not fabricate money). A broken arm's net P&L would corrupt the verdict."""
    if not arm.conservation_ok:
        raise RuntimeError(
            f"arm {arm.label} violates cash/position conservation — refusing to "
            f"report a corrupted net P&L"
        )


def _verdict(
    arm_results: list[ArmResult],
    dsr: dict[str, float],
    t_vs_csi300: dict[str, float],
) -> dict[str, object]:
    """Pre-committed batch-B2 §2 verdict — per-arm, FAIL reported not laundered.

    A deployable defensive-sleeve arm needs ALL of MDD <= 8% + net P&L > 0 +
    DSR >= 0.95 + it beats the deployable beta baseline (a positive t vs CSI300
    buy-and-hold is the necessary direction; SPA/Romano-Wolf disclosed alongside).
    The honest expectation (spec §2): no arm clears all four — reaching MDD <= 8%
    drives net P&L toward the bond/cash floor (the book stops being a stock gate).
    """
    per_arm: dict[str, dict[str, object]] = {}
    any_deployable = False
    for a in arm_results:
        if a.label == "baseline":
            continue
        mdd_ok = a.mdd_within_cap
        pnl_ok = a.net_pnl_yuan > 0.0
        dsr_ok = dsr.get(a.label, 0.0) >= DSR_GATE
        beats_beta = t_vs_csi300.get(a.label, 0.0) > 0.0
        deployable = mdd_ok and pnl_ok and dsr_ok and beats_beta
        any_deployable = any_deployable or deployable
        per_arm[a.label] = {
            "mdd": a.max_drawdown_pct,
            "mdd_within_cap": mdd_ok,
            "net_pnl_yuan": a.net_pnl_yuan,
            "net_pnl_positive": pnl_ok,
            "dsr": dsr.get(a.label, float("nan")),
            "dsr_pass": dsr_ok,
            "beats_beta_t": t_vs_csi300.get(a.label, float("nan")),
            "deployable": deployable,
        }
    return {
        "any_deployable_edge": any_deployable,
        "per_arm": per_arm,
        "summary": (
            "A defensive-sleeve arm earns a deployable edge (MDD<=8% + net P&L>0 + "
            "DSR>=0.95 + beats beta)."
            if any_deployable
            else "NO defensive-sleeve arm earns a deployable edge — reaching MDD<=8% "
            "drives net P&L toward the bond/cash floor (the book stops being a "
            "stock-selection gate) and/or no arm clears DSR>=0.95 / beats beta. The "
            "MDD/return frontier is the honest best partial. Reported as FAIL."
        ),
    }


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full permanent-sleeve event-loop ablation → a JSON-able result dict."""
    log("[1/6] firewall + neutralize panel (train_val only)")
    ranker_table, rebs, daily_days, _split = firewalled_ranker_table(
        panel_path=panel_path,
        lock_path=lock_path,
        snapshot_root=snapshot_root,
        factors=[*xv.RANKER_FACTORS, xv.CROWD_FACTOR],
        min_obs=MIN_OBS,
        winsor_quantile=WINSOR_QUANTILE,
        smoke_periods=smoke_periods,
        log=log,
    )
    universe = (*xv.panel_universe(ranker_table), *UNIVERSE_ETFS)
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[2/6] build PIT bar source (heavy) + cash overlay")
    store = SnapshotStore(snapshot_root)
    base_bar_source = PitBarSource(
        store=store, trading_days=daily_days, universe=universe
    )
    bar_source = do.CashAugmentedBarSource(base_bar_source)
    log("      bar source ready")

    log("[3/6] build arms (permanent sleeves) + run event loop")
    arms_scores, arms_health = _build_arms(ranker_table)
    arm_results: list[ArmResult] = []
    sleeve_intents: dict[str, int] = {}
    for label in ARM_LABELS:
        gate = _run_arm(arms_scores[label], arms_health[label], bar_source=bar_source)
        arm_results.append(_arm_from_gate(label, gate))
        a = arm_results[-1]
        sleeve_intents[label] = dp.asset_buy_intent_count(
            gate.backtest_result.decision_vectors, ARM_SLEEVES[label]
        )
        _assert_sleeve_engaged(label, ARM_SLEEVES[label], sleeve_intents[label])
        _assert_conservation(a)
        log(
            f"      {label:12s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} sleeve_intents={sleeve_intents[label]} "
            f"cons={a.conservation_ok}"
        )

    log("[4/6] deployable / defensive baselines (beta + defensive hurdles)")
    baseline_results = [
        hold_baseline_arm(
            bar_source, code, lbl,
            initial_capital_yuan=INITIAL_CAPITAL_YUAN, horizon=HORIZON, mdd_cap=MDD_CAP,
        )
        for code, lbl in (
            (CSI300_ETF, "csi300_etf_hold"),
            (dp.BOND_ETF, "bond_etf_hold"),
            (dp.DIVIDEND_ETF, "div_etf_hold"),
        )
    ]
    for a in baseline_results:
        log(
            f"      {a.label:14s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} exp={a.avg_exposure:.2f}"
        )

    log("[5/6] stats: SPA/Romano-Wolf + DSR-HAC + non-zeroing ledger + frontier")
    all_arms = arm_results + baseline_results
    by_label = {a.label: a for a in all_arms}
    bench = by_label["csi300_etf_hold"].period_returns
    cand_returns = [by_label[f].period_returns for f in ARM_LABELS]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    if nmin < 2:
        raise ValueError(f"degenerate window — only {nmin} non-overlapping periods")
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=list(ARM_LABELS),
        family=LEDGER_FAMILY,
    )
    window = (rebs[0], rebs[-1])
    n_trials = ledger_n_trials(
        ledger_path, arm_results, window,
        persist=smoke_periods is None, family=LEDGER_FAMILY, round_label="qgr-4-b2",
        description="permanent defensive-sleeve sweep (cash K=1..4 + div/bond)",
        ledger_date=LEDGER_DATE,
    )
    lag = _overlap_lag(HORIZON, REBALANCE_FREQ)
    dsr = {
        a.label: deflated_sharpe_hac(
            list(a.period_returns), n_trials=n_trials, hac_lag=lag
        )
        for a in arm_results
    }
    t_vs = dict(zip(ARM_LABELS, cmp.t_stats, strict=True))
    regimes = _classify_regimes(bench)

    log("[6/6] assemble result")
    frontier = [
        {
            "arm": a.label,
            "sleeve_slots": len(ARM_SLEEVES[a.label]),
            "net_pnl_yuan": a.net_pnl_yuan,
            "mdd": a.max_drawdown_pct,
            "avg_exposure": a.avg_exposure,
        }
        for a in arm_results
    ]
    return {
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
        },
        "sleeve_buy_intents_per_arm": dict(sleeve_intents),
        "arms": {
            a.label: asdict(a)
            | {"period_returns": None, "n_periods": len(a.period_returns)}
            for a in all_arms
        },
        "frontier": frontier,
        "dsr": dsr,
        "n_trials_deflation": n_trials,
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(ARM_LABELS, cmp.rw_rejected, strict=True)),
        "t_stats_vs_csi300": t_vs,
        "regimes": _regime_table(all_arms, regimes),
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "verdict": _verdict(arm_results, dsr, t_vs),
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
        "--out", default="data/factor_research/b2_defensive_sleeve_result.json"
    )
    parser.add_argument("--smoke-periods", type=int, default=None)
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
    out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result["verdict"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = ["ARM_LABELS", "ARM_SLEEVES", "LEDGER_FAMILY", "run_ablation"]
