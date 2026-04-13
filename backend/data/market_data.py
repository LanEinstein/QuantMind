"""Real-time market data service with adata primary / akshare fallback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pandas as pd
import structlog

from backend.data.config import DataSourcesConfig
from backend.models.market import (
    CapitalFlowData,
    IndexQuote,
    SectorQuote,
    StockQuote,
)

log = structlog.get_logger(component="market_data")


class DataFetchError(Exception):
    """Raised when both primary and fallback data sources fail."""


# ---------------------------------------------------------------------------
# Low-level fetchers (module-level so they can be easily patched in tests)
# ---------------------------------------------------------------------------


def _fetch_index_adata(code: str) -> pd.DataFrame:
    """Fetch real-time index quote from adata (sync, run via to_thread)."""
    import adata.stock.market as m

    return m.get_market_index_current(index_code=code)


def _fetch_index_akshare(code: str) -> pd.DataFrame:
    """Fallback: fetch index quote from akshare."""
    import akshare

    df = akshare.index_zh_a_hist(symbol=code, period="daily")
    if df.empty:
        return df
    row = df.iloc[-1:]
    return pd.DataFrame(
        [
            {
                "index_code": code,
                "trade_time": str(datetime.now(tz=UTC)),
                "trade_date": str(row.iloc[0].get("日期", "")),
                "open": row.iloc[0].get("开盘", 0),
                "high": row.iloc[0].get("最高", 0),
                "low": row.iloc[0].get("最低", 0),
                "price": row.iloc[0].get("收盘", 0),
                "volume": row.iloc[0].get("成交量", 0),
                "amount": row.iloc[0].get("成交额", 0),
                "change": 0,
                "change_pct": row.iloc[0].get("涨跌幅", 0),
            }
        ]
    )


def _fetch_index_history_akshare(
    code: str, start_date: str, end_date: str
) -> pd.DataFrame:
    """Fetch historical index prices from akshare."""
    import akshare

    return akshare.index_zh_a_hist(
        symbol=code, period="daily",
        start_date=start_date, end_date=end_date,
    )


def _fetch_stock_adata(code: str) -> pd.DataFrame:
    """Fetch real-time stock quote from adata."""
    import adata.stock.market as m

    return m.list_market_current(code_list=[code])


def _fetch_stock_akshare(code: str) -> pd.DataFrame:
    """Fallback: fetch stock quote from akshare."""
    import akshare

    df = akshare.stock_zh_a_spot_em()
    return df[df["代码"] == code]


def _fetch_stock_list_adata(codes: list[str]) -> pd.DataFrame:
    """Fetch real-time quotes for multiple stocks from adata."""
    import adata.stock.market as m

    return m.list_market_current(code_list=codes)


def _fetch_sectors_akshare() -> pd.DataFrame:
    """Fetch sector overview from akshare (primary source for sectors)."""
    import akshare

    return akshare.stock_board_industry_name_em()


def _fetch_capital_flow_akshare() -> pd.DataFrame:
    """Fetch northbound capital flow from akshare."""
    import akshare

    return akshare.stock_hsgt_hist_em(symbol="北向资金")


# ---------------------------------------------------------------------------
# Model conversion helpers
# ---------------------------------------------------------------------------


def _index_row_to_quote(row: pd.Series) -> IndexQuote:
    """Convert an adata index DataFrame row to IndexQuote."""
    now = datetime.now(tz=UTC)
    return IndexQuote(
        code=str(row.get("index_code", "")),
        name=_index_name(str(row.get("index_code", ""))),
        price=float(row.get("price", 0)),
        change_pct=float(row.get("change_pct", 0)),
        volume=float(row.get("volume", 0)),
        amount=float(row.get("amount", 0)),
        timestamp=now,
    )


def _index_name(code: str) -> str:
    """Map common index codes to Chinese names."""
    names = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
    }
    return names.get(code, code)


def _adata_stock_row_to_quote(row: pd.Series) -> StockQuote:
    """Convert an adata list_market_current row to StockQuote."""
    now = datetime.now(tz=UTC)
    return StockQuote(
        code=str(row.get("stock_code", "")),
        name=str(row.get("short_name", "")),
        price=float(row.get("price", 0)),
        open=float(row.get("open", 0)),
        high=float(row.get("high", 0)),
        low=float(row.get("low", 0)),
        prev_close=float(row.get("pre_close", 0)),
        change_pct=float(row.get("change_pct", 0)),
        volume=float(row.get("volume", 0)),
        amount=float(row.get("amount", 0)),
        turnover_rate=float(row.get("turnover_ratio", 0)),
        timestamp=now,
    )


def _akshare_stock_row_to_quote(row: pd.Series) -> StockQuote:
    """Convert an akshare stock_zh_a_spot_em row to StockQuote."""
    now = datetime.now(tz=UTC)
    return StockQuote(
        code=str(row.get("代码", "")),
        name=str(row.get("名称", "")),
        price=float(row.get("最新价", 0)),
        open=float(row.get("今开", 0)),
        high=float(row.get("最高", 0)),
        low=float(row.get("最低", 0)),
        prev_close=float(row.get("昨收", 0)),
        change_pct=float(row.get("涨跌幅", 0)),
        volume=float(row.get("成交量", 0)),
        amount=float(row.get("成交额", 0)),
        turnover_rate=float(row.get("换手率", 0)),
        timestamp=now,
    )


def _akshare_sector_row_to_quote(row: pd.Series) -> SectorQuote:
    """Convert an akshare board row to SectorQuote."""
    now = datetime.now(tz=UTC)
    return SectorQuote(
        name=str(row.get("板块名称", "")),
        change_pct=float(row.get("涨跌幅", 0)),
        leader_code=str(row.get("领涨股票代码", "")),
        leader_name=str(row.get("领涨股票", "")),
        leader_change_pct=float(row.get("领涨涨跌幅", 0)),
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# MarketDataService
# ---------------------------------------------------------------------------


class MarketDataService:
    """Async service for real-time A-share market data.

    Uses adata as primary data source with akshare as fallback.
    All external calls are wrapped in asyncio.to_thread for async compat.
    """

    def __init__(self, config: DataSourcesConfig) -> None:
        self._config = config
        self._log = log

    async def get_index_realtime(
        self, codes: list[str] | None = None
    ) -> list[IndexQuote]:
        """Get real-time quotes for major indices."""
        if codes is None:
            codes = ["000001", "399001", "399006"]

        results: list[IndexQuote] = []
        for code in codes:
            try:
                df = await asyncio.to_thread(_fetch_index_adata, code)
            except Exception:
                self._log.warning("index_adata_failed", code=code)
                try:
                    df = await asyncio.to_thread(_fetch_index_akshare, code)
                except Exception:
                    self._log.error("index_both_failed", code=code)
                    raise DataFetchError(
                        f"Both adata and akshare failed for index {code}"
                    )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    results.append(_index_row_to_quote(row))
        return results

    async def get_index_history(
        self, index_code: str = "000300", days: int = 252
    ) -> pd.DataFrame:
        """Fetch historical index prices (default: CSI300, 1 year).

        Returns DataFrame with columns: date, open, high, low, close, volume.
        """
        from datetime import timedelta

        end = datetime.now(tz=UTC).strftime("%Y%m%d")
        start = (datetime.now(tz=UTC) - timedelta(days=days)).strftime("%Y%m%d")

        try:
            df = await asyncio.to_thread(
                _fetch_index_history_akshare, index_code, start, end
            )
        except Exception as exc:
            self._log.error("index_history_failed", code=index_code, error=str(exc))
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        result = pd.DataFrame({
            "date": df["日期"].astype(str),
            "open": pd.to_numeric(df.get("开盘", 0), errors="coerce").fillna(0),
            "high": pd.to_numeric(df.get("最高", 0), errors="coerce").fillna(0),
            "low": pd.to_numeric(df.get("最低", 0), errors="coerce").fillna(0),
            "close": pd.to_numeric(df.get("收盘", 0), errors="coerce").fillna(0),
            "volume": pd.to_numeric(df.get("成交量", 0), errors="coerce").fillna(0),
        })
        return result

    async def get_stock_realtime(self, code: str) -> StockQuote:
        """Get real-time quote for a single stock."""
        try:
            df = await asyncio.to_thread(_fetch_stock_adata, code)
            if df.empty:
                raise DataFetchError(f"No data from adata for {code}")
            return _adata_stock_row_to_quote(df.iloc[0])
        except Exception:
            self._log.warning("stock_adata_failed", code=code)
            try:
                df = await asyncio.to_thread(_fetch_stock_akshare, code)
                if df.empty:
                    raise DataFetchError(f"No data from akshare for {code}")
                return _akshare_stock_row_to_quote(df.iloc[0])
            except Exception:
                self._log.error("stock_both_failed", code=code)
                raise DataFetchError(
                    f"Both adata and akshare failed for stock {code}"
                )

    async def get_stock_list_realtime(
        self, codes: list[str]
    ) -> list[StockQuote]:
        """Get real-time quotes for multiple stocks."""
        try:
            df = await asyncio.to_thread(_fetch_stock_list_adata, codes)
        except Exception:
            self._log.warning("stock_list_adata_failed")
            try:
                df = await asyncio.to_thread(_fetch_stock_akshare, ",".join(codes))
            except Exception:
                raise DataFetchError("Both sources failed for stock list")

        if df is None or df.empty:
            return []

        # Detect source by column names
        if "stock_code" in df.columns:
            return [_adata_stock_row_to_quote(row) for _, row in df.iterrows()]
        return [_akshare_stock_row_to_quote(row) for _, row in df.iterrows()]

    async def get_sector_overview(self) -> list[SectorQuote]:
        """Get sector performance overview."""
        try:
            df = await asyncio.to_thread(_fetch_sectors_akshare)
        except Exception:
            self._log.error("sectors_fetch_failed")
            raise DataFetchError("Failed to fetch sector data")

        if df is None or df.empty:
            return []

        return [_akshare_sector_row_to_quote(row) for _, row in df.iterrows()]

    async def get_capital_flow(self) -> CapitalFlowData:
        """Get northbound capital flow."""
        try:
            df = await asyncio.to_thread(_fetch_capital_flow_akshare)
        except Exception:
            self._log.error("capital_flow_fetch_failed")
            raise DataFetchError("Failed to fetch capital flow data")

        if df is None or df.empty:
            return CapitalFlowData(
                north_net_inflow=0.0,
                main_net_inflow=0.0,
                timestamp=datetime.now(tz=UTC),
            )

        latest = df.iloc[-1]
        north = float(
            latest.get("north_money", latest.get("当日资金流入", 0))
        )
        return CapitalFlowData(
            north_net_inflow=north,
            main_net_inflow=0.0,
            timestamp=datetime.now(tz=UTC),
        )
