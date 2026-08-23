"""Unit tests for the MZ-1 subscription calendars (zero network)."""

from __future__ import annotations

from typing import Any

from scripts.institutional_rent.calendars import (
    board_of,
    fetch_cb_subscriptions,
    fetch_stock_subscriptions,
    normalize_date,
)


class FakeFrame:
    def __init__(self, records: list[dict[str, Any]]):
        self._records = records

    @property
    def empty(self) -> bool:
        return not self._records

    def to_dict(self, orient: str) -> list[dict[str, Any]]:
        assert orient == "records"
        return list(self._records)


def _query_of(frames: dict[str, FakeFrame]):
    def query(endpoint: str, **kwargs: Any) -> FakeFrame:
        return frames[endpoint]

    return query


def test_board_of_labels() -> None:
    assert board_of("688001.SH") == "科创板"
    assert board_of("301689.SZ") == "创业板"
    assert board_of("601123.SH") == "沪主板"
    assert board_of("002271.SZ") == "深主板"


def test_normalize_date_mixed_formats() -> None:
    assert normalize_date("2026-08-19") == "20260819"
    assert normalize_date("20260819") == "20260819"


def test_stock_subscriptions_filters_bj_and_wrong_dates() -> None:
    frames = {
        "new_share": FakeFrame(
            [
                {"ts_code": "301689.SZ", "sub_code": "301689", "name": "电科思仪",
                 "ipo_date": "20260828", "price": 0.0},
                {"ts_code": "920289.BJ", "sub_code": "920289", "name": "华汇智能",
                 "ipo_date": "20260828", "price": 17.71},
                {"ts_code": "601123.SH", "sub_code": "780123", "name": "马矿股份",
                 "ipo_date": "20260821", "price": 6.65},
            ]
        )
    }
    subs = fetch_stock_subscriptions(_query_of(frames), "20260828")
    assert len(subs) == 1
    only = subs[0]
    assert only.ts_code == "301689.SZ"
    assert only.board == "创业板"
    assert only.price is None  # 0.0 → not yet published


def test_stock_subscriptions_empty_frame() -> None:
    query = _query_of({"new_share": FakeFrame([])})
    assert fetch_stock_subscriptions(query, "20260828") == ()


def test_cb_subscriptions_matches_onl_date_with_dashes() -> None:
    frames = {
        "cb_issue": FakeFrame(
            [
                {"ts_code": "123284.SZ", "onl_code": "371628", "onl_name": "强达发债",
                 "onl_date": "2026-08-19"},
                {"ts_code": "123283.SZ", "onl_code": "371459", "onl_name": "丰茂发债",
                 "onl_date": "2026-08-18"},
                {"ts_code": "111099.SH", "onl_code": None, "onl_name": None,
                 "onl_date": "2026-08-19"},
            ]
        )
    }
    subs = fetch_cb_subscriptions(_query_of(frames), "20260819")
    assert [c.onl_code for c in subs] == ["371628"]
    assert subs[0].onl_name == "强达发债"
