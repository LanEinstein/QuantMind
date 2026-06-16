"""PHASE 4 — one-shot locked-test evaluation of the frozen strategy.

THE single sanctioned read of the sacred test window (2025-06-04 .. 2026-06-12).
It builds the test panel (features ``<= d``, labels ``> d``, rebalancing only on
test dates — see :func:`build_factor_panel.build_test_panel`), loads the weights
**frozen in Phase 3** (committed *before* any test access — the data-snooping
firewall), backtests them net-of-cost vs CSI300 over the test window, and judges
PASS/FAIL against the owner-locked bar:

    net cumulative return > 0  AND  cumulative excess vs CSI300 >= 0
    AND  max drawdown <= 15%   AND  per-period-annualised Sharpe >= 0.5

Run ONCE. Touching test during development, or re-running to tune, voids the
locked test set (test-set covenant — handoff §5). The verdict is reported
honestly: if it FAILS, it FAILS — no口径 change to clear the bar.

Limitation (documented, same as the search backtest): this is a portfolio-sort
net-of-cost backtest, NOT the full ``backend.backtest`` event-loop engine — it
does not model T+1 same-day settlement or limit-up/down at-fill rejection
(entries are next-period; the panel already excludes by board/liquidity/price).
A full-engine cross-check is the recommended confirmation step (Phase 5).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.marketdata_snapshot.store import SnapshotStore

from .build_factor_panel import build_test_panel
from .locked_split import LockedSplit
from .portfolio_backtest import BacktestResult, backtest, load_benchmark
from .weight_search import EXPECTED_FACTOR_ORDER

HORIZON: int = 5  # matches the frozen strategy's design (weekly rebalance)
TOP_N: int = 5  # production book ≤ 5 slots
# Owner-locked PASS bar (handoff §1) — all four must hold.
MAX_DRAWDOWN_BAR: float = 0.15
MIN_SHARPE_BAR: float = 0.5
# The strategy frozen in Phase 3 (commit 189af2e / handoff e1a04a4), recorded
# here to 3 dp as the auditable pre-commitment; the run asserts the on-disk
# result artifact agrees (catches a drifted/regenerated JSON).
FROZEN_WEIGHTS_3DP: dict[str, float] = {
    "ret_5d": 0.089,
    "ret_20d": 0.018,
    "vol_20d": 0.163,
    "max_20d": 0.163,
    "ep_ttm": 0.211,
    "turn_20d": 0.173,
    "amihud_20d": 0.183,
}


@dataclass(frozen=True)
class Phase4Verdict:
    """The one-shot locked-test outcome + the four-criterion PASS/FAIL."""

    weights: dict[str, float]
    n_periods: int
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    bench_total_return: float
    excess_vs_bench: float
    win_rate: float
    avg_turnover: float
    per_year: dict[str, dict[str, float]]
    criteria: dict[str, bool]
    passed: bool


def load_frozen_weights(path: str) -> dict[str, float]:
    """Load the Phase-3 frozen weights, asserting they match the committed record.

    Fail-closed on factor-order drift or any weight differing from the
    git-committed 3 dp pre-commitment — so the locked test can only ever score
    the strategy that was frozen before test was touched.
    """
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if tuple(artifact["factor_names"]) != EXPECTED_FACTOR_ORDER:
        raise ValueError(
            f"artifact factor order {artifact['factor_names']} != pinned "
            f"{EXPECTED_FACTOR_ORDER} — refusing (fail closed)."
        )
    weights = {k: float(v) for k, v in artifact["selected_weights"].items()}
    for factor, frozen in FROZEN_WEIGHTS_3DP.items():
        if abs(weights[factor] - frozen) > 5e-4:
            raise ValueError(
                f"weight {factor}={weights[factor]:.6f} != frozen {frozen} — "
                "result artifact drifted from the pre-commitment (fail closed)."
            )
    return weights


def _compound(rets: list[float]) -> float:
    """Cumulative net return of a per-period series."""
    equity = 1.0
    for r in rets:
        equity *= 1.0 + r
    return equity - 1.0


def _per_year(res: BacktestResult) -> dict[str, dict[str, float]]:
    """Per-calendar-year rebalance count + compounded net return."""
    buckets: dict[str, list[float]] = {}
    for date, ret in zip(res.dates, res.net_returns, strict=True):
        buckets.setdefault(date[:4], []).append(ret)
    return {
        year: {"n_periods": float(len(rs)), "total_return": _compound(rs)}
        for year, rs in sorted(buckets.items())
    }


def evaluate(res: BacktestResult, weights: dict[str, float]) -> Phase4Verdict:
    """Apply the four owner-locked PASS criteria to the test backtest."""
    criteria = {
        "net_positive": res.total_return > 0.0,
        "beats_csi300": res.excess_vs_bench >= 0.0,
        "drawdown_within_15pct": res.max_drawdown <= MAX_DRAWDOWN_BAR,
        "sharpe_at_least_0.5": res.sharpe >= MIN_SHARPE_BAR,
    }
    return Phase4Verdict(
        weights=weights,
        n_periods=res.n_periods,
        total_return=res.total_return,
        annual_return=res.annual_return,
        sharpe=res.sharpe,
        max_drawdown=res.max_drawdown,
        bench_total_return=res.bench_total_return,
        excess_vs_bench=res.excess_vs_bench,
        win_rate=res.win_rate,
        avg_turnover=res.avg_turnover,
        per_year=_per_year(res),
        criteria=criteria,
        passed=all(criteria.values()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--weights", default="data/factor_research/weight_search_result.json"
    )
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--panel-out", default="data/factor_research/panel_test.csv")
    parser.add_argument("--out", default="data/factor_research/phase4_result.json")
    args = parser.parse_args()

    weights = load_frozen_weights(args.weights)  # before any test read
    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    # === THE one-shot sacred-test read ===
    panel = build_test_panel(split, store, rebalance_freq=HORIZON)
    panel_out = Path(args.panel_out)
    panel_out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(panel_out, index=False)

    bench = load_benchmark(args.benchmark)
    res = backtest(panel, weights, benchmark=bench, horizon=HORIZON, top_n=TOP_N)
    verdict = evaluate(res, weights)

    out = Path(args.out)
    out.write_text(json.dumps(asdict(verdict), indent=2), encoding="utf-8")

    print("=" * 64)
    print("PHASE 4 — LOCKED-TEST ONE-SHOT (2025-06-04 .. 2026-06-12)")
    print("=" * 64)
    print(
        f"rebalances={verdict.n_periods}  net={verdict.total_return:+.2%}  "
        f"annual={verdict.annual_return:+.2%}  sharpe={verdict.sharpe:+.2f}"
    )
    print(
        f"mdd={verdict.max_drawdown:.2%}  CSI300={verdict.bench_total_return:+.2%}  "
        f"excess={verdict.excess_vs_bench:+.2%}  win={verdict.win_rate:.2%}  "
        f"turnover={verdict.avg_turnover:.2f}"
    )
    for year, stats in verdict.per_year.items():
        print(
            f"  {year}: periods={int(stats['n_periods'])} "
            f"net={stats['total_return']:+.2%}"
        )
    print("-" * 64)
    for name, ok in verdict.criteria.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("-" * 64)
    print(f"VERDICT: {'PASS' if verdict.passed else 'FAIL'}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
