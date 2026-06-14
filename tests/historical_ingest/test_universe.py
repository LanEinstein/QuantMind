"""Tests for AE-001 survivorship-bias-free universe.

Red line (amendment §2.2): delisted codes must carry their listed-era
history and must NOT appear in the tradable set on/after their delist date.
Including a delisted code on a date it no longer trades would let a backtest
"fill" an untradable order — a look-ahead / survivorship leak (codex 头号
数据险).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.data.historical_ingest.universe import (
    StockListing,
    SurvivorshipUniverse,
)


def _basic_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    listed = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "000001.SZ"],
            "name": ["贵州茅台", "平安银行"],
            "list_date": ["20010827", "19910403"],
            "delist_date": [None, None],
            "list_status": ["L", "L"],
        }
    )
    delisted = pd.DataFrame(
        {
            "ts_code": ["600001.SH"],
            "name": ["邯郸钢铁"],
            "list_date": ["19980122"],
            "delist_date": ["20100817"],
            "list_status": ["D"],
        }
    )
    return listed, delisted


def test_listing_tradable_window() -> None:
    lst = StockListing(
        ts_code="600001.SH",
        name="x",
        list_date="19980122",
        delist_date="20100817",
    )
    assert not lst.is_tradable_asof("19980121")  # before IPO
    assert lst.is_tradable_asof("19980122")  # IPO day
    assert lst.is_tradable_asof("20100816")  # day before delist
    assert not lst.is_tradable_asof("20100817")  # delist day → excluded
    assert not lst.is_tradable_asof("20200101")  # long after delist


def test_listed_code_has_no_delist_bound() -> None:
    lst = StockListing(
        ts_code="600519.SH", name="x", list_date="20010827", delist_date=None
    )
    assert lst.is_tradable_asof("20010827")
    assert lst.is_tradable_asof("20260101")


def test_from_stock_basic_parses_both_statuses() -> None:
    listed, delisted = _basic_frames()
    u = SurvivorshipUniverse.from_stock_basic(listed, delisted)
    assert u.all_codes() == frozenset(
        {"600519.SH", "000001.SZ", "600001.SH"}
    )


def test_tradable_asof_excludes_delisted_after_delist() -> None:
    listed, delisted = _basic_frames()
    u = SurvivorshipUniverse.from_stock_basic(listed, delisted)

    # On a 2008 date the now-delisted code WAS tradable (survivorship-correct)
    early = u.tradable_asof("20080101")
    assert "600001.SH" in early

    # After its 2010 delist it must be gone
    late = u.tradable_asof("20200101")
    assert "600001.SH" not in late
    assert "600519.SH" in late


def test_not_yet_listed_excluded() -> None:
    listed, delisted = _basic_frames()
    u = SurvivorshipUniverse.from_stock_basic(listed, delisted)
    # 贵州茅台 listed 2001-08-27; on a 2000 date it is not in the set
    early = u.tradable_asof("20000101")
    assert "600519.SH" not in early
    assert "000001.SZ" in early  # 平安银行 listed 1991


def test_empty_delist_date_treated_as_listed() -> None:
    listed = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["x"],
            "list_date": ["19910403"],
            "delist_date": [""],  # empty string, not None
            "list_status": ["L"],
        }
    )
    u = SurvivorshipUniverse.from_stock_basic(listed, pd.DataFrame())
    assert u.tradable_asof("20260101") == frozenset({"000001.SZ"})


def test_duplicate_code_fails_closed() -> None:
    listed = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "name": ["x", "y"],
            "list_date": ["19910403", "19910403"],
            "delist_date": [None, None],
            "list_status": ["L", "L"],
        }
    )
    with pytest.raises(ValueError, match="duplicate"):
        SurvivorshipUniverse.from_stock_basic(listed, pd.DataFrame())


def test_bad_date_fails_closed() -> None:
    listed = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "name": ["x"],
            "list_date": ["1991"],  # not YYYYMMDD
            "delist_date": [None],
            "list_status": ["L"],
        }
    )
    with pytest.raises(ValueError, match="YYYYMMDD"):
        SurvivorshipUniverse.from_stock_basic(listed, pd.DataFrame())
