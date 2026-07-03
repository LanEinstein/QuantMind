"""D1 dividend-low-vol defensive ablation (dual container + size/random placebos).

Replays the committed D1 block-weighted defensive ranker (``defensive_d1_ranker``)
through the frozen ``run_gate_backtest`` event loop (T+1 / per-board slippage /
¥5 min commission / ≤1 rotation/day) at the monthly (20d) horizon under the two
committed containers — ``eq_5`` (science gate, ~full investment) and ``buf40_5``
(deployment gate, 5 slots × 8% cap ≈ 40% gross / 60% cash buffer, the P-E floor) —
plus skill-free placebos that isolate the defensive SELECTION from size/mechanics:

* **placebo_sizematched_{container}** — per date, 5 names size-matched (nearest
  ``log_circ_mv``) to the D1 top-5, run at the SAME slots/cap as the container it
  controls, so an edge cannot be "just the low-vol / size tilt" (the project's
  recurring size-trap, R5) NOR a difference in gross exposure.
* **placebo_random_{container}** — uniform random top-5 (seeded) from the SAME
  post-exclusion defensive universe, at the container's slots/cap: controls "any 5
  defensive-universe names + ≤5-slot mechanics at matched exposure".
* **csi300_hold** — full-invested buy-and-hold of 510300.SH (the beta hurdle).

Reports per arm: net P&L / MDD / turnover / realized exposure / DSR (deflated on the
non-zeroing ledger, family ``ds.d1_dividend_lowvol``) + bull/bear/sideways regime
stratification + the six §A6 crash slices + SPA / Romano-Wolf vs the CSI300 hold. The
``read`` block is keyed on the owner D1 criterion (bear-regime cumulative ≥ 0 / each
crash slice not-crashing / net P&L > 0 / beats both placebos) and MDD is disclosure-
only (criterion-rebar) — it is a DIAGNOSTIC surface for the owner to judge, NOT an
auto-verdict. Train_val only (sealed test never read; the ACTUAL bar-read window incl.
the HORIZON extension is asserted ⊆ train_val); deterministic; offline; never the live
path.

DEVIATION (see report): the pure-reversal **A0 baseline is DEFERRED** (it needs its
own crowding/QGR panel + a second heavy bar source over a different universe); the
``--a0-panel`` hook is a documented stub. The size-matched + random + CSI300 baselines
cover the size / mechanics / beta nulls; the orchestrator can run the A0 cross-family
comparison via ``slot_frontier`` / ``exit_veto_ablation`` on the QGR panel.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .exit_veto_ablation import ArmResult

import numpy as np
import pandas as pd

from backend.backtest.strategy import CodeHealth, StrategyConfig
from backend.marketdata_snapshot.store import SnapshotStore
from backend.slot_portfolio import load_rotation_policy_config

from . import defensive_d1_ranker as d1r
from . import exit_veto_panel as xv
from .arena_ablation import ledger_n_trials
from .avoid_top_ablation import (
    _classify_regimes,
    _crash_slice_table,
    _regime_table,
)
from .baselines import buy_and_hold_baseline, random_top_n_scores
from .crowding_factor_diagnostics import _overlap_lag
from .defensive_d1_spec import CONTAINERS, HORIZON, RANKER_FACTORS
from .gate_backtest import (
    DEFAULT_ROTATION_CONFIG_PATH,
    GateBacktestResult,
    PanelScoreProvider,
    default_selector,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies
from .neutralize import neutralize_panel

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
REBALANCE_FREQ: int = 20  # monthly (matches HORIZON) — the D1 rebalance cadence
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
LEDGER_DATE: str = "2026-07-03"
PLACEBO_SEED: int = 20260703
DSR_GATE: float = 0.95  # main anti-overfitting gate (NOT relaxed)
BEATS_PLACEBO_T: float = 2.0  # strict one-sided paired-t (not the lenient t>1)
PLACEBO_TOP_N: int = 5  # ≤5-slot container size for the placebo draws

# The D1 container arms (report order); placebo labels are derived per container so
# each D1 arm is compared to an EXPOSURE-MATCHED (same slots/cap) placebo (codex P1).
D1_ARMS: tuple[str, ...] = ("eq_5", "buf40_5")


def _sm_label(container: str) -> str:
    return f"placebo_sizematched_{container}"


def _rd_label(container: str) -> str:
    return f"placebo_random_{container}"


@dataclass(frozen=True)
class DefensiveArm:
    """One arm's arena outcome (net-P&L / MDD / turnover + the period series)."""

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
    """Live-parity strategy config with the shortlist widened to ``slots`` (see
    ``slot_frontier._strategy_config``: at ``slots=5`` this is the C1a selector)."""
    return StrategyConfig(
        selector=default_selector(
            final_shortlist_size=slots, min_quant_slots=min(3, slots)
        ),
        rotation=load_rotation_policy_config(DEFAULT_ROTATION_CONFIG_PATH),
        max_total_positions=slots,
        single_stock_cap_percent=cap_percent,
    )


