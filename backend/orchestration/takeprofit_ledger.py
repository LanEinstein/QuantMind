"""Append-only take-profit tier ledger (D1-d tiered scale-out).

P0-10-amendment-line2-2026-06-04: the tiered TAKE_PROFIT ladder ("+1R sell
half → +2R sell another tranche → residual rides the trailing stop") needs
to know which tiers the OPEN holding episode has already taken. The
2026-05-31 WON'T-DO rejected rebuilding that state from ``broker_events``
reverse-lookup (fragile); this ledger uses the V-003 / ``entry_rank``
pattern instead — an explicit append-only JSONL event log folded at read
time, replayable bit-exact.

Events:

* ``TIER_TAKEN`` — a tiered TAKE_PROFIT was ROUTED (validated + dispatched)
  for ``code`` at 1-based ``tier``.
* ``EPISODE_CLOSED`` — the holding episode ended (the code left the held
  set); a later re-buy starts a fresh episode from tier 1.

Red lines: this module records tiers only — it never constructs an
InstructionPlan (R0 §4) and is consumed solely by the Line-2 intraday
runner. Corrupt rows fail closed (raise), mirroring ``entry_rank``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock

log = structlog.get_logger(component="takeprofit_ledger")


class TakeProfitLedgerError(RuntimeError):
    """Corrupt / unreadable ledger row — fail closed, never guess tiers."""


class TakeProfitEventType(StrEnum):
    TIER_TAKEN = "tier_taken"
    EPISODE_CLOSED = "episode_closed"


@dataclass(frozen=True)
class _TierEvent:
    event_type: TakeProfitEventType
    code: str
    trade_date: str
    tier: int | None = None
    signal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "event_type": self.event_type.value,
            "code": self.code,
            "trade_date": self.trade_date,
        }
        if self.tier is not None:
            out["tier"] = self.tier
        if self.signal_id is not None:
            out["signal_id"] = self.signal_id
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _TierEvent:
        try:
            event = cls(
                event_type=TakeProfitEventType(str(raw["event_type"])),
                code=str(raw["code"]),
                trade_date=str(raw["trade_date"]),
                tier=int(raw["tier"]) if raw.get("tier") is not None else None,
                signal_id=(
                    str(raw["signal_id"])
                    if raw.get("signal_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TakeProfitLedgerError(
                f"corrupt take-profit ledger row: {raw!r}: {exc}"
            ) from exc
        # A syntactically-valid but semantically-broken TIER_TAKEN row must
        # not silently advance/suppress the ladder — fail closed (codex P2).
        if event.event_type is TakeProfitEventType.TIER_TAKEN and (
            event.tier is None or event.tier <= 0
        ):
            raise TakeProfitLedgerError(
                f"corrupt take-profit ledger row (TIER_TAKEN without a "
                f"positive tier): {raw!r}"
            )
        return event


class TakeProfitLedgerStore:
    """Append-only ledger of per-episode take-profit tiers."""

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._lock = FileLock(str(lock_path or f"{self._path}.lock"))

    @property
    def path(self) -> Path:
        return self._path

    def _append(self, event: _TierEvent) -> None:
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

    def _load(self) -> tuple[_TierEvent, ...]:
        if not self._path.exists():
            return ()
        events: list[_TierEvent] = []
        for lineno, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TakeProfitLedgerError(
                    f"corrupt take-profit ledger row at "
                    f"{self._path}:{lineno}: {exc}"
                ) from exc
            events.append(_TierEvent.from_dict(row))
        return tuple(events)

    def tiers_taken(self) -> dict[str, int]:
        """code → count of tiers taken in the OPEN episode (folded)."""
        out: dict[str, int] = {}
        for ev in self._load():
            if ev.event_type is TakeProfitEventType.TIER_TAKEN:
                out[ev.code] = out.get(ev.code, 0) + 1
            elif ev.event_type is TakeProfitEventType.EPISODE_CLOSED:
                out.pop(ev.code, None)
        return out

    def record_tier(
        self, code: str, *, tier: int, trade_date: str, signal_id: str
    ) -> None:
        """Append a TIER_TAKEN event (call only for ROUTED take-profits)."""
        self._append(
            _TierEvent(
                event_type=TakeProfitEventType.TIER_TAKEN,
                code=code,
                trade_date=trade_date,
                tier=tier,
                signal_id=signal_id,
            )
        )
        log.info(
            "takeprofit_tier_recorded",
            code=code,
            tier=tier,
            trade_date=trade_date,
        )

    def sync_episodes(
        self, held_codes: frozenset[str], *, trade_date: str
    ) -> tuple[str, ...]:
        """Close the episode for every open-tier code no longer held.

        A full exit (stop / thesis break / manual) ends the episode; a later
        re-buy starts fresh from tier 1. Returns the codes closed this call.
        """
        open_now = self.tiers_taken()
        closed: list[str] = []
        for code in sorted(open_now):
            if code not in held_codes:
                self._append(
                    _TierEvent(
                        event_type=TakeProfitEventType.EPISODE_CLOSED,
                        code=code,
                        trade_date=trade_date,
                    )
                )
                closed.append(code)
        if closed:
            log.info(
                "takeprofit_episodes_closed",
                codes=closed,
                trade_date=trade_date,
            )
        return tuple(closed)


__all__ = [
    "TakeProfitEventType",
    "TakeProfitLedgerError",
    "TakeProfitLedgerStore",
]
