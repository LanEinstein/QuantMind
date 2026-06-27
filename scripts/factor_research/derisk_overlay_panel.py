"""Pure overlay transforms for the batch-B1 regime de-risk ablation.

The frozen arena event loop only models the ≤5-slot rotation mechanic, which is
**fully invested by construction** — ``decide_day`` sells only via rotation
(needs a challenger; ≤1/rebalance) and ``protective_stop``/``hard_exit`` make an
incumbent *protected*, not sold. So the only faithful per-day exposure lever is
to rotate a weak slot into a **low-risk destination that wins rotation** (batch-B1
spec §1/§3). This module supplies that destination — a synthetic **cash sleeve**
— plus the per-arm cash-injection schedules and the de-risk health overrides.

* :class:`CashAugmentedBarSource` wraps the PIT ``BarSource`` and overlays a flat,
  zero-return, board=``etf`` cash bar for each ``CASH*.SH`` sleeve — never touching
  the frozen engine / bar-source bytes. Holding cash earns 0 and draws down 0
  (it pays realistic ETF round-trip friction on unwind — conservative, biased
  AGAINST finding a de-risk edge; a documented proxy boundary).
* the cash-injection **schedule** differs only by which rebalance dates get the
  cash treatment: the regime arm uses the high-risk dates; the placebos use a
  regime-blind constant / random set of the SAME size (so a de-risk effect can be
  told apart from "less exposure on average" — only the TIMING differs, §6.6).
* on a treated date the cash sleeves enter as top-scored, strong-health challengers
  AND every stock incumbent is set ``independently_weak`` (the closest proxy for a
  live EXIT in the rotation-only arena) so the engine rotates a weak stock slot into
  cash. Applied identically to the placebos — it cannot manufacture a spurious EDGE
  for the regime arm, only a TIMING difference.

Pure functions of injected look-ahead-free inputs; the only pseudo-randomness is
the seeded placebo-date draw. Never imports the live path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from backend.backtest.event_loop import BarSource, DayBar
from backend.backtest.strategy import CodeHealth

from . import exit_veto_panel as xv

# Five sleeves so the book can rotate fully (≤5 slots) into cash over a spell.
CASH_CODES: tuple[str, ...] = (
    "CASH1.SH",
    "CASH2.SH",
    "CASH3.SH",
    "CASH4.SH",
    "CASH5.SH",
)
CASH_PRICE_CENTS: int = 10_000  # flat ¥100.00 — a zero-return, zero-vol sleeve
CASH_BOARD: str = "etf"  # lowest slippage tier (1.5 bp); ETF-parity friction
CASH_ADV_VOLUME: float = 1e12  # never capacity-limited
_CASH_LIMIT_UP_CENTS: int = 2_000_000  # ≫ open ⇒ never at_limit_up (buyable)
_CASH_LIMIT_DOWN_CENTS: int = 1  # ≪ open ⇒ never at_limit_down (sellable)
# A score that dominates any z-mean ranker score so cash tops the shortlist and
# always wins the rotation margin; distinct per sleeve for deterministic ordering.
CASH_SCORE: float = 1_000_000.0


def cash_bar(code: str, day: str) -> DayBar:
    """A flat, always-tradable synthetic cash bar (zero return, zero drawdown)."""
    return DayBar(
        code=code,
        trade_date=day,
        open_cents=CASH_PRICE_CENTS,
        high_cents=CASH_PRICE_CENTS,
        low_cents=CASH_PRICE_CENTS,
        close_cents=CASH_PRICE_CENTS,
        adv_volume=CASH_ADV_VOLUME,
        limit_up_cents=_CASH_LIMIT_UP_CENTS,
        limit_down_cents=_CASH_LIMIT_DOWN_CENTS,
        board=CASH_BOARD,
        transfer_fee_applies=False,
    )


class CashAugmentedBarSource:
    """Wrap a PIT :class:`BarSource`, overlaying the synthetic cash sleeves.

    Delegates every real bar to the wrapped source and adds a flat cash bar per
    ``cash_codes`` on every day. The wrapped source's bytes are untouched; the
    cash codes are NOT in the wrapped universe (the wrapper is the only place they
    exist). Look-ahead-free: the cash bar is a constant, independent of any day.
    """

    def __init__(
        self, base: BarSource, *, cash_codes: Sequence[str] = CASH_CODES
    ) -> None:
        self._base = base
        self._cash_codes = tuple(cash_codes)

    def trading_days(self) -> tuple[str, ...]:
        return self._base.trading_days()

    def bars_on(self, day: str) -> Mapping[str, DayBar]:
        bars = dict(self._base.bars_on(day))
        for code in self._cash_codes:
            bars[code] = cash_bar(code, day)
        return bars


# ---- cash-injection schedules (only the treated date SET differs per arm) ----


def constant_cash_dates(
    rebalance_dates: Sequence[str], n_treated: int
) -> tuple[str, ...]:
    """``n_treated`` evenly-spaced rebalance dates (the regime-blind placebo).

    Picks indices ``round(i·(R−1)/(n−1))`` so the treated set is spread across the
    window with the SAME count as the regime arm — controlling "less exposure on
    average" while being blind to when crashes actually happen.
    """
    rebs = sorted(set(rebalance_dates))
    r = len(rebs)
    n = max(0, min(n_treated, r))
    if n == 0:
        return ()
    if n == 1:
        return (rebs[r // 2],)
    idx = sorted({round(i * (r - 1) / (n - 1)) for i in range(n)})
    return tuple(rebs[i] for i in idx)


def random_cash_dates(
    rebalance_dates: Sequence[str], n_treated: int, *, seed: int
) -> tuple[str, ...]:
    """``n_treated`` seeded-random rebalance dates (the regime-blind placebo)."""
    rebs = sorted(set(rebalance_dates))
    n = max(0, min(n_treated, len(rebs)))
    if n == 0:
        return ()
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(rebs), size=n, replace=False)
    return tuple(sorted(rebs[i] for i in idx))


# ---- per-arm scores + health (cash treatment on the arm's treated dates) ----


def inject_cash_scores(
    base_scores: Mapping[str, xv.ScoredDay], cash_dates: Sequence[str]
) -> dict[str, xv.ScoredDay]:
    """Add the cash sleeves (top scores) to every treated date's candidate list.

    On a treated date the five cash sleeves dominate the shortlist, so the selector
    short-lists cash (no new stock risk is added); off treated dates the base stock
    scores are returned unchanged (cash is not a buy candidate).
    """
    treated = set(cash_dates)
    out: dict[str, xv.ScoredDay] = {}
    for date, scored in base_scores.items():
        if date in treated:
            cash = [
                (code, CASH_SCORE + (len(CASH_CODES) - i))
                for i, code in enumerate(CASH_CODES)
            ]
            out[date] = cash + list(scored)
        else:
            out[date] = list(scored)
    return out


def _strong_cash_health() -> CodeHealth:
    """Cash held in a high-risk regime: protected (not independently weak)."""
    return CodeHealth(
        line1_percentile=1.0,
        composite_score=CASH_SCORE,
        qualified=True,
        entry_percentile=1.0,
    )


def _weak_cash_health() -> CodeHealth:
    """Cash on an untreated date: independently weak ⇒ rotated back to stocks."""
    return CodeHealth(
        line1_percentile=0.0,
        composite_score=-CASH_SCORE,
        qualified=True,
        entry_percentile=1.0,
        anomaly_flag_active=True,
    )


def _forced_weak_stock_health(score: float) -> CodeHealth:
    """A stock incumbent flagged de-risk-eligible (independently weak) on a treated
    date — the rotation-only arena's faithful proxy for a live EXIT signal.

    ``composite_score`` keeps the stock's real ranker score so the weakest-ranked
    holding is rotated to cash first; the percentile/entry/anomaly fields satisfy
    the 7-condition weakness gate (conditions 4/5/6). Holding-age (condition 3) is
    engine-tracked and not forced — a fresh buy ages naturally before it can exit.
    """
    return CodeHealth(
        line1_percentile=0.0,
        composite_score=score,
        qualified=True,
        entry_percentile=1.0,
        anomaly_flag_active=True,
    )


def build_arm_health(
    ranker_table: pd.DataFrame,
    base_health: Mapping[str, Mapping[str, CodeHealth]],
    cash_dates: Sequence[str],
) -> dict[str, dict[str, CodeHealth]]:
    """``{date: {code: CodeHealth}}`` for one arm — cash treatment on treated dates.

    Treated date: every stock → ``independently_weak`` (de-risk-eligible) + the cash
    sleeves → strong (protected/winning). Untreated date: base panel-driven stock
    health + cash sleeves → weak (so any held cash unwinds back to stocks). Cash
    health is supplied on EVERY date so a held sleeve is always evaluable.
    """
    treated = set(cash_dates)
    score_by_day_code: dict[str, dict[str, float]] = {}
    for date, grp in ranker_table.groupby("date", sort=True):
        score_by_day_code[str(date)] = {
            str(c): float(s)
            for c, s in zip(grp["ts_code"], grp["ranker_score"], strict=True)
        }
    out: dict[str, dict[str, CodeHealth]] = {}
    for date in sorted(score_by_day_code):
        day: dict[str, CodeHealth] = {}
        if date in treated:
            for code, score in score_by_day_code[date].items():
                day[code] = _forced_weak_stock_health(score)
            cash_health = _strong_cash_health()
            for cash_code in CASH_CODES:
                day[cash_code] = cash_health
        else:
            day.update(dict(base_health.get(date, {})))
            cash_health = _weak_cash_health()
            for cash_code in CASH_CODES:
                day[cash_code] = cash_health
        out[date] = day
    return out


def cash_buy_intent_count(decision_vectors: Sequence[object]) -> int:
    """How many decided BUY intents were cash sleeves (de-risk treatment, diagnostic).

    Counts ``DayDecision.buy_codes`` (the orders the strategy DECIDED on the
    rebalance date), NOT realized T+1 fills — a cash buy may not fill (cash
    exhausted across same-day sleeves, or a reject), so this is an upper bound on
    realized de-risk bite. It is a mechanism diagnostic only; the deployable-edge
    verdict consumes returns / MDD, never this count (codex CONFIRMED #1).
    """
    cash_set = set(CASH_CODES)
    total = 0
    for d in decision_vectors:
        buys = getattr(d, "buy_codes", ())
        total += sum(1 for c in buys if c in cash_set)
    return total


__all__ = [
    "CASH_CODES",
    "CASH_SCORE",
    "CashAugmentedBarSource",
    "build_arm_health",
    "cash_bar",
    "cash_buy_intent_count",
    "constant_cash_dates",
    "inject_cash_scores",
    "random_cash_dates",
]
