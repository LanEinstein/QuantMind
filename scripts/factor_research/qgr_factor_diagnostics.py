"""QGR-3 short-term factor diagnostics ⑦ — IC + collinearity + limit disclosure.

The R4-4-protocol honest sign verification for the QGR fast-leg factors
(reversal + forced lottery removal): rank IC (raw + industry/size-neutralized) on
the train_val QGR panel, the sign verified FROM ZERO (the A-share literature prior
is never assumed), collinearity against the round-1 cross-sectional carry cluster
(the fast-leg cousins — ret_5d / max_20d / turn_20d / …) AND among the new factors
themselves, plus the §3.1 limit-loser disclosure (reversal IC with vs without the
day-d at-limit names). A thin reporting orchestrator over the already-tested
``study`` / ``neutralize_panel`` / ``_pairwise`` pieces — deterministic, offline,
train_val only (the sealed test window is never read).

Inclusion gate (same as R2-2 / R3-3 / R4-4): a factor is CARRIED only if its
best-horizon **neutralized** ``|t| >= T_BAR`` (Harvey-Liu-Zhu floor) with the
literature-aligned sign AND it is not highly collinear (``|corr| > 0.7``) with the
carry cluster, then a greedy MUTUAL dedup keeps the strongest-``|t|`` representative
of each collinear cluster. A weak / redundant factor is dropped honestly.

The IC ``|t|`` is an OPTIMISTIC SCREEN, not the verdict — overlapping forward
windows make the per-date IC autocorrelated (effective N < n_dates) and the gate
takes the best of 3 horizons. The honest multiple-testing control is the QGR-2
arena's DSR / SPA / Romano-Wolf with cumulative-N deflation (QGR-4), never this
gate. QGR-3 only builds the library + verifies the sign; it runs no search.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .factor_ic_study import rank_ic_series, study, summarize_ic
from .factor_lib import FACTOR_NAMES, QGR2_FACTOR_NAMES, QGR_FACTOR_NAMES
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
from .r4_factor_diagnostics import _pairwise

WINSOR_QUANTILE: float = 0.01
MIN_OBS: int = 20
COLLINEARITY_CEILING: float = 0.7
MIN_COLLIN_DATES: int = 60
# The carry cluster = the round-1 cross-sectional factors (the fast-leg cousins).
# A QGR short factor redundant with these adds no new axis. The slower
# fundamental / analyst families (R2-R4) are a separate slow-leg dimension and are
# verified there, not against the fast leg.
CARRY_CLUSTER: tuple[str, ...] = FACTOR_NAMES
# All QGR short factors under verdict = tranche-1 (reversal+lottery) ∪ tranche-2
# (1-day momentum + limit-board structure).
QGR_ALL_NAMES: tuple[str, ...] = (*QGR_FACTOR_NAMES, *QGR2_FACTOR_NAMES)
# Forward horizons: add the fast-leg T+1 to the 5/10/20d set (the 1-day momentum
# factor is a T+1 signal; the round-1 cousins live at 5/10/20d).
QGR_FORWARD_COLS: tuple[str, ...] = (
    "fwd_ret_1d",
    "fwd_ret_5d",
    "fwd_ret_10d",
    "fwd_ret_20d",
)
# Reversal factors whose loser leg the §3.1 limit disclosure inspects.
REVERSAL_FACTORS: tuple[str, ...] = ("rev_1d", "rev_3d")


def _neut(factor: str) -> str:
    return f"{factor}{NEUT_SUFFIX}"


@dataclass(frozen=True)
class CarryDecision:
    """The QGR-3 carry verdict for the short-term factors (immutable)."""

    survivors: tuple[str, ...]
    carry_redundant: tuple[str, ...]
    mutual_redundant: tuple[str, ...]
    no_signal: tuple[str, ...]
    low_support: tuple[str, ...]


def max_carry_collinearity(
    neut_panel: pd.DataFrame, factor: str
) -> tuple[str, float, int]:
    """``(carry_factor, |corr|, support_dates)`` of the most-collinear carry factor.

    Both sides use the neutralized (``*_neut``) columns (residual-vs-residual).
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
    """Pre-compute the carry-cluster + mutual collinearity dicts (neut columns)."""
    carry_collin = {f: max_carry_collinearity(neut_panel, f) for f in QGR_ALL_NAMES}
    mutual: dict[frozenset[str], tuple[float, int]] = {}
    for i, a in enumerate(QGR_ALL_NAMES):
        for b in QGR_ALL_NAMES[i + 1 :]:
            mutual[frozenset((a, b))] = _pairwise(neut_panel, _neut(a), _neut(b))
    return carry_collin, mutual


