"""O-002/O-005 MiroFish 17:00 EOD pipeline orchestration.

Order of operations per trade date (each step independently guarded —
a failure degrades that step and never blocks the rest):

1. **Score** (O-005, when a ledger is wired): settle due sector
   forecasts against realized sector returns; produce the trailing
   calibration note embedded in today's forecast evidence.
2. **Digest** (O-001): fetch deterministic inputs (full-market daily +
   industry map + index history + multi-domain news), enrich the top
   sectors with KG industry-chain adjacency, render the digest and
   persist the ``MARKET-DIGEST-`` / ``NEWS-DIGEST-`` evidence rows.
3. **Forecast** (O-002): reserve a ``cost_guard`` slot (dedup + daily
   cap + ¥100 true-reservation), call the LLM once, strict-parse, and
   persist the ``MIROFISH-FORECAST-`` evidence row (payload inside the
   evidence doc — evidence-only by construction).
4. **EOD review row**: the legacy ``MIROFISH-EOD-`` audit row that the
   cron fired (one per trade date, idempotent on the unique id).

The whole pipeline is advisory: it writes only ``evidence_collection``
documents; nothing here constructs an InstructionPlan, touches
RiskCheckSummary, or feeds the runtime market-data path.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

# Orchestration IS the decision-path layer (same rationale as
# line1_frame.py): the per-line noqa keeps the global TID251 ban active
# for true Phase X modules while letting this Line-1-adjacent runner
# compose the data + mirofish stacks it exists to orchestrate.
from backend.data.trading_calendar import prev_trading_day  # noqa: TID251
from backend.mirofish.digest_evidence import (  # noqa: TID251
    InfoDigestEvidenceWriter,
    build_market_digest_evidence,
    build_news_digest_evidence,
)
from backend.mirofish.info_digest import (  # noqa: TID251
    TOP_SECTOR_COUNT,
    DailyStockRow,
    IndexBar,
    InfoDigest,
    build_info_digest,
)
from backend.mirofish.output_writer import (  # noqa: TID251
    MiroFishEvidenceWriter,
    build_eod_evidence,
    build_sector_forecast_evidence,
)
from backend.services.cost_guard import (
    reserve_sector_forecast_slot,
    settle_budget,
)
from backend.utils.trading_hours import SHANGHAI, is_trading_day

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.news_crawler import NewsCrawlerService  # noqa: TID251
    from backend.data.tushare_client import TushareClient  # noqa: TID251
    from backend.mirofish.sector_forecast import (  # noqa: TID251
        SectorForecaster,
    )
    from backend.models.market import NewsArticle

log = structlog.get_logger(component="orchestration.mirofish_eod")

# Conservative per-forecast cost estimate for the true-reservation gate
# (digest ~4k chars in + ~2k out on the intelligence_officer model is
# well under this; the actual spend is tracked by the router).
ESTIMATED_FORECAST_RMB = 0.5

# Indices rendered in the digest trend section (domestic anchors).
DIGEST_INDEXES: tuple[tuple[str, str], ...] = (
    ("000001.SH", "上证指数"),
    ("000300.SH", "沪深300"),
    ("399006.SZ", "创业板指"),
)

# Trailing index history fetched for the trend section (calendar days —
# generous so 21+ trading bars survive holidays).
_INDEX_LOOKBACK_DAYS = 60

_NEWS_LIMIT = 80

# KG industry-chain adjacency: edges that define relatedness and node
# types whose names are rendered as "related sectors". Mirrors the
# Z-002 chain-subgraph definition (backend/api/theme_research.py).
_KG_RELATED_EDGE_TYPES = frozenset(
    {"UPSTREAM_OF", "DRIVES", "REQUIRES", "SUPPLIES_PRODUCT", "MEMBER_OF"}
)
_KG_RELATED_NODE_TYPES = frozenset({"Sector", "ChainLink", "Product"})
_KG_RELATED_CAP = 6


class DigestInputsError(RuntimeError):
    """Raised by a provider when no trustworthy digest inputs exist."""


@dataclass(frozen=True)
class DigestInputs:
    """Everything :func:`build_info_digest` needs for one trade date."""

    trade_date: str  # effective YYYY-MM-DD (may be the prev trading day)
    index_bars: tuple[IndexBar, ...]
    daily_rows: tuple[DailyStockRow, ...]
    industry_by_code: Mapping[str, str]
    news: tuple[NewsArticle, ...]


class DigestInputsProvider(Protocol):
    """Async source of digest inputs (live Tushare/news or test fixture)."""

    async def fetch(self, requested_date: str) -> DigestInputs: ...


class ForecastCalibrationLedger(Protocol):
    """O-005 hook: score due forecasts, return the trailing-stats note."""

    async def score_due_and_summarize(self, as_of: str) -> str: ...


RelatedSectorsProvider = Callable[
    [tuple[str, ...]], Awaitable[Sequence[tuple[str, tuple[str, ...]]]]
]


# ---------------------------------------------------------------------------
# KG industry-chain adjacency (deterministic, read-only)
# ---------------------------------------------------------------------------


def kg_related_sectors(
    db_path: Path | str,
    sectors: tuple[str, ...],
    *,
    cap: int = _KG_RELATED_CAP,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Related sectors via KG chain-edge adjacency (exact-name match).

    Deterministic, read-only, best-effort: any failure (missing DB,
    schema drift) returns ``()`` — the digest renders "no graph data"
    and never blocks. Sync on purpose (SQLite); callers off the event
    loop should wrap in ``asyncio.to_thread``.
    """
    try:
        from backend.knowledge_graph.store import SqliteKGStore

        path = Path(db_path)
        if not path.is_file():
            return ()
        store = SqliteKGStore(path)
        try:
            graph = store.to_networkx()
        finally:
            store.close()

        nodes_by_name: dict[str, list[str]] = {}
        for node_id, data in graph.nodes(data=True):
            nodes_by_name.setdefault(str(data.get("name", "")), []).append(
                node_id
            )

        results: list[tuple[str, tuple[str, ...]]] = []
        for sector in sectors:
            related: set[str] = set()
            for node_id in nodes_by_name.get(sector, []):
                edge_iter = list(graph.out_edges(node_id, data=True)) + [
                    (v, u, d) for u, v, d in graph.in_edges(node_id, data=True)
                ]
                for _, neighbor, data in edge_iter:
                    if data.get("edge_type") not in _KG_RELATED_EDGE_TYPES:
                        continue
                    ndata = graph.nodes[neighbor]
                    if ndata.get("node_type") not in _KG_RELATED_NODE_TYPES:
                        continue
                    name = str(ndata.get("name", ""))
                    if name and name != sector:
                        related.add(name)
            if related:
                results.append((sector, tuple(sorted(related))[:cap]))
        return tuple(results)
    except Exception as exc:  # noqa: BLE001 — best-effort enrichment
        log.warning("kg_related_sectors_failed", error=str(exc))
        return ()


