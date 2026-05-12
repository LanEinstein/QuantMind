"""Pure suspension detection for watchlist 30s snapshots.

P0-8 §1.6.1 locks suspension recognition as a *pure function over a
snapshot row*. Three heuristics are enough for the A-share spot view
shipped by ``akshare.stock_zh_a_spot_em`` and the ``adata`` primary that
:class:`backend.models.market.WatchlistMarketSnapshot` normalises both
into:

1. **Missing price** — ``price <= 0`` or ``prev_close <= 0``. A stopped
   stock has no last-traded price on the spot view; vendors typically
   report 0.
2. **Zero volume *and* zero amount** during trading hours — the row is
   syntactically alive but no trades happened, the classical halt
   signature. Volume alone is not enough (new listings have low but
   non-zero volume during the auction); the *amount* zero gate avoids
   false positives.
3. **NaN-like change_pct** — vendors that propagate halts as ``NaN`` or
   sentinel values (``-1.0`` is a common amber flag) end up here. We
   only treat *exact NaN* via ``math.isnan`` to keep the heuristic
   conservative; small negative pcts are legitimate.

The function is intentionally pure and accepts the ``WatchlistMarketSnapshot``
dataclass (P0-8 §2 redline 11 — single-source-of-truth) rather than the
raw vendor row, so suspension detection runs *after* the model has
validated the field set (P0-3 §2 redline 12 strict / extra='forbid').

This module is part of the data-quality boundary (P0-8 §2 redline 8,
P1-2.B §2 redline 8): no ``backend.llm`` / ``backend.agents`` /
``backend.risk`` imports, no IO.
"""

from __future__ import annotations

import math

from backend.models.market import WatchlistMarketSnapshot


def is_suspended(snapshot: WatchlistMarketSnapshot) -> bool:
    """Return True when ``snapshot`` matches a known suspension pattern.

    See module docstring for the three locked heuristics. Any one match
    flips the result to ``True``; absence of all three returns ``False``.

    NaN-form prices (vendors that propagate halts as ``NaN`` rather than
    ``0``) are folded into the first heuristic — ``NaN <= 0`` is False
    in Python, so we test :func:`math.isnan` explicitly. The same
    treatment covers ``NaN`` volume / amount in the second heuristic.

    Args:
        snapshot: A persisted-or-pending :class:`WatchlistMarketSnapshot`
            row from the 30s scheduler tick.

    Returns:
        True if the snapshot looks like a halted instrument.
    """
    if (
        snapshot.price <= 0
        or snapshot.prev_close <= 0
        or math.isnan(snapshot.price)
        or math.isnan(snapshot.prev_close)
    ):
        return True
    vol_zero = snapshot.volume == 0 or math.isnan(snapshot.volume)
    amt_zero = snapshot.amount == 0 or math.isnan(snapshot.amount)
    if vol_zero and amt_zero:
        return True
    if math.isnan(snapshot.change_pct):
        return True
    return False


__all__ = ["is_suspended"]
