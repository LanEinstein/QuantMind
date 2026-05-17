"""C-005 within-domain news deduplication.

P0-8 §1.3.4 / §2 redline 16 lock the dedupe rule to "domain + 标题 +
published_at 差 ≤ 60s → 视为重复,保留最早" — explicitly *not* URL
based, because the same story re-posted to two vendors in the same
domain frequently surfaces with two distinct URLs (e.g. an eastmoney
detail page + an eastmoney terminal mirror). Title + 60s window
catches that without collapsing genuinely distinct rows.

Across domains the same story is preserved verbatim — the multi-domain
echo of an event is itself a MiroFish input signal (P0-8 §1.2 / §2
redline 16 "严禁实施期为减少存储成本而跨域去重").

This module is a pure helper. It does not touch I/O, does not import
``backend.llm`` / ``backend.agents`` / ``backend.mirofish`` (C-005 §2
acceptance: ``backend/data/*`` stays clean of those layers), and is
fully deterministic given an input order.
"""

from __future__ import annotations

from collections.abc import Iterable

from backend.models.market import NewsArticle, NewsDomain

# Locked dedupe window per P0-8 §1.3.4. Exposed as a module-level
# constant so callers / tests / the redline-check.sh static guard can
# reference a single value.
TITLE_DEDUPE_WINDOW_SECONDS: int = 60


def dedupe_within_domain(
    articles: Iterable[NewsArticle],
    *,
    title_window_seconds: int = TITLE_DEDUPE_WINDOW_SECONDS,
) -> list[NewsArticle]:
    """Drop within-domain duplicates by ``(domain, title, ≤60s)``.

    The first article seen for a given ``(domain, title)`` pair wins
    when subsequent articles fall within ``title_window_seconds`` of
    its ``publish_time``. Articles in the same domain with the same
    title but published more than 60 seconds apart are kept as
    independent rows (vendor re-runs of an old headline are real news).

    Cross-domain duplicates (same title, different domain) are kept
    on purpose — P0-8 §1.2: the multi-domain echo is itself an input
    signal for MiroFish.

    Order-preserving: callers that pre-sort by ``publish_time desc``
    will get a stable dedupe result keyed by the earliest seen
    article for each (domain, title) bucket within the window.

    ``title_window_seconds`` defaults to the locked
    :data:`TITLE_DEDUPE_WINDOW_SECONDS` (60). Override is intended for
    unit tests; runtime callers must use the locked default.
    """
    # Group by (domain, normalised_title); inside each bucket, walk
    # the timestamps and keep only the first within a 60s window.
    # Empty / whitespace-only titles collapse to a single bucket per
    # domain so a stream of untitled flashes doesn't accidentally
    # de-dupe distinct stories.
    keepers: list[NewsArticle] = []
    # Each bucket records the timestamp(s) of the kept rows so we can
    # decide whether the next article falls inside any kept article's
    # 60s window.
    bucket_keepers: dict[tuple[NewsDomain, str], list[NewsArticle]] = {}
    for article in articles:
        key = (article.domain, _normalise_title(article.title))
        kept_for_key = bucket_keepers.setdefault(key, [])
        if any(
            _within_window(
                article.publish_time,
                kept.publish_time,
                title_window_seconds,
            )
            for kept in kept_for_key
        ):
            continue
        kept_for_key.append(article)
        keepers.append(article)
    return keepers


def _normalise_title(title: str) -> str:
    """Trim and lowercase title for stable dedupe keys.

    Two vendors often emit the same headline with trailing whitespace
    or case variation; normalisation keeps the bucket coherent without
    being so aggressive that it merges genuinely different stories.
    """
    return title.strip().lower()


def _within_window(a, b, window_seconds: int) -> bool:
    """Return True if ``|a - b| ≤ window_seconds``.

    Returns False if either timestamp is missing tzinfo and the other
    is tz-aware (mixing naive and aware would raise). The crawler
    parses every vendor timestamp through ``_parse_dt_lenient`` which
    always returns tz-aware UTC, so in practice this branch is dead;
    it just keeps the helper defensive.
    """
    if a.tzinfo is None or b.tzinfo is None:
        return False
    delta = abs((a - b).total_seconds())
    return delta <= window_seconds


def count_by_domain(
    articles: Iterable[NewsArticle],
) -> dict[NewsDomain, int]:
    """Return a histogram of article count per domain.

    Used by the news scheduler debug log and by the C-005 acceptance
    test to assert that all three domains are represented when the
    full multi-source fan-out succeeds.
    """
    counts: dict[NewsDomain, int] = {
        "financial": 0,
        "political": 0,
        "global": 0,
    }
    for article in articles:
        counts[article.domain] = counts.get(article.domain, 0) + 1
    return counts


def split_by_domain(
    articles: Iterable[NewsArticle],
) -> dict[NewsDomain, list[NewsArticle]]:
    """Partition articles into a ``domain -> list`` mapping.

    Order within each domain bucket matches the input iteration order
    so callers can pre-sort by publish_time before partitioning.
    """
    buckets: dict[NewsDomain, list[NewsArticle]] = {
        "financial": [],
        "political": [],
        "global": [],
    }
    for article in articles:
        buckets[article.domain].append(article)
    return buckets