def _arm_from_gate(
    label: str, slots: int, cap_percent: int, g: GateBacktestResult
) -> DefensiveArm:
    return DefensiveArm(
        label=label,
        slots=slots,
        cap_percent=cap_percent,
        net_pnl_yuan=g.net_pnl_yuan,
        max_drawdown_pct=g.max_drawdown_pct,
        monthly_turnover=g.monthly_turnover,
        fill_count=g.fill_count,
        avg_exposure=g.backtest_result.avg_exposure_ratio,
        conservation_ok=g.conservation_ok,
        dsr=0.0,  # filled after the deflation N is known
        period_returns=g.period_returns,
    )


def _resolve_window(
    rebalance_dates: list[str], calendar: tuple[str, ...], *, allowed: set[str]
) -> list[str]:
    """The daily days the event loop replays (MTM + T+1) over the ``allowed`` set.

    ``allowed`` = train_val ∪ embargo, so the last rebalance's full HORIZON forward
    window is NOT truncated at the train_val boundary (codex P2) — the embargo is
    the non-test buffer reserved exactly for this forward-label read. The caller
    still funnels the result through ``assert_all_not_test`` (embargo is not test)."""
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in allowed]


def size_matched_scores(
    ranker_table: pd.DataFrame, *, top_n: int
) -> dict[str, xv.ScoredDay]:
    """Per-date placebo: ``top_n`` names size-matched to the D1 top-``top_n`` picks.

    For each date the D1 top-``top_n`` (by ``ranker_score``) are the "selected"; each
    is greedily matched to its nearest-``log_circ_mv`` NON-selected name (reusing the
    tested :func:`exit_veto_panel._size_matched_draw`). The matched names get a flat
    high score (the selector takes the top-N) — a deterministic size control that
    holds the size distribution while discarding the defensive signal.
    """
    out: dict[str, xv.ScoredDay] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        g = grp.sort_values(["ranker_score", "ts_code"], ascending=[False, True])
        selected = set(g["ts_code"].astype(str).head(top_n))
        pool = g[~g["ts_code"].astype(str).isin(selected)]
        matched = xv._size_matched_draw(g, selected, pool, len(selected))
        # Fail-closed (codex P2): a short match would change the arm's exposure
        # (fewer than top_n names) and confound the de-exposure comparison.
        if len(matched) != len(selected):
            raise ValueError(
                f"size-matched placebo could not fill {len(selected)} names on "
                f"{date} (got {len(matched)}) — fail-closed"
            )
        out[str(date)] = [(code, 1.0) for code in sorted(matched)]
    return out


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


