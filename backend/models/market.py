"""Frozen Pydantic models for market data, financial data, and news."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IndexQuote(BaseModel):
    """Real-time quote for a market index (e.g. Shanghai Composite)."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    timestamp: datetime


class StockQuote(BaseModel):
    """Real-time quote for a single stock."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    change_pct: float
    volume: float
    amount: float
    turnover_rate: float
    timestamp: datetime


# "tushare_sina" added by P0-8-amendment-2026-06-03: get_watchlist_snapshot's
# primary leg is ts.realtime_quote(src='sina'). Additive — no downstream branch
# keys on the watchlist source value (staleness tag is provenance-only; MTM
# reads price/timestamp; data_quality_probes' source=="adata" is the decoupled
# single-stock dual-leg selector).
QuoteSource = Literal["adata", "akshare", "tushare_sina", "unknown"]


class StockOrderbook(BaseModel):
    """Five-level orderbook depth snapshot (U-E2 / 缺口4 price-cage input).

    The A-share continuous-auction price cage (价格笼子) is computed against
    the current 卖一 (lowest ask). :class:`StockQuote` only carries a last
    print + OHLC, so a dedicated orderbook fetch supplies ``best_ask`` (and
    ``best_bid`` for context). The deterministic
    :func:`backend.risk.price_cage.cage_bounded_buy_limit` consumes ``best_ask``
    to derive the BUY 限价上限 the operator sees.

    Fields are nullable because a vendor may omit a leg (e.g. adata's
    ``get_market_five`` returns no last print, and a thin book may have an
    empty 卖一). A ``None`` ``best_ask`` is a fail-closed signal upstream:
    the Line-1 provider degrades the lead to a non-actionable notice rather
    than pricing a BUY without a 卖一 reference (U-E2 §2.0). Frozen + strict +
    ``extra='forbid'`` per P0-3 §2 redline 12 so an LLM-routed field can never
    sneak in.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    last: float | None
    best_ask: float | None
    best_bid: float | None
    source: QuoteSource
    ts: datetime


class WatchlistMarketSnapshot(BaseModel):
    """Per-stock 30s snapshot row persisted to ``watchlist_market_snapshots``.

    P0-8 §1.1 requires a per-stock tick every 30s during trading hours for the
    full watchlist (13 codes) so :class:`backend.data.data_quality.DataQualityProvider`
    can compute staleness / divergence / missing-rate per code (acceptance
    target ≤1% missing). The schema is intentionally narrower than
    :class:`StockQuote` because:

    * ``source`` records which data leg produced the row (adata primary /
      akshare fallback / unknown). The DataQualityProvider 14-check view
      uses it for the divergence ≤0.3% check and the fail-closed
      ``data_unavailable`` branch.
    * ``snapshot_at`` is the scheduler tick timestamp (UTC, tz-aware) and
      is the unique key with ``code`` in Mongo — re-runs of the same tick
      are idempotent.

    Frozen + strict + ``extra='forbid'`` per P0-3 §2 redline 12 so a stray
    LLM-routed field can never sneak through.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=0, max_length=64)
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    change_pct: float
    volume: float
    amount: float
    turnover_rate: float
    source: QuoteSource
    snapshot_at: datetime


class SectorQuote(BaseModel):
    """Performance summary for a market sector."""

    model_config = ConfigDict(frozen=True)

    name: str
    change_pct: float
    leader_code: str
    leader_name: str
    leader_change_pct: float
    timestamp: datetime


class CapitalFlowData(BaseModel):
    """Northbound capital flow and main capital flow snapshot."""

    model_config = ConfigDict(frozen=True)

    north_net_inflow: float
    main_net_inflow: float
    timestamp: datetime


class FinancialData(BaseModel):
    """Financial indicators for a stock."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    roe: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None
    report_date: str
    timestamp: datetime


NewsDomain = Literal["financial", "political", "global"]
"""C-005 / P0-8 §1.2 three-domain partition.

* ``financial`` — eastmoney + cls (財經)
* ``political`` — cctv (時政)
* ``global``    — stock_info_global_em + sina (全球)

The partition is the unit of deduplication: within one domain, identical
URLs collapse; across domains the same story is preserved because the
multi-domain echo is itself MiroFish input (P0-8 §1.2). Adding a sixth
source or fourth domain requires a P0-8-amendment-*.md before any code
change.
"""

NewsSource = Literal[
    "eastmoney",
    "cls",
    "cctv",
    "global_em",
    "global_sina",
    "unknown",
]
"""Locked 5-source allowlist for C-005 with one ``unknown`` escape hatch.

``unknown`` covers legacy ``news_articles`` rows persisted before C-005
landed; the dedupe and domain-routing code treats ``unknown`` as a
read-only legacy bucket so the parser does not invent a domain for it.
"""

# Maps each ``NewsSource`` to the ``NewsDomain`` it belongs in. Single
# source of truth used by ``news_crawler`` to tag articles and by
# ``news_dedupe`` to enforce within-domain-only collapse.
NEWS_SOURCE_TO_DOMAIN: dict[NewsSource, NewsDomain] = {
    "eastmoney": "financial",
    "cls": "financial",
    "cctv": "political",
    "global_em": "global",
    "global_sina": "global",
}


class NewsArticle(BaseModel):
    """A news article tagged with its source and domain.

    Cross-domain duplicates (same URL, different domain) are preserved
    on purpose: the multi-domain echo of a single event is itself a
    MiroFish input signal (P0-8 §1.2). Within-domain dedupe lives in
    :mod:`backend.data.news_dedupe`.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    source: str
    url: str
    publish_time: datetime
    stock_codes: tuple[str, ...] = ()
    importance_score: int = Field(default=0, ge=0, le=10)
    # C-005: ``domain`` defaults to ``financial`` so pre-C-005 Mongo
    # rows (eastmoney-only) round-trip without migration. New rows
    # must set ``domain`` explicitly; the crawler sets it via
    # ``NEWS_SOURCE_TO_DOMAIN`` lookup.
    domain: NewsDomain = "financial"
