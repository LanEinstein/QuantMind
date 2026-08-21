"""Load PIT daily prices joined to the adjustment factor of the same date.

Kept apart from the audit itself so the join, its coverage counters, and the
factor-change mask can be tested without loading a study.
"""

from __future__ import annotations

import gc
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.market import load_trade_dates

VENDOR = "tushare"
EXCHANGE_SUFFIXES = (".SH", ".SZ", ".BJ")
DAILY_COLUMNS = ["trade_date", "ts_code", "open", "close", "pct_chg"]


@dataclass(frozen=True)
class PricedSeries:
    """Raw daily prices for one security plus its stored adjustment factor."""

    code: str
    dates: np.ndarray
    opens: np.ndarray
    closes: np.ndarray
    pct_chg: np.ndarray
    adj: np.ndarray

    @property
    def adjusted_closes(self) -> np.ndarray:
        """Back-adjusted closes (close x factor), the causal adjustment form."""

        return self.closes * self.adj


def _read_daily(store: SnapshotStore, trade_date: str) -> pd.DataFrame | None:
    snapshot = store.latest(vendor=VENDOR, endpoint="daily", trade_date=trade_date)
    if snapshot is None:
        return None
    frame = pd.read_csv(
        io.BytesIO(snapshot.raw_payload),
        usecols=DAILY_COLUMNS,
        dtype={"trade_date": "int32", "ts_code": "string"},
    )
    return frame[frame["ts_code"].str.endswith(EXCHANGE_SUFFIXES, na=False)]


def _read_factor(store: SnapshotStore, trade_date: str) -> pd.DataFrame | None:
    snapshot = store.latest(
        vendor=VENDOR, endpoint="adj_factor", trade_date=trade_date
    )
    if snapshot is None:
        return None
    return pd.read_csv(
        io.BytesIO(snapshot.raw_payload),
        usecols=["trade_date", "ts_code", "adj_factor"],
        dtype={"trade_date": "int32", "ts_code": "string"},
    )


def load_priced_panel(
    pit_root: Path, *, start_date: str, end_date: str
) -> tuple[tuple[PricedSeries, ...], dict[str, object]]:
    """Join daily prices to the factor stored under the same trade date.

    The join runs one trade date at a time so a missing factor row is counted
    where it happens instead of vanishing into a whole-panel merge.
    """

    calendar = load_trade_dates(pit_root)
    dates = tuple(day for day in calendar if start_date <= day <= end_date)
    store = SnapshotStore(pit_root)
    frames: list[pd.DataFrame] = []
    daily_rows = 0
    daily_without_factor = 0
    factor_without_daily = 0
    dates_missing_factor: list[str] = []
    for trade_date in dates:
        daily = _read_daily(store, trade_date)
        if daily is None:
            continue
        factor = _read_factor(store, trade_date)
        daily_rows += len(daily)
        if factor is None:
            dates_missing_factor.append(trade_date)
            daily_without_factor += len(daily)
            continue
        merged = daily.merge(factor, on=["trade_date", "ts_code"], how="outer")
        daily_without_factor += int(merged["adj_factor"].isna().sum())
        factor_without_daily += int(merged["close"].isna().sum())
        frames.append(merged[merged["close"].notna() & merged["adj_factor"].notna()])
    if not frames:
        raise ValueError("no daily snapshots in requested range")
    panel = pd.concat(frames, ignore_index=True)
    del frames
    series = _split_by_code(panel)
    coverage = {
        "requested_date_count": len(dates),
        "dates_missing_factor_snapshot": dates_missing_factor,
        "daily_rows": daily_rows,
        "daily_rows_without_factor": daily_without_factor,
        "factor_rows_without_daily": factor_without_daily,
        "joined_rows": int(len(panel)),
        "security_count_with_30_rows": len(series),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }
    del panel
    gc.collect()
    return series, coverage


def _split_by_code(panel: pd.DataFrame) -> tuple[PricedSeries, ...]:
    """Turn the joined panel into per-security chronological arrays."""

    for column in ("open", "close", "pct_chg", "adj_factor"):
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    panel.sort_values(["ts_code", "trade_date"], kind="mergesort", inplace=True)
    out: list[PricedSeries] = []
    for code, group in panel.groupby("ts_code", sort=False, observed=True):
        if len(group) < 30:
            continue
        out.append(
            PricedSeries(
                code=str(code),
                dates=group["trade_date"].to_numpy(dtype=np.int32, copy=True),
                opens=group["open"].to_numpy(dtype=float, copy=True),
                closes=group["close"].to_numpy(dtype=float, copy=True),
                pct_chg=group["pct_chg"].to_numpy(dtype=float, copy=True),
                adj=group["adj_factor"].to_numpy(dtype=float, copy=True),
            )
        )
    return tuple(out)


def factor_change_mask(series: PricedSeries) -> np.ndarray:
    """Mark bars whose stored factor differs from the previous bar's factor."""

    changed = np.zeros(len(series.adj), dtype=bool)
    if len(series.adj) < 2:
        return changed
    previous, current = series.adj[:-1], series.adj[1:]
    usable = np.isfinite(previous) & np.isfinite(current) & (previous > 0)
    ratio = current / np.where(usable, previous, 1.0)
    changed[1:] = usable & (np.abs(ratio - 1.0) > 1e-9)
    return changed
