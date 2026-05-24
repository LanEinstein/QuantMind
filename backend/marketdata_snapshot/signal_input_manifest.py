"""SignalInputManifest — consumed-row lineage for exact replay (K-003).

Red line A.3 (R0 §3): per ``signal_id`` record the snapshots read, the
**exact rows consumed** (by stable key + content hash), the feature-code
version, the config hashes, and the join/filter params. This lets
``replay`` (K-005) rebuild the precise row set the signal consumed — not
the wider snapshot superset — and detect drift (a row whose bytes
changed under the same key, e.g. a vendor restatement).

The manifest is byte-agnostic: the caller parses a snapshot's raw bytes
into ``{row_key: row_bytes}`` and hashes each consumed row through
:func:`row_sha256`. Reconstruction re-hashes the available rows and
fails closed on any mismatch / absence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import structlog
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from backend.marketdata_snapshot._jsonl import append_row, load_rows

log = structlog.get_logger(component="marketdata_snapshot.signal_input_manifest")

SIGNAL_INPUT_MANIFEST_SCHEMA_VERSION = 1


class SignalInputError(RuntimeError):
    """Raised when consumed-row lineage cannot be reconstructed
    (drift / absence) or a manifest invariant fails."""


def row_sha256(row_bytes: bytes) -> str:
    """Canonical per-row SHA256 hex. Both manifest-build and replay use
    this so hashes are comparable."""
    return hashlib.sha256(row_bytes).hexdigest()


class ConsumedRow(BaseModel):
    """One row a signal consumed: which snapshot, the stable row key, and
    the content hash at consumption time."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: UUID
    row_key: str = Field(min_length=1)
    """Stable identifier within the snapshot, e.g. ``ts_code``."""
    row_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedRow(NamedTuple):
    """A consumed row resolved back to its verified bytes during replay."""

    snapshot_id: UUID
    row_key: str
    row_bytes: bytes


def build_consumed_row(
    snapshot_id: UUID, row_key: str, row_bytes: bytes
) -> ConsumedRow:
    """Build a :class:`ConsumedRow`, hashing the canonical row bytes."""
    return ConsumedRow(
        snapshot_id=snapshot_id,
        row_key=row_key,
        row_sha256=row_sha256(row_bytes),
    )


class SignalInputManifest(BaseModel):
    """Consumed-row lineage for one signal."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(
        default=SIGNAL_INPUT_MANIFEST_SCHEMA_VERSION, ge=1
    )
    signal_id: str = Field(min_length=1)
    created_at: datetime
    snapshot_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    consumed_rows: tuple[ConsumedRow, ...] = Field(default_factory=tuple)
    feature_code_version: str = Field(min_length=1)
    """Pinned feature-engineering code version, e.g. ``alpha158-subset@v1``."""
    config_hashes: dict[str, str] = Field(default_factory=dict)
    join_filter_params: dict[str, Any] = Field(default_factory=dict)

    def reconstruct_consumed(
        self, available_by_snapshot: Mapping[UUID, Mapping[str, bytes]]
    ) -> tuple[ResolvedRow, ...]:
        """Resolve the consumed rows back to verified bytes.

        Args:
            available_by_snapshot: ``{snapshot_id: {row_key: row_bytes}}``
                parsed from the stored snapshots.

        Returns:
            The consumed rows (in manifest order) with their verified
            bytes — exactly the subset the signal consumed.

        Raises:
            SignalInputError: a snapshot/row is absent, or a row's bytes
                no longer hash to the recorded value (drift).
        """
        resolved: list[ResolvedRow] = []
        for cr in self.consumed_rows:
            rows = available_by_snapshot.get(cr.snapshot_id)
            if rows is None:
                raise SignalInputError(
                    f"snapshot {cr.snapshot_id} not available for signal "
                    f"{self.signal_id}"
                )
            if cr.row_key not in rows:
                raise SignalInputError(
                    f"consumed row {cr.row_key!r} absent from snapshot "
                    f"{cr.snapshot_id} (signal {self.signal_id})"
                )
            row_bytes = rows[cr.row_key]
            digest = row_sha256(row_bytes)
            if digest != cr.row_sha256:
                raise SignalInputError(
                    f"row {cr.row_key!r} drifted: {digest} != recorded "
                    f"{cr.row_sha256} (signal {self.signal_id})"
                )
            resolved.append(ResolvedRow(cr.snapshot_id, cr.row_key, row_bytes))
        return tuple(resolved)


class SignalInputManifestStore:
    """Append-only JSONL store keyed by ``signal_id`` (unique)."""

    _FILE = "signal_input.jsonl"
    _LOCK = "signal_input.lock"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._path = self._root / self._FILE
        self._lock = FileLock(str(self._root / self._LOCK))
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, manifest: SignalInputManifest) -> SignalInputManifest:
        with self._lock:
            if self._find_row(manifest.signal_id) is not None:
                raise SignalInputError(
                    f"signal_id {manifest.signal_id!r} already stored "
                    "(append-only — lineage is immutable)"
                )
            append_row(
                self._path, manifest.model_dump(mode="json"), self._lock
            )
        log.info(
            "signal_input_manifest_put",
            signal_id=manifest.signal_id,
            snapshots=len(manifest.snapshot_ids),
            consumed_rows=len(manifest.consumed_rows),
            feature_code_version=manifest.feature_code_version,
        )
        return manifest

    def get(self, signal_id: str) -> SignalInputManifest | None:
        row = self._find_row(signal_id)
        return self._from_row(row) if row is not None else None

    def _find_row(self, signal_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = None
        for row in load_rows(self._path):
            if row["signal_id"] == signal_id:
                found = row
        return found

    @staticmethod
    def _from_row(row: dict[str, Any]) -> SignalInputManifest:
        # Rebuild with native UUID / datetime / tuple types — strict mode
        # does not coerce the JSON-stored str/list forms.
        consumed = tuple(
            ConsumedRow(
                snapshot_id=UUID(c["snapshot_id"]),
                row_key=c["row_key"],
                row_sha256=c["row_sha256"],
            )
            for c in row["consumed_rows"]
        )
        return SignalInputManifest(
            schema_version=row["schema_version"],
            signal_id=row["signal_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            snapshot_ids=tuple(UUID(s) for s in row["snapshot_ids"]),
            consumed_rows=consumed,
            feature_code_version=row["feature_code_version"],
            config_hashes=row["config_hashes"],
            join_filter_params=row["join_filter_params"],
        )


__all__ = [
    "SIGNAL_INPUT_MANIFEST_SCHEMA_VERSION",
    "ConsumedRow",
    "ResolvedRow",
    "SignalInputError",
    "SignalInputManifest",
    "SignalInputManifestStore",
    "build_consumed_row",
    "row_sha256",
]
