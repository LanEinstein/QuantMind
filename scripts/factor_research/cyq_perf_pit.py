"""PIT reader for the cyq_perf chip-distribution summary — QGR-3 ⑧ bottom gate.

The §3.8B bottom-confirmation gate's "站稳筹码成本带上方" component reads each
code's day-``d`` chip cost band (the price levels at which 5/15/50/85/95% of the
float's average holding cost sits) and ``winner_rate`` (the % of the float in
profit at the close). Two honesty constraints (main doc §3.5):

* **model-derived, not a raw observation** — cyq_perf is Tushare's PROPRIETARY
  chip-distribution model output, NOT an observed market quantity. It is carried
  with that caveat, disclosed in the diagnostic, and the gate is designed to work
  WITHOUT it (ablatable), never treated as a clean additive ranking axis.
* **fail-closed on the degenerate rows** — the model emits an all-zero cost band
  for names it cannot fit (fresh listings with too little trading history); a
  non-positive / non-finite ``cost_50pct`` (the anchor) drops the whole record so
  the gate never confirms against a fabricated band. cyq_perf starts 2018, so a
  pre-2018 day has no snapshot → empty map → the gate fails closed to ``None``.

Reads only the byte-exact PIT store; pure + deterministic, no
``backend.{llm,agents,mirofish}`` import.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from .ingest_round2_data import EP_CYQ_PERF

VENDOR = "tushare"

# Columns consumed from the cyq_perf payload (the cost band + holder-profit rate).
_USECOLS: tuple[str, ...] = (
    "ts_code",
    "cost_5pct",
    "cost_15pct",
    "cost_50pct",
    "cost_85pct",
    "cost_95pct",
    "weight_avg",
    "winner_rate",
)


@dataclass(frozen=True)
class ChipRecord:
    """One code's day-``d`` chip-distribution summary (immutable, model-derived).

    ``cost_50pct`` (the median holder cost) is guaranteed finite + positive — it
    is the anchor the bottom gate compares the close against. The other cost-band
    percentiles / ``weight_avg`` / ``winner_rate`` are ``None`` when that
    particular cell is missing or out of range (never a fabricated value).
    """

    cost_5pct: float | None
    cost_15pct: float | None
    cost_50pct: float  # anchor — always finite + positive (record dropped otherwise)
    cost_85pct: float | None
    cost_95pct: float | None
    weight_avg: float | None
    winner_rate: float | None  # % of float in profit, in [0, 100] or None


class _SnapshotLike(Protocol):
    raw_payload: bytes


class _StoreLike(Protocol):
    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> _SnapshotLike | None: ...


def _pos_float(value: object) -> float | None:
    """Coerce a cell to a finite POSITIVE float, else ``None`` (cost-band cells)."""
    try:
        f = float(str(value))
    except (ValueError, TypeError):
        return None
    return f if (math.isfinite(f) and f > 0.0) else None


def _winner_rate(value: object) -> float | None:
    """Coerce ``winner_rate`` to a finite value in ``[0, 100]``, else ``None``."""
    try:
        f = float(str(value))
    except (ValueError, TypeError):
        return None
    return f if (math.isfinite(f) and 0.0 <= f <= 100.0) else None


def read_cyq_perf(store: _StoreLike, day: str) -> dict[str, ChipRecord]:
    """``{ts_code: ChipRecord}`` for ``day`` (model-derived chip distribution).

    Empty when no cyq_perf snapshot exists for the day (pre-2018 → the gate fails
    closed to ``None``). A row whose ``cost_50pct`` is missing / non-finite /
    non-positive is dropped fail-closed (the model could not fit a real band).
    """
    snap = store.latest(vendor=VENDOR, endpoint=EP_CYQ_PERF, trade_date=day)
    if snap is None:
        return {}
    frame = pd.read_csv(io.BytesIO(snap.raw_payload), usecols=list(_USECOLS))
    out: dict[str, ChipRecord] = {}
    for row in frame.itertuples(index=False):
        cost_50 = _pos_float(row.cost_50pct)
        if cost_50 is None:
            continue  # no usable median-cost anchor → fail closed (skip the name)
        out[str(row.ts_code)] = ChipRecord(
            cost_5pct=_pos_float(row.cost_5pct),
            cost_15pct=_pos_float(row.cost_15pct),
            cost_50pct=cost_50,
            cost_85pct=_pos_float(row.cost_85pct),
            cost_95pct=_pos_float(row.cost_95pct),
            weight_avg=_pos_float(row.weight_avg),
            winner_rate=_winner_rate(row.winner_rate),
        )
    return out


__all__ = ["EP_CYQ_PERF", "VENDOR", "ChipRecord", "read_cyq_perf"]
