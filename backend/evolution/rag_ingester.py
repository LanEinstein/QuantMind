"""RagIngester — sanitise, hash, whitelist-check, and persist a RAG document.

Crawlers (X-010) produce :class:`CrawledDocument` objects; this ingester
turns them into:

* ``data/rag/{source}/{date}/{doc_id}.md`` — the canonical markdown
  payload referenced by every downstream citation.
* ``data/rag/provenance.jsonl`` — append-only ledger of
  :class:`backend.evolution.provenance.models.RagProvenanceEntry`
  rows hash-anchoring each markdown file.

Three layers of injection defence (P2-2 §1.6):

1. **Source whitelist** — only the five locked sources
   (``arxiv`` / ``semanticscholar`` / ``openreview`` /
   ``github_releases`` / ``akshare``) reach the writer. Anything else
   is rejected with a ``RAG_DOCUMENT_REJECTED_NON_WHITELIST`` audit
   row (P2-2 §2 red line 22 / Category-5 emission via
   :class:`EvolutionAuditWriter`).
2. **Sanitisation** — :class:`Sanitiser` strips HTML, normalises
   control / zero-width characters via NFKC, and counts known
   prompt-injection markers (``Ignore previous instructions``,
   ``System:`` …). The counters land in the provenance row so a
   downstream reader can audit what the ingester actually saw.
3. **Retrieval precision floor** — :data:`RAG_RETRIEVAL_PRECISION_FLOOR`
   = 0.80 (R3 lock). Callers that compute a precision figure across a
   batch must invoke :func:`assert_precision_floor` BEFORE feeding
   the documents into a prompt; under-floor batches fail-closed.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
``backend.evolution.provenance`` and ``backend.audit`` are explicitly
allowed (provenance is part of the substrate, audit is the cross-cut).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from backend.evolution.provenance.models import (
    DOC_ID_RE,
    WHITELIST_SOURCES,
    RagProvenanceEntry,
    SanitizationApplied,
)
from backend.evolution.provenance.verifier import compute_content_sha256
from backend.evolution.provenance.writer import ProvenanceWriter
from backend.services.evolution_audit_writer import EvolutionAuditWriter

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

RAG_RETRIEVAL_PRECISION_FLOOR = 0.80
"""R3 hard floor (P2-2 §2 red line 22). Batches whose retrieval
precision falls below this value fail-closed inside
:func:`assert_precision_floor` so an under-quality batch never makes
it into a prompt assembly."""

INJECTION_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bignore (?:all )?previous instructions\b", re.IGNORECASE),
    re.compile(r"^\s*system\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*assistant\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*user\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"</?(?:system|assistant|user|prompt)>", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
)
"""Known prompt-injection markers. The sanitiser COUNTS hits — it does
NOT redact — so the provenance row honestly records what the upstream
document contained. The amendment drafter inspects this counter when
ranking documents for inclusion (a document with high marker counts
is a tag-it-don't-trust-it signal)."""

HTML_TAG_RE = re.compile(r"<[^<>]+>")
"""Pragmatic HTML stripper — we are not parsing real HTML, just
sweeping the tag-shaped bytes out of crawler-supplied text. Real HTML
that survives crawling is downgraded to plain text before ingest so
this regex is sufficient. ``bleach`` is a future upgrade path if a
crawler ever yields markup that needs nested-tag awareness."""

WHITESPACE_RUN_RE = re.compile(r"\n\s*\n\s*\n+")
"""Three-or-more consecutive blank lines collapse to a double blank."""

INGESTER_VERSION = "v1.0"
"""Mirror of :data:`PROVENANCE_INGESTER_VERSION` in the
``provenance.jsonl`` writer — bumped via amendment when the sanitiser
contract changes."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RagIngesterError(Exception):
    """Base class for ingester failures."""


class NonWhitelistedSourceError(RagIngesterError):
    """Raised when ``source`` is not in :data:`WHITELIST_SOURCES`."""


class RetrievalPrecisionTooLowError(RagIngesterError):
    """R3 fail-closed — batch precision under the 0.80 floor."""


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrawledDocument:
    """Single document returned by a crawler (X-010) before sanitise.

    Frozen so the caller cannot mutate the crawler output between
    sanitise and write — every transform produces a fresh value.
    """

    doc_id: str
    source: Literal[
        "arxiv", "semanticscholar", "openreview", "github_releases", "akshare"
    ]
    source_url: str
    source_domain: str
    title: str
    authors: tuple[str, ...]
    published_at: datetime
    license: str
    external_id: str
    raw_text: str
    category: tuple[str, ...] = field(default_factory=tuple)
    language_detected: Literal["en", "zh", "other"] = "en"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one :meth:`RagIngester.ingest` call."""

    accepted: bool
    provenance_entry: RagProvenanceEntry | None
    payload_path: Path | None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Sanitiser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SanitisedDocument:
    """Sanitised text + the audit counters."""

    text: str
    applied: SanitizationApplied


@dataclass(frozen=True)
class Sanitiser:
    """Three-step text sanitiser (HTML → NFKC → injection-marker count).

    Frozen so the caller cannot mutate the sanitiser between calls
    inside one batch; the redaction logic is intentionally simple
    enough that all configuration lives on the dataclass.
    """

    strip_html: bool = True
    collapse_whitespace: bool = True

    def sanitise(self, raw_text: str) -> SanitisedDocument:
        """Return :class:`SanitisedDocument` with applied counter audit."""
        text = raw_text

        # Count injection markers BEFORE any tag stripping so XML-style
        # ``<system>...</system>`` / ``</prompt>`` markers in
        # INJECTION_MARKER_PATTERNS are still observed in the audit
        # counter (codex review P2-2). The counter is honest about
        # what the upstream document contained; the sanitiser then
        # removes the tag bytes from the persisted body.
        pre_strip_markers = sum(
            len(pattern.findall(text)) for pattern in INJECTION_MARKER_PATTERNS
        )

        html_stripped = False
        if self.strip_html and HTML_TAG_RE.search(text) is not None:
            text = HTML_TAG_RE.sub("", text)
            html_stripped = True

        # NFKC normalises width / ligatures so a zero-width character
        # cannot smuggle through the marker regexes.
        before = text
        normalized = unicodedata.normalize("NFKC", text)
        unicode_normalized = normalized != before
        text = normalized

        # Strip C-category control chars (except newline + tab which
        # are already low-risk for a prompt injection vector).
        control_chars_removed = 0
        kept: list[str] = []
        for ch in text:
            if ch in ("\n", "\t"):
                kept.append(ch)
                continue
            if unicodedata.category(ch).startswith("C"):
                control_chars_removed += 1
                continue
            kept.append(ch)
        text = "".join(kept)

        whitespace_collapsed = False
        if self.collapse_whitespace:
            collapsed = WHITESPACE_RUN_RE.sub("\n\n", text)
            if collapsed != text:
                whitespace_collapsed = True
            text = collapsed

        # Final tally = max(pre-strip, post-sanitise). The post-sanitise
        # pass catches markers that only emerge after NFKC normalisation
        # collapses zero-width tricks; pre-strip ensures HTML-tag style
        # markers are not erased by the tag stripper.
        post_strip_markers = sum(
            len(pattern.findall(text)) for pattern in INJECTION_MARKER_PATTERNS
        )
        markers_flagged = max(pre_strip_markers, post_strip_markers)

        return SanitisedDocument(
            text=text,
            applied=SanitizationApplied(
                html_stripped=html_stripped,
                control_chars_removed=control_chars_removed,
                injection_markers_flagged=markers_flagged,
                unicode_normalized_nfkc=unicode_normalized,
                max_consecutive_whitespace_collapsed=whitespace_collapsed,
            ),
        )


# ---------------------------------------------------------------------------
# Precision floor helper
# ---------------------------------------------------------------------------


def assert_precision_floor(precision: float) -> None:
    """Raise :class:`RetrievalPrecisionTooLowError` on sub-floor batches.

    R3 fail-closed: ``precision < 0.80`` is unrecoverable; the caller
    must either widen the query or refuse to ground the prompt on
    this batch (P2-2 §2 red line 22).
    """
    if precision < 0:
        raise RetrievalPrecisionTooLowError(
            f"precision {precision} is negative; caller bug"
        )
    if precision < RAG_RETRIEVAL_PRECISION_FLOOR:
        raise RetrievalPrecisionTooLowError(
            f"precision {precision:.4f} below floor "
            f"{RAG_RETRIEVAL_PRECISION_FLOOR}"
        )


# ---------------------------------------------------------------------------
# Ingester
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RagIngester:
    """Sanitise → whitelist-check → write → audit.

    Frozen so the writer / audit / sanitiser triple is constructed once
    at boot and reused. The dispatcher (X-008) constructs one instance.
    """

    writer: ProvenanceWriter
    audit: EvolutionAuditWriter
    rag_root: Path = Path("data/rag")
    whitelist_rule_version: str = "v1.0"
    sanitiser: Sanitiser = field(default_factory=Sanitiser)
    ingester_version: str = INGESTER_VERSION

    async def ingest(
        self,
        document: CrawledDocument,
        *,
        correlation_id: str | None = None,
    ) -> IngestResult:
        """Sanitise + persist one document. Returns the outcome.

        Pipeline:

        1. Reject if ``source`` is not in :data:`WHITELIST_SOURCES`.
        2. Sanitise the raw text + collect the audit counters.
        3. Compute the content SHA256 (canonical via
           :func:`compute_content_sha256`).
        4. Write the markdown payload to
           ``data/rag/{source}/{YYYY-MM-DD}/{doc_id}.md``.
        5. Write a :class:`RagProvenanceEntry` line to
           ``data/rag/provenance.jsonl`` via the
           :class:`ProvenanceWriter` from X-002.
        6. Emit a ``RAG_DOCUMENT_INGESTED`` audit row.
        """
        if document.source not in WHITELIST_SOURCES:
            await self.audit.rag_document_rejected_non_whitelist(
                attempted_source=document.source,
                url=document.source_url,
                reason="source not in WHITELIST_SOURCES",
                correlation_id=correlation_id,
            )
            return IngestResult(
                accepted=False,
                provenance_entry=None,
                payload_path=None,
                reason="non_whitelisted_source",
            )
        if not DOC_ID_RE.fullmatch(document.doc_id):
            await self.audit.rag_document_rejected_non_whitelist(
                attempted_source=document.source,
                url=document.source_url,
                reason=f"doc_id {document.doc_id!r} fails DOC_ID_RE",
                correlation_id=correlation_id,
            )
            return IngestResult(
                accepted=False,
                provenance_entry=None,
                payload_path=None,
                reason="doc_id_malformed",
            )

        sanitised = self.sanitiser.sanitise(document.raw_text)
        payload_bytes = sanitised.text.encode("utf-8")
        sha = compute_content_sha256(payload_bytes)

        # Critical: the on-disk layout key MUST match what
        # ``ProvenanceVerifier.verify_entry`` reconstructs from
        # ``entry.ingested_at``. Using ``published_at`` here causes
        # hash-anchored retrieval to look in the wrong day's folder
        # for any document whose publish date differs from the
        # crawl date (codex review P1-1).
        ingested_at = datetime.now(UTC)
        payload_path = self._write_payload(
            doc_id=document.doc_id,
            source=document.source,
            ingested_at=ingested_at,
            payload_bytes=payload_bytes,
        )

        entry = RagProvenanceEntry(
            doc_id=document.doc_id,
            source=document.source,
            source_url=document.source_url,  # type: ignore[arg-type]
            source_domain=document.source_domain,
            title=document.title,
            authors=document.authors,
            published_at=document.published_at,
            ingested_at=ingested_at,
            content_sha256=sha,
            content_length_chars=min(len(sanitised.text), 200_000),
            whitelist_rule_version=self.whitelist_rule_version,
            license=document.license,
            external_id=document.external_id,
            category=document.category,
            language_detected=document.language_detected,
            sanitization_applied=sanitised.applied,
            ingester_version=self.ingester_version,
            rejection_reason=None,
        )
        self.writer.write_entry(entry)

        await self.audit.rag_document_ingested(
            doc_id=document.doc_id,
            source=document.source,
            content_sha256=sha,
            whitelist_rule_version=self.whitelist_rule_version,
            correlation_id=correlation_id,
        )

        return IngestResult(
            accepted=True,
            provenance_entry=entry,
            payload_path=payload_path,
            reason=None,
        )

    def _write_payload(
        self,
        *,
        doc_id: str,
        source: str,
        ingested_at: datetime,
        payload_bytes: bytes,
    ) -> Path:
        # Match ProvenanceVerifier.verify_entry's reconstruction:
        # ``entry.ingested_at.astimezone().date().isoformat()``.
        ingested_date = ingested_at.astimezone().date().isoformat()
        date_dir = self.rag_root / source / ingested_date
        date_dir.mkdir(parents=True, exist_ok=True)
        payload_path = date_dir / f"{doc_id}.md"
        payload_path.write_bytes(payload_bytes)
        return payload_path


__all__ = [
    "INGESTER_VERSION",
    "INJECTION_MARKER_PATTERNS",
    "RAG_RETRIEVAL_PRECISION_FLOOR",
    "CrawledDocument",
    "IngestResult",
    "NonWhitelistedSourceError",
    "RagIngester",
    "RagIngesterError",
    "RetrievalPrecisionTooLowError",
    "SanitisedDocument",
    "Sanitiser",
    "assert_precision_floor",
]
