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


QuoteSource = Literal["adata", "akshare", "unknown"]


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


class NewsArticle(BaseModel):
    """A financial news article."""

    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    source: str
    url: str
    publish_time: datetime
    stock_codes: tuple[str, ...] = ()
    importance_score: int = Field(default=0, ge=0, le=10)
