"""Durable per-day fired-trigger dedup store (intraday ops hardening).

P0-10-amendment-line2-2026-06-04-intraday-ops-hardening §1.1: the Line-2
intraday runner's per-day ``(code, trigger_kind)`` dedup lived only in
memory, so every process restart reset it and a still-breached trigger
re-routed — on 2026-06-04 two restarts re-sent the same 3-SELL batch to
Feishu twice within 18 minutes. This store persists each fired key as an
append-only JSONL row so a restarted runner reloads today's keys and the
dedup survives the process.

WHY fail-open (the opposite of ``takeprofit_ledger``): this dedup is a
UX-layer guard (don't spam the decision chat with repeats), not a
safety-layer one — every order still passes the RiskEngine 14-check and
the Feishu human gate. A corrupt/unreadable store therefore degrades to
an EMPTY set (worst case: one duplicate message) rather than failing the
tick closed (worst case: protective stops stop firing). The ledger next
door guards "would we double-take a profit tier" — a safety question —
and so fails closed.

Red lines: records routing state only — never constructs an
InstructionPlan (R0 §4); zero ``backend.*`` sub-package imports (pure
module, mirrors ``takeprofit_ledger``).
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from filelock import FileLock

log = structlog.get_logger(component="fired_trigger_store")


class FiredTriggerStore:
    """Append-only JSONL of per-day fired ``(code, trigger_kind)`` keys."""

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        self._lock = FileLock(str(lock_path or f"{self._path}.lock"))

    @property
    def path(self) -> Path:
        return self._path

    def load_fired(self, trade_date: str) -> frozenset[tuple[str, str]]:
        """Return the ``(code, kind)`` keys recorded for ``trade_date``.

        FAIL-OPEN: any read/parse problem returns the keys parsed so far
        (possibly empty) with a loud error log — a broken store must never
        block the monitoring tick (see module docstring).
        """
        keys: set[tuple[str, str]] = set()
        try:
            if not self._path.exists():
                return frozenset()
            for lineno, line in enumerate(
                self._path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    code = str(row["code"]).strip()
                    kind = str(row["kind"]).strip()
                    # A row with an empty code/kind is corrupt — skipping it
                    # beats letting "" suppress every ADD via the same-day
                    # mutex (review angle B).
                    if str(row["trade_date"]) == trade_date and code and kind:
                        keys.add((code, kind))
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    log.error(
                        "fired_trigger_store_corrupt_row",
                        path=str(self._path),
                        lineno=lineno,
                        error=str(exc),
                    )
        except OSError as exc:
            log.error(
                "fired_trigger_store_read_failed",
                path=str(self._path),
                error=str(exc),
            )
        return frozenset(keys)

    def record_fired(
        self,
        trade_date: str,
        code: str,
        kind: str,
        *,
        signal_id: str,
        sold_price: float | None = None,
        sold_volume: int | None = None,
    ) -> None:
        """Append one fired key. FAIL-OPEN: an I/O error logs and returns.

        ``sold_price``/``sold_volume`` (E5 re-entry,
        P0-10-amendment-line2-2026-06-04-reentry-and-time-stop §1.2): a
        DELIVERED SELL may carry its limit price + volume so the next-day
        re-entry gate can compare the open against yesterday's sale. Both
        optional — rows written by older code simply lack them, and a code
        without a recorded sale price is NOT re-entry-eligible (fail-closed
        toward not trading).
        """
        row: dict[str, object] = {
            "trade_date": trade_date,
            "code": code,
            "kind": kind,
            "signal_id": signal_id,
        }
        if sold_price is not None:
            row["sold_price"] = float(sold_price)
        if sold_volume is not None:
            row["sold_volume"] = int(sold_volume)
        try:
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
        except OSError as exc:
            log.error(
                "fired_trigger_store_write_failed",
                path=str(self._path),
                code=code,
                kind=kind,
                error=str(exc),
            )

    def delivered_sales(self, trade_date: str) -> dict[str, dict[str, float]]:
        """code → {kind, sold_price, sold_volume} for ``trade_date`` rows that
        carry a sale price (E5 re-entry eligibility input).

        Only rows with BOTH a finite positive ``sold_price`` and a positive
        ``sold_volume`` qualify; the LAST qualifying row per code wins (a
        same-day multi-tranche exit re-enters against the latest sale).
        FAIL-OPEN like :meth:`load_fired` — a broken store yields no
        eligibility (toward not trading), never an exception.
        """
        out: dict[str, dict[str, float]] = {}
        try:
            if not self._path.exists():
                return {}
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if str(row.get("trade_date")) != trade_date:
                        continue
                    code = str(row.get("code", "")).strip()
                    kind = str(row.get("kind", "")).strip()
                    price = row.get("sold_price")
                    volume = row.get("sold_volume")
                    if (
                        code
                        and kind
                        and isinstance(price, (int, float))
                        and price > 0
                        and isinstance(volume, (int, float))
                        and volume > 0
                    ):
                        out[code] = {
                            "kind": kind,
                            "sold_price": float(price),
                            "sold_volume": float(volume),
                        }
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue  # fail-open: unreadable rows grant no eligibility
        except OSError as exc:
            log.error(
                "fired_trigger_store_read_failed",
                path=str(self._path),
                error=str(exc),
            )
        return out

    def prune_before(self, trade_date: str) -> None:
        """Drop rows with ``trade_date`` older than the given ISO date.

        Retention for the append-only file: dedup keys matter for ONE day,
        so anything older than a small window is dead weight that the
        once-per-day full-file scan would keep re-parsing forever (review
        angle A). ISO dates compare lexicographically. FAIL-OPEN: any
        problem leaves the file as-is (worst case: a bigger file).
        """
        try:
            with self._lock:
                if not self._path.exists():
                    return
                kept: list[str] = []
                dropped = 0
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        row = json.loads(stripped)
                        if str(row["trade_date"]) < trade_date:
                            dropped += 1
                            continue
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass  # keep unparseable rows — pruning never destroys
                    kept.append(stripped)
                if dropped:
                    self._path.write_text(
                        "".join(f"{line}\n" for line in kept),
                        encoding="utf-8",
                    )
                    log.info(
                        "fired_trigger_store_pruned",
                        dropped=dropped,
                        kept=len(kept),
                    )
        except OSError as exc:
            log.error(
                "fired_trigger_store_prune_failed",
                path=str(self._path),
                error=str(exc),
            )


__all__ = ["FiredTriggerStore"]
