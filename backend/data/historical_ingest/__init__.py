"""AE-001 — offline bulk historical PIT ingestion (self-evolution data floor).

Governing decision: ``P0-8-amendment-2026-06-14-bulk-historical-pit-ingestion``
(+ R0 §3 PIT-reproducibility red line: store raw bytes + checksum + an
independent adjustment-factor pin). The system had ``kline_daily = 0`` — no
historical price data at all — so the quant-parameter evolution loop's
backtest had nothing to run on (dossier §11). This package adds an **offline
batch** job (never wired into the 13 runtime crons, never on the realtime
path) that pulls Tushare daily / adj_factor / daily_basic / fund_daily for
2015-present across the full market plus delisted codes (survivorship
bias-free) and persists each pull byte-exact into the K-002 ``SnapshotStore``.

Boundaries (red lines, see the amendment §4):
* Tushare official SDK only; never akshare 节假日 API; offline batch only.
* PIT storage: raw bytes + checksum + adjustment-factor pin (never hash-only,
  never "adjusted price only").
* Survivorship bias-free universe (delisted codes carry their listed-era
  history; excluded from the tradable set after their delist date).
* LLM never participates in ingestion/parsing (§2.5).
* The real multi-thousand-call run is **owner-gated**; this code ships with a
  small dry-run path (``scripts/ingest_historical_pit.py``).
"""

from __future__ import annotations

from backend.data.historical_ingest.adjust_view import reconstruct_adjusted_close
from backend.data.historical_ingest.calendar_provider import (
    StaticTradeCalendar,
    TradeCalendarProvider,
    TushareTradeCalendar,
)
from backend.data.historical_ingest.job import (
    HistoricalIngestJob,
    IngestReport,
    KlineRowWriter,
)
from backend.data.historical_ingest.rate_limiter import RateLimiter
from backend.data.historical_ingest.serialization import (
    canonical_csv_bytes,
    parse_csv_bytes,
)
from backend.data.historical_ingest.universe import (
    StockListing,
    SurvivorshipUniverse,
)

__all__ = [
    "HistoricalIngestJob",
    "IngestReport",
    "KlineRowWriter",
    "RateLimiter",
    "StaticTradeCalendar",
    "StockListing",
    "SurvivorshipUniverse",
    "TradeCalendarProvider",
    "TushareTradeCalendar",
    "canonical_csv_bytes",
    "parse_csv_bytes",
    "reconstruct_adjusted_close",
]
