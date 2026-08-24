"""Confirmatory science-gate for SLV-2 — the quality (gross-profitability) sleeve.

Preregistration: ``docs/research/defensive-sleeve2-preregistration-2026-08-23.md``
(committed BEFORE this implementation; criteria/seed/operators frozen there; the
audit-driven pre-run addendum §11 covers the three fixes below).

Mirrors the SLV-1 science gate machinery (same panel, same LockedSplit firewall,
same frozen ``run_gate_backtest`` event loop, same regime/crash/DSR/SPA disclosure
stack) with these deltas:

1. selection = RAW ``gpm`` top-5 eq AFTER mechanically excluding SLV-1's committed
   TARGET book (``dv_ratio`` top-5) from each date's gated universe. Target books
   are disjoint by construction (asserted); the EXECUTED books can still overlap
   when the event loop retains a name past its target window — that executed
   overlap is measured and DISCLOSED, never claimed away (codex P1).
2. the random placebo is a HARD gate (paired-t ≥ 2.0) and runs the SAME
   weakness-gate machinery as the sleeve arm over a full per-date randomized score
   table — per-date random top-5 WITH rotation semantics, not a stale initial book
   (codex P1; the size-matched disclosure arm gets the same treatment).
3. rebalance-date coverage is validated against the ORIGINAL panel's dates: only a
   LEADING run of zero-candidate dates (pure gpm coverage absence, early 2015) may
   be skipped, capped and disclosed; any later gap or thin date aborts (codex P1).

Six arms: sleeve2_buf40_5 (judged) / sleeve2_eq_5 (naive baseline, no-buffer MDD) /
placebo_random_buf40_5 (HARD gate) / placebo_sizematched_buf40_5 (disclosure) /
sleeve1_buf40_5_disclosure / csi300_hold (beta hurdle).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

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
from .baselines import _hash_score, buy_and_hold_baseline
from .crowding_factor_diagnostics import _overlap_lag
from .defensive_d1_ablation import (
    DefensiveArm,
    _arm_from_gate,
    _paired_t,
    _strategy_config,
    size_matched_scores,
)
from .defensive_sleeve2_spec import (
    CONTAINER,
    HORIZON,
    LEDGER_DATE,
    LEDGER_FAMILY,
    LEDGER_ROUND,
    PLACEBO_SEED,
    REBALANCE_FREQ,
    SCIENCE_GATE,
    SELECTION_FACTOR,
    SELECTION_TOP_N,
    SLV1_EXCLUSION,
    spec_hash,
)
from .defensive_sleeve_science_gate import build_sleeve_ranker_table
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
DSR_GATE: float = 0.95  # disclosure-only

# Prereg §11.1: only a LEADING run of zero-candidate rebalance dates (gpm PIT
# coverage absence in early 2015) may be excluded — capped, enumerated, disclosed.
MAX_LEADING_COVERAGE_SKIP: int = 6

_RANKER_TABLE_COLS: tuple[str, ...] = (
    "date",
    "ts_code",
    "ranker_score",
    "ranker_pct",
    "log_circ_mv",
)
_BUF = f"sleeve2_{CONTAINER.label}"
_EQ = "sleeve2_eq_5"
_RAND = f"placebo_random_{CONTAINER.label}"
_SM = f"placebo_sizematched_{CONTAINER.label}"
_S1 = f"sleeve1_{CONTAINER.label}_disclosure"


class _FillLike(Protocol):
    trade_date: str
    code: str
    side_is_buy: bool
    volume: int


def slv1_books_by_date(panel: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Per rebalance date: SLV-1's committed TARGET book (prereg §2 exclusion rule).

    Within the gated universe, keep finite ``dv_ratio`` rows, sort by
    (``dv_ratio`` desc, ``ts_code`` asc), take :data:`SLV1_EXCLUSION.top_n`.
    """
    out: dict[str, tuple[str, ...]] = {}
    for date, grp in panel.groupby("date", sort=True):
        surv = d1r.apply_exclusion_gates(grp).dropna(subset=[SLV1_EXCLUSION.factor])
        surv = surv[np.isfinite(surv[SLV1_EXCLUSION.factor])]
        book = surv.sort_values(
            [SLV1_EXCLUSION.factor, "ts_code"], ascending=[False, True]
        )["ts_code"].astype(str)
        out[str(date)] = tuple(book.head(SLV1_EXCLUSION.top_n))
    return out


