"""PIT reader for daily price-limit bands (``stk_limit``) — QGR-3 short-term panel.

The QGR-3 fast-leg factors need each code's same-day up/down price-limit band to
(a) census limit-up closes (the limit-CENSORED MAX, ``n_limit_up_5d``) and (b)
carry day-``d`` tradability flags (closed-at-up-limit / closed-at-down-limit) so
the IC study can disclose the §3.1 loser-leg caveat (reversal losers cluster in
limit-locked / distressed names) WITHOUT biasing the ranked cohort.

The bands are the RAW (unadjusted) same-day prices Tushare publishes — compared
directly against the RAW close, no adjustment needed (both move together). A row
with a missing / NaN / non-positive band is dropped fail-closed (an unknown band
cannot anchor a censored observation). Reads only the byte-exact PIT store; pure
+ deterministic, no ``backend.{llm,agents,mirofish}`` import.
"""

from __future__ import annotations

import io
import math
from typing import Protocol

import pandas as pd

from .ingest_round2_data import EP_STK_LIMIT

VENDOR = "tushare"


class _SnapshotLike(Protocol):
    raw_payload: bytes


class _StoreLike(Protocol):
    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> _SnapshotLike | None: ...


def read_limits(store: _StoreLike, day: str) -> dict[str, tuple[float, float]]:
    """``{ts_code: (up_limit, down_limit)}`` for ``day`` (raw same-day prices).

    Returns an empty mapping when no ``stk_limit`` snapshot exists for the day
    (the panel then carries NaN bands → ``n_limit_up_5d`` fails closed for any
    window touching that day). A malformed / NaN / non-positive band row is
    skipped (never a fabricated band).
    """
    snap = store.latest(vendor=VENDOR, endpoint=EP_STK_LIMIT, trade_date=day)
    if snap is None:
        return {}
    frame = pd.read_csv(
        io.BytesIO(snap.raw_payload), usecols=["ts_code", "up_limit", "down_limit"]
    )
    out: dict[str, tuple[float, float]] = {}
    for row in frame.itertuples(index=False):
        try:
            up = float(str(row.up_limit))
            down = float(str(row.down_limit))
        except (ValueError, TypeError):
            continue
        if not (math.isfinite(up) and math.isfinite(down)) or up <= 0 or down <= 0:
            continue
        out[str(row.ts_code)] = (up, down)
    return out


__all__ = ["EP_STK_LIMIT", "VENDOR", "read_limits"]
