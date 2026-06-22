"""PIT reader for the daily limit-up/down board (``limit_list_d``) — QGR-3 ⑦ t2.

The tranche-2 limit-board structure factors (consecutive limit-up streak /
broke-board fade, §3.3) read each code's PRIOR-day ``limit_list_d`` record. Two
things matter for PIT honesty:

* **`<d` only** — same-day ``limit_list_d`` is only complete after the close, so
  the panel consumes the PRIOR day's record for a day-d feature (the reader is
  per-day; the panel does the `<d` shift).
* **availability** — ``limit_list_d`` starts 2020-01, so a day with NO snapshot
  must be distinguished from a day whose snapshot simply does not list the stock
  (it was not limit-up). The reader returns ``(available, records)``: ``available``
  is whether a snapshot exists for the day at all; ``records`` maps the listed
  codes to ``(limit, limit_times, open_times)``. A code absent from ``records`` on
  an available day was not on the board (streak/broke = 0); on an unavailable day
  the factors fail closed to ``None``.

Reads only the byte-exact PIT store; pure + deterministic, no
``backend.{llm,agents,mirofish}`` import.
"""

from __future__ import annotations

import io
import math
from typing import Protocol

import pandas as pd

from .ingest_round2_data import EP_LIMIT_LIST_D

VENDOR = "tushare"

# (limit flag 'U'/'D'/'Z' or None, limit_times or None, open_times or None)
LimitRecord = tuple[str | None, float | None, float | None]


class _SnapshotLike(Protocol):
    raw_payload: bytes


class _StoreLike(Protocol):
    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> _SnapshotLike | None: ...


def _opt_float(value: object) -> float | None:
    try:
        f = float(str(value))
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def read_limit_board(
    store: _StoreLike, day: str
) -> tuple[bool, dict[str, LimitRecord]]:
    """``(available, {ts_code: (limit, limit_times, open_times)})`` for ``day``.

    ``available`` is ``False`` (and the map empty) when no ``limit_list_d``
    snapshot exists for the day (pre-2020) — the panel then fails the streak/broke
    factors closed to ``None``. A malformed ``limit`` cell becomes ``None`` (not on
    a known board); ``limit_times`` / ``open_times`` non-finite → ``None``."""
    snap = store.latest(vendor=VENDOR, endpoint=EP_LIMIT_LIST_D, trade_date=day)
    if snap is None:
        return False, {}
    frame = pd.read_csv(
        io.BytesIO(snap.raw_payload),
        usecols=["ts_code", "limit", "limit_times", "open_times"],
    )
    out: dict[str, LimitRecord] = {}
    for row in frame.itertuples(index=False):
        limit_text = "" if row.limit is None else str(row.limit).strip()
        # Empty / 'nan' cell → None (not a known board flag); else the raw 'U'/'D'/'Z'.
        limit = limit_text if limit_text and limit_text.lower() != "nan" else None
        out[str(row.ts_code)] = (
            limit,
            _opt_float(row.limit_times),
            _opt_float(row.open_times),
        )
    return True, out


__all__ = ["EP_LIMIT_LIST_D", "VENDOR", "LimitRecord", "read_limit_board"]
