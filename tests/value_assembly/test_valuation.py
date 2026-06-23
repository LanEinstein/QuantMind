"""AF-002 valuation (cheapness) factor — cross-sectional, PIT, fail-closed."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.value_assembly.valuation import valuation_scores

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _put_daily_basic(
    store: SnapshotStore, trade_date: str, frame: pd.DataFrame
) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint="daily_basic",
        params={"trade_date": trade_date},
        trade_date=trade_date,
        raw_payload=canonical_csv_bytes(frame),
        encoding="csv",
        compression="none",
        fetch_time_utc=FIXED_NOW,
        metadata={"rows": int(len(frame))},
    )
    store.put(snap)


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(str(tmp_path))


def test_cheap_outranks_expensive(store: SnapshotStore) -> None:
    _put_daily_basic(
        store,
        "20240102",
        pd.DataFrame(
            {
                "ts_code": ["CHEAP.SH", "MID.SH", "RICH.SH"],
                "dv_ratio": ["6.0", "3.0", "0.0"],  # high yield good
                "pe_ttm": ["5.0", "20.0", "120.0"],  # low PE good
                "pb": ["0.6", "2.0", "9.0"],  # low PB good
            }
        ),
    )
    out = valuation_scores(
        store, codes=["CHEAP.SH", "MID.SH", "RICH.SH"], decision_date="20240102"
    )
    assert out["CHEAP.SH"] is not None and out["RICH.SH"] is not None
    assert out["CHEAP.SH"] > out["MID.SH"] > out["RICH.SH"]


def test_missing_code_is_none(store: SnapshotStore) -> None:
    _put_daily_basic(
        store,
        "20240102",
        pd.DataFrame(
            {"ts_code": ["A.SH"], "dv_ratio": ["3.0"], "pe_ttm": ["10"], "pb": ["1"]}
        ),
    )
    out = valuation_scores(store, codes=["A.SH", "GONE.SH"], decision_date="20240102")
    assert out["GONE.SH"] is None
    assert out["A.SH"] is not None


def test_non_positive_pe_pb_dropped(store: SnapshotStore) -> None:
    # A loss-making name (pe_ttm blank, pb negative) must not pose as 'cheap';
    # only its dividend dimension survives.
    _put_daily_basic(
        store,
        "20240102",
        pd.DataFrame(
            {
                "ts_code": ["LOSS.SH", "OK.SH"],
                "dv_ratio": ["1.0", "1.0"],
                "pe_ttm": ["", "10.0"],
                "pb": ["-2.0", "1.0"],
            }
        ),
    )
    out = valuation_scores(store, codes=["LOSS.SH", "OK.SH"], decision_date="20240102")
    # LOSS.SH still scores (dividend only), never None, never inflated by a
    # negative PB masquerading as cheap.
    assert out["LOSS.SH"] is not None
    assert 0.0 <= out["LOSS.SH"] <= 1.0


def test_no_snapshot_all_none(store: SnapshotStore) -> None:
    out = valuation_scores(store, codes=["A.SH"], decision_date="20240102")
    assert out == {"A.SH": None}


def test_deterministic_replay(store: SnapshotStore) -> None:
    _put_daily_basic(
        store,
        "20240102",
        pd.DataFrame(
            {
                "ts_code": ["A.SH", "B.SH"],
                "dv_ratio": ["2.0", "4.0"],
                "pe_ttm": ["15", "8"],
                "pb": ["3", "1"],
            }
        ),
    )
    a = valuation_scores(store, codes=["A.SH", "B.SH"], decision_date="20240102")
    b = valuation_scores(store, codes=["A.SH", "B.SH"], decision_date="20240102")
    assert a == b
