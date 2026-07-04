"""Confirmatory science-gate for the DEPLOYABLE defensive sleeve (SLV-0 Step 1).

The sleeve (``defensive_sleeve_spec``) freezes a RISK product, not a ranking alpha:
the D1 defensive-universe GATES (dividend-low-vol) + the SIMPLEST selection
(``dv_ratio`` top-5 eq) + the ``buf40_5`` cash buffer. D1 showed the FILTER + buffer
drawdown (buf40_5 MDD 14.78%) with its block-weighted RANKER; this cut MEASURES the risk
property under the deployable SIMPLEST rule — a definition committed BEFORE this run.

Universe honesty (codex): the sleeve applies the D1 exclusion GATES only, a SUPERSET
of D1's ranker-held book (D1 additionally required all 7 ``<factor>_neut`` present — an
artifact of its block ranker the dv_ratio rule doesn't need). NOT a byte-repro
of D1's 14.78%; it measures the sleeve's OWN MDD (buffer-dominated), disclosed as such.

(Distinct from ``defensive_sleeve_ablation`` = the batch-B2 cash-sleeve probe.)

Judging (amendment + ``defensive_sleeve_spec.SCIENCE_GATE`` — a RISK claim, NOT ranking
alpha): net P&L > 0 + bear-regime cumulative ≥ 0 + the mechanical MDD bound holds
(disclosed). DSR/SPA/RW + the sleeve-vs-random / sleeve-vs-sizematched paired-t are
computed and DISCLOSED — beating a random placebo is NOT required (the sleeve makes no
ranking claim; disclosing sleeve vs random shows whether the dv_ratio selection adds any
RISK value over "any 5 defensive-universe names", which the risk framing expects ~0 —
the value is the universe + buffer).

Arms (dual container + exposure-matched placebos):
* **sleeve_buf40_5** — the DEPLOYABLE product (dv_ratio top-5 eq, 60% cash buffer).
* **sleeve_eq_5** — dv_ratio top-5 full investment (the naive within-universe baseline
  the forward kill-switch trails against; shows the MDD WITHOUT the buffer).
* **placebo_random_buf40_5** / **placebo_sizematched_buf40_5** — random / size-matched
  top-5 in the defensive universe at buf40 (does dv_ratio selection add risk value?).
* **csi300_hold** — the beta hurdle.

Reuses the D1 filter (``defensive_d1_ranker.apply_exclusion_gates``) + the
D1 panel + frozen ``run_gate_backtest`` event loop (20d monthly). Train_val only (the
ACTUAL bar-read window is asserted ⊆ train_val ∪ embargo); deterministic; offline; never
the live path. Ledger appended ``ds.defensive_sleeve`` (disclosure).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .exit_veto_ablation import ArmResult

import numpy as np
import pandas as pd

from backend.backtest.strategy import CodeHealth
from backend.marketdata_snapshot.store import SnapshotStore

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
from .defensive_d1_ablation import (
    DefensiveArm,
    _arm_from_gate,
    _paired_t,
    _strategy_config,
    size_matched_scores,
)
from .defensive_sleeve_spec import (
    CONTAINER,
    HORIZON,
    REBALANCE_FREQ,
    SCIENCE_GATE,
    SELECTION_FACTOR,
    SELECTION_TOP_N,
    spec_hash,
)
from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
LEDGER_DATE: str = "2026-07-04"
PLACEBO_SEED: int = 20260704
DSR_GATE: float = 0.95  # disclosure-only (the sleeve makes no ranking claim)

# The RAW columns the D1 exclusion gates + the dv_ratio selection read (no neut:
# the gates + ranking are on RAW values — the sleeve makes no factor-neutral claim).
_GATE_COLS: tuple[str, ...] = ("max_20d", "roe", "gpm", "dv_ratio")
_RANKER_TABLE_COLS: tuple[str, ...] = (
    "date",
    "ts_code",
    "ranker_score",
    "ranker_pct",
    "log_circ_mv",
)
_EQ = "sleeve_eq_5"
_BUF = f"sleeve_{CONTAINER.label}"
_RAND = f"placebo_random_{CONTAINER.label}"
_SM = f"placebo_sizematched_{CONTAINER.label}"


def build_sleeve_ranker_table(panel: pd.DataFrame) -> pd.DataFrame:
    """``(date, ts_code, ranker_score, ranker_pct, log_circ_mv)`` — sleeve selection.

    Per date: apply the D1 defensive-universe exclusion gates (reused, on the RAW
    columns), then the SIMPLEST committed selection — rank by RAW ``dv_ratio`` (higher
    dividend yield = higher score; the universe requires dv_ratio ≥ median, so this
    picks the highest-dividend defensive names). No neutralization, no block ranker.

    UNIVERSE HONESTY (codex): this is the D1 exclusion GATES only — a strict SUPERSET of
    D1's ranker-HELD book, which additionally required all 7 ``<factor>_neut`` present
    (a D1-ranker artifact the simplest dv_ratio rule does not need). A short-history
    high-dividend name D1 dropped (missing accr/beta/tail_beta) can enter here. So the
    confirmatory measures the sleeve's OWN risk (gates + dv_ratio + buffer), NOT
    a byte-repro of D1's 14.78% — the buffer, not the exact book, dominates the MDD.
    The bottom-30% size cut lives in the panel builder (red line ok by construction).
    """
    need = [*_GATE_COLS, "date", "ts_code", "log_circ_mv"]
    missing = [c for c in need if c not in panel.columns]
    if missing:
        raise KeyError(f"panel missing columns: {missing}")
    rows: list[pd.DataFrame] = []
    for _date, grp in panel.groupby("date", sort=True):
        surv = (
            d1r.apply_exclusion_gates(grp)
            .dropna(subset=[SELECTION_FACTOR, "log_circ_mv"])
            .copy()
        )
        surv["ranker_score"] = surv[SELECTION_FACTOR].to_numpy()
        # Fail-closed: never rank on a non-finite dv_ratio (dropna keeps ±inf; drop it).
        surv = surv[np.isfinite(surv["ranker_score"])].copy()
        if surv.empty:
            continue
        surv["ranker_pct"] = surv["ranker_score"].rank(pct=True, method="average")
        rows.append(surv[list(_RANKER_TABLE_COLS)])
    if not rows:
        return pd.DataFrame(columns=list(_RANKER_TABLE_COLS))
    return pd.concat(rows, ignore_index=True)


def _run_arm(
    *,
    scores: dict[str, xv.ScoredDay],
    health: dict[str, dict[str, CodeHealth]] | None,
    slots: int,
    cap_percent: int,
    bar_source: PitBarSource,
) -> GateBacktestResult:
    provider = (
        PanelScoreProvider(scores, health_overrides=health)
        if health is not None
        else PanelScoreProvider(scores)
    )
    return run_gate_backtest(
        bar_source=bar_source,
        provider=provider,
        strategy_config=_strategy_config(slots, cap_percent),
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )


def _resolve_window(
    rebalance_dates: list[str], calendar: tuple[str, ...], *, allowed: set[str]
) -> list[str]:
    """The daily bar-read window (train_val ∪ embargo so the last HORIZON is kept)."""
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in allowed]


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Confirmatory sleeve science-gate ablation → a JSON-able result dict."""
    log(f"[0/6] spec_hash={spec_hash()} (frozen before evaluation)")

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

    log("[2/6] defensive gates + dv_ratio top-5 selection (raw, simplest rule)")
    # No neutralization: gates + dv_ratio ranking are on RAW values (the sleeve
    # makes no factor-neutral / ranking-alpha claim). Passing the raw panel is byte-
    # identical to the old neut path (the ranker never read a _neut column).
    ranker_table = build_sleeve_ranker_table(panel)
    if smoke_periods is not None:
        keep = set(sorted(ranker_table["date"].astype(str).unique())[:smoke_periods])
        ranker_table = ranker_table[ranker_table["date"].astype(str).isin(keep)].copy()
    if ranker_table.empty:
        raise ValueError("sleeve ranker table is empty — fail-closed")
    min_breadth = 2 * SELECTION_TOP_N
    thin = ranker_table.groupby("date").size()
    thin = thin[thin < min_breadth]
    if not thin.empty:
        raise ValueError(
            f"{len(thin)} date(s) carry < {min_breadth} defensive-universe names "
            f"(e.g. {dict(thin.head(3))}) — fail-closed before the bar source"
        )

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    allowed = set(split.train_val_dates) | set(split.embargo_dates)
    daily_days = _resolve_window(rebs, calendar, allowed=allowed)
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = (*xv.panel_universe(ranker_table), CSI300_ETF)
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/6] build PIT bar source (heavy) + sleeve scores/health + placebo scores")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    scores = xv.scores_by_day(ranker_table)
    health = xv.build_health_overrides(ranker_table)
    rand_scores = random_top_n_scores(
        xv.universe_by_day(ranker_table), seed=PLACEBO_SEED, top_n=SELECTION_TOP_N
    )
    sm_scores = size_matched_scores(ranker_table, top_n=SELECTION_TOP_N)

    log("[4/6] run arms (deployable buf40 + eq_5 baseline + random/size placebos)")
    arms: dict[str, DefensiveArm] = {}
    arms[_BUF] = _arm_from_gate(
        _BUF,
        CONTAINER.slots,
        CONTAINER.cap_percent,
        _run_arm(
            scores=scores,
            health=health,
            slots=CONTAINER.slots,
            cap_percent=CONTAINER.cap_percent,
            bar_source=bar_source,
        ),
    )
    arms[_EQ] = _arm_from_gate(
        _EQ,
        5,
        100,
        _run_arm(
            scores=scores,
            health=health,
            slots=5,
            cap_percent=100,
            bar_source=bar_source,
        ),
    )
    arms[_RAND] = _arm_from_gate(
        _RAND,
        CONTAINER.slots,
        CONTAINER.cap_percent,
        _run_arm(
            scores=rand_scores,
            health=None,
            slots=CONTAINER.slots,
            cap_percent=CONTAINER.cap_percent,
            bar_source=bar_source,
        ),
    )
    arms[_SM] = _arm_from_gate(
        _SM,
        CONTAINER.slots,
        CONTAINER.cap_percent,
        _run_arm(
            scores=sm_scores,
            health=None,
            slots=CONTAINER.slots,
            cap_percent=CONTAINER.cap_percent,
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
    all_labels = (_BUF, _EQ, _RAND, _SM, "csi300_hold")
    for label in all_labels:
        a = arms[label]
        log(
            f"      {label:28s} slots={a.slots:2d} cap={a.cap_percent:3d} "
            f"netPnL={a.net_pnl_yuan:+,.0f} MDD={a.max_drawdown_pct:.2%} "
            f"turn={a.monthly_turnover:.2f} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} cons={a.conservation_ok}"
        )

    log("[5/6] deflation N (non-zeroing ledger) + DSR + SPA/RW + regimes/slices")
    window = (rebs[0], rebs[-1])
    n_trials = ledger_n_trials(
        ledger_path,
        cast("Sequence[ArmResult]", [arms[_BUF], arms[_EQ]]),
        window,
        persist=smoke_periods is None,
        family="ds.defensive_sleeve",
        round_label="ds-sleeve",
        description="deployable defensive sleeve confirmatory science gate",
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
    fam_labels = [label for label in all_labels if label != "csi300_hold"]
    cand_returns = [arms[label].period_returns for label in fam_labels]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=fam_labels,
        family="ds.defensive_sleeve",
    )
    regimes = _classify_regimes(bench)
    n_periods = len(arms[_BUF].period_returns)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]
    ordered_arms = [arms[label] for label in all_labels]
    regime_tbl = _regime_table(ordered_arms, regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(ordered_arms, period_dates)  # type: ignore[arg-type]

    log("[6/6] assemble result + science-gate read")
    return {
        "spec_hash": spec_hash(),
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
        "dsr_role": "disclosure_only",
        "conservation_ok": all(a.conservation_ok for a in arms.values()),
        "arms": {
            label: asdict(arms[label])
            | {"period_returns": None, "n_periods": len(arms[label].period_returns)}
            for label in all_labels
        },
        "spa_p_value": cmp.spa_p_value,
        "rw_rejected": dict(zip(fam_labels, cmp.rw_rejected, strict=True)),
        "t_stats_vs_csi300": dict(zip(fam_labels, cmp.t_stats, strict=True)),
        "regimes": regime_tbl,
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "crash_slices": crash_tbl,
        "read": _read(arms, regime_tbl, crash_tbl),
    }


def _read(
    arms: dict[str, DefensiveArm],
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    """The RISK-property science-gate read for the deployable sleeve (buf40_5).

    Science gate (amendment): net P&L > 0 + bear-regime cumulative ≥ 0 + mechanical MDD
    bound disclosed. sleeve-vs-random / sleeve-vs-sizematched are DISCLOSED (does the
    dv_ratio selection add RISK value over random within the universe?), NOT gated —
    the sleeve makes no ranking-alpha claim.
    """
    buf = arms[_BUF]
    bear_bucket = regime_tbl[_BUF]["bear"]
    bear_n = int(bear_bucket["n"])
    bear_cum = float(bear_bucket["sum_return"])
    crash_buckets = crash_tbl[_BUF]
    crash_cums = {name: v["cum_return"] for name, v in crash_buckets.items()}
    tested = {name: v["cum_return"] for name, v in crash_buckets.items() if v["n"] > 0}
    mdd = buf.max_drawdown_pct
    net_pos = buf.net_pnl_yuan > 0.0
    bear_ok = bear_n > 0 and bear_cum >= 0.0
    return {
        "note": (
            "DIAGNOSTIC — owner judges. Science gate = net>0 + bear≥0 + mechanical "
            "MDD bound disclosed (a RISK claim). sleeve-vs-random disclosed, NOT gated."
        ),
        "deployable_arm": _BUF,
        "net_pnl_yuan": buf.net_pnl_yuan,
        "net_pnl_positive": net_pos,
        "max_drawdown_pct": mdd,
        "mdd_disclose_bound": SCIENCE_GATE.mdd_disclose_bound,
        "mdd_within_bound": mdd <= SCIENCE_GATE.mdd_disclose_bound,
        "avg_exposure": buf.avg_exposure,
        "dsr": buf.dsr,  # disclosure-only
        "bear_regime_n": bear_n,
        "bear_regime_cumulative": bear_cum,
        "bear_regime_nonneg": bear_ok,
        "crash_slice_cumulative": crash_cums,
        "crash_slices_tested": sorted(tested),
        "all_crash_slices_nonneg": bool(tested)
        and all(v >= 0.0 for v in tested.values()),
        "science_gate_pass": bool(
            net_pos and bear_ok
        ),  # owner criteria (MDD disclosed)
        "vs_random_t": _paired_t(buf.period_returns, arms[_RAND].period_returns)[1],
        "vs_sizematched_t": _paired_t(buf.period_returns, arms[_SM].period_returns)[1],
        "eq5_mdd_no_buffer": arms[_EQ].max_drawdown_pct,
        "buffer_mdd_reduction": arms[_EQ].max_drawdown_pct - mdd,
    }


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
        "--out", default="data/factor_research/defensive_sleeve_result.json"
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
    print(json.dumps(result["read"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "build_sleeve_ranker_table",
    "run_ablation",
]
