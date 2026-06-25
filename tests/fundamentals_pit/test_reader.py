"""AF-002 backend PIT statement reader → AF-003 quality records (deterministic)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.fundamentals_pit.reader import (
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_FINA,
    EP_INCOME,
    BackendStatementPIT,
    quality_metric_records,
    recent_quarter_ends,
)
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.quality_fundamentals.quality import QualityMetric, fundamentals_scores

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


class TestRecentQuarterEnds:
    def test_strictly_before_and_ordered(self) -> None:
        out = recent_quarter_ends("20240501", 4)
        assert out == ("20230630", "20230930", "20231231", "20240331")
        assert all(e < "20240501" for e in out)

    def test_exact_quarter_end_excluded(self) -> None:
        # 20240331 is not strictly before 20240331 → newest usable is 20231231.
        assert recent_quarter_ends("20240331", 1) == ("20231231",)

    def test_zero_periods_empty(self) -> None:
        assert recent_quarter_ends("20240501", 0) == ()

    def test_bad_date_rejected(self) -> None:
        with pytest.raises(ValueError):
            recent_quarter_ends("2024-05-01", 4)


class TestBackendStatementPIT:
    def test_announced_values_keeps_every_vintage(self, store: SnapshotStore) -> None:
        # Two periods, the first restated → three (ann_date, value) records.
        _put(
            store,
            EP_FINA,
            "20230331",
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH", "600519.SH"],
                    "end_date": ["20230331", "20230331"],
                    "ann_date": ["20230420", "20230610"],  # original + restatement
                    "update_flag": ["0", "1"],
                    "roe": [10.0, 10.5],
                    "grossprofit_margin": [90.0, 90.0],
                }
            ),
        )
        _put(
            store,
            EP_FINA,
            "20230630",
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "end_date": ["20230630"],
                    "ann_date": ["20230820"],
                    "update_flag": ["0"],
                    "roe": [20.0],
                    "grossprofit_margin": [91.0],
                }
            ),
        )
        pit = BackendStatementPIT.build(
            store,
            ["20230331", "20230630"],
            endpoint=EP_FINA,
            fields=["roe", "grossprofit_margin"],
            report_type_filter=None,
        )
        roe = pit.announced_values("600519.SH", "roe")
        assert sorted(roe) == [
            ("20230420", 10.0),
            ("20230610", 10.5),
            ("20230820", 20.0),
        ]

    def test_report_type_filter_drops_non_consolidated(
        self, store: SnapshotStore
    ) -> None:
        _put(
            store,
            EP_INCOME,
            "20230630",
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH", "600519.SH", "600519.SH"],
                    "end_date": ["20230630", "20230630", "20230630"],
                    "ann_date": ["20230815", "20230815", "20230815"],
                    "report_type": ["1", "2", ""],  # YTD, single-quarter, blank
                    "update_flag": ["0", "0", "0"],
                    "n_income": [3.0e10, 1.5e10, 9.9e9],
                }
            ),
        )
        pit = BackendStatementPIT.build(
            store, ["20230630"], endpoint=EP_INCOME, fields=["n_income"]
        )
        vals = pit.announced_values("600519.SH", "n_income")
        assert vals == [("20230815", 3.0e10)]  # only report_type == '1'

    def test_missing_snapshot_is_skipped(self, store: SnapshotStore) -> None:
        pit = BackendStatementPIT.build(
            store,
            ["20230331"],
            endpoint=EP_FINA,
            fields=["roe"],
            report_type_filter=None,
        )
        assert pit.by_code == {}


class TestQualityMetricRecords:
    def _seed_full(self, store: SnapshotStore) -> None:
        _put(
            store,
            EP_FINA,
            "20231231",
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "end_date": ["20231231", "20231231"],
                    "ann_date": ["20240328", "20240328"],
                    "update_flag": ["0", "0"],
                    "roe": [18.0, 6.0],
                    "grossprofit_margin": [40.0, 25.0],
                }
            ),
        )
        for ep, field, vals in (
            (EP_INCOME, "n_income", [1.0e9, 5.0e8]),
            (EP_CASHFLOW, "n_cashflow_act", [1.2e9, 2.0e8]),
            (EP_BALANCESHEET, "total_assets", [1.0e10, 1.0e10]),
        ):
            _put(
                store,
                ep,
                "20231231",
                pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ"],
                        "end_date": ["20231231", "20231231"],
                        "ann_date": ["20240328", "20240328"],
                        "report_type": ["1", "1"],
                        "update_flag": ["0", "0"],
                        field: vals,
                    }
                ),
            )

    def test_builds_roe_gpm_accruals(self, store: SnapshotStore) -> None:
        self._seed_full(store)
        recs = quality_metric_records(
            store, codes=["000001.SZ", "000002.SZ"], periods=["20231231"]
        )
        a = recs["000001.SZ"]
        assert a[QualityMetric.ROE] == [("20240328", 18.0)]
        assert a[QualityMetric.GPM] == [("20240328", 40.0)]
        # accrual = (1.0e9 - 1.2e9) / 1.0e10 = -0.02 ; cash-backed (negative).
        ann, val = a[QualityMetric.ACCRUALS][0]
        assert ann == "20240328"
        assert val == pytest.approx(-0.02)

    def test_gpm_reads_ratio_not_absolute_gross_profit(
        self, store: SnapshotStore
    ) -> None:
        """M1 regression: GPM must be the RATIO (grossprofit_margin), never the
        absolute gross profit in yuan (gross_margin) — ranking the cross-section
        by the absolute amount ranks by company SIZE (the QGR-rejected size
        tilt). Seed BOTH columns with the real-world scale gap and assert the
        reader picks the ratio.
        """
        _put(
            store,
            EP_FINA,
            "20231231",
            pd.DataFrame(
                {
                    "ts_code": ["002210.SZ"],
                    "end_date": ["20231231"],
                    "ann_date": ["20240328"],
                    "update_flag": ["0"],
                    "roe": [12.0],
                    # gross_margin = absolute 毛利 in yuan (size); grossprofit_margin
                    # = the ratio. The reader must return the ratio.
                    "gross_margin": [43_243_968.86],
                    "grossprofit_margin": [0.6119],
                }
            ),
        )
        recs = quality_metric_records(
            store, codes=["002210.SZ"], periods=["20231231"]
        )
        gpm = recs["002210.SZ"][QualityMetric.GPM]
        assert gpm == [("20240328", 0.6119)]
        # Guard against the size-tilt value sneaking back in.
        assert all(abs(v) < 1.0e4 for _ann, v in gpm)

    def test_feeds_af003_pit_gated(self, store: SnapshotStore) -> None:
        self._seed_full(store)
        recs = quality_metric_records(
            store, codes=["000001.SZ", "000002.SZ"], periods=["20231231"]
        )
        # Decision BEFORE the 2024-03-28 announcement → nothing known → all None.
        early = fundamentals_scores(recs, "20240101")
        assert early.get("000001.SZ") is None
        # Decision after → 000001 (high ROE/GPM, low accruals) ranks above 000002.
        late = fundamentals_scores(recs, "20240601")
        assert late["000001.SZ"] is not None
        assert late["000001.SZ"] > late["000002.SZ"]

    def test_no_data_code_absent(self, store: SnapshotStore) -> None:
        self._seed_full(store)
        recs = quality_metric_records(store, codes=["999999.SZ"], periods=["20231231"])
        assert "999999.SZ" not in recs
