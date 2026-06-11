"""Theme-research provenance capture — raw bytes + checksum (Y-002).

Red line R0 §3, extended by P0-8-amendment-2026-06-01 §2.2: an LLM+web
investigation is only red-line-safe if it is **fully captured**. Every byte the
investigation saw or produced — SERP results, fetched page text, the rendered
prompt, the model response, the tool transcript — is persisted append-only and
content-addressed (``sha256``), exactly like :class:`MarketDataSnapshot`. The
consequence:

* ``replay`` never goes online and never calls an LLM — it re-reads the pinned
  bytes and re-checks the hashes (offline bit-exact, same as marketdata).
* A run that hid any required byte category is **non-promotable** — it cannot be
  pinned, so it can never influence live selection (§2.2 "隐藏字节的 run = ...").
* Citations in the SOP output (:mod:`sop_schema`) reference snippet hashes that
  must resolve to captured snapshots here, or the run fails the promotable check.

This module is byte-agnostic and pure (frozen Pydantic strict + a small
content-addressed append-only store). It runs NO business logic on the payloads;
``investigator.py`` produces them, this layer only persists / checksums / serves.
It imports no ``backend.*`` runtime stack — only the standard library, pydantic,
structlog, filelock.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

log = structlog.get_logger(component="theme_research.provenance")

THEME_PROVENANCE_SCHEMA_VERSION = 1

_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")


def theme_sha256(payload: bytes) -> str:
    """Canonical SHA256 hex of a captured payload (one hashing scheme)."""
    return hashlib.sha256(payload).hexdigest()


class ThemeArtifactType(StrEnum):
    """What a captured byte blob is — every category an audit needs to replay.

    A promotable run must carry, at minimum, the PROMPT it sent and the
    LLM_RESPONSE it got; any web claim must carry its SERP/PAGE bytes.
    """

    SERP = "serp"
    PAGE = "page"
    PROMPT = "prompt"
    LLM_RESPONSE = "llm_response"
    TOOL_TRANSCRIPT = "tool_transcript"


class ThemeResearchSnapshot(BaseModel):
    """One captured byte blob from an investigation, persisted point-in-time.

    Mirrors :class:`MarketDataSnapshot`: ``raw_payload`` + ``size`` +
    ``raw_payload_sha256`` are self-validating (a tampered reconstruction fails
    construction), and provenance fields identify *what* / *when* / *from where*.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_id: UUID = Field(default_factory=uuid4)
    schema_version: int = Field(default=THEME_PROVENANCE_SCHEMA_VERSION, ge=1)

    artifact_type: ThemeArtifactType
    source_url: str = Field(default="", max_length=2048)
    """The fetched URL for SERP/PAGE; empty for PROMPT/LLM_RESPONSE."""
    source_domain: str = Field(default="", max_length=253)
    """Netloc the bytes came from (allowlist-checked by the investigator)."""
    http_status: int | None = Field(default=None, ge=100, le=599)
    model: str = Field(default="", max_length=128)
    """LLM model id for PROMPT/LLM_RESPONSE; empty for web bytes."""
    params: dict[str, str] = Field(default_factory=dict)
    fetch_time_utc: datetime

    raw_payload: bytes
    size: int = Field(ge=0)
    encoding: str = Field(min_length=1)
    compression: str = Field(min_length=1)
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_schema_version(self) -> ThemeResearchSnapshot:
        if self.schema_version != THEME_PROVENANCE_SCHEMA_VERSION:
            raise ValueError(
                f"theme provenance schema_version {self.schema_version} != "
                f"{THEME_PROVENANCE_SCHEMA_VERSION}; upgrade before reading"
            )
        return self

    @model_validator(mode="after")
    def _check_payload_integrity(self) -> ThemeResearchSnapshot:
        if self.size != len(self.raw_payload):
            raise ValueError(
                f"size {self.size} != len(raw_payload) {len(self.raw_payload)}"
            )
        digest = theme_sha256(self.raw_payload)
        if self.raw_payload_sha256 != digest:
            raise ValueError(
                f"raw_payload_sha256 mismatch: stored {self.raw_payload_sha256} "
                f"!= computed {digest}"
            )
        return self

    @model_validator(mode="after")
    def _check_fetch_time_aware(self) -> ThemeResearchSnapshot:
        if self.fetch_time_utc.tzinfo is None:
            raise ValueError("fetch_time_utc must be timezone-aware (UTC)")
        return self

    @classmethod
    def create(
        cls,
        *,
        artifact_type: ThemeArtifactType,
        raw_payload: bytes,
        encoding: str,
        compression: str,
        fetch_time_utc: datetime,
        source_url: str = "",
        source_domain: str = "",
        http_status: int | None = None,
        model: str = "",
        params: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ThemeResearchSnapshot:
        """Build a snapshot, computing ``size`` and ``raw_payload_sha256``."""
        return cls(
            artifact_type=artifact_type,
            source_url=source_url,
            source_domain=source_domain,
            http_status=http_status,
            model=model,
            params=dict(params or {}),
            fetch_time_utc=fetch_time_utc,
            raw_payload=raw_payload,
            size=len(raw_payload),
            encoding=encoding,
            compression=compression,
            raw_payload_sha256=theme_sha256(raw_payload),
            metadata=dict(metadata or {}),
        )


class ThemeResearchRun(BaseModel):
    """One investigation run: the captured snapshots + the structured product.

    ``promotable`` is DERIVED, never trusted from the caller: a run may be pinned
    only if it captured a PROMPT and an LLM_RESPONSE and every citation snippet
    hash referenced by the output resolves to a captured snapshot. A run that
    hid bytes is non-promotable by construction (§2.2).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=THEME_PROVENANCE_SCHEMA_VERSION, ge=1)
    run_id: str = Field(min_length=1, max_length=128)
    started_at: datetime
    prompt_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    """SHA256 of the pinned SOP prompt version used (LiveArtifactRegistry)."""
    snapshot_ids: tuple[UUID, ...] = Field(default_factory=tuple)
    captured_types: tuple[ThemeArtifactType, ...] = Field(default_factory=tuple)
    captured_pages: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    """``(source_domain, sha256)`` for every captured PAGE snapshot."""
    output_sha256: str = Field(default="", max_length=64)
    """SHA256 of the canonical-JSON serialised :class:`ThemeResearchOutput`
    (empty when no output was produced)."""
    cited_pages: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    """``(source_domain, sha256)`` for every citation the output makes."""

    @model_validator(mode="after")
    def _check_output_sha(self) -> ThemeResearchRun:
        # Empty (no output) or a real 64-char hex digest — never a truncated /
        # malformed string masquerading as a digest (codex Y P2).
        if self.output_sha256 and not _SHA256_HEX_RE.fullmatch(self.output_sha256):
            raise ValueError(
                f"output_sha256 must be empty or 64-char lowercase hex, got "
                f"{self.output_sha256!r}"
            )
        return self

    def is_promotable(self) -> tuple[bool, str]:
        """Return ``(promotable, reason)`` — the SINGLE source of truth.

        Computed purely from the stored fields so the durable record can never
        disagree with a consumer's later verdict (codex Y P1). Promotable only
        when a PROMPT and an LLM_RESPONSE were captured, a structured output
        digest was recorded, and every citation's ``(domain, sha)`` matches a
        captured page — covering both "bytes not captured" and "domain claimed
        but not the captured source".
        """
        captured = set(self.captured_types)
        if ThemeArtifactType.PROMPT not in captured:
            return False, "no PROMPT bytes captured"
        if ThemeArtifactType.LLM_RESPONSE not in captured:
            return False, "no LLM_RESPONSE bytes captured"
        if not self.output_sha256:
            return False, "no structured output digest recorded"
        captured_pages = {(d, h) for d, h in self.captured_pages}
        captured_shas = {h for _, h in self.captured_pages}
        for domain, sha in self.cited_pages:
            if (domain, sha) in captured_pages:
                continue
            if sha not in captured_shas:
                return False, f"cited snippet {sha} not byte-captured"
            return False, (
                f"citation domain {domain!r} does not match the captured "
                f"snapshot for sha {sha}"
            )
        return True, "all required bytes captured"


class ThemeResearchStore:
    """Content-addressed, append-only store for run snapshots + run records.

    Mirrors the marketdata ``SnapshotStore`` discipline: payload bytes live at
    ``payloads/<sha[:2]>/<sha>.bin`` (verify-before-adopt on read), run records
    append to ``runs.jsonl``. Same ``snapshot_id`` is refused (append-only).
    """

    _PAYLOADS = "payloads"
    _SNAPSHOT_INDEX = "snapshots.jsonl"
    _RUNS = "runs.jsonl"
    _LOCK = "theme_research.lock"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._payload_dir = self._root / self._PAYLOADS
        self._snapshot_index = self._root / self._SNAPSHOT_INDEX
        self._runs_path = self._root / self._RUNS
        self._lock = FileLock(str(self._root / self._LOCK))
        self._payload_dir.mkdir(parents=True, exist_ok=True)

    def put_snapshot(self, snap: ThemeResearchSnapshot) -> ThemeResearchSnapshot:
        """Persist a snapshot's bytes (content-addressed) + index its metadata.

        Append-only same-id rejection (codex Y P2): a re-put of an existing
        ``snapshot_id`` is refused, so two index rows can never share an id with
        different bytes and make audit/replay ambiguous. Distinct snapshots that
        happen to share bytes (different ids) are fine — the payload is stored
        once (content-addressed) and each id is indexed once.
        """
        with self._lock:
            sid = str(snap.snapshot_id)
            for row in self._load(self._snapshot_index):
                if row.get("snapshot_id") == sid:
                    raise ValueError(
                        f"snapshot_id {sid} already stored (append-only — a "
                        f"snapshot id is immutable)"
                    )
            blob_path = self._blob_path(snap.raw_payload_sha256)
            if blob_path.exists():
                # Content-addressed: identical bytes ⇒ identical file. Verify the
                # on-disk bytes still hash to the same value (corruption guard)
                # before treating it as already-stored.
                existing = blob_path.read_bytes()
                if theme_sha256(existing) != snap.raw_payload_sha256:
                    raise ValueError(
                        f"stored payload {snap.raw_payload_sha256} is corrupt "
                        f"(on-disk bytes hash differently); refusing to adopt"
                    )
            else:
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                blob_path.write_bytes(snap.raw_payload)
            # Exclude raw_payload from the JSON dump itself (codex Y P1): the
            # bytes are arbitrary web content (possibly non-UTF-8), so letting
            # pydantic serialise them to JSON would fail or be lossy. The bytes
            # already live content-addressed in the blob; the index is metadata.
            self._append(
                self._snapshot_index,
                snap.model_dump(mode="json", exclude={"raw_payload"}),
            )
        log.info(
            "theme_snapshot_put",
            snapshot_id=str(snap.snapshot_id),
            artifact_type=snap.artifact_type.value,
            sha256=snap.raw_payload_sha256,
            size=snap.size,
        )
        return snap

    def get_payload(self, sha256: str) -> bytes | None:
        """Return the stored bytes for ``sha256``, verifying integrity."""
        blob_path = self._blob_path(sha256)
        if not blob_path.is_file():
            return None
        payload = blob_path.read_bytes()
        if theme_sha256(payload) != sha256:
            raise ValueError(
                f"stored payload {sha256} failed checksum on read (corrupt); "
                f"fail-closed rather than serve tampered bytes"
            )
        return payload

    def put_run(self, run: ThemeResearchRun) -> ThemeResearchRun:
        """Append a run record (append-only; duplicate run_id refused)."""
        with self._lock:
            for row in self._load(self._runs_path):
                if row.get("run_id") == run.run_id:
                    raise ValueError(
                        f"run_id {run.run_id!r} already stored (append-only)"
                    )
            self._append(self._runs_path, run.model_dump(mode="json"))
        log.info(
            "theme_run_put",
            run_id=run.run_id,
            snapshots=len(run.snapshot_ids),
            promotable=run.is_promotable()[0],
        )
        return run

    def _blob_path(self, sha256: str) -> Path:
        return self._payload_dir / sha256[:2] / f"{sha256}.bin"

    def _append(self, path: Path, row: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    @staticmethod
    def _load(path: Path) -> Iterable[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows


__all__ = [
    "THEME_PROVENANCE_SCHEMA_VERSION",
    "ThemeArtifactType",
    "ThemeResearchRun",
    "ThemeResearchSnapshot",
    "ThemeResearchStore",
    "theme_sha256",
]
