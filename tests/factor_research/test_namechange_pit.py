"""Tests for the PIT share-name / ST-flag reader (R3-2).

Cover the name-window reconstruction across year pages, the point-in-time name
selection, the ST/退 marker detection, and the fail-open-to-included default —
against a real tmp ``SnapshotStore``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.ingest_round2_data import EP_NAMECHANGE, NAMECHANGE_FIELDS
from scripts.factor_research.namechange_pit import (
    NameChangePIT,
    _is_st_name,
    namechange_snapshot_keys,
)

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _put_namechange(store: SnapshotStore, key: str, frame: pd.DataFrame) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=EP_NAMECHANGE,
        params={"start_date": f"{key[:4]}0101", "end_date": key},
        trade_date=key,
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


@pytest.mark.parametrize(
    "name,expected",
    [
        ("贵州茅台", False),
        ("ST康美", True),
        ("*ST华业", True),
        ("SST前锋", True),
        ("S*ST佳通", True),
        ("退市厦华", True),
        ("鹏起退", True),
        ("", False),
    ],
)
def test_is_st_name(name: str, expected: bool) -> None:
    assert _is_st_name(name) is expected


def test_name_asof_picks_window_in_effect(store: SnapshotStore) -> None:
    # 600519 was renamed *ST in 2018-01, then back to normal in 2019-05.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "name": ["*ST茅台", "贵州茅台"],
            "start_date": ["20180115", "20190520"],
            "end_date": ["20190520", ""],
            "change_reason": ["风险警示", "撤销警示"],
        }
    )
    _put_namechange(store, "20181231", frame)
    pit = NameChangePIT.build(store, ["20181231"])
    # During the ST window
    assert pit.name_asof("600519.SH", "20180601") == "*ST茅台"
    assert pit.is_st_asof("600519.SH", "20180601") is True
    # After the ST window
    assert pit.name_asof("600519.SH", "20200101") == "贵州茅台"
    assert pit.is_st_asof("600519.SH", "20200101") is False
    # Before any window → unknown → not ST (original name assumed)
    assert pit.name_asof("600519.SH", "20170101") is None
    assert pit.is_st_asof("600519.SH", "20170101") is False


def test_unknown_code_not_st(store: SnapshotStore) -> None:
    _put_namechange(
        store,
        "20181231",
        pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "name": ["*ST茅台"],
                "start_date": ["20180115"],
                "end_date": [""],
                "change_reason": ["风险警示"],
            }
        ),
    )
    pit = NameChangePIT.build(store, ["20181231"])
    # A code never renamed has no row → treated as non-ST (fail-open to included).
    assert pit.is_st_asof("000001.SZ", "20200101") is False


def test_empty_year_page_contributes_no_rows(store: SnapshotStore) -> None:
    # An empty (headered) namechange page must parse and add nothing.
    empty = pd.DataFrame(columns=list(NAMECHANGE_FIELDS))
    _put_namechange(store, "20171231", empty)
    _put_namechange(
        store,
        "20181231",
        pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "name": ["*ST茅台"],
                "start_date": ["20180115"],
                "end_date": [""],
                "change_reason": ["风险警示"],
            }
        ),
    )
    pit = NameChangePIT.build(store, ["20171231", "20181231"])  # must not raise
    assert pit.is_st_asof("600519.SH", "20180601") is True


def test_windows_deduped_across_year_pages(store: SnapshotStore) -> None:
    # The same window can appear under two year pages (open end_date) → dedup.
    row = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "name": ["*ST茅台"],
            "start_date": ["20180115"],
            "end_date": [""],
            "change_reason": ["风险警示"],
        }
    )
    _put_namechange(store, "20181231", row)
    _put_namechange(store, "20191231", row)
    pit = NameChangePIT.build(store, ["20181231", "20191231"])
    assert len(pit.by_code["600519.SH"]) == 1  # deduped


def test_namechange_snapshot_keys(store: SnapshotStore, tmp_path: Path) -> None:
    _put_namechange(
        store,
        "20171231",
        pd.DataFrame(columns=list(NAMECHANGE_FIELDS)),
    )
    _put_namechange(
        store,
        "20181231",
        pd.DataFrame(columns=list(NAMECHANGE_FIELDS)),
    )
    assert namechange_snapshot_keys(str(tmp_path)) == ["20171231", "20181231"]


def test_namechange_snapshot_keys_empty_fails_closed(
    store: SnapshotStore, tmp_path: Path
) -> None:
    # codex P2: an index with NO namechange page must raise (never return []),
    # so build_r3_inputs cannot silently disable the PIT ST exclusion. The store
    # has an index (some other endpoint) but no namechange snapshot.
    from backend.marketdata_snapshot.snapshot import MarketDataSnapshot

    store.put(
        MarketDataSnapshot.create(
            vendor="tushare",
            endpoint="daily",
            params={"trade_date": "20240101"},
            trade_date="20240101",
            raw_payload=b"ts_code\n600519.SH\n",
            encoding="csv",
            compression="none",
            fetch_time_utc=FIXED_NOW,
            metadata={"rows": 1},
        )
    )
    with pytest.raises(FileNotFoundError, match="namechange"):
        namechange_snapshot_keys(str(tmp_path))
