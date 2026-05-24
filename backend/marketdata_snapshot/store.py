"""SnapshotStore — append-only, content-addressed filesystem store (K-002).

A filesystem store (not Mongo) is deliberate: module 0 must be
independently testable and drive **offline** bit-exact replay (K-005)
with no network and no database. Raw payloads are content-addressed by
their SHA256 so identical bytes dedup to one file; metadata rows live in
an append-only ``index.jsonl``.

The 8 P1-2.A append-only red lines map onto the filesystem layer:

1. **insert-only** — metadata rows are *appended* to ``index.jsonl``;
   the store never rewrites an existing line.
2. **no in-place mutation** — a snapshot is immutable once written; a
   correction is a NEW snapshot (new id, bigger ``version``).
3. **no delete** — the store API exposes no delete / unlink of payloads
   or index rows.
4. **no truncate** — no method clears the index or payload tree.
5. **no schema rewrite** — rows carrying a different ``schema_version``
   fail Pydantic validation on read.
6. (sharding N/A on a filesystem.)
7. **no checksum patch** — the only legitimate fix is a new snapshot
   with correct bytes; the checksum is recomputed and verified on read.
8. **same-id reject** — ``put`` of an already-stored ``snapshot_id``
   raises :class:`SnapshotOverwriteError`.

Reads are **verify-before-adopt**: the on-disk bytes are re-hashed and
compared to the stored ``raw_payload_sha256``; a mismatch raises
:class:`ChecksumMismatchError` (fail-closed) so a corrupted payload can
never drive a signal.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from filelock import FileLock

from backend.marketdata_snapshot.snapshot import MarketDataSnapshot

log = structlog.get_logger(component="marketdata_snapshot.store")


class SnapshotStoreError(RuntimeError):
    """Base error for the marketdata snapshot store."""


class SnapshotOverwriteError(SnapshotStoreError):
    """Raised when ``put`` would overwrite an existing snapshot_id
    (append-only red line)."""


class ChecksumMismatchError(SnapshotStoreError):
    """Raised when on-disk payload bytes do not match the stored
    ``raw_payload_sha256`` (verify-before-adopt fail-closed)."""


_INDEX_NAME = "index.jsonl"
_PAYLOAD_DIR = "payloads"
_LOCK_NAME = "index.lock"

# Fields persisted in the index row (everything except the raw bytes,
# which live content-addressed under payloads/).
_METADATA_FIELDS = (
    "snapshot_id",
    "schema_version",
    "vendor",
    "endpoint",
    "params",
    "trade_date",
    "fetch_time_utc",
    "size",
    "encoding",
    "compression",
    "raw_payload_sha256",
    "version",
    "metadata",
)


class SnapshotStore:
    """Append-only content-addressed store for :class:`MarketDataSnapshot`.

    Args:
        root: Directory under which ``index.jsonl`` and ``payloads/`` live.
            Created if missing.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._payload_dir = self._root / _PAYLOAD_DIR
        self._index_path = self._root / _INDEX_NAME
        self._lock = FileLock(str(self._root / _LOCK_NAME))
        self._root.mkdir(parents=True, exist_ok=True)
        self._payload_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, dict[str, Any]] = {}
        self._load_index()

    # -- index load ----------------------------------------------------

    def _load_index(self) -> None:
        """Read existing index rows into memory (offline, no network)."""
        if not self._index_path.exists():
            return
        for lineno, line in enumerate(
            self._index_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SnapshotStoreError(
                    f"corrupt index row at {self._index_path}:{lineno}: {exc}"
                ) from exc
            self._index[row["snapshot_id"]] = row

    # -- paths ---------------------------------------------------------

    def _payload_path(self, sha256: str) -> Path:
        return self._payload_dir / sha256[:2] / f"{sha256}.bin"

    # -- write ---------------------------------------------------------

    def put(self, snapshot: MarketDataSnapshot) -> MarketDataSnapshot:
        """Append a snapshot. Idempotent payload write, unique-id index.

        Raises:
            SnapshotOverwriteError: ``snapshot_id`` already stored.
        """
        sid = str(snapshot.snapshot_id)
        with self._lock:
            # Re-read under the lock so a concurrent writer's row is seen.
            self._load_index()
            if sid in self._index:
                raise SnapshotOverwriteError(
                    f"snapshot_id {sid} already stored — append-only "
                    "(a restatement must use a new id + bigger version)"
                )
            self._write_payload(snapshot)
            row = self._to_index_row(snapshot)
            with self._index_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        row, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )
            self._index[sid] = row
        log.info(
            "marketdata_snapshot_put",
            snapshot_id=sid,
            vendor=snapshot.vendor,
            endpoint=snapshot.endpoint,
            trade_date=snapshot.trade_date,
            version=snapshot.version,
            sha256=snapshot.raw_payload_sha256[:12],
        )
        return snapshot

    def _write_payload(self, snapshot: MarketDataSnapshot) -> None:
        """Write content-addressed bytes; dedup identical content.

        If a file at the address already exists, its bytes are by
        construction identical (same sha256) — we verify defensively and
        leave it untouched (idempotent), so an old version's bytes are
        never clobbered by a restatement that happens to collide.
        """
        path = self._payload_path(snapshot.raw_payload_sha256)
        if path.exists():
            existing = path.read_bytes()
            if existing != snapshot.raw_payload:
                raise SnapshotStoreError(
                    f"sha256 collision / corruption at {path}: existing bytes "
                    "differ from new payload with the same address"
                )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".bin.tmp")
        tmp.write_bytes(snapshot.raw_payload)
        tmp.replace(path)  # atomic publish

    @staticmethod
    def _to_index_row(snapshot: MarketDataSnapshot) -> dict[str, Any]:
        dumped = snapshot.model_dump(mode="json")
        return {k: dumped[k] for k in _METADATA_FIELDS}

    # -- read ----------------------------------------------------------

    def get(self, snapshot_id: UUID) -> MarketDataSnapshot:
        """Load + verify a snapshot by id (verify-before-adopt).

        Raises:
            SnapshotStoreError: unknown id / missing payload file.
            ChecksumMismatchError: on-disk bytes fail the checksum.
        """
        sid = str(snapshot_id)
        row = self._index.get(sid)
        if row is None:
            raise SnapshotStoreError(f"unknown snapshot_id {sid}")
        expected_sha = row["raw_payload_sha256"]
        path = self._payload_path(expected_sha)
        if not path.exists():
            raise SnapshotStoreError(
                f"payload file missing for snapshot {sid} at {path}"
            )
        raw = path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != expected_sha:
            raise ChecksumMismatchError(
                f"snapshot {sid}: on-disk sha256 {actual_sha} != stored "
                f"{expected_sha} — refusing to adopt corrupted payload"
            )
        # Reconstruct via the strict model (re-validates size + sha).
        # JSON-stored provenance types are converted back to native ones
        # because strict mode does not coerce str -> UUID / datetime.
        return MarketDataSnapshot(
            snapshot_id=UUID(row["snapshot_id"]),
            schema_version=row["schema_version"],
            vendor=row["vendor"],
            endpoint=row["endpoint"],
            params=row["params"],
            trade_date=row["trade_date"],
            fetch_time_utc=datetime.fromisoformat(row["fetch_time_utc"]),
            raw_payload=raw,
            size=row["size"],
            encoding=row["encoding"],
            compression=row["compression"],
            raw_payload_sha256=row["raw_payload_sha256"],
            version=row["version"],
            metadata=row["metadata"],
        )

    def versions(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> tuple[MarketDataSnapshot, ...]:
        """All stored versions for a (vendor, endpoint, trade_date),
        ordered by ``version`` ascending."""
        matches = [
            row
            for row in self._index.values()
            if row["vendor"] == vendor
            and row["endpoint"] == endpoint
            and row["trade_date"] == trade_date
        ]
        matches.sort(key=lambda r: r["version"])
        return tuple(self.get(UUID(r["snapshot_id"])) for r in matches)

    def latest(
        self, *, vendor: str, endpoint: str, trade_date: str
    ) -> MarketDataSnapshot | None:
        """Highest-version snapshot for the key, or ``None`` if absent."""
        versions = self.versions(
            vendor=vendor, endpoint=endpoint, trade_date=trade_date
        )
        return versions[-1] if versions else None


__all__ = [
    "ChecksumMismatchError",
    "SnapshotOverwriteError",
    "SnapshotStore",
    "SnapshotStoreError",
]
