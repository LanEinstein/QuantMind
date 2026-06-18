"""Tests for the point-in-time SW industry lookup (R2-2 / S2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.industry_pit import IndustryPIT
from scripts.factor_research.ingest_round2_data import EP_INDEX_MEMBER

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
ASOF = "20260618"


def _put_member(store: SnapshotStore, frame: pd.DataFrame) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=EP_INDEX_MEMBER,
        params={"asof": ASOF},
        trade_date=ASOF,
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


def _member_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    cols = ["ts_code", "l1_code", "l1_name", "in_date", "out_date"]
    return pd.DataFrame(rows, columns=cols)


def test_l1_asof_current_member_open_out_date(store: SnapshotStore) -> None:
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "000002.SZ",
                    "l1_code": "801180.SI",
                    "l1_name": "房地产",
                    "in_date": "19910129",
                    "out_date": "",  # current member
                }
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    assert ind.l1_asof("000002.SZ", "20240101") == "801180.SI"
    # also dtype-safe: in_date kept as a literal string, not floatified.
    assert ind.l1_asof("000002.SZ", "19910129") == "801180.SI"  # on the in_date


def test_l1_asof_respects_in_and_out_window(store: SnapshotStore) -> None:
    # Code moved industry: 800 until 2020-06-30, then 080 from 2020-07-01.
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "600601.SH",
                    "l1_code": "801080.SI",
                    "l1_name": "电子",
                    "in_date": "20200701",
                    "out_date": "",
                },
                {
                    "ts_code": "600601.SH",
                    "l1_code": "801880.SI",
                    "l1_name": "汽车",
                    "in_date": "20100101",
                    "out_date": "20200701",
                },
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    assert ind.l1_asof("600601.SH", "20150101") == "801880.SI"
    assert ind.l1_asof("600601.SH", "20200630") == "801880.SI"
    # out_date itself is exclusive (the day it left) → next segment.
    assert ind.l1_asof("600601.SH", "20200701") == "801080.SI"
    assert ind.l1_asof("600601.SH", "20240101") == "801080.SI"


def test_l1_asof_before_in_date_is_none(store: SnapshotStore) -> None:
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "688001.SH",
                    "l1_code": "801080.SI",
                    "l1_name": "电子",
                    "in_date": "20200701",
                    "out_date": "",
                }
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    assert ind.l1_asof("688001.SH", "20200101") is None  # not yet a member


def test_l1_asof_unknown_code_is_none(store: SnapshotStore) -> None:
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "000002.SZ",
                    "l1_code": "801180.SI",
                    "l1_name": "房地产",
                    "in_date": "19910129",
                    "out_date": "",
                }
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    assert ind.l1_asof("999999.SZ", "20240101") is None


def test_build_skips_rows_missing_in_date_or_l1(store: SnapshotStore) -> None:
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "000002.SZ",
                    "l1_code": "",  # no industry → unusable
                    "l1_name": "",
                    "in_date": "19910129",
                    "out_date": "",
                },
                {
                    "ts_code": "000333.SZ",
                    "l1_code": "801110.SI",
                    "l1_name": "家用电器",
                    "in_date": "",  # no in_date → unusable
                    "out_date": "",
                },
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    assert ind.l1_asof("000002.SZ", "20240101") is None
    assert ind.l1_asof("000333.SZ", "20240101") is None


def test_coverage_fraction(store: SnapshotStore) -> None:
    _put_member(
        store,
        _member_frame(
            [
                {
                    "ts_code": "000002.SZ",
                    "l1_code": "801180.SI",
                    "l1_name": "房地产",
                    "in_date": "19910129",
                    "out_date": "",
                }
            ]
        ),
    )
    ind = IndustryPIT.build(store, ASOF)
    cov = ind.coverage(["000002.SZ", "999999.SZ"], "20240101")
    assert cov == pytest.approx(0.5)


def test_build_missing_snapshot_raises(store: SnapshotStore) -> None:
    with pytest.raises(FileNotFoundError):
        IndustryPIT.build(store, ASOF)
