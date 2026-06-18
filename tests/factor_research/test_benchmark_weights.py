"""Tests for the PIT CSI300 constituent-weight reader (R2-3 / T1)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.benchmark_weights import BenchmarkWeightsPIT
from scripts.factor_research.ingest_round2_data import EP_INDEX_WEIGHT

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _row(con: str, pub: str, w: object) -> dict[str, object]:
    return {"con_code": con, "index_code": "000300.SH", "trade_date": pub, "weight": w}


def _put_weight(
    store: SnapshotStore, month_key: str, rows: list[dict[str, object]]
) -> None:
    cols = ["con_code", "index_code", "trade_date", "weight"]
    frame = pd.DataFrame(rows, columns=cols)
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=EP_INDEX_WEIGHT,
        params={"index_code": "000300.SH"},
        trade_date=month_key,
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


def _seed(store: SnapshotStore) -> None:
    # One monthly snapshot with TWO publish dates (the real multi-publish shape).
    _put_weight(
        store,
        "20240131",
        [
            _row("600519.SH", "20240102", 6.0),
            _row("601318.SH", "20240102", 4.0),
            _row("600519.SH", "20240131", 7.0),
            _row("601318.SH", "20240131", 3.0),
        ],
    )
    _put_weight(
        store,
        "20240229",
        [
            _row("600519.SH", "20240229", 8.0),
            _row("601318.SH", "20240229", 2.0),
        ],
    )


def test_asof_picks_latest_publish_and_normalizes(store: SnapshotStore) -> None:
    _seed(store)
    pit = BenchmarkWeightsPIT.build(store, ("20240131", "20240229"))
    # d=20240215: latest publish < d is 20240131 (weights 7/3 → 0.7/0.3).
    w = pit.asof("20240215")
    assert w == pytest.approx({"600519.SH": 0.7, "601318.SH": 0.3})
    assert sum(w.values()) == pytest.approx(1.0)


def test_asof_uses_only_publish_strictly_before_d(store: SnapshotStore) -> None:
    _seed(store)
    pit = BenchmarkWeightsPIT.build(store, ("20240131", "20240229"))
    # d=20240131 itself → the 20240131 publish is NOT yet usable (strict-before);
    # falls back to 20240102 (6/4 → 0.6/0.4).
    w = pit.asof("20240131")
    assert w == pytest.approx({"600519.SH": 0.6, "601318.SH": 0.4})
    # d after the Feb publish → 8/2 → 0.8/0.2
    w2 = pit.asof("20240301")
    assert w2 == pytest.approx({"600519.SH": 0.8, "601318.SH": 0.2})


def test_asof_before_any_publish_is_empty(store: SnapshotStore) -> None:
    _seed(store)
    pit = BenchmarkWeightsPIT.build(store, ("20240131", "20240229"))
    assert pit.asof("20231231") == {}


def test_publish_dates_sorted(store: SnapshotStore) -> None:
    _seed(store)
    pit = BenchmarkWeightsPIT.build(store, ("20240131", "20240229"))
    assert pit.publish_dates == ("20240102", "20240131", "20240229")


def test_build_skips_zero_or_nan_weight_rows(store: SnapshotStore) -> None:
    _put_weight(
        store,
        "20240131",
        [
            _row("600519.SH", "20240131", 9.0),
            _row("000001.SZ", "20240131", 1.0),
            _row("BADROW.SZ", "20240131", ""),
        ],
    )
    pit = BenchmarkWeightsPIT.build(store, ("20240131",))
    w = pit.asof("20240201")
    assert set(w) == {"600519.SH", "000001.SZ"}  # bad weight dropped
    assert sum(w.values()) == pytest.approx(1.0)


def test_build_drops_infinite_weight(store: SnapshotStore) -> None:
    # codex P3: an inf weight must be dropped (not let through by a NaN-only check).
    _put_weight(
        store,
        "20240131",
        [
            _row("600519.SH", "20240131", 9.0),
            _row("000001.SZ", "20240131", 1.0),
            _row("INFROW.SZ", "20240131", "inf"),
        ],
    )
    pit = BenchmarkWeightsPIT.build(store, ("20240131",))
    w = pit.asof("20240201")
    assert set(w) == {"600519.SH", "000001.SZ"}  # inf dropped, not normalized in
    assert sum(w.values()) == pytest.approx(1.0)
