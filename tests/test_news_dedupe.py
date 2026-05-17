"""C-005 within-domain dedupe + domain histogram tests.

Locks the rule from P0-8 §1.3.4 / §2 redline 16:

* Within a single domain, same title + publish_time within a 60s window
  → keep the first, drop the rest.
* Same title at a different domain, OR same title >60s apart in the
  same domain → keep both (multi-domain echo is a MiroFish signal;
  re-runs of an old headline are real news).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.data.news_dedupe import (
    TITLE_DEDUPE_WINDOW_SECONDS,
    count_by_domain,
    dedupe_within_domain,
    split_by_domain,
)
from backend.models.market import NEWS_SOURCE_TO_DOMAIN, NewsArticle


def _make(
    *,
    url: str,
    source: str = "eastmoney",
    domain: str = "financial",
    title: str = "t",
    when: datetime | None = None,
    minute: int = 0,
    second: int = 0,
) -> NewsArticle:
    publish = when or datetime(
        2026, 5, 16, 9, minute, second, tzinfo=UTC
    )
    return NewsArticle(
        title=title,
        content="c",
        source=source,
        url=url,
        publish_time=publish,
        domain=domain,  # type: ignore[arg-type]
    )


class TestDedupeWithinDomain:
    def test_locked_window_constant(self) -> None:
        assert TITLE_DEDUPE_WINDOW_SECONDS == 60

    def test_within_domain_same_title_inside_window_collapses(self) -> None:
        """Two articles, same domain + title, 30s apart → 1 row."""
        a = _make(
            url="https://example.com/1",
            domain="financial",
            title="降准消息",
            second=0,
        )
        b = _make(
            url="https://example.com/2",  # different URL!
            domain="financial",
            title="降准消息",
            second=30,
        )
        result = dedupe_within_domain([a, b])
        assert len(result) == 1
        # First-wins ordering
        assert result[0].url == "https://example.com/1"

    def test_within_domain_same_title_outside_window_kept(self) -> None:
        """Same title >60s apart in same domain → two distinct rows
        (vendor re-runs are real news)."""
        a = _make(
            url="https://example.com/1",
            domain="financial",
            title="降准消息",
            second=0,
        )
        b = _make(
            url="https://example.com/2",
            domain="financial",
            title="降准消息",
            when=datetime(2026, 5, 16, 9, 5, 0, tzinfo=UTC),  # 5 min later
        )
        out = dedupe_within_domain([a, b])
        assert len(out) == 2

    def test_cross_domain_duplicates_preserved(self) -> None:
        """Same title in two domains is two pieces of evidence (P0-8 §1.2)."""
        fin = _make(
            url="https://example.com/x",
            domain="financial",
            title="央行降准",
        )
        pol = _make(
            url="https://example.com/y",
            source="cctv",
            domain="political",
            title="央行降准",
        )
        glb = _make(
            url="https://example.com/z",
            source="global_em",
            domain="global",
            title="央行降准",
        )
        out = dedupe_within_domain([fin, pol, glb])
        assert len(out) == 3
        assert {a.domain for a in out} == {"financial", "political", "global"}

    def test_normalisation_collapses_whitespace_and_case(self) -> None:
        a = _make(
            url="u1",
            domain="financial",
            title="降准 消息",
            second=0,
        )
        b = _make(
            url="u2",
            domain="financial",
            title="降准 消息",  # same after strip
            second=10,
        )
        c = _make(
            url="u3",
            domain="financial",
            title="降准 消息",  # leading/trailing
            second=20,
        )
        out = dedupe_within_domain([a, b, c])
        assert len(out) == 1

    def test_distinct_titles_kept(self) -> None:
        items = [
            _make(url="u1", domain="financial", title="a", second=0),
            _make(url="u2", domain="financial", title="b", second=10),
            _make(url="u3", domain="financial", title="c", second=20),
        ]
        out = dedupe_within_domain(items)
        assert [a.title for a in out] == ["a", "b", "c"]

    def test_window_param_override(self) -> None:
        a = _make(url="u1", domain="financial", title="x", second=0)
        b = _make(url="u2", domain="financial", title="x", second=30)
        # With a shorter window, b is outside and survives.
        out = dedupe_within_domain([a, b], title_window_seconds=10)
        assert len(out) == 2

    def test_order_preserved(self) -> None:
        items = [
            _make(
                url=f"https://x/{i}",
                title=f"title-{i}",
                second=i,
                domain="financial",
            )
            for i in range(5)
        ]
        out = dedupe_within_domain(items)
        assert [a.url for a in out] == [a.url for a in items]

    def test_empty_input_returns_empty_list(self) -> None:
        assert dedupe_within_domain([]) == []

    def test_dedupe_is_deterministic(self) -> None:
        base = datetime(2026, 5, 16, 9, 0, 0, tzinfo=UTC)
        items = [
            _make(url="u1", domain="financial", title="a", when=base),
            _make(
                url="u2",
                domain="financial",
                title="a",
                when=base + timedelta(seconds=20),
            ),
            _make(
                url="u3",
                domain="global",
                source="global_em",
                title="a",
                when=base,
            ),
            _make(
                url="u4",
                domain="financial",
                title="b",
                when=base,
            ),
        ]
        first = dedupe_within_domain(items)
        second = dedupe_within_domain(items)
        assert [a.url for a in first] == [a.url for a in second]
        assert [a.title for a in first] == [a.title for a in second]


class TestCountByDomain:
    def test_returns_zero_for_missing_domains(self) -> None:
        assert count_by_domain([]) == {
            "financial": 0,
            "political": 0,
            "global": 0,
        }

    def test_counts_per_domain(self) -> None:
        items = [
            _make(url="u1", domain="financial"),
            _make(url="u2", domain="financial"),
            _make(url="u3", domain="political", source="cctv"),
            _make(url="u4", domain="global", source="global_em"),
            _make(url="u5", domain="global", source="global_sina"),
        ]
        assert count_by_domain(items) == {
            "financial": 2,
            "political": 1,
            "global": 2,
        }


class TestSplitByDomain:
    def test_partitions_preserves_order(self) -> None:
        a = _make(url="u1", domain="financial", title="A")
        b = _make(url="u2", domain="financial", title="B")
        c = _make(url="u3", domain="political", source="cctv", title="C")
        buckets = split_by_domain([b, a, c])
        assert [x.title for x in buckets["financial"]] == ["B", "A"]
        assert [x.title for x in buckets["political"]] == ["C"]
        assert buckets["global"] == []


class TestSourceDomainMap:
    @pytest.mark.parametrize(
        "source,expected_domain",
        [
            ("eastmoney", "financial"),
            ("cls", "financial"),
            ("cctv", "political"),
            ("global_em", "global"),
            ("global_sina", "global"),
        ],
    )
    def test_locked_mapping(self, source: str, expected_domain: str) -> None:
        assert NEWS_SOURCE_TO_DOMAIN[source] == expected_domain  # type: ignore[index]

    def test_unknown_source_not_in_map(self) -> None:
        """`unknown` is a read-only legacy bucket — it does not auto-map."""
        assert "unknown" not in NEWS_SOURCE_TO_DOMAIN
