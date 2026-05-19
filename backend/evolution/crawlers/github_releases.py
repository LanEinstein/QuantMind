"""GitHubReleasesCrawler — GitHub releases ingest (X-010 / P2-2 §1.2).

Uses the GitHub REST API ``/repos/{owner}/{repo}/releases`` endpoint
with PAT auth (5000 req/hr) and ETag caching. ``doc_id`` prefix locked
to ``GH-REL-``.

Repo allowlist is enforced by the orchestrator (X-010
``frontier_crawler``); this module assumes the upstream fetcher only
returns approved repo / release pairs. Per provenance schema
(``doc_id`` cross-check) the crawler-side prefix must stay synced with
the source name.

Production wiring note (codex X-027 R4 claim 1+2):
    The real ``FetcherCallable`` provided at production wiring time
    MUST load the PAT from ``os.environ["GITHUB_TOKEN"]`` only —
    never from ``.env`` files, never hard-coded. Use it as a Bearer
    auth header (``Authorization: Bearer <token>``). Log only the
    SHA256[:8] fingerprint per
    :func:`backend.services.secrets_validator.compute_fingerprint`;
    never log the raw token.

    Q3 of P2-2-implementation-plan-2026-05-18 keeps the PAT
    heterogeneous to the LLM 3 + Feishu 5 pool, so
    ``secrets_validator`` only soft-warns on a malformed PAT (not
    fail-fast). The crawler-side init is the place to fail-fast with
    a typed ``MissingCrawlerCredentialError`` if the env var is unset
    while the GitHub source is enabled in
    ``config/evolution/frontier.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.evolution.crawlers.base import CrawlerBase, RawRecord
from backend.evolution.rag_ingester import CrawledDocument

SOURCE_NAME = "github_releases"
SOURCE_DOMAIN = "api.github.com"
DEFAULT_RATE_LIMIT_SLEEP_SEC = 0.0


@dataclass(frozen=True)
class GitHubReleasesCrawler(CrawlerBase):
    """Pull releases.body from approved GitHub repos."""

    def _to_document(self, *, raw: RawRecord) -> CrawledDocument:
        external_id = str(raw["external_id"])  # e.g. "owner_repo_v1.2.3"
        return CrawledDocument(
            doc_id=f"GH-REL-{external_id}",
            source=SOURCE_NAME,  # type: ignore[arg-type]
            source_url=str(raw["url"]),
            source_domain=SOURCE_DOMAIN,
            title=str(raw["title"])[:500],
            authors=tuple(str(a) for a in raw.get("authors", ()))[:50],
            published_at=_parse_published(raw),
            license=str(raw.get("license", "GitHub Releases — repo-specific")),
            external_id=external_id,
            raw_text=str(raw["body"]),
            category=tuple(raw.get("tags", ())),
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


__all__ = ["GitHubReleasesCrawler", "SOURCE_DOMAIN", "SOURCE_NAME"]