def _run_d1_arm(
    slots: int,
    cap_percent: int,
    *,
    scores: dict[str, xv.ScoredDay],
    health: dict[str, dict[str, CodeHealth]],
    bar_source: PitBarSource,
) -> GateBacktestResult:
    provider = PanelScoreProvider(scores, health_overrides=health)
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=_strategy_config(slots, cap_percent),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _run_placebo_arm(
    scores: dict[str, xv.ScoredDay],
    *,
    slots: int,
    cap_percent: int,
    bar_source: PitBarSource,
) -> GateBacktestResult:
    """A placebo at the SAME slots/cap as the D1 container it controls (codex P1).

    Runs with DEFAULT provider health (no forced-rotation overrides) — the skill-
    free posture of ``exit_veto_ablation._run_baselines`` — but at the container's
    exposure (cap_percent), so a D1 arm is compared to an exposure-MATCHED placebo
    (esp. buf40_5's 40%-gross cap vs a 40%-gross placebo, not a 100%-gross one)."""
    provider = PanelScoreProvider(scores)
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=_strategy_config(slots, cap_percent),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    a0_panel: str | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full D1 defensive ablation → a JSON-able result dict (the report's data)."""
    if a0_panel is not None:
        raise NotImplementedError(
            "the pure-reversal A0 baseline is deferred (needs its own QGR/crowding "
            "panel + a second bar source over a different universe) — run it via "
            "slot_frontier / exit_veto_ablation on the QGR panel instead."
        )

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

    log("[2/6] neutralize the 7 defensive factors (industry SW-L1 + log size)")
    factors = [f.name for f in RANKER_FACTORS]
    neut = neutralize_panel(
        panel, factors, min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    ranker_table = d1r.build_defensive_ranker_table(neut)
    if smoke_periods is not None:
        keep = set(sorted(ranker_table["date"].astype(str).unique())[:smoke_periods])
        ranker_table = ranker_table[
            ranker_table["date"].astype(str).isin(keep)
        ].copy()
    if ranker_table.empty:
        raise ValueError("ranker table is empty after exclusion gates — fail-closed")

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    allowed = set(split.train_val_dates) | set(split.embargo_dates)
    daily_days = _resolve_window(rebs, calendar, allowed=allowed)
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = (*d1r.panel_universe(ranker_table), CSI300_ETF)
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/6] build PIT bar source (heavy) + D1 scores/health + placebo scores")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    d1_scores = d1r.scores_by_day(ranker_table)
    d1_health = d1r.build_health_overrides(ranker_table)
    sm_scores = size_matched_scores(ranker_table, top_n=PLACEBO_TOP_N)
    rand_scores = random_top_n_scores(
        d1r.universe_by_day(ranker_table), seed=PLACEBO_SEED, top_n=PLACEBO_TOP_N
    )

    log("[4/6] run arms (per container: D1 + exposure-matched size/random placebos)")
    arms: dict[str, DefensiveArm] = {}
    for container in CONTAINERS:
        g = _run_d1_arm(
            container.slots,
            container.cap_percent,
            scores=d1_scores,
            health=d1_health,
            bar_source=bar_source,
        )
        arms[container.label] = _arm_from_gate(
            container.label, container.slots, container.cap_percent, g
        )
        # Exposure-matched placebos at the SAME slots/cap as this container.
        arms[_sm_label(container.label)] = _arm_from_gate(
            _sm_label(container.label),
            container.slots,
            container.cap_percent,
            _run_placebo_arm(
                sm_scores,
                slots=container.slots,
                cap_percent=container.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_rd_label(container.label)] = _arm_from_gate(
            _rd_label(container.label),
            container.slots,
            container.cap_percent,
            _run_placebo_arm(
                rand_scores,
                slots=container.slots,
                cap_percent=container.cap_percent,
                bar_source=bar_source,
            ),
        )
    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=CSI300_ETF,
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )
    arms["csi300_hold"] = DefensiveArm(
        label="csi300_hold",
        slots=1,
        cap_percent=100,
        net_pnl_yuan=bh.net_pnl_yuan,
        max_drawdown_pct=bh.max_drawdown_pct,
        monthly_turnover=0.0,
        fill_count=bh.fill_count,
        avg_exposure=bh.invested_fraction,
        conservation_ok=bh.conservation_ok,
        dsr=0.0,
        period_returns=bh.period_returns,
    )
    placebo_labels = tuple(
        lbl
        for c in CONTAINERS
        for lbl in (_sm_label(c.label), _rd_label(c.label))
    )
    all_labels = (*D1_ARMS, *placebo_labels, "csi300_hold")
    for label in all_labels:
        a = arms[label]
        log(
            f"      {label:22s} slots={a.slots:2d} cap={a.cap_percent:3d} "
            f"netPnL={a.net_pnl_yuan:+,.0f} MDD={a.max_drawdown_pct:.2%} "
            f"turn={a.monthly_turnover:.2f} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} cons={a.conservation_ok}"
        )

    log("[5/6] deflation N (non-zeroing ledger) + DSR + SPA/RW + regimes/slices")
    window = (rebs[0], rebs[-1])
    # DSR is deflated on the D1 candidate arms only (the placebos are skill-free
    # hurdles, not searched configs).
    d1_result_arms = [arms[label] for label in D1_ARMS]
    n_trials = ledger_n_trials(
        ledger_path,
        # ledger_n_trials only reads ``.period_returns`` (structural); DefensiveArm
        # satisfies that shape — cast past the nominal ArmResult annotation.
        cast("Sequence[ArmResult]", d1_result_arms),
        window,
        persist=smoke_periods is None,
        family="ds.d1_dividend_lowvol",
        round_label="ds-d1",
        description="D1 dividend-low-vol defensive dual-container ablation",
        ledger_date=LEDGER_DATE,
    )
    lag = _overlap_lag(HORIZON, REBALANCE_FREQ)
    arms = {
        label: replace(
            a,
            dsr=deflated_sharpe_hac(
                list(a.period_returns), n_trials=n_trials, hac_lag=lag
            ),
        )
        for label, a in arms.items()
    }

    bench = arms["csi300_hold"].period_returns
    fam_labels = list(all_labels)
    cand_returns = [arms[label].period_returns for label in fam_labels]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=fam_labels,
        family="ds.d1_dividend_lowvol",
    )
    regimes = _classify_regimes(bench)
    n_periods = len(arms["eq_5"].period_returns)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]
    ordered_arms = [arms[label] for label in all_labels]
    regime_tbl = _regime_table(ordered_arms, regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(ordered_arms, period_dates)  # type: ignore[arg-type]

    log("[6/6] assemble result + diagnostic read")
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
        "conservation_ok": all(a.conservation_ok for a in arms.values()),
        "arms": {
            label: asdict(arms[label])
            | {
                "period_returns": None,
                "n_periods": len(arms[label].period_returns),
            }
            for label in all_labels
        },
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(fam_labels, cmp.rw_rejected, strict=True)),
        "rw_adjusted_p": dict(zip(fam_labels, cmp.rw_adjusted_pvalues, strict=True)),
        "t_stats_vs_csi300": dict(zip(fam_labels, cmp.t_stats, strict=True)),
        "regimes": regime_tbl,
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "crash_slices": crash_tbl,
        "read": _read(arms, regime_tbl, crash_tbl),
    }


