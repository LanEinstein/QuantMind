"""``_observable_index_closes`` — PIT + freshness guard for the regime series.

codex P2: the benchmark closes now drive the live ADD bear-ban and the D1-b
drawdown tightening, so the series must be finite-positive, observable as of the
tick, and fresh — else fail open to NEUTRAL (empty).
"""

from __future__ import annotations

from datetime import date

from backend.main import _observable_index_closes

_CUTOFF = date(2026, 6, 2)


def _row(d: object, close: object) -> dict[str, object]:
    return {"date": d, "close": close}


def test_valid_fresh_series() -> None:
    rows = [
        _row("2026-05-30", 3800.0),
        _row("2026-06-01", 3850.0),
        _row("2026-06-02", 3830.0),
    ]
    assert _observable_index_closes(rows, cutoff=_CUTOFF) == (3800.0, 3850.0, 3830.0)


def test_zero_and_invalid_closes_dropped() -> None:
    rows = [
        _row("2026-05-30", 3800.0),
        _row("2026-05-31", 0.0),  # coerced-missing → drop
        _row("2026-06-01", float("nan")),  # non-finite → drop
        _row("2026-06-01", float("inf")),  # non-finite → drop
        _row("2026-06-02", -5.0),  # negative → drop
        _row("2026-06-02", 3830.0),
    ]
    assert _observable_index_closes(rows, cutoff=_CUTOFF) == (3800.0, 3830.0)


def test_future_dated_rows_dropped() -> None:
    rows = [
        _row("2026-06-01", 3850.0),
        _row("2026-06-02", 3830.0),
        _row("2026-06-03", 9999.0),  # after cutoff → not observable
    ]
    assert _observable_index_closes(rows, cutoff=_CUTOFF) == (3850.0, 3830.0)


def test_stale_series_fails_to_empty() -> None:
    # Latest observable close 20 days before the cutoff → stale → NEUTRAL.
    rows = [_row("2026-05-10", 3800.0), _row("2026-05-13", 3810.0)]
    assert _observable_index_closes(rows, cutoff=_CUTOFF) == ()


def test_undated_or_unparseable_rows_dropped() -> None:
    rows = [
        _row(None, 3800.0),
        _row("not-a-date", 3810.0),
        _row("2026-06-02", 3830.0),
    ]
    assert _observable_index_closes(rows, cutoff=_CUTOFF) == (3830.0,)


def test_empty_and_all_invalid_are_empty() -> None:
    assert _observable_index_closes([], cutoff=_CUTOFF) == ()
    assert _observable_index_closes([_row("2026-06-02", 0.0)], cutoff=_CUTOFF) == ()


def test_custom_staleness_bound() -> None:
    rows = [_row("2026-05-28", 3800.0)]  # 5 days before cutoff
    assert _observable_index_closes(rows, cutoff=_CUTOFF, max_staleness_days=3) == ()
    assert _observable_index_closes(
        rows, cutoff=_CUTOFF, max_staleness_days=10
    ) == (3800.0,)
