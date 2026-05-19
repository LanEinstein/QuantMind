"""Codex X-028 R5 coverage closure — _parse_published fallback paths.

The three crawlers ``akshare_changelog``, ``github_releases``, and
``openreview_crawler`` each defined a module-level ``_parse_published``
helper whose fallback branches (non-datetime / non-str / unparseable
ISO string → ``datetime.now(UTC)``) sat uncovered at 75% line coverage.
The three parametrised tests below hit:

* missing ``published_at`` key — ``raw.get("published_at")`` returns
  ``None``, neither the ``isinstance(value, datetime)`` nor the
  ``isinstance(value, str)`` branch matches, so the fallback runs.
* malformed ISO string — passes the ``isinstance(value, str)`` guard
  but ``datetime.fromisoformat`` raises ``ValueError``, the inner
  ``except`` swallows it and the function falls through to the
  fallback ``datetime.now(UTC)`` return.
* passthrough — a real ``datetime`` is returned unchanged, locking the
  happy path so future refactors do not silently turn every record
  into ``now()``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.evolution.crawlers.akshare_changelog import (
    _parse_published as ak_pp,
)
from backend.evolution.crawlers.github_releases import (
    _parse_published as gh_pp,
)
from backend.evolution.crawlers.openreview_crawler import (
    _parse_published as or_pp,
)


@pytest.mark.parametrize(
    "fn",
    [ak_pp, gh_pp, or_pp],
    ids=["akshare", "github_releases", "openreview"],
)
def test_parse_published_handles_missing_field(fn) -> None:
    """No ``published_at`` key → fallback to ``datetime.now(UTC)``."""
    before = datetime.now(UTC)
    result = fn({})
    after = datetime.now(UTC)
    assert isinstance(result, datetime)
    assert result.tzinfo is UTC
    assert before <= result <= after


@pytest.mark.parametrize(
    "fn",
    [ak_pp, gh_pp, or_pp],
    ids=["akshare", "github_releases", "openreview"],
)
def test_parse_published_handles_malformed_string(fn) -> None:
    """Unparseable ISO string → swallow ``ValueError``, fallback to ``now``."""
    before = datetime.now(UTC)
    result = fn({"published_at": "not-a-date"})
    after = datetime.now(UTC)
    assert isinstance(result, datetime)
    assert result.tzinfo is UTC
    assert before <= result <= after


@pytest.mark.parametrize(
    "fn,sample",
    [
        (ak_pp, datetime(2026, 1, 1, tzinfo=UTC)),
        (gh_pp, datetime(2026, 2, 2, tzinfo=UTC)),
        (or_pp, datetime(2026, 3, 3, tzinfo=UTC)),
    ],
    ids=["akshare", "github_releases", "openreview"],
)
def test_parse_published_returns_input_datetime(fn, sample) -> None:
    """Datetime input is returned unchanged (locks the happy path)."""
    assert fn({"published_at": sample}) == sample


@pytest.mark.parametrize(
    "fn",
    [ak_pp, gh_pp, or_pp],
    ids=["akshare", "github_releases", "openreview"],
)
def test_parse_published_parses_well_formed_iso_string(fn) -> None:
    """A canonical RFC 3339 / ISO 8601 string parses correctly — the
    ``str`` branch *succeeds* path, locked separately from the
    ``ValueError`` fallback above."""
    result = fn({"published_at": "2026-05-19T12:34:56+00:00"})
    assert isinstance(result, datetime)
    assert result == datetime(2026, 5, 19, 12, 34, 56, tzinfo=UTC)


@pytest.mark.parametrize(
    "fn",
    [ak_pp, gh_pp, or_pp],
    ids=["akshare", "github_releases", "openreview"],
)
def test_parse_published_parses_zulu_suffix(fn) -> None:
    """``Z`` suffix is normalised to ``+00:00`` before parsing — locks the
    GitHub / OpenReview / akshare upstream convention."""
    result = fn({"published_at": "2026-05-19T12:34:56Z"})
    assert isinstance(result, datetime)
    assert result == datetime(2026, 5, 19, 12, 34, 56, tzinfo=UTC)
