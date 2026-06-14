"""Tests for AE-001 as-of forward-adjusted close reconstruction.

Demonstrates the R0 §3 red line: we store the *raw un-adjusted* close + an
independent adj_factor pin, and reconstruct the qfq view as-of a date
bit-exactly (Decimal). A split *after* the reference date must not leak
backwards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from backend.data.historical_ingest.adjust_view import reconstruct_adjusted_close
from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _put(
    store: SnapshotStore, endpoint: str, trade_date: str, df: pd.DataFrame
) -> None:
    store.put(
        MarketDataSnapshot.create(
            vendor=VENDOR,
            endpoint=endpoint,
            params={"trade_date": trade_date},
            trade_date=trade_date,
            raw_payload=canonical_csv_bytes(df),
            encoding="csv",
            compression="none",
            fetch_time_utc=_NOW,
            metadata={"rows": len(df)},
        )
    )


def _seed_split(store: SnapshotStore) -> list[str]:
    """Two days; a 2:1 forward-adjust factor change between them."""
    ts = "600519.SH"
    # Day 1: raw close 100, factor 1.0
    _put(store, "daily", "20180102", pd.DataFrame({"ts_code": [ts], "close": [100.0]}))
    _put(
        store,
        "adj_factor",
        "20180102",
        pd.DataFrame({"ts_code": [ts], "adj_factor": [1.0]}),
    )
    # Day 2: raw close 60, factor 2.0 (e.g. post-dividend re-scaling)
    _put(store, "daily", "20180103", pd.DataFrame({"ts_code": [ts], "close": [60.0]}))
    _put(
        store,
        "adj_factor",
        "20180103",
        pd.DataFrame({"ts_code": [ts], "adj_factor": [2.0]}),
    )
    return ["20180102", "20180103"]


def test_asof_uses_only_factors_known_by_that_date(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "snap")
    days = _seed_split(store)

    # As of day 1 only day-1 factor is known → close is just the raw close.
    asof1 = reconstruct_adjusted_close(store, "600519.SH", days, asof_date="20180102")
    assert asof1 == {"20180102": Decimal("100.0") * Decimal("1.0") / Decimal("1.0")}

    # As of day 2 the reference factor is 2.0 → day-1 close is back-adjusted.
    asof2 = reconstruct_adjusted_close(store, "600519.SH", days, asof_date="20180103")
    assert asof2["20180102"] == Decimal("100.0") * Decimal("1.0") / Decimal("2.0")
    assert asof2["20180103"] == Decimal("60.0") * Decimal("2.0") / Decimal("2.0")


def test_reconstruction_is_bit_exact_repeatable(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "snap")
    days = _seed_split(store)
    a = reconstruct_adjusted_close(store, "600519.SH", days, asof_date="20180103")
    b = reconstruct_adjusted_close(store, "600519.SH", days, asof_date="20180103")
    assert a == b  # exact Decimal equality, no float drift


def test_raw_close_never_adjusted_in_storage(tmp_path) -> None:
    """The stored daily snapshot holds the RAW close, not an adjusted one."""
    from backend.data.historical_ingest.serialization import parse_csv_bytes

    store = SnapshotStore(tmp_path / "snap")
    _seed_split(store)
    snap = store.latest(vendor=VENDOR, endpoint="daily", trade_date="20180102")
    assert snap is not None
    df = parse_csv_bytes(snap.raw_payload)
    assert df.iloc[0]["close"] == 100.0  # un-adjusted, verbatim


def test_missing_code_returns_empty(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "snap")
    days = _seed_split(store)
    result = reconstruct_adjusted_close(
        store, "999999.SZ", days, asof_date="20180103"
    )
    assert result == {}
