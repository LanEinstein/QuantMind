"""QGR-4 first cut — EXIT-veto event-loop ablation (3 arms + placebo + baselines).

Cashes out the batch-A finding (``ideal_amplitude_20d`` is a real orthogonal,
size-neutral crowding EXIT axis — A2 PASS) into the owner criterion (absolute net
P&L + MDD ≤ 8%, the CSI300-excess hard gate dropped to disclosure-only) by
replaying it through the **real** QGR-2 arena event loop (≤5 slots / 5td rotation /
T+1 / per-board slippage / 涨停不可成交). It does NOT search candidates — it is a
falsification ablation (main-force-intent §6.6):

* **baseline** — the QGR-3 fast-leg ranker ``{rev_1d, max_5d, turn_spike}`` alone.
* **exit_veto** — same ranker, top-crowding decile removed from the BUY set.
* **placebo_random / placebo_sizematched** — same per-date removal COUNT, drawn
  randomly / size-matched, so a veto effect can be told apart from "buy fewer
  names → less exposure" (and from a size tilt — the project's R5 trap).

Verdict (pre-committed): the veto earns a deployable edge ONLY if it does not hurt
net P&L, improves (or holds) MDD, **and beats the placebos** on that trade-off;
otherwise the effect is an exposure/size artifact and is reported as such (FAIL is
reported, not laundered — QGR principle #1). All arms share ONE PIT bar source.
Deterministic, offline, train_val only (the sealed test window is never read; a
real OOS / forward confirmation is the deferred B-layer gate). Never the live path.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.backtest.strategy import CodeHealth
from backend.marketdata_snapshot.store import SnapshotStore

from . import exit_veto_panel as xv
from .baselines import buy_and_hold_baseline, random_top_n_scores, single_asset_scores
from .crowding_factor_diagnostics import _overlap_lag
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
from .trial_ledger import TrialLedger, TrialRecord

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
HORIZON: int = 5
REBALANCE_FREQ: int = 5
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
MDD_CAP: float = 0.08  # owner criterion (P0-6 §2.8; qgr-2 freeze §1.2)
LEDGER_DATE: str = "2026-06-26"
PLACEBO_SEED: int = 20260626
DSR_GATE: float = 0.95  # main anti-overfitting gate (CLAUDE.md principle #1)
# A deployable veto must clear net-P&L tolerance + a STRICT beats-placebo paired-t.
PNL_HURT_TOL_FRAC: float = 0.02  # ≤2% of initial capital (sign-safe, not %-of-base)
BEATS_PLACEBO_T: float = 2.0  # ~p<0.025 one-sided — not the lenient t>1 of a noise edge
# Market-regime thresholds on the trailing-4-period CSI300 cumulative return
# (look-back only, no look-ahead): bull / bear bands, else sideways. Pre-committed.
REGIME_LOOKBACK_PERIODS: int = 4
BULL_BAND: float = 0.03
BEAR_BAND: float = -0.03


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
    exposure_cap_violations: int
    period_returns: tuple[float, ...]
    mdd_within_cap: bool


def _arm_from_gate(label: str, r: GateBacktestResult) -> ArmResult:
    return ArmResult(
        label=label,
        net_pnl_yuan=r.net_pnl_yuan,
        total_return=r.total_return,
        max_drawdown_pct=r.max_drawdown_pct,
        monthly_turnover=r.monthly_turnover,
        fill_count=r.fill_count,
        avg_exposure=r.backtest_result.avg_exposure_ratio,
        conservation_ok=r.conservation_ok,
        exposure_cap_violations=r.exposure_cap_violations,
        period_returns=r.period_returns,
        mdd_within_cap=r.max_drawdown_pct <= MDD_CAP,
    )


def _resolve_window(
    rebalance_dates: list[str], calendar: tuple[str, ...], *, train_val: set[str]
) -> list[str]:
    """The daily trading days the event loop replays (MTM + T+1 fills).

    Scores live only on the rebalance dates, but the bar source replays EVERY daily
    day from the first rebalance through ``HORIZON`` td past the last (so the final
    positions get marked / filled). The read window is **clamped to train_val** and
    asserted by the caller — the HORIZON extension must never read sealed test bytes.
    """
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in train_val]


def _build_arms(
    ranker_table: pd.DataFrame,
) -> tuple[
    dict[str, dict[str, xv.ScoredDay]],
    dict[str, dict[str, CodeHealth]],
    dict[str, int],
]:
    """Per-arm ``scores_by_day`` + the shared health overrides + per-date veto count.

    The base scores are grouped+sorted ONCE; the veto / placebo arms are derived by
    filtering the dropped codes out of that single sorted table (order-preserving).
    """
    veto = xv.veto_codes_by_day(ranker_table)
    placebo_rand = xv.placebo_codes_by_day(
        ranker_table, veto, seed=PLACEBO_SEED, size_matched=False
    )
    placebo_size = xv.placebo_codes_by_day(
        ranker_table, veto, seed=PLACEBO_SEED, size_matched=True
    )
    base = xv.scores_by_day(ranker_table)
    arms = {
        "baseline": base,
        "exit_veto": xv.drop_from_scores(base, veto),
        "placebo_random": xv.drop_from_scores(base, placebo_rand),
        "placebo_sizematched": xv.drop_from_scores(base, placebo_size),
    }
    health = xv.build_health_overrides(ranker_table)
    return arms, health, xv.removed_counts(veto)


def _run_arm(
    scores: dict[str, xv.ScoredDay],
    health: dict[str, dict[str, CodeHealth]],
    *,
    bar_source: PitBarSource,
) -> GateBacktestResult:
    provider = PanelScoreProvider(scores, health_overrides=health)
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=default_strategy_config(),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _veto_bite(
    base: GateBacktestResult, veto: GateBacktestResult
) -> dict[str, int]:
    """How much the veto actually changed BUY decisions vs the baseline (mechanism).

    Compares the per-day decided ``buy_codes`` of the two arms — the honest read on
    whether the reversal ranker's top-5 ever overlaps the vetoed crowding decile.
    """
    base_buys = {
        d.trade_date: set(d.buy_codes) for d in base.backtest_result.decision_vectors
    }
    veto_buys = {
        d.trade_date: set(d.buy_codes) for d in veto.backtest_result.decision_vectors
    }
    days_changed = 0
    names_dropped = 0
    total_base_buys = 0
    for day, bset in base_buys.items():
        total_base_buys += len(bset)
        vset = veto_buys.get(day, set())
        if bset != vset:
            days_changed += 1
            names_dropped += len(bset - vset)
    return {
        "days_buy_set_changed": days_changed,
        "baseline_buy_names_dropped_by_veto": names_dropped,
        "total_baseline_buys": total_base_buys,
    }


def _run_baselines(
    ranker_table: pd.DataFrame, daily_days: list[str], *, bar_source: PitBarSource
) -> list[ArmResult]:
    """Deployable skill-free hurdles (codex P1): random top-5, ETF-in-slot, ETF hold."""
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
            mdd_within_cap=bh.max_drawdown_pct <= MDD_CAP,
        )
    )
    return out


def _classify_regimes(beta_periods: tuple[float, ...]) -> list[str]:
    """Per-period market regime from the trailing-4-period beta cumret (look-back)."""
    labels: list[str] = []
    for i in range(len(beta_periods)):
        lo = max(0, i - REGIME_LOOKBACK_PERIODS)
        trail = beta_periods[lo:i]
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
        per = arm.period_returns
        buckets: dict[str, list[float]] = {"bull": [], "bear": [], "sideways": []}
        for i, r in enumerate(per):
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
    ledger_path: str,
    arms: list[ArmResult],
    window: tuple[str, str],
    *,
    persist: bool,
) -> int:
    """Append the ``qgr.exit_veto`` family (ONC-deduped) → non-zeroing deflation N.

    ``persist=False`` (a smoke / sub-window run) computes the deflation N but does
    NOT write the spurious sub-window record to the real ledger (its window key would
    otherwise pollute the content-addressed trial set).
    """
    ledger = TrialLedger.with_legacy(ledger_path)
    matrix = [list(a.period_returns) for a in arms]
    eff = onc_effective_n(matrix) if len(matrix) > 1 else len(matrix)
    if persist:
        ledger.append(
            TrialRecord(
                round_label="qgr-4",
                kind="ablation",
                family="qgr.exit_veto",
                description="EXIT-veto event-loop ablation (baseline/veto/2 placebos)",
                n_nominal_trials=len(arms),
                window_start=window[0],
                window_end=window[1],
                registered_at=LEDGER_DATE,
                effective_n=eff,
            )
        )
    return ledger.deflation_n_trials(onc_effective_n=eff)


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full event-loop ablation → a JSON-able result dict (the report's data)."""
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

    log("[2/7] neutralize survivors + crowding axis (industry SW-L1 + log size)")
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
    # Firewall the ACTUAL bar-read window, not just the rebalance dates: the
    # HORIZON extension past the last rebalance must never read sealed test bytes.
    split.assert_all_not_test(daily_days)
    universe = (*xv.panel_universe(ranker_table), CSI300_ETF)
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/7] build PIT bar source (heavy: full daily window)")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    log("      bar source ready")

    log("[4/7] build arms (veto / 2 placebos) + run event loop")
    arms_scores, health, veto_counts = _build_arms(ranker_table)
    arm_results: list[ArmResult] = []
    raw_results: dict[str, GateBacktestResult] = {}
    for label in ("baseline", "exit_veto", "placebo_random", "placebo_sizematched"):
        gate = _run_arm(arms_scores[label], health, bar_source=bar_source)
        raw_results[label] = gate
        arm_results.append(_arm_from_gate(label, gate))
        a = arm_results[-1]
        log(
            f"      {label:20s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} turn={a.monthly_turnover:.2f} "
            f"fills={a.fill_count} exp={a.avg_exposure:.2f} cons={a.conservation_ok}"
        )

    log("[5/7] run deployable baselines (beta hurdle)")
    baseline_results = _run_baselines(ranker_table, daily_days, bar_source=bar_source)
    for a in baseline_results:
        log(
            f"      {a.label:20s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} fills={a.fill_count}"
        )

    log("[6/7] stats: SPA/Romano-Wolf + DSR-HAC + non-zeroing ledger + regimes")
    all_arms = arm_results + baseline_results
    by_label = {a.label: a for a in all_arms}
    bench = by_label["csi300_etf_hold"].period_returns
    family = ["baseline", "exit_veto", "placebo_random", "placebo_sizematched"]
    cand_returns = [by_label[f].period_returns for f in family]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=family,
        family="qgr.exit_veto",
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
    # Regimes from the arena's OWN beta series (csi300_etf_hold period returns) so
    # they are segmentation-aligned with every arm's period_returns by construction
    # (no separately-filtered CSV that could drift in length/calendar).
    regimes = _classify_regimes(bench)
    veto_vs_base = _paired_t(
        by_label["exit_veto"].period_returns, by_label["baseline"].period_returns
    )
    veto_vs_pr = _paired_t(
        by_label["exit_veto"].period_returns, by_label["placebo_random"].period_returns
    )
    veto_vs_ps = _paired_t(
        by_label["exit_veto"].period_returns,
        by_label["placebo_sizematched"].period_returns,
    )

    log("[7/7] assemble result")
    return {
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
        },
        "veto": {
            "total_vetoed": int(sum(veto_counts.values())),
            "mean_per_date": float(np.mean(list(veto_counts.values())))
            if veto_counts
            else 0.0,
            "top_q": xv.TOP_CROWD_Q,
            "bite": _veto_bite(raw_results["baseline"], raw_results["exit_veto"]),
        },
        "arms": {
            a.label: asdict(a)
            | {"period_returns": None, "n_periods": len(a.period_returns)}
            for a in all_arms
        },
        "dsr": dsr,
        "n_trials_deflation": n_trials,
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(family, cmp.rw_rejected, strict=True)),
        "rw_adjusted_p": dict(zip(family, cmp.rw_adjusted_pvalues, strict=True)),
        "t_stats_vs_csi300": dict(zip(family, cmp.t_stats, strict=True)),
        "veto_vs_baseline": {"mean_diff": veto_vs_base[0], "t": veto_vs_base[1]},
        "veto_vs_placebo_random": {"mean_diff": veto_vs_pr[0], "t": veto_vs_pr[1]},
        "veto_vs_placebo_sizematched": {"mean_diff": veto_vs_ps[0], "t": veto_vs_ps[1]},
        "regimes": _regime_table(all_arms, regimes),
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "verdict": _verdict(by_label, veto_vs_ps, veto_vs_pr, dsr),
    }


