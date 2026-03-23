"""Frozen Pydantic models for market data, financial data, and news."""

from __future__ import annotations

from datetime import datetime

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
