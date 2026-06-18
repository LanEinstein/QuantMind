"""Round-2 benchmark-relative diagnostics (R2-3 / T4) — DEV evidence, not verdict.

Thin reporting orchestrator: runs the deployable benchmark-relative primary arm
(T2) and the research-only market-neutral reference arm (T3) over the real
train_val round-2 panel, and writes an honest Markdown diagnostic — realised
excess / TE / IR + exposure disclosure (net active ≈ 0, gross/forced active,
size-active, max industry-active) + the long-short alpha upper bound.

The primary arm is run over a SMALL ILLUSTRATIVE (k, a_max) grid purely to show
how TE / IR scale with tilt aggressiveness — NOT a selection (no winner is
chosen or carried forward; the deflated CPCV search that picks the single
strategy is R2-4). This is development evidence; the PASS/FAIL verdict is the
R2-6 forward test only. Deterministic, offline, train_val only.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

import pandas as pd

from .benchmark_relative import (
    CARRY_FACTORS,
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .benchmark_weights import BenchmarkWeightsPIT, index_weight_keys
from .locked_split import LockedSplit
from .long_short import market_neutral_backtest
from .neutralize import neutralize_panel

DEFAULT_HORIZON: int = 5
# Illustrative tilt grid (NOT a selection — all disclosed, none carried forward).
K_GRID: tuple[float, ...] = (0.05, 0.10, 0.20)
A_MAX_GRID: tuple[float, ...] = (0.01, 0.02, 0.04)
WINSOR_QUANTILE: float = 0.01


def load_benchmark_before(path: str, ceiling: str) -> dict[str, float]:
    """CSI300 ``{trade_date: close}`` for dates STRICTLY before ``ceiling``.

    Streams the CSV row-by-row and skips any date ``>= ceiling`` during the
    parse, so locked test-window closes are never materialized (codex P1 — the
    test-set covenant forbids reading the sacred window even as side data; a
    parse-then-filter would still pull every test row into memory first).
    """
    out: dict[str, float] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            day = str(row.get("trade_date", "")).strip()
            if not day or day >= ceiling:
                continue
            try:
                out[day] = float(row["close"])
            except (TypeError, ValueError, KeyError):
                continue
    return out


def build_index_returns(
    benchmark: Mapping[str, float], dates: Sequence[str], horizon: int
) -> dict[str, float]:
    """CSI300 horizon-bar return for each date in ``dates`` (on the index calendar).

    ``benchmark`` is {trade_date: close}. For date ``d`` the return is
    ``close[d+horizon]/close[d] - 1`` on the benchmark's own calendar; dates whose
    ``d`` or ``d+horizon`` bar is missing are omitted (the backtest skips them).
    """
    bench_dates = sorted(benchmark)
    pos = {dt: i for i, dt in enumerate(bench_dates)}
    out: dict[str, float] = {}
    for d in dates:
        i = pos.get(d)
        if i is None or i + horizon >= len(bench_dates):
            continue
        b0 = benchmark[bench_dates[i]]
        b1 = benchmark[bench_dates[i + horizon]]
        if b0 > 0 and b1 > 0:
            out[d] = b1 / b0 - 1.0
    return out


def _equal_carry_weights() -> dict[str, float]:
    return {f: 1.0 for f in CARRY_FACTORS}


def _primary_grid(
    panel: pd.DataFrame,
    bench_pit: BenchmarkWeightsPIT,
    index_returns: Mapping[str, float],
    weights: Mapping[str, float],
    horizon: int,
) -> tuple[list[str], list[BenchmarkRelativeResult]]:
    """Run the (k, a_max) grid; return its Markdown table lines + the results."""
    lines = [
        "| k | a_max | periods | total excess | annual excess | TE | IR | "
        "turnover | gross active | forced UW | net active | size active | "
        "max ind active |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    results: list[BenchmarkRelativeResult] = []
    for k in K_GRID:
        for a_max in A_MAX_GRID:
            r = benchmark_relative_backtest(
                panel,
                bench_pit.asof,
                index_returns,
                weights=weights,
                horizon=horizon,
                k=k,
                a_max=a_max,
            )
            results.append(r)
            lines.append(
                f"| {k:.2f} | {a_max:.2f} | {r.n_periods} | "
                f"{r.total_excess:+.2%} | {r.annual_excess:+.2%} | "
                f"{r.tracking_error:.2%} | {r.information_ratio:+.2f} | "
                f"{r.avg_turnover:.2f} | {r.avg_gross_active:.2%} | "
                f"{r.avg_forced_underweight:.2%} | {r.mean_net_active:+.2e} | "
                f"{r.mean_size_active:+.3f} | {r.mean_max_industry_active:.2%} |"
            )
    return lines, results


def build_report(
    panel: pd.DataFrame,
    bench_pit: BenchmarkWeightsPIT,
    index_returns: Mapping[str, float],
    *,
    horizon: int,
) -> str:
    """Assemble the Markdown diagnostic (primary grid + reference arm).

    Neutralizes the panel first (adds the carry factors' ``*_neut`` columns the
    composite consumes) — the saved round-2 panel carries only raw factors +
    industry/size, so the tilt would be inert without this step.
    """
    weights = _equal_carry_weights()
    panel = neutralize_panel(
        panel, list(CARRY_FACTORS), winsor_quantile=WINSOR_QUANTILE
    )
    dates = sorted(panel["date"].astype(str).unique())
    covered = sum(1 for d in dates if bench_pit.asof(d))

    lines = [
        "# Round-2 benchmark-relative diagnostics (R2-3, train_val only)\n",
        f"> Panel: {len(panel)} rows / {panel['ts_code'].nunique()} codes / "
        f"{len(dates)} rebalance dates. Benchmark-weighted dates (publish<d, "
        f"≥2016): {covered}/{len(dates)} (pre-2016 have no CSI300 weights → "
        "skipped).\n"
        "> Composite = EQUAL-weight over the carry set's industry/size-"
        "neutralized columns (round-1 seven + roe/gpm/np_yoy/rev_yoy). This is "
        "DEVELOPMENT evidence over an ILLUSTRATIVE tilt grid — NOT a selection; "
        "the deflated CPCV search is R2-4, the verdict is R2-6 forward.\n",
        "## 1. Primary arm — benchmark-relative long-only (deployable)\n",
    ]
    grid_lines, results = _primary_grid(
        panel, bench_pit, index_returns, weights, horizon
    )
    lines += grid_lines

    # Run the reference arm on the SAME dates as the primary arm (benchmark
    # weights available) so the alpha upper bound is apples-to-apples (codex P2).
    ref_index_returns = {d: r for d, r in index_returns.items() if bench_pit.asof(d)}
    ref = market_neutral_backtest(
        panel, ref_index_returns, weights=weights, horizon=horizon, top_quantile=0.2
    )
    lines += [
        "\n## 2. Reference arm — market-neutral (RESEARCH ONLY, not deployable)\n",
        "> long top-20% composite − short CSI300. **Never a PASS claim, never "
        "deployed, never enters the verdict** (A-share retail cannot short; "
        "永禁真实下单). Bounds the factors' alpha upside only.\n",
        f"- periods: {ref.n_periods}",
        f"- total alpha: {ref.total_alpha:+.2%} / annual {ref.annual_alpha:+.2%}",
        f"- alpha Sharpe: {ref.alpha_sharpe:+.2f}",
        f"- max drawdown: {ref.max_drawdown:.2%}",
        f"- avg turnover: {ref.avg_turnover:.2f}",
        "\n## 3. Honest read\n",
        _honest_read(results),
    ]
    return "\n".join(lines) + "\n"


def _honest_read(results: list) -> str:  # type: ignore[type-arg]
    """Format the honest-read bullets with figures derived from the grid runs.

    Computed from the actual results (not hardcoded) so a non-default CLI run
    cannot produce a self-contradictory narrative (codex P2).
    """
    size = [r.mean_size_active for r in results]
    gross = [r.avg_gross_active for r in results]
    irs = [r.information_ratio for r in results]
    net = max(abs(r.mean_net_active) for r in results)
    forced = results[0].avg_forced_underweight if results else 0.0
    size_lo, size_hi = min(size), max(size)
    size_drift = max(abs(size_lo), abs(size_hi)) > 0.3
    return "\n".join(
        [
            f"- **Beta neutral, size {'NOT' if size_drift else 'roughly'} "
            f"neutral.** Net active ≈ 0 (max |net| {net:.0e}; beta ≈ 1 by the "
            f"renormalize-to-Σw=1 design), but `size active` runs "
            f"{size_lo:+.2f}…{size_hi:+.2f} std and `gross active` "
            f"{min(gross):.0%}…{max(gross):.0%}. The tilt spans the full "
            "investable universe but starts from 300 CSI300 weights, so "
            "high-composite NON-constituents (mostly small/mid caps, "
            "w_bench=0) get positive active → a systematic size drift even "
            "though the FACTORS are size-neutralized. The disclosed excess / "
            f"IR ({min(irs):+.2f}…{max(irs):+.2f}) is therefore CONTAMINATED "
            "by a size bet, not a clean factor tilt — exactly the hidden bet "
            "this disclosure exists to catch.\n"
            "- **R2-4 must constrain off-benchmark exposure**: restrict the "
            "active tilt to CSI300 constituents (true enhanced-index), and/or "
            "add a portfolio-level size-neutrality constraint, and/or cap "
            "non-constituent active + target a TE band. Size-neutralized "
            "factors alone do NOT prevent a universe-mismatch size drift.\n"
            "- **TE/IR scaling**: IR is supplementary disclosure (NOT a "
            "replacement for the four owner-locked gates); higher a_max raises "
            "TE/turnover — R2-4 searches (k, a_max, weights, exposure "
            "constraints) under DSR/PBO/SPA deflation.\n"
            f"- **Forced underweight ({forced:.1%})**: CSI300 constituents "
            "excluded by the investable universe (round-1 exclusions — STAR "
            "科创板 / 北交 BSE boards, ST, liquidity/price, bottom-30% size; "
            "创业板 ChiNext is whitelisted, NOT excluded) are forced to 0 — a "
            "passive active the "
            "index leg penalises. Its size/industry attributes are NOT in the "
            "panel, so the `size active` / `max ind active` columns cover the "
            "investable sleeve ONLY and understate this residual (see the R2-2 "
            "66% industry-coverage gap).\n"
            "- **Reference arm caveat**: the market-neutral +alpha is similarly "
            "inflated by the small-cap-vs-large-cap-index mismatch; it bounds "
            "upside only and is RESEARCH-ONLY (never deployed, never a "
            "verdict).\n"
            "- **This is development evidence, not PASS/FAIL.** The verdict is "
            "the one-shot R2-6 forward test on data postdating the freeze.\n"
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_r2.csv"
    )
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument(
        "--out",
        default="docs/research/"
        "factor-strategy-round2-r2-3-benchmark-relative-diagnostics-2026-06-18.md",
    )
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"date": str, "code": str, "ts_code": str})
    from backend.marketdata_snapshot.store import SnapshotStore

    store = SnapshotStore(args.snapshot_root)
    # Research firewall: never read the sacred test window — not the panel, and
    # not the benchmark SIDE inputs (codex P1/P2). Restrict the index_weight
    # snapshot keys AND the CSI300 closes to strictly before test_start before
    # building/using them, so no test-window weight payload or index bar is ever
    # consumed during an R2-3 development diagnostic.
    split = LockedSplit.load(args.lock, args.snapshot_root)
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))
    test_start = split.test_dates[0]
    pre_test_keys = tuple(
        k for k in index_weight_keys(args.snapshot_root) if k < test_start
    )
    bench_pit = BenchmarkWeightsPIT.build(store, pre_test_keys)
    benchmark = load_benchmark_before(args.benchmark, test_start)
    dates = sorted(panel["date"].astype(str).unique())
    index_returns = build_index_returns(benchmark, dates, args.horizon)

    report = build_report(panel, bench_pit, index_returns, horizon=args.horizon)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()
