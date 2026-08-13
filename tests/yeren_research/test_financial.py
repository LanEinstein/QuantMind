from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.financial import read_financial_records


def _put(store: SnapshotStore, period: str, rows: bytes) -> None:
    store.put(
        MarketDataSnapshot.create(
            vendor="tushare",
            endpoint="fina_indicator_vip",
            params={"period": period},
            trade_date=period,
            raw_payload=rows,
            encoding="csv",
            compression="none",
            fetch_time_utc=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"rows": 1},
        )
    )


def _put_daily(store: SnapshotStore, trade_date: str) -> None:
    store.put(
        MarketDataSnapshot.create(
            vendor="tushare",
            endpoint="daily",
            params={"trade_date": trade_date},
            trade_date=trade_date,
            raw_payload=b"ts_code,trade_date,close\n000001.SZ,20240506,10\n",
            encoding="csv",
            compression="none",
            fetch_time_utc=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"rows": 1},
        )
    )


def test_financial_records_exclude_same_day_and_keep_snapshot_provenance(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(tmp_path)
    header = (
        b"ts_code,ann_date,end_date,update_flag,roe,grossprofit_margin,"
        b"netprofit_yoy,or_yoy\n"
    )
    _put(
        store,
        "20240331",
        header + b"000001.SZ,20240430,20240331,1,10,20,30,40\n",
    )
    _put(
        store,
        "20240630",
        header + b"000001.SZ,20240801,20240630,1,11,21,31,41\n",
    )
    _put_daily(store, "20240506")

    records = read_financial_records(
        pit_root=tmp_path,
        codes=("000001.SZ",),
        decision_cutoff=datetime.fromisoformat("2024-08-01T23:00:00+08:00"),
    )

    assert len(records) == 1
    assert records[0].data["end_date"] == "20240331"
    assert records[0].data["values"]["roe"] == 10.0
    assert records[0].source_ref.startswith("data/marketdata_pit#")
    assert records[0].information_available_at == datetime.fromisoformat(
        "2024-05-06T09:30:00+08:00"
    )