def build_sleeve2_ranker_table(panel: pd.DataFrame) -> pd.DataFrame:
    """``(date, ts_code, ranker_score, ranker_pct, log_circ_mv)`` — SLV-2 selection.

    Per date: D1 exclusion gates (reused verbatim) → drop SLV-1's committed TARGET
    book → drop non-finite ``gpm`` → rank by RAW ``gpm`` (higher = better). Ties
    resolved downstream by the selector's deterministic (score desc, ts_code asc)
    ordering — same as SLV-1.
    """
    need = ["date", "ts_code", "log_circ_mv", SELECTION_FACTOR, SLV1_EXCLUSION.factor]
    missing = [c for c in need if c not in panel.columns]
    if missing:
        raise KeyError(f"panel missing columns: {missing}")
    books = slv1_books_by_date(panel)
    rows: list[pd.DataFrame] = []
    for date, grp in panel.groupby("date", sort=True):
        surv = (
            d1r.apply_exclusion_gates(grp)
            .dropna(subset=[SELECTION_FACTOR, "log_circ_mv"])
            .copy()
        )
        surv = surv[~surv["ts_code"].astype(str).isin(set(books[str(date)]))]
        surv["ranker_score"] = surv[SELECTION_FACTOR].to_numpy()
        surv = surv[np.isfinite(surv["ranker_score"])].copy()
        if surv.empty:
            continue
        surv["ranker_pct"] = surv["ranker_score"].rank(pct=True, method="average")
        rows.append(surv[list(_RANKER_TABLE_COLS)])
    if not rows:
        return pd.DataFrame(columns=list(_RANKER_TABLE_COLS))
    return pd.concat(rows, ignore_index=True)


def validate_rebalance_coverage(
    panel_dates: Sequence[str],
    ranker_table: pd.DataFrame,
    *,
    min_breadth: int,
) -> list[str]:
    """Fail-closed coverage check vs the ORIGINAL panel's rebalance dates.

    Returns the leading zero-candidate dates that may be skipped (prereg §11.1).
    A leading date with 1..min_breadth-1 candidates is NOT skippable (that is
    thinness, not coverage absence); any zero/thin date AFTER evaluation starts
    aborts; more than :data:`MAX_LEADING_COVERAGE_SKIP` leading skips aborts.
    """
    counts = ranker_table.groupby("date").size()
    leading: list[str] = []
    started = False
    for date in panel_dates:
        n = int(counts.get(date, 0))
        if not started:
            if n == 0:
                leading.append(str(date))
                continue
            if n < min_breadth:
                raise ValueError(
                    f"first evaluable date {date} has {n} < {min_breadth} "
                    "post-exclusion candidates — thinness, not coverage absence; "
                    "fail-closed"
                )
            started = True
        elif n < min_breadth:
            raise ValueError(
                f"rebalance date {date} has {n} < {min_breadth} post-exclusion "
                "candidates AFTER evaluation started — fail-closed (no silent gaps)"
            )
    if not started:
        raise ValueError("no evaluable rebalance dates — fail-closed")
    if len(leading) > MAX_LEADING_COVERAGE_SKIP:
        raise ValueError(
            f"{len(leading)} leading zero-candidate dates > cap "
            f"{MAX_LEADING_COVERAGE_SKIP} — fail-closed"
        )
    return leading


def randomized_score_table(ranker_table: pd.DataFrame, *, seed: int) -> pd.DataFrame:
    """The random-placebo FULL score table (prereg §11.2).

    Same rows (same post-exclusion candidate pool), scores replaced by the
    deterministic per-(seed, date, code) hash score. Feeding this through the
    SAME ``scores_by_day`` + ``build_health_overrides`` machinery gives the
    placebo per-date random top-5 WITH the sleeve's own rotation semantics —
    never a stale initial book (codex P1).
    """
    t = ranker_table.copy()
    t["ranker_score"] = [
        _hash_score(seed, str(d), str(c))
        for d, c in zip(t["date"], t["ts_code"], strict=True)
    ]
    t["ranker_pct"] = t.groupby("date")["ranker_score"].rank(
        pct=True, method="average"
    )
    return t