def _verdict(
    by_label: dict[str, ArmResult],
    veto_vs_ps: tuple[float, float],
    veto_vs_pr: tuple[float, float],
    dsr: dict[str, float],
) -> dict[str, object]:
    """Pre-committed §6.6 verdict — FAIL is reported, not laundered.

    A deployable EXIT-veto edge requires ALL of: (1) net P&L not materially hurt
    (a sign-safe band measured against initial capital, NOT a fraction of a possibly
    negative baseline P&L); (2) MDD improved and within the 8% cap; (3) beats BOTH
    placebos at a STRICT paired-t bar (the effect is not exposure/size noise); and
    (4) the veto arm clears the DSR≥0.95 main anti-overfitting gate (CLAUDE.md
    principle #1 — a low-DSR pass is provisional and must NOT be sold as deployable).
    """
    base, veto = by_label["baseline"], by_label["exit_veto"]
    pnl_tol = INITIAL_CAPITAL_YUAN * PNL_HURT_TOL_FRAC
    no_pnl_hurt = veto.net_pnl_yuan >= base.net_pnl_yuan - pnl_tol
    mdd_improved = veto.max_drawdown_pct <= base.max_drawdown_pct
    beats_placebo = (
        veto_vs_ps[1] >= BEATS_PLACEBO_T and veto_vs_pr[1] >= BEATS_PLACEBO_T
    )
    dsr_pass = dsr.get("exit_veto", 0.0) >= DSR_GATE
    deployable = (
        no_pnl_hurt
        and mdd_improved
        and veto.mdd_within_cap
        and beats_placebo
        and dsr_pass
    )
    return {
        "no_pnl_hurt": no_pnl_hurt,
        "mdd_improved": mdd_improved,
        "mdd_within_cap": veto.mdd_within_cap,
        "beats_placebo": beats_placebo,
        "beats_placebo_t_bar": BEATS_PLACEBO_T,
        "dsr_pass": dsr_pass,
        "exit_veto_dsr": dsr.get("exit_veto", float("nan")),
        "deployable_edge": deployable,
        "summary": (
            "EXIT-veto earns a deployable edge: net P&L held, MDD improved within "
            "cap, beat both placebos at the strict t-bar, and cleared DSR≥0.95."
            if deployable
            else "EXIT-veto does NOT earn an independent deployable edge at ≤5-slot "
            "scale — it fails one or more pre-committed gates (net P&L / MDD / "
            "beats-placebo strict-t / DSR≥0.95). The effect is within placebo noise "
            "/ an exposure-reduction artifact, OR the reversal ranker already prices "
            "crowding. Reported as FAIL, not laundered."
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
        "--out", default="data/factor_research/qgr4_exit_veto_result.json"
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


__all__ = ["ArmResult", "run_ablation"]
