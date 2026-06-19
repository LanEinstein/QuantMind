"""Round-3 factor diagnostics (R3-3) — IC + collinearity + vintage for the new factors.

The R2-2-protocol honest validation for the round-3 alpha sources (SUE / accruals
/ asset-growth): rank IC (raw + industry/size-neutralized) on the train_val r3
panel, collinearity against the round-2 carry cluster (and the two
balance-sheet factors against each other), and the statement vintage audits
(restatement contamination). It is a thin reporting orchestrator over the
already-tested ``factor_ic_study`` / ``neutralize_panel`` / ``statement_vintage_audit``
pieces — deterministic, offline, train_val only (never the sealed test window).

Inclusion gate (same as R2-2): a new factor is CARRIED into the round-3 search
only if its best-horizon **neutralized** ``|t| >= 3`` (Harvey-Liu-Zhu
multiple-testing floor) with the literature-aligned sign AND it is not highly
collinear with the existing carry cluster. A weak factor is dropped honestly
(as R2-2 dropped momentum/trend). The chosen carry increment is reported; the
final R3_CARRY decision (round-2 eleven ∪ survivors) is made from this output.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from backend.marketdata_snapshot.store import SnapshotStore

from .benchmark_relative import CARRY_FACTORS
from .factor_ic_study import factor_correlation, study
from .factor_lib import R3_FACTOR_NAMES
from .fundamentals_pit import VintageAudit
from .ingest_round2_data import (
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_FINA,
    EP_INCOME,
    report_periods,
)
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .r2_factor_diagnostics import (
    NEUT_SUFFIX,
    T_BAR,
    FactorVerdict,
    _ic_table,
    _verdict_table,
    _vintage_section,
    verdicts,
)
from .statements_pit import PeriodStatementPIT, statement_vintage_audit

WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
# Collinearity ceiling: a new factor whose |mean cross-sectional rank corr| with
# ANY carry factor exceeds this is flagged as redundant (judgment, like R2-2's
# trend_slope↔reversal call) — not an automatic drop, but disclosed.
COLLINEARITY_CEILING: float = 0.7

# The statement endpoints + the field each contributes, for the vintage audits.
_STATEMENT_AUDIT_SPECS: tuple[tuple[str, str, str, str | None], ...] = (
    ("fina profit_dedt (SUE)", EP_FINA, "profit_dedt", None),
    ("income n_income (accruals)", EP_INCOME, "n_income", "1"),
    ("cashflow n_cashflow_act (accruals)", EP_CASHFLOW, "n_cashflow_act", "1"),
    ("balancesheet total_assets (accr/AG)", EP_BALANCESHEET, "total_assets", "1"),
)


def carry_increment(
    neut_verdicts: list[FactorVerdict],
    *,
    redundant: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """The new factors (base names) passing the FULL inclusion gate.

    neutralized ``|t| >= 3`` + literature-aligned sign AND not in ``redundant``
    (the set of factors too collinear with the carry cluster, ``|corr| >``
    :data:`COLLINEARITY_CEILING`). codex R3-3 P2: the collinearity ceiling is part
    of the documented gate, so it MUST be applied here — not merely reported — or
    a redundant factor the report itself flags could still be carried.
    """
    out: list[str] = []
    for v in neut_verdicts:
        if not v.factor.endswith(NEUT_SUFFIX):
            continue
        base = v.factor[: -len(NEUT_SUFFIX)]
        if v.has_signal and v.aligned and base not in redundant:
            out.append(base)
    return tuple(out)


def _cell(corr: pd.DataFrame, row: str, col: str) -> float:
    """Absolute value of a correlation cell (0.0 when the pair is absent)."""
    if row not in corr.index or col not in corr.columns:
        return 0.0
    return abs(float(corr.loc[row, col]))  # type: ignore[arg-type]


def redundant_factors(corr: pd.DataFrame) -> frozenset[str]:
    """New factors whose max |corr| with the carry cluster exceeds the ceiling."""
    return frozenset(
        f
        for f in R3_FACTOR_NAMES
        if _max_carry_collinearity(corr, f)[1] > COLLINEARITY_CEILING
    )


def _max_carry_collinearity(corr: pd.DataFrame, factor: str) -> tuple[str, float]:
    """``(carry_factor, |corr|)`` of the carry factor most collinear with ``factor``."""
    if factor not in corr.index:
        return ("-", 0.0)
    best_name, best_val = "-", 0.0
    for carry in CARRY_FACTORS:
        val = _cell(corr, factor, carry)
        if val > best_val:
            best_name, best_val = carry, val
    return best_name, best_val


def _collinearity_section(corr: pd.DataFrame) -> str:
    """Per-new-factor max |corr| vs the carry cluster + the two BS factors' mutual."""
    ceil = COLLINEARITY_CEILING
    lines = [
        f"| new factor | most-collinear carry | |corr| | redundant (>{ceil:.1f})? |",
        "|---|---|---|---|",
    ]
    for f in R3_FACTOR_NAMES:
        name, val = _max_carry_collinearity(corr, f)
        flag = "**YES**" if val > ceil else "no"
        lines.append(f"| {f} | {name} | {val:.2f} | {flag} |")
    mutual = _cell(corr, "accr", "asset_growth")
    axis = "one balance-sheet-quality axis" if mutual > ceil else "distinct axes"
    lines.append("")
    lines.append(f"accr ↔ asset_growth mutual |corr| = **{mutual:.2f}** ({axis}).")
    return "\n".join(lines)


