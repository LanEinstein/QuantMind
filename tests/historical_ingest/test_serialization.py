"""Tests for AE-001 canonical byte-stable serialization.

The PIT snapshot store (R0 §3) is content-addressed by SHA256: an
idempotent re-fetch of the same trade date must serialize to *identical*
bytes so the payload dedups instead of producing a second version. Tushare
may return rows in a different order across calls, so serialization must
canonicalise order while preserving every cell value verbatim.
"""

from __future__ import annotations

import pandas as pd

from backend.data.historical_ingest.serialization import (
    canonical_csv_bytes,
    parse_csv_bytes,
)


def test_row_order_invariant() -> None:
    """Same content, rows shuffled → identical bytes."""
    a = pd.DataFrame(
        {"ts_code": ["000001.SZ", "600519.SH"], "close": [10.5, 1700.0]}
    )
    b = a.iloc[::-1].reset_index(drop=True)  # reversed rows
    assert canonical_csv_bytes(a) == canonical_csv_bytes(b)


def test_column_order_invariant() -> None:
    """Same content, columns reordered → identical bytes."""
    a = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5], "open": [10.0]})
    b = a[["close", "open", "ts_code"]]
    assert canonical_csv_bytes(a) == canonical_csv_bytes(b)


def test_index_dropped() -> None:
    """A non-default index must not leak into the bytes."""
    a = pd.DataFrame({"ts_code": ["x"], "v": [1]})
    b = a.copy()
    b.index = [999]
    assert canonical_csv_bytes(a) == canonical_csv_bytes(b)


def test_values_preserved_verbatim() -> None:
    """No business transform: the original cell values round-trip."""
    df = pd.DataFrame(
        {"ts_code": ["000001.SZ", "000002.SZ"], "close": [10.5, 20.25]}
    )
    back = parse_csv_bytes(canonical_csv_bytes(df))
    # canonical order is lexicographic by stringified row
    assert list(back["ts_code"]) == ["000001.SZ", "000002.SZ"]
    assert list(back["close"]) == [10.5, 20.25]


def test_deterministic_across_calls() -> None:
    """Serialising the same frame twice yields the same bytes."""
    df = pd.DataFrame({"ts_code": ["a", "b"], "v": [1, 2]})
    assert canonical_csv_bytes(df) == canonical_csv_bytes(df)


def test_distinct_content_distinct_bytes() -> None:
    """Different content must not collide to the same address."""
    a = pd.DataFrame({"ts_code": ["a"], "close": [10.0]})
    b = pd.DataFrame({"ts_code": ["a"], "close": [11.0]})
    assert canonical_csv_bytes(a) != canonical_csv_bytes(b)


def test_empty_frame_is_deterministic() -> None:
    """An empty (e.g. holiday) frame still serialises deterministically."""
    a = pd.DataFrame({"ts_code": [], "close": []})
    b = pd.DataFrame({"ts_code": [], "close": []})
    assert canonical_csv_bytes(a) == canonical_csv_bytes(b)


def test_nan_cells_do_not_break_sort() -> None:
    """Mixed NaN/value cells sort without a type-comparison error."""
    df = pd.DataFrame(
        {"ts_code": ["b", "a"], "pe": [float("nan"), 12.3]}
    )
    out = canonical_csv_bytes(df)
    assert isinstance(out, bytes) and len(out) > 0
    # order is stable and content-preserving
    assert canonical_csv_bytes(df) == out
