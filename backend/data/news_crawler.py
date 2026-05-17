"""News crawler service for Chinese financial news sources.

C-005 widens the source set to five akshare-backed feeds spread across
three domains (P0-8 §1.2):

* financial:  ``stock_news_em``           (eastmoney)
* financial:  ``stock_info_global_cls``   (cls)
* political:  ``news_cctv``               (cctv, 6h cadence)
* global:     ``stock_info_global_em``    (global_em)
* global:     ``stock_info_global_sina``  (global_sina)

Each per-source fetcher is wrapped in a tolerant helper (``_safe_*``)
that classifies upstream regressions as info, real failures as warnings,
and *always* returns an empty DataFrame so the async fan-out keeps
running. Within-domain dedupe collapses identical URLs; cross-domain
duplicates are preserved on purpose (P0-8 §1.2: multi-domain echo is
itself a MiroFish input signal).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pandas as pd
import structlog

from backend.data.config import DataSourcesConfig
from backend.data.news_dedupe import dedupe_within_domain
from backend.models.market import (
    NEWS_SOURCE_TO_DOMAIN,
    NewsArticle,
    NewsDomain,
    NewsSource,
)

log = structlog.get_logger(component="news_crawler")

# Regex for 6-digit A-share stock codes in content
_STOCK_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")

# Eastmoney akshare columns; reused as the fallback schema when upstream
# regresses so downstream parsing is stable.
_EXPECTED_NEWS_COLUMNS: list[str] = [
    "新闻标题",
    "新闻内容",
    "新闻链接",
    "发布时间",
]

# CLS / global_em / sina / cctv each return a different column schema.
# We keep an explicit per-source fallback column list so the safe
# wrappers can hand the parser a well-typed empty frame.
_EXPECTED_CLS_COLUMNS: list[str] = ["标题", "内容", "发布日期", "发布时间"]
_EXPECTED_GLOBAL_EM_COLUMNS: list[str] = ["标题", "摘要", "发布时间", "链接"]
_EXPECTED_GLOBAL_SINA_COLUMNS: list[str] = ["时间", "内容"]
_EXPECTED_CCTV_COLUMNS: list[str] = ["date", "title", "content"]


# ---------------------------------------------------------------------------
# Low-level fetchers
# ---------------------------------------------------------------------------


def _fetch_news_eastmoney() -> pd.DataFrame:
    """Fetch latest financial news via akshare's eastmoney interface (sync)."""
    import akshare

    return akshare.stock_news_em(symbol="")


def _safe_fetch_news_eastmoney() -> pd.DataFrame:
    """Tolerant wrapper around ``_fetch_news_eastmoney``.

    akshare's upstream raises ``KeyError('result')`` when its server returns
    an unexpected payload for empty-symbol queries — observed every 5 min
    in production logs. We treat that exact key as "no news" (info-level
    log) and return an empty DataFrame with the expected columns. Other
    ``KeyError`` keys are re-raised so genuine schema bugs stay visible.
    Network and parsing failures degrade to a warning + empty DataFrame
    so the scheduler keeps running.
    """
    try:
        return _fetch_news_eastmoney()
    except KeyError as exc:
        if exc.args == ("result",):
            log.info("eastmoney_empty_payload", reason="upstream_regression")
            return pd.DataFrame(columns=_EXPECTED_NEWS_COLUMNS)
        raise
    except Exception as exc:
        log.warning("eastmoney_news_failed", error=str(exc))
        return pd.DataFrame(columns=_EXPECTED_NEWS_COLUMNS)


def _fetch_stock_news_akshare(code: str) -> pd.DataFrame:
    """Fetch news for a specific stock via akshare (sync)."""
    import akshare

    return akshare.stock_news_em(symbol=code)


def _fetch_news_cls() -> pd.DataFrame:
    """Fetch financial-flash news from CLS (财联社) via akshare."""
    import akshare

    return akshare.stock_info_global_cls(symbol="财联社")


