"""Shared crawler scaffolding (X-010).

Each per-source crawler is a frozen dataclass wrapping a fetcher
:class:`Protocol` callable. The base helpers here:

* Rate-limit via an injected :class:`asyncio.Semaphore` plus a
  provider-specific sleep delay.
* Convert provider-native records into
  :class:`backend.evolution.rag_ingester.CrawledDocument` instances
  wrapped with Spotlighting datamarking.
* Honour ``test_mode=True`` so unit tests bypass network I/O entirely.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeAlias

from backend.evolution.crawlers.spotlighting import wrap_with_spotlight
from backend.evolution.rag_ingester import CrawledDocument

log = logging.getLogger(__name__)


# Provider-native row shape — every crawler accepts a fetcher returning
# a sequence of dicts and adapts them in its own ``_to_document`` step.
RawRecord: TypeAlias = dict[str, Any]


class Fetcher(Protocol):
    """Async callable returning provider-native records.

    Tests inject a stub that returns a pre-canned list; production
    wires the real HTTP client. The signature is intentionally narrow
    (only the as-of date flows in) so the orchestrator can iterate
    over heterogeneous crawlers uniformly.
    """

    async def __call__(self, *, as_of: datetime) -> Sequence[RawRecord]: ...


FetcherCallable: TypeAlias = (
    Fetcher | Callable[..., Awaitable[Sequence[RawRecord]]]
)


@dataclass(frozen=True)
class CrawlerBase:
    """Shared per-source rate-limiting + Spotlighting helper.

    Crawlers extend this class by defining ``source_name`` /
    ``rate_limit_sleep_sec`` class attributes plus a ``_to_document``
    static helper. Frozen so the semaphore + fetcher pair stays
    immutable across the daily cron run.
    """

    fetcher: FetcherCallable
    semaphore: asyncio.Semaphore
    rate_limit_sleep_sec: float = 0.0
    """Per-record sleep applied AFTER the fetcher returns to honour the
    most aggressive provider limit. arxiv OAI-PMH for example asks for
    ~3 second pauses between bulk requests."""

    async def fetch_documents(
        self, *, as_of: datetime
    ) -> tuple[CrawledDocument, ...]:
        """Fetch + adapt records into :class:`CrawledDocument` tuple.

        Returns an empty tuple if the fetcher yields no records. The
        Spotlighting wrap is applied per record so every body the
        ingester sees has the sentinel boundaries.
        """
        async with self.semaphore:
            raw_records = await self.fetcher(as_of=as_of)
        out: list[CrawledDocument] = []
        for raw in raw_records:
            doc = self._build_document(raw=raw)
            out.append(doc)
        if self.rate_limit_sleep_sec > 0:
            await asyncio.sleep(self.rate_limit_sleep_sec)
        return tuple(out)

    def _build_document(self, *, raw: RawRecord) -> CrawledDocument:
        """Subclasses override :meth:`_to_document` for provider mapping
        and call this helper to apply Spotlighting wrap + final
        :class:`CrawledDocument` construction.
        """
        partial = self._to_document(raw=raw)
        wrapped_text = wrap_with_spotlight(
            source=partial.source,
            external_id=partial.external_id,
            body=partial.raw_text,
        )
        # Rebuild with wrapped body — frozen dataclass cannot be
        # mutated, so construct a new instance.
        return CrawledDocument(
            doc_id=partial.doc_id,
            source=partial.source,
            source_url=partial.source_url,
            source_domain=partial.source_domain,
            title=partial.title,
            authors=partial.authors,
            published_at=partial.published_at,
            license=partial.license,
            external_id=partial.external_id,
            raw_text=wrapped_text,
            category=partial.category,
            language_detected=partial.language_detected,
        )

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        """Subclass hook — must be overridden."""
        raise NotImplementedError


__all__ = [
    "CrawlerBase",
    "Fetcher",
    "FetcherCallable",
    "RawRecord",
]
