"""SemanticScholarCrawler — Semantic Scholar bulk API ingest (X-010).

Uses the free SS API key + bulk endpoint to fetch paper abstracts.
``doc_id`` prefix locked to ``S2-`` (matches
:data:`backend.evolution.provenance.models.DOC_ID_RE`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.evolution.crawlers.base import CrawlerBase, RawRecord
from backend.evolution.rag_ingester import CrawledDocument

SOURCE_NAME = "semanticscholar"
SOURCE_DOMAIN = "api.semanticscholar.org"
DEFAULT_RATE_LIMIT_SLEEP_SEC = 1.0


@dataclass(frozen=True)
class SemanticScholarCrawler(CrawlerBase):
    """Pull paper abstracts via Semantic Scholar bulk endpoint."""

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        external_id = str(raw["external_id"])
        return CrawledDocument(
            doc_id=f"S2-{external_id}",
            source=SOURCE_NAME,  # type: ignore[arg-type]
            source_url=str(raw["url"]),
            source_domain=SOURCE_DOMAIN,
            title=str(raw["title"])[:500],
            authors=tuple(str(a) for a in raw.get("authors", ()))[:50],
            published_at=_parse_published(raw),
            license=str(raw.get("license", "Semantic Scholar API ToS")),
            external_id=external_id,
            raw_text=str(raw["body"]),
            category=tuple(raw.get("categories", ())),
            language_detected=raw.get("language", "en"),
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


__all__ = ["SOURCE_DOMAIN", "SOURCE_NAME", "SemanticScholarCrawler"]