def _safe_fetch_news_cls() -> pd.DataFrame:
    try:
        return _fetch_news_cls()
    except Exception as exc:
        log.warning("cls_news_failed", error=str(exc))
        return pd.DataFrame(columns=_EXPECTED_CLS_COLUMNS)


def _fetch_news_global_em() -> pd.DataFrame:
    """Fetch global financial flashes from eastmoney via akshare."""
    import akshare

    return akshare.stock_info_global_em()


def _safe_fetch_news_global_em() -> pd.DataFrame:
    try:
        return _fetch_news_global_em()
    except Exception as exc:
        log.warning("global_em_news_failed", error=str(exc))
        return pd.DataFrame(columns=_EXPECTED_GLOBAL_EM_COLUMNS)


def _fetch_news_global_sina() -> pd.DataFrame:
    """Fetch global flashes from Sina via akshare."""
    import akshare

    return akshare.stock_info_global_sina()


def _safe_fetch_news_global_sina() -> pd.DataFrame:
    try:
        return _fetch_news_global_sina()
    except Exception as exc:
        log.warning("global_sina_news_failed", error=str(exc))
        return pd.DataFrame(columns=_EXPECTED_GLOBAL_SINA_COLUMNS)


def _fetch_news_cctv(date_str: str) -> pd.DataFrame:
    """Fetch CCTV news for a given ``YYYYMMDD`` date via akshare."""
    import akshare

    return akshare.news_cctv(date=date_str)


