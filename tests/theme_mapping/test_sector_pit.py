"""AF-001 — 申万 L3 point-in-time membership (in/out windows, fail-closed)."""

from __future__ import annotations

import pandas as pd

from backend.theme_mapping.sector_pit import SectorMembershipPIT


def _frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    cols = ["ts_code", "l3_code", "in_date", "out_date"]
    return pd.DataFrame(rows, columns=cols).astype(str)


def test_open_window_is_member_today() -> None:
    m = SectorMembershipPIT.from_frame(
        _frame(
            [
                {
                    "ts_code": "600519.SH",
                    "l3_code": "850816.SI",
                    "in_date": "20100101",
                    "out_date": "",
                }
            ]
        )
    )
    assert m.l3_asof("600519.SH", "20200101") == frozenset({"850816.SI"})
    assert m.l3_asof("600519.SH", "20091231") == frozenset()  # before in_date


def test_closed_window_excludes_after_out_date() -> None:
    m = SectorMembershipPIT.from_frame(
        _frame(
            [
                {
                    "ts_code": "000001.SZ",
                    "l3_code": "850814.SI",
                    "in_date": "20150101",
                    "out_date": "20180101",
                }
            ]
        )
    )
    assert m.l3_asof("000001.SZ", "20160101") == frozenset({"850814.SI"})
    assert m.l3_asof("000001.SZ", "20180101") == frozenset()  # out_date is exclusive
    assert m.l3_asof("000001.SZ", "20190101") == frozenset()


def test_reclassification_resolves_pit_segment() -> None:
    m = SectorMembershipPIT.from_frame(
        _frame(
            [
                {
                    "ts_code": "X.SH",
                    "l3_code": "850841.SI",
                    "in_date": "20100101",
                    "out_date": "20200101",
                },
                {
                    "ts_code": "X.SH",
                    "l3_code": "850816.SI",
                    "in_date": "20200101",
                    "out_date": "",
                },
            ]
        )
    )
    assert m.l3_asof("X.SH", "20150101") == frozenset({"850841.SI"})  # old class
    assert m.l3_asof("X.SH", "20250101") == frozenset({"850816.SI"})  # after switch


def test_unknown_code_and_malformed_rows_fail_closed() -> None:
    m = SectorMembershipPIT.from_frame(
        _frame(
            [
                {
                    "ts_code": "",
                    "l3_code": "850816.SI",
                    "in_date": "20100101",
                    "out_date": "",
                },  # no ts_code → skipped
                {
                    "ts_code": "A.SH",
                    "l3_code": "",
                    "in_date": "20100101",
                    "out_date": "",
                },  # no l3 → skipped
                {
                    "ts_code": "B.SH",
                    "l3_code": "850816.SI",
                    "in_date": "bad",
                    "out_date": "",
                },  # bad in_date → skipped
                {
                    "ts_code": "C.SH",
                    "l3_code": "850816.SI",
                    "in_date": "20100101",
                    "out_date": "bad",
                },  # bad out_date → segment dropped
            ]
        )
    )
    assert m.l3_asof("ZZZ.SH", "20200101") == frozenset()  # unknown
    assert m.l3_asof("A.SH", "20200101") == frozenset()
    assert m.l3_asof("B.SH", "20200101") == frozenset()
    assert m.l3_asof("C.SH", "20200101") == frozenset()
