"""O-001 info-digest evidence DTOs + writer (NEWS- / MARKET- prefixes).

Mirrors the narrow-writer pattern of
:class:`backend.mirofish.output_writer.MiroFishEvidenceWriter`: each
evidence domain gets its own single public write entry point so the
locked prefix set (P0-8 §1.6.2) is enforced by construction. The digest
produces exactly two documents per trade date:

* ``MARKET-DIGEST-{YYYYMMDD}`` — index / sentiment / sector-heat /
  related-sector sections (deterministic market aggregation).
* ``NEWS-DIGEST-{YYYYMMDD}`` — multi-domain 5-source news roll-up.

Both ids are unique per trade date, so a same-day cron re-run collides
on the ``evidence_id`` unique index and the writer reports ``False``
(idempotent, never a second row). The writer has no read / update /
delete APIs and no RiskCheckSummary plumbing — evidence-only by
construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import structlog

from backend.mirofish.info_digest import (
    InfoDigest,
    render_market_sections,
    render_news_section,
)
from backend.models.evidence import EvidencePrefix, validate_evidence_id

if TYPE_CHECKING:
    from backend.data.database import MongoDBService

log = structlog.get_logger(component="mirofish.digest_evidence")

DigestEvidenceKind = Literal["market_digest", "news_digest"]

_ALLOWED_PREFIXES = (
    f"{EvidencePrefix.MARKET.value}-",
    f"{EvidencePrefix.NEWS.value}-",
)


class InfoDigestEvidenceError(RuntimeError):
    """Raised when a digest evidence write is rejected pre-Mongo."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class InfoDigestEvidence:
    """Frozen DTO for one digest ``evidence_collection`` write."""

    evidence_id: str
    kind: DigestEvidenceKind
    content: str
    trade_date: str  # YYYY-MM-DD Asia/Shanghai
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )

    def to_mongo(self) -> dict[str, object]:
        """Project the DTO to a Mongo-insertable dict."""
        prefix = self.evidence_id.split("-", 1)[0]
        return {
            "evidence_id": self.evidence_id,
            "prefix": prefix,
            "kind": self.kind,
            "content": self.content,
            "trade_date": self.trade_date,
            "created_at": self.created_at,
        }


def make_market_digest_evidence_id(trade_date: str) -> str:
    """``MARKET-DIGEST-{YYYYMMDD}`` — unique per trade date."""
    return f"MARKET-DIGEST-{trade_date.replace('-', '')}"


def make_news_digest_evidence_id(trade_date: str) -> str:
    """``NEWS-DIGEST-{YYYYMMDD}`` — unique per trade date."""
    return f"NEWS-DIGEST-{trade_date.replace('-', '')}"


def build_market_digest_evidence(digest: InfoDigest) -> InfoDigestEvidence:
    """Market-side digest sections as one MARKET- evidence DTO."""
    return InfoDigestEvidence(
        evidence_id=make_market_digest_evidence_id(digest.trade_date),
        kind="market_digest",
        content=render_market_sections(digest),
        trade_date=digest.trade_date,
    )


def build_news_digest_evidence(digest: InfoDigest) -> InfoDigestEvidence:
    """News-side digest section as one NEWS- evidence DTO."""
    return InfoDigestEvidence(
        evidence_id=make_news_digest_evidence_id(digest.trade_date),
        kind="news_digest",
        content=render_news_section(digest),
        trade_date=digest.trade_date,
    )


class InfoDigestEvidenceWriter:
    """Single entry point for digest ``evidence_collection`` writes.

    Narrow on purpose: one insert per call, prefix-validated, idempotent
    per trade date via the unique ``evidence_id`` index. Duplicate-key
    collisions (same-day re-run) and generic insert failures both return
    ``False`` — the digest is a best-effort daily artifact, never a
    boot- or pipeline-blocker.
    """

    COLLECTION_NAME = "evidence_collection"

    def __init__(self, mongodb: MongoDBService) -> None:
        self._mongodb = mongodb
        self._log = log

    async def write(self, evidence: InfoDigestEvidence) -> bool:
        """Persist one digest evidence row. ``True`` on a fresh insert."""
        if not evidence.evidence_id.startswith(_ALLOWED_PREFIXES):
            raise InfoDigestEvidenceError(
                f"digest evidence_id {evidence.evidence_id!r} must use "
                f"one of prefixes {_ALLOWED_PREFIXES}",
                reason="prefix_violation",
            )
        try:
            validate_evidence_id(evidence.evidence_id)
        except ValueError as exc:
            raise InfoDigestEvidenceError(
                str(exc), reason="evidence_id_invalid"
            ) from exc

        coll = self._mongodb._db[self.COLLECTION_NAME]  # noqa: SLF001
        try:
            await coll.insert_one(evidence.to_mongo())
        except Exception as exc:
            if _is_duplicate_key_error(exc):
                self._log.info(
                    "digest_evidence_already_written",
                    evidence_id=evidence.evidence_id,
                    trade_date=evidence.trade_date,
                )
                return False
            self._log.warning(
                "digest_evidence_insert_failed",
                evidence_id=evidence.evidence_id,
                error=str(exc),
            )
            return False
        self._log.info(
            "digest_evidence_written",
            evidence_id=evidence.evidence_id,
            kind=evidence.kind,
        )
        return True


def _is_duplicate_key_error(exc: Exception) -> bool:
    """Library-agnostic Mongo duplicate-key detection (mirrors output_writer)."""
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in (11000, 11001):
        return True
    if type(exc).__name__ == "DuplicateKeyError":
        return True
    msg = str(exc).lower()
    return "duplicate key" in msg or "e11000" in msg


__all__ = [
    "DigestEvidenceKind",
    "InfoDigestEvidence",
    "InfoDigestEvidenceError",
    "InfoDigestEvidenceWriter",
    "build_market_digest_evidence",
    "build_news_digest_evidence",
    "make_market_digest_evidence_id",
    "make_news_digest_evidence_id",
]