def decide_carry(
    neut_verdicts: list[FactorVerdict],
    *,
    carry_collin: dict[str, tuple[str, float, int]],
    mutual: dict[frozenset[str], tuple[float, int]],
) -> CarryDecision:
    """Apply the full inclusion gate → the QGR short-factor carry set.

    (1) IC — neutralized ``|t| >= T_BAR`` AND literature-aligned sign; (2) carry
    redundancy — drop if max ``|corr|`` with the carry cluster exceeds the ceiling;
    (3) mutual redundancy — greedily keep the strongest-``|t|`` representative of
    each collinear (> ceiling) cluster. A survivor whose carry-collinearity rested
    on fewer than :data:`MIN_COLLIN_DATES` dates is flagged ``low_support``.
    """
    by_base = {
        v.factor[: -len(NEUT_SUFFIX)]: v
        for v in neut_verdicts
        if v.factor.endswith(NEUT_SUFFIX)
    }
    no_signal = tuple(
        f
        for f in QGR_ALL_NAMES
        if not (f in by_base and by_base[f].has_signal and by_base[f].aligned)
    )
    candidates = [f for f in QGR_ALL_NAMES if f not in no_signal]
    carry_redundant = tuple(
        f
        for f in candidates
        if carry_collin.get(f, ("-", 0.0, 0))[1] > COLLINEARITY_CEILING
    )
    surviving = [f for f in candidates if f not in carry_redundant]
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
    survivors = tuple(f for f in QGR_ALL_NAMES if f in kept)
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
    lines = [
        "| factor | defined-rate (of cohort rows) | mean (defined) |",
        "|---|---|---|",
    ]
    for f in QGR_ALL_NAMES:
        defined = float(panel[f].notna().mean()) if f in panel.columns else 0.0
        sub = panel[f].dropna() if f in panel.columns else pd.Series(dtype=float)
        mean = float(sub.mean()) if len(sub) else float("nan")
        lines.append(f"| {f} | {defined:.2%} | {mean:+.4f} |")
    return "\n".join(lines)


def _collinearity_section(
    carry_collin: dict[str, tuple[str, float, int]],
    mutual: dict[frozenset[str], tuple[float, int]],
) -> str:
    ceil = COLLINEARITY_CEILING
    lines = [
        f"| QGR factor | most-collinear carry | |corr| | support (dates) | "
        f"redundant >{ceil:.1f}? |",
        "|---|---|---|---|---|",
    ]
    for f in QGR_ALL_NAMES:
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
        lines.append(f"**Mutually collinear QGR pairs (|corr| > {ceil:.1f}):**")
        for pair, v in high:
            lines.append(f"- `{pair[0]}` ↔ `{pair[1]}` = **{v:.2f}**")
    else:
        lines.append(f"No QGR pair exceeds |corr| {ceil:.1f} (all distinct axes).")
    return "\n".join(lines)


def reversal_ic_under_filter(
    panel: pd.DataFrame, factor: str, fwd_col: str, *, mask: pd.Series | None = None
) -> tuple[float, float, int]:
    """``(IC_mean, t, n_dates)`` for ``factor`` on the (optionally masked) panel.

    ``mask`` keeps only the rows where it is True (e.g. NOT at the down-limit).
    """
    sub = panel if mask is None else panel[mask]
    ics = rank_ic_series(sub, factor, fwd_col)
    s = summarize_ic(factor, fwd_col, ics)
    return s.ic_mean, s.t_stat, s.n_dates


