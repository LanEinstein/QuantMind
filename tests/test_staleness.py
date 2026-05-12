"""Pure-function tests for :mod:`backend.data.staleness` (C-004 / P0-8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from backend.data.staleness import StalenessReport, evaluate_staleness


class TestEvaluateStaleness:
    """Locked semantics for the P0-8 staleness signal."""

    def test_fresh_quote_is_not_stale(self) -> None:
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        snap = now - timedelta(seconds=2)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert isinstance(report, StalenessReport)
        assert report.is_stale is False
        assert report.age_seconds == 2.0
        assert report.quote_source == "adata"

    def test_boundary_equal_threshold_is_not_stale(self) -> None:
        """``age == threshold`` is NOT stale — only strictly greater is."""
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        snap = now - timedelta(seconds=5)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert report.age_seconds == 5.0
        assert report.is_stale is False

    def test_age_greater_than_threshold_is_stale(self) -> None:
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        snap = now - timedelta(seconds=6)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert report.age_seconds == 6.0
        assert report.is_stale is True

    def test_future_dated_quote_is_not_stale(self) -> None:
        """Clock-skewed future quotes report negative age but is_stale=False."""
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        snap = now + timedelta(seconds=3)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert report.age_seconds == -3.0
        assert report.is_stale is False

    def test_mixed_tz_awareness_raises(self) -> None:
        """Aware vs naive datetimes must not silently compare."""
        aware = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        naive = datetime(2026, 5, 12, 9, 30, 0)
        with pytest.raises(ValueError, match="tz-awareness"):
            evaluate_staleness(
                snapshot_at=naive, now=aware, quote_source="adata",
                threshold_seconds=5.0,
            )
        with pytest.raises(ValueError, match="tz-awareness"):
            evaluate_staleness(
                snapshot_at=aware, now=naive, quote_source="adata",
                threshold_seconds=5.0,
            )

    def test_different_tz_aware_pair_normalises_to_utc(self) -> None:
        """Two aware datetimes in different tz compare in UTC."""
        shanghai = timezone(timedelta(hours=8))
        now_sh = datetime(2026, 5, 12, 17, 30, 0, tzinfo=shanghai)  # 09:30 UTC
        snap_utc = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        report = evaluate_staleness(
            snapshot_at=snap_utc, now=now_sh, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert report.age_seconds == 0.0
        assert report.is_stale is False

    def test_threshold_is_propagated_through(self) -> None:
        """The threshold the caller picked appears verbatim on the report."""
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        snap = now - timedelta(seconds=1)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="akshare",
            threshold_seconds=10.0,
        )
        assert report.threshold_seconds == 10.0
        assert report.quote_source == "akshare"

    def test_naive_naive_pair_is_allowed(self) -> None:
        """Two naive datetimes are fine (e.g. legacy callers)."""
        now = datetime(2026, 5, 12, 9, 30, 0)
        snap = now - timedelta(seconds=3)
        report = evaluate_staleness(
            snapshot_at=snap, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        assert report.age_seconds == 3.0
        assert report.is_stale is False

    def test_report_is_frozen(self) -> None:
        """StalenessReport is a frozen dataclass."""
        now = datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC)
        report = evaluate_staleness(
            snapshot_at=now, now=now, quote_source="adata",
            threshold_seconds=5.0,
        )
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            report.is_stale = True  # type: ignore[misc]
