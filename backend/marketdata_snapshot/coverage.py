"""CoverageManifest — requested vs delivered universe (K-003).

Red line A.2 (R0 §3): a ``row_count`` alone lets a partial universe
pull silently masquerade as the full market. The manifest records the
**requested** vs **delivered** universe so ``completeness`` < 1.0 and a
non-empty ``missing_symbols`` set flag the gap. ``completeness`` is a
derived property (not a stored field) so it can never drift from the
universes it is computed from.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from backend.marketdata_snapshot._jsonl import append_row, load_rows

log = structlog.get_logger(component="marketdata_snapshot.coverage")

COVERAGE_MANIFEST_SCHEMA_VERSION = 1


class CoverageManifest(BaseModel):
    """Coverage of a single full-market fetch.

    ``requested_universe`` is the set we expected to receive (e.g. every
    listed code for the board whitelist); ``delivered_universe`` is what
    the vendor actually returned. ``missing_symbols`` /
    ``completeness`` / ``is_complete`` are derived from those two.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=COVERAGE_MANIFEST_SCHEMA_VERSION, ge=1)
    granularity: str = Field(min_length=1)
    """e.g. ``daily`` / ``period``."""
    endpoint: str = Field(min_length=1)
    params: dict[str, str] = Field(default_factory=dict)
    session_start: str = Field(pattern=r"^\d{8}$")
    session_end: str = Field(pattern=r"^\d{8}$")
    requested_universe: tuple[str, ...] = Field(default_factory=tuple)
    delivered_universe: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def _requested_set(self) -> frozenset[str]:
        return frozenset(self.requested_universe)

    @property
    def _delivered_set(self) -> frozenset[str]:
        return frozenset(self.delivered_universe)

    @property
    def missing_symbols(self) -> tuple[str, ...]:
        """Requested symbols absent from the delivery, request-order."""
        delivered = self._delivered_set
        return tuple(s for s in self.requested_universe if s not in delivered)

    @property
    def completeness(self) -> float:
        """``|requested ∩ delivered| / |requested|``; 1.0 if none requested."""
        requested = self._requested_set
        if not requested:
            return 1.0
        return len(requested & self._delivered_set) / len(requested)

    @property
    def is_complete(self) -> bool:
        return self.completeness == 1.0 and not self.missing_symbols


class CoverageStore:
    """Append-only JSONL store for coverage manifests, keyed by
    (endpoint, session_end)."""

    _FILE = "coverage.jsonl"
    _LOCK = "coverage.lock"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._path = self._root / self._FILE
        self._lock = FileLock(str(self._root / self._LOCK))
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, manifest: CoverageManifest) -> CoverageManifest:
        append_row(self._path, manifest.model_dump(mode="json"), self._lock)
        log.info(
            "coverage_manifest_put",
            endpoint=manifest.endpoint,
            session_end=manifest.session_end,
            completeness=manifest.completeness,
            missing=len(manifest.missing_symbols),
        )
        return manifest

    def iter_keys(self) -> Iterator[tuple[str, str]]:
        """Yield ``(endpoint, session_end)`` of every stored manifest, one pass.

        Lighter than reconstructing each :class:`CoverageManifest` (which carries
        the full requested/delivered universes) — a bulk idempotent writer pulls
        the present keys once and skips them in O(1), avoiding an O(n²) per-row
        rescan of a large coverage file. Later rows for the same key are yielded
        too (the caller de-dupes via a set); the file is append-only.
        """
        for row in load_rows(self._path):
            yield (row["endpoint"], row["session_end"])

    @staticmethod
    def _from_row(row: dict[str, Any]) -> CoverageManifest:
        """Rebuild tuple fields that strict Pydantic will not coerce from JSON."""
        return CoverageManifest(
            schema_version=row["schema_version"],
            granularity=row["granularity"],
            endpoint=row["endpoint"],
            params=row["params"],
            session_start=row["session_start"],
            session_end=row["session_end"],
            requested_universe=tuple(row["requested_universe"]),
            delivered_universe=tuple(row["delivered_universe"]),
        )

    def get_many(
        self, keys: Collection[tuple[str, str]]
    ) -> dict[tuple[str, str], CoverageManifest]:
        """Return the latest manifests for requested keys after one file scan.

        The production coverage log is large enough that calling :meth:`get`
        once per report period makes an incremental ingest repeatedly parse the
        same file. Retain only requested keys while preserving latest-row wins.
        """
        wanted = set(keys)
        latest: dict[tuple[str, str], CoverageManifest] = {}
        for row in load_rows(self._path):
            key = (row["endpoint"], row["session_end"])
            if key in wanted:
                latest[key] = self._from_row(row)
        return latest

    def get(self, *, endpoint: str, session_end: str) -> CoverageManifest | None:
        """Latest manifest for (endpoint, session_end), or None."""
        latest: CoverageManifest | None = None
        for row in load_rows(self._path):
            if row["endpoint"] == endpoint and row["session_end"] == session_end:
                latest = self._from_row(row)
        return latest


__all__ = [
    "COVERAGE_MANIFEST_SCHEMA_VERSION",
    "CoverageManifest",
    "CoverageStore",
]
