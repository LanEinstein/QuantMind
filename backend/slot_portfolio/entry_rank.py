"""First-seen entry-rank ledger (Phase V-004).

``incumbent_independently_weak`` condition 5 ("rank deteriorated >= 20 pct since
entry") needs an *entry baseline* — the Line-1 percentile a holding had when it
was bought. Phase W's ``PositionThesis`` will persist a richer buy-time context;
until then this self-contained, append-only ledger records each holding's
**first-observed** Line-1 percentile as its entry baseline (owner decision
2026-06-01). Absent a baseline (e.g. holdings that predate this feature),
condition 5 fails closed (no deterioration → not weak → no rotation).

Open/close lifecycle (append-only event log, same discipline as
``rotation_intent``): an ``OPENED`` event records the baseline the first time a
code is seen held; a ``CLOSED`` event is appended when it leaves the held set.
A code sold then re-bought therefore gets a **fresh** baseline (the old record
was closed when it exited) — never a stale one. Deterministic + replayable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock

log = structlog.get_logger(component="slot_portfolio.entry_rank")


class EntryRankError(RuntimeError):
    """Raised on a corrupt entry-rank row."""


class EntryRankEventType(StrEnum):
    OPENED = "opened"  # a holding first observed — its entry baseline
    CLOSED = "closed"  # a holding left the held set — baseline retired


@dataclass(frozen=True)
class EntryRank:
    """One holding's entry baseline (the percentile it had when first observed)."""

    code: str
    first_seen_trade_date: str
    entry_percentile: float
    entry_score: float


@dataclass(frozen=True)
class _EntryEvent:
    event_type: EntryRankEventType
    code: str
    trade_date: str
    entry_percentile: float = 0.0
    entry_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "code": self.code,
            "trade_date": self.trade_date,
            "entry_percentile": self.entry_percentile,
            "entry_score": self.entry_score,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _EntryEvent:
        try:
            return cls(
                event_type=EntryRankEventType(raw["event_type"]),
                code=str(raw["code"]),
                trade_date=str(raw["trade_date"]),
                entry_percentile=float(raw.get("entry_percentile", 0.0)),
                entry_score=float(raw.get("entry_score", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EntryRankError(f"malformed entry-rank row: {exc}") from exc


class EntryRankStore:
    """Append-only ledger of holding entry baselines (open/close lifecycle)."""

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._lock = FileLock(str(lock_path or f"{self._path}.lock"))

    @property
    def path(self) -> Path:
        return self._path

    def _append(self, event: _EntryEvent) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        event.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )

    def _load(self) -> tuple[_EntryEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[_EntryEvent] = []
        for lineno, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EntryRankError(
                    f"corrupt entry-rank row at {self._path}:{lineno}: {exc}"
                ) from exc
            events.append(_EntryEvent.from_dict(row))
        return tuple(events)

    def open_entries(self) -> dict[str, EntryRank]:
        """code → current open :class:`EntryRank` (folded from the event log)."""
        out: dict[str, EntryRank] = {}
        for ev in self._load():
            if ev.event_type is EntryRankEventType.OPENED:
                out[ev.code] = EntryRank(
                    code=ev.code,
                    first_seen_trade_date=ev.trade_date,
                    entry_percentile=ev.entry_percentile,
                    entry_score=ev.entry_score,
                )
            elif ev.event_type is EntryRankEventType.CLOSED:
                out.pop(ev.code, None)
        return out

    def entry_percentile_for(self, code: str) -> float | None:
        """The open entry baseline percentile for ``code`` (None if unknown)."""
        entry = self.open_entries().get(code)
        return entry.entry_percentile if entry is not None else None

    def sync_holdings(
        self,
        held_codes: frozenset[str],
        *,
        trade_date: str,
        percentile_by_code: Mapping[str, float],
        score_by_code: Mapping[str, float],
    ) -> tuple[str, ...]:
        """Reconcile the ledger with the current held set (append-only).

        Records an ``OPENED`` baseline for each newly-held code (first seen) and
        a ``CLOSED`` event for each previously-open code no longer held. Returns
        the codes newly opened this call. A newly-held code with no Line-1
        percentile today (e.g. it fell out of the universe) gets no baseline —
        it is recorded the first day it does have one (fail-open on the open
        side; condition 5 stays fail-closed until a baseline exists).
        """
        open_now = self.open_entries()
        newly_opened: list[str] = []
        for code in sorted(held_codes):
            if code in open_now or code not in percentile_by_code:
                continue
            self._append(
                _EntryEvent(
                    event_type=EntryRankEventType.OPENED,
                    code=code,
                    trade_date=trade_date,
                    entry_percentile=float(percentile_by_code[code]),
                    entry_score=float(score_by_code.get(code, 0.0)),
                )
            )
            newly_opened.append(code)
        for code in sorted(open_now):
            if code not in held_codes:
                self._append(
                    _EntryEvent(
                        event_type=EntryRankEventType.CLOSED,
                        code=code,
                        trade_date=trade_date,
                    )
                )
        if newly_opened:
            log.info(
                "entry_rank_opened", codes=newly_opened, trade_date=trade_date
            )
        return tuple(newly_opened)


__all__ = [
    "EntryRank",
    "EntryRankError",
    "EntryRankEventType",
    "EntryRankStore",
]
