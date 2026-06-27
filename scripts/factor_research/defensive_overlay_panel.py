"""Pure overlay transforms for the batch-B2 permanent defensive-sleeve ablation.

B1 proved (a) the rotation-only arena is fully invested by construction so no
overlay reaches MDD <= 8%, and (b) regime TIMING is negative skill (blind constant
cash beat regime-timed cash). B2 therefore drops timing and tests a **permanent
defensive sleeve** (batch-B2 spec): always reserve K slots for a defensive
destination, and ask (Q1) does the destination identity matter — synthetic cash vs
red-dividend equity (510880) vs government bonds (511010) — and (Q2) sweep the cash
intensity K=1..4 to map the MDD/return frontier (the honest best partial).

Mechanism: the destination asset is injected as a top-scored candidate on EVERY
rebalance date and given strong (protected, never-independently-weak) health
(``arena_ablation.strong_protected_health``), so it is bought at the day-1 fill and
held forever; the remaining slots run the normal QGR-3 ranker with panel-driven
rotation. Real defensive ETFs are already priced by the PIT bar source (``fund_daily``),
so only the synthetic cash sweep needs B1's ``CashAugmentedBarSource``.

Pure functions of injected look-ahead-free inputs; no IO, no wall-clock, no RNG.
Never imports the live path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from backend.backtest.strategy import CodeHealth

from . import exit_veto_panel as xv
from .arena_ablation import PROTECTED_COMPOSITE, strong_protected_health

# Full-window PIT-covered defensive destinations (probe 2026-06-27). NOTE: 510880
# (上证红利) is an EQUITY dividend ETF — it crashed ~45% in the 2015 broad crash, so
# it is NOT a broad-crash hedge; only bonds (511010) / cash defend a broad crash.
# The dedicated 红利低波 ETFs (512890/515080/515100) are post-2018/2019 and miss the
# 2015/2018 crashes, so they are excluded from this full-window study. Only the two
# destinations actually used as arm sleeves are named here (gold 518880 is full-window
# too but is not wired, so it is not advertised as a priceable destination).
DIVIDEND_ETF: str = "510880.SH"  # 上证红利 (equity dividend tilt)
BOND_ETF: str = "511010.SH"  # 国债 ETF (true broad-crash defensive)


def inject_permanent_scores(
    base_scores: Mapping[str, xv.ScoredDay], asset_codes: Sequence[str]
) -> dict[str, xv.ScoredDay]:
    """Add ``asset_codes`` as top candidates on EVERY date (permanent sleeve).

    The destination assets dominate the shortlist, so they fill slots at the day-1
    rebalance; the remaining slots take the top ranker stocks. An empty ``asset_codes``
    returns the base stock scores unchanged (the baseline arm).
    """
    out: dict[str, xv.ScoredDay] = {}
    n = len(asset_codes)
    for date, scored in base_scores.items():
        injected = [
            (code, PROTECTED_COMPOSITE + (n - i))
            for i, code in enumerate(asset_codes)
        ]
        out[date] = injected + list(scored)
    return out


def build_permanent_health(
    ranker_table: pd.DataFrame,
    base_health: Mapping[str, Mapping[str, CodeHealth]],
    asset_codes: Sequence[str],
) -> dict[str, dict[str, CodeHealth]]:
    """``{date: {code: CodeHealth}}`` — destinations protected, stocks panel-driven.

    Every date: the destination assets get protected health (locked permanent slot);
    the stocks keep their base panel-driven health (normal rotation among the
    remaining slots). An empty ``asset_codes`` reproduces the base health (baseline).
    """
    protected = strong_protected_health()
    out: dict[str, dict[str, CodeHealth]] = {}
    for date in sorted({str(d) for d in ranker_table["date"]}):
        day = dict(base_health.get(date, {}))
        for code in asset_codes:
            day[code] = protected
        out[date] = day
    return out


def asset_buy_intent_count(
    decision_vectors: Sequence[object], asset_codes: Sequence[str]
) -> int:
    """How many decided BUY intents were destination assets (sleeve-fill diagnostic).

    Counts ``DayDecision.buy_codes`` (decided intents), NOT realized T+1 fills — an
    upper bound on realized sleeve occupancy. A persisted, non-zero count is the
    fail-closed signal that the permanent sleeve actually engaged (codex B2 #1/#2).
    """
    targets = set(asset_codes)
    total = 0
    for d in decision_vectors:
        buys = getattr(d, "buy_codes", ())
        total += sum(1 for c in buys if c in targets)
    return total


__all__ = [
    "BOND_ETF",
    "DIVIDEND_ETF",
    "asset_buy_intent_count",
    "build_permanent_health",
    "inject_permanent_scores",
]
