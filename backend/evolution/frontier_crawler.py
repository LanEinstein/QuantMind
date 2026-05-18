"""FrontierCrawler — coordinates the 5-source nightly crawl (X-010).

Owns the per-source rate limit + Spotlighting datamarking and feeds
:class:`backend.evolution.rag_ingester.RagIngester` with the resulting
:class:`CrawledDocument` instances. The orchestrator is intentionally
*synchronous-with-async-fan-out*: each source crawler runs concurrently
under a single :class:`asyncio.Semaphore` that caps the number of
parallel network ops so the host's IPv4-only egress (CLAUDE.md §2.9)
is never saturated.

LLM out-bound budget (X-010 row in P2-2 §1.13): the optional DeepSeek
``summariser`` callable is wrapped with
:func:`backend.services.cost_guard.assert_budget_allows` so daily
summary calls roll up into the ¥20 hard ceiling. Tests pass ``None``
for ``summariser`` so no LLM call is attempted.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
``backend.services.cost_guard`` is on the allow-list as the budget
substrate.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from backend.evolution.crawlers.base import CrawlerBase
from backend.evolution.rag_ingester import (
    CrawledDocument,
    IngestResult,
    RagIngester,
)
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
)

if TYPE_CHECKING:
    import redis.asyncio

log = logging.getLogger(__name__)


DEFAULT_GLOBAL_CONCURRENCY = 5
"""Global cap on concurrent HTTP requests across all five crawlers.
Matches the IPv4-only egress concern — five parallel ops keeps the
NAT table comfortable on a single host."""


SummariserCallable = Callable[[CrawledDocument], Awaitable[str]]
"""Optional async summariser — typically DeepSeek API. Tests pass None."""


@dataclass(frozen=True)
class FrontierCrawlResult:
    """Aggregate outcome of one nightly cron pass."""

    fetched: int
    ingested: int
    rejected: int
    crawler_errors: tuple[str, ...]


@dataclass(frozen=True)
class FrontierCrawler:
    """Coordinator over the 5 per-source crawlers.

    Frozen so the wiring (crawler list + ingester + concurrency cap)
    cannot drift between cron firings — the dispatcher (X-008)
    constructs one instance at boot.
    """

    crawlers: tuple[CrawlerBase, ...]
    ingester: RagIngester
    global_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY
    summariser: SummariserCallable | None = None
    _global_semaphore: asyncio.Semaphore = field(
        init=False, repr=False, default=None  # type: ignore[arg-type]
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_global_semaphore",
            asyncio.Semaphore(self.global_concurrency),
        )

    async def run(
        self,
        *,
        as_of: datetime | None = None,
        redis_client: redis.asyncio.Redis | None = None,
        correlation_id: str | None = None,
    ) -> FrontierCrawlResult:
        """Fetch + ingest one nightly batch across all 5 crawlers.

        Args:
            as_of: tick the crawlers are seeded with. Defaults to now.
            redis_client: optional cost-guard backing — when supplied
                each summariser call is preceded by
                :func:`assert_budget_allows`. ``None`` skips the
                budget check (tests / simulation paths).
            correlation_id: forwarded to every audit row.
        """
        as_of_ts = as_of or datetime.now(UTC)
        fetched = 0
        ingested = 0
        rejected = 0
        errors: list[str] = []

        # Run all 5 crawlers concurrently under the global cap so the
        # nightly window stays bounded.
        gather_results = await asyncio.gather(
            *[
                self._run_one_crawler(crawler=crawler, as_of=as_of_ts)
                for crawler in self.crawlers
            ],
            return_exceptions=True,
        )

        documents: list[CrawledDocument] = []
        for outcome in gather_results:
            if isinstance(outcome, BaseException):
                errors.append(f"{type(outcome).__name__}: {outcome}")
                continue
            documents.extend(outcome)

        fetched = len(documents)

        budget_blocked = False
        for doc in documents:
            # Summariser is a side-channel (cost-tracked LLM call).
            # Raw ingest costs zero LLM budget — so a budget breach
            # only disables further summariser calls; it must not
            # prevent the raw document from landing on disk (codex
            # review P2-3).
            if self.summariser is not None and not budget_blocked:
                if redis_client is not None:
                    try:
                        await assert_budget_allows(
                            redis_client, agent_name="frontier_crawler"
                        )
                    except DailyBudgetExceededError as exc:
                        errors.append(f"cost_guard: {exc}")
                        budget_blocked = True
                if not budget_blocked:
                    try:
                        _ = await self.summariser(doc)
                    except Exception as exc:  # noqa: BLE001 — best-effort
                        errors.append(f"summariser: {exc}")

            result: IngestResult = await self.ingester.ingest(
                doc, correlation_id=correlation_id
            )
            if result.accepted:
                ingested += 1
            else:
                rejected += 1

        return FrontierCrawlResult(
            fetched=fetched,
            ingested=ingested,
            rejected=rejected,
            crawler_errors=tuple(errors),
        )

    async def _run_one_crawler(
        self,
        *,
        crawler: CrawlerBase,
        as_of: datetime,
    ) -> Sequence[CrawledDocument]:
        async with self._global_semaphore:
            return await crawler.fetch_documents(as_of=as_of)


__all__ = [
    "DEFAULT_GLOBAL_CONCURRENCY",
    "FrontierCrawlResult",
    "FrontierCrawler",
    "SummariserCallable",
]
