"""Analyst-revision-momentum ablation (dual container + own-random selection gate).

Ranking candidate #2 (owner-directed after DS-D2 branch (c)): does an information-flow
factor — the *change* in broker analyst consensus, ``{np_rev, rev_diff, cover_chg}``
committed +1 (round-4 R4-4 orthogonal subset, reused verbatim) — earn a book-layer edge
where the price-derived reversal factor did NOT? Same dev-selection protocol as D2
(amendment 2026-07-04): the promotion decision is the SELECTION gate (beats own
exposure-matched random top-5, paired-t≥2, both containers joint) + the owner criteria
(bear cumulative ≥ 0 / crash slices not-crashing / net P&L > 0); DSR/SPA/RW
only (pre-declared to FAIL).

Replays the committed equal-weight signed-z ranker through the frozen event loop
(T+1 / per-board slippage / ¥5 min commission / ≤1 rotation/day) at the monthly
(20d) horizon under the two committed containers — ``eq_5`` + ``buf40_5``:
(deployment gate, ≈40% gross / 60% cash buffer) — plus exposure-matched placebos:

* **placebo_random_{c}** — uniform random top-5 (seeded) from the SAME analyst-covered
  universe, at the container's slots/cap: the SELECTION main gate.
* **placebo_sizematched_{c}** — 5 names size-matched to the analyst top-5: controls the
  size-tilt channel (the R5 trap; analyst coverage is large-cap-skewed).
* **csi300_hold** — full-invested buy-and-hold of 510300.SH (the beta hurdle).

There is NO A0 byte anchor here (unlike D2) — analyst momentum has no ``slot_frontier``
equivalent; its baseline IS its own random placebo. An empty crash / bear bucket reads
UNTESTED (n>0 guard), never a false pass. Coverage caveat: the ranker requires
all three analyst factors present, so the universe is analyst-covered (large-cap)
names — sparse in 2015/2016 (~6-8%), ~45% from 2018. Train_val only (the ACTUAL bar-read
window incl. the HORIZON extension asserted ⊆ train_val); deterministic; offline; never
the live path.
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

from . import exit_veto_panel as xv
from .analyst_momentum_spec import (
    BEATS_PLACEBO_T,
    CONTAINERS,
    HORIZON,
    PLACEBO_SEED,
    PLACEBO_TOP_N,
    RANKER_FACTORS,
    REBALANCE_FREQ,
    factor_sign,
    spec_hash,
)
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
from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .honest_gates import deflated_sharpe_hac
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies
from .neutralize import neutralize_panel

CSI300_ETF: str = "510300.SH"
INITIAL_CAPITAL_YUAN: float = 1_000_000.0
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
LEDGER_DATE: str = "2026-07-04"
DSR_GATE: float = 0.95  # main anti-overfit gate (NOT relaxed; disclosure per amendment)

_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in RANKER_FACTORS)
_NEUT_COLS: tuple[str, ...] = tuple(f"{n}_neut" for n in _FACTOR_NAMES)
_RANKER_TABLE_COLS: tuple[str, ...] = (
    "date",
    "ts_code",
    "ranker_score",
    "ranker_pct",
    "log_circ_mv",
)
# The candidate arms that debit the ledger (placebos/hold are hurdles, not searched).
_LEDGER_LABELS = tuple(f"am_{c.label}" for c in CONTAINERS)


def _am(container: str) -> str:
    return f"am_{container}"


def _rand(container: str) -> str:
    return f"placebo_random_{container}"


def _sm(container: str) -> str:
    return f"placebo_sizematched_{container}"


def build_analyst_ranker_table(neut_panel: pd.DataFrame) -> pd.DataFrame:
    """``(date, ts_code, ranker_score, ranker_pct, log_circ_mv)`` — committed ranker.

    Per date: drop any name missing one of the three ``<factor>_neut`` columns (the
    analyst-covered cross-section), then rank on the committed equal-weight signed
    z-blend (``mean_f sign_f · zscore(neut_f)``; higher = attractive). ``ranker_pct``
    is the within-date percentile in [0, 1]. An empty date contributes no rows.
    """
    need = [*_NEUT_COLS, "date", "ts_code", "log_circ_mv"]
    missing = [c for c in need if c not in neut_panel.columns]
    if missing:
        raise KeyError(f"neut_panel missing columns: {missing}")
    rows: list[pd.DataFrame] = []
    for _date, grp in neut_panel.groupby("date", sort=True):
        surv = grp.dropna(subset=list(_NEUT_COLS)).copy()
        if surv.empty:
            continue
        blend = pd.concat(
            [factor_sign(n) * xv._zscore(surv[f"{n}_neut"]) for n in _FACTOR_NAMES],
            axis=1,
        ).mean(axis=1)
        surv["ranker_score"] = blend.to_numpy()
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
    """One arm through the frozen event loop at the committed monthly horizon."""
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
    """The daily bar-read window (train_val ∪ embargo so the last HORIZON is kept).

    ``allowed`` = train_val ∪ embargo; the caller asserts ⊆ (train_val ∪ embargo); the
    embargo is the non-test buffer for the final forward label — the sealed test
    is never read. (No frontier byte anchor, so the D1 embargo-extended window is used
    — the last rebalance's full 20d forward window not clipped at the train_val edge.)
    """
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
    """Full analyst-momentum ablation → a JSON-able result dict (the report's data)."""
    log(f"[0/6] spec_hash={spec_hash()} (frozen before evaluation)")

    log("[1/6] load + firewall panel (train_val only)")
    usecols = ["date", "code", "ts_code", *_FACTOR_NAMES, "industry_l1", "log_circ_mv"]
    panel = pd.read_csv(
        panel_path, dtype={"date": str, "code": str, "ts_code": str}, usecols=usecols
    )
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/6] neutralize the 3 analyst factors (industry SW-L1 + log size)")
    neut = neutralize_panel(
        panel, list(_FACTOR_NAMES), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    ranker_table = build_analyst_ranker_table(neut)
    full_covered_rows = len(ranker_table)  # pre-truncation (honest coverage disclosure)
    if smoke_periods is not None:
        keep = set(sorted(ranker_table["date"].astype(str).unique())[:smoke_periods])
        ranker_table = ranker_table[ranker_table["date"].astype(str).isin(keep)].copy()
    if ranker_table.empty:
        raise ValueError("analyst ranker table is empty — fail-closed")

    # Min-breadth feasibility guard (fail-fast, BEFORE the heavy bar source): each arm
    # is a ≤5 book plus an exposure-matched size placebo needing PLACEBO_TOP_N
    # non-selected size matches, so a date must carry ≥ 2·PLACEBO_TOP_N covered names.
    # Analyst coverage is documented-sparse (codex); this turns a would-be mid-run
    # size-match abort into an upfront error instead of dying ~40min into the replay.
    min_breadth = 2 * PLACEBO_TOP_N
    thin = ranker_table.groupby("date").size()
    thin = thin[thin < min_breadth]
    if not thin.empty:
        raise ValueError(
            f"{len(thin)} date(s) carry < {min_breadth} analyst-covered names "
            f"(e.g. {dict(thin.head(3))}) — too thin for the exposure-matched size "
            "placebo; fail-closed before the bar source."
        )

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    allowed = set(split.train_val_dates) | set(split.embargo_dates)
    daily_days = _resolve_window(rebs, calendar, allowed=allowed)
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = (*xv.panel_universe(ranker_table), CSI300_ETF)
    # Coverage = FULL-panel analyst-covered fraction (not the smoke-truncated table).
    covered_frac = full_covered_rows / max(len(panel), 1)
    log(
        f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)} "
        f"covered_rows(full)={full_covered_rows}/{len(panel)} ({covered_frac:.1%})"
    )

    log("[3/6] build PIT bar source (heavy) + analyst scores/health + placebo scores")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    am_scores = xv.scores_by_day(ranker_table)
    am_health = xv.build_health_overrides(ranker_table)
    rand_scores = random_top_n_scores(
        xv.universe_by_day(ranker_table), seed=PLACEBO_SEED, top_n=PLACEBO_TOP_N
    )
    sm_scores = size_matched_scores(ranker_table, top_n=PLACEBO_TOP_N)

    log(
        "[4/6] run arms (per container: analyst + matched random/size placebos)"
    )
    arms: dict[str, DefensiveArm] = {}
    for c in CONTAINERS:
        arms[_am(c.label)] = _arm_from_gate(
            _am(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=am_scores,
                health=am_health,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_rand(c.label)] = _arm_from_gate(
            _rand(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=rand_scores,
                health=None,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_sm(c.label)] = _arm_from_gate(
            _sm(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=sm_scores,
                health=None,
                slots=c.slots,
                cap_percent=c.cap_percent,
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
    all_labels = (
        *(f(c.label) for c in CONTAINERS for f in (_am, _rand, _sm)),
        "csi300_hold",
    )
    for label in all_labels:
        a = arms[label]
        log(
            f"      {label:26s} slots={a.slots:2d} cap={a.cap_percent:3d} "
            f"netPnL={a.net_pnl_yuan:+,.0f} MDD={a.max_drawdown_pct:.2%} "
            f"turn={a.monthly_turnover:.2f} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} cons={a.conservation_ok}"
        )

    log("[5/6] deflation N (non-zeroing ledger) + DSR + SPA/RW + regimes/slices")
    window = (rebs[0], rebs[-1])
    ledger_arms = [arms[label] for label in _LEDGER_LABELS]
    n_trials = ledger_n_trials(
        ledger_path,
        cast("Sequence[ArmResult]", ledger_arms),
        window,
        persist=smoke_periods is None,
        family="ds.analyst_momentum",
        round_label="ds-analyst-momentum",
        description="analyst-revision-momentum dual-container ablation",
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
    # Benchmark is NOT a candidate (a degenerate all-zero excess member enlarges the
    # SPA/RW family) — compare only the real arms vs CSI300 (codex D2 lesson).
    fam_labels = [label for label in all_labels if label != "csi300_hold"]
    cand_returns = [arms[label].period_returns for label in fam_labels]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=fam_labels,
        family="ds.analyst_momentum",
    )
    regimes = _classify_regimes(bench)
    n_periods = len(arms[_am("eq_5")].period_returns)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]
    ordered_arms = [arms[label] for label in all_labels]
    regime_tbl = _regime_table(ordered_arms, regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(ordered_arms, period_dates)  # type: ignore[arg-type]

    log("[6/6] assemble result + diagnostic read")
    return {
        "spec_hash": spec_hash(),
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "covered_row_fraction": covered_frac,
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
        "rw_adjusted_p": dict(zip(fam_labels, cmp.rw_adjusted_pvalues, strict=True)),
        "t_stats_vs_csi300": dict(zip(fam_labels, cmp.t_stats, strict=True)),
        "regimes": regime_tbl,
        "regime_counts": {r: regimes.count(r) for r in ("bull", "bear", "sideways")},
        "crash_slices": crash_tbl,
        "read": _read(arms, regime_tbl, crash_tbl),
    }


def _candidate_read(
    arm: DefensiveArm,
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
    *,
    vs_own_random: tuple[float, float],
    vs_sizematched: tuple[float, float],
) -> dict[str, object]:
    """The selection/owner-criterion diagnostic for one container (NOT a verdict).

    An empty crash / bear bucket reads UNTESTED (n>0 guard), never a false pass.
    """
    bear_bucket = regime_tbl[arm.label]["bear"]
    bear_n = int(bear_bucket["n"])
    bear_cum = float(bear_bucket["sum_return"])
    crash_buckets = crash_tbl[arm.label]
    crash_cums = {name: v["cum_return"] for name, v in crash_buckets.items()}
    tested = {name: v["cum_return"] for name, v in crash_buckets.items() if v["n"] > 0}
    return {
        "net_pnl_yuan": arm.net_pnl_yuan,
        "net_pnl_positive": arm.net_pnl_yuan > 0.0,
        "max_drawdown_pct": arm.max_drawdown_pct,  # disclosure-only
        "avg_exposure": arm.avg_exposure,
        "dsr": arm.dsr,
        "dsr_pass": arm.dsr >= DSR_GATE,  # disclosure-only (pre-declared FAIL)
        "bear_regime_n": bear_n,
        "bear_regime_cumulative": bear_cum,
        "bear_regime_nonneg": bear_n > 0 and bear_cum >= 0.0,
        "crash_slice_cumulative": crash_cums,
        "crash_slices_tested": sorted(tested),
        "all_crash_slices_nonneg": bool(tested)
        and all(v >= 0.0 for v in tested.values()),
        "vs_own_random_t": vs_own_random[1],
        "vs_sizematched_t": vs_sizematched[1],
        "beats_own_random_strict": vs_own_random[1] >= BEATS_PLACEBO_T,
    }


def _read(
    arms: dict[str, DefensiveArm],
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    """A terse machine read of the amendment's selection gate + owner criteria.

    Diagnostic only — owner judges promotion. Selection main gate = beats OWN random
    placebo (joint t≥2, both containers). ``candidate_edge`` = joint selection gate AND
    owner criteria (net>0 + bear≥0 + crash slices nonneg, both containers). DSR/SPA/RW
    disclosure-only (pre-declared FAIL).
    """
    out: dict[str, object] = {
        "note": (
            "DIAGNOSTIC — owner judges per amendment "
            "qgr-certification-rearch-amendment-2026-07-04. Selection gate = beats OWN "
            "random placebo (joint t>=2). DSR/SPA/RW disclosure-only (pre-declared)."
        )
    }
    containers: dict[str, dict[str, object]] = {}
    for c in CONTAINERS:
        arm = arms[_am(c.label)]
        containers[c.label] = _candidate_read(
            arm,
            regime_tbl,
            crash_tbl,
            vs_own_random=_paired_t(
                arm.period_returns, arms[_rand(c.label)].period_returns
            ),
            vs_sizematched=_paired_t(
                arm.period_returns, arms[_sm(c.label)].period_returns
            ),
        )
    joint = all(
        bool(containers[c.label]["beats_own_random_strict"]) for c in CONTAINERS
    )
    owner_gates = all(
        bool(containers[c.label]["net_pnl_positive"])
        and bool(containers[c.label]["bear_regime_nonneg"])
        and bool(containers[c.label]["all_crash_slices_nonneg"])
        for c in CONTAINERS
    )
    out["containers"] = containers
    out["beats_own_random_joint"] = joint
    out["owner_gates_pass"] = owner_gates
    out["candidate_edge"] = joint and owner_gates
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_analyst_momentum.csv"
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--out", default="data/factor_research/analyst_momentum_result.json"
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
    "build_analyst_ranker_table",
    "run_ablation",
]
