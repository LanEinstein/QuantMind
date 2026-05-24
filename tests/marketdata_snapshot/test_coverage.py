"""K-003 — CoverageManifest: prevent a partial fetch masquerading as full.

Red line A.2 (R0 §3): a row_count alone lets a partial universe pull
silently stand in for the full market. CoverageManifest records the
requested vs delivered universe so completeness < 1.0 (and a non-empty
missing_symbols set) flags the gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.marketdata_snapshot.coverage import (
    CoverageManifest,
    CoverageStore,
)


def _manifest(
    requested: tuple[str, ...],
    delivered: tuple[str, ...],
    *,
    endpoint: str = "daily",
    session_end: str = "20260522",
) -> CoverageManifest:
    return CoverageManifest(
        granularity="daily",
        endpoint=endpoint,
        params={"trade_date": session_end},
        session_start=session_end,
        session_end=session_end,
        requested_universe=requested,
        delivered_universe=delivered,
    )


class TestCompletenessSemantics:
    def test_full_delivery_is_complete(self) -> None:
        m = _manifest(("000001.SZ", "600519.SH"), ("000001.SZ", "600519.SH"))
        assert m.completeness == 1.0
        assert m.missing_symbols == ()
        assert m.is_complete is True

    def test_partial_delivery_flagged(self) -> None:
        m = _manifest(
            ("000001.SZ", "600519.SH", "300750.SZ"), ("000001.SZ", "600519.SH")
        )
        assert m.missing_symbols == ("300750.SZ",)
        assert m.completeness == pytest.approx(2 / 3)
        assert m.is_complete is False

    def test_delivered_superset_still_complete_for_requested(self) -> None:
        """Extra delivered symbols don't reduce completeness; requested
        is the denominator."""
        m = _manifest(("000001.SZ",), ("000001.SZ", "600519.SH"))
        assert m.completeness == 1.0
        assert m.missing_symbols == ()
        assert m.is_complete is True

    def test_empty_requested_is_complete(self) -> None:
        m = _manifest((), ())
        assert m.completeness == 1.0
        assert m.is_complete is True

    def test_model_is_frozen(self) -> None:
        m = _manifest(("000001.SZ",), ("000001.SZ",))
        with pytest.raises(Exception):
            m.endpoint = "x"  # type: ignore[misc]


class TestCoverageStore:
    def test_put_get_roundtrip(self, tmp_path: Path) -> None:
        store = CoverageStore(root=tmp_path)
        m = _manifest(("000001.SZ", "600519.SH"), ("000001.SZ",))
        store.put(m)
        loaded = store.get(endpoint="daily", session_end="20260522")
        assert loaded is not None
        assert loaded.missing_symbols == ("600519.SH",)
        assert loaded.completeness == 0.5

    def test_reopened_store_reads_offline(self, tmp_path: Path) -> None:
        CoverageStore(root=tmp_path).put(
            _manifest(("000001.SZ",), ("000001.SZ",))
        )
        assert (
            CoverageStore(root=tmp_path).get(
                endpoint="daily", session_end="20260522"
            )
            is not None
        )

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = CoverageStore(root=tmp_path)
        assert store.get(endpoint="daily", session_end="20990101") is None
