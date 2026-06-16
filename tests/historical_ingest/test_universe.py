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


def test_tushare_t_prefixed_delisted_code_accepted() -> None:
    """Tushare disambiguates a *reused* 6-digit code by prefixing the older,
    delisted security with a letter: ``T600018.SH`` 上港集箱(退) (delisted
    2006) shares its digits with the currently-listed ``600018.SH`` 上港集团.

    The builder must accept the ``T``-code as a DISTINCT survivorship entry —
    not crash on the format, and not collide with the reused live code
    (stripping the ``T`` would duplicate ``600018.SH`` and fail closed). This
    is the lone non-standard code in the real Tushare delisted roster.
    """
    listed = pd.DataFrame(
        {
            "ts_code": ["600018.SH"],
            "name": ["上港集团"],
            "list_date": ["20061026"],
            "delist_date": [None],
            "list_status": ["L"],
        }
    )
    delisted = pd.DataFrame(
        {
            "ts_code": ["T600018.SH"],
            "name": ["上港集箱(退)"],
            "list_date": ["20000719"],
            "delist_date": ["20061020"],
            "list_status": ["D"],
        }
    )
    u = SurvivorshipUniverse.from_stock_basic(listed, delisted)
    # Both retained as distinct codes (no collision, no silent drop).
    assert u.all_codes() == frozenset({"600018.SH", "T600018.SH"})
    # The delisted T-code WAS tradable in its 2005 window; the reused live
    # code is not yet listed then (their lifecycles do not overlap).
    early = u.tradable_asof("20050101")
    assert "T600018.SH" in early
    assert "600018.SH" not in early
    # After the 2006 delist the T-code is gone; the live code trades in 2020.
    late = u.tradable_asof("20200101")
    assert "T600018.SH" not in late
    assert "600018.SH" in late


@pytest.mark.parametrize(
    "bad_code",
    [
        "AB600018.SH",  # two-letter prefix — over-relaxation guard
        "a600018.SH",  # lowercase prefix
        "6000018.SH",  # 7 digits
        "60018.SH",  # 5 digits
        "600018.SS",  # unknown exchange suffix
    ],
)
def test_only_single_uppercase_prefix_accepted(bad_code: str) -> None:
    """The ``[A-Z]?`` relaxation must stay surgical: a single optional
    uppercase letter only. Anything broader (multi-letter / lowercase /
    wrong digit count / wrong suffix) must still fail closed — this pins the
    boundary so a future edit cannot silently widen it into garbage."""
    frame = pd.DataFrame(
        {
            "ts_code": [bad_code],
            "name": ["x"],
            "list_date": ["20000719"],
            "delist_date": ["20061020"],
            "list_status": ["D"],
        }
    )
    with pytest.raises(ValueError, match="must look like"):
        SurvivorshipUniverse.from_stock_basic(pd.DataFrame(), frame)


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
