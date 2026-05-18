"""Five RAG-source crawlers (X-010, P2-2 §1.13).

Each crawler implements the :class:`Crawler` Protocol from
:mod:`backend.evolution.frontier_crawler` so the frontier orchestrator
can iterate over them uniformly. Every crawler must:

* Apply rate limiting (asyncio.Semaphore + provider-specific sleep).
* Return :class:`backend.evolution.rag_ingester.CrawledDocument`
  instances with the locked ``doc_id`` prefix for its source.
* Never hit the network when ``test_mode`` is set — the dispatcher
  unit tests inject stub providers.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
"""

from __future__ import annotations

from backend.evolution.crawlers.akshare_changelog import AkshareChangelogCrawler
from backend.evolution.crawlers.arxiv import ArxivCrawler
from backend.evolution.crawlers.github_releases import GitHubReleasesCrawler
from backend.evolution.crawlers.openreview_crawler import OpenReviewCrawler
from backend.evolution.crawlers.semanticscholar import SemanticScholarCrawler

__all__ = [
    "AkshareChangelogCrawler",
    "ArxivCrawler",
    "GitHubReleasesCrawler",
    "OpenReviewCrawler",
    "SemanticScholarCrawler",
]
