"""Round-2 factor diagnostics (R2-2 / S6) — IC + collinearity + vintage audit.

Composes the already-tested round-2 pieces into the honest train_val diagnostic
the plan requires: rank IC (raw + industry/size-neutralized) for the new
trend/quality/growth factors, their collinearity with the round-1 seven, the
fundamentals vintage audit (restatement contamination), and the PIT industry
coverage. It is a thin reporting orchestrator over ``build_panel_r2`` /
``neutralize_panel`` / ``factor_ic_study`` / ``fundamentals_pit.vintage_audit``
— all deterministic, offline, train_val only (never the sealed test window).

Reads the pre-built ``panel_train_val_r2.csv`` (heavy ingest done once by
``build_factor_panel --factor-set r2``) and writes a Markdown report. The
``honest read`` flags a factor as carrying NO independent train_val signal when
its best-horizon |t| < 3 (Harvey-Liu-Zhu multiple-testing bar) — surfaced, not
hidden, so a weak family is not silently carried into R2-3.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .factor_ic_study import (
    ICSummary,
    factor_correlation,
    study,
)
from .factor_lib import FACTOR_NAMES, R2_FACTOR_NAMES
from .fundamentals_pit import FundamentalsPIT, VintageAudit, vintage_audit
from .ingest_round2_data import report_periods
from .locked_split import LockedSplit
from .neutralize import neutralize_panel

# Harvey-Liu-Zhu (2016): a self-discovered A-share factor needs |t| > ~3 to
# survive multiple-testing; t in (2,3) is treated as no robust signal.
T_BAR: float = 3.0
NEUT_SUFFIX = "_neut"


@dataclass(frozen=True)
class FactorVerdict:
    """Per-factor honest read across horizons (raw + neutralized)."""

    factor: str
    best_horizon: str
    best_ic: float
    best_t: float
    aligned: bool
    has_signal: bool  # |best_t| >= T_BAR


def _best_summary(summaries: list[ICSummary], factor: str) -> ICSummary | None:
    """The factor's horizon with the largest |t| (the most favourable read)."""
    cand = [s for s in summaries if s.factor == factor]
    if not cand:
        return None
    return max(cand, key=lambda s: abs(s.t_stat))


def verdicts(
    summaries: list[ICSummary], factors: tuple[str, ...]
) -> list[FactorVerdict]:
    """Collapse each factor's per-horizon IC into one honest verdict row."""
    out: list[FactorVerdict] = []
    for factor in factors:
        best = _best_summary(summaries, factor)
        if best is None:
            continue
        empirical = 1 if best.ic_mean > 0 else (-1 if best.ic_mean < 0 else 0)
        out.append(
            FactorVerdict(
                factor=factor,
                best_horizon=best.horizon,
                best_ic=best.ic_mean,
                best_t=best.t_stat,
                aligned=(empirical == best.expected_sign),
                has_signal=abs(best.t_stat) >= T_BAR,
            )
        )
    return out


