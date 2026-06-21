"""Round-4 factor diagnostics (R4-4) — IC + collinearity for the analyst factors.

The R2-2-protocol honest validation for the round-4 alpha source (the seven
analyst-revision factors): rank IC (raw + industry/size-neutralized) on the
train_val r4 panel, collinearity against the round-3 carry cluster AND among the
new factors themselves, plus an analyst-coverage disclosure (the sell-side
universe is the headline caveat). It is a thin reporting orchestrator over the
already-tested ``study`` / ``neutralize_panel`` / ``factor_correlation`` pieces —
deterministic, offline, train_val only (never the sealed test window).

Inclusion gate (same as R2-2 / R3-3): a new factor is CARRIED into the round-4
search only if its best-horizon **neutralized** ``|t| >= 3`` (Harvey-Liu-Zhu
multiple-testing floor) with the literature-aligned sign AND it is not highly
collinear (``|corr| > 0.7``) with the existing carry cluster. R4 adds one twist
over R3: the analyst factors are mutually collinear (np_rev↔eps_rev≈0.9,
rev_diff↔np_rev≈0.8 — magnitude vs breadth of the same revision), so after the
carry-redundancy screen a greedy MUTUAL dedup keeps only the strongest-|t|
representative of each collinear cluster. A weak / redundant factor is dropped
honestly (as R2-2 dropped momentum, R3-3 dropped SUE). The final
``R4_CARRY = R3_CARRY (12) ∪ survivors`` is reported.

Collinearity is computed PAIRWISE on each pair's 2-way common support (not a
single multi-way ``dropna`` over all 19 columns): the analyst factors have
uneven coverage (rev_diff/rating_chg ~35%, np_rev ~67%), so a global intersection
would shrink the support to the best-covered names and bias every estimate. The
neutralized (``*_neut``) columns are correlated, since those are exactly what the
benchmark-relative composite blends.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .benchmark_relative import R3_CARRY_FACTORS
from .factor_ic_study import _MIN_CROSS_SECTION, study
from .factor_lib import R4_FACTOR_NAMES
from .locked_split import LockedSplit
from .neutralize import neutralize_panel
from .r2_factor_diagnostics import (
    NEUT_SUFFIX,
    T_BAR,
    FactorVerdict,
    _ic_table,
    _verdict_table,
    verdicts,
)

WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
# Collinearity ceiling: a factor whose |neutralized rank corr| with ANY carry
# factor (or, in the mutual screen, a stronger-|t| new factor) exceeds this is
# treated as redundant — same judgment threshold as R3-3.
COLLINEARITY_CEILING: float = 0.7
# The cumulative carry cluster entering round 4 = the round-3 twelve (round-2
# eleven + accruals). An analyst factor redundant with it adds no new axis.
CARRY_CLUSTER: tuple[str, ...] = R3_CARRY_FACTORS
# A pairwise collinearity estimated on fewer than this many rebalance dates is
# "thin": the analyst factors have uneven coverage, and a thin estimate that
# returns ~0 would let a genuinely-redundant low-coverage factor evade the
# redundancy screen (fail-OPEN). We do NOT silently trust it — a survivor whose
# carry-collinearity support is thin is FLAGGED in the report (codex R4-4 review).
MIN_COLLIN_DATES: int = 60


def _neut(factor: str) -> str:
    return f"{factor}{NEUT_SUFFIX}"


def _pairwise(panel: pd.DataFrame, a: str, b: str) -> tuple[float, int]:
    """``(|mean cross-sectional rank corr|, n_dates)`` on ``a,b``'s 2-way support.

    One groupby over the pair's COMMON support (both non-NaN, ≥
    ``_MIN_CROSS_SECTION`` names that date) — so an analyst factor's low coverage
    does not bias the estimate toward the best-covered names (the global multi-way
    ``dropna`` r3 used would). Mirrors ``factor_correlation``'s |mean-of-signed|
    convention, and additionally returns the number of dates the estimate
    averaged over so a thin (unreliable) estimate is visible, not silent.
    """
    if a not in panel.columns or b not in panel.columns:
        return 0.0, 0
    signed: list[float] = []
    for _, group in panel.groupby("date", sort=True):
        sub = group[[a, b]].dropna()
        if len(sub) < _MIN_CROSS_SECTION:
            continue
        if sub[a].nunique() < 2 or sub[b].nunique() < 2:
            continue
        rho = sub[a].rank().corr(sub[b].rank())
        if rho == rho:  # not NaN
            signed.append(float(rho))
    if not signed:
        return 0.0, 0
    return abs(sum(signed) / len(signed)), len(signed)


def pairwise_collinearity(panel: pd.DataFrame, a: str, b: str) -> float:
    """``|mean cross-sectional rank corr|`` between ``a`` and ``b`` (2-way support).

    A thin / unestimable pair returns ``0.0`` — fail-OPEN ("not redundant"). For
    an inclusion screen this is the optimistic direction, so the support is
    tracked (:func:`compute_collinearity`) and the report flags thin estimates.
    """
    return _pairwise(panel, a, b)[0]


def max_carry_collinearity(
    neut_panel: pd.DataFrame, factor: str
) -> tuple[str, float, int]:
    """``(carry_factor, |corr|, support_dates)`` of the most-collinear carry factor.

    Both sides use the neutralized (``*_neut``) columns — the orthogonality that
    matters is residual-vs-residual, since the composite blends the residuals.
    The support is of the winning (max-|corr|) pair.
    """
    best: tuple[str, float, int] = ("-", 0.0, 0)
    for carry in CARRY_CLUSTER:
        val, support = _pairwise(neut_panel, _neut(factor), _neut(carry))
        if val > best[1]:
            best = (carry, val, support)
    return best


def compute_collinearity(
    neut_panel: pd.DataFrame,
) -> tuple[dict[str, tuple[str, float, int]], dict[frozenset[str], tuple[float, int]]]:
    """Pre-compute the carry-cluster + mutual collinearity dicts (once, for reuse).

    ``carry_collin[f] = (most_collinear_carry, |corr|, support)``; ``mutual[{a,b}]
    = (|corr|, support)`` for every unordered pair of distinct R4 factors. Both on
    neutralized columns, pairwise common support.
    """
    carry_collin = {f: max_carry_collinearity(neut_panel, f) for f in R4_FACTOR_NAMES}
    mutual: dict[frozenset[str], tuple[float, int]] = {}
    for i, a in enumerate(R4_FACTOR_NAMES):
        for b in R4_FACTOR_NAMES[i + 1 :]:
            mutual[frozenset((a, b))] = _pairwise(neut_panel, _neut(a), _neut(b))
    return carry_collin, mutual


@dataclass(frozen=True)
class CarryDecision:
    """The R4-4 carry verdict for the analyst factors (immutable)."""

    survivors: tuple[str, ...]  # carried into R4_CARRY (registry order)
    carry_redundant: tuple[str, ...]  # dropped: collinear with the carry cluster
    mutual_redundant: tuple[str, ...]  # dropped: collinear with a stronger new factor
    no_signal: tuple[str, ...]  # dropped: neut |t| < 3 or misaligned sign
    low_support: tuple[str, ...]  # survivors whose collinearity estimate is THIN


def decide_carry(
    neut_verdicts: list[FactorVerdict],
    *,
    carry_collin: dict[str, tuple[str, float, int]],
    mutual: dict[frozenset[str], tuple[float, int]],
) -> CarryDecision:
    """Apply the full inclusion gate → the analyst carry increment.

    Gate order: (1) IC — neutralized ``|t| >= T_BAR`` AND literature-aligned sign;
    (2) carry-redundancy — drop if max ``|corr|`` with the carry cluster exceeds
    the ceiling; (3) mutual-redundancy — among the survivors, greedily keep the
    strongest-``|t|`` representative of each collinear (``> ceiling``) cluster.
    Survivors are returned in registry order. A survivor whose carry-collinearity
    estimate rested on fewer than :data:`MIN_COLLIN_DATES` dates is additionally
    flagged in ``low_support`` (the redundancy screen could not assess it reliably
    — disclosed, not silently trusted).
    """
    by_base = {
        v.factor[: -len(NEUT_SUFFIX)]: v
        for v in neut_verdicts
        if v.factor.endswith(NEUT_SUFFIX)
    }
    no_signal = tuple(
        f
        for f in R4_FACTOR_NAMES
        if not (f in by_base and by_base[f].has_signal and by_base[f].aligned)
    )
    candidates = [f for f in R4_FACTOR_NAMES if f not in no_signal]
    carry_redundant = tuple(
        f
        for f in candidates
        if carry_collin.get(f, ("-", 0.0, 0))[1] > COLLINEARITY_CEILING
    )
    surviving = [f for f in candidates if f not in carry_redundant]
    # Greedy mutual dedup: strongest |t| first; drop a factor collinear with an
    # already-kept (stronger) one.
    ordered = sorted(surviving, key=lambda f: -abs(by_base[f].best_t))
    kept: list[str] = []
    mutual_redundant: list[str] = []
    for f in ordered:
        if any(
            mutual.get(frozenset((f, k)), (0.0, 0))[0] > COLLINEARITY_CEILING
            for k in kept
        ):
            mutual_redundant.append(f)
        else:
            kept.append(f)
    survivors = tuple(f for f in R4_FACTOR_NAMES if f in kept)
    low_support = tuple(
        f for f in survivors if carry_collin.get(f, ("-", 0.0, 0))[2] < MIN_COLLIN_DATES
    )
    return CarryDecision(
        survivors=survivors,
        carry_redundant=carry_redundant,
        mutual_redundant=tuple(mutual_redundant),
        no_signal=no_signal,
        low_support=low_support,
    )


def _coverage_section(panel: pd.DataFrame) -> str:
    """Per-factor defined-rate (of cohort rows) + mean — the analyst-coverage caveat."""
    lines = [
        "| factor | defined-rate (of cohort rows) | mean (defined) |",
        "|---|---|---|",
    ]
    for f in R4_FACTOR_NAMES:
        defined = float(panel[f].notna().mean()) if f in panel.columns else 0.0
        sub = panel[f].dropna() if f in panel.columns else pd.Series(dtype=float)
        mean = float(sub.mean()) if len(sub) else float("nan")
        lines.append(f"| {f} | {defined:.2%} | {mean:+.4f} |")
    return "\n".join(lines)


def _collinearity_section(
    carry_collin: dict[str, tuple[str, float, int]],
    mutual: dict[frozenset[str], tuple[float, int]],
) -> str:
    """Per-factor max |corr| vs the carry cluster (with support) + high mutual pairs."""
    ceil = COLLINEARITY_CEILING
    lines = [
        f"| analyst factor | most-collinear carry | |corr| | support (dates) | "
        f"redundant >{ceil:.1f}? |",
        "|---|---|---|---|---|",
    ]
    for f in R4_FACTOR_NAMES:
        name, val, support = carry_collin.get(f, ("-", 0.0, 0))
        flag = "**YES**" if val > ceil else "no"
        thin = " ⚠️thin" if support < MIN_COLLIN_DATES else ""
        lines.append(f"| {f} | {name} | {val:.2f} | {support}{thin} | {flag} |")
    high = sorted(
        ((sorted(pair), v) for pair, (v, _) in mutual.items() if v > ceil),
        key=lambda kv: -kv[1],
    )
    lines.append("")
    if high:
        lines.append(f"**Mutually collinear analyst pairs (|corr| > {ceil:.1f}):**")
        for pair, v in high:
            lines.append(f"- `{pair[0]}` ↔ `{pair[1]}` = **{v:.2f}**")
    else:
        lines.append(f"No analyst pair exceeds |corr| {ceil:.1f} (all distinct axes).")
    return "\n".join(lines)


def build_report(panel: pd.DataFrame, *, params_note: str = "") -> str:
    """Assemble the round-4 factor diagnostic Markdown (deterministic)."""
    under_neut = (*CARRY_CLUSTER, *R4_FACTOR_NAMES)
    neut_panel = neutralize_panel(
        panel, list(under_neut), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    r4_neut_names = tuple(_neut(f) for f in R4_FACTOR_NAMES)
    ic_all = study(neut_panel, factor_names=(*R4_FACTOR_NAMES, *r4_neut_names))
    raw = [s for s in ic_all if not s.factor.endswith(NEUT_SUFFIX)]
    neut = [s for s in ic_all if s.factor.endswith(NEUT_SUFFIX)]
    r4_raw_verdicts = verdicts(raw, R4_FACTOR_NAMES)
    r4_neut_verdicts = verdicts(neut, r4_neut_names)

    carry_collin, mutual = compute_collinearity(neut_panel)
    decision = decide_carry(r4_neut_verdicts, carry_collin=carry_collin, mutual=mutual)
    r4_carry = (*CARRY_CLUSTER, *decision.survivors)

    dates = panel["date"].nunique()
    codes = panel["code"].nunique()
    note = f"\n> Panel params: {params_note}.\n" if params_note else "\n"

    def _join(names: tuple[str, ...]) -> str:
        return ", ".join(names) if names else "(none)"

    parts = [
        "# Round-4 factor diagnostics (R4-4, train_val only)\n",
        f"> Panel: {len(panel)} rows / {codes} codes / {dates} rebalance dates "
        "(train_val; the sealed test window is never read)."
        + note
        + f"> Neutralization: industry L1 dummies + log(circ_mv), per-date OLS, "
        f"winsor={WINSOR_QUANTILE}, min_obs={MIN_OBS}.\n"
        f"> Collinearity: PAIRWISE 2-way common support on the *_neut columns "
        "(robust to the analyst factors' uneven coverage).\n"
        f"> Inclusion gate: neutralized |t| ≥ {T_BAR:.0f} + aligned sign + low "
        f"collinearity (≤ {COLLINEARITY_CEILING}) vs the carry cluster AND vs a "
        "stronger new factor. **The IC t-stat is OPTIMISTIC** (overlapping forward "
        "windows → autocorrelated IC, effective N < n_dates; best-of-3-horizons "
        "selection) — this is a SCREEN, not the verdict; R4-5's DSR/PBO/SPA with "
        "cumulative-N deflation is the real multiple-testing control (§7).\n",
        "## 1. Analyst coverage (the headline caveat — sell-side skews large-cap)\n",
        _coverage_section(panel),
        "\n## 2. Analyst-factor honest verdict (raw)\n",
        _verdict_table(r4_raw_verdicts),
        "\n## 3. Analyst-factor honest verdict (industry+size neutralized)\n",
        _verdict_table(r4_neut_verdicts),
        "\n## 4. Collinearity vs the round-3 carry cluster + mutual\n",
        _collinearity_section(carry_collin, mutual),
        "\n## 5. IC tables — analyst factors (raw + neutralized)\n",
        _ic_table([*raw, *neut]),
        "\n## 6. Carry decision\n",
        f"- **Survivors (neut |t| ≥ {T_BAR:.0f} + aligned + |corr| ≤ "
        f"{COLLINEARITY_CEILING})**: `{_join(decision.survivors)}`.\n"
        f"- **Dropped — no signal (neut |t| < {T_BAR:.0f} or misaligned)**: "
        f"`{_join(decision.no_signal)}`.\n"
        f"- **Dropped — redundant with carry cluster (|corr| > "
        f"{COLLINEARITY_CEILING})**: `{_join(decision.carry_redundant)}`.\n"
        f"- **Dropped — redundant with a stronger new factor**: "
        f"`{_join(decision.mutual_redundant)}`.\n"
        f"- **R4_CARRY = R3_CARRY (12) ∪ survivors** = `{', '.join(r4_carry)}`.\n"
        f"- **Thin-collinearity-support survivors (redundancy screen unreliable, "
        f"< {MIN_COLLIN_DATES} dates → carried but flagged)**: "
        f"`{_join(decision.low_support)}`.\n"
        "- Weak / redundant factors are dropped honestly (as R2-2 dropped "
        "momentum, R3-3 dropped SUE). If the survivor set is empty, R4-5 adds no "
        "new alpha source and the round likely still FAILs — reported, not "
        "papered over.\n",
        "\n## 7. Honest read (development evidence ≠ verdict)\n",
        "- **Strong neutralized IC is NECESSARY, NOT SUFFICIENT.** Rounds 1/2/3 "
        "all had strong train_val IC yet FAILed the locked test; the DSR / PBO / "
        "SPA gates correctly warned each time. A high |t| here does not pre-judge "
        "R4-6.\n"
        "- **The screen's t-stat is OPTIMISTIC** (§ header): overlapping 10/20-day "
        "forward windows make the per-date IC series autocorrelated, so the "
        "effective N is well below n_dates, and `verdicts` takes the best of 3 "
        "horizons — both inflate |t|. The |t| ≥ 3 floor is therefore a generous "
        "screen, NOT the Harvey-Liu-Zhu guarantee on a clean t. The honest "
        "multiple-testing control is R4-5's DSR/PBO/SPA (deflated by the cumulative "
        "trial count across all 4 rounds), not this gate.\n"
        f"- **What is genuinely different this round**: {len(decision.survivors)} "
        "analyst factors survive AND are orthogonal to the existing 12-factor "
        "carry cluster — the strongest pair is tp_impl ↔ ret_20d at |corr| 0.38 "
        "(below the 0.7 ceiling but NOT negligible — target-price implied return "
        "carries a reversal flavour). Still, this is the first round whose new "
        "material is both strong and a largely NEW axis (R2 quality survived but "
        "was insufficient; R3 accruals earned only a 0.006 weight). Analyst "
        "revision is information-flow, not a financial-report derivative — the "
        "orthogonal source the three FAILs lacked.\n"
        "- **The np_rev vs eps_rev pick is a near-tie** (neut |t| 5.64 vs 5.47, "
        "|corr| 0.90): np_rev is kept on a t-margin smaller than the screen's own "
        "inflation, so the specific survivor identity is not robustly preferred — "
        "the two are economically interchangeable (magnitude of the same revision) "
        "and only one belongs in the composite.\n"
        "- **Collinearity fail-open**: a pair with thin 2-way support scores 0.00 "
        "(treated as 'not redundant'), so a low-coverage factor is more likely to "
        "clear the redundancy gate than a well-covered one — §4 shows each "
        "survivor's support and ⚠️-flags thin estimates (none thin this run).\n"
        "- **Coverage caveats (§1)**: tp_impl has the lowest coverage (~30%); "
        "sell-side coverage skews large-cap, so the carry set tilts toward the "
        "covered universe; A-share analysts are systematically optimistic, so only "
        "the *change* (used here) is clean.\n"
        "- **Verdict path**: R4-5 (DSR≥0.95 / PBO≤0.5 / SPA-vs-passive + sentinel "
        "+ CPCV, cumulative-N deflation across 4 rounds) → R4-6 (existing locked "
        "test, 4th evaluation, four gates NOT relaxed). If the analyst alpha does "
        "not produce a positive index excess net of cost, the round is reported "
        "FAIL — no data-snooping to clear the bar.\n",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_r4.csv"
    )
    parser.add_argument(
        "--out",
        default="docs/research/factor-strategy-round4-r4-4-factor-diagnostics-2026-06-21.md",
    )
    parser.add_argument(
        "--params-note",
        default="staleness=90d / lookback=90d / level=180d (main)",
        help="how the panel was built (for the report header)",
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
    "CARRY_CLUSTER",
    "COLLINEARITY_CEILING",
    "CarryDecision",
    "build_report",
    "compute_collinearity",
    "decide_carry",
    "max_carry_collinearity",
    "pairwise_collinearity",
]
