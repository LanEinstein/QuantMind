"""Historical data service with adata primary / baostock fallback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pandas as pd
import structlog

from backend.data.config import DataSourcesConfig
from backend.data.market_data import DataFetchError
from backend.models.market import FinancialData

log = structlog.get_logger(component="history_data")

VALID_PERIODS = {"daily", "weekly", "monthly"}
VALID_ADJUSTS = {"qfq", "hfq", "none"}

# adata k_type mapping: 1=daily, 2=weekly, 3=monthly
_ADATA_KTYPE = {"daily": 1, "weekly": 2, "monthly": 3}
# adata adjust_type: 1=qfq, 2=hfq, 0=none
_ADATA_ADJUST = {"qfq": 1, "hfq": 2, "none": 0}

# baostock frequency: d=daily, w=weekly, m=monthly
_BAOSTOCK_FREQ = {"daily": "d", "weekly": "w", "monthly": "m"}
# baostock adjustflag: 2=qfq, 1=hfq, 3=none
_BAOSTOCK_ADJUST = {"qfq": "2", "hfq": "1", "none": "3"}


# ---------------------------------------------------------------------------
# Low-level fetchers
# ---------------------------------------------------------------------------


def _fetch_kline_adata(
    code: str, start_date: str, end_date: str, k_type: int, adjust_type: int
) -> pd.DataFrame:
    """Fetch K-line data from adata (sync)."""
    import adata.stock.market as m

    return m.get_market(
        stock_code=code,
        start_date=start_date,
        end_date=end_date,
        k_type=k_type,
        adjust_type=adjust_type,
    )


def _fetch_kline_baostock(
    code: str, start_date: str, end_date: str, frequency: str, adjustflag: str
) -> pd.DataFrame:
    """Fetch K-line data from baostock (sync, manages login/logout)."""
    import baostock as bs

    bs.login()
    try:
        # baostock expects code like "sh.600519" or "sz.000001"
        prefix = "sh" if code.startswith("6") else "sz"
        bs_code = f"{prefix}.{code}"
        fields = "date,open,high,low,close,volume,amount"
        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=start_date or None,
            end_date=end_date or None,
            frequency=frequency,
            adjustflag=adjustflag,
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)
    finally:
        bs.logout()


def _fetch_financial_adata(code: str) -> pd.DataFrame:
    """Fetch core financial indicators from adata."""
    import adata.stock.finance as fin

    return fin.get_core_index(stock_code=code)


def _fetch_financial_akshare(code: str) -> pd.DataFrame:
    """Fallback: financial data from akshare spot DataFrame."""
    import akshare

    df = akshare.stock_zh_a_spot_em()
    return df[df["代码"] == code]


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_adata_kline(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize adata K-line DataFrame to standard columns."""
    if df.empty:
        return df
    rename = {
        "trade_date": "date",
        "trade_time": "_trade_time",
    }
    result = df.rename(columns=rename)
    standard_cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    for col in standard_cols:
        if col not in result.columns:
            result[col] = 0
    return result[standard_cols].copy()


def _normalize_baostock_kline(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize baostock K-line DataFrame to standard columns."""
    if df.empty:
        return df
    numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    standard_cols = ["date", "open", "high", "low", "close", "volume", "amount"]
    for col in standard_cols:
        if col not in df.columns:
            df[col] = 0
    return df[standard_cols].copy()


# ---------------------------------------------------------------------------
# HistoryDataService
# ---------------------------------------------------------------------------


class HistoryDataService:
    """Async service for historical K-line and financial data.

    Uses adata as primary source with baostock as fallback for K-line data.
    """

    def __init__(self, config: DataSourcesConfig) -> None:
        self._config = config
        self._log = log

    async def get_kline(
        self,
        code: str,
        period: str = "daily",
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """Get historical K-line data.

        Args:
            code: Stock code (e.g. "600519").
            period: One of "daily", "weekly", "monthly".
            start_date: Start date string (YYYY-MM-DD). Empty = default.
            end_date: End date string (YYYY-MM-DD). Empty = today.
            adjust: One of "qfq" (forward), "hfq" (backward), "none".

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount.

        Raises:
            ValueError: If period or adjust is invalid.
            DataFetchError: If both sources fail.
        """
        if period not in VALID_PERIODS:
            raise ValueError(
                f"Invalid period '{period}'. Must be one of {VALID_PERIODS}"
            )
        if adjust not in VALID_ADJUSTS:
            raise ValueError(
                f"Invalid adjust '{adjust}'. Must be one of {VALID_ADJUSTS}"
            )

        # Try adata primary
        try:
            df = await asyncio.to_thread(
                _fetch_kline_adata,
                code,
                start_date or "1990-01-01",
                end_date or None,
                _ADATA_KTYPE[period],
                _ADATA_ADJUST[adjust],
            )
            return _normalize_adata_kline(df)
        except Exception:
            self._log.warning("kline_adata_failed", code=code)

        # Fallback to baostock
        try:
            df = await asyncio.to_thread(
                _fetch_kline_baostock,
                code,
                start_date,
                end_date,
                _BAOSTOCK_FREQ[period],
                _BAOSTOCK_ADJUST[adjust],
            )
            return _normalize_baostock_kline(df)
        except Exception:
            self._log.error("kline_both_failed", code=code)
            raise DataFetchError(
                f"Both adata and baostock failed for kline {code}"
            )

    async def get_financial_data(self, code: str) -> FinancialData:
        """Get financial indicators for a stock.

        Args:
            code: Stock code (e.g. "600519").

        Returns:
            FinancialData with PE, PB, ROE, EPS, revenue growth.

        Raises:
            DataFetchError: If both sources fail.
        """
        now = datetime.now(tz=UTC)

        # Try adata primary
        try:
            df = await asyncio.to_thread(_fetch_financial_adata, code)
            if df.empty:
                raise DataFetchError(f"No financial data from adata for {code}")
            row = df.iloc[0]
            return FinancialData(
                code=str(row.get("stock_code", code)),
                name=str(row.get("short_name", "")),
                pe_ratio=_safe_float(row.get("pe_ratio")),
                pb_ratio=_safe_float(row.get("pb_ratio")),
                roe=_safe_float(row.get("roe_wtd")),
                eps=_safe_float(row.get("basic_eps")),
                revenue_growth=_safe_float(row.get("total_rev_yoy_gr")),
                report_date=str(row.get("report_date", "")),
                timestamp=now,
            )
        except Exception:
            self._log.warning("financial_adata_failed", code=code)

        # Fallback to akshare
        try:
            df = await asyncio.to_thread(_fetch_financial_akshare, code)
            if df.empty:
                raise DataFetchError(
                    f"No financial data from akshare for {code}"
                )
            row = df.iloc[0]
            return FinancialData(
                code=str(row.get("代码", code)),
                name=str(row.get("名称", "")),
                pe_ratio=_safe_float(row.get("市盈率-动态")),
                pb_ratio=_safe_float(row.get("市净率")),
                roe=None,
                eps=None,
                revenue_growth=None,
                report_date="",
                timestamp=now,
            )
        except Exception:
            self._log.error("financial_both_failed", code=code)
            raise DataFetchError(
                f"Both adata and akshare failed for financial data {code}"
            )


def _safe_float(value: object) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