# ---------------------------------------------------------------------------
# Live inputs provider (Tushare + news crawler)
# ---------------------------------------------------------------------------


class LiveDigestInputsProvider:
    """Assemble digest inputs from Tushare pulls + the news crawler.

    If the requested date is not a trading day, or its full-market
    ``daily`` frame is still empty (vendor lag), the provider falls back
    to the previous trading day exactly once — the digest is then a
    re-render of an existing date and the evidence writes de-dup on the
    unique id (idempotent).
    """

    def __init__(
        self,
        *,
        tushare_factory: Callable[[], TushareClient],
        news_crawler: NewsCrawlerService,
    ) -> None:
        self._tushare_factory = tushare_factory
        self._news_crawler = news_crawler
        self._log = log

    async def fetch(self, requested_date: str) -> DigestInputs:
        client = self._tushare_factory()
        target = dt.date.fromisoformat(requested_date)
        if not is_trading_day(target):
            target = prev_trading_day(target)

        daily_df = await client.daily(target.strftime("%Y%m%d"))
        if daily_df is None or daily_df.empty:
            target = prev_trading_day(target)
            daily_df = await client.daily(target.strftime("%Y%m%d"))
        if daily_df is None or daily_df.empty:
            raise DigestInputsError(
                f"no full-market daily rows on/before {requested_date}"
            )

        basic_df = await client.stock_basic(fields="ts_code,industry")
        index_bars = await self._fetch_index_bars(client, target)
        news = tuple(
            await self._news_crawler.fetch_latest_news(
                limit=_NEWS_LIMIT, include_cctv=True
            )
        )

        rows: list[DailyStockRow] = []
        for record in daily_df.itertuples(index=False):
            code = str(getattr(record, "ts_code", "") or "")
            pct = _finite(getattr(record, "pct_chg", None))
            amount = _finite(getattr(record, "amount", None))
            if not code or pct is None:
                continue
            rows.append(
                DailyStockRow(
                    code=code, pct_chg=pct, amount=amount if amount else 0.0
                )
            )

        industry: dict[str, str] = {}
        if basic_df is not None and not basic_df.empty:
            for record in basic_df.itertuples(index=False):
                code = str(getattr(record, "ts_code", "") or "")
                sector = getattr(record, "industry", None)
                if code and isinstance(sector, str) and sector.strip():
                    industry[code] = sector.strip()

        return DigestInputs(
            trade_date=target.isoformat(),
            index_bars=index_bars,
            daily_rows=tuple(rows),
            industry_by_code=industry,
            news=news,
        )

    async def _fetch_index_bars(
        self, client: TushareClient, target: dt.date
    ) -> tuple[IndexBar, ...]:
        start = (target - dt.timedelta(days=_INDEX_LOOKBACK_DAYS)).strftime(
            "%Y%m%d"
        )
        end = target.strftime("%Y%m%d")
        bars: list[IndexBar] = []
        for code, name in DIGEST_INDEXES:
            try:
                df = await client.index_daily(
                    code, start_date=start, end_date=end
                )
            except Exception as exc:  # noqa: BLE001 — per-index degrade
                self._log.warning(
                    "digest_index_fetch_failed", code=code, error=str(exc)
                )
                continue
            if df is None or df.empty:
                continue
            try:
                ordered = df.sort_values("trade_date")
                closes = tuple(
                    c
                    for c in (
                        _finite(v) for v in ordered["close"].tolist()
                    )
                    if c is not None and c > 0
                )
            except Exception as exc:  # noqa: BLE001 — malformed frame
                self._log.warning(
                    "digest_index_frame_malformed", code=code, error=str(exc)
                )
                continue
            if closes:
                bars.append(IndexBar(code=code, name=name, closes=closes))
        return tuple(bars)


