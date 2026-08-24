"""Align stored daily price limits to an already-loaded priced panel.

The 520 studies prefetched limits for a computed set of "bars a replay could
touch", which needed a fixed-point refinement loop because knowing which bars
are touched depends on the limits themselves.  The right-side-wave study drops
that circularity by aligning the whole ``stk_limit`` panel to the priced panel
once: two float arrays per security, indexed exactly like its price arrays.

Research-only.  No playbook, simulator order, or broker instruction is created.
"""

from __future__ import annotations

import gc
import io
from pathlib import Path

import numpy as np
import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_priced_panel import PricedSeries

VENDOR = "tushare"
LIMIT_COLUMNS = ["trade_date", "ts_code", "up_limit", "down_limit"]
# A stored up_limit at or above this means no effective ceiling that day (new
# listings, resumption auctions); same reading as the 520 executability audit.
NO_UP_LIMIT = 9_999.0
NO_DOWN_LIMIT = 0.02
PRICE_TOLERANCE = 1e-6


def _read_limits(store: SnapshotStore, trade_date: str) -> pd.DataFrame | None:
    snapshot = store.latest(vendor=VENDOR, endpoint="stk_limit", trade_date=trade_date)
    if snapshot is None:
        return None
    frame = pd.read_csv(
        io.BytesIO(snapshot.raw_payload),
        usecols=LIMIT_COLUMNS,
        dtype={"trade_date": "int32", "ts_code": "string"},
    )
    # read_csv keeps the file's column order, not the usecols order.
    return frame[LIMIT_COLUMNS]


def load_limit_panel(
    pit_root: Path, *, start_date: str, end_date: str
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]], dict[str, object]]:
    """Read every stored ``stk_limit`` row in range, grouped per security.

    Returns ``code -> (dates, up_limit, down_limit)`` with dates ascending, plus
    a coverage dict naming the trade dates that had no snapshot at all.
    """

    calendar = load_trade_dates(pit_root)
    dates = tuple(day for day in calendar if start_date <= day <= end_date)
    store = SnapshotStore(pit_root)
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for trade_date in dates:
        frame = _read_limits(store, trade_date)
        if frame is None:
            missing.append(trade_date)
            continue
        frames.append(frame)
    if not frames:
        raise ValueError("no stk_limit snapshots in requested range")
    panel = pd.concat(frames, ignore_index=True)
    del frames
    for column in ("up_limit", "down_limit"):
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel.sort_values(["ts_code", "trade_date"], kind="mergesort", inplace=True)
    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for code, group in panel.groupby("ts_code", sort=False, observed=True):
        out[str(code)] = (
            group["trade_date"].to_numpy(dtype=np.int32, copy=True),
            group["up_limit"].to_numpy(dtype=float, copy=True),
            group["down_limit"].to_numpy(dtype=float, copy=True),
        )
    coverage = {
        "requested_date_count": len(dates),
        "dates_missing_limit_snapshot": missing,
        "limit_rows": int(len(panel)),
        "securities_with_limit_rows": len(out),
    }
    del panel
    gc.collect()
    return out, coverage


def align_limits(
    series: PricedSeries,
    limits: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray]:
    """Project one security's stored limits onto its own price bar dates.

    A bar with no stored limit row gets NaN, which callers must treat as
    "unverified fill" rather than as "no limit in force".
    """

    entry = limits.get(series.code)
    length = len(series.dates)
    if entry is None:
        return np.full(length, np.nan), np.full(length, np.nan)
    limit_dates, up, down = entry
    position = np.searchsorted(limit_dates, series.dates, side="left")
    clipped = np.clip(position, 0, len(limit_dates) - 1)
    matched = limit_dates[clipped] == series.dates
    return (
        np.where(matched, up[clipped], np.nan),
        np.where(matched, down[clipped], np.nan),
    )


def blocks_buy(raw_open: float, up_limit: float) -> bool:
    """Is a buy at this raw open contradicted by the stored ceiling."""

    if not np.isfinite(up_limit) or up_limit >= NO_UP_LIMIT:
        return False
    return bool(raw_open >= up_limit - PRICE_TOLERANCE)


def blocks_sell(raw_open: float, down_limit: float) -> bool:
    """Is a sell at this raw open contradicted by the stored floor."""

    if not np.isfinite(down_limit) or down_limit <= NO_DOWN_LIMIT:
        return False
    return bool(raw_open <= down_limit + PRICE_TOLERANCE)
