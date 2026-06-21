"""R4-6 — one-shot locked-test verdict of the frozen round-4 strategy.

THE single sanctioned read of the sacred test window (2025-06-04 .. 2026-06-12)
for round-4 — the FOURTH evaluation of this locked test set (round-1 / round-2 /
round-3 / round-4). Owner extended the round-2 test-reuse decision through
round-4 (``factor-strategy-round2-test-reuse-decision-2026-06-19.md``): the
verdict runs on the EXISTING locked test set rather than a forward window, with
the four honesty safeguards kept — strategy git-frozen BEFORE any test read,
cumulative-N deflation already done in R4-5 (N=2348 = 512+612+612+612 across all
four rounds), the "4th evaluation" disclosure in the report, and the four
owner-locked gates NOT relaxed.

It builds the round-4 test panel (the sole sanctioned reader
:func:`build_factor_panel.build_test_panel_r4`), loads the strategy git-frozen in
R4-5 (asserting it matches the committed pre-commitment — the data-snooping
firewall), runs the benchmark-relative backtest ONCE over the test window, and
judges PASS/FAIL against:

    net cumulative return > 0  AND  cumulative excess vs CSI300 >= 0
    AND  max drawdown <= 15%   AND  per-period-annualised Sharpe >= 0.5

Run ONCE. The four gates are reported honestly: if it FAILS, it FAILS — no口径
change to clear the bar. IR / TE are SUPPLEMENTARY disclosure, never a gate.
The only difference from R3-6 is the carry set (round-3 twelve + the four R4-4
analyst-revision survivors) and the panel builder (round-4 report_rc PIT,
sanctioned to read the test-era analyst months for this one-shot).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from backend.marketdata_snapshot.store import SnapshotStore

from .benchmark_relative import (
    R4_CARRY_FACTORS,
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .benchmark_weights import BenchmarkWeightsPIT, index_weight_keys
from .build_factor_panel import build_r4_inputs, build_test_panel_r4
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .r2_benchmark_relative_diagnostics import build_index_returns

HORIZON: int = 5
WINSOR_QUANTILE: float = 0.01
# Owner-locked PASS bar (handoff §1) — all four must hold. NOT relaxed for R4.
MAX_DRAWDOWN_BAR: float = 0.15
MIN_SHARPE_BAR: float = 0.5
_PERIODS_PER_YEAR_BASE: int = 252

# === The strategy git-frozen in R4-5 (filled from the R4-5 search result BEFORE
# the test is read). Recorded here to 3 dp as the auditable pre-commitment; the
# run asserts the on-disk search-result artifact agrees (catches a drifted JSON).
# R4-5 selected: constituent_only (true enhanced index, same construction that
# fixed the R2-3 size drift), k=0.20, a_max=0.04 — the STRONGEST tilt in the grid
# (round-2/3 picked weak k=0.10/0.05); the composite leans on the analyst block
# (rev_diff 0.216 dominant + cover_chg/tp_impl/np_rev ≈ 0.35 total). DEV
# disclosure (in-sample): val IR 0.71 / excess +4.55% / CPCV all-positive
# (IR_mean 1.45) / SPA-vs-passive p=0.056 (the closest-to-significant of all four
# rounds), BUT DSR 0.007 fails >=0.95 (deflated by N=2348) + PBO 0.329 + the
# shuffled-composite SENTINEL FAILS (noise val IR 1.34 > selected 0.71) — the
# honesty gates again warn "fragile / not cleanly separated from noise". The
# verdict is THIS one-shot.
FROZEN_R4_CONSTRAINT: str = "constituent_only"
FROZEN_R4_K: float = 0.20
FROZEN_R4_A_MAX: float = 0.04
FROZEN_R4_NONCONST_CAP: float = 0.10
FROZEN_R4_WEIGHTS_3DP: dict[str, float] = {
    "ret_5d": 0.161,
    "ret_20d": 0.053,
    "vol_20d": 0.013,
    "max_20d": 0.007,
    "ep_ttm": 0.012,
    "turn_20d": 0.005,
    "amihud_20d": 0.114,
    "roe": 0.018,
    "gpm": 0.077,
    "np_yoy": 0.082,
    "rev_yoy": 0.036,
    "accr": 0.074,
    "np_rev": 0.035,
    "rev_diff": 0.216,
    "tp_impl": 0.037,
    "cover_chg": 0.060,
}


@dataclass(frozen=True)
class R4Verdict:
    """The one-shot locked-test outcome + the four-criterion PASS/FAIL."""

    constraint: str
    k: float
    a_max: float
    nonconst_cap: float
    weights: dict[str, float]
    n_periods: int
    net_total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    bench_total_return: float
    excess_vs_bench: float
    information_ratio: float
    tracking_error: float
    avg_turnover: float
    mean_size_active: float
    avg_gross_active: float
    avg_forced_underweight: float
    mean_net_active: float
    per_year: dict[str, dict[str, float]]
    criteria: dict[str, bool]
    passed: bool


def load_frozen_strategy(
    path: str,
) -> tuple[str, float, float, float, dict[str, float]]:
    """Load the R4-5 search result, asserting it matches the committed freeze.

    Returns the COMMITTED ``FROZEN_R4_*`` values (not the artifact's), so the
    scored strategy is provably the git-frozen one — the gitignored artifact is
    only cross-checked for agreement (fail-closed on any drift), never executed.
    A factor frozen at ``0.000`` therefore stays exactly zero even if the
    full-precision artifact carries a sub-tolerance non-zero value (codex R3-5).
    """
    if (
        FROZEN_R4_CONSTRAINT == "PLACEHOLDER_FILLED_IN_R4_5"
        or not FROZEN_R4_WEIGHTS_3DP
    ):
        raise ValueError(
            "FROZEN_R4_* constants are not filled — R4-5 must git-freeze the "
            "selected strategy into this module BEFORE the locked test is read "
            "(fail closed; the firewall is only as strong as these constants)."
        )
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact["selected_constraint"] != FROZEN_R4_CONSTRAINT:
        raise ValueError(
            f"constraint {artifact['selected_constraint']} != frozen "
            f"{FROZEN_R4_CONSTRAINT} — artifact drifted (fail closed)."
        )
    for name, frozen in (
        ("selected_k", FROZEN_R4_K),
        ("selected_a_max", FROZEN_R4_A_MAX),
        ("selected_nonconst_cap", FROZEN_R4_NONCONST_CAP),
    ):
        if abs(float(artifact[name]) - frozen) > 1e-9:
            raise ValueError(
                f"{name}={artifact[name]} != frozen {frozen} (fail closed)."
            )
    weights = {k: float(v) for k, v in artifact["selected_weights"].items()}
    # Pin the exact weight SET, not just the listed factors — an artifact with an
    # extra/renamed factor must NOT slip through the firewall (review finding).
    if set(weights) != set(FROZEN_R4_WEIGHTS_3DP):
        raise ValueError(
            f"weight factor set {sorted(weights)} != frozen "
            f"{sorted(FROZEN_R4_WEIGHTS_3DP)} (fail closed)."
        )
    for factor, frozen_w in FROZEN_R4_WEIGHTS_3DP.items():
        if abs(weights[factor] - frozen_w) > 5e-4:
            raise ValueError(
                f"weight {factor}={weights[factor]:.6f} != frozen {frozen_w} "
                "— search result drifted from the pre-commitment (fail closed)."
            )
    # Score the COMMITTED constants, not the artifact's values — the verdict must
    # be tied to git, not to a mutable (gitignored) JSON within the tolerance.
    return (
        FROZEN_R4_CONSTRAINT,
        FROZEN_R4_K,
        FROZEN_R4_A_MAX,
        FROZEN_R4_NONCONST_CAP,
        dict(FROZEN_R4_WEIGHTS_3DP),
    )


def _compound(rets: list[float]) -> float:
    equity = 1.0
    for r in rets:
        equity *= 1.0 + r
    return equity - 1.0


def _max_drawdown(rets: list[float]) -> float:
    """Worst peak-to-trough decline of the net-return equity curve."""
    if not rets:
        return 0.0
    curve = np.cumprod([1.0 + r for r in rets])
    equity = np.concatenate([[1.0], curve])  # count a first-period loss in the peak
    peak = np.maximum.accumulate(equity)
    return float((1.0 - equity / peak).max())


def _portfolio_net_returns(
    res: BenchmarkRelativeResult, index_returns: dict[str, float]
) -> list[float]:
    """Per-period PORTFOLIO net return = excess + benchmark return.

    ``benchmark_relative_backtest`` reports excess = port_ret − bench_ret − cost,
    so the strategy's own net return is ``excess + bench_ret`` (every result date
    has a benchmark return by construction — the backtest skips dates without one).
    """
    return [
        e + index_returns[d] for e, d in zip(res.excess_returns, res.dates, strict=True)
    ]


def _per_year(net: list[float], dates: tuple[str, ...]) -> dict[str, dict[str, float]]:
    buckets: dict[str, list[float]] = {}
    for d, r in zip(dates, net, strict=True):
        buckets.setdefault(d[:4], []).append(r)
    return {
        year: {"n_periods": float(len(rs)), "net_return": _compound(rs)}
        for year, rs in sorted(buckets.items())
    }


def evaluate(
    res: BenchmarkRelativeResult,
    index_returns: dict[str, float],
    *,
    constraint: str,
    k: float,
    a_max: float,
    nonconst_cap: float,
    weights: dict[str, float],
) -> R4Verdict:
    """Apply the four owner-locked PASS criteria to the test backtest."""
    net = _portfolio_net_returns(res, index_returns)
    bench = [index_returns[d] for d in res.dates]
    net_total = _compound(net)
    bench_total = _compound(bench)
    excess_vs_bench = net_total - bench_total
    mdd = _max_drawdown(net)
    arr = np.asarray(net, dtype=float)
    ppy = _PERIODS_PER_YEAR_BASE / HORIZON
    std = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    sharpe = float(arr.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    annual = (
        float((1.0 + net_total) ** (ppy / len(net)) - 1.0)
        if net and net_total > -1.0
        else 0.0
    )
    criteria = {
        "net_positive": net_total > 0.0,
        "beats_csi300": excess_vs_bench >= 0.0,
        "drawdown_within_15pct": mdd <= MAX_DRAWDOWN_BAR,
        "sharpe_at_least_0.5": sharpe >= MIN_SHARPE_BAR,
    }
    return R4Verdict(
        constraint=constraint,
        k=k,
        a_max=a_max,
        nonconst_cap=nonconst_cap,
        weights=weights,
        n_periods=res.n_periods,
        net_total_return=net_total,
        annual_return=annual,
        sharpe=sharpe,
        max_drawdown=mdd,
        bench_total_return=bench_total,
        excess_vs_bench=excess_vs_bench,
        information_ratio=res.information_ratio,
        tracking_error=res.tracking_error,
        avg_turnover=res.avg_turnover,
        mean_size_active=res.mean_size_active,
        avg_gross_active=res.avg_gross_active,
        avg_forced_underweight=res.avg_forced_underweight,
        mean_net_active=res.mean_net_active,
        per_year=_per_year(net, res.dates),
        criteria=criteria,
        passed=all(criteria.values()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--search-result", default="data/factor_research/round4_search_result.json"
    )
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--panel-out", default="data/factor_research/panel_test_r4.csv")
    parser.add_argument(
        "--out", default="data/factor_research/round4_locked_test_result.json"
    )
    args = parser.parse_args()

    # Load + verify the frozen strategy BEFORE any test read (the firewall).
    constraint, k, a_max, nonconst_cap, weights = load_frozen_strategy(
        args.search_result
    )
    # Resolve + create the output dirs BEFORE consuming the sacred test window, so
    # a bad --out path fails fast rather than discarding the one-shot read.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel_out = Path(args.panel_out)
    panel_out.parent.mkdir(parents=True, exist_ok=True)
    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)

    # === THE one-shot sacred-test read ===
    test_end = split.test_dates[-1]
    # Sanctioned test read: the analyst reader loads report_rc months through
    # test_end (incl test-era months) — the strategy is git-frozen, so reading the
    # sealed window for this one verdict is the covenant, not a leak. PIT is kept
    # by the analyst reader's report_date<d gate per decision date.
    inputs = build_r4_inputs(
        store,
        args.snapshot_root,
        last_period_date=test_end,
        report_rc_last_date=test_end,
        test_start=split.test_dates[0],
        sanctioned_test_read=True,
    )
    panel = build_test_panel_r4(split, store, inputs=inputs, rebalance_freq=HORIZON)
    panel = neutralize_panel(
        panel, list(R4_CARRY_FACTORS), winsor_quantile=WINSOR_QUANTILE
    )
    panel.to_csv(panel_out, index=False)

    # Benchmark side over the TEST window (sanctioned here — the strategy is
    # frozen): ALL index_weight keys (incl. test) + CSI300 test closes.
    bench_pit = BenchmarkWeightsPIT.build(store, index_weight_keys(args.snapshot_root))
    benchmark = _load_benchmark_all(args.benchmark)
    dates = sorted(panel["date"].astype(str).unique())
    index_returns = build_index_returns(benchmark, dates, HORIZON)

    res = benchmark_relative_backtest(
        panel,
        bench_pit.asof,
        index_returns,
        weights=weights,
        horizon=HORIZON,
        k=k,
        a_max=a_max,
        exposure_constraint=constraint,
        nonconst_cap=nonconst_cap,
    )
    verdict = evaluate(
        res,
        index_returns,
        constraint=constraint,
        k=k,
        a_max=a_max,
        nonconst_cap=nonconst_cap,
        weights=weights,
    )

    out.write_text(json.dumps(asdict(verdict), indent=2), encoding="utf-8")
    _print_verdict(verdict)
    print(f"-> {out}")


def _load_benchmark_all(path: str) -> dict[str, float]:
    """CSI300 ``{trade_date: close}`` for ALL dates (test read is sanctioned here)."""
    import csv

    out: dict[str, float] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = str(row.get("trade_date", "")).strip()
            if not day:
                continue
            try:
                out[day] = float(row["close"])
            except (TypeError, ValueError, KeyError):
                continue
    return out


def _print_verdict(v: R4Verdict) -> None:
    print("=" * 64)
    print("R4-6 — LOCKED-TEST ONE-SHOT (2025-06-04 .. 2026-06-12)")
    print("=" * 64)
    print(f"strategy: {v.constraint} k={v.k} a_max={v.a_max} cap={v.nonconst_cap}")
    print(
        f"rebalances={v.n_periods} net={v.net_total_return:+.2%} "
        f"annual={v.annual_return:+.2%} sharpe={v.sharpe:+.2f}"
    )
    print(
        f"mdd={v.max_drawdown:.2%} CSI300={v.bench_total_return:+.2%} "
        f"excess={v.excess_vs_bench:+.2%} IR={v.information_ratio:+.2f} "
        f"TE={v.tracking_error:.2%} turnover={v.avg_turnover:.2f}"
    )
    print(
        f"exposure: size_active={v.mean_size_active:+.3f} "
        f"gross={v.avg_gross_active:.1%} forcedUW={v.avg_forced_underweight:.1%}"
    )
    for year, stats in v.per_year.items():
        print(
            f"  {year}: periods={int(stats['n_periods'])} "
            f"net={stats['net_return']:+.2%}"
        )
    print("-" * 64)
    for name, ok in v.criteria.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("-" * 64)
    print(f"VERDICT: {'PASS' if v.passed else 'FAIL'}")
    print(
        "DISCLOSURE: this locked test set's 4TH evaluation (round-1/2/3/4 each "
        "once); cross-strategy multiple testing exists but bounded (4, not "
        "hundreds), and the R4-5 DSR was deflated by the cumulative N=2348 across "
        "all four rounds. The four gates are NOT relaxed; round-4's alpha addition "
        "is the analyst-revision block (rev_diff/np_rev/tp_impl/cover_chg)."
    )


if __name__ == "__main__":
    main()


__all__ = [
    "MAX_DRAWDOWN_BAR",
    "MIN_SHARPE_BAR",
    "R4Verdict",
    "evaluate",
    "load_frozen_strategy",
]
