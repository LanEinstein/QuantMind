"""ArxivCrawler — arxiv.org OAI-PMH ingest (X-010 / P2-2 §1.2).

Sources are filtered to the three categories most relevant to the
QuantMind self-evolution lane:

* ``q-fin``      — quantitative finance.
* ``cs.LG``      — machine learning systems.
* ``cs.AI``      — general AI research (broad enough to catch DSPy /
                   GEPA / agent-architecture papers).

Spotlighting is applied automatically by :class:`CrawlerBase` so the
caller does not need to remember the wrapping convention.

Rate limit: ~3.1 seconds between bulk requests (OAI-PMH guidance) —
the orchestrator (X-010 frontier_crawler) coordinates the cross-source
schedule on top of the per-crawler semaphore.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.evolution.crawlers.base import CrawlerBase, RawRecord
from backend.evolution.rag_ingester import CrawledDocument

SOURCE_NAME = "arxiv"
SOURCE_DOMAIN = "arxiv.org"
DEFAULT_RATE_LIMIT_SLEEP_SEC = 3.1


@dataclass(frozen=True)
class ArxivCrawler(CrawlerBase):
    """Pull q-fin / cs.LG / cs.AI metadata + abstract from arxiv OAI-PMH."""

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        external_id = str(raw["external_id"])
        authors = tuple(str(a) for a in raw.get("authors", ()))
        return CrawledDocument(
            doc_id=f"ARXIV-{external_id}",
            source=SOURCE_NAME,  # type: ignore[arg-type]
            source_url=str(raw["url"]),
            source_domain=SOURCE_DOMAIN,
            title=str(raw["title"])[:500],
            authors=authors[:50],
            published_at=_parse_published(raw),
            license=str(raw.get("license", "arXiv perpetual non-exclusive")),
            external_id=external_id,
            raw_text=str(raw["body"]),
            category=tuple(raw.get("categories", ())),
            language_detected="en",
        )


def _parse_published(raw: RawRecord) -> datetime:
    value = raw.get("published_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # arxiv timestamps come as YYYY-MM-DDTHH:MM:SSZ
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)


__all__ = ["ArxivCrawler", "SOURCE_DOMAIN", "SOURCE_NAME"]
