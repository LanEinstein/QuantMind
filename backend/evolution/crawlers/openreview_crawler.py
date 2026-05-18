"""OpenReviewCrawler — OpenReview.net papers + meta-reviews (X-010).

Built on the ``openreview-py>=1.40`` SDK shape (returns
``Note``-style dicts). ``doc_id`` prefix locked to ``OPENREVIEW-``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.evolution.crawlers.base import CrawlerBase, RawRecord
from backend.evolution.rag_ingester import CrawledDocument

SOURCE_NAME = "openreview"
SOURCE_DOMAIN = "openreview.net"
DEFAULT_RATE_LIMIT_SLEEP_SEC = 1.0


@dataclass(frozen=True)
class OpenReviewCrawler(CrawlerBase):
    """Pull venue papers + meta-reviews from OpenReview."""

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        external_id = str(raw["external_id"])
        return CrawledDocument(
            doc_id=f"OPENREVIEW-{external_id}",
            source=SOURCE_NAME,  # type: ignore[arg-type]
            source_url=str(raw["url"]),
            source_domain=SOURCE_DOMAIN,
            title=str(raw["title"])[:500],
            authors=tuple(str(a) for a in raw.get("authors", ()))[:50],
            published_at=_parse_published(raw),
            license=str(raw.get("license", "OpenReview public posting")),
            external_id=external_id,
            raw_text=str(raw["body"]),
            category=tuple(raw.get("venue_tags", ())),
            language_detected="en",
        )


def _parse_published(raw: RawRecord) -> datetime:
    value = raw.get("published_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


__all__ = ["OpenReviewCrawler", "SOURCE_DOMAIN", "SOURCE_NAME"]
