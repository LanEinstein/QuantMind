"""Tests for the point-in-time fundamentals reader (R2-2 / S1).

Cover the dtype-safe period read, the as-known vintage selection (ann_date PIT
+ restatement handling), the fail-closed paths, and the vintage audit — all
against a real tmp ``SnapshotStore`` so the byte round-trip (and the float-date
trap) is exercised end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.fundamentals_pit import (
    FUNDAMENTAL_FIELDS,
    FundamentalsPIT,
    read_fina_period,
    vintage_audit,
)
from scripts.factor_research.ingest_round2_data import EP_FINA

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _put_fina(store: SnapshotStore, period: str, frame: pd.DataFrame) -> None:
    """Persist one fina_indicator_vip period snapshot byte-exact."""
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=EP_FINA,
        params={"period": period},
        trade_date=period,
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


def test_read_fina_period_keeps_dates_as_str_not_float(store: SnapshotStore) -> None:
    # The all-numeric ann_date/end_date columns must NOT floatify on read-back.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "end_date": ["20240331"],
            "ann_date": ["20240430"],
            "update_flag": ["1"],
            "roe": [20.5],
            "grossprofit_margin": [90.1],
            "netprofit_yoy": [12.3],
            "or_yoy": [8.0],
        }
    )
    _put_fina(store, "20240331", frame)
    out = read_fina_period(store, "20240331")
    assert out.loc[0, "ann_date"] == "20240430"  # literal, not 20240430.0
    assert out.loc[0, "end_date"] == "20240331"
    assert out.loc[0, "ts_code"] == "600519.SH"
    # factor columns are numeric floats
    assert out.loc[0, "roe"] == pytest.approx(20.5)


def test_read_fina_period_missing_required_raises(store: SnapshotStore) -> None:
    with pytest.raises(FileNotFoundError):
        read_fina_period(store, "20240331")


def test_read_fina_period_empty_factor_becomes_nan(store: SnapshotStore) -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "end_date": ["20240331"],
            "ann_date": ["20240430"],
            "update_flag": ["1"],
            "roe": [""],  # missing earnings
            "grossprofit_margin": ["90.1"],
            "netprofit_yoy": ["12.3"],
            "or_yoy": ["8.0"],
        }
    )
    _put_fina(store, "20240331", frame)
    out = read_fina_period(store, "20240331")
    assert pd.isna(out.loc[0, "roe"])


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    cols = ["ts_code", "end_date", "ann_date", "update_flag", *FUNDAMENTAL_FIELDS]
    return pd.DataFrame(rows, columns=cols)


def test_asof_picks_latest_announced_report_period(store: SnapshotStore) -> None:
    # Two report periods, both announced before d=20240901 → pick the later end.
    _put_fina(
        store,
        "20231231",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240328",
                    "update_flag": "1",
                    "roe": 30.0,
                    "grossprofit_margin": 91.0,
                    "netprofit_yoy": 19.0,
                    "or_yoy": 18.0,
                }
            ]
        ),
    )
    _put_fina(
        store,
        "20240630",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20240630",
                    "ann_date": "20240810",
                    "update_flag": "1",
                    "roe": 17.0,
                    "grossprofit_margin": 90.0,
                    "netprofit_yoy": 12.0,
                    "or_yoy": 11.0,
                }
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20231231", "20240630"))
    rec = pit.asof("600519.SH", "20240901")
    assert rec is not None
    assert rec.end_date == "20240630"
    assert rec.get("roe") == pytest.approx(17.0)


def test_asof_excludes_reports_announced_on_or_after_d(store: SnapshotStore) -> None:
    # d=20240801: the 20240630 report (ann 20240810) is NOT yet known → use
    # 20231231 (ann 20240328). Strict-before also excludes a same-day ann.
    _put_fina(
        store,
        "20231231",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240328",
                    "update_flag": "1",
                    "roe": 30.0,
                    "grossprofit_margin": 91.0,
                    "netprofit_yoy": 19.0,
                    "or_yoy": 18.0,
                }
            ]
        ),
    )
    _put_fina(
        store,
        "20240630",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20240630",
                    "ann_date": "20240810",
                    "update_flag": "1",
                    "roe": 17.0,
                    "grossprofit_margin": 90.0,
                    "netprofit_yoy": 12.0,
                    "or_yoy": 11.0,
                }
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20231231", "20240630"))
    rec = pit.asof("600519.SH", "20240801")
    assert rec is not None
    assert rec.end_date == "20231231"
    assert rec.get("roe") == pytest.approx(30.0)
    # Exactly on the announcement day → still not usable (strict-before).
    rec_same = pit.asof("600519.SH", "20240328")
    assert rec_same is None


def test_asof_restatement_uses_latest_vintage_known_by_d(store: SnapshotStore) -> None:
    # Same end_date 20231231, original ann 20240328 (roe 30) then a RESTATEMENT
    # ann 20240920 (roe 25). For d=20240901 only the original is known; for
    # d=20241001 the restatement is known.
    _put_fina(
        store,
        "20231231",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240328",
                    "update_flag": "0",
                    "roe": 30.0,
                    "grossprofit_margin": 91.0,
                    "netprofit_yoy": 19.0,
                    "or_yoy": 18.0,
                },
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240920",
                    "update_flag": "1",
                    "roe": 25.0,
                    "grossprofit_margin": 89.0,
                    "netprofit_yoy": 14.0,
                    "or_yoy": 16.0,
                },
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20231231",))
    early = pit.asof("600519.SH", "20240901")
    assert early is not None and early.get("roe") == pytest.approx(30.0)
    late = pit.asof("600519.SH", "20241001")
    assert late is not None and late.get("roe") == pytest.approx(25.0)


def test_build_drops_rows_with_missing_ann_date(store: SnapshotStore) -> None:
    _put_fina(
        store,
        "20240331",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20240331",
                    "ann_date": "",  # unusable → dropped fail-closed
                    "update_flag": "1",
                    "roe": 20.0,
                    "grossprofit_margin": 90.0,
                    "netprofit_yoy": 10.0,
                    "or_yoy": 9.0,
                }
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20240331",))
    assert pit.asof("600519.SH", "20250101") is None


def test_asof_unknown_code_returns_none(store: SnapshotStore) -> None:
    _put_fina(
        store,
        "20240331",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20240331",
                    "ann_date": "20240430",
                    "update_flag": "1",
                    "roe": 20.0,
                    "grossprofit_margin": 90.0,
                    "netprofit_yoy": 10.0,
                    "or_yoy": 9.0,
                }
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20240331",))
    assert pit.asof("000001.SZ", "20250101") is None


def test_extra_lag_days_delays_availability(store: SnapshotStore) -> None:
    _put_fina(
        store,
        "20240331",
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20240331",
                    "ann_date": "20240430",
                    "update_flag": "1",
                    "roe": 20.0,
                    "grossprofit_margin": 90.0,
                    "netprofit_yoy": 10.0,
                    "or_yoy": 9.0,
                }
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20240331",))
    # ann 20240430; with a 5-day extra lag it is usable only from 20240506.
    assert pit.asof("600519.SH", "20240502", extra_lag_days=5) is None
    assert pit.asof("600519.SH", "20240506", extra_lag_days=5) is not None


def test_vintage_audit_counts_restatements(store: SnapshotStore) -> None:
    _put_fina(
        store,
        "20231231",
        _frame(
            [
                # restated (two distinct ann_date for same code-period)
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240328",
                    "update_flag": "0",
                    "roe": 30.0,
                    "grossprofit_margin": 91.0,
                    "netprofit_yoy": 19.0,
                    "or_yoy": 18.0,
                },
                {
                    "ts_code": "600519.SH",
                    "end_date": "20231231",
                    "ann_date": "20240920",
                    "update_flag": "1",
                    "roe": 25.0,
                    "grossprofit_margin": 89.0,
                    "netprofit_yoy": 14.0,
                    "or_yoy": 16.0,
                },
                # NOT restated: same ann_date twice (update_flag 0/1 duplicate)
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240315",
                    "update_flag": "0",
                    "roe": 12.0,
                    "grossprofit_margin": 40.0,
                    "netprofit_yoy": 5.0,
                    "or_yoy": 4.0,
                },
                {
                    "ts_code": "000001.SZ",
                    "end_date": "20231231",
                    "ann_date": "20240315",
                    "update_flag": "1",
                    "roe": 12.0,
                    "grossprofit_margin": 40.0,
                    "netprofit_yoy": 5.0,
                    "or_yoy": 4.0,
                },
            ]
        ),
    )
    pit = FundamentalsPIT.build(store, ("20231231",))
    audit = vintage_audit(pit)
    assert audit.n_code_periods == 2
    assert audit.n_restated_code_periods == 1  # only 600519 is genuinely restated
    assert audit.restatement_rate == pytest.approx(0.5)
