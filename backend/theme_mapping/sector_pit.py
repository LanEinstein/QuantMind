"""Point-in-time 申万 L3 membership from ``index_member_all`` (AF-001).

A theme's stock set is defined by 申万 L3 industries, and a stock's L3 changes
over time, so labelling a historical cross-section with today's classification is
look-ahead. ``index_member_all`` carries each code's ``in_date``/``out_date`` per
L3 segment, so any decision date reconstructs the code's L3 as
``in_date <= d < out_date`` (an empty ``out_date`` = still a member).

This mirrors the round-2 ``industry_pit`` reader (same dtype-safe, fail-closed
discipline) but resolves the **L3** leaf (312 industries) the policy-theme
mapping keys on, rather than L1. A code with no segment covering ``d`` resolves
to an empty set (fail-closed — never an invented bucket). Pure + deterministic.

**Data caveat (honest):** the stored ``index_member_all`` snapshot is presently a
*current roster* — every row is an open window (blank ``out_date``, ``in_date`` =
listing date), so a code that was reclassified or delisted is not captured with a
closed window. The window logic here is correct, but historical L3 fidelity is
bounded by the source table (the same ~66% coverage caveat round-2 noted), so the
backtest's『跟主旋律择场』anti-hindsight claim is only as strong as a future ingest
of a truly historical (closed-window) ``index_member_all``.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

VENDOR = "tushare"
ENDPOINT_INDEX_MEMBER = "index_member_all"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD


class _L3Segment(NamedTuple):
    """One membership window of a code in a 申万 L3 industry."""

    in_date: str
    out_date: str  # "" = still a member (open window)
    l3_code: str


@dataclass(frozen=True)
class SectorMembershipPIT:
    """Per-code 申万 L3 membership segments (immutable, PIT-reconstructable)."""

    by_code: dict[str, tuple[_L3Segment, ...]]

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> SectorMembershipPIT:
        """Build from an ``index_member_all`` frame (columns include
        ``ts_code``/``l3_code``/``in_date``/``out_date``).

        Rows missing ``ts_code``/``l3_code``/a valid ``in_date`` are skipped
        (fail-closed — they cannot anchor a PIT window); a malformed
        ``out_date`` drops that one segment, not the code.
        """
        staged: dict[str, list[_L3Segment]] = defaultdict(list)
        for row in frame.itertuples(index=False):
            ts = str(getattr(row, "ts_code", "")).strip()
            l3 = str(getattr(row, "l3_code", "")).strip()
            in_date = str(getattr(row, "in_date", "")).strip()
            out_date = str(getattr(row, "out_date", "")).strip()
            if not (ts and l3 and _DATE_RE.match(in_date)):
                continue
            if out_date and not _DATE_RE.match(out_date):
                continue
            staged[ts].append(_L3Segment(in_date, out_date, l3))
        return cls(
            by_code={
                code: tuple(sorted(segs, key=lambda s: (s.in_date, s.out_date)))
                for code, segs in staged.items()
            }
        )

    @classmethod
    def from_store(cls, store: SnapshotStore, asof: str) -> SectorMembershipPIT:
        """Read the latest ``index_member_all`` snapshot as-of ``asof``."""
        snapshot = store.latest(
            vendor=VENDOR, endpoint=ENDPOINT_INDEX_MEMBER, trade_date=asof
        )
        if snapshot is None:
            raise FileNotFoundError(f"no index_member_all snapshot as-of {asof}")
        frame = pd.read_csv(
            io.StringIO(snapshot.raw_payload.decode("utf-8")),
            dtype=str,
            keep_default_na=False,
        )
        return cls.from_frame(frame)

    def l3_asof(self, code: str, decision_date: str) -> frozenset[str]:
        """申万 L3 code(s) for ``code`` on ``decision_date`` (``in <= d < out``).

        Empty set when the code is unknown or no segment covers the date.
        Normally a single L3 covers a date; overlapping segments all count.
        """
        segments = self.by_code.get(code)
        if not segments:
            return frozenset()
        return frozenset(
            seg.l3_code
            for seg in segments
            if seg.in_date <= decision_date
            and (not seg.out_date or decision_date < seg.out_date)
        )


__all__ = ["SectorMembershipPIT"]
