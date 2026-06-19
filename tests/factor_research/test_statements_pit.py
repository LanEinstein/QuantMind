"""Tests for the round-3 PIT financial-statement reader (R3-2).

Cover the dtype-safe period read, the report_type='1' consolidated filter, the
as-known vintage selection (ann_date PIT + restatement), the per-period
``as_known`` series, and the fail-closed paths — against a real tmp
``SnapshotStore`` so the byte round-trip (and the float-date trap) is exercised.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.ingest_round2_data import (
    EP_BALANCESHEET,
    EP_FINA,
    EP_INCOME,
)
from scripts.factor_research.statements_pit import (
    PeriodStatementPIT,
    read_statement_period,
    statement_vintage_audit,
)

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _put(store: SnapshotStore, endpoint: str, period: str, frame: pd.DataFrame) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=endpoint,
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


def test_read_statement_period_keeps_dates_str(store: SnapshotStore) -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "ann_date": ["20240330"],
            "report_type": ["1"],
            "update_flag": ["1"],
            "total_assets": [2.5e11],
        }
    )
    _put(store, EP_BALANCESHEET, "20231231", frame)
    out = read_statement_period(store, EP_BALANCESHEET, "20231231", ["total_assets"])
    assert out.loc[0, "ann_date"] == "20240330"  # literal, not 20240330.0
    assert out.loc[0, "end_date"] == "20231231"
    assert out.loc[0, "total_assets"] == pytest.approx(2.5e11)


def test_read_statement_period_missing_raises(store: SnapshotStore) -> None:
    with pytest.raises(FileNotFoundError):
        read_statement_period(store, EP_INCOME, "20231231", ["n_income"])


def test_report_type_filter_keeps_only_consolidated(store: SnapshotStore) -> None:
    # Two rows for the same (code, period): consolidated YTD (1) and
    # single-quarter (2). Only the consolidated row must survive.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "end_date": ["20230630", "20230630"],
            "ann_date": ["20230815", "20230815"],
            "report_type": ["1", "2"],
            "update_flag": ["1", "1"],
            "n_income": [3.0e10, 1.5e10],  # YTD vs single-quarter
        }
    )
    _put(store, EP_INCOME, "20230630", frame)
    pit = PeriodStatementPIT.build(
        store, ["20230630"], endpoint=EP_INCOME, fields=["n_income"]
    )
    rec = pit.asof("600519.SH", "20231231")
    assert rec is not None
    assert rec.get("n_income") == pytest.approx(3.0e10)  # the YTD (report_type=1)


def test_fina_endpoint_read_unfiltered_with_none_filter(
    store: SnapshotStore,
) -> None:
    # fina_indicator_vip has no report_type column → MUST be built with
    # report_type_filter=None (the real build_r3_inputs usage) so its rows survive.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "end_date": ["20230331"],
            "ann_date": ["20230428"],
            "update_flag": ["1"],
            "profit_dedt": [1.2e10],
        }
    )
    _put(store, EP_FINA, "20230331", frame)
    pit = PeriodStatementPIT.build(
        store,
        ["20230331"],
        endpoint=EP_FINA,
        fields=["profit_dedt"],
        report_type_filter=None,
    )
    rec = pit.asof("600519.SH", "20231231")
    assert rec is not None and rec.get("profit_dedt") == pytest.approx(1.2e10)


def test_blank_report_type_dropped_under_filter(store: SnapshotStore) -> None:
    # codex P2: with report_type_filter='1' (default), a blank/missing report_type
    # row must be DROPPED fail-closed, never selected as a vintage.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "end_date": ["20230630", "20230630"],
            "ann_date": ["20230815", "20230820"],
            "report_type": ["1", ""],  # consolidated + an unclassified row
            "update_flag": ["1", "1"],
            "n_income": [3.0e10, 9.9e10],
        }
    )
    _put(store, EP_INCOME, "20230630", frame)
    pit = PeriodStatementPIT.build(
        store, ["20230630"], endpoint=EP_INCOME, fields=["n_income"]
    )
    rec = pit.asof("600519.SH", "20231231")
    # The blank-report_type row (later ann_date, 9.9e10) is dropped → the
    # consolidated 3.0e10 is the as-known value.
    assert rec is not None and rec.get("n_income") == pytest.approx(3.0e10)


def test_asof_respects_ann_date_gate(store: SnapshotStore) -> None:
    # A period announced AFTER the decision date is not visible yet.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "end_date": ["20231231"],
            "ann_date": ["20240330"],
            "report_type": ["1"],
            "update_flag": ["1"],
            "total_assets": [9.9e11],
        }
    )
    _put(store, EP_BALANCESHEET, "20231231", frame)
    pit = PeriodStatementPIT.build(
        store, ["20231231"], endpoint=EP_BALANCESHEET, fields=["total_assets"]
    )
    # decision date BEFORE the announcement → not known
    assert pit.asof("600519.SH", "20240301") is None
    # decision date AFTER the announcement → known
    assert pit.asof("600519.SH", "20240401") is not None


def test_asof_picks_latest_vintage_before_cutoff(store: SnapshotStore) -> None:
    # Same (code, period) restated twice: first ann 20240330, restated 20240820.
    frame = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "end_date": ["20231231", "20231231"],
            "ann_date": ["20240330", "20240820"],
            "report_type": ["1", "1"],
            "update_flag": ["0", "1"],
            "total_assets": [1.0e11, 1.1e11],  # restated upward
        }
    )
    _put(store, EP_BALANCESHEET, "20231231", frame)
    pit = PeriodStatementPIT.build(
        store, ["20231231"], endpoint=EP_BALANCESHEET, fields=["total_assets"]
    )
    # Between the two announcements → the FIRST vintage is the as-known value.
    rec_mid = pit.asof("600519.SH", "20240601")
    assert rec_mid is not None and rec_mid.get("total_assets") == pytest.approx(1.0e11)
    # After the restatement → the restated value.
    rec_late = pit.asof("600519.SH", "20240901")
    assert rec_late is not None and rec_late.get("total_assets") == pytest.approx(
        1.1e11
    )


def test_as_known_returns_record_per_period(store: SnapshotStore) -> None:
    for period, ann, ta in (
        ("20221231", "20230330", 8.0e10),
        ("20231231", "20240330", 9.0e10),
    ):
        _put(
            store,
            EP_BALANCESHEET,
            period,
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "end_date": [period],
                    "ann_date": [ann],
                    "report_type": ["1"],
                    "update_flag": ["1"],
                    "total_assets": [ta],
                }
            ),
        )
    pit = PeriodStatementPIT.build(
        store,
        ["20221231", "20231231"],
        endpoint=EP_BALANCESHEET,
        fields=["total_assets"],
    )
    known = pit.as_known("600519.SH", "20241231")
    assert set(known) == {"20221231", "20231231"}
    assert known["20221231"].get("total_assets") == pytest.approx(8.0e10)
    assert known["20231231"].get("total_assets") == pytest.approx(9.0e10)
    # asof returns the latest period.
    assert pit.asof("600519.SH", "20241231").end_date == "20231231"


def test_unknown_code_returns_empty(store: SnapshotStore) -> None:
    _put(
        store,
        EP_INCOME,
        "20231231",
        pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "end_date": ["20231231"],
                "ann_date": ["20240330"],
                "report_type": ["1"],
                "update_flag": ["1"],
                "n_income": [1.0e10],
            }
        ),
    )
    pit = PeriodStatementPIT.build(
        store, ["20231231"], endpoint=EP_INCOME, fields=["n_income"]
    )
    assert pit.as_known("000001.SZ", "20241231") == {}
    assert pit.asof("000001.SZ", "20241231") is None


def test_statement_vintage_audit_counts_restatements(store: SnapshotStore) -> None:
    # 600519 period 20231231 has TWO ann_dates (a restatement); 000001 has one.
    _put(
        store,
        EP_BALANCESHEET,
        "20231231",
        pd.DataFrame(
            {
                "ts_code": ["600519.SH", "600519.SH", "000001.SZ"],
                "end_date": ["20231231", "20231231", "20231231"],
                "ann_date": ["20240330", "20240820", "20240328"],
                "report_type": ["1", "1", "1"],
                "update_flag": ["0", "1", "1"],
                "total_assets": [1.0e11, 1.1e11, 4.0e12],
            }
        ),
    )
    pit = PeriodStatementPIT.build(
        store, ["20231231"], endpoint=EP_BALANCESHEET, fields=["total_assets"]
    )
    audit = statement_vintage_audit(pit)
    assert audit.n_codes == 2
    assert audit.n_code_periods == 2  # one (code, end_date) each
    assert audit.n_restated_code_periods == 1  # only 600519 restated
    assert audit.restatement_rate == pytest.approx(0.5)
    assert audit.ann_lag_days_median is not None
