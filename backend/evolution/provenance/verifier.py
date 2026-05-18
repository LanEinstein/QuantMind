"""Hash-anchored provenance verifier (P2-2 §1.6 + X-004).

When the X-013 amendment drafter retrieves a RAG document for
prompt injection, it walks the ``data/rag/provenance.jsonl`` ledger
to find the entry by ``doc_id``, then opens the on-disk markdown
payload and recomputes the content SHA256. A mismatch means the
document has been tampered with after ingest (out-of-band edit,
filesystem corruption, partial rebase) — the drafter must fail-close
instead of citing a silently-different source.

This module wires up the three pieces:

* :func:`compute_content_sha256` — single canonical hash function
  shared with the X-004 ingester so the writer and verifier agree
  on whitespace, encoding, etc.
* :class:`ProvenanceVerifier` — JSONL ledger reader + hash check
  helper; intentionally does **not** cache the parsed ledger so
  every retrieval re-reads the latest tail (the writer appends
  while the verifier may already be running).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.evolution.provenance.models import RagProvenanceEntry
from backend.evolution.provenance.writer import (
    DEFAULT_PROVENANCE_PATH,
    PROVENANCE_FILE_NAME,
    ProvenanceAppendError,
)


def compute_content_sha256(content: bytes) -> str:
    """Canonical SHA256 hex digest used by the writer and verifier.

    Bytes-in / hex-out so the upstream ingester and downstream
    verifier cannot disagree on Unicode normalization or trailing
    newline handling. Callers that have text in hand should encode
    once with the canonical UTF-8 representation they intend to
    persist on disk — typically ``payload.encode("utf-8")``.
    """
    return hashlib.sha256(content).hexdigest()


class ProvenanceVerifierError(RuntimeError):
    """Raised on any verification failure (missing doc, hash mismatch)."""


class ProvenanceVerifier:
    """JSONL ledger reader + hash-anchored content verifier.

    Construction is cheap: the ledger path is recorded but not read.
    Each lookup re-reads the JSONL tail so the verifier sees writes
    made by an in-process or out-of-process writer; this keeps the
    code path simple at the cost of doing one ``open()`` per call,
    which is fine — the amendment drafter retrieves a handful of
    documents per prompt assembly, not thousands.
    """

    def __init__(
        self,
        rag_root: Path | str = "data/rag",
        provenance_path: Path | str | None = None,
    ) -> None:
        self._rag_root = Path(rag_root)
        self._provenance_path = (
            Path(provenance_path)
            if provenance_path is not None
            else (self._rag_root / PROVENANCE_FILE_NAME
                  if Path(rag_root) != Path("data/rag")
                  else DEFAULT_PROVENANCE_PATH)
        )

    @property
    def provenance_path(self) -> Path:
        return self._provenance_path

    def lookup(self, doc_id: str) -> RagProvenanceEntry | None:
        """Return the most recent entry for ``doc_id`` or ``None``.

        The "most recent" semantics matters because the X-004
        rejection path appends a new entry when re-ingesting after
        a sanitisation failure — the verifier should see the latest
        decision, not a superseded one.
        """
        if not self._provenance_path.is_file():
            raise ProvenanceVerifierError(
                f"provenance ledger {self._provenance_path} is missing"
            )
        latest: RagProvenanceEntry | None = None
        with self._provenance_path.open("rb") as fh:
            for raw_line in fh:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                # First parse the line as JSON to peek at doc_id without
                # paying the full strict-Pydantic cost on every row.
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ProvenanceVerifierError(
                        f"corrupt ledger line in {self._provenance_path}: "
                        f"{exc.msg}"
                    ) from exc
                if not isinstance(record, dict) or record.get("doc_id") != doc_id:
                    continue
                # Use JSON-mode validation so the on-disk ISO timestamps
                # and JSON arrays coerce back into datetime + tuple
                # under the strict schema (model_validate would refuse
                # those coercions per ConfigDict(strict=True)).
                try:
                    latest = RagProvenanceEntry.model_validate_json(stripped)
                except Exception as exc:  # noqa: BLE001 — schema check is the boundary
                    raise ProvenanceVerifierError(
                        f"ledger entry for {doc_id!r} fails schema: {exc}"
                    ) from exc
        return latest

    def verify_entry(self, entry: RagProvenanceEntry) -> Path:
        """Resolve the on-disk markdown path and verify its SHA256.

        The path is derived deterministically from the entry's
        ``source`` + ``ingested_at`` date + ``doc_id`` (the
        ``data/rag/{source}/{date}/{doc_id}.md`` convention locked
        by P2-2 §1.6). Hash mismatch / missing file raise
        :class:`ProvenanceVerifierError` so the caller fail-closes
        instead of citing tampered content.
        """
        ingested_date = entry.ingested_at.astimezone().date().isoformat()
        payload_path = (
            self._rag_root
            / entry.source
            / ingested_date
            / f"{entry.doc_id}.md"
        )
        if not payload_path.is_file():
            raise ProvenanceVerifierError(
                f"RAG payload {payload_path} is missing — restore from git "
                f"or re-ingest"
            )
        observed = compute_content_sha256(payload_path.read_bytes())
        if observed != entry.content_sha256:
            raise ProvenanceVerifierError(
                f"hash-anchored citation failed for {entry.doc_id!r}: "
                f"ledger expected {entry.content_sha256}, file is "
                f"{observed} — refusing to cite tampered content"
            )
        return payload_path


__all__ = [
    "ProvenanceVerifier",
    "ProvenanceVerifierError",
    "compute_content_sha256",
    # Re-exported so the verifier's stdlib-only import surface is the
    # single thing the X-018 gate has to check.
    "ProvenanceAppendError",
]