def sizematched_score_table(ranker_table: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    """The size-matched FULL score table (disclosure arm; prereg §11.2).

    Matched names score 1.0, everything else 0.0 (flat-tie percentiles are a
    documented approximation for this DISCLOSURE-only arm), so incumbents that
    drop out of the matched set become weak and rotate instead of being held
    forever by a missing health entry.
    """
    sm = size_matched_scores(ranker_table, top_n=top_n)
    selected = {(d, code) for d, pairs in sm.items() for code, _ in pairs}
    t = ranker_table.copy()
    t["ranker_score"] = [
        1.0 if (str(d), str(c)) in selected else 0.0
        for d, c in zip(t["date"], t["ts_code"], strict=True)
    ]
    t["ranker_pct"] = t.groupby("date")["ranker_score"].rank(
        pct=True, method="average"
    )
    return t


def selected_books_by_date(ranker_table: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Per date: the top-N TARGET book the selector takes (score desc, code asc)."""
    out: dict[str, tuple[str, ...]] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        g = grp.sort_values(["ranker_score", "ts_code"], ascending=[False, True])
        out[str(date)] = tuple(g["ts_code"].astype(str).head(SELECTION_TOP_N))
    return out


def _overlap_report(
    slv1_books: dict[str, tuple[str, ...]],
    slv2_books: dict[str, tuple[str, ...]],
) -> dict[str, object]:
    """TARGET-book overlap; disjointness by construction is asserted (fail-closed)."""
    overlaps = {
        d: sorted(set(slv1_books.get(d, ())) & set(slv2_books[d])) for d in slv2_books
    }
    bad = {d: o for d, o in overlaps.items() if o}
    if bad:
        raise AssertionError(
            f"SLV-1/SLV-2 TARGET books overlap on {len(bad)} date(s) (e.g. "
            f"{dict(list(bad.items())[:3])}) — exclusion rule broken, fail-closed"
        )
    return {"dates_checked": len(overlaps), "target_book_overlap_total": 0}


def held_by_day(
    fills: Iterable[_FillLike], daily_days: Sequence[str]
) -> dict[str, set[str]]:
    """Per trading day: the EXECUTED holding set reconstructed from applied fills."""
    by_day: dict[str, list[_FillLike]] = {}
    for fill in fills:
        by_day.setdefault(str(fill.trade_date), []).append(fill)
    vol: dict[str, int] = {}
    out: dict[str, set[str]] = {}
    for day in daily_days:
        for fill in by_day.get(day, ()):
            delta = fill.volume if fill.side_is_buy else -fill.volume
            vol[fill.code] = vol.get(fill.code, 0) + delta
        out[day] = {code for code, v in vol.items() if v > 0}
    return out


def executed_overlap_disclosure(
    result_a: GateBacktestResult,
    result_b: GateBacktestResult,
    daily_days: Sequence[str],
) -> dict[str, object]:
    """EXECUTED-holdings overlap between two arms (disclosure, never asserted).

    The event loop can retain a name past its target window (health-gated sells),
    so executed books may overlap even though target books are disjoint (codex P1
    — the disjointness claim is scoped to target books; this measures the rest).
    """
    ha = held_by_day(result_a.backtest_result.fills, daily_days)
    hb = held_by_day(result_b.backtest_result.fills, daily_days)
    per_day = [len(ha[d] & hb[d]) for d in daily_days]
    names: set[str] = set()
    for d in daily_days:
        names |= ha[d] & hb[d]
    overlap_days = sum(1 for n in per_day if n > 0)
    return {
        "days_checked": len(daily_days),
        "days_with_overlap": overlap_days,
        "overlap_day_share": (overlap_days / len(daily_days)) if daily_days else 0.0,
        "max_concurrent_overlap": max(per_day, default=0),
        "distinct_overlapping_names": sorted(names),
    }


def _sector_concentration(
    panel: pd.DataFrame, books: dict[str, tuple[str, ...]]
) -> dict[str, object]:
    """Disclosure: how concentrated the selected books are by ``industry_l1``."""
    if "industry_l1" not in panel.columns:
        return {"available": False}
    ind = panel.set_index(["date", "ts_code"])["industry_l1"]
    counts: dict[str, int] = {}
    total = 0
    for date, codes in books.items():
        for code in codes:
            sector = str(ind.get((date, code), "unknown"))
            counts[sector] = counts.get(sector, 0) + 1
            total += 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    return {
        "available": True,
        "slot_total": total,
        "top_sectors": dict(top),
        "top1_share": (top[0][1] / total) if total else 0.0,
    }


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


def run_science_gate(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """SLV-2 confirmatory science gate → a JSON-able result dict."""
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

    log("[2/6] gates + SLV-1 book exclusion + gpm top-5 selection")
    slv1_books = slv1_books_by_date(panel)
    ranker2 = build_sleeve2_ranker_table(panel)
    min_breadth = 2 * SELECTION_TOP_N
    leading_skipped = validate_rebalance_coverage(
        panel_dates, ranker2, min_breadth=min_breadth
    )
    if leading_skipped:
        log(
            f"      leading zero-candidate dates skipped (gpm coverage, "
            f"disclosed): {leading_skipped}"
        )
    # The sleeve1 DISCLOSURE arm is aligned to sleeve2's evaluated dates so period
    # arrays match one-to-one (correlation / paired stats need equal length).
    sleeve2_dates = set(ranker2["date"].astype(str).unique())
    sleeve1_table = build_sleeve_ranker_table(panel)
    sleeve1_table = sleeve1_table[
        sleeve1_table["date"].astype(str).isin(sleeve2_dates)
    ].copy()
    if smoke_periods is not None:
        keep = set(sorted(sleeve2_dates)[:smoke_periods])
        ranker2 = ranker2[ranker2["date"].astype(str).isin(keep)].copy()
        sleeve1_table = sleeve1_table[
            sleeve1_table["date"].astype(str).isin(keep)
        ].copy()
    if ranker2.empty:
        raise ValueError("sleeve2 ranker table is empty — fail-closed")
    slv2_books = selected_books_by_date(ranker2)
    overlap = _overlap_report(slv1_books, slv2_books)
    sectors = _sector_concentration(panel, slv2_books)
    log(f"      overlap={overlap} top_sectors={sectors.get('top_sectors')}")

    rebs = sorted(ranker2["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    allowed = set(split.train_val_dates) | set(split.embargo_dates)
    daily_days = _resolve_window(rebs, calendar, allowed=allowed)
    split.assert_all_not_test(daily_days)  # firewall the ACTUAL bar-read window
    universe = tuple(
        sorted(
            {*xv.panel_universe(ranker2), *xv.panel_universe(sleeve1_table), CSI300_ETF}
        )
    )
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/6] build PIT bar source (heavy) + scores + placebos (same machinery)")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    rand_table = randomized_score_table(ranker2, seed=PLACEBO_SEED)
    sm_table = sizematched_score_table(ranker2, top_n=SELECTION_TOP_N)
    tables = {
        _BUF: ranker2,
        _EQ: ranker2,
        _RAND: rand_table,
        _SM: sm_table,
        _S1: sleeve1_table,
    }

    log("[4/6] run arms (sleeve2 buf/eq + rotating placebos + sleeve1 disclosure)")
    arms: dict[str, DefensiveArm] = {}
    raw: dict[str, GateBacktestResult] = {}
    for label, slots, cap in (
        (_BUF, CONTAINER.slots, CONTAINER.cap_percent),
        (_EQ, 5, 100),
        (_RAND, CONTAINER.slots, CONTAINER.cap_percent),
        (_SM, CONTAINER.slots, CONTAINER.cap_percent),
        (_S1, CONTAINER.slots, CONTAINER.cap_percent),
    ):
        table = tables[label]
        gate_res = _run_arm(
            scores=xv.scores_by_day(table),
            health=xv.build_health_overrides(table),
            slots=slots,
            cap_percent=cap,
            bar_source=bar_source,
        )
        raw[label] = gate_res
        arms[label] = _arm_from_gate(label, slots, cap, gate_res)
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
    executed_overlap = executed_overlap_disclosure(raw[_BUF], raw[_S1], daily_days)
    all_labels = (_BUF, _EQ, _RAND, _SM, _S1, "csi300_hold")
    for label in all_labels:
        a = arms[label]
        log(
            f"      {label:32s} slots={a.slots:2d} cap={a.cap_percent:3d} "
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
        family=LEDGER_FAMILY,
        round_label=LEDGER_ROUND,
        description="SLV-2 quality sleeve confirmatory science gate",
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
        family=LEDGER_FAMILY,
    )
    regimes = _classify_regimes(bench)
    n_periods = len(arms[_BUF].period_returns)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]
    ordered_arms = [arms[label] for label in all_labels]
    regime_tbl = _regime_table(ordered_arms, regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(ordered_arms, period_dates)  # type: ignore[arg-type]

    log("[6/6] assemble result + preregistered four-criteria read")
    return {
        "spec_hash": spec_hash(),
        "preregistration": (
            "docs/research/defensive-sleeve2-preregistration-2026-08-23.md"
        ),
        "window": {
            "start": window[0],
            "end": window[1],
            "rebalance_dates": len(rebs),
            "panel_rebalance_dates": len(panel_dates),
            "leading_skipped_dates": leading_skipped,
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
        "target_book_overlap": overlap,
        "executed_holdings_overlap": executed_overlap,
        "sector_concentration": sectors,
        "read": _read(arms, regime_tbl, crash_tbl),
    }


def _read(
    arms: dict[str, DefensiveArm],
    regime_tbl: dict[str, dict[str, dict[str, float]]],
    crash_tbl: dict[str, dict[str, dict[str, float]]],
) -> dict[str, object]:
    """The preregistered FOUR-criteria read (prereg §5; all four must hold)."""
    buf = arms[_BUF]
    bear_bucket = regime_tbl[_BUF]["bear"]
    bear_n = int(bear_bucket["n"])
    bear_cum = float(bear_bucket["sum_return"])
    crash_buckets = crash_tbl[_BUF]
    tested = {name: v["cum_return"] for name, v in crash_buckets.items() if v["n"] > 0}
    mdd = buf.max_drawdown_pct
    vs_random_t = _paired_t(buf.period_returns, arms[_RAND].period_returns)[1]
    s1_corr = float(
        np.corrcoef(
            np.asarray(buf.period_returns), np.asarray(arms[_S1].period_returns)
        )[0, 1]
    )
    c1 = buf.net_pnl_yuan > 0.0
    c2 = bear_n > 0 and bear_cum >= 0.0
    c3 = mdd <= SCIENCE_GATE.mdd_hard_bound
    c4 = vs_random_t >= SCIENCE_GATE.placebo_t_min
    return {
        "note": (
            "PREREGISTERED four-criteria gate (prereg §5): net>0, bear>=0, "
            f"MDD<= {SCIENCE_GATE.mdd_hard_bound}, paired-t vs random >= "
            f"{SCIENCE_GATE.placebo_t_min}. ALL four required. DSR/SPA/RW disclosed."
        ),
        "deployable_arm": _BUF,
        "net_pnl_yuan": buf.net_pnl_yuan,
        "criterion_1_net_pnl_positive": c1,
        "bear_regime_n": bear_n,
        "bear_regime_cumulative": bear_cum,
        "criterion_2_bear_cum_nonneg": c2,
        "max_drawdown_pct": mdd,
        "mdd_hard_bound": SCIENCE_GATE.mdd_hard_bound,
        "criterion_3_mdd_within_bound": c3,
        "vs_random_t": vs_random_t,
        "placebo_t_min": SCIENCE_GATE.placebo_t_min,
        "criterion_4_beats_random": c4,
        "science_gate_pass": bool(c1 and c2 and c3 and c4),
        "avg_exposure": buf.avg_exposure,
        "dsr": buf.dsr,  # disclosure-only
        "crash_slice_cumulative": {
            k: v["cum_return"] for k, v in crash_buckets.items()
        },
        "crash_slices_tested": sorted(tested),
        "vs_sizematched_t": _paired_t(buf.period_returns, arms[_SM].period_returns)[1],
        "sleeve1_period_return_correlation": s1_corr,  # disclosure (both long A-share)
        "sleeve1_disclosure_net_pnl": arms[_S1].net_pnl_yuan,
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
        "--out", default="data/factor_research/defensive_sleeve2_result.json"
    )
    parser.add_argument(
        "--smoke-periods",
        type=int,
        default=None,
        help="restrict to the first N rebalance dates (engineering smoke; no ledger)",
    )
    args = parser.parse_args()

    result = run_science_gate(
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
    "build_sleeve2_ranker_table",
    "executed_overlap_disclosure",
    "held_by_day",
    "randomized_score_table",
    "run_science_gate",
    "selected_books_by_date",
    "sizematched_score_table",
    "slv1_books_by_date",
    "validate_rebalance_coverage",
]