def _arm_read(
    arm: DefensiveArm,
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
    *,
    vs_sizematched: tuple[float, float],
    vs_random: tuple[float, float],
) -> dict[str, object]:
    """The owner-criterion diagnostic for one D1 container (NOT an auto-verdict)."""
    bear_cum = float(regime_tbl[arm.label]["bear"]["sum_return"])
    crash_cums = {name: v["cum_return"] for name, v in crash_tbl[arm.label].items()}
    beats_both = (
        vs_sizematched[1] >= BEATS_PLACEBO_T and vs_random[1] >= BEATS_PLACEBO_T
    )
    return {
        "net_pnl_yuan": arm.net_pnl_yuan,
        "net_pnl_positive": arm.net_pnl_yuan > 0.0,
        "max_drawdown_pct": arm.max_drawdown_pct,  # disclosure-only
        "dsr": arm.dsr,
        "dsr_pass": arm.dsr >= DSR_GATE,
        "bear_regime_cumulative": bear_cum,
        "bear_regime_nonneg": bear_cum >= 0.0,
        "crash_slice_cumulative": crash_cums,
        "all_crash_slices_nonneg": all(v >= 0.0 for v in crash_cums.values()),
        "vs_placebo_sizematched_t": vs_sizematched[1],
        "vs_placebo_random_t": vs_random[1],
        "beats_both_placebos_strict_t": beats_both,
    }


def _read(
    arms: dict[str, DefensiveArm],
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    """A terse machine read of the owner D1 criterion for both containers.

    Diagnostic surface only — the owner judges promotion (bear cumulative ≥ 0 AND
    each crash slice not-crashing AND net P&L > 0 AND beats both placebos → advance to
    holdout; else FAIL → next candidate). MDD is disclosure-only (criterion-rebar).
    """
    out: dict[str, object] = {
        "note": (
            "DIAGNOSTIC, not a verdict — owner judges promotion per DS-D1 §8. "
            "A0 pure-reversal cross-family baseline is deferred (see module docstring)."
        )
    }
    for label in D1_ARMS:
        arm = arms[label]
        # Compare each container to its EXPOSURE-MATCHED placebos (codex P1).
        vs_sm = _paired_t(
            arm.period_returns, arms[_sm_label(label)].period_returns
        )
        vs_rd = _paired_t(arm.period_returns, arms[_rd_label(label)].period_returns)
        out[label] = _arm_read(
            arm, regime_tbl, crash_tbl, vs_sizematched=vs_sm, vs_random=vs_rd
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_defensive_d1.csv"
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--out", default="data/factor_research/defensive_d1_result.json"
    )
    parser.add_argument(
        "--a0-panel",
        default=None,
        help="(deferred) QGR/crowding panel for the pure-reversal A0 baseline",
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
        a0_panel=args.a0_panel,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["read"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "DefensiveArm",
    "run_ablation",
    "size_matched_scores",
]
