"""RAG provenance subpackage (P2-2 §1.6 + X-002/X-004).

Append-only JSONL store at ``data/rag/provenance.jsonl`` plus per-source
markdown payloads under ``data/rag/{source}/{date}/{doc_id}.md``. The
schema (17 fields + 5 sanitization sub-fields) lands in X-004; this
subpackage ships the lower-level filesystem primitives in X-002 first so
the schema can be added incrementally without re-touching the IO layer.
"""

from backend.evolution.provenance.writer import (
    DEFAULT_PROVENANCE_PATH,
    PROVENANCE_FILE_NAME,
    ProvenanceAppendError,
    ProvenanceWriter,
    fail_fast_validate_paths,
)

__all__ = [
    "DEFAULT_PROVENANCE_PATH",
    "PROVENANCE_FILE_NAME",
    "ProvenanceAppendError",
    "ProvenanceWriter",
    "fail_fast_validate_paths",
]
