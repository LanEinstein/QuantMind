"""Canonical, byte-stable serialization for PIT snapshot payloads (AE-001).

The K-002 ``SnapshotStore`` is content-addressed by ``sha256(raw_payload)``.
For the idempotency red line — *幂等重摄不重复写* — an offline re-fetch of the
same trade date must produce **identical bytes** so the payload deduplicates
to one file instead of forming a spurious second version. Tushare can return
the same logical full-market frame with rows (and occasionally columns) in a
different order, so we canonicalise ordering while preserving every cell value
verbatim (R0 §3: store the *raw, un-adjusted* bytes — no business transform).

Canonical form:
* columns sorted lexicographically;
* rows sorted by the lexicographic tuple of their stringified cells (a sort
  *key* only — the emitted values are the originals, never the str cast);
* the index dropped;
* a fixed CSV dialect (``\\n`` terminator, UTF-8, no index column).
"""

from __future__ import annotations

from io import StringIO

import pandas as pd

_LINE_TERMINATOR = "\n"
_ENCODING = "utf-8"


def canonical_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize ``df`` to deterministic CSV bytes (see module docstring).

    Args:
        df: A vendor DataFrame (e.g. a Tushare full-market ``daily`` pull).

    Returns:
        UTF-8 CSV bytes that are invariant to input row/column ordering and
        to the DataFrame index, with cell values preserved verbatim.
    """
    # Sort columns; ``sorted`` over the Index gives a plain list of labels.
    ordered_cols = sorted(map(str, df.columns))
    framed = df.copy()
    framed.columns = [str(c) for c in framed.columns]
    framed = framed[ordered_cols]

    if not framed.empty:
        # Build a stringified sort key so heterogeneous / NaN cells never
        # raise a type-comparison error, then reorder the *original* rows by
        # that key (stable). Values themselves are untouched.
        key = framed.astype(str)
        order = key.sort_values(
            by=ordered_cols, kind="stable"
        ).index
        framed = framed.loc[order]

    framed = framed.reset_index(drop=True)
    text = framed.to_csv(index=False, lineterminator=_LINE_TERMINATOR)
    return text.encode(_ENCODING)


def parse_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Round-trip helper: parse canonical CSV bytes back to a DataFrame.

    Used by the adjustment-factor as-of reconstruction (AE-001 acceptance:
    *复权 as-of bit-exact 重建*) and by tests. Type inference is left to
    pandas; callers that need exact dtypes cast explicitly.

    Args:
        raw: Bytes previously produced by :func:`canonical_csv_bytes`.

    Returns:
        The parsed DataFrame (empty frame for header-only bytes).
    """
    return pd.read_csv(StringIO(raw.decode(_ENCODING)))


__all__ = ["canonical_csv_bytes", "parse_csv_bytes"]
