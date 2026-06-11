"""Structured reverse-deduction SOP output schema (Y-002).

The first-class design artifact of the theme-research layer: the explicit,
strict, machine-readable shape the LLM investigation MUST produce. Encoding the
"第一性原理·产业链倒推" methodology as a typed schema is what makes the layer
red-line-safe (P0-8-amendment-2026-06-01 §2.11):

* The LLM is asked for **structured output matching this schema**, NOT free
  prose that live code regex-parses into candidates. The strict validation IS
  the prompt-injection containment surface — adversarial web text cannot smuggle
  a decision field through, because every field is typed and bounded and NONE of
  them is a decision field (§2.3: codes are sourcing hints, never orders).
* Every step carries citations + a confidence, and ``null_result`` is explicit
  so "查无" is transparent rather than hallucinated (§2.11 analysis framework).
* The five steps mirror the SOP skeleton (DIRECTION → SECTORS → CHAIN →
  CHOKEPOINT → TICKERS). The skeleton is frozen methodology; only wording /
  exemplars evolve (Y-006 registry), so the *shape* here never changes without
  an amendment.

This module is pure: frozen Pydantic v2 strict models, no IO, no LLM, no
``backend.*`` runtime imports. It defines WHAT a valid investigation looks like;
``investigator.py`` produces it and ``provenance.py`` captures the bytes.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

THEME_SOP_SCHEMA_VERSION = 1
"""Locked schema version. A structural change bumps this (+ amendment) so a
stale pinned artifact / replay fails closed rather than mis-parsing."""

# 6-digit A-share code (沪深主板/创业板/ETF live in this space). The selector
# re-validates membership against the quant universe; here we only reject
# structurally impossible codes so a candidate is at least addressable.
STOCK_CODE_RE = re.compile(r"^\d{6}$")

# SHA256 hex — links a citation back to the exact captured page/SERP bytes in
# provenance.py (content-addressed). A citation whose snippet is not byte-pinned
# cannot be promoted (the run is non-promotable).
SNIPPET_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ThemeStep(StrEnum):
    """The five frozen SOP steps (P0-8-amendment-2026-06-01 §2.11).

    ① DIRECTION  — 判未来大方向 (宏观/政策/技术拐点; allowlisted sources only)
    ② SECTORS    — 圈受益板块 (映射申万行业 / 概念板块)
    ③ CHAIN      — 倒推必需产业链 (上游材料→中游→下游; reads pinned KG)
    ④ CHOKEPOINT — 识别卡脖子环节 (断供 × 替代难度 × 供应集中度 × 未炒热)
    ⑤ TICKERS    — 挖代表性标的 (链环节→上市公司)
    """

    DIRECTION = "direction"
    SECTORS = "sectors"
    CHAIN = "chain"
    CHOKEPOINT = "chokepoint"
    TICKERS = "tickers"


# The frozen step ordering. Y-006's registry verifies the prompt YAML enumerates
# exactly these step keys in this order (the skeleton is immutable methodology).
THEME_SOP_STEPS: tuple[ThemeStep, ...] = (
    ThemeStep.DIRECTION,
    ThemeStep.SECTORS,
    ThemeStep.CHAIN,
    ThemeStep.CHOKEPOINT,
    ThemeStep.TICKERS,
)


class SourceCitation(BaseModel):
    """One evidence citation: which allowlisted source + the byte-pinned snippet.

    ``source_domain`` is the netloc the investigator fetched; it must have been
    on the source allowlist at fetch time (the investigator enforces that — the
    schema only records it for audit). ``snippet_sha256`` ties the claim to the
    exact captured bytes in :mod:`provenance`, so a citation can never reference
    text that was not provenance-captured.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    source_domain: str = Field(min_length=1, max_length=253)
    snippet_sha256: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _check_hash(self) -> SourceCitation:
        if not SNIPPET_SHA256_RE.fullmatch(self.snippet_sha256):
            raise ValueError(
                f"snippet_sha256 must be 64-char lowercase hex, got "
                f"{self.snippet_sha256!r}"
            )
        return self


