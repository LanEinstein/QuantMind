"""Append-only PositionThesis store (Phase W-001).

Explicit buy-time persistence — the root-cause fix for the P-006 fragility
(P0-10-amendment-line2-2026-05-31 rejected reconstructing a thesis from
``broker_events``, which lack ``evidence_ids``). The thesis is written **once**
when the BUY routes and read back verbatim; it is never re-derived.

Same append-only JSONL + open/close lifecycle discipline as
``slot_portfolio.entry_rank`` / ``rotation_intent`` (the owner-approved Phase V
precedent): an ``OPENED`` event records the full thesis the first time a code's
BUY routes; a ``CLOSED`` event retires it when the position leaves the held set.
A code sold then re-bought therefore gets a **fresh** thesis (the old one was
closed when it exited) — never a stale one. Deterministic + replayable; the
store imports no LLM/agents/mirofish and constructs no InstructionPlan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock

from backend.models.position_thesis import PositionThesis

log = structlog.get_logger(component="position_thesis.store")


class PositionThesisError(RuntimeError):
    """Raised on a corrupt thesis row."""


class ThesisEventType(StrEnum):
    OPENED = "opened"  # a BUY routed — its full buy-time thesis recorded
    CLOSED = "closed"  # the position left the held set — thesis retired


@dataclass(frozen=True)
class _ThesisEvent:
    event_type: ThesisEventType
    code: str
    trade_date: str
    thesis: PositionThesis | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_type": self.event_type.value,
            "code": self.code,
            "trade_date": self.trade_date,
        }
        if self.thesis is not None:
            payload["thesis"] = self.thesis.model_dump(mode="json")
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _ThesisEvent:
        try:
            event_type = ThesisEventType(raw["event_type"])
            thesis_raw = raw.get("thesis")
            # Validate through JSON mode (``model_validate_json``) so the
            # strict model accepts the JSON-native forms (ISO datetime string,
            # arrays → tuples) the append wrote — ``model_validate`` on the raw
            # dict would reject them under strict mode.
            thesis = (
                PositionThesis.model_validate_json(json.dumps(thesis_raw))
                if thesis_raw is not None
                else None
            )
            return cls(
                event_type=event_type,
                code=str(raw["code"]),
                trade_date=str(raw["trade_date"]),
                thesis=thesis,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PositionThesisError(f"malformed thesis row: {exc}") from exc


class PositionThesisStore:
    """Append-only ledger of position theses (open/close lifecycle)."""

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._lock = FileLock(str(lock_path or f"{self._path}.lock"))

    @property
    def path(self) -> Path:
        return self._path

    def _append(self, event: _ThesisEvent) -> None:
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

    def _load(self) -> tuple[_ThesisEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[_ThesisEvent] = []
        for lineno, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PositionThesisError(
                    f"corrupt thesis row at {self._path}:{lineno}: {exc}"
                ) from exc
            events.append(_ThesisEvent.from_dict(row))
        return tuple(events)

    def open_theses(self) -> dict[str, PositionThesis]:
        """code → current open :class:`PositionThesis` (folded from the log)."""
        out: dict[str, PositionThesis] = {}
        for ev in self._load():
            if ev.event_type is ThesisEventType.OPENED and ev.thesis is not None:
                out[ev.code] = ev.thesis
            elif ev.event_type is ThesisEventType.CLOSED:
                out.pop(ev.code, None)
        return out

    def thesis_for(self, code: str) -> PositionThesis | None:
        """The current open thesis for ``code`` (None if unknown / closed)."""
        return self.open_theses().get(code)

    def closed_theses_on(self, trade_date: str) -> dict[str, PositionThesis]:
        """code → thesis retired by a CLOSED event dated ``trade_date``.

        AA-002 (codex Phase-AA P2 fix): the 18:00 attribution review
        needs the entry price for positions fully sold the same day —
        but the 17:30 thesis sync has already CLOSED those theses, so
        ``open_theses`` no longer sees them. Read-only fold; a same-day
        close-then-reopen-then-close keeps the latest closed thesis.
        """
        open_now: dict[str, PositionThesis] = {}
        closed: dict[str, PositionThesis] = {}
        for ev in self._load():
            if ev.event_type is ThesisEventType.OPENED and (
                ev.thesis is not None
            ):
                open_now[ev.code] = ev.thesis
            elif ev.event_type is ThesisEventType.CLOSED:
                retired = open_now.pop(ev.code, None)
                if retired is not None and ev.trade_date == trade_date:
                    closed[ev.code] = retired
        return closed

    def open_thesis(self, thesis: PositionThesis) -> bool:
        """Record a buy-time thesis. Idempotent on a re-routed instruction_id.

        Returns ``True`` when a new OPENED event was appended, ``False`` when an
        open thesis for the code already carries the same ``instruction_id`` (a
        same-day cron re-run) — so a re-run never grows the log unbounded.
        """
        existing = self.open_theses().get(thesis.stock_code)
        if existing is not None and existing.instruction_id == thesis.instruction_id:
            return False
        self._append(
            _ThesisEvent(
                event_type=ThesisEventType.OPENED,
                code=thesis.stock_code,
                trade_date=thesis.trade_date,
                thesis=thesis,
            )
        )
        log.info(
            "position_thesis_opened",
            code=thesis.stock_code,
            instruction_id=thesis.instruction_id,
            trade_date=thesis.trade_date,
        )
        return True

    def close_position(self, code: str, *, trade_date: str) -> bool:
        """Retire the open thesis for ``code`` (no-op when none is open)."""
        if code not in self.open_theses():
            return False
        self._append(
            _ThesisEvent(
                event_type=ThesisEventType.CLOSED,
                code=code,
                trade_date=trade_date,
            )
        )
        log.info("position_thesis_closed", code=code, trade_date=trade_date)
        return True

    def sync_holdings(
        self, held_codes: frozenset[str], *, trade_date: str
    ) -> tuple[str, ...]:
        """Close theses for codes no longer held (append-only). Returns closed.

        Open is explicit (``open_thesis`` at buy with the full context); this
        only retires theses whose position has exited so a re-bought code gets a
        fresh thesis rather than a stale one.
        """
        closed: list[str] = []
        for code in sorted(self.open_theses()):
            if code not in held_codes:
                self.close_position(code, trade_date=trade_date)
                closed.append(code)
        return tuple(closed)


__all__ = [
    "PositionThesisError",
    "PositionThesisStore",
    "ThesisEventType",
]
