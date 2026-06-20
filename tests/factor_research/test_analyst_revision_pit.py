"""Tests for the round-4 analyst-revision PIT aggregator (R4-3).

Cover the calendar FY anchor, the house-agnostic rating ordinal map (unknown →
None fail-closed), the row parse / report_type filter / report_date gate / dedup,
the seven factors' math (np_rev / eps_rev / rev_diff / rating_chg / tp_impl /
disp / cover_chg) on synthetic report slices, the PIT report_date<d gate, the
same-FY (year-roll) alignment, and the fail-closed thin/missing paths — plus an
end-to-end build over a real tmp ``SnapshotStore`` (the byte round-trip + the
date-floatify trap).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.analyst_revision_pit import (
    ANALYST_FACTOR_NAMES,
    EP_REPORT_RC,
    AnalystRevisionPIT,
    _parse_target_fy,
    _Report,
    analyst_factor_vector,
    rating_ordinal,
    read_report_rc_month,
    target_fy_asof,
)
from scripts.factor_research.factor_lib import R4_FACTOR_NAMES

FIXED_NOW = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)


def _r(
    date: str, org: str, *, npv=None, eps=None, rat=None, mp=None, fy=2024
) -> _Report:
    """A synthetic _Report (create_time = report_date for a stable tie-break)."""
    return _Report(date, f"{date} 00:00:00", org, fy, npv, eps, rat, mp)


# --- registry / calendar / rating map ---------------------------------------


def test_factor_names_match_registry() -> None:
    assert tuple(ANALYST_FACTOR_NAMES) == tuple(R4_FACTOR_NAMES)


def test_target_fy_calendar_anchor() -> None:
    # Jan-Mar → prior year is FY1; April onward → current year.
    assert target_fy_asof("20240131") == 2023
    assert target_fy_asof("20240331") == 2023
    assert target_fy_asof("20240401") == 2024
    assert target_fy_asof("20250430") == 2025
    assert target_fy_asof("20251215") == 2025


def test_rating_ordinal_tiers_and_failclosed() -> None:
    assert rating_ordinal("买入") == 5
    assert rating_ordinal("强烈推荐") == 5
    assert rating_ordinal("增持") == 4
    assert rating_ordinal("跑赢行业") == 4
    assert rating_ordinal("持有") == 3
    assert rating_ordinal("减持") == 2
    assert rating_ordinal("卖出") == 1
    # ASCII case-insensitive.
    assert rating_ordinal("buy") == 5
    assert rating_ordinal("OVERWEIGHT") == 4
    assert rating_ordinal("Hold") == 3
    # fail-closed: 无 / blank / unknown vocab → None (never a numeric default).
    assert rating_ordinal("无") is None
    assert rating_ordinal("") is None
    assert rating_ordinal("  ") is None
    assert rating_ordinal("强烈卖出加仓") is None
    assert rating_ordinal(None) is None


def test_parse_target_fy_q4_only() -> None:
    assert _parse_target_fy("2024Q4") == 2024
    assert _parse_target_fy("2025Q1") is None  # non-annual
    assert _parse_target_fy("Q") is None
    assert _parse_target_fy("") is None
    assert _parse_target_fy("2024q4") is None  # lower-case not accepted


# --- factor math (pure, on synthetic slices) --------------------------------


def test_np_and_eps_revision_ratio() -> None:
    now = [
        _r("20240320", "A", npv=110, eps=1.1),
        _r("20240321", "B", npv=112, eps=1.12),
        _r("20240322", "C", npv=108, eps=1.08),
    ]
    back = [
        _r("20231210", "A", npv=100, eps=1.0),
        _r("20231211", "B", npv=101, eps=1.01),
        _r("20231212", "C", npv=99, eps=0.99),
    ]
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    # median now 110, median back 100 → +0.10.
    assert v["np_rev"] == pytest.approx(0.10)
    assert v["eps_rev"] == pytest.approx(0.10)


def test_rev_diff_and_rating_chg_diffusion() -> None:
    now = [
        _r("20240320", "A", npv=110, rat=5),
        _r("20240321", "B", npv=112, rat=5),
        _r("20240322", "C", npv=90, rat=4),
    ]
    back = [
        _r("20231210", "A", npv=100, rat=4),
        _r("20231211", "B", npv=101, rat=4),
        _r("20231212", "C", npv=100, rat=4),
    ]
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    # np: A up, B up, C down → (2-1)/3.
    assert v["rev_diff"] == pytest.approx(1 / 3)
    # rating: A 4→5 up, B 4→5 up, C 4→4 flat → (2-0)/3.
    assert v["rating_chg"] == pytest.approx(2 / 3)


def test_diffusion_dispersion_need_three_brokers() -> None:
    now = [_r("20240320", "A", npv=110, eps=1.1), _r("20240321", "B", npv=112, eps=1.2)]
    back = [
        _r("20231210", "A", npv=100, eps=1.0),
        _r("20231211", "B", npv=101, eps=1.0),
    ]
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    assert v["rev_diff"] is None  # only 2 brokers in both → fail-closed
    assert v["disp"] is None  # only 2 eps in the level window
    # the median consensus (n>=1) still computes.
    assert v["np_rev"] is not None and v["eps_rev"] is not None


def test_tp_impl_uses_target_price_and_close() -> None:
    now = [
        _r("20240320", "A", mp=120.0),
        _r("20240321", "B", mp=100.0),
        _r("20240322", "C", mp=None),
    ]
    v = analyst_factor_vector(now, [], close=100.0, decision_date="20240401")
    # median(min_price over A,B) = 110 → 110/100 - 1 = 0.10.
    assert v["tp_impl"] == pytest.approx(0.10)
    # no close → None fail-closed.
    assert (
        analyst_factor_vector(now, [], close=None, decision_date="20240401")["tp_impl"]
        is None
    )
    assert (
        analyst_factor_vector(now, [], close=0.0, decision_date="20240401")["tp_impl"]
        is None
    )


def test_dispersion_value() -> None:
    now = [
        _r("20240320", "A", eps=1.0),
        _r("20240321", "B", eps=2.0),
        _r("20240322", "C", eps=3.0),
    ]
    v = analyst_factor_vector(now, [], close=10.0, decision_date="20240401")
    # mean 2.0, pstdev sqrt(2/3)=0.8165 → 0.4082.
    assert v["disp"] == pytest.approx(0.40825, abs=1e-4)


def test_cover_chg_log_ratio() -> None:
    now = [
        _r("20240320", "A", npv=1),
        _r("20240321", "B", npv=1),
        _r("20240322", "C", npv=1),
    ]
    back = [_r("20231210", "A", npv=1)]
    import math

    v = analyst_factor_vector(now, back, close=10.0, decision_date="20240401")
    assert v["cover_chg"] == pytest.approx(math.log(3 / 1))


def test_zero_back_consensus_failsclosed() -> None:
    now = [_r("20240320", "A", npv=110)]
    back = [_r("20231210", "A", npv=0)]  # median back == 0 → ratio undefined
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    assert v["np_rev"] is None


def test_same_fy_alignment_drops_other_year() -> None:
    # decision 20240401 → FY anchor 2024. A back report targets 2025 (wrong FY)
    # so the look-back consensus for 2024 is missing → np_rev fail-closed.
    now = [_r("20240320", "A", npv=110, fy=2024)]
    back = [_r("20231210", "A", npv=100, fy=2025)]
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    assert v["np_rev"] is None


def test_broker_latest_picks_latest_report() -> None:
    # Same broker A files twice in the now-window; the LATER report's value wins.
    now = [
        _r("20240301", "A", npv=100, fy=2024),
        _r("20240320", "A", npv=130, fy=2024),
        _r("20240321", "B", npv=130, fy=2024),
    ]
    back = [
        _r("20231210", "A", npv=100, fy=2024),
        _r("20231211", "B", npv=100, fy=2024),
    ]
    v = analyst_factor_vector(now, back, close=90.0, decision_date="20240401")
    # consensus now = median(130, 130) = 130; back = median(100,100)=100 → +0.30.
    assert v["np_rev"] == pytest.approx(0.30)


def test_uncovered_returns_all_none() -> None:
    v = analyst_factor_vector([], [], close=90.0, decision_date="20240401")
    assert all(val is None for val in v.values())
    assert set(v) == set(R4_FACTOR_NAMES)


# --- end-to-end build over a real tmp SnapshotStore -------------------------


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(str(tmp_path))


def _put_month(store: SnapshotStore, key: str, frame: pd.DataFrame) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=EP_REPORT_RC,
        params={"end_date": key},
        trade_date=key,
        raw_payload=canonical_csv_bytes(frame),
        encoding="csv",
        compression="none",
        fetch_time_utc=FIXED_NOW,
        metadata={"rows": int(len(frame))},
    )
    store.put(snap)


def _month_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    cols = [
        "ts_code",
        "report_date",
        "report_type",
        "org_name",
        "author_name",
        "quarter",
        "np",
        "eps",
        "rating",
        "min_price",
        "create_time",
    ]
    return pd.DataFrame([{c: r.get(c, "") for c in cols} for r in rows], columns=cols)


def test_read_report_rc_month_keeps_dates_str(store: SnapshotStore) -> None:
    _put_month(
        store,
        "20240131",
        _month_frame(
            [
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240115",
                    "report_type": "点评",
                    "org_name": "中信",
                    "quarter": "2024Q4",
                    "np": "1000000",
                    "create_time": "20240116",
                },
            ]
        ),
    )
    out = read_report_rc_month(store, "20240131")
    assert out.loc[0, "report_date"] == "20240115"  # literal, not 20240115.0


def test_build_missing_month_raises(store: SnapshotStore) -> None:
    with pytest.raises(FileNotFoundError):
        read_report_rc_month(store, "20240131")
    with pytest.raises(FileNotFoundError):
        AnalystRevisionPIT.build(store, ["20240131"])


def test_build_filters_report_type_and_dedup(store: SnapshotStore) -> None:
    _put_month(
        store,
        "20240131",
        _month_frame(
            [
                # individual-stock forecast (kept)
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240110",
                    "report_type": "点评",
                    "org_name": "中信",
                    "author_name": "X",
                    "quarter": "2024Q4",
                    "np": "100",
                    "create_time": "2024-01-11 09:00:00",
                },
                # exact dup dedup key, LATER create_time, different np → keep latest
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240110",
                    "report_type": "点评",
                    "org_name": "中信",
                    "author_name": "X",
                    "quarter": "2024Q4",
                    "np": "999",
                    "create_time": "2024-01-11 18:00:00",
                },
                # industry report (dropped)
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240112",
                    "report_type": "非个股",
                    "org_name": "中信",
                    "quarter": "2024Q4",
                    "np": "55555",
                    "create_time": "x",
                },
                # new-share report (dropped)
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240113",
                    "report_type": "新股",
                    "org_name": "海通",
                    "quarter": "2024Q4",
                    "np": "77777",
                    "create_time": "x",
                },
            ]
        ),
    )
    pit = AnalystRevisionPIT.build(store, ["20240131"])
    reports = pit.by_code["600519.SH"]
    assert len(reports) == 1  # dedup collapsed the pair, the 2 non-stock dropped
    assert reports[0].net_profit == pytest.approx(999.0)  # latest create_time won


def test_build_factors_end_to_end_pit_gate(store: SnapshotStore) -> None:
    # Two months: a rising NP consensus. Decision 20240401 (FY 2024). Reports ON
    # the decision date must NOT be visible (report_date < d strict).
    _put_month(
        store,
        "20231130",
        _month_frame(
            [
                {
                    "ts_code": "600519.SH",
                    "report_date": "20231101",
                    "report_type": "点评",
                    "org_name": "中信",
                    "author_name": "a",
                    "quarter": "2024Q4",
                    "np": "100",
                    "create_time": "2023-11-02 09:00:00",
                },
                {
                    "ts_code": "600519.SH",
                    "report_date": "20231102",
                    "report_type": "深度",
                    "org_name": "海通",
                    "author_name": "b",
                    "quarter": "2024Q4",
                    "np": "100",
                    "create_time": "2023-11-03 09:00:00",
                },
                {
                    "ts_code": "600519.SH",
                    "report_date": "20231103",
                    "report_type": "一般",
                    "org_name": "国君",
                    "author_name": "c",
                    "quarter": "2024Q4",
                    "np": "100",
                    "create_time": "2023-11-04 09:00:00",
                },
            ]
        ),
    )
    _put_month(
        store,
        "20240331",
        _month_frame(
            [
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240310",
                    "report_type": "点评",
                    "org_name": "中信",
                    "author_name": "a",
                    "quarter": "2024Q4",
                    "np": "110",
                    "create_time": "2024-03-11 09:00:00",
                },
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240311",
                    "report_type": "深度",
                    "org_name": "海通",
                    "author_name": "b",
                    "quarter": "2024Q4",
                    "np": "112",
                    "create_time": "2024-03-12 09:00:00",
                },
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240312",
                    "report_type": "一般",
                    "org_name": "国君",
                    "author_name": "c",
                    "quarter": "2024Q4",
                    "np": "108",
                    "create_time": "2024-03-13 09:00:00",
                },
                # a report ON the decision date — excluded by the strict PIT gate
                {
                    "ts_code": "600519.SH",
                    "report_date": "20240401",
                    "report_type": "点评",
                    "org_name": "中信",
                    "author_name": "a",
                    "quarter": "2024Q4",
                    "np": "9999",
                    "create_time": "2024-04-01 09:00:00",
                },
            ]
        ),
    )
    pit = AnalystRevisionPIT.build(store, ["20231130", "20240331"])
    # now window [d-120, d); back consensus ends at d-90=20240101, window
    # [20230903, 20240101) → catches the 20231101-03 back reports.
    v = pit.factors(
        "600519.SH",
        "20240401",
        close=1500.0,
        staleness_days=120,
        lookback_days=90,
        level_window_days=200,
    )
    # now median 110, back median 100 → +0.10 (the 9999 on-date row excluded).
    assert v["np_rev"] == pytest.approx(0.10)
    assert v["rev_diff"] == pytest.approx(1.0)  # all 3 up
    # an unknown code → all None.
    assert all(
        x is None for x in pit.factors("000001.SZ", "20240401", close=10.0).values()
    )