def _safe_fetch_news_cctv(date_str: str) -> pd.DataFrame:
    try:
        return _fetch_news_cctv(date_str)
    except Exception as exc:
        log.warning("cctv_news_failed", error=str(exc))
        return pd.DataFrame(columns=_EXPECTED_CCTV_COLUMNS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_stock_codes(text: str) -> tuple[str, ...]:
    """Extract A-share stock codes from text content."""
    matches = _STOCK_CODE_RE.findall(text)
    return tuple(sorted(set(matches)))


def _parse_dt_lenient(value: object) -> datetime:
    """Best-effort parse of a vendor timestamp, falling back to ``now``.

    Vendors return inconsistent ISO / ``YYYY-MM-DD HH:MM:SS`` / Chinese
    formats. We try ``fromisoformat`` first, then ``pandas.to_datetime``,
    and finally fall back to ``datetime.now(UTC)`` so a malformed cell
    never derails the whole parse.
    """
    text = str(value)
    if not text or text == "nan":
        return datetime.now(tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    except (ValueError, TypeError):
        try:
            ts = pd.to_datetime(text, errors="raise")
            py = ts.to_pydatetime()
            if py.tzinfo is None:
                return py.replace(tzinfo=UTC)
            return py
        except Exception:
            return datetime.now(tz=UTC)


def _build_article(
    *,
    title: str,
    content: str,
    url: str,
    publish_time: datetime,
    source: NewsSource,
) -> NewsArticle:
    """Construct a :class:`NewsArticle` with domain auto-derived from source."""
    stock_codes = _extract_stock_codes(f"{title} {content}")
    domain = NEWS_SOURCE_TO_DOMAIN[source]
    return NewsArticle(
        title=title,
        content=content,
        source=source,
        url=url,
        publish_time=publish_time,
        stock_codes=stock_codes,
        importance_score=0,
        domain=domain,
    )


# ---------------------------------------------------------------------------
# Per-source parsers
# ---------------------------------------------------------------------------


def _parse_news_df(
    df: pd.DataFrame, source: NewsSource = "eastmoney"
) -> list[NewsArticle]:
    """Convert an eastmoney news DataFrame to list of NewsArticle models."""
    articles: list[NewsArticle] = []
    seen_urls: set[str] = set()

    for _, row in df.iterrows():
        url = str(row.get("新闻链接", row.get("url", "")))
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = str(row.get("新闻标题", row.get("title", "")))
        content = str(row.get("新闻内容", row.get("content", "")))
        pub = _parse_dt_lenient(row.get("发布时间", row.get("publish_time", "")))

        articles.append(
            _build_article(
                title=title,
                content=content,
                url=url,
                publish_time=pub,
                source=source,
            )
        )

    return articles


def _parse_cls_df(df: pd.DataFrame) -> list[NewsArticle]:
    """Parse the CLS (财联社) flash schema."""
    articles: list[NewsArticle] = []
    for idx, row in df.iterrows():
        title = str(row.get("标题", row.get("title", "")))
        content = str(row.get("内容", row.get("content", "")))
        date_part = str(row.get("发布日期", ""))
        time_part = str(row.get("发布时间", ""))
        url = str(row.get("链接", row.get("url", "")))
        # CLS often omits a URL — synthesise a stable key so two
        # identical flashes from the same minute collapse but distinct
        # rows survive.
        if not url:
            url = f"cls://{date_part}-{time_part}-{idx}"
        composite = (
            f"{date_part} {time_part}".strip()
            if (date_part or time_part)
            else ""
        )
        pub = _parse_dt_lenient(composite) if composite else datetime.now(tz=UTC)
        articles.append(
            _build_article(
                title=title,
                content=content,
                url=url,
                publish_time=pub,
                source="cls",
            )
        )
    return articles


def _parse_global_em_df(df: pd.DataFrame) -> list[NewsArticle]:
    """Parse the eastmoney global-flash schema."""
    articles: list[NewsArticle] = []
    for idx, row in df.iterrows():
        title = str(row.get("标题", row.get("title", "")))
        content = str(row.get("摘要", row.get("content", "")))
        url = str(row.get("链接", row.get("url", "")))
        pub = _parse_dt_lenient(row.get("发布时间", ""))
        if not url:
            url = f"global_em://{pub.isoformat()}-{idx}"
        articles.append(
            _build_article(
                title=title,
                content=content,
                url=url,
                publish_time=pub,
                source="global_em",
            )
        )
    return articles


def _parse_global_sina_df(df: pd.DataFrame) -> list[NewsArticle]:
    """Parse the Sina global-flash schema (no native URL field)."""
    articles: list[NewsArticle] = []
    for idx, row in df.iterrows():
        content = str(row.get("内容", row.get("content", "")))
        title = content[:32] if content else ""
        pub = _parse_dt_lenient(row.get("时间", ""))
        # Sina flashes have no canonical URL — synthesise one keyed on
        # the timestamp + row index so within-domain dedupe still
        # catches the obvious duplicates.
        url = f"global_sina://{pub.isoformat()}-{idx}"
        articles.append(
            _build_article(
                title=title,
                content=content,
                url=url,
                publish_time=pub,
                source="global_sina",
            )
        )
    return articles


def _parse_cctv_df(df: pd.DataFrame) -> list[NewsArticle]:
    """Parse the CCTV news schema."""
    articles: list[NewsArticle] = []
    for idx, row in df.iterrows():
        title = str(row.get("title", ""))
        content = str(row.get("content", ""))
        date_part = str(row.get("date", ""))
        pub = _parse_dt_lenient(date_part) if date_part else datetime.now(tz=UTC)
        # CCTV API has no URL field; synthesise from date + index.
        url = f"cctv://{date_part}-{idx}"
        articles.append(
            _build_article(
                title=title,
                content=content,
                url=url,
                publish_time=pub,
                source="cctv",
            )
        )
    return articles


# ---------------------------------------------------------------------------
# NewsCrawlerService
# ---------------------------------------------------------------------------


_DomainFanIn = tuple[
    NewsSource,
    Callable[[], Awaitable[list[NewsArticle]]],
]


class NewsCrawlerService:
    """Async service for fetching multi-domain Chinese news.

    Five sources fan out concurrently (P0-8 §1.2 footprint): financial
    eastmoney + cls, political cctv, global eastmoney + sina. Each
    source has its own tolerant wrapper so a single vendor outage does
    not collapse the rest of the pipeline. Within-domain duplicates
    collapse on URL; cross-domain duplicates of the same story are
    preserved as a MiroFish signal.
    """

    def __init__(self, config: DataSourcesConfig) -> None:
        self._config = config
        self._log = log

    async def fetch_latest_news(
        self, limit: int = 50, *, include_cctv: bool = False
    ) -> list[NewsArticle]:
        """Fetch latest news from all configured sources.

        ``include_cctv`` is opt-in because the CCTV feed only refreshes
        once every 6 hours (P0-8 §1.2) — the 5-min scheduler tick uses
        the default ``False`` and a separate 6h cron pulls CCTV.

        Returns articles sorted by ``publish_time`` desc, capped at
        ``limit`` after within-domain dedupe.
        """
        fan_in: list[_DomainFanIn] = [
            ("eastmoney", self._fetch_eastmoney),
            ("cls", self._fetch_cls),
            ("global_em", self._fetch_global_em),
            ("global_sina", self._fetch_global_sina),
        ]
        if include_cctv:
            fan_in.append(("cctv", self._fetch_cctv_today))

        results = await asyncio.gather(
            *(coro() for _, coro in fan_in),
            return_exceptions=True,
        )

        all_articles: list[NewsArticle] = []
        for (source, _), result in zip(fan_in, results):
            if isinstance(result, Exception):
                self._log.warning(
                    "news_source_failed",
                    source=source,
                    error=str(result),
                )
                continue
            all_articles.extend(result)

        unique = dedupe_within_domain(all_articles)
        unique.sort(key=lambda a: a.publish_time, reverse=True)
        return unique[:limit]

    async def fetch_cctv(self, date: datetime | None = None) -> list[NewsArticle]:
        """Fetch CCTV news for a given date (defaults to today UTC).

        The scheduler calls this on its own 6h cadence rather than every
        news-tick, matching the upstream refresh interval.
        """
        target = (date or datetime.now(tz=UTC)).strftime("%Y%m%d")
        try:
            df = await asyncio.to_thread(_safe_fetch_news_cctv, target)
            articles = _parse_cctv_df(df)
            articles.sort(key=lambda a: a.publish_time, reverse=True)
            return articles
        except Exception as exc:
            self._log.warning("cctv_news_pipeline_failed", error=str(exc))
            return []

    async def fetch_stock_news(
        self, code: str, limit: int = 20
    ) -> list[NewsArticle]:
        """Fetch news related to a specific stock from eastmoney."""
        try:
            df = await asyncio.to_thread(_fetch_stock_news_akshare, code)
            articles = _parse_news_df(df, source="eastmoney")
            articles.sort(key=lambda a: a.publish_time, reverse=True)
            return articles[:limit]
        except Exception as exc:
            self._log.warning(
                "stock_news_failed", code=code, error=str(exc)
            )
            return []

    # --- per-source helpers (kept on the class so tests can patch
    # them independently of the asyncio.to_thread plumbing) ---

    async def _fetch_eastmoney(self) -> list[NewsArticle]:
        df = await asyncio.to_thread(_safe_fetch_news_eastmoney)
        return _parse_news_df(df, source="eastmoney")

    async def _fetch_cls(self) -> list[NewsArticle]:
        df = await asyncio.to_thread(_safe_fetch_news_cls)
        return _parse_cls_df(df)

    async def _fetch_global_em(self) -> list[NewsArticle]:
        df = await asyncio.to_thread(_safe_fetch_news_global_em)
        return _parse_global_em_df(df)

    async def _fetch_global_sina(self) -> list[NewsArticle]:
        df = await asyncio.to_thread(_safe_fetch_news_global_sina)
        return _parse_global_sina_df(df)

    async def _fetch_cctv_today(self) -> list[NewsArticle]:
        return await self.fetch_cctv()


# Public typing exports (used by tests / scheduler typing).
__all__ = [
    "NewsArticle",
    "NewsCrawlerService",
    "NewsDomain",
    "NewsSource",
    "_parse_cctv_df",
    "_parse_cls_df",
    "_parse_global_em_df",
    "_parse_global_sina_df",
    "_parse_news_df",
]
