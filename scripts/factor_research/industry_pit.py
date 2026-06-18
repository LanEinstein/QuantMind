"""Point-in-time 申万 (SW) L1 industry lookup (R2-2 / S2).

The benchmark-relative arm neutralises factors against industry to avoid the
round-1 failure mode (a defensive book that systematically lags a cap-weighted
index when large-cap sectors lead). Industry must be **point-in-time**: a code's
sector can change, and using today's classification to label a 2018 cross-section
is look-ahead. The round-2 ingest stored ``index_member_all`` — the SW membership
table with each code's ``in_date`` / ``out_date`` per industry segment — so any
historical day reconstructs the code's industry as ``in_date <= d < out_date``.

This deliberately uses the SW membership table, NOT ``stock_basic.industry``
(current-only = look-ahead, an explicit round-2 red line). A code with no segment
covering ``d`` returns ``None`` (fail-closed — never an invented bucket); the
caller drops that code-date from neutralisation and the diagnostic discloses the
coverage gap (long-delisted codes may be absent from the current SW table).

dtype-safe read (the round-2 trap): ``in_date`` / ``out_date`` are read as literal
strings (``keep_default_na=False``) so an empty ``out_date`` is ``""`` (= still a
member) and a date never floatifies. Pure + deterministic.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

from .ingest_round2_data import EP_INDEX_MEMBER

VENDOR = "tushare"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD


class _Segment(NamedTuple):
    """One membership window of a code in an SW L1 industry."""

    in_date: str
    out_date: str  # "" = still a member (open window)
    l1_code: str


@dataclass(frozen=True)
class IndustryPIT:
    """Per-code SW L1 membership segments (immutable, PIT-reconstructable)."""

    by_code: dict[str, tuple[_Segment, ...]]

    @classmethod
    def build(cls, store: SnapshotStore, asof: str) -> IndustryPIT:
        """Read the ``index_member_all`` snapshot and index segments by code.

        Rows missing ``in_date`` or ``l1_code`` are skipped (fail-closed — they
        cannot anchor a PIT window). A missing snapshot raises
        :class:`FileNotFoundError`.
        """
        snapshot = store.latest(
            vendor=VENDOR, endpoint=EP_INDEX_MEMBER, trade_date=asof
        )
        if snapshot is None:
            raise FileNotFoundError(f"no index_member_all snapshot as-of {asof}")
        frame = pd.read_csv(
            io.StringIO(snapshot.raw_payload.decode("utf-8")),
            dtype=str,
            keep_default_na=False,
        )
        staged: dict[str, list[_Segment]] = defaultdict(list)
        for row in frame.itertuples(index=False):
            ts = str(getattr(row, "ts_code", "")).strip()
            l1 = str(getattr(row, "l1_code", "")).strip()
            in_date = str(getattr(row, "in_date", "")).strip()
            out_date = str(getattr(row, "out_date", "")).strip()
            if not (ts and l1 and _DATE_RE.match(in_date)):
                continue
            if out_date and not _DATE_RE.match(out_date):
                continue  # malformed out_date → drop the segment fail-closed
            staged[ts].append(_Segment(in_date, out_date, l1))
        return cls(
            by_code={
                code: tuple(sorted(segs, key=lambda s: (s.in_date, s.out_date)))
                for code, segs in staged.items()
            }
        )

    def l1_asof(self, code: str, decision_date: str) -> str | None:
        """SW L1 code for ``code`` on ``decision_date`` (``in_date <= d < out``).

        ``None`` when the code is unknown or no segment covers the date. When
        (rarely) several segments overlap, the latest-starting one wins.
        """
        segments = self.by_code.get(code)
        if not segments:
            return None
        best: _Segment | None = None
        for seg in segments:
            if seg.in_date <= decision_date and (
                not seg.out_date or decision_date < seg.out_date
            ):
                if best is None or seg.in_date > best.in_date:
                    best = seg
        return best.l1_code if best is not None else None

    def coverage(self, codes: Sequence[str], decision_date: str) -> float:
        """Fraction of ``codes`` with a PIT industry on ``decision_date``."""
        if not codes:
            return 0.0
        hit = sum(1 for c in codes if self.l1_asof(c, decision_date) is not None)
        return hit / len(codes)


__all__ = ["IndustryPIT"]
