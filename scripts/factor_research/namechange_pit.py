"""Point-in-time share-name / ST-flag lookup from namechange snapshots (R3-2).

Reads the year-paged ``namechange`` snapshots (R3-1) and reconstructs each code's
name **in effect on any date**, so a backtest can apply a *point-in-time* ST
exclusion — closing the ``build_factor_panel`` gap (it previously had no PIT name
source and leaned on the size/liquidity filters to remove ST names implicitly).

A namechange row is a name's validity window ``[start_date, end_date)`` (an empty
``end_date`` = still in effect). For decision date ``d`` the name is the window
with ``start_date <= d`` and (``end_date`` empty or ``d < end_date``); when
several match, the latest-starting one wins. A code with no namechange row keeps
its original (never-renamed) name and is treated as non-ST.

ST detection: the Tushare name carries the regulatory prefix — ``ST`` / ``*ST`` /
the historical ``SST`` / ``S*ST`` (risk-warning) and ``退`` (delisting). Any of
these marks the name ST/at-risk → excluded.

dtype-safe read (``keep_default_na=False``): an empty ``end_date`` stays ``""``
(open window) and dates never floatify. Pure + deterministic; reads bytes from the
``SnapshotStore`` only.
"""

from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

from .ingest_round2_data import EP_NAMECHANGE

VENDOR = "tushare"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
# Regulatory risk-warning / delisting markers in the share name.
_ST_PREFIXES: tuple[str, ...] = ("*ST", "SST", "S*ST", "ST")


def _is_st_name(name: str) -> bool:
    """True iff the (stripped) name carries an ST / risk-warning / 退 marker."""
    n = name.strip().replace(" ", "")
    if not n:
        return False
    if any(n.startswith(p) for p in _ST_PREFIXES):
        return True
    return "退" in n  # delisting names (退市XX / XX退)


def namechange_snapshot_keys(snapshot_root: str) -> list[str]:
    """All stored ``namechange`` snapshot keys (the per-year / asof pages).

    Raises :class:`FileNotFoundError` when NO namechange page is present (codex
    P2): the R3 PIT ST exclusion is a promised universe filter, so missing
    namechange data must fail closed — an empty list would build an empty
    ``NameChangePIT`` whose ``is_st_asof`` is always False, silently disabling
    the exclusion and admitting ST names into the R3 universe.
    """
    index_path = Path(snapshot_root) / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"snapshot index not found: {index_path}")
    keys: set[str] = set()
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("endpoint") == EP_NAMECHANGE:
                keys.add(str(rec["trade_date"]))
    if not keys:
        raise FileNotFoundError(
            f"no namechange snapshot in {snapshot_root} — run R3-1 ingest "
            "(--phase round3) before the R3 panel build (fail-closed: the PIT "
            "ST exclusion must not silently no-op)"
        )
    return sorted(keys)


class _NameWindow(NamedTuple):
    """One name's validity window for a code."""

    start_date: str
    end_date: str  # "" = still in effect (open window)
    name: str


@dataclass(frozen=True)
class NameChangePIT:
    """Per-code name windows reconstructed from namechange snapshots (immutable)."""

    by_code: dict[str, tuple[_NameWindow, ...]]

    @classmethod
    def build(cls, store: SnapshotStore, keys: Sequence[str]) -> NameChangePIT:
        """Union the namechange rows across all ``keys`` into per-code windows.

        A missing snapshot for a listed key raises :class:`FileNotFoundError`
        (fail-closed); an empty-but-headered page contributes no rows. Rows
        without a usable ``start_date`` are dropped (cannot anchor a window).
        Duplicate ``(start_date, name)`` windows (a code appearing under several
        year pages) are de-duplicated.
        """
        staged: dict[str, set[_NameWindow]] = defaultdict(set)
        for key in keys:
            snapshot = store.latest(
                vendor=VENDOR, endpoint=EP_NAMECHANGE, trade_date=key
            )
            if snapshot is None:
                raise FileNotFoundError(f"no namechange snapshot for key {key}")
            frame = pd.read_csv(
                io.StringIO(snapshot.raw_payload.decode("utf-8")),
                dtype=str,
                keep_default_na=False,
            )
            for row in frame.itertuples(index=False):
                ts = str(getattr(row, "ts_code", "")).strip()
                start = str(getattr(row, "start_date", "")).strip()
                end = str(getattr(row, "end_date", "")).strip()
                name = str(getattr(row, "name", "")).strip()
                if not (ts and name and _DATE_RE.match(start)):
                    continue
                if end and not _DATE_RE.match(end):
                    end = ""  # malformed end → treat as open
                staged[ts].add(_NameWindow(start, end, name))
        return cls(
            by_code={
                code: tuple(sorted(ws, key=lambda w: (w.start_date, w.end_date)))
                for code, ws in staged.items()
            }
        )

    def name_asof(self, code: str, decision_date: str) -> str | None:
        """The name in effect for ``code`` on ``decision_date`` (``None`` if unknown).

        ``None`` when the code has no namechange row (never renamed) — the caller
        treats that as the original, non-ST name.
        """
        windows = self.by_code.get(code)
        if not windows:
            return None
        best: _NameWindow | None = None
        for w in windows:
            if w.start_date <= decision_date and (
                not w.end_date or decision_date < w.end_date
            ):
                if best is None or w.start_date > best.start_date:
                    best = w
        return best.name if best is not None else None

    def is_st_asof(self, code: str, decision_date: str) -> bool:
        """True iff ``code`` carried an ST / 退 name on ``decision_date`` (PIT).

        A code with no known name on that date (never renamed, or renamed only
        later) is NOT ST — fail-open to *included*, since the size/liquidity
        filters remain the backstop and a missing namechange row means the
        original (non-ST) name was in effect.
        """
        name = self.name_asof(code, decision_date)
        return _is_st_name(name) if name is not None else False


__all__ = ["NameChangePIT", "namechange_snapshot_keys"]
