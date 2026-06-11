"""Machine-readable theme candidate artifact + evidence separation (Y-003).

The bright line of P0-8-amendment-2026-06-01 §2.3: the LLM's raw output may only
ever become **display/audit evidence text** (``evidence_collection.content`` with
the new ``THEME-`` prefix); live selection code must NEVER regex-parse that prose
into candidates. The machine-readable candidate set is instead a SEPARATE,
content-addressed, frozen artifact built ONLY from the strict-validated
:class:`ThemeResearchOutput` typed fields — so adversarial web text smuggled into
a rationale string can never become a tradeable code.

Two physically separate products from one validated investigation:

* :func:`theme_evidence_text` — a plain human-readable string for the THEME-
  evidence row (display/audit only; must be rendered through the fixed-escape
  Feishu renderer downstream — never executed, never parsed back).
* :class:`ThemeCandidateArtifact` — codes + sector/chain mapping + scores, with a
  content SHA256 that a human pins (Y-004 / LiveArtifactRegistry discipline)
  before it can influence live selection.

Pure module: frozen Pydantic strict + the evidence-id validator. No LLM, no IO,
no ``backend.*`` trading-stack imports (``backend.models.evidence`` is a pure
model).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.evidence import validate_evidence_id
from backend.theme_research.sop_schema import (
    STOCK_CODE_RE,
    ThemeResearchOutput,
)

THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION = 1
THEME_EVIDENCE_PREFIX = "THEME"


def build_theme_evidence_id(run_id: str, *, seq: int = 0) -> str:
    """Return a validated ``THEME-{run_id}[-{seq}]`` evidence id.

    Raises ``ValueError`` if ``seq`` is negative or ``run_id`` makes the id
    violate the locked format — the caller keeps ``run_id`` to the evidence-id
    suffix charset.
    """
    if seq < 0:
        raise ValueError(f"seq must be non-negative, got {seq}")
    suffix = f"{run_id}-{seq}" if seq else run_id
    evidence_id = f"{THEME_EVIDENCE_PREFIX}-{suffix}"
    validate_evidence_id(evidence_id)
    return evidence_id


def theme_evidence_text(output: ThemeResearchOutput) -> str:
    """Render the investigation as plain display/audit text (NOT machine-read).

    This is what lands in ``evidence_collection.content``. It is deliberately
    human prose: no live code parses it back into candidates (that is what
    :class:`ThemeCandidateArtifact` is for). Shown in Feishu only via the
    fixed-escape renderer.
    """
    lines = [
        f"趋势: {output.trend_direction}",
        f"受益板块: {', '.join(output.beneficiary_sectors) or '—'}",
        f"产业链环节: {', '.join(output.chain_links) or '—'}",
        f"总体置信度: {output.overall_confidence:.2f}",
        f"null_result: {output.null_result}",
    ]
    for cp in output.chokepoints:
        lines.append(
            f"卡脖子[{cp.chain_link}] (conf={cp.confidence:.2f}): {cp.rationale}"
        )
    for cand in output.candidates:
        lines.append(
            f"候选 {cand.code} [{cand.sector}/{cand.chain_link}] "
            f"(conf={cand.confidence:.2f}): {cand.rationale}"
        )
    return "\n".join(lines)


class ThemeCandidateEntry(BaseModel):
    """One machine-readable pick: code + mapping + score (NO prose).

    Only the typed, bounded fields — the rationale prose stays in the evidence
    text, never here, so the artifact carries nothing an injection could exploit.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(min_length=6, max_length=6)
    sector: str = Field(min_length=1, max_length=128)
    chain_link: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_code(self) -> ThemeCandidateEntry:
        if not STOCK_CODE_RE.fullmatch(self.code):
            raise ValueError(f"entry code must be a 6-digit code, got {self.code!r}")
        return self


# Fixed precision for confidence in the digest. Hashing the raw float repr would
# make the pin fragile to float representation (0.1+0.2 != 0.3) and could silently
# fail-closed a pinned artifact on a re-parse (codex/review finding). A canonical
# fixed-decimal string is collision-stable across parses.
_CONFIDENCE_DIGEST_FORMAT = "{:.6f}"