def _limit_disclosure_section(panel: pd.DataFrame) -> str:
    """§3.1 loser-leg disclosure: reversal IC full vs excluding at-limit names.

    A reversal LOSER (very negative recent return = maximally "attractive") that
    closed at the down-limit is a falling knife, not a buyable oversold name —
    excluding it should sharpen the reversal IC. Reported at the 5d horizon (the
    canonical fast-leg label), so the cohort-bias caveat is visible, not papered
    over.
    """
    fwd = "fwd_ret_5d"
    has_down = "at_down_limit_d" in panel.columns
    has_up = "at_up_limit_d" in panel.columns
    no_down = (~panel["at_down_limit_d"].astype(bool)) if has_down else None
    no_any = None
    if has_down and has_up:
        no_any = (~panel["at_down_limit_d"].astype(bool)) & (
            ~panel["at_up_limit_d"].astype(bool)
        )
    lines = [
        f"| reversal factor | filter | IC_mean ({fwd}) | t | n_dates |",
        "|---|---|---|---|---|",
    ]
    for f in REVERSAL_FACTORS:
        if f not in panel.columns:
            continue
        ic, t, n = reversal_ic_under_filter(panel, f, fwd)
        lines.append(f"| {f} | all rows | {ic:+.4f} | {t:+.2f} | {n} |")
        if no_down is not None:
            ic, t, n = reversal_ic_under_filter(panel, f, fwd, mask=no_down)
            lines.append(
                f"| {f} | exclude at-down-limit | {ic:+.4f} | {t:+.2f} | {n} |"
            )
        if no_any is not None:
            ic, t, n = reversal_ic_under_filter(panel, f, fwd, mask=no_any)
            lines.append(f"| {f} | exclude any at-limit | {ic:+.4f} | {t:+.2f} | {n} |")
    frac_down = float(panel["at_down_limit_d"].astype(bool).mean()) if has_down else 0.0
    frac_up = float(panel["at_up_limit_d"].astype(bool).mean()) if has_up else 0.0
    lines.append("")
    lines.append(
        f"> Cohort rows at the down-limit on d: **{frac_down:.2%}**; at the "
        f"up-limit on d: **{frac_up:.2%}**. The strategy (QGR-4) filters un-buyable "
        "at-limit names; here they are KEPT so the IC measurement is unbiased and "
        "the loser-leg effect is disclosed, not hidden."
    )
    return "\n".join(lines)


