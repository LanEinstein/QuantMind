"""Pure helpers for ``watchlist_market_snapshots`` missing-rate accounting.

P0-8 §2.1 sets the acceptance target at "missing rate ≤1%" — defined as the
fraction of *expected* ``(code, tick)`` snapshot rows that were not
persisted to MongoDB inside a measurement window. Two views of "missing"
are useful and exposed as independent pure functions:

* :func:`compute_tick_missing_rate` — single-tick view used by
  :class:`backend.data.data_quality.DataQualityProvider` to detect partial
  fetches mid-tick. Returns ``missing / expected`` ∈ [0.0, 1.0].
* :func:`compute_window_missing_rate` — windowed view used by the daily
  acceptance pipeline (P0-6) when scoring 5-min / 30-min buckets. Floors
  at 0.0 so an over-fetch (more rows than expected, e.g. retry duplicates)
  never produces a negative rate.

Both helpers are pure: no IO, no logging, no ``backend.{llm,agents,risk}``
imports. They live in ``backend/data`` because they sit on the data
collection side of the risk boundary (P0-10 §2.1 redline 1).
"""

from __future__ import annotations

from collections.abc import Iterable


def compute_tick_missing_rate(
    expected_codes: Iterable[str],
    observed_codes: Iterable[str],
) -> float:
    """Return the fraction of ``expected_codes`` absent from ``observed_codes``.

    Both inputs are reduced to frozensets before comparison so duplicate
    rows in ``observed_codes`` cannot mask a missing code. The result is
    in [0.0, 1.0]:

    * Empty ``expected_codes`` → ``0.0`` (no expectation, nothing missing).
    * Otherwise → ``len(expected - observed) / len(expected)``.

    Observed codes outside the expected universe are ignored — the rate
    measures coverage of the watchlist, not over-fetch noise.
    """
    expected = frozenset(expected_codes)
    if not expected:
        return 0.0
    observed = frozenset(observed_codes)
    missing = expected - observed
    return len(missing) / len(expected)


def compute_window_missing_rate(
    expected_code_count: int,
    window_tick_count: int,
    observed_row_count: int,
) -> float:
    """Return missing rate across a multi-tick window.

    Useful when the caller already has the persisted ``observed_row_count``
    from a Mongo aggregate (e.g. ``count_documents({"snapshot_at":
    {"$gte": ..., "$lt": ...}})``) and just wants the comparison against
    the theoretical ``expected_code_count * window_tick_count`` ceiling.

    Returns ``0.0`` if the denominator is non-positive (no expectation or
    no ticks) and floors at ``0.0`` if the observed count exceeds the
    expected total (retry duplicates / clock drift), so callers can rely
    on the result being in [0.0, 1.0].

    Args:
        expected_code_count: Active watchlist size (e.g. 13 under P0-9).
        window_tick_count: Number of 30s ticks in the measurement window.
        observed_row_count: Snapshot rows actually persisted in the window.
    """
    if expected_code_count <= 0 or window_tick_count <= 0:
        return 0.0
    expected_total = expected_code_count * window_tick_count
    missing = expected_total - observed_row_count
    if missing <= 0:
        return 0.0
    return missing / expected_total


__all__ = [
    "compute_tick_missing_rate",
    "compute_window_missing_rate",
]