def _content_digest(
    *,
    schema_version: int,
    run_id: str,
    prompt_version_hash: str,
    source_promotable: bool,
    entries: tuple[ThemeCandidateEntry, ...],
) -> str:
    """Canonical SHA256 over ALL pin-relevant content.

    Deterministic (sorted keys, no whitespace, fixed-precision confidence) so the
    same picks always hash the same — this is the value a human pins. Binds
    ``schema_version`` and ``source_promotable`` (review finding): the pin then
    refuses a non-promotable / schema-drifted artifact by HASH, not only by the
    runtime boolean — even a buggy/forged ``source_promotable`` cannot reuse a
    hash pinned for a different promotability/version.
    """
    payload = {
        "schema_version": schema_version,
        "run_id": run_id,
        "prompt_version_hash": prompt_version_hash,
        "source_promotable": source_promotable,
        "entries": [
            {
                "code": e.code,
                "sector": e.sector,
                "chain_link": e.chain_link,
                "confidence": _CONFIDENCE_DIGEST_FORMAT.format(e.confidence),
            }
            for e in entries
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ThemeCandidateArtifact(BaseModel):
    """Content-addressed, human-pinnable candidate set (separate from evidence).

    Built ONLY from a strict-validated :class:`ThemeResearchOutput` (typed code /
    sector / chain_link / confidence) — never from parsed prose. ``content_sha256``
    is self-verifying and is the hash a human approves (Y-004) before any of these
    codes can enter the deterministic selector as peer-sourced candidates.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(
        default=THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION, ge=1
    )
    run_id: str = Field(min_length=1, max_length=128)
    prompt_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_promotable: bool
    """True only if the source run captured all required bytes (non-promotable
    runs yield an artifact that the pin layer must refuse — fail-closed)."""
    created_at: datetime
    entries: tuple[ThemeCandidateEntry, ...] = Field(default_factory=tuple)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_digest(self) -> ThemeCandidateArtifact:
        expected = _content_digest(
            schema_version=self.schema_version,
            run_id=self.run_id,
            prompt_version_hash=self.prompt_version_hash,
            source_promotable=self.source_promotable,
            entries=self.entries,
        )
        if self.content_sha256 != expected:
            raise ValueError(
                f"content_sha256 mismatch: stored {self.content_sha256} != "
                f"computed {expected} (artifact tampered)"
            )
        return self

    @model_validator(mode="after")
    def _check_unique_codes(self) -> ThemeCandidateArtifact:
        seen: set[str] = set()
        for e in self.entries:
            if e.code in seen:
                raise ValueError(f"duplicate candidate code {e.code!r}")
            seen.add(e.code)
        return self

    @classmethod
    def from_output(
        cls,
        *,
        run_id: str,
        prompt_version_hash: str,
        output: ThemeResearchOutput,
        source_promotable: bool,
        created_at: datetime,
    ) -> ThemeCandidateArtifact:
        """Build the artifact from the typed output candidates ONLY.

        By construction reads ``output.candidates[i].{code,sector,chain_link,
        confidence}`` — typed, bounded fields — and never touches any rationale /
        evidence prose. A malicious string in a rationale cannot add a code here.
        """
        entries = tuple(
            ThemeCandidateEntry(
                code=c.code,
                sector=c.sector,
                chain_link=c.chain_link,
                confidence=c.confidence,
            )
            for c in output.candidates
        )
        digest = _content_digest(
            schema_version=THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
            run_id=run_id,
            prompt_version_hash=prompt_version_hash,
            source_promotable=source_promotable,
            entries=entries,
        )
        return cls(
            run_id=run_id,
            prompt_version_hash=prompt_version_hash,
            source_promotable=source_promotable,
            created_at=created_at,
            entries=entries,
            content_sha256=digest,
        )

    def content_hash(self) -> str:
        """The hash a human pins (Y-004 / LiveArtifactRegistry discipline)."""
        return self.content_sha256


__all__ = [
    "THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION",
    "THEME_EVIDENCE_PREFIX",
    "ThemeCandidateArtifact",
    "ThemeCandidateEntry",
    "build_theme_evidence_id",
    "theme_evidence_text",
]