def _finite(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class MiroFishEodRunner:
    """Drives the 17:00 EOD digest → forecast → audit-row pipeline."""

    def __init__(
        self,
        *,
        inputs_provider: DigestInputsProvider,
        digest_writer: InfoDigestEvidenceWriter,
        mirofish_writer: MiroFishEvidenceWriter,
        forecaster: SectorForecaster | None = None,
        redis_client: redis.asyncio.Redis | None = None,
        related_sectors_provider: RelatedSectorsProvider | None = None,
        ledger: ForecastCalibrationLedger | None = None,
        industry_map_sink: Callable[[str, Mapping[str, str]], None] | None = None,
        now_fn: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._inputs_provider = inputs_provider
        self._digest_writer = digest_writer
        self._mirofish_writer = mirofish_writer
        self._forecaster = forecaster
        self._redis = redis_client
        self._related_provider = related_sectors_provider
        self._ledger = ledger
        # O-003 PIT pin: persist the exact code→sector map this digest used,
        # co-dated with the forecast, so the next morning's advisory re-rank
        # is replayable (loads the same map by date, not a fresh live fetch).
        self._industry_map_sink = industry_map_sink
        self._now_fn = now_fn or (lambda: dt.datetime.now(tz=SHANGHAI))
        self._log = log

    async def run(self) -> None:
        """Execute the pipeline once; every step degrades independently."""
        requested = self._now_fn().astimezone(SHANGHAI).date().isoformat()

        calibration_note = await self._score_due_forecasts(requested)
        digest = await self._build_and_persist_digest(requested)
        if digest is not None:
            await self._forecast_and_persist(digest, calibration_note)
        await self._write_eod_review_row(
            digest.trade_date if digest is not None else requested
        )

    # -- step 1: O-005 calibration -------------------------------------------

    async def _score_due_forecasts(self, as_of: str) -> str:
        if self._ledger is None:
            return ""
        try:
            return await self._ledger.score_due_and_summarize(as_of)
        except Exception as exc:  # noqa: BLE001 — scoring never blocks
            self._log.warning("forecast_ledger_failed", error=str(exc))
            return ""

    # -- step 2: digest --------------------------------------------------------

    async def _build_and_persist_digest(
        self, requested: str
    ) -> InfoDigest | None:
        try:
            inputs = await self._inputs_provider.fetch(requested)
        except Exception as exc:  # noqa: BLE001 — degrade to no digest
            self._log.warning("digest_inputs_failed", error=str(exc))
            return None

        try:
            digest = build_info_digest(
                trade_date=inputs.trade_date,
                index_bars=inputs.index_bars,
                daily_rows=inputs.daily_rows,
                industry_by_code=inputs.industry_by_code,
                news=inputs.news,
            )
            related = await self._related_for(digest)
            if related:
                digest = build_info_digest(
                    trade_date=inputs.trade_date,
                    index_bars=inputs.index_bars,
                    daily_rows=inputs.daily_rows,
                    industry_by_code=inputs.industry_by_code,
                    news=inputs.news,
                    related_sectors=related,
                )
        except Exception as exc:  # noqa: BLE001 — degrade to no digest
            self._log.warning("digest_build_failed", error=str(exc))
            return None

        # O-003 PIT pin: persist the code→sector map this digest used,
        # co-dated with the forecast, BEFORE the forecast is written so a
        # later replay loads the same map by date (best-effort; a sink
        # failure never blocks the digest/forecast).
        if self._industry_map_sink is not None and inputs.industry_by_code:
            try:
                self._industry_map_sink(
                    digest.trade_date, inputs.industry_by_code
                )
            except Exception as exc:  # noqa: BLE001 — pin is best-effort
                self._log.warning("industry_map_pin_failed", error=str(exc))

        for builder in (build_market_digest_evidence, build_news_digest_evidence):
            try:
                await self._digest_writer.write(builder(digest))
            except Exception as exc:  # noqa: BLE001 — evidence best-effort
                self._log.warning(
                    "digest_evidence_write_failed", error=str(exc)
                )
        return digest

    async def _related_for(
        self, digest: InfoDigest
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        if self._related_provider is None or not digest.sector_heat:
            return ()
        top = digest.sector_names[:TOP_SECTOR_COUNT]
        try:
            related = await self._related_provider(top)
        except Exception as exc:  # noqa: BLE001 — enrichment only
            self._log.warning("related_sectors_failed", error=str(exc))
            return ()
        return tuple((sector, tuple(names)) for sector, names in related)

    # -- step 3: forecast -------------------------------------------------------

    async def _forecast_and_persist(
        self, digest: InfoDigest, calibration_note: str
    ) -> None:
        if self._forecaster is None or self._redis is None:
            return
        reservation = await reserve_sector_forecast_slot(
            self._redis,
            trigger_key=digest.trade_date,
            estimated_rmb=ESTIMATED_FORECAST_RMB,
        )
        if reservation is None:
            self._log.info(
                "sector_forecast_skipped_by_gate",
                trade_date=digest.trade_date,
            )
            return
        try:
            forecast = await self._forecaster.forecast(digest)
        finally:
            await settle_budget(self._redis, reservation)
        if forecast is None:
            self._log.warning(
                "sector_forecast_degraded", trade_date=digest.trade_date
            )
            return
        try:
            await self._mirofish_writer.write(
                build_sector_forecast_evidence(
                    forecast, calibration_note=calibration_note
                )
            )
        except Exception as exc:  # noqa: BLE001 — evidence best-effort
            self._log.warning(
                "forecast_evidence_write_failed",
                trade_date=digest.trade_date,
                error=str(exc),
            )

    # -- step 4: legacy EOD audit row -------------------------------------------

    async def _write_eod_review_row(self, trade_date: str) -> None:
        try:
            await self._mirofish_writer.write(
                build_eod_evidence(events=(), trade_date=trade_date)
            )
        except Exception as exc:  # noqa: BLE001 — audit row best-effort
            self._log.warning(
                "eod_review_row_failed", trade_date=trade_date, error=str(exc)
            )


def build_kg_related_sectors_provider(
    db_path: Path | str,
) -> RelatedSectorsProvider:
    """Async adapter over :func:`kg_related_sectors` (thread off the loop)."""

    async def _provider(
        sectors: tuple[str, ...],
    ) -> Sequence[tuple[str, tuple[str, ...]]]:
        return await asyncio.to_thread(kg_related_sectors, db_path, sectors)

    return _provider


__all__ = [
    "DIGEST_INDEXES",
    "DigestInputs",
    "DigestInputsError",
    "DigestInputsProvider",
    "ESTIMATED_FORECAST_RMB",
    "ForecastCalibrationLedger",
    "LiveDigestInputsProvider",
    "MiroFishEodRunner",
    "build_kg_related_sectors_provider",
    "kg_related_sectors",
]