def build_report(panel: pd.DataFrame, *, params_note: str = "") -> str:
    """Assemble the QGR-3 short-factor diagnostic Markdown (deterministic)."""
    under_neut = (*CARRY_CLUSTER, *QGR_ALL_NAMES)
    neut_panel = neutralize_panel(
        panel, list(under_neut), min_obs=MIN_OBS, winsor_quantile=WINSOR_QUANTILE
    )
    qgr_neut_names = tuple(_neut(f) for f in QGR_ALL_NAMES)
    ic_all = study(
        neut_panel,
        factor_names=(*QGR_ALL_NAMES, *qgr_neut_names),
        forward_cols=QGR_FORWARD_COLS,
    )
    raw = [s for s in ic_all if not s.factor.endswith(NEUT_SUFFIX)]
    neut = [s for s in ic_all if s.factor.endswith(NEUT_SUFFIX)]
    qgr_raw_verdicts = verdicts(raw, QGR_ALL_NAMES)
    qgr_neut_verdicts = verdicts(neut, qgr_neut_names)

    carry_collin, mutual = compute_collinearity(neut_panel)
    decision = decide_carry(qgr_neut_verdicts, carry_collin=carry_collin, mutual=mutual)

    dates = panel["date"].nunique()
    codes = panel["code"].nunique()
    note = f"\n> Panel params: {params_note}.\n" if params_note else "\n"

    def _join(names: tuple[str, ...]) -> str:
        return ", ".join(names) if names else "(none)"

    parts = [
        "# QGR-3 short-term factor diagnostics ⑦ (train_val only)\n",
        f"> Panel: {len(panel)} rows / {codes} codes / {dates} rebalance dates "
        "(train_val; the sealed test window is never read)."
        + note
        + "> Family (full fast leg): tranche-1 reversal (rev_1d/rev_3d) + lottery "
        "removal (max_5d/turn_spike/n_limit_up_5d); tranche-2 1-day momentum "
        "(intraday_ret_1d/overnight_gap_1d) + limit-board structure "
        "(limit_streak_prev/broke_board_prev, `<d`, limit_list_d 2020+). Carry "
        "cluster = the round-1 cross-sectional factors. Horizons 1/5/10/20d "
        "(fwd_ret_1d = fast-leg T+1).\n"
        f"> Neutralization: industry SW-L1 dummies + log(circ_mv), per-date OLS, "
        f"winsor={WINSOR_QUANTILE}, min_obs={MIN_OBS}. Collinearity: PAIRWISE 2-way "
        "common support on the *_neut columns.\n"
        f"> Inclusion gate: neutralized |t| ≥ {T_BAR:.0f} + aligned sign + "
        f"|corr| ≤ {COLLINEARITY_CEILING} vs the carry cluster AND vs a stronger "
        "QGR factor. **The IC t-stat is an OPTIMISTIC SCREEN, not the verdict** "
        "(overlapping forward windows → autocorrelated IC, effective N < n_dates; "
        "best-of-3-horizons) — the honest control is the QGR-2 arena's "
        "DSR/SPA/Romano-Wolf with cumulative-N deflation (QGR-4).\n",
        "## 1. Coverage (defined-rate of cohort rows)\n",
        _coverage_section(panel),
        "\n## 2. QGR-factor honest verdict (raw)\n",
        _verdict_table(qgr_raw_verdicts),
        "\n## 3. QGR-factor honest verdict (industry+size neutralized)\n",
        _verdict_table(qgr_neut_verdicts),
        "\n## 4. Collinearity vs the round-1 carry cluster + mutual\n",
        _collinearity_section(carry_collin, mutual),
        "\n## 5. §3.1 limit-loser disclosure (reversal IC with vs without at-limit)\n",
        _limit_disclosure_section(panel),
        "\n## 6. IC tables — QGR factors (raw + neutralized)\n",
        _ic_table([*raw, *neut]),
        "\n## 7. Carry decision\n",
        f"- **Survivors (neut |t| ≥ {T_BAR:.0f} + aligned + |corr| ≤ "
        f"{COLLINEARITY_CEILING})**: `{_join(decision.survivors)}`.\n"
        f"- **Dropped — no signal (neut |t| < {T_BAR:.0f} or misaligned)**: "
        f"`{_join(decision.no_signal)}`.\n"
        f"- **Dropped — redundant with the round-1 carry cluster (|corr| > "
        f"{COLLINEARITY_CEILING})**: `{_join(decision.carry_redundant)}`.\n"
        f"- **Dropped — redundant with a stronger QGR factor**: "
        f"`{_join(decision.mutual_redundant)}`.\n"
        f"- **Thin-collinearity-support survivors (< {MIN_COLLIN_DATES} dates, "
        f"carried but flagged)**: `{_join(decision.low_support)}`.\n"
        "- Weak / redundant factors are dropped honestly (as R2-2 dropped "
        "momentum, R3-3 dropped SUE). The survivor set is the QGR-4 short-leg "
        "candidate axis; if empty, the fast leg adds no orthogonal signal — "
        "reported, not papered over.\n",
        "\n## 8. Honest read (development evidence ≠ verdict)\n"
        "- **Strong neutralized IC is NECESSARY, NOT SUFFICIENT.** Rounds 1-4 all "
        "had strong train_val IC yet the locked test FAILed three times; the "
        "honest gates correctly warned each time. A high |t| here does not "
        "pre-judge anything — QGR-3 runs NO search and makes NO promotion.\n"
        "- **Sign verified from zero**: the A-share daily/short reversal and "
        "anti-lottery priors are CONFIRMED or REFUTED on real data here, never "
        "assumed; a refuted-sign factor is dropped.\n"
        "- **Limit-loser caveat (§5)**: the reversal loser leg is polluted by "
        "at-down-limit falling knives; the strategy filters them, the diagnostic "
        "discloses the effect.\n"
        "- **Limit-board factors are post-2020-only**: `limit_list_d` starts 2020, "
        "so `limit_streak_prev` / `broke_board_prev` are None for every pre-2020 "
        "rebalance date (§1 defined-rate ≈ the post-2020 fraction, NOT data loss). "
        "Their IC is measured on the post-2020 sub-regime — read alongside the "
        "full-window 1/5/10/20d cousins, not as comparable coverage.\n",
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
        default="docs/research/qgr-3-short-factor-diagnostics-2026-06-22.md",
    )
    parser.add_argument(
        "--params-note",
        default="rebalance=5td / fast leg t1(reversal+lottery)+t2(1d-mom+limit-board)",
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
    "reversal_ic_under_filter",
]