class ChokePointFinding(BaseModel):
    """A qualitative choke-point identification for one chain link.

    The LLM identifies WHICH link is a bottleneck and WHY (断供负面 / 替代难度 /
    供应集中度 / 未炒热) as advisory rationale text + confidence. It does NOT
    compute the quant choke-point score — that is the deterministic centrality
    (Y-001) × crowding/valuation percentile layer downstream (§2.10). Keeping the
    qualitative finding and the quant score separate is the bright line.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    chain_link: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[SourceCitation, ...] = Field(default_factory=tuple)


class ThemeCandidate(BaseModel):
    """One sourced candidate: a code + the chain link it represents + rationale.

    A candidate is a **sourcing hint**, never an order: ``code`` is a bare
    6-digit string the downstream deterministic pipeline must still qualify
    (排除四件套 + affordability + 14-check) and a human must pin. ``confidence``
    and ``rationale`` are advisory text (P0-10-allowed display fields). There is
    deliberately no side / volume / price field here — by construction the LLM
    cannot express a trade.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(min_length=6, max_length=6)
    name: str = Field(default="", max_length=64)
    sector: str = Field(min_length=1, max_length=128)
    chain_link: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    citations: tuple[SourceCitation, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check_code(self) -> ThemeCandidate:
        if not STOCK_CODE_RE.fullmatch(self.code):
            raise ValueError(
                f"candidate code must be a 6-digit A-share code, got {self.code!r}"
            )
        return self


class ThemeResearchOutput(BaseModel):
    """The full structured investigation result (one research run's product).

    Maps the SOP chain ``趋势 → 板块 → 链环节 → 卡脖子理由 → 候选 codes`` onto
    typed fields. Strict + frozen + ``extra="forbid"`` so the LLM cannot add an
    out-of-band key; every field is sourcing/advisory, none is a decision field.

    Invariants:

    * ``null_result=True`` ⇒ ``candidates`` and ``chokepoints`` empty — "查无"
      must be honest, not paired with smuggled picks.
    * Candidate codes are unique (an ambiguous duplicate fails closed).
    * A non-null result must name at least one beneficiary sector (the SOP
      cannot skip from a macro trend straight to tickers).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: int = Field(default=THEME_SOP_SCHEMA_VERSION, ge=1)
    trend_direction: str = Field(min_length=1, max_length=4000)
    beneficiary_sectors: tuple[str, ...] = Field(default_factory=tuple)
    chain_links: tuple[str, ...] = Field(default_factory=tuple)
    chokepoints: tuple[ChokePointFinding, ...] = Field(default_factory=tuple)
    candidates: tuple[ThemeCandidate, ...] = Field(default_factory=tuple)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    null_result: bool = False
    trend_citations: tuple[SourceCitation, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check_schema_version(self) -> ThemeResearchOutput:
        if self.schema_version != THEME_SOP_SCHEMA_VERSION:
            raise ValueError(
                f"theme SOP schema_version {self.schema_version} != "
                f"{THEME_SOP_SCHEMA_VERSION}; module needs upgrade before reading"
            )
        return self

    @model_validator(mode="after")
    def _check_null_result_consistency(self) -> ThemeResearchOutput:
        if self.null_result and (self.candidates or self.chokepoints):
            raise ValueError(
                "null_result=True must have empty candidates and chokepoints "
                "(透明 null result; never pair '查无' with smuggled picks)"
            )
        if not self.null_result and not self.beneficiary_sectors:
            raise ValueError(
                "a non-null result must name ≥1 beneficiary sector "
                "(SOP cannot jump from trend to tickers without a sector)"
            )
        return self

    @model_validator(mode="after")
    def _check_unique_codes(self) -> ThemeResearchOutput:
        seen: set[str] = set()
        for cand in self.candidates:
            if cand.code in seen:
                raise ValueError(
                    f"duplicate candidate code {cand.code!r} — ambiguous output"
                )
            seen.add(cand.code)
        return self

    @model_validator(mode="after")
    def _check_citations_present(self) -> ThemeResearchOutput:
        """Every claim in a non-null result must cite a byte-pinned source.

        Without this a candidate / chokepoint / trend could carry zero citations,
        leave ``cited_snippet_hashes`` empty, and a run could be marked promotable
        with NO captured source bytes behind its picks (codex Y P1). Requiring a
        citation forces the promotability check to verify the bytes were captured.
        """
        if self.null_result:
            return self
        if not self.trend_citations:
            raise ValueError(
                "a non-null result must cite the trend (trend_citations non-empty)"
            )
        for cand in self.candidates:
            if not cand.citations:
                raise ValueError(
                    f"candidate {cand.code!r} must cite ≥1 byte-pinned source "
                    f"(no uncited promotable pick)"
                )
        for cp in self.chokepoints:
            if not cp.citations:
                raise ValueError(
                    f"chokepoint {cp.chain_link!r} must cite ≥1 byte-pinned source"
                )
        return self


__all__ = [
    "SNIPPET_SHA256_RE",
    "STOCK_CODE_RE",
    "THEME_SOP_SCHEMA_VERSION",
    "THEME_SOP_STEPS",
    "ChokePointFinding",
    "SourceCitation",
    "ThemeCandidate",
    "ThemeResearchOutput",
    "ThemeStep",
]
