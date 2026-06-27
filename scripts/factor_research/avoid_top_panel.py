"""Avoid-top trigger table for the C1 avoid-top EXIT-on-held ablation.

C1 (plan ``misty-doodling-pnueli`` §A4 / criterion-rebar §8.1 / handoff C1.1)
cashes the batch-A finding (``ideal_amplitude_20d`` is a real orthogonal,
size-neutral crowding EXIT axis — A2 PASS, DSR 1.000) into an EXIT-ON-HELD
overlay: each day, a HELD name that has entered the crowded / over-extended
state AND confirmed a rolling-top (P-A symmetry) is SOLD on the next bar through
the frozen C0b execution contract. This is the live **Line-2 monitoring** SELL
the rotation-only frozen engine structurally cannot express (the B1/B2 "barely
bites" wall — see ``qgr-4-exit-veto-ablation-results`` / ``b1`` / ``b2``).

This module is the **thin, pure** trigger layer: it turns the neutralised
batch-A crowding panel into a per-rebalance-date **crowded set** (top decile of
the size/industry-neutral ``ideal_amplitude_20d``) and a **forward-fill**
``crowded_asof(day)`` lookup the daily event loop consults between rebalances.
The momentum-stall CONFIRMATION (P-A "确认滚顶, 非择顶" — §9 Erratum) lives in the
stateful overlay (it needs the daily close path), NOT here — this layer only
decides *which names are crowded*.

It **reuses** ``exit_veto_panel`` (``build_ranker_table`` for the QGR-3 fast-leg
buy ranker + ``crowd_pct`` axis; ``veto_codes_by_day`` for the top-decile crowded
set; ``scores_by_day`` / ``build_health_overrides`` for the arena arms) so the
ranker / crowding / health machinery is byte-for-byte the same as the QGR-4
EXIT-veto cut — only the CONSUMPTION differs (EXIT-on-held vs BUY-set veto).

The crowded cutoff ``top_q = 0.90`` is the batch-A §3 **pre-committed** decile
(NOT re-searched here) — re-using a frozen threshold adds no new mining-debt
degree of freedom. Pure: no IO, no wall-clock, no randomness; never the live path.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

import pandas as pd

from .exit_veto_panel import TOP_CROWD_Q, veto_codes_by_day

# The avoid-top crowded axis = the batch-A orthogonal size-neutral EXIT winner
# (A2 PASS). Re-exported from ``exit_veto_panel.CROWD_FACTOR`` semantics; the
# crowded set is selected on its ``crowd_pct`` (within-date rank) >= ``top_q``.
AVOID_TOP_TOP_Q: float = TOP_CROWD_Q  # 0.90 — batch-A §3 pre-committed decile


@dataclass(frozen=True)
class AvoidTopTriggerTable:
    """Per-rebalance-date crowded set + a PIT forward-fill ``crowded_asof``.

    The crowded flag at rebalance date ``d`` is computed from bars ``<= d`` (the
    neutralised panel is PIT by construction) and applied on every daily day in
    ``[d, d')`` until the next rebalance date ``d'`` — a forward-fill that never
    looks ahead (a name's crowded status only ever uses information available at
    or before the day it is acted on).
    """

    rebalance_dates: tuple[str, ...]
    crowded_by_date: dict[str, frozenset[str]]
    top_q: float

    def crowded_asof(self, day: str) -> frozenset[str]:
        """The crowded ts_code set effective on ``day`` (most-recent rebalance ≤ day).

        Returns the empty set before the first rebalance date (no crowded
        information is available yet — fail-open to "nothing crowded", never a
        fabricated trigger).
        """
        idx = bisect.bisect_right(self.rebalance_dates, day) - 1
        if idx < 0:
            return frozenset()
        return self.crowded_by_date.get(self.rebalance_dates[idx], frozenset())

    @property
    def total_crowded_flags(self) -> int:
        """Total (date × code) crowded flags — the EXIT trigger opportunity count."""
        return sum(len(s) for s in self.crowded_by_date.values())


def build_avoid_top_triggers(
    ranker_table: pd.DataFrame, *, top_q: float = AVOID_TOP_TOP_Q
) -> AvoidTopTriggerTable:
    """Build the avoid-top trigger table from a built ``ranker_table``.

    Reuses ``exit_veto_panel.veto_codes_by_day`` (top-``top_q`` ``crowd_pct``) so
    the crowded set is the SAME selection as the QGR-4 EXIT-veto cut — here it
    feeds an EXIT-on-held overlay instead of a BUY-set veto.
    """
    crowded = veto_codes_by_day(ranker_table, top_q=top_q)
    crowded_frozen = {d: frozenset(s) for d, s in crowded.items()}
    rebalance_dates = tuple(sorted(crowded_frozen))
    return AvoidTopTriggerTable(
        rebalance_dates=rebalance_dates,
        crowded_by_date=crowded_frozen,
        top_q=top_q,
    )


__all__ = [
    "AVOID_TOP_TOP_Q",
    "AvoidTopTriggerTable",
    "build_avoid_top_triggers",
]