def _ic_table(summaries: list[ICSummary]) -> str:
    lines = [
        "| factor | horizon | IC_mean | ICIR | t | hit | n | prior | aligned? |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        empirical = 1 if s.ic_mean > 0 else (-1 if s.ic_mean < 0 else 0)
        aligned = "yes" if empirical == s.expected_sign else "**NO**"
        lines.append(
            f"| {s.factor} | {s.horizon} | {s.ic_mean:+.4f} | {s.icir:+.3f} | "
            f"{s.t_stat:+.2f} | {s.hit_rate:.2f} | {s.n_dates} | "
            f"{s.expected_sign:+d} | {aligned} |"
        )
    return "\n".join(lines)


def _verdict_table(rows: list[FactorVerdict]) -> str:
    lines = [
        "| factor | best horizon | IC | t | aligned? | independent signal (|t|≥3)? |",
        "|---|---|---|---|---|---|",
    ]
    for v in rows:
        lines.append(
            f"| {v.factor} | {v.best_horizon} | {v.best_ic:+.4f} | {v.best_t:+.2f} | "
            f"{'yes' if v.aligned else '**NO**'} | "
            f"{'yes' if v.has_signal else 'no'} |"
        )
    return "\n".join(lines)


def _vintage_section(audit: VintageAudit) -> str:
    lag = (
        f"{audit.ann_lag_days_median:.0f}d"
        if audit.ann_lag_days_median is not None
        else "—"
    )
    gap = (
        f"{audit.restate_gap_days_median:.0f}d"
        if audit.restate_gap_days_median is not None
        else "—"
    )
    return (
        f"- codes with fundamentals: **{audit.n_codes}**\n"
        f"- (code, report-period) cells: **{audit.n_code_periods}**\n"
        f"- restated (≥2 distinct ann_date): **{audit.n_restated_code_periods}** "
        f"(**{audit.restatement_rate:.2%}**)\n"
        f"- median announcement lag (ann_date − end_date): **{lag}**\n"
        f"- median restatement gap (latest − first ann): **{gap}**\n"
    )


def build_report(
    panel: pd.DataFrame,
    audit: VintageAudit,
    *,
    industry_coverage: float,
    winsor_quantile: float,
    min_obs: int,
) -> str:
    """Assemble the full Markdown diagnostic report (deterministic)."""
    all_raw = (*FACTOR_NAMES, *R2_FACTOR_NAMES)
    neut_panel = neutralize_panel(
        panel, list(all_raw), min_obs=min_obs, winsor_quantile=winsor_quantile
    )
    neut_names = tuple(f"{f}{NEUT_SUFFIX}" for f in all_raw)
    ic_all = study(neut_panel, factor_names=(*all_raw, *neut_names))

    raw_summaries = [s for s in ic_all if not s.factor.endswith(NEUT_SUFFIX)]
    neut_summaries = [s for s in ic_all if s.factor.endswith(NEUT_SUFFIX)]
    r2_raw_verdicts = verdicts(raw_summaries, R2_FACTOR_NAMES)
    r2_neut_verdicts = verdicts(neut_summaries, neut_names[len(FACTOR_NAMES) :])

    corr = factor_correlation(panel, factor_names=all_raw)
    dates = panel["date"].nunique()
    codes = panel["code"].nunique()

    parts = [
        "# Round-2 factor diagnostics (R2-2, train_val only)\n",
        f"> Panel: {len(panel)} rows / {codes} codes / {dates} rebalance dates "
        "(train_val; the sealed test window is never read).\n"
        f"> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, "
        f"winsor={winsor_quantile}, min_obs={min_obs}.\n"
        f"> PIT industry coverage (rows with an SW L1): **{industry_coverage:.2%}** "
        "(long-delisted names absent from the current SW table → neutralized "
        "factor None for those, raw factor retained).\n",
        "## 1. Fundamentals vintage audit (PIT contamination)\n",
        _vintage_section(audit),
        "\n## 2. New-factor honest verdict (raw)\n",
        _verdict_table(r2_raw_verdicts),
        "\n## 3. New-factor honest verdict (industry+size neutralized)\n",
        _verdict_table(r2_neut_verdicts),
        "\n## 4. Full IC table — raw factors (round-1 + round-2)\n",
        _ic_table(raw_summaries),
        "\n## 5. Full IC table — neutralized factors\n",
        _ic_table(neut_summaries),
        "\n## 6. Collinearity (mean cross-sectional rank corr, raw)\n",
        "```\n" + corr.round(2).to_string() + "\n```",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_r2.csv"
    )
    parser.add_argument(
        "--out",
        default="docs/research/factor-strategy-round2-r2-2-factor-diagnostics-2026-06-18.md",
    )
    parser.add_argument("--winsor-quantile", type=float, default=0.01)
    parser.add_argument("--min-obs", type=int, default=20)
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"code": str, "ts_code": str})
    split = LockedSplit.load(args.lock, args.snapshot_root)
    from backend.marketdata_snapshot.store import SnapshotStore

    store = SnapshotStore(args.snapshot_root)
    periods = report_periods(2015, split.train_val_dates[-1])
    fundamentals = FundamentalsPIT.build(store, periods)
    audit = vintage_audit(fundamentals)
    industry_coverage = float(panel["industry_l1"].notna().mean())

    report = build_report(
        panel,
        audit,
        industry_coverage=industry_coverage,
        winsor_quantile=args.winsor_quantile,
        min_obs=args.min_obs,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()
