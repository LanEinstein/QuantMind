"""RAG provenance entry schema (P2-2 §1.6 + X-004).

17-field Pydantic v2 ``RagProvenanceEntry`` (plus the 5-sub-field
``SanitizationApplied`` block) that documents every document the
frontier crawler ingests into ``data/rag/{source}/{date}/{doc_id}.md``.
Each entry is the cryptographic anchor — content SHA256 + ingester
version + sanitization audit — for the markdown payload on disk; the
X-011 ``rag_ingester`` writes it via ``ProvenanceWriter.write_entry``
and the X-013 ``amendment_drafter`` reads it via
``ProvenanceVerifier.verify_entry`` to enforce hash-anchored citation
on every prompt injection.

All fields are frozen + strict + ``extra='forbid'`` (P0-3 §2 red line
12). The schema is intentionally tighter than the upstream provider
APIs report — e.g. ``language_detected`` is a 3-way literal and not a
free string, so an upstream typo cannot poison downstream filters.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

WHITELIST_SOURCES: frozenset[str] = frozenset(
    {"arxiv", "semanticscholar", "openreview", "github_releases", "akshare"}
)
"""Five RAG sources locked by P2-2 §1.1.1. Mirrors
:data:`backend.evolution.provenance.writer.WHITELIST_SOURCE_DIRS` —
the writer enforces the directory-level allowlist, this schema
enforces the field-level allowlist."""

_DOC_ID_PREFIXES = ("ARXIV", "S2", "OPENREVIEW", "GH-REL", "AKSHARE")
DOC_ID_RE = re.compile(
    r"^(?:" + "|".join(_DOC_ID_PREFIXES) + r")-[A-Za-z0-9._-]+$"
)
"""``doc_id`` format — one of five prefixes followed by a hyphen and a
provider-native identifier. The 5 prefixes are *not* the same as the
P0-8 evidence_id 5 prefixes (NEWS / MIROFISH / MARKET / RISK / DEBATE)
— RAG documents never enter ``evidence_collection`` (P2-2 §1.1.1
Round 3 Q3); the doc_id namespace stays separate so an audit query
can distinguish frontier ingest from runtime evidence at a glance."""

SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
"""Lowercase 64-char hex. Matches Python ``hashlib.sha256().hexdigest()``."""

WHITELIST_RULE_VERSION_RE = re.compile(r"^v\d+\.\d+$")
"""``whitelist_rule_version`` format — ``v1.0``, ``v1.7``, ``v2.0``.
Bumped via amendment when the source allowlist changes shape so
provenance entries pinpoint which generation of the rule accepted them."""


# ---------------------------------------------------------------------------
# SanitizationApplied — 5 sub-fields
# ---------------------------------------------------------------------------


class SanitizationApplied(BaseModel):
    """Audit trail for the three-layer prompt-injection sanitiser.

    Layer 1: ``bleach`` HTML stripping (``html_stripped``).
    Layer 2: control-character + zero-width unicode normalization
    (``control_chars_removed`` + ``unicode_normalized_nfkc``).
    Layer 3: known prompt-injection markers (``Ignore previous
    instructions``, ``System:`` etc.) detected and counted
    (``injection_markers_flagged``).

    Whitespace collapse is an opportunistic cleanup of multi-blankline
    runs that often appear after HTML stripping; not a security gate,
    but tracked so the operator can spot crawler regressions.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    html_stripped: bool
    control_chars_removed: int = Field(ge=0)
    injection_markers_flagged: int = Field(ge=0)
    unicode_normalized_nfkc: bool
    max_consecutive_whitespace_collapsed: bool


# ---------------------------------------------------------------------------
# RagProvenanceEntry — 17 fields
# ---------------------------------------------------------------------------


class RagProvenanceEntry(BaseModel):
    """One row in ``data/rag/provenance.jsonl``.

    Identifies a single RAG document end-to-end: provider identity,
    license, sanitisation audit, content SHA256 + length, the
    in-tree whitelist rule version that accepted it, and the
    ingester version that wrote the row. The 17 fields are the
    minimum needed for ``ProvenanceVerifier`` to fail-close on
    tampering / hash drift and for the amendment drafter to cite the
    source with a hash anchor.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # --- Identifiers ---
    doc_id: str = Field(min_length=1, max_length=160)
    source: Literal[
        "arxiv", "semanticscholar", "openreview", "github_releases", "akshare"
    ]
    source_url: HttpUrl
    source_domain: str = Field(min_length=1, max_length=128)

    # --- Bibliographic core ---
    title: str = Field(min_length=1, max_length=500)
    authors: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    published_at: datetime
    ingested_at: datetime

    # --- Content audit ---
    content_sha256: str = Field(min_length=64, max_length=64)
    content_length_chars: int = Field(ge=0, le=200_000)

    # --- Provenance metadata ---
    whitelist_rule_version: str = Field(min_length=1, max_length=16)
    license: str = Field(min_length=1, max_length=128)
    external_id: str = Field(min_length=1, max_length=160)
    category: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    language_detected: Literal["en", "zh", "other"]
    sanitization_applied: SanitizationApplied
    ingester_version: str = Field(min_length=1, max_length=64)

    # --- Optional rejection trail (kept None on success ingest) ---
    rejection_reason: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _check_invariants(self) -> RagProvenanceEntry:
        if not DOC_ID_RE.fullmatch(self.doc_id):
            raise ValueError(
                f"doc_id {self.doc_id!r} must match {DOC_ID_RE.pattern!r}"
            )
        if not SHA256_HEX_RE.fullmatch(self.content_sha256):
            raise ValueError(
                f"content_sha256 must be 64-char lowercase hex, "
                f"got {self.content_sha256!r}"
            )
        if not WHITELIST_RULE_VERSION_RE.fullmatch(self.whitelist_rule_version):
            raise ValueError(
                f"whitelist_rule_version must match "
                f"{WHITELIST_RULE_VERSION_RE.pattern!r}, "
                f"got {self.whitelist_rule_version!r}"
            )
        # Light cross-check: the doc_id prefix should match the source
        # so an entry cannot quietly claim "arxiv" while carrying a
        # GH-REL- doc_id (the upstream sanitiser would still let it
        # through; this catches the mismatch at schema validation).
        prefix_to_source = {
            "ARXIV": "arxiv",
            "S2": "semanticscholar",
            "OPENREVIEW": "openreview",
            "GH-REL": "github_releases",
            "AKSHARE": "akshare",
        }
        prefix = self.doc_id.split("-", 1)[0]
        if prefix == "GH":
            # GH-REL- splits into ["GH", "REL-..."] under split("-", 1)
            prefix = "GH-REL"
        expected = prefix_to_source.get(prefix)
        if expected is not None and expected != self.source:
            raise ValueError(
                f"doc_id prefix {prefix!r} implies source {expected!r} "
                f"but entry declares {self.source!r}"
            )
        # If the entry records a rejection, content_length_chars and
        # content_sha256 still must be present (post-sanitize hash of
        # whatever we received) so the audit trail covers what the
        # ingester actually saw.
        return self

    @property
    def is_rejection(self) -> bool:
        return self.rejection_reason is not None


__all__ = [
    "DOC_ID_RE",
    "RagProvenanceEntry",
    "SHA256_HEX_RE",
    "SanitizationApplied",
    "WHITELIST_RULE_VERSION_RE",
    "WHITELIST_SOURCES",
]
