"""Append-only position-episode store (entry dates for the chandelier stop).

P0-7-amendment-2026-06-04-entry-anchored-chandelier §1.2: the entry-anchored
stop needs each holding's ENTRY DATE ("highest close **since entry**" —
LeBeau's canonical anchoring). Rebuilding that from broker_events reverse-
lookup was rejected as fragile (P-006); this store uses the V-003 /
``takeprofit_ledger`` pattern instead — an explicit append-only JSONL event
log, folded at read time:

* ``opened`` — the code entered the held set (first tick it was seen).
* ``closed`` — the code left the held set (full exit); a later re-buy opens
  a FRESH episode (the anchor restarts, exactly like the take-profit tiers).

The runner calls :meth:`sync` once per tick (idempotent, appends only on
membership changes). Bootstrap caveat (amendment §1.2): positions held
BEFORE this store deploys get their episode opened on the first synced tick
— the anchor window starts late (conservative: a lower anchor means a lower
chandelier, and the initial money-management stop floors the result). The
owner may seed real entry dates by appending ``opened`` rows before boot.

WHY fail-open (mirrors ``fired_trigger_store``): a corrupt/unreadable store
degrades to "no entry date" → the trigger evaluator falls back to the
window-anchored v8 stop for that code, so PROTECTION NEVER DISAPPEARS —
only the anchoring improvement does, loudly logged.

Red lines: records membership only — never constructs an InstructionPlan
(R0 §4); zero ``backend.*`` sub-package imports (pure module).
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from filelock import FileLock

log = structlog.get_logger(component="position_episode_store")

_OPENED = "opened"
_CLOSED = "closed"


class PositionEpisodeStore:
    """Append-only JSONL of holding-episode opened/closed events."""

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._lock = FileLock(str(lock_path or f"{self._path}.lock"))

    @property
    def path(self) -> Path:
        return self._path

    def open_episodes(self) -> dict[str, str]:
        """code → opened trade_date (ISO) for every OPEN episode (folded).

        FAIL-OPEN: any read/parse problem returns what folded so far
        (possibly empty) with a loud error log — a broken store must never
        block the monitoring tick (the evaluator falls back per code).
        """
        episodes: dict[str, str] = {}
        try:
            if not self._path.exists():
                return {}
            for lineno, line in enumerate(
                self._path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    event = str(row["event_type"])
                    code = str(row["code"]).strip()
                    opened = str(row["trade_date"]).strip()
                    if not code or not opened:
                        raise ValueError("empty code/trade_date")
                    if event == _OPENED:
                        # Keep the FIRST opened date (a manual seed row or a
                        # duplicate sync must never advance the anchor start).
                        episodes.setdefault(code, opened)
                    elif event == _CLOSED:
                        episodes.pop(code, None)
                    else:
                        raise ValueError(f"unknown event_type {event!r}")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    log.error(
                        "position_episode_store_corrupt_row",
                        path=str(self._path),
                        lineno=lineno,
                        error=str(exc),
                    )
        except OSError as exc:
            log.error(
                "position_episode_store_read_failed",
                path=str(self._path),
                error=str(exc),
            )
        return episodes

    def _append(self, event_type: str, code: str, trade_date: str) -> None:
        row = {"event_type": event_type, "code": code, "trade_date": trade_date}
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        row,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )

    def sync(self, held_codes: frozenset[str], *, trade_date: str) -> dict[str, str]:
        """Reconcile episodes with the held set; return the folded open map.

        Opens an episode for every newly-held code and closes the episode of
        every code that left the held set. Idempotent per tick (appends only
        on membership changes). FAIL-OPEN: an I/O error logs loudly and
        returns the pre-sync fold — the tick continues with what is known.
        """
        episodes = self.open_episodes()
        try:
            for code in sorted(held_codes - episodes.keys()):
                self._append(_OPENED, code, trade_date)
                episodes[code] = trade_date
                log.info(
                    "position_episode_opened", code=code, trade_date=trade_date
                )
            for code in sorted(episodes.keys() - held_codes):
                self._append(_CLOSED, code, trade_date)
                episodes.pop(code, None)
                log.info(
                    "position_episode_closed", code=code, trade_date=trade_date
                )
        except OSError as exc:
            log.error(
                "position_episode_store_write_failed",
                path=str(self._path),
                error=str(exc),
            )
        return episodes


__all__ = ["PositionEpisodeStore"]
