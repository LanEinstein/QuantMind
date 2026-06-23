"""§3.8B bottom-confirmation gate diagnostics — CONDITIONAL forward-return test.

The bottom-confirmation gate is an OVERLAY, not a rankable additive factor (main
doc §3.8B), so it is NOT validated by a rank-IC ranking axis. It is validated by
its forward-return DISCRIMINATION: per rebalance date, does the confirmed group
out-earn the not-confirmed group? The per-date spread ``mean(fwd | confirmed) −
mean(fwd | not-confirmed)`` is aggregated to a mean / t-stat / hit-rate — the
"conditional IC" of the gate. Reported on the slow-leg horizons (5/10/20d, the
weeks-to-months holding the §3.8B basing thesis targets).

Honest framing (carried verbatim into the report):
* **the t-stat is an OPTIMISTIC SCREEN, not a verdict** — overlapping forward
  windows autocorrelate the per-date spread (effective N < n_dates); the honest
  control is the QGR-2 arena's DSR/SPA/Romano-Wolf with cumulative-N deflation
  (QGR-4). QGR-3 runs NO search and makes NO promotion.
* **cyq_perf is MODEL-derived (§3.5)** → the cost-band condition is kept OUT of
  the clean-PIT core and ABLATED here (core vs full on the cyq-available subset):
  if the full gate does not beat the core gate, the cost band is not load-bearing.
* **③ 资金流企稳 is DELIBERATELY DEFERRED** (moneyflow not ingested in QGR-1 +
  §3.6 trap) — disclosed, not silently dropped.
* **gate ≠ ranking axis** — the discrimination measures whether the gate is a
  useful FILTER on the slow-leg dip candidates, not a score to rank on.

Deterministic, offline, train_val only (the sealed test window is never read).
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .bottom_confirmation import BOTTOM_CONFIRM_CONDITIONS, CORE_CONDITION_NAMES
from .factor_ic_study import rank_ic_series, summarize_ic
from .locked_split import LockedSplit

# Slow-leg horizons (the basing thesis holds weeks-to-months); 1d omitted (fast leg).
SLOW_FWD_COLS: tuple[str, ...] = ("fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d")
MIN_GROUP: int = 5  # need >= this many names per side per date to take a spread
DIP_POOL_QUANTILE: float = 1.0 / 3.0  # bottom tercile of ret_20d = "recently fallen"
DIP_POOL_FACTOR: str = "ret_20d"
# The per-condition flag columns (5) + the two composites.
_CONDITION_COLS: tuple[str, ...] = tuple(
    f"bc_{c.name}" for c in BOTTOM_CONFIRM_CONDITIONS
)
_CORE_COL = "bc_core_confirmed"
_FULL_COL = "bc_full_confirmed"


@dataclass(frozen=True)
class SpreadSummary:
    """Aggregated confirmed-vs-not forward-return spread (the gate's conditional IC)."""

    label: str
    horizon: str
    mean_spread: float
    t_stat: float
    hit_rate: float  # fraction of dates whose spread shares the mean's sign
    n_dates: int


def group_spread_series(
    panel: pd.DataFrame,
    flag_col: str,
    fwd_col: str,
    *,
    mask: pd.Series | None = None,
    min_group: int = MIN_GROUP,
) -> list[float]:
    """Per-date ``mean(fwd | flag==1) − mean(fwd | flag==0)`` (None flags dropped).

    A date contributes only when BOTH sides have ≥ ``min_group`` names (else the
    spread is too thin to be a stable estimate)."""
    sub = panel if mask is None else panel[mask]
    spreads: list[float] = []
    for _, group in sub.groupby("date", sort=True):
        pair = group[[flag_col, fwd_col]].dropna()
        conf = pair.loc[pair[flag_col] == 1.0, fwd_col]
        notc = pair.loc[pair[flag_col] == 0.0, fwd_col]
        if len(conf) < min_group or len(notc) < min_group:
            continue
        spreads.append(float(conf.mean() - notc.mean()))
    return spreads


def spread_stats(
    spreads: list[float], *, label: str = "", horizon: str = ""
) -> SpreadSummary:
    """Aggregate a per-date spread series → mean / t-stat / hit-rate (fail-closed)."""
    n = len(spreads)
    if n == 0:
        return SpreadSummary(label, horizon, 0.0, 0.0, 0.0, 0)
    arr = np.asarray(spreads, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    # Degenerate variance (a near-constant spread series) → t undefined; fail
    # closed to 0 rather than letting float noise (~1e-18 std) explode the t-stat.
    t_stat = mean / (std / math.sqrt(n)) if std > 1e-12 else 0.0
    hit = float(np.mean(np.sign(arr) == np.sign(mean))) if mean != 0 else 0.0
    return SpreadSummary(label, horizon, mean, t_stat, hit, n)


def dip_pool_mask(
    panel: pd.DataFrame,
    *,
    factor: str = DIP_POOL_FACTOR,
    quantile: float = DIP_POOL_QUANTILE,
) -> pd.Series:
    """Per-date boolean mask of the ``factor`` bottom-quantile (the dip candidates).

    The §3.8B claim is specifically about NAMES THAT HAVE PULLED BACK — does the
    gate separate healthy basers from falling knives among them? Bottom-tercile
    ``ret_20d`` is that "recently fallen" pool. NaN ``factor`` → excluded."""
    mask = pd.Series(False, index=panel.index)
    for _, idx in panel.groupby("date", sort=True).groups.items():
        vals = panel.loc[idx, factor]
        cut = vals.quantile(quantile)
        if pd.isna(cut):  # the whole date's factor is NaN → no dip pool
            continue
        mask.loc[idx] = vals <= cut
    return mask


def _spread_table(summaries: list[SpreadSummary]) -> str:
    lines = [
        "| gate / condition | horizon | mean spread | t | hit | n_dates |",
        "|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.label} | {s.horizon} | {s.mean_spread:+.4f} | {s.t_stat:+.2f} | "
            f"{s.hit_rate:.2f} | {s.n_dates} |"
        )
    return "\n".join(lines)


def _coverage_section(panel: pd.DataFrame) -> str:
    lines = [
        "| column | defined-rate (of rows) | confirmed-rate (of evaluable) |",
        "|---|---|---|",
    ]
    for col in (*_CONDITION_COLS, _CORE_COL, _FULL_COL):
        if col not in panel.columns:
            continue
        defined = float(panel[col].notna().mean())
        ev = panel[col].dropna()
        conf = float((ev == 1.0).mean()) if len(ev) else float("nan")
        lines.append(f"| {col} | {defined:.2%} | {conf:.2%} |")
    return "\n".join(lines)


def _gate_discrimination(
    panel: pd.DataFrame, flag_col: str, label: str, *, mask: pd.Series | None = None
) -> list[SpreadSummary]:
    out: list[SpreadSummary] = []
    for fwd in SLOW_FWD_COLS:
        if fwd not in panel.columns:
            continue
        spreads = group_spread_series(panel, flag_col, fwd, mask=mask)
        out.append(spread_stats(spreads, label=label, horizon=fwd))
    return out


def _component_section(panel: pd.DataFrame) -> str:
    """Per-condition marginal discrimination at the 5d horizon."""
    out: list[SpreadSummary] = []
    for col in _CONDITION_COLS:
        if col not in panel.columns:
            continue
        spreads = group_spread_series(panel, col, "fwd_ret_5d")
        out.append(spread_stats(spreads, label=col, horizon="fwd_ret_5d"))
    return _spread_table(out)


def _ablation_section(panel: pd.DataFrame) -> str:
    """Core-vs-full on the cyq-AVAILABLE subset → is the cost band load-bearing?"""
    if "bc_above_cost_band" not in panel.columns:
        return "_(cyq_perf column absent — ablation skipped.)_"
    cyq_mask = panel["bc_above_cost_band"].notna()
    n_rows = int(cyq_mask.sum())
    core = _gate_discrimination(panel, _CORE_COL, "core (no cyq)", mask=cyq_mask)
    full = _gate_discrimination(panel, _FULL_COL, "full (+cyq band)", mask=cyq_mask)
    table = _spread_table([*core, *full])
    return (
        f"> Both gates measured on the SAME cyq-available subset "
        f"(**{n_rows}** rows, cyq_perf 2018+). If `full` does not beat `core`, the "
        "MODEL-derived cyq_perf cost band (§3.5) is NOT load-bearing.\n\n" + table
    )


def _continuous_section(panel: pd.DataFrame) -> str:
    """SECONDARY: rank-IC of the continuous cyq reads (cost premium / winner rate).

    Reported for completeness only — these are continuous reads of the
    model-derived cyq_perf, NOT a sanctioned ranking axis (the gate is a filter)."""
    rows: list[str] = [
        "| continuous read | horizon | rank-IC | t | n_dates |",
        "|---|---|---|---|---|",
    ]
    for col in ("bc_cost_premium", "bc_winner_rate"):
        if col not in panel.columns:
            continue
        for fwd in SLOW_FWD_COLS:
            if fwd not in panel.columns:
                continue
            s = summarize_ic(col, fwd, rank_ic_series(panel, col, fwd))
            rows.append(
                f"| {col} | {fwd} | {s.ic_mean:+.4f} | {s.t_stat:+.2f} | {s.n_dates} |"
            )
    return "\n".join(rows)


def build_report(panel: pd.DataFrame, *, params_note: str = "") -> str:
    """Assemble the bottom-confirmation gate diagnostic Markdown (deterministic)."""
    dates = panel["date"].nunique()
    codes = panel["code"].nunique()
    note = f"\n> Panel params: {params_note}.\n" if params_note else "\n"
    dip = dip_pool_mask(panel)

    core_full = _gate_discrimination(panel, _CORE_COL, "core gate (4 clean-PIT)")
    full_full = _gate_discrimination(panel, _FULL_COL, "full gate (+cyq band)")
    core_dip = _gate_discrimination(
        panel, _CORE_COL, "core gate | dip pool", mask=dip
    )

    parts = [
        "# QGR-3 ⑧ bottom-confirmation gate diagnostics (§3.8B, train_val only)\n",
        f"> Panel: {len(panel)} rows / {codes} codes / {dates} rebalance dates "
        "(train_val; the sealed test window is never read)."
        + note
        + "> **The gate is an OVERLAY, not a ranking axis** — validated by its "
        "forward-return DISCRIMINATION (confirmed vs not-confirmed), not a rank-IC. "
        "Conditions (§3.8B): ① 缩量 + ④ 无破位 + ⑤ 无困境(PIT-ST) + ⑥ 质量地板 = "
        "clean-PIT **core**; ② 站稳筹码成本带 (cyq_perf, MODEL-derived §3.5) kept "
        "SEPARATE + ablated; **③ 资金流企稳 DEFERRED** (moneyflow not ingested + "
        "§3.6 trap). Horizons = slow leg 5/10/20d.\n"
        "> **The t-stat is an OPTIMISTIC SCREEN, not the verdict** (overlapping "
        "windows → autocorrelated spread, effective N < n_dates) — the honest "
        "control is the QGR-2 arena DSR/SPA/Romano-Wolf + cumulative-N deflation "
        "(QGR-4). QGR-3 runs NO search, makes NO promotion.\n",
        "## 1. Coverage (evaluable-rate + confirmed-rate)\n",
        _coverage_section(panel),
        "\n## 2. Core gate discrimination (4 clean-PIT conditions, full window)\n",
        _spread_table(core_full),
        "\n## 3. Full gate discrimination (core + cyq band, 2018+ where evaluable)\n",
        _spread_table(full_full),
        "\n## 4. Conditional on the dip pool (ret_20d bottom tercile = 买跌票)\n"
        "> The precise §3.8B claim: among RECENTLY-FALLEN names, does confirmation "
        "separate healthy basers from falling knives?\n\n",
        _spread_table(core_dip),
        "\n## 5. Per-condition marginal discrimination (5d)\n",
        _component_section(panel),
        "\n## 6. cyq_perf ablation (is the model-derived cost band load-bearing?)\n",
        _ablation_section(panel),
        "\n## 7. Continuous cyq reads — SECONDARY (rank-IC, not a ranking axis)\n",
        _continuous_section(panel),
        "\n## 8. Honest read (development evidence ≠ verdict)\n"
        f"- **Core = the 4 clean-PIT conditions** "
        f"(`{', '.join(CORE_CONDITION_NAMES)}`); "
        "the cyq_perf band is ablatable, NOT core (§3.5 model-derived).\n"
        "- **A positive, significant spread means the gate is a useful FILTER** on "
        "the slow-leg dip candidates — it does NOT pre-judge a strategy (rounds "
        "1-4 had strong train_val signal yet the locked test FAILed three times). "
        "The verdict is the QGR-2 arena + QGR-4 search + QGR-6 forward window.\n"
        "- **③ 资金流企稳 deferred, not dropped silently**: moneyflow/"
        "moneyflow_hsgt/margin were never ingested (QGR-1) and §3.6 flags daily "
        "moneyflow as a trap; the stabilisation is carried by ①缩量 + ④无破位.\n"
        "- **cyq_perf caveat (§3.5)**: model-derived, 2018+ only, degenerate-band "
        "rows fail closed; treated as an ablatable overlay, never a clean axis.\n",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_qgr.csv"
    )
    parser.add_argument(
        "--out",
        default="docs/research/qgr-3-bottom-confirmation-diagnostics-2026-06-22.md",
    )
    parser.add_argument(
        "--params-note",
        default="rebalance=5td / §3.8B gate: core(缩量+无破位+无ST+质量) + cyq band",
    )
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"date": str, "code": str, "ts_code": str})
    split = LockedSplit.load(args.lock, args.snapshot_root)
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))

    report = build_report(panel, params_note=args.params_note)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "DIP_POOL_QUANTILE",
    "MIN_GROUP",
    "SLOW_FWD_COLS",
    "SpreadSummary",
    "build_report",
    "dip_pool_mask",
    "group_spread_series",
    "spread_stats",
]
