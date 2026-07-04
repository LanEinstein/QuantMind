"""D2 reversal-on-defensive-universe ablation (A0 byte anchor + evidence-gap placebos).

The re-characterised D2 cut (amendment 2026-07-04): does the ≤5-slot reversal RANKING
layer earn its place on the defensive sleeve? Done in ONE ablation / ONE bar source /
ONE window that runs, side by side through the frozen ``run_gate_backtest`` event loop
(T+1 / per-board slippage / ¥5 min commission / ≤1 rotation/day) at the A0 weekly (5d)
cadence:

* **a0_{container}** — the pure-reversal book over the FULL universe. Its ranker table
  is built by REUSING ``exit_veto_panel.build_ranker_table`` on the same neutralized
  crowding panel ``slot_frontier`` used, so its net P&L / MDD are a **byte anchor** to
  ``slot_frontier_result.json`` (asserted fail-closed before any interpretation).
* **d2_{container}** — the candidate: the SAME reused ranker, but ranking only within
  the D2 defensive universe (``apply_d2_universe_filter`` — the sole difference vs A0).
* **placebo_random_a0_{container}** — uniform random top-5 from the FULL universe. This
  fills the *evidence gap*: A0 pure reversal was never compared to a random placebo at
  the book layer (QGR-3 validated IC sign; C1a/QGR-4 placebos were EXIT-layer; the
  frontier had no placebo arm; the D1 ablation explicitly DEFERRED it). Debt paid here.
* **placebo_random_d2_{container}** — random top-5 from the DEFENSIVE universe: the D2
  selection main hurdle ("any 5 defensive-universe names + ≤5-slot mechanics").
* **placebo_sizematched_d2_{container}** — 5 names size-matched to the D2 top-5:
  controls the size-tilt channel (the project's recurring R5 trap).
* **csi300_hold** — full-invested buy-and-hold of 510300.SH (the beta hurdle).

Every placebo runs at the SAME slots/cap (exposure) as the container it controls (the D1
codex-P1 lesson). Reports per arm: net P&L / MDD / turnover / realized exposure / DSR +
regime stratification + the six crash slices + SPA / Romano-Wolf vs the CSI300 hold. Per
the amendment DSR/SPA/RW are DISCLOSURE ONLY (pre-declared to FAIL); the promotion
decision is the SELECTION gate (beats own random placebo, joint t≥2) + owner criteria
(bear cumulative ≥ 0 / crash slices not-crashing / net P&L > 0), which the owner judges
from the pre-registered three-branch read. Train_val only (the ACTUAL bar-read window
incl. the HORIZON extension is asserted ⊆ train_val); deterministic; offline.
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

import pandas as pd

from backend.backtest.strategy import CodeHealth, StrategyConfig
from backend.marketdata_snapshot.store import SnapshotStore
from backend.slot_portfolio import load_rotation_policy_config

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
    size_matched_scores,
)
from .defensive_d2_spec import (
    BEATS_PLACEBO_T,
    CONTAINERS,
    D2_UNIVERSE_FILTER,
    HORIZON,
    PLACEBO_SEED,
    PLACEBO_TOP_N,
    REBALANCE_FREQ,
    spec_hash,
)
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
WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
LEDGER_DATE: str = "2026-07-04"
DSR_GATE: float = 0.95  # main anti-overfit gate (NOT relaxed; disclosure per amendment)

# The neutralized factor set — one-for-one with ``slot_frontier`` so A0's ranker table
# (and thus the a0_{container} arms) are byte-reproducible.
NEUT_FACTORS: tuple[str, ...] = (*xv.RANKER_FACTORS, xv.CROWD_FACTOR)

# Raw filter columns the D2 universe filter reads (must be present in the panel).
_FILTER_COLS: tuple[str, ...] = ("vol_20d", "max_20d", "dv_ratio", "roe", "gpm")

# The two containers whose D2 arms debit the ledger (a0 / placebos are baselines).
_D2_LEDGER_LABELS = tuple(f"d2_{c.label}" for c in CONTAINERS)


def _a0(container: str) -> str:
    return f"a0_{container}"


def _d2(container: str) -> str:
    return f"d2_{container}"


def _rand_a0(container: str) -> str:
    return f"placebo_random_a0_{container}"


def _rand_d2(container: str) -> str:
    return f"placebo_random_d2_{container}"


def _sm_d2(container: str) -> str:
    return f"placebo_sizematched_d2_{container}"


def _strategy_config(slots: int, cap_percent: int) -> StrategyConfig:
    """Live-parity config — byte-identical to ``slot_frontier._strategy_config``.

    The shortlist is widened to ``slots`` candidates/day (at ``slots=5`` this is the C1a
    selector), so the a0 arms reproduce the frontier's eq_5 / buf40_5 exactly.
    """
    return StrategyConfig(
        selector=default_selector(
            final_shortlist_size=slots, min_quant_slots=min(3, slots)
        ),
        rotation=load_rotation_policy_config(DEFAULT_ROTATION_CONFIG_PATH),
        max_total_positions=slots,
        single_stock_cap_percent=cap_percent,
    )


def apply_d2_universe_filter(neut: pd.DataFrame) -> pd.DataFrame:
    """Keep only the defensive universe per date (the sole D2 change vs A0).

    Committed thresholds (``D2_UNIVERSE_FILTER``), per-date quantiles of the present
    (non-NaN) values on the RAW columns. A name is KEPT iff it clears BOTH always-on
    exclusions (low-vol keep + non-lottery) AND at least one defensive-quality branch
    (dividend ≥ median OR roe > 0 ∧ gpm above its bottom decile). Fail-closed: a missing
    ``vol_20d`` / ``max_20d`` drops the name; a missing branch input fails that branch.
    """
    f = D2_UNIVERSE_FILTER
    missing = [c for c in _FILTER_COLS if c not in neut.columns]
    if missing:
        raise KeyError(f"panel missing D2 filter columns: {missing}")
    kept: list[pd.DataFrame] = []
    for _date, grp in neut.groupby("date", sort=True):
        vol, mx = grp["vol_20d"], grp["max_20d"]
        dv, roe, gpm = grp["dv_ratio"], grp["roe"], grp["gpm"]
        false = pd.Series(False, index=grp.index)
        # Always-on excludes (missing input → dropped).
        vol_ok = (
            vol.notna() & (vol <= float(vol.quantile(f.vol_keep_max_quantile)))
            if vol.notna().any()
            else false
        )
        mx_ok = (
            mx.notna() & (mx < float(mx.quantile(f.max20d_lottery_exclude_quantile)))
            if mx.notna().any()
            else false
        )
        # Defensive-quality inclusion branches (a name needs at least one).
        branch_div = (
            dv.notna() & (dv >= float(dv.quantile(f.dividend_min_percentile)))
            if dv.notna().any()
            else false
        )
        gpm_ok = (
            gpm.notna() & (gpm > float(gpm.quantile(f.gpm_floor_quantile)))
            if gpm.notna().any()
            else false
        )
        branch_qual = roe.notna() & (roe > f.roe_floor) & gpm_ok
        keep = vol_ok & mx_ok & (branch_div | branch_qual)
        kept.append(grp[keep])
    if not kept:
        return neut.iloc[0:0].copy()
    return pd.concat(kept, ignore_index=False)


def _run_arm(
    *,
    scores: dict[str, xv.ScoredDay],
    health: dict[str, dict[str, CodeHealth]] | None,
    slots: int,
    cap_percent: int,
    bar_source: PitBarSource,
) -> GateBacktestResult:
    """One arm through the frozen event loop at the D2 (=A0) weekly horizon.

    ``health`` drives the real weakness+margin rotation gate for the a0/d2 candidate
    arms; the placebos run with DEFAULT health (skill-free) at matched exposure.
    """
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
    rebalance_dates: list[str], calendar: tuple[str, ...], *, train_val: set[str]
) -> list[str]:
    """The daily bar-read window — byte-identical to ``slot_frontier._resolve_window``.

    Clamped to train_val ONLY (NOT train_val ∪ embargo): this reproduces the frontier's
    exact window so the a0 arms byte-match ``slot_frontier_result.json``. The caller
    asserts ⊆ train_val (the sealed test is never read).
    """
    cal = sorted(calendar)
    first, last = rebalance_dates[0], rebalance_dates[-1]
    last_idx = cal.index(last) if last in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    return [d for d in cal if first <= d <= end and d in train_val]


def _assert_a0_byte_anchor(
    arms: dict[str, DefensiveArm], frontier_path: str
) -> dict[str, dict[str, float]]:
    """Fail-closed: the a0 arms MUST byte-reproduce ``slot_frontier_result.json``.

    A mismatch means the neutralization / window / engine config drifted from the
    frontier path — STOP before any interpretation (build-sheet §6.1). Returns the
    disclosed frontier values for the report.
    """
    frontier = json.loads(Path(frontier_path).read_text(encoding="utf-8"))
    configs = frontier["configs"]
    checked: dict[str, dict[str, float]] = {}
    for container in ("eq_5", "buf40_5"):
        ref = configs[container]
        got = arms[_a0(container)]
        if got.net_pnl_yuan != ref["net_pnl_yuan"]:
            raise ValueError(
                f"A0 byte anchor FAILED for {container}: net "
                f"{got.net_pnl_yuan!r} != {ref['net_pnl_yuan']!r} — fail-closed"
            )
        if got.max_drawdown_pct != ref["max_drawdown_pct"]:
            raise ValueError(
                f"A0 byte anchor FAILED for {container}: MDD "
                f"{got.max_drawdown_pct!r} != frontier "
                f"{ref['max_drawdown_pct']!r} — fail-closed"
            )
        checked[container] = {
            "net_pnl_yuan": ref["net_pnl_yuan"],
            "max_drawdown_pct": ref["max_drawdown_pct"],
        }
    return checked


def _load_working_panel(crowding_panel_path: str, d2_panel_path: str) -> pd.DataFrame:
    """The A0-exact crowding panel spliced with D2's dv_ratio / roe / gpm columns.

    The A0 byte anchor requires the four neut factors (rev_1d / max_5d / turn_spike /
    ideal_amplitude_20d) + industry + log size to be BIT-identical to what
    ``slot_frontier`` read from ``panel_train_val_crowding.csv``. The D2 panel is that
    same crowding frame re-serialized to CSV, which round-trips floats lossily (~1e-15),
    enough to flip a top-5 tie and break the anchor. So the A0 / neut base is read
    straight from the crowding CSV (frontier's exact input), and only the three RAW D2
    filter columns are spliced in by position (row order asserted identical). Those cols
    feed the filter only (no byte anchor), so their round-trip is harmless.
    """
    crowding = pd.read_csv(
        crowding_panel_path, dtype={"date": str, "code": str, "ts_code": str}
    )
    d2 = pd.read_csv(d2_panel_path, dtype={"date": str, "code": str, "ts_code": str})
    if len(crowding) != len(d2):
        raise ValueError(
            f"crowding rows {len(crowding)} != d2 rows {len(d2)} — fail-closed"
        )
    key = ["date", "ts_code"]
    if not (
        crowding[key].reset_index(drop=True) == d2[key].reset_index(drop=True)
    ).all().all():
        raise ValueError(
            "crowding and d2 panels are not row-aligned (date, ts_code) — fail-closed"
        )
    for col in ("dv_ratio", "roe", "gpm"):
        if col in crowding.columns:
            raise ValueError(f"crowding panel already carries {col} — fail-closed")
        crowding[col] = d2[col].to_numpy()
    return crowding


def run_ablation(
    *,
    panel_path: str,
    crowding_panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    frontier_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """Full D2 ablation → a JSON-able result dict (the report's data)."""
    log(f"[0/6] spec_hash={spec_hash()} (frozen before evaluation)")

    log("[1/6] load (A0-exact crowding + D2 filter cols) + firewall (train_val only)")
    panel = _load_working_panel(crowding_panel_path, panel_path)
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/6] neutralize (A0-parity) → a0 table + D2-filtered table")
    neut = neutralize_panel(
        panel, list(NEUT_FACTORS), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    a0_table = xv.build_ranker_table(neut)
    d2_table = xv.build_ranker_table(apply_d2_universe_filter(neut))
    if smoke_periods is not None:
        keep = set(sorted(a0_table["date"].astype(str).unique())[:smoke_periods])
        a0_table = a0_table[a0_table["date"].astype(str).isin(keep)].copy()
        d2_table = d2_table[d2_table["date"].astype(str).isin(keep)].copy()
    if a0_table.empty or d2_table.empty:
        raise ValueError("a0 or d2 ranker table empty — fail-closed")

    # d2 ⊆ a0 (same date × ts_code universe) and equal, non-empty date sets (§5.2).
    a0_keys = set(
        zip(a0_table["date"].astype(str), a0_table["ts_code"].astype(str), strict=True)
    )
    d2_keys = set(
        zip(d2_table["date"].astype(str), d2_table["ts_code"].astype(str), strict=True)
    )
    if not d2_keys <= a0_keys:
        raise ValueError("d2 universe is NOT a subset of a0 universe — fail-closed")
    a0_dates = set(a0_table["date"].astype(str))
    d2_dates = set(d2_table["date"].astype(str))
    if a0_dates != d2_dates:
        missing = sorted(a0_dates - d2_dates)[:3]
        raise ValueError(
            f"d2 filter emptied {len(a0_dates - d2_dates)} date(s) (e.g. {missing}) "
            "— fail-closed (coverage / limit anomaly)"
        )

    rebs = sorted(a0_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    daily_days = _resolve_window(rebs, calendar, train_val=set(split.train_val_dates))
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = (*xv.panel_universe(a0_table), CSI300_ETF)
    d2_frac = len(d2_keys) / max(len(a0_keys), 1)
    log(
        f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)} "
        f"d2_rows/a0_rows={len(d2_keys)}/{len(a0_keys)} ({d2_frac:.1%})"
    )

    log("[3/6] build PIT bar source (heavy) + a0/d2 scores/health + placebo scores")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    a0_scores = xv.scores_by_day(a0_table)
    a0_health = xv.build_health_overrides(a0_table)
    d2_scores = xv.scores_by_day(d2_table)
    d2_health = xv.build_health_overrides(d2_table)
    rand_a0 = random_top_n_scores(
        xv.universe_by_day(a0_table), seed=PLACEBO_SEED, top_n=PLACEBO_TOP_N
    )
    rand_d2 = random_top_n_scores(
        xv.universe_by_day(d2_table), seed=PLACEBO_SEED, top_n=PLACEBO_TOP_N
    )
    sm_d2 = size_matched_scores(d2_table, top_n=PLACEBO_TOP_N)

    log("[4/6] run 11 arms (per container: a0 / d2 / rand-a0 / rand-d2 / sm-d2)")
    arms: dict[str, DefensiveArm] = {}
    for c in CONTAINERS:
        arms[_a0(c.label)] = _arm_from_gate(
            _a0(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=a0_scores,
                health=a0_health,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_d2(c.label)] = _arm_from_gate(
            _d2(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=d2_scores,
                health=d2_health,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_rand_a0(c.label)] = _arm_from_gate(
            _rand_a0(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=rand_a0,
                health=None,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_rand_d2(c.label)] = _arm_from_gate(
            _rand_d2(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=rand_d2,
                health=None,
                slots=c.slots,
                cap_percent=c.cap_percent,
                bar_source=bar_source,
            ),
        )
        arms[_sm_d2(c.label)] = _arm_from_gate(
            _sm_d2(c.label),
            c.slots,
            c.cap_percent,
            _run_arm(
                scores=sm_d2,
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
        *(
            f(c.label)
            for c in CONTAINERS
            for f in (_a0, _d2, _rand_a0, _rand_d2, _sm_d2)
        ),
        "csi300_hold",
    )
    for label in all_labels:
        a = arms[label]
        log(
            f"      {label:28s} slots={a.slots:2d} cap={a.cap_percent:3d} "
            f"netPnL={a.net_pnl_yuan:+,.0f} MDD={a.max_drawdown_pct:.2%} "
            f"turn={a.monthly_turnover:.2f} exp={a.avg_exposure:.2f} "
            f"fills={a.fill_count} cons={a.conservation_ok}"
        )

    # Byte anchor (full run only; a smoke's short window cannot match the frontier).
    byte_anchor: dict[str, dict[str, float]] | None = None
    if smoke_periods is None:
        byte_anchor = _assert_a0_byte_anchor(arms, frontier_path)
        log(f"      A0 byte anchor OK: {json.dumps(byte_anchor, ensure_ascii=False)}")

    log("[5/6] deflation N (non-zeroing ledger) + DSR + SPA/RW + regimes/slices")
    window = (rebs[0], rebs[-1])
    d2_ledger_arms = [arms[label] for label in _D2_LEDGER_LABELS]
    n_trials = ledger_n_trials(
        ledger_path,
        cast("Sequence[ArmResult]", d2_ledger_arms),
        window,
        persist=smoke_periods is None,
        family="ds.d2_reversal_on_defensive",
        round_label="ds-d2",
        description="D2 reversal-on-defensive-universe dual-container ablation",
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
    # The benchmark is NOT a candidate (codex): including csi300_hold gives an all-zero
    # excess series that enlarges the SPA/Romano-Wolf family with a degenerate member,
    # skews the disclosed multiple-testing stats. Compare only the real arms vs CSI300.
    fam_labels = [label for label in all_labels if label != "csi300_hold"]
    cand_returns = [arms[label].period_returns for label in fam_labels]
    nmin = min(len(bench), *(len(c) for c in cand_returns))
    cmp = compare_strategies(
        candidate_returns=[list(c[:nmin]) for c in cand_returns],
        benchmark_returns=list(bench[:nmin]),
        labels=fam_labels,
        family="ds.d2_reversal_on_defensive",
    )
    regimes = _classify_regimes(bench)
    n_periods = len(arms[_a0("eq_5")].period_returns)
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
            "d2_row_fraction": d2_frac,
            "smoke_periods": smoke_periods,
            "csi300_hold_net_pnl": bh.net_pnl_yuan,
            "csi300_hold_mdd": bh.max_drawdown_pct,
        },
        "a0_byte_anchor": byte_anchor,
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
    vs_sizematched: tuple[float, float] | None = None,
    vs_a0: tuple[float, float] | None = None,
) -> dict[str, object]:
    """The selection/owner-criterion diagnostic for one arm (NOT an auto-verdict).

    A crash slice / bear regime with ZERO periods for this arm is treated as UNTESTED,
    not as a pass (codex: an empty bucket's ``cum_return`` / ``sum_return`` defaults to
    0.0, and ``0.0 >= 0.0`` would falsely read as "survived" a crash never faced).
    So the nonneg checks require ``n > 0`` and the crash check needs at least one tested
    slice — an untested criterion never inflates the promotion read toward advance.
    """
    bear_bucket = regime_tbl[arm.label]["bear"]
    bear_n = int(bear_bucket["n"])
    bear_cum = float(bear_bucket["sum_return"])
    crash_buckets = crash_tbl[arm.label]
    crash_cums = {name: v["cum_return"] for name, v in crash_buckets.items()}
    tested_slices = {
        name: v["cum_return"] for name, v in crash_buckets.items() if v["n"] > 0
    }
    out: dict[str, object] = {
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
        "crash_slices_tested": sorted(tested_slices),
        "all_crash_slices_nonneg": bool(tested_slices)
        and all(v >= 0.0 for v in tested_slices.values()),
        "vs_own_random_t": vs_own_random[1],
        "beats_own_random_strict": vs_own_random[1] >= BEATS_PLACEBO_T,
    }
    if vs_sizematched is not None:
        out["vs_sizematched_t"] = vs_sizematched[1]
    if vs_a0 is not None:
        out["vs_a0_paired_t"] = vs_a0[1]
    return out


def _read(
    arms: dict[str, DefensiveArm],
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    """A terse machine read of the amendment's selection gate + pre-registered branches.

    Diagnostic surface only — owner judges promotion per the amendment. The selection
    main gate = beats OWN random placebo (joint t≥2, both containers). DSR/SPA/RW are
    disclosure-only (pre-declared to FAIL).
    """
    out: dict[str, object] = {
        "note": (
            "DIAGNOSTIC — owner judges per amendment "
            "qgr-certification-rearch-amendment-2026-07-04. Selection gate = beats OWN "
            "random placebo (joint t>=2). DSR/SPA/RW disclosure-only (pre-declared "
            "FAIL)."
        )
    }
    d2_containers: dict[str, dict[str, object]] = {}
    a0_containers: dict[str, dict[str, object]] = {}
    for c in CONTAINERS:
        d2_arm = arms[_d2(c.label)]
        d2_containers[c.label] = _candidate_read(
            d2_arm,
            regime_tbl,
            crash_tbl,
            vs_own_random=_paired_t(
                d2_arm.period_returns, arms[_rand_d2(c.label)].period_returns
            ),
            vs_sizematched=_paired_t(
                d2_arm.period_returns, arms[_sm_d2(c.label)].period_returns
            ),
            vs_a0=_paired_t(d2_arm.period_returns, arms[_a0(c.label)].period_returns),
        )
        a0_arm = arms[_a0(c.label)]
        a0_containers[c.label] = _candidate_read(
            a0_arm,
            regime_tbl,
            crash_tbl,
            vs_own_random=_paired_t(
                a0_arm.period_returns, arms[_rand_a0(c.label)].period_returns
            ),
        )

    d2_joint = all(
        bool(d2_containers[c.label]["beats_own_random_strict"]) for c in CONTAINERS
    )
    a0_joint = all(
        bool(a0_containers[c.label]["beats_own_random_strict"]) for c in CONTAINERS
    )
    owner_gates_improved = all(
        bool(d2_containers[c.label]["net_pnl_positive"])
        and bool(d2_containers[c.label]["bear_regime_nonneg"])
        and bool(d2_containers[c.label]["all_crash_slices_nonneg"])
        for c in CONTAINERS
    )
    # Sleeve risk profile intact (branch b): deployment container keeps its defensive
    # character (bear cumulative ≥ 0) even if the ranking layer shows no placebo edge.
    sleeve_profile_intact = bool(d2_containers["buf40_5"]["bear_regime_nonneg"])
    out["d2"] = d2_containers
    out["a0"] = a0_containers
    out["d2_beats_own_placebo_joint"] = d2_joint
    out["a0_beats_own_placebo_joint"] = a0_joint
    out["owner_gates_improved"] = owner_gates_improved
    out["branch_read"] = {
        "a": d2_joint and owner_gates_improved,
        "b": (not d2_joint) and sleeve_profile_intact,
        "c": not a0_joint,
    }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_d2.csv"
    )
    parser.add_argument(
        "--crowding-panel",
        default="data/factor_research/panel_train_val_crowding.csv",
        help="A0-exact neut base (frontier's input); dv/roe/gpm spliced from --panel",
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--frontier", default="data/factor_research/slot_frontier_result.json"
    )
    parser.add_argument(
        "--out", default="data/factor_research/defensive_d2_result.json"
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
        crowding_panel_path=args.crowding_panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        ledger_path=args.ledger,
        frontier_path=args.frontier,
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
    "apply_d2_universe_filter",
    "run_ablation",
]
