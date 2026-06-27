"""Batch-B1 first cut — regime-gated exposure de-risk event-loop ablation.

QGR-4 proved a buy-set crowding veto cannot control drawdown (it only decides who
to BUY, never reduces held exposure; every arm's MDD was 54–58% ≫ 8%). This cut
turns to the owner criterion's real engine — **regime-gated true de-exposure** —
and asks the honest questions head-on (batch-B1 spec §0):

* can the frozen ≤5-slot **fully-invested** rotation arena even be throttled to
  MDD ≤ 8% (it is fully invested by construction — spec §1)?
* does **regime timing** beat a "constant / random hold-cash" placebo of the SAME
  average exposure reduction (is there timing skill, or just less beta)?
* does the de-risk arm survive DSR ≥ 0.95 against the regime-threshold DOF?

Mechanism (spec §3): a synthetic cash sleeve that wins rotation on treated dates,
with the IDENTICAL treatment applied to the placebos — only the treated *date set*
differs (regime high-risk vs regime-blind constant/random of matched count). So a
de-risk effect for the regime arm can only come from TIMING, not the mechanism.

Pre-committed verdict (spec §4, FAIL is reported not laundered): a deployable
de-risk edge needs ALL of MDD ≤ 8% + net P&L > 0 + strictly beats BOTH placebos
(paired-t ≥ 2.0) + DSR ≥ 0.95. Deterministic, offline, train_val only (the sealed
test window is never read; the firewall asserts the actual bar-read window ⊆
train_val). Never the live path; the engine / bar-source bytes are untouched (cash
is overlaid by a wrapper). The QGR-4 arena helpers are reused unchanged for a
bit-comparable arena (the only divergence is the cash overlay).
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

from . import derisk_overlay_panel as do
from . import exit_veto_panel as xv
from .baselines import (
    buy_and_hold_baseline,
    random_top_n_scores,
    single_asset_scores,
)
from .crowding_factor_diagnostics import _overlap_lag
from .exit_veto_ablation import (
    BEATS_PLACEBO_T,
    CSI300_ETF,
    DSR_GATE,
    HORIZON,
    INITIAL_CAPITAL_YUAN,
    LEDGER_DATE,
    MIN_OBS,
    REBALANCE_FREQ,
    WINSOR_QUANTILE,
    ArmResult,
    _arm_from_gate,
    _classify_regimes,
    _paired_t,
    _regime_table,
    _resolve_window,
)
from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    default_strategy_config,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac, onc_effective_n
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies
from .neutralize import neutralize_panel
from .regime_detector import (
    CSI300_ETF as REGIME_MARKET_CODE,
)
from .regime_detector import (
    classify_regimes,
    high_risk_dates,
    read_market_closes,
)
from .trial_ledger import TrialLedger, TrialRecord

PLACEBO_SEED: int = 20260627
LEDGER_FAMILY: str = "qgr.derisk_regime"
# Fail-closed floor for the regime market-series coverage (codex PLAUSIBLE #3):
# below this the treatment schedule is untrustworthy and the run must error, not
# launder a data gap into a FAIL verdict.
MARKET_COVERAGE_FLOOR: float = 0.90
ARM_LABELS: tuple[str, ...] = (
    "baseline",
    "derisk_regime",
    "placebo_constant",
    "placebo_random",
)

ArmHealth = dict[str, dict[str, CodeHealth]]


def _assert_market_coverage(n_closes: int, n_days: int) -> float:
    """Fail-closed guard on the regime market-series coverage (codex #3).

    The treatment schedule is driven by this externally-read series; if 510300.SH
    coverage is sparse the regime arm would de-risk on the wrong/empty date set and
    the run would be reported as a benign "no edge" FAIL — a data gap laundered into
    a scientific verdict. Raise instead, mirroring the panel firewall. Returns the
    coverage fraction for logging.
    """
    coverage = n_closes / n_days if n_days else 0.0
    if coverage < MARKET_COVERAGE_FLOOR:
        raise ValueError(
            f"regime market series {REGIME_MARKET_CODE} covers only {n_closes}/"
            f"{n_days} daily days ({coverage:.1%} < {MARKET_COVERAGE_FLOOR:.0%}) — "
            f"fail-closed (do not launder a data gap into a FAIL verdict)"
        )
    return coverage


def _assert_treatment_fired(n_treated: int, derisk_cash_intents: int) -> None:
    """Integrity guard: treated dates must actually rotate into cash (codex #5).

    If the regime arm has treated dates but the engine never decided a cash buy, the
    de-risk mechanism is silently vacuous (e.g. the slot_portfolio weakness gate
    changed so the forced-weak health no longer fires) — a bug, not a "no edge"
    result. Raise rather than report a spurious FAIL with zero treatment applied.
    """
    if n_treated > 0 and derisk_cash_intents == 0:
        raise RuntimeError(
            f"regime arm has {n_treated} treated dates but ZERO cash buy intents — "
            f"the de-risk mechanism did not fire (vacuous experiment); check the "
            f"slot_portfolio weakness gate vs the forced-weak health"
        )


def _assert_enough_periods(nmin: int) -> None:
    """Guard against a degenerate window with too few non-overlapping periods (#4)."""
    if nmin < 2:
        raise ValueError(
            f"degenerate window — an arm produced {nmin} non-overlapping periods; "
            f"the comparison stats would be meaningless (codex PLAUSIBLE #4)"
        )


def _run_arm(
    scores: dict[str, xv.ScoredDay],
    health: ArmHealth,
    *,
    bar_source: BarSource,
) -> GateBacktestResult:
    """Replay one arm through the arena (cash overlaid by ``bar_source``)."""
    provider = PanelScoreProvider(scores, health_overrides=health)
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=default_strategy_config(),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _run_baselines(
    ranker_table: pd.DataFrame, daily_days: list[str], *, bar_source: BarSource
) -> list[ArmResult]:
    """Deployable skill-free hurdles: random top-5, ETF-in-slot, ETF buy-and-hold.

    The cash sleeves are never in these score tables, so the cash overlay on the
    shared ``bar_source`` is inert for the baselines (they never select it).
    """
    uni = xv.universe_by_day(ranker_table)
    out: list[ArmResult] = []
    rand = PanelScoreProvider(random_top_n_scores(uni, seed=PLACEBO_SEED))
    out.append(
        _arm_from_gate(
            "random_top5",
            run_gate_backtest(
                bar_source=bar_source,
                provider=rand,
                strategy_config=default_strategy_config(),
                initial_capital_yuan=INITIAL_CAPITAL_YUAN,
                horizon=HORIZON,
            ),
        )
    )
    etf = PanelScoreProvider(single_asset_scores(daily_days, CSI300_ETF))
    out.append(
        _arm_from_gate(
            "etf_only_510300",
            run_gate_backtest(
                bar_source=bar_source,
                provider=etf,
                strategy_config=default_strategy_config(),
                initial_capital_yuan=INITIAL_CAPITAL_YUAN,
                horizon=HORIZON,
            ),
        )
    )
    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=CSI300_ETF,
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )
    out.append(
        ArmResult(
            label="csi300_etf_hold",
            net_pnl_yuan=bh.net_pnl_yuan,
            total_return=bh.total_return,
            max_drawdown_pct=bh.max_drawdown_pct,
            monthly_turnover=0.0,
            fill_count=bh.fill_count,
            avg_exposure=bh.invested_fraction,
            conservation_ok=bh.conservation_ok,
            exposure_cap_violations=bh.exposure_cap_violations,
            period_returns=bh.period_returns,
            mdd_within_cap=bh.max_drawdown_pct <= 0.08,
        )
    )
    return out


def _build_arms(
    ranker_table: pd.DataFrame, cash_dates_by_arm: dict[str, tuple[str, ...]]
) -> tuple[dict[str, dict[str, xv.ScoredDay]], dict[str, ArmHealth]]:
    """Per-arm ``scores_by_day`` + ``health_overrides`` (cash treatment per arm).

    The base scores + base panel-driven health are computed ONCE; each arm injects
    the cash sleeves + de-risk health on its own treated dates (the regime arm's
    high-risk dates; the placebos' matched-count regime-blind dates).
    """
    base_scores = xv.scores_by_day(ranker_table)
    base_health = xv.build_health_overrides(ranker_table)
    scores: dict[str, dict[str, xv.ScoredDay]] = {}
    health: dict[str, ArmHealth] = {}
    for label in ARM_LABELS:
        cash_dates = cash_dates_by_arm[label]
        scores[label] = do.inject_cash_scores(base_scores, cash_dates)
        health[label] = do.build_arm_health(ranker_table, base_health, cash_dates)
    return scores, health


def _cash_dates_by_arm(
    rebs: list[str], regime_high_risk: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    """Treated-date set per arm: regime high-risk, then matched-count placebos."""
    n_treated = len(regime_high_risk)
    return {
        "baseline": (),
        "derisk_regime": regime_high_risk,
        "placebo_constant": do.constant_cash_dates(rebs, n_treated),
        "placebo_random": do.random_cash_dates(rebs, n_treated, seed=PLACEBO_SEED),
    }


def _ledger_n_trials(
    ledger_path: str,
    arms: list[ArmResult],
    window: tuple[str, str],
    *,
    persist: bool,
) -> int:
    """Append the ``qgr.derisk_regime`` family (ONC-deduped) → non-zeroing N."""
    ledger = TrialLedger.with_legacy(ledger_path)
    matrix = [list(a.period_returns) for a in arms]
    eff = onc_effective_n(matrix) if len(matrix) > 1 else len(matrix)
    if persist:
        ledger.append(
            TrialRecord(
                round_label="qgr-4-b1",
                kind="ablation",
                family=LEDGER_FAMILY,
                description="regime de-risk overlay (baseline/regime/2 placebos)",
                n_nominal_trials=len(arms),
                window_start=window[0],
                window_end=window[1],
                registered_at=LEDGER_DATE,
                effective_n=eff,
            )
        )
    return ledger.deflation_n_trials(onc_effective_n=eff)


def _verdict(
    by_label: dict[str, ArmResult],
    regime_vs_const: tuple[float, float],
    regime_vs_rand: tuple[float, float],
    dsr: dict[str, float],
) -> dict[str, object]:
    """Pre-committed batch-B1 §4 de-risk verdict — FAIL is reported, not laundered.

    A deployable regime de-risk edge requires ALL of: (1) MDD within the 8% cap
    (the owner hard constraint, the primary target); (2) absolute net P&L > 0
    (de-risking must not turn the book net-negative); (3) STRICTLY beats BOTH
    placebos at a paired-t bar (regime timing adds value over constant/random
    same-size exposure reduction — the core scientific question); (4) the regime
    arm clears the DSR ≥ 0.95 main anti-overfitting gate (CLAUDE.md principle #1).
    """
    regime = by_label["derisk_regime"]
    mdd_within_cap = regime.mdd_within_cap
    net_pnl_positive = regime.net_pnl_yuan > 0.0
    beats_placebo = (
        regime_vs_const[1] >= BEATS_PLACEBO_T
        and regime_vs_rand[1] >= BEATS_PLACEBO_T
    )
    dsr_pass = dsr.get("derisk_regime", 0.0) >= DSR_GATE
    deployable = mdd_within_cap and net_pnl_positive and beats_placebo and dsr_pass
    return {
        "mdd_within_cap": mdd_within_cap,
        "regime_mdd": regime.max_drawdown_pct,
        "net_pnl_positive": net_pnl_positive,
        "regime_net_pnl_yuan": regime.net_pnl_yuan,
        "beats_placebo": beats_placebo,
        "beats_placebo_t_bar": BEATS_PLACEBO_T,
        "dsr_pass": dsr_pass,
        "regime_dsr": dsr.get("derisk_regime", float("nan")),
        "deployable_edge": deployable,
        "summary": (
            "Regime de-risk earns a deployable edge: MDD within the 8% cap, net "
            "P&L positive, beat both placebos at the strict t-bar, cleared DSR≥0.95."
            if deployable
            else "Regime de-risk does NOT earn an independent deployable edge — it "
            "fails one or more pre-committed gates (MDD ≤ 8% / net P&L > 0 / beats "
            "BOTH placebos strict-t / DSR ≥ 0.95). The improvement is an "
            "exposure-reduction artifact (no timing skill beyond constant/random "
            "hold-cash), OR the fully-invested rotation arena cannot de-risk fast "
            "enough to reach the cap. Reported as FAIL, not laundered."
        ),
    }


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    use_vol_variant: bool = False,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full regime de-risk event-loop ablation → a JSON-able result dict."""
    log("[1/7] load + firewall panel (train_val only)")
    panel = pd.read_csv(
        panel_path, dtype={"date": str, "code": str, "ts_code": str}
    )
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/7] neutralize survivors (industry SW-L1 + log size)")
    factors = [*xv.RANKER_FACTORS, xv.CROWD_FACTOR]
    neut = neutralize_panel(
        panel, factors, min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    ranker_table = xv.build_ranker_table(neut)
    if smoke_periods is not None:
        keep = set(
            sorted(ranker_table["date"].astype(str).unique())[:smoke_periods]
        )
        ranker_table = ranker_table[
            ranker_table["date"].astype(str).isin(keep)
        ].copy()

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    train_val = set(split.train_val_dates)
    daily_days = _resolve_window(rebs, calendar, train_val=train_val)
    split.assert_all_not_test(daily_days)
    universe = (*xv.panel_universe(ranker_table), CSI300_ETF)
    log(
        f"      rebal={len(rebs)} daily={len(daily_days)} "
        f"universe={len(universe)}"
    )

    log("[3/7] build PIT bar source (heavy: full daily window) + cash overlay")
    store = SnapshotStore(snapshot_root)
    base_bar_source = PitBarSource(
        store=store, trading_days=daily_days, universe=universe
    )
    bar_source = do.CashAugmentedBarSource(base_bar_source)
    log("      bar source ready")

    log("[4/7] PIT regime detection (CSI300 ETF trailing drawdown, no look-ahead)")
    closes = read_market_closes(store, daily_days, market_code=REGIME_MARKET_CODE)
    coverage = _assert_market_coverage(len(closes), len(daily_days))
    regimes_market = classify_regimes(closes)
    regime_hi = high_risk_dates(
        regimes_market, rebs, use_vol_variant=use_vol_variant
    )
    cash_dates_by_arm = _cash_dates_by_arm(rebs, regime_hi)
    log(
        f"      market_days={len(closes)}/{len(daily_days)} ({coverage:.1%}) "
        f"high_risk_rebs={len(regime_hi)}/{len(rebs)} "
        f"vol_variant={use_vol_variant}"
    )

    log("[5/7] build arms (regime / 2 placebos) + run event loop")
    arms_scores, arms_health = _build_arms(ranker_table, cash_dates_by_arm)
    arm_results: list[ArmResult] = []
    raw_results: dict[str, GateBacktestResult] = {}
    cash_intents: dict[str, int] = {}
    for label in ARM_LABELS:
        gate = _run_arm(
            arms_scores[label], arms_health[label], bar_source=bar_source
        )
        raw_results[label] = gate
        arm_results.append(_arm_from_gate(label, gate))
        a = arm_results[-1]
        # Computed once here and reused in the result dict (codex efficiency #7).
        cash_intents[label] = do.cash_buy_intent_count(
            gate.backtest_result.decision_vectors
        )
        log(
            f"      {label:18s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} cash_intents={cash_intents[label]} "
            f"cash_dates={len(cash_dates_by_arm[label])}"
        )

    _assert_treatment_fired(len(regime_hi), cash_intents["derisk_regime"])

    log("[6/7] run deployable baselines (beta hurdle)")
    baseline_results = _run_baselines(
        ranker_table, daily_days, bar_source=bar_source
    )
    for a in baseline_results:
        log(
            f"      {a.label:18s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%}"
        )

    log("[7/7] stats: SPA/Romano-Wolf + DSR-HAC + non-zeroing ledger + regimes")
    all_arms = arm_results + baseline_results
    by_label = {a.label: a for a in all_arms}
    bench = by_label["csi300_etf_hold"].period_returns
    cand_returns = [by_label[f].period_returns for f in ARM_LABELS]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    _assert_enough_periods(nmin)
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=list(ARM_LABELS),
        family=LEDGER_FAMILY,
    )
    window = (rebs[0], rebs[-1])
    n_trials = _ledger_n_trials(
        ledger_path, arm_results, window, persist=smoke_periods is None
    )
    lag = _overlap_lag(HORIZON, REBALANCE_FREQ)
    dsr = {
        a.label: deflated_sharpe_hac(
            list(a.period_returns), n_trials=n_trials, hac_lag=lag
        )
        for a in arm_results
    }
    regimes = _classify_regimes(bench)
    regime_vs_const = _paired_t(
        by_label["derisk_regime"].period_returns,
        by_label["placebo_constant"].period_returns,
    )
    regime_vs_rand = _paired_t(
        by_label["derisk_regime"].period_returns,
        by_label["placebo_random"].period_returns,
    )
    regime_vs_base = _paired_t(
        by_label["derisk_regime"].period_returns,
        by_label["baseline"].period_returns,
    )

    return {
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
        },
        "regime": {
            "market_code": REGIME_MARKET_CODE,
            "high_risk_rebalance_dates": len(regime_hi),
            "total_rebalance_dates": len(rebs),
            "use_vol_variant": use_vol_variant,
            "cash_dates_per_arm": {
                k: len(v) for k, v in cash_dates_by_arm.items()
            },
            "cash_buy_intents_per_arm": dict(cash_intents),
        },
        "arms": {
            a.label: asdict(a)
            | {"period_returns": None, "n_periods": len(a.period_returns)}
            for a in all_arms
        },
        "dsr": dsr,
        "n_trials_deflation": n_trials,
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(ARM_LABELS, cmp.rw_rejected, strict=True)),
        "rw_adjusted_p": dict(zip(ARM_LABELS, cmp.rw_adjusted_pvalues, strict=True)),
        "t_stats_vs_csi300": dict(zip(ARM_LABELS, cmp.t_stats, strict=True)),
        "regime_vs_baseline": {
            "mean_diff": regime_vs_base[0],
            "t": regime_vs_base[1],
        },
        "regime_vs_placebo_constant": {
            "mean_diff": regime_vs_const[0],
            "t": regime_vs_const[1],
        },
        "regime_vs_placebo_random": {
            "mean_diff": regime_vs_rand[0],
            "t": regime_vs_rand[1],
        },
        "regimes": _regime_table(all_arms, regimes),
        "regime_counts": {
            r: regimes.count(r) for r in ("bull", "bear", "sideways")
        },
        "verdict": _verdict(by_label, regime_vs_const, regime_vs_rand, dsr),
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
        "--out", default="data/factor_research/b1_derisk_regime_result.json"
    )
    parser.add_argument("--smoke-periods", type=int, default=None)
    parser.add_argument(
        "--vol-variant",
        action="store_true",
        help="use the disclosure-only drawdown-OR-vol detector (spec §2)",
    )
    args = parser.parse_args()
    result = run_ablation(
        panel_path=args.panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        ledger_path=args.ledger,
        smoke_periods=args.smoke_periods,
        use_vol_variant=args.vol_variant,
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


__all__ = ["ARM_LABELS", "LEDGER_FAMILY", "run_ablation"]
