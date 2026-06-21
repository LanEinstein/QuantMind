"""R5 — forward-window virgin OOS of the frozen round-4 strategy (the binding test).

The round-4 locked test PASSed (provisional), but the dev anti-overfit gates did
not confirm it (DSR 0.007, sentinel failed) and it was the test set's 4th
evaluation. The ONLY clean resolution is a forward window whose data postdates the
locked test (``> test_end`` 2026-06-12) — a genuinely-virgin OOS the strategy was
frozen (``ffc1db3``) long before. This runner scores the SAME git-frozen
``FROZEN_R4_*`` strategy (via :func:`round4_locked_test.load_frozen_strategy`, the
same data-snooping firewall) over the forward window.

The forward window ACCRUES over calendar time. The 5-day-horizon strategy needs
at least ``HORIZON`` forward bars after a rebalance to score even one period, and
a meaningful four-gate read needs many periods (the locked test had 49). So this
runner is honest about insufficiency:

* fewer than ``HORIZON`` forward bars after the first rebalance → no complete
  forward-return label yet → status ACCRUING (0 scoreable periods), NEVER a
  verdict on noise;
* 0 < scoreable periods < :data:`MIN_FORWARD_PERIODS` → status ACCRUING
  (preliminary numbers shown but explicitly NOT a verdict);
* scoreable periods ≥ :data:`MIN_FORWARD_PERIODS` → the four owner-locked gates
  are evaluated (still disclosed as a forward read of N periods, not the final
  word until the window is long).

Re-run as data accrues. Deterministic; the strategy is unchanged.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from backend.marketdata_snapshot.store import SnapshotStore

from .benchmark_relative import (
    R4_CARRY_FACTORS,
    benchmark_relative_backtest,
)
from .benchmark_weights import BenchmarkWeightsPIT, index_weight_keys
from .build_factor_panel import (
    build_forward_panel_r4,
    build_r4_inputs,
    forward_trade_dates,
)
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .r2_benchmark_relative_diagnostics import build_index_returns
from .round4_locked_test import (
    HORIZON,
    WINSOR_QUANTILE,
    R4Verdict,
    evaluate,
    load_frozen_strategy,
)

# A four-gate forward read needs enough rebalances to be more than noise. Below
# this the window is reported ACCRUING (the locked test had 49 periods; even this
# floor is a tentative read, not the final confirmation that wants ~20-40).
MIN_FORWARD_PERIODS: int = 8


@dataclass(frozen=True)
class ForwardStatus:
    """The forward-window status: ACCRUING (insufficient) or a forward VERDICT."""

    status: str  # "ACCRUING" or "VERDICT"
    forward_td: int
    forward_start: str
    forward_end: str
    scoreable_periods: int
    min_periods_for_verdict: int
    note: str
    verdict: dict[str, object] | None  # R4Verdict asdict when status == VERDICT


def _accruing(
    forward_dates: list[str], scoreable: int, note: str
) -> ForwardStatus:
    return ForwardStatus(
        status="ACCRUING",
        forward_td=len(forward_dates),
        forward_start=forward_dates[0] if forward_dates else "-",
        forward_end=forward_dates[-1] if forward_dates else "-",
        scoreable_periods=scoreable,
        min_periods_for_verdict=MIN_FORWARD_PERIODS,
        note=note,
        verdict=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--search-result", default="data/factor_research/round4_search_result.json"
    )
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument(
        "--panel-out", default="data/factor_research/panel_forward_r4.csv"
    )
    parser.add_argument(
        "--out", default="data/factor_research/round4_forward_status.json"
    )
    args = parser.parse_args()

    # Same firewall as the locked test: the scored strategy is the git-frozen one.
    constraint, k, a_max, nonconst_cap, weights = load_frozen_strategy(
        args.search_result
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    test_end = split.test_dates[-1]

    forward_dates = forward_trade_dates(args.snapshot_root, test_end)
    if len(forward_dates) <= HORIZON:
        status = _accruing(
            forward_dates,
            0,
            f"only {len(forward_dates)} forward trading day(s) since test_end "
            f"{test_end}; need > {HORIZON} for one complete {HORIZON}d-forward "
            "label. Window accruing — re-run as data lands.",
        )
        _finish(status, out)
        return

    forward_end = forward_dates[-1]
    inputs = build_r4_inputs(
        store,
        args.snapshot_root,
        last_period_date=forward_end,
        report_rc_last_date=forward_end,
        test_start=split.test_dates[0],
        sanctioned_test_read=True,  # the forward window is the sanctioned virgin OOS
    )
    panel = build_forward_panel_r4(
        store=store,
        split=split,
        inputs=inputs,
        forward_dates=forward_dates,
        rebalance_freq=HORIZON,
    )
    fwd_col = f"fwd_ret_{HORIZON}d"
    scoreable = panel.dropna(subset=[fwd_col]) if len(panel) else panel
    if len(scoreable) == 0:
        status = _accruing(
            forward_dates,
            0,
            f"{len(forward_dates)} forward td but no rebalance has a complete "
            f"{HORIZON}d-forward label yet (the first forward rebalance needs "
            f"{HORIZON} bars after it). Window accruing.",
        )
        _finish(status, out)
        return

    panel.to_csv(args.panel_out, index=False)
    panel = neutralize_panel(
        panel, list(R4_CARRY_FACTORS), winsor_quantile=WINSOR_QUANTILE
    )
    bench_pit = BenchmarkWeightsPIT.build(store, index_weight_keys(args.snapshot_root))
    benchmark = _load_benchmark_all(args.benchmark)
    dates = sorted(panel["date"].astype(str).unique())
    index_returns = build_index_returns(benchmark, dates, HORIZON)

    # Fail-closed: the benchmark must cover every scoreable forward rebalance date
    # (its HORIZON-forward CSI300 bar must exist), else build_index_returns
    # silently drops it and the backtest UNDERCOUNTS periods. csi300_daily.csv is
    # NOT auto-refreshed with forward closes (it ends at test_end), so a stale
    # benchmark would otherwise mask the forward window or emit a verdict over
    # fewer/older periods than the panel actually scored. Refuse instead.
    scoreable_dates = sorted(scoreable["date"].astype(str).unique())
    uncovered = [d for d in scoreable_dates if d not in index_returns]
    if uncovered:
        latest = max(benchmark) if benchmark else "-"
        raise SystemExit(
            f"benchmark {args.benchmark} (latest {latest}) does not cover "
            f"{len(uncovered)} scoreable forward rebalance date(s) "
            f"(e.g. {uncovered[0]}). Refresh csi300_daily.csv with forward "
            "index_daily(000300.SH) closes through the forward window + HORIZON "
            "before the forward verdict (fail-closed: refusing to undercount "
            "periods on a stale benchmark)."
        )

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
    if res.n_periods < MIN_FORWARD_PERIODS:
        status = _accruing(
            forward_dates,
            res.n_periods,
            f"{res.n_periods} scoreable forward period(s) < {MIN_FORWARD_PERIODS} "
            "minimum — preliminary, NOT a verdict. Window accruing.",
        )
        _finish(status, out)
        return

    verdict: R4Verdict = evaluate(
        res,
        index_returns,
        constraint=constraint,
        k=k,
        a_max=a_max,
        nonconst_cap=nonconst_cap,
        weights=weights,
    )
    status = ForwardStatus(
        status="VERDICT",
        forward_td=len(forward_dates),
        forward_start=forward_dates[0],
        forward_end=forward_end,
        scoreable_periods=res.n_periods,
        min_periods_for_verdict=MIN_FORWARD_PERIODS,
        note=(
            f"forward read over {res.n_periods} periods — the virgin OOS the frozen "
            "round-4 strategy was committed before. Four gates evaluated; still a "
            "forward read, strengthening as the window lengthens."
        ),
        verdict=asdict(verdict),
    )
    _finish(status, out)


def _load_benchmark_all(path: str) -> dict[str, float]:
    """CSI300 ``{trade_date: close}`` for ALL dates (forward read is sanctioned)."""
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


def _finish(status: ForwardStatus, out: Path) -> None:
    out.write_text(json.dumps(asdict(status), indent=2), encoding="utf-8")
    print("=" * 64)
    print("R5 — FORWARD-WINDOW VIRGIN OOS (postdate test_end 2026-06-12)")
    print("=" * 64)
    print(
        f"forward_td={status.forward_td} "
        f"({status.forward_start}..{status.forward_end}) "
        f"scoreable_periods={status.scoreable_periods}"
    )
    print(f"STATUS: {status.status}")
    if status.verdict is not None:
        v = status.verdict
        print(
            f"  net={v['net_total_return']:+.2%} excess={v['excess_vs_bench']:+.2%} "
            f"sharpe={v['sharpe']:+.2f} mdd={v['max_drawdown']:.2%}"
        )
        criteria = cast("dict[str, bool]", v["criteria"])
        for name, ok in criteria.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"  PASSED={v['passed']}")
    print(f"NOTE: {status.note}")
    print(f"-> {out}")


__all__ = [
    "MIN_FORWARD_PERIODS",
    "ForwardStatus",
    "main",
]


if __name__ == "__main__":
    main()