def build_report(
    panel: pd.DataFrame,
    audits: list[tuple[str, VintageAudit]],
    *,
    industry_coverage: float,
) -> str:
    """Assemble the round-3 factor diagnostic Markdown (deterministic)."""
    under_study = (*CARRY_FACTORS, *R3_FACTOR_NAMES)
    neut_panel = neutralize_panel(
        panel, list(under_study), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    neut_names = tuple(f"{f}{NEUT_SUFFIX}" for f in under_study)
    ic_all = study(neut_panel, factor_names=(*under_study, *neut_names))

    raw = [s for s in ic_all if not s.factor.endswith(NEUT_SUFFIX)]
    neut = [s for s in ic_all if s.factor.endswith(NEUT_SUFFIX)]
    r3_raw_verdicts = verdicts(raw, R3_FACTOR_NAMES)
    r3_neut_names = tuple(f"{f}{NEUT_SUFFIX}" for f in R3_FACTOR_NAMES)
    r3_neut_verdicts = verdicts(neut, r3_neut_names)

    corr = factor_correlation(panel, factor_names=under_study)
    # Apply BOTH gates: IC (|t|≥3 + aligned) AND collinearity (≤ ceiling).
    redundant = redundant_factors(corr)
    carried = carry_increment(r3_neut_verdicts, redundant=redundant)
    r3_summaries = [s for s in raw if s.factor in R3_FACTOR_NAMES] + [
        s for s in neut if s.factor in r3_neut_names
    ]
    dates = panel["date"].nunique()
    codes = panel["code"].nunique()

    vintage_lines = []
    for label, audit in audits:
        vintage_lines.append(f"\n**{label}**\n")
        vintage_lines.append(_vintage_section(audit))

    parts = [
        "# Round-3 factor diagnostics (R3-3, train_val only)\n",
        f"> Panel: {len(panel)} rows / {codes} codes / {dates} rebalance dates "
        "(train_val; the sealed test window is never read).\n"
        f"> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, "
        f"winsor={WINSOR_QUANTILE}, min_obs={MIN_OBS}.\n"
        f"> PIT industry coverage (rows with an SW L1): **{industry_coverage:.2%}**.\n"
        f"> Inclusion gate: neutralized |t| ≥ {T_BAR:.0f} + aligned sign + low "
        f"collinearity (≤ {COLLINEARITY_CEILING}).\n",
        "## 1. New-factor honest verdict (raw)\n",
        _verdict_table(r3_raw_verdicts),
        "\n## 2. New-factor honest verdict (industry+size neutralized)\n",
        _verdict_table(r3_neut_verdicts),
        "\n## 3. Collinearity vs the round-2 carry cluster\n",
        _collinearity_section(corr),
        "\n## 4. IC tables — round-3 factors (raw + neutralized)\n",
        _ic_table(r3_summaries),
        "\n## 5. Statement vintage audits (PIT restatement contamination)\n",
        "".join(vintage_lines),
        "\n## 6. Carry decision\n",
        f"- **Survivors (neut |t| ≥ {T_BAR:.0f} + aligned + |corr| ≤ "
        f"{COLLINEARITY_CEILING})**: "
        f"`{', '.join(carried) if carried else '(none — all dropped)'}`.\n"
        f"- **Dropped as redundant (|corr| > {COLLINEARITY_CEILING})**: "
        f"`{', '.join(sorted(redundant)) if redundant else '(none)'}`.\n"
        f"- **R3_CARRY = round-2 eleven ∪ survivors** = "
        f"`{', '.join((*CARRY_FACTORS, *carried))}`.\n"
        "- Weak/redundant factors are dropped honestly (as R2-2 dropped "
        "momentum/trend). If the carry increment is empty, R3-4 cannot add a new "
        "alpha source and the round likely still FAILs — reported, not papered over.\n",
    ]
    return "\n".join(parts) + "\n"


def build_statement_audits(
    store: SnapshotStore, periods: list[str]
) -> list[tuple[str, VintageAudit]]:
    """Build the four statement vintage audits (fina profit_dedt + 3 statements)."""
    out: list[tuple[str, VintageAudit]] = []
    for label, endpoint, field, rt_filter in _STATEMENT_AUDIT_SPECS:
        pit = PeriodStatementPIT.build(
            store,
            periods,
            endpoint=endpoint,
            fields=[field],
            report_type_filter=rt_filter,
        )
        out.append((label, statement_vintage_audit(pit)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_r3.csv"
    )
    parser.add_argument(
        "--out",
        default="docs/research/factor-strategy-round3-r3-factor-diagnostics-2026-06-19.md",
    )
    args = parser.parse_args()

    panel = pd.read_csv(args.panel, dtype={"date": str, "code": str, "ts_code": str})
    split = LockedSplit.load(args.lock, args.snapshot_root)
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))
    from backend.marketdata_snapshot.store import SnapshotStore

    store = SnapshotStore(args.snapshot_root)
    periods = report_periods(2015, split.train_val_dates[-1])
    audits = build_statement_audits(store, periods)
    industry_coverage = float(panel["industry_l1"].notna().mean())

    report = build_report(panel, audits, industry_coverage=industry_coverage)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written: {args.out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "COLLINEARITY_CEILING",
    "build_report",
    "build_statement_audits",
    "carry_increment",
]
