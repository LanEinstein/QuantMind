"""Survivorship-bias-free universe from Tushare ``stock_basic`` (AE-001).

A backtest that only knows *currently listed* codes is survivorship-biased:
it never holds the names that later went to zero / were delisted, inflating
returns. The amendment (§2.2) requires ingesting both the listed roster
(``list_status='L'``) and the delisted roster (``list_status='D'``) with each
code's ``list_date`` / ``delist_date`` so any historical day can reconstruct
the *真实可交易集* — exactly the codes that were listed and not yet delisted
on that day.

Dates are A-share ``YYYYMMDD`` strings; fixed-width so lexicographic order
equals chronological order. A code is tradable on ``date`` iff
``list_date <= date`` and (it is still listed, or ``date < delist_date``) —
the delist day itself is **excluded** (conservative: never let a backtest fill
an order on a day the security no longer trades).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
# Standard A-share code is 6 digits + exchange suffix (600519.SH). Tushare
# disambiguates a *reused* 6-digit code by prefixing the older, delisted
# security with a letter — e.g. ``T600018.SH`` 上港集箱(退) (delisted 2006),
# whose 600018 code was later reused by the currently-listed 上港集团
# (600018.SH). Accept an optional single leading uppercase letter so that
# delisted name is retained (survivorship-complete) and stays a DISTINCT code
# from the live listing — stripping the prefix would collide with the reused
# code and trip the duplicate fail-closed below. This ``T``-form is the lone
# non-standard code in the real Tushare delisted roster (1 of 326).
_TS_CODE_RE = re.compile(r"^[A-Z]?\d{6}\.(SH|SZ|BJ)$")


def _norm_date(value: object, *, field: str, ts_code: str) -> str | None:
    """Normalise a date cell to ``YYYYMMDD`` or ``None`` (empty/NaN).

    Raises:
        ValueError: a non-empty value that is not 8 digits (fail-closed —
            a malformed date would silently corrupt the tradable window).
    """
    if value is None:
        return None
    # pandas may hand us a float NaN for an empty cell.
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return None
    if not _DATE_RE.match(text):
        raise ValueError(
            f"{field} {text!r} for {ts_code} must be YYYYMMDD (8 digits)"
        )
    return text


@dataclass(frozen=True)
class StockListing:
    """One security's listing lifecycle (immutable)."""

    ts_code: str
    name: str
    list_date: str
    delist_date: str | None

    def is_tradable_asof(self, date: str) -> bool:
        """Was this security listed-and-not-delisted on ``date``?"""
        if self.list_date > date:
            return False
        if self.delist_date is not None and date >= self.delist_date:
            return False
        return True


@dataclass(frozen=True)
class SurvivorshipUniverse:
    """The full survivorship-bias-free roster (listed + delisted)."""

    listings: tuple[StockListing, ...]

    def all_codes(self) -> frozenset[str]:
        """Every ``ts_code`` ever in the roster (listed or delisted)."""
        return frozenset(listing.ts_code for listing in self.listings)

    def tradable_asof(self, date: str) -> frozenset[str]:
        """Codes that were tradable on ``date`` (the 真实可交易集)."""
        if not _DATE_RE.match(date):
            raise ValueError(f"date {date!r} must be YYYYMMDD (8 digits)")
        return frozenset(
            listing.ts_code
            for listing in self.listings
            if listing.is_tradable_asof(date)
        )

    @classmethod
    def from_stock_basic(
        cls, listed: pd.DataFrame, delisted: pd.DataFrame
    ) -> SurvivorshipUniverse:
        """Build from the two Tushare ``stock_basic`` rosters (L + D).

        Args:
            listed: ``stock_basic(list_status='L')`` frame.
            delisted: ``stock_basic(list_status='D')`` frame (may be empty).

        Raises:
            ValueError: a duplicate ``ts_code`` (ambiguous lifecycle) or a
                malformed date — both fail-closed.
        """
        by_code: dict[str, StockListing] = {}
        for frame in (listed, delisted):
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                ts_code = str(row["ts_code"]).strip()
                if not _TS_CODE_RE.match(ts_code):
                    raise ValueError(
                        f"ts_code {ts_code!r} must look like 600519.SH"
                    )
                if ts_code in by_code:
                    raise ValueError(
                        f"duplicate ts_code {ts_code} in stock_basic — "
                        "ambiguous listing lifecycle (fail-closed)"
                    )
                list_date = _norm_date(
                    row.get("list_date"), field="list_date", ts_code=ts_code
                )
                if list_date is None:
                    raise ValueError(
                        f"list_date missing for {ts_code} (YYYYMMDD required)"
                    )
                delist_date = _norm_date(
                    row.get("delist_date"),
                    field="delist_date",
                    ts_code=ts_code,
                )
                by_code[ts_code] = StockListing(
                    ts_code=ts_code,
                    name=str(row.get("name", "")).strip(),
                    list_date=list_date,
                    delist_date=delist_date,
                )
        # Sort for a deterministic ordering (content-stable manifests).
        listings = tuple(by_code[code] for code in sorted(by_code))
        return cls(listings=listings)


__all__ = ["StockListing", "SurvivorshipUniverse"]
