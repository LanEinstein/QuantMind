"""AkshareChangelogCrawler — akshare upstream changelog ingest (X-010).

akshare publishes new-version notes as an Atom feed. The crawler
reads the feed entries and adapts each into a :class:`CrawledDocument`
with the ``AKSHARE-`` doc_id prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.evolution.crawlers.base import CrawlerBase, RawRecord
from backend.evolution.rag_ingester import CrawledDocument

SOURCE_NAME = "akshare"
SOURCE_DOMAIN = "akshare.akfamily.xyz"
DEFAULT_RATE_LIMIT_SLEEP_SEC = 0.0


@dataclass(frozen=True)
class AkshareChangelogCrawler(CrawlerBase):
    """Pull changelog entries from akshare's upstream Atom feed."""

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        external_id = str(raw["external_id"])  # e.g. "v1.10.5"
        return CrawledDocument(
            doc_id=f"AKSHARE-{external_id}",
            source=SOURCE_NAME,  # type: ignore[arg-type]
            source_url=str(raw["url"]),
            source_domain=SOURCE_DOMAIN,
            title=str(raw["title"])[:500],
            authors=tuple(str(a) for a in raw.get("authors", ()))[:50],
            published_at=_parse_published(raw),
            license=str(raw.get("license", "akshare upstream changelog")),
            external_id=external_id,
            raw_text=str(raw["body"]),
            category=tuple(raw.get("tags", ())),
            language_detected=raw.get("language", "zh"),
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


__all__ = ["AkshareChangelogCrawler", "SOURCE_DOMAIN", "SOURCE_NAME"]
