"""Snapshot checksum — deterministic SHA256[:16] over canonical state.

The checksum guards against corruption-in-transit (Mongo storage
failure, partial write, manual db edit). The recovery loader recomputes
this exact value and refuses to adopt a snapshot whose stored checksum
does not match — see :class:`backend.broker.persistence.recovery
.ChecksumMismatchError`.

Canonical state payload includes ONLY:

* ``cash`` (rounded to 4 decimal places)
* ``frozen_cash`` (rounded to 4 decimal places)
* ``initial_capital`` (rounded to 2 decimal places)
* ``positions`` — sorted by ``code`` ascending, each row's
  ``volume`` / ``today_bought_volume`` / ``cost_price``
  (cost_price rounded to 4 decimal places).

Fields deliberately **excluded**: snapshot_id, created_at, trade_date,
metadata, last_event_sequence. Those are provenance / ordering fields;
including them would make the checksum useless as a corruption
detector because a re-run on the same state would change them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from backend.broker.persistence.snapshots import BrokerSnapshotPosition


def canonical_state_payload(
    cash: float,
    frozen_cash: float,
    initial_capital: float,
    positions: Iterable[BrokerSnapshotPosition],
) -> dict[str, Any]:
    """Build the canonical dict whose JSON encoding feeds the checksum.

    Floats are rounded to 4 decimal places so trivial float-representation
    differences do not break the checksum. Positions are sorted by
    ``code`` so iteration order from the broker doesn't matter.
    """
    sorted_positions = sorted(positions, key=lambda p: p.code)

    def _row(pos: BrokerSnapshotPosition) -> dict[str, Any]:
        row: dict[str, Any] = {
            "code": pos.code,
            "volume": pos.volume,
            "today_bought_volume": pos.today_bought_volume,
            "cost_price": round(pos.cost_price, 4),
        }
        # v2 (P0-4-amendment-2026-06-04): fold the per-date buy record in
        # ONLY when present — an empty map keeps the payload byte-identical
        # to v1, so checksums stored by v1 writers still validate on read.
        bought = getattr(pos, "bought_by_date", None)
        if bought:
            row["bought_by_date"] = {k: bought[k] for k in sorted(bought)}
        return row

    return {
        "cash": round(cash, 4),
        "frozen_cash": round(frozen_cash, 4),
        "initial_capital": round(initial_capital, 2),
        "positions": [_row(pos) for pos in sorted_positions],
    }


def compute_snapshot_checksum(
    cash: float,
    frozen_cash: float,
    initial_capital: float,
    positions: Iterable[BrokerSnapshotPosition],
) -> str:
    """Return the deterministic SHA256[:16] hex digest of the canonical
    state payload.

    ``json.dumps`` is called with ``sort_keys=True`` and a fixed
    separator pair so the output bytes are stable across Python
    versions and dict-insertion orders.
    """
    payload = canonical_state_payload(
        cash, frozen_cash, initial_capital, positions
    )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


__all__ = [
    "canonical_state_payload",
    "compute_snapshot_checksum",
]
