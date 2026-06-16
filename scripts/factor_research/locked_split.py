"""Sacred train/validation/test split loader + guard (Phase 2c/3).

Loads ``config/research/test_set_lock.json`` and re-derives the three date
windows from the live trading calendar, **verifying each window's committed
``dates_sha256``**. Any drift in the data or the boundaries (a re-ingest that
added/removed a day, a hand-edit of the lock) makes a hash mismatch and fails
closed — so a development run can never silently operate on a shifted window.

Every Phase 3 script funnels date access through :meth:`LockedSplit.assert_not_test`
so the held-out test window (2025-06-04 .. 2026-06-12) is physically
unreachable until the single Phase 4 evaluation. The covenant (see the lock
file) is that touching test during development voids the lock.

Pure stdlib; no ``backend`` import (reads the snapshot index file directly).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_LOCK_PATH = "config/research/test_set_lock.json"
DEFAULT_SNAPSHOT_ROOT = "data/marketdata_pit"


class LockVerificationError(RuntimeError):
    """The lock's committed hashes do not match the re-derived windows."""


class SacredTestAccessError(RuntimeError):
    """A development-time attempt to read a sacred test-window date."""


def _sha(dates: list[str]) -> str:
    """Hash a date list exactly as the lock generator did."""
    return hashlib.sha256("|".join(dates).encode("utf-8")).hexdigest()


def load_daily_calendar(snapshot_root: str = DEFAULT_SNAPSHOT_ROOT) -> tuple[str, ...]:
    """Authoritative trading calendar = sorted ``daily`` snapshot trade dates."""
    index_path = Path(snapshot_root) / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"snapshot index not found: {index_path}")
    days: set[str] = set()
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("endpoint") == "daily":
                days.add(str(rec["trade_date"]))
    return tuple(sorted(days))


@dataclass(frozen=True)
class LockedSplit:
    """The verified, immutable train/validation/test windows (YYYYMMDD)."""

    train_val_dates: tuple[str, ...]
    embargo_dates: tuple[str, ...]
    test_dates: tuple[str, ...]

    @property
    def test_date_set(self) -> frozenset[str]:
        return frozenset(self.test_dates)

    def is_test(self, date: str) -> bool:
        return date in self.test_date_set

    def assert_not_test(self, date: str) -> None:
        """Fail closed if ``date`` is in the sacred test window."""
        if date in self.test_date_set:
            raise SacredTestAccessError(
                f"date {date} is in the SACRED locked test window "
                f"({self.test_dates[0]}..{self.test_dates[-1]}) — development "
                "code must never read it (test-set covenant)."
            )

    def assert_all_not_test(self, dates: list[str]) -> None:
        for d in dates:
            self.assert_not_test(d)

    @classmethod
    def from_lock(cls, lock: dict[str, Any], calendar: tuple[str, ...]) -> LockedSplit:
        """Re-derive + verify the windows from ``lock`` and ``calendar``.

        Slices the calendar by the lock's window boundaries and checks each
        window's ``dates_sha256``. A mismatch (data drift / tampering) raises
        :class:`LockVerificationError` (fail-closed — never proceed on an
        unverified split).
        """
        cal = list(calendar)
        windows: dict[str, list[str]] = {}
        for name in ("train_val", "embargo", "test"):
            spec = lock[name]
            start, end = spec["start"], spec["end"]
            try:
                lo = cal.index(start)
                hi = cal.index(end)
            except ValueError as exc:
                raise LockVerificationError(
                    f"{name} boundary {start}/{end} not in calendar"
                ) from exc
            dates = cal[lo : hi + 1]
            if len(dates) != spec["n_days"]:
                raise LockVerificationError(
                    f"{name} window has {len(dates)} days, lock says {spec['n_days']}"
                )
            actual = _sha(dates)
            if actual != spec["dates_sha256"]:
                raise LockVerificationError(
                    f"{name} dates_sha256 mismatch — calendar drifted from the "
                    f"lock (expected {spec['dates_sha256'][:16]}, got {actual[:16]})"
                )
            windows[name] = dates
        # Windows must be contiguous + non-overlapping in calendar order.
        ordered = windows["train_val"] + windows["embargo"] + windows["test"]
        first = cal.index(ordered[0])
        if cal[first : first + len(ordered)] != ordered:
            raise LockVerificationError("windows are not contiguous in the calendar")
        return cls(
            train_val_dates=tuple(windows["train_val"]),
            embargo_dates=tuple(windows["embargo"]),
            test_dates=tuple(windows["test"]),
        )

    @classmethod
    def load(
        cls,
        lock_path: str = DEFAULT_LOCK_PATH,
        snapshot_root: str = DEFAULT_SNAPSHOT_ROOT,
    ) -> LockedSplit:
        """Load the lock file + live calendar and return the verified split."""
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        calendar = load_daily_calendar(snapshot_root)
        return cls.from_lock(lock, calendar)


__all__ = [
    "DEFAULT_LOCK_PATH",
    "DEFAULT_SNAPSHOT_ROOT",
    "LockVerificationError",
    "LockedSplit",
    "SacredTestAccessError",
    "load_daily_calendar",
]
