"""Real-time market data service with adata primary / tushare-sina fallback.

P0-8-amendment-2026-05-28: the dual-source ``last`` fallback leg switched
from akshare ``stock_zh_a_spot_em()`` (full-market batch, ~5500 rows / 58
pages — eastmoney throttles it with ``RemoteDisconnected``) to Tushare
``ts.realtime_quote(src='sina')`` (single-symbol, sina-sourced, off the
``pro_api`` credit interface). akshare paths in the non-dual code remain
in place; only ``get_stock_realtime_dual`` switched legs.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime

import pandas as pd
import structlog

from backend.data.config import DataSourcesConfig
from backend.data.stock_metadata import Board, classify_board
from backend.models.market import (
    CapitalFlowData,
    IndexQuote,
    QuoteSource,
    SectorQuote,
    StockOrderbook,
    StockQuote,
    WatchlistMarketSnapshot,
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
        symbol=code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
    )


def _fetch_stock_adata(code: str) -> pd.DataFrame:
    """Fetch real-time stock quote from adata."""
    import adata.stock.market as m

    return m.list_market_current(code_list=[code])


def _fetch_stock_akshare(code: str) -> pd.DataFrame:
    """Fallback: fetch stock quote from akshare.

    NOTE (P0-8-amendment-2026-05-28): this helper is **no longer** the
    dual-source fallback leg — eastmoney throttles ``stock_zh_a_spot_em()``
    (full-market batch, ~58 pages) with ``RemoteDisconnected``. It is
    retained for the non-dual ``get_stock_realtime`` single-source path and
    for legacy callers (test fixtures, single-source utilities). The
    dual-source path uses :func:`_fetch_stock_tushare_sina` instead.
    """
    import akshare

    df = akshare.stock_zh_a_spot_em()
    return df[df["代码"] == code]


def _to_tushare_ts_code(code: str) -> str:
    """Map a bare 6-digit A-share code to a Tushare ``ts_code``.

    Defers to :func:`backend.data.stock_metadata.classify_board` for
    fail-closed enforcement of the project's universe rules
    (P0-9 §1.2: STAR / 北交 / 可转债 / B-share are forbidden), then maps
    the resulting :class:`Board` to the exchange suffix:

    * :attr:`Board.SH_MAIN` → ``.SH``
    * :attr:`Board.SZ_MAIN` / :attr:`Board.CHUANGYE` → ``.SZ``
    * :attr:`Board.ETF` → ``.SH`` for SH-listed ETFs (``51x`` / ``588``),
      ``.SZ`` for SZ-listed ETFs (``159``)

    Raises :class:`ForbiddenCodeError` (universe-blocked) or
    :class:`UnknownCodeError` (malformed / unknown prefix); both subclass
    :class:`ValueError` so callers can catch either as input error.
    Sharing ``classify_board`` keeps the prefix table — and the audit
    namespace (``star_forbidden`` / ``bj_forbidden`` / ``cb_forbidden`` /
    ``b_share_forbidden``) — single-source with the rest of the data layer.
    """
    board = classify_board(code)  # raises Forbidden/UnknownCodeError
    if board is Board.SH_MAIN:
        return f"{code}.SH"
    if board in (Board.SZ_MAIN, Board.CHUANGYE):
        return f"{code}.SZ"
    # ETF: 51x/588 = SH; 159 = SZ. The classify_board allowlist is the
    # single source of truth — we only need to disambiguate which exchange
    # an allowed ETF prefix lives on.
    if code.startswith("159"):
        return f"{code}.SZ"
    return f"{code}.SH"


def _fetch_stock_tushare_sina(code: str) -> pd.DataFrame:
    """Dual-source ``fallback`` leg: Tushare ``realtime_quote(src='sina')``.

    Returns the raw Tushare DataFrame (single row, 33 columns including
    ``TS_CODE`` / ``PRICE`` / ``PRE_CLOSE`` / ``OPEN`` / ``HIGH`` / ``LOW`` /
    ``VOLUME`` / ``AMOUNT`` / ``BID`` / ``ASK`` / ``A1_P``..``A5_P`` /
    ``B1_P``..``B5_P`` / ``DATE`` / ``TIME``). The conversion to
    :class:`StockQuote` lives in :func:`_tushare_sina_row_to_quote`.

    Lazy-imports ``tushare`` to mirror the file's lazy-import discipline
    (akshare / adata also lazy) and to spare every module-load site —
    pytest collection, uvicorn startup, scripts/ — the full Tushare
    transitive stack when the sina path is never taken.

    Raises:
        ForbiddenCodeError / UnknownCodeError: when ``code`` is universe-
            blocked or malformed (propagated from :func:`_to_tushare_ts_code`
            so the caller never silently routes to a guessed exchange).
        Exception: surfaces any SDK / network error so the dual-source
            handler can log ``dual_tushare_sina_failed`` and set the
            fallback leg to ``None`` — silently swallowing would let a
            single-source view masquerade as dual. Note: Tushare's SDK
            wraps ``realtime_quote`` with ``@require_permission`` which
            performs an HTTPS verify-token round-trip to api.tushare.pro
            on every call (known overhead; amendment §1 footnote).
    """
    import tushare as ts  # lazy: see docstring

    ts_code = _to_tushare_ts_code(code)
    return ts.realtime_quote(ts_code=ts_code, src="sina")


def _fetch_stock_list_akshare(codes: list[str]) -> pd.DataFrame:
    """Fallback: fetch real-time quotes for multiple stocks from akshare.

    ``stock_zh_a_spot_em`` returns the full A-share spot table in one
    shot, so multi-code support is just an :py:meth:`pandas.DataFrame.isin`
    filter. Single-row helpers like :func:`_fetch_stock_akshare` use
    ``==`` against a single string, which would silently produce an
    empty frame when called with a comma-joined multi-code string —
    that bug masked watchlist outages until Codex review caught it.
    """
    import akshare

    df = akshare.stock_zh_a_spot_em()
    return df[df["代码"].isin(codes)]


def _fetch_stock_list_adata(codes: list[str]) -> pd.DataFrame:
    """Fetch real-time quotes for multiple stocks from adata."""
    import adata.stock.market as m

    return m.list_market_current(code_list=codes)


# Max ts_codes per ts.realtime_quote(src='sina') call. Sina serves multi-code
# in one round-trip, but chunking bounds the URL length for a large watchlist.
_SINA_BATCH = 50


def _fetch_stock_list_tushare_sina(codes: list[str]) -> pd.DataFrame:
    """Primary watchlist leg: Tushare ``realtime_quote(src='sina')`` batched.

    Maps each bare 6-digit code to a ts_code via :func:`_to_tushare_ts_code`
    (fail-closed). A universe-blocked / malformed code is **skipped** (logged
    at debug) rather than failing the whole batch, so one bad code never
    starves the rest of the watchlist. Valid ts_codes are comma-joined and
    fetched in chunks of :data:`_SINA_BATCH`; the concatenated raw frame
    (sina English-uppercase columns) is returned, or an empty frame when no
    code could be fetched. Row→:class:`StockQuote` mapping lives in
    :func:`_tushare_sina_row_to_quote` (P0-8-amendment-2026-06-03).

    Lazy-imports tushare to mirror the file's lazy-import discipline (akshare /
    adata also lazy) and spare every module-load site the Tushare stack when
    the sina path is never taken.
    """
    import tushare as ts  # lazy: see docstring

    ts_codes: list[str] = []
    for code in codes:
        try:
            ts_codes.append(_to_tushare_ts_code(code))
        except ValueError:
            # universe-blocked / malformed — skip this one code (fail-closed
            # for the code, not the batch); it simply gets no fresh quote.
            log.debug("watchlist_sina_skip_unmappable_code", code=code)
    if not ts_codes:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for i in range(0, len(ts_codes), _SINA_BATCH):
        chunk = ts_codes[i : i + _SINA_BATCH]
        df = ts.realtime_quote(ts_code=",".join(chunk), src="sina")
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _fetch_orderbook_adata(code: str) -> pd.DataFrame:
    """Fetch the five-level orderbook (五档) from adata (primary).

    ``get_market_five`` returns a single-row frame with ``s1``..``s5`` (ask
    prices, ``s1`` = 卖一 = best_ask), ``b1``..``b5`` (bid prices, ``b1`` =
    买一 = best_bid) and matching ``sv*`` / ``bv*`` volumes. It carries NO
    last print, so the orderbook's ``last`` is left ``None`` on this leg
    (the dual-source last comes from the spot quote, not the book).
    """
    import adata.stock.market as m

    return m.get_market_five(stock_code=code)


def _fetch_orderbook_akshare(code: str) -> pd.DataFrame:
    """Fallback: fetch the five-level orderbook from akshare.

    ``stock_bid_ask_em`` returns a long ``[item, value]`` frame whose rows
    include ``sell_1`` (best_ask), ``buy_1`` (best_bid) and ``最新`` (last).
    """
    import akshare

    return akshare.stock_bid_ask_em(symbol=code)


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


def _tushare_sina_row_to_quote(row: pd.Series) -> StockQuote:
    """Convert a Tushare ``realtime_quote(src='sina')`` row to :class:`StockQuote`.

    Sina returns a single row with English uppercase columns (see
    :func:`_fetch_stock_tushare_sina`). Mapping (amendment §2.1):

    * ``code`` ← last 6 digits of ``TS_CODE`` (``000021.SZ`` → ``000021``);
      a bare 6-digit ``TS_CODE`` passes through unchanged.
    * ``price`` ← ``PRICE`` coerced via :func:`_positive_or_none`; NaN / inf /
      non-positive (halted / pre-open) → :class:`ValueError`. The dual-source
      handler then logs ``dual_tushare_sina_failed`` and sets fallback=None,
      yielding the fail-closed missing-data degrade that P0-8 expects for a
      halted symbol (NOT a sham ``price=NaN`` divergence reading).
    * ``change_pct`` ← derived ``(PRICE - PRE_CLOSE) / PRE_CLOSE * 100``
      when ``PRE_CLOSE`` is a positive finite float; else ``0.0`` (informational
      field, not part of divergence).
    * ``turnover_rate`` ← ``0.0`` (sina row carries no turnover; field is
      informational on the dual-source view).
    * ``timestamp`` ← :func:`datetime.now` UTC at fetch time — mirrors the
      adata primary leg's fetch-time semantic so per-leg staleness comparisons
      see a consistent epoch. (Sina also carries an exchange-clock DATE+TIME
      header; capturing it would need a new field on :class:`StockQuote` and
      a P0-8 amendment — out of scope for this swap.)
    """
    ts_code = str(row.get("TS_CODE", "") or "")
    code = ts_code.rsplit(".", 1)[0] if "." in ts_code else ts_code

    price = _positive_or_none(row.get("PRICE"))
    if price is None:
        raise ValueError(
            f"sina row for {code!r} has no finite positive PRICE "
            f"(halted / pre-open / parse failure); fallback leg fail-closed"
        )

    prev_close = _positive_or_none(row.get("PRE_CLOSE")) or 0.0
    change_pct = (price - prev_close) / prev_close * 100.0 if prev_close else 0.0

    return StockQuote(
        code=code,
        name=str(row.get("NAME") or ""),
        price=price,
        open=_positive_or_none(row.get("OPEN")) or 0.0,
        high=_positive_or_none(row.get("HIGH")) or 0.0,
        low=_positive_or_none(row.get("LOW")) or 0.0,
        prev_close=prev_close,
        change_pct=change_pct,
        volume=float(row.get("VOLUME") or 0),
        amount=float(row.get("AMOUNT") or 0),
        turnover_rate=0.0,
        timestamp=datetime.now(tz=UTC),
    )


def _rows_to_quotes(
    df: pd.DataFrame | None,
    mapper: Callable[[pd.Series], StockQuote],
) -> list[StockQuote]:
    """Map each frame row to a :class:`StockQuote` via ``mapper``, skipping any
    row whose mapper raises.

    On the sina leg :func:`_tushare_sina_row_to_quote` raises ``ValueError`` for
    a halted / non-positive-PRICE symbol; skipping that one row degrades
    fail-closed for that code (it gets no fresh quote → its MTM degrades) WITHOUT
    starving the rest of the watchlist batch (P0-8-amendment-2026-06-03). An
    empty / None frame yields ``[]``.
    """
    if df is None or df.empty:
        return []
    quotes: list[StockQuote] = []
    for _, row in df.iterrows():
        try:
            quotes.append(mapper(row))
        except Exception as exc:  # halted / non-positive price → skip this code
            log.debug("watchlist_row_skipped", error=str(exc))
    return quotes


def _positive_or_none(value: object) -> float | None:
    """Coerce a vendor price cell to a positive FINITE float, else ``None``.

    A blank / zero / NaN / ``inf`` / non-numeric 卖一 (thin or halted book, or a
    pandas missing-cell representation) becomes ``None`` so the orderbook never
    advertises a non-finite or non-positive best_ask — the cage base must be a
    real price (U-E2 fail-closed). ``inf`` must map to ``None`` (not pass
    through): otherwise it would suppress the akshare fallback AND later blow up
    ``cage_bounded_buy_limit`` with a ValueError (codex/review U-E2).
    """
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:  # NaN / ±inf / non-positive
        return None
    return price


def _adata_five_to_orderbook(row: pd.Series) -> StockOrderbook:
    """Convert an adata ``get_market_five`` row to StockOrderbook.

    ``s1`` = 卖一 (best_ask), ``b1`` = 买一 (best_bid). adata's five-level
    frame carries no last print, so ``last`` is ``None`` on this leg.
    """
    return StockOrderbook(
        code=str(row.get("stock_code", "")),
        last=None,
        best_ask=_positive_or_none(row.get("s1")),
        best_bid=_positive_or_none(row.get("b1")),
        source="adata",
        ts=datetime.now(tz=UTC),
    )


def _akshare_bidask_to_orderbook(code: str, df: pd.DataFrame) -> StockOrderbook:
    """Convert an akshare ``stock_bid_ask_em`` long frame to StockOrderbook.

    The frame is ``[item, value]`` rows; ``sell_1`` = best_ask, ``buy_1`` =
    best_bid, ``最新`` = last. Missing rows degrade to ``None`` per field.
    """
    by_item = dict(zip(df["item"], df["value"], strict=False))
    return StockOrderbook(
        code=code,
        last=_positive_or_none(by_item.get("最新")),
        best_ask=_positive_or_none(by_item.get("sell_1")),
        best_bid=_positive_or_none(by_item.get("buy_1")),
        source="akshare",
        ts=datetime.now(tz=UTC),
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

        result = pd.DataFrame(
            {
                "date": df["日期"].astype(str),
                "open": pd.to_numeric(df.get("开盘", 0), errors="coerce").fillna(0),
                "high": pd.to_numeric(df.get("最高", 0), errors="coerce").fillna(0),
                "low": pd.to_numeric(df.get("最低", 0), errors="coerce").fillna(0),
                "close": pd.to_numeric(df.get("收盘", 0), errors="coerce").fillna(0),
                "volume": pd.to_numeric(df.get("成交量", 0), errors="coerce").fillna(0),
            }
        )
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
                raise DataFetchError(f"Both adata and akshare failed for stock {code}")

    async def get_stock_list_realtime(self, codes: list[str]) -> list[StockQuote]:
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

    async def get_stock_orderbook(self, code: str) -> StockOrderbook:
        """Fetch the five-level orderbook (best_ask/best_bid) for one stock.

        adata ``get_market_five`` primary, akshare ``stock_bid_ask_em``
        fallback (U-E2 / 缺口4). The primary leg is treated as failed — and
        the fallback fires — when it raises, returns an empty frame, OR yields
        no positive 卖一 (a thin / halted book): the price cage needs a real
        best_ask, so a book without one is no better than an outage. Raises
        :class:`DataFetchError` when neither leg yields a usable book; the
        Line-1 provider then degrades the lead to a non-actionable notice
        rather than pricing a BUY without a 卖一 reference.
        """
        primary_exc: Exception | None = None
        try:
            df = await asyncio.to_thread(_fetch_orderbook_adata, code)
            if df is not None and not df.empty:
                ob = _adata_five_to_orderbook(df.iloc[0])
                if ob.best_ask is not None:
                    return ob
                self._log.warning("orderbook_adata_no_ask", code=code)
        except Exception as exc:
            self._log.warning("orderbook_adata_failed", code=code, error=str(exc))
            primary_exc = exc

        try:
            df = await asyncio.to_thread(_fetch_orderbook_akshare, code)
        except Exception as exc:
            self._log.error(
                "orderbook_both_failed",
                code=code,
                primary_error=str(primary_exc) if primary_exc else "no_ask_or_empty",
                fallback_error=str(exc),
            )
            raise DataFetchError(
                f"Both adata and akshare failed for orderbook {code}"
            ) from exc
        if df is None or df.empty:
            raise DataFetchError(f"No orderbook data for {code}")
        # Honour the documented contract: a column-drifted akshare frame (no
        # ``item``/``value``) is an unusable book → DataFetchError, not a raw
        # KeyError leaking out of the converter (review U-E2).
        if "item" not in df.columns or "value" not in df.columns:
            raise DataFetchError(
                f"akshare orderbook for {code} missing item/value columns"
            )
        return _akshare_bidask_to_orderbook(code, df)

    async def get_stock_realtime_dual(
        self, code: str
    ) -> tuple[StockQuote | None, StockQuote | None]:
        """Fetch BOTH spot legs for the P0-8 divergence / staleness check.

        Returns a positional ``(primary, fallback)`` tuple — primary is the
        adata leg, fallback the **Tushare sina** leg (P0-8-amendment-2026-05-28;
        replaced the akshare ``stock_zh_a_spot_em()`` batch leg that eastmoney
        throttled with ``RemoteDisconnected``). Each leg is ``None`` if that
        source failed or returned nothing. Unlike :meth:`get_stock_realtime`
        (which collapses to a single quote), the Line-1 provider needs BOTH
        legs to run ``evaluate_divergence`` (≤0.3%) and degrade single-source.

        The two legs are now independent (different vendors, sina single-symbol
        instead of the old eastmoney 58-page batch) and run concurrently via
        :func:`asyncio.gather` — halves the per-symbol wall-clock and keeps
        more of the 5s staleness budget for actual divergence work.

        Program-side ``ValueError`` from the fallback leg (malformed code or
        forbidden universe prefix raised by :func:`classify_board`) is logged
        under a separate ``dual_fallback_input_error`` key so ops can tell a
        plumbing bug from a vendor outage — ``dual_tushare_sina_failed`` is
        reserved for sina/SDK/network failures.
        """
        primary_df, fallback_df = await asyncio.gather(
            asyncio.to_thread(_fetch_stock_adata, code),
            asyncio.to_thread(_fetch_stock_tushare_sina, code),
            return_exceptions=True,
        )

        primary: StockQuote | None = None
        if isinstance(primary_df, BaseException):
            self._log.warning("dual_adata_failed", code=code, error=str(primary_df))
        elif primary_df is not None and not primary_df.empty:
            primary = _adata_stock_row_to_quote(primary_df.iloc[0])

        fallback: StockQuote | None = None
        if isinstance(fallback_df, ValueError):
            # Program / plumbing bug — never silently look like a vendor outage.
            self._log.warning(
                "dual_fallback_input_error", code=code, error=str(fallback_df)
            )
        elif isinstance(fallback_df, BaseException):
            self._log.warning(
                "dual_tushare_sina_failed", code=code, error=str(fallback_df)
            )
        elif fallback_df is not None and not fallback_df.empty:
            try:
                fallback = _tushare_sina_row_to_quote(fallback_df.iloc[0])
            except ValueError as exc:
                # halted / pre-open sina row (no positive PRICE) → fail-closed
                self._log.warning("dual_tushare_sina_failed", code=code, error=str(exc))

        return primary, fallback

    async def get_watchlist_snapshot(
        self, codes: list[str], snapshot_at: datetime
    ) -> list[WatchlistMarketSnapshot]:
        """Return per-stock 30s snapshot for the active watchlist (C-003).

        Primary leg = Tushare ``realtime_quote(src='sina')`` (the canonical
        Sina realtime source — reachable + full OHLC/prev_close); fallback =
        adata ``list_market_current`` (P0-8-amendment-2026-06-03). The dead
        eastmoney akshare batch leg was removed: it is unreachable from the
        host, and adata's batch returned empty during trading-hours
        collection, which starved MTM + Line-2 monitoring since go-live. Each
        row is tagged with the leg that produced it (``tushare_sina`` /
        ``adata``) for provenance.

        Empty ``codes`` short-circuits to ``[]``. *Both* an exception **and**
        an empty / all-rows-skipped frame from the primary leg count as a
        primary failure (fall through to adata). Only when both legs yield no
        quotes does the method return ``[]``; only when both legs raise does it
        surface :class:`DataFetchError`. Per-row halted symbols are skipped,
        not fatal (see :func:`_rows_to_quotes`).
        """
        if not codes:
            return []

        actual_source: QuoteSource = "tushare_sina"
        primary_exc: Exception | None = None
        try:
            df = await asyncio.to_thread(_fetch_stock_list_tushare_sina, codes)
            quotes = _rows_to_quotes(df, _tushare_sina_row_to_quote)
        except Exception as exc:
            self._log.warning(
                "watchlist_snapshot_tushare_sina_failed", error=str(exc)
            )
            primary_exc = exc
            quotes = []

        # An empty (or all-rows-skipped) primary frame during trading hours is
        # also a primary failure — fall through to adata rather than returning
        # [] and starving DataQualityProvider / MTM / Line-2.
        if not quotes:
            actual_source = "adata"
            try:
                df = await asyncio.to_thread(_fetch_stock_list_adata, codes)
                quotes = _rows_to_quotes(df, _adata_stock_row_to_quote)
            except Exception as exc:
                # Raise only when BOTH legs failed exceptionally — a primary
                # empty + fallback exception is still an outage (no rows).
                self._log.error(
                    "watchlist_snapshot_both_failed",
                    primary_error=str(primary_exc) if primary_exc else "empty",
                    fallback_error=str(exc),
                )
                raise DataFetchError(
                    "Both tushare-sina and adata failed for watchlist snapshot"
                ) from exc

        if not quotes:
            return []

        return [
            WatchlistMarketSnapshot(
                code=q.code,
                name=q.name,
                price=q.price,
                open=q.open,
                high=q.high,
                low=q.low,
                prev_close=q.prev_close,
                change_pct=q.change_pct,
                volume=q.volume,
                amount=q.amount,
                turnover_rate=q.turnover_rate,
                source=actual_source,
                snapshot_at=snapshot_at,
            )
            for q in quotes
        ]

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
        north = float(latest.get("north_money", latest.get("当日资金流入", 0)))
        return CapitalFlowData(
            north_net_inflow=north,
            main_net_inflow=0.0,
            timestamp=datetime.now(tz=UTC),
        )
