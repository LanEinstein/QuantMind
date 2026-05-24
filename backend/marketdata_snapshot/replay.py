"""Offline bit-exact replay of a signal's feature input (K-005).

Red line A.5 (R0 §3): ``replay(signal_id)`` rebuilds — with **no
network** — the exact rows the signal consumed, from the stored raw
bytes (verify-before-adopt) and the pinned consumed-row lineage. The
backtest / 45-day shadow / live signal-explanation paths all read by
``snapshot_id`` through this one entry, so they share a single source of
truth and a P0-6 acceptance gate validates signal rather than noise.

The replay is byte-agnostic: a :class:`RowParser` turns a snapshot's raw
bytes into ``{row_key: canonical_row_bytes}``. The parser used here must
match the one used when the signal's :class:`SignalInputManifest` was
built (its row hashes were computed over the same canonical row bytes) —
that pairing is part of the pinned ``feature_code_version`` contract.
A drift / mismatch fails closed via the manifest's reconstruction.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field

from backend.marketdata_snapshot.signal_input_manifest import (
    ResolvedRow,
    SignalInputManifestStore,
)
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore

log = structlog.get_logger(component="marketdata_snapshot.replay")


class ReplayError(RuntimeError):
    """Raised when a signal cannot be replayed (unknown id / missing
    snapshot / drift)."""


@runtime_checkable
class RowParser(Protocol):
    """Turns a snapshot's raw bytes into ``{row_key: canonical_row_bytes}``."""

    def parse(self, snapshot: MarketDataSnapshot) -> dict[str, bytes]: ...


class CsvRowParser:
    """Default parser for CSV payloads (e.g. ``DataFrame.to_csv``).

    The first line is treated as a header and skipped; each data line is
    keyed by its first comma-separated field (``ts_code``) and its
    canonical bytes are the line with any trailing ``\\r`` stripped (no
    trailing newline). Hashing the same line bytes on both the build and
    replay side keeps consumed-row hashes comparable.
    """

    def parse(self, snapshot: MarketDataSnapshot) -> dict[str, bytes]:
        lines = snapshot.raw_payload.split(b"\n")
        rows: dict[str, bytes] = {}
        for line in lines[1:]:  # skip header
            line = line.rstrip(b"\r")
            if not line:
                continue
            key = line.split(b",", 1)[0].decode("ascii")
            rows[key] = line
        return rows


class ReplayResult(BaseModel):
    """The reconstructed feature input for a signal."""

    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", arbitrary_types_allowed=True
    )

    signal_id: str
    snapshot_ids: tuple[UUID, ...]
    consumed: tuple[ResolvedRow, ...]
    feature_code_version: str
    config_hashes: dict[str, str]
    feature_input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    """SHA256 over the canonical (snapshot_id | row_key | bytes) stream in
    manifest order — identical across replays iff bit-exact."""


class Replayer:
    """Single offline entry for reading data by ``snapshot_id``.

    Args:
        snapshot_store: source of raw bytes (verify-before-adopt).
        manifest_store: source of consumed-row lineage by signal_id.
        row_parser: how to split a snapshot into rows (default CSV).
    """

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        manifest_store: SignalInputManifestStore,
        row_parser: RowParser | None = None,
    ) -> None:
        self._snapshots = snapshot_store
        self._manifests = manifest_store
        self._parser: RowParser = row_parser or CsvRowParser()

    def replay(self, signal_id: str) -> ReplayResult:
        """Rebuild the signal's consumed feature input, offline + bit-exact.

        Raises:
            ReplayError: unknown signal_id.
            SnapshotStoreError / ChecksumMismatchError: a snapshot is
                missing or its bytes are corrupt (fail-closed).
            SignalInputError: a consumed row drifted / is absent.
        """
        manifest = self._manifests.get(signal_id)
        if manifest is None:
            raise ReplayError(f"no SignalInputManifest for signal {signal_id!r}")

        available: dict[UUID, dict[str, bytes]] = {}
        for sid in manifest.snapshot_ids:
            snapshot = self._snapshots.get(sid)  # verify-before-adopt
            available[sid] = self._parser.parse(snapshot)

        resolved = manifest.reconstruct_consumed(available)
        digest = self._feature_input_digest(resolved)
        log.info(
            "signal_replayed",
            signal_id=signal_id,
            snapshots=len(manifest.snapshot_ids),
            consumed_rows=len(resolved),
            feature_input_digest=digest[:12],
        )
        return ReplayResult(
            signal_id=signal_id,
            snapshot_ids=manifest.snapshot_ids,
            consumed=resolved,
            feature_code_version=manifest.feature_code_version,
            config_hashes=manifest.config_hashes,
            feature_input_digest=digest,
        )

    @staticmethod
    def _feature_input_digest(resolved: tuple[ResolvedRow, ...]) -> str:
        """Deterministic digest over the consumed rows in manifest order."""
        h = hashlib.sha256()
        for r in resolved:
            h.update(str(r.snapshot_id).encode("ascii"))
            h.update(b"|")
            h.update(r.row_key.encode("utf-8"))
            h.update(b"|")
            h.update(r.row_bytes)
            h.update(b"\n")
        return h.hexdigest()


def replay_signal(
    signal_id: str,
    *,
    root: str,
    row_parser: RowParser | None = None,
) -> ReplayResult:
    """Convenience wrapper: build stores rooted at ``root`` and replay."""
    return Replayer(
        SnapshotStore(root),
        SignalInputManifestStore(root),
        row_parser,
    ).replay(signal_id)


__all__ = [
    "CsvRowParser",
    "ReplayError",
    "ReplayResult",
    "Replayer",
    "RowParser",
    "replay_signal",
]
