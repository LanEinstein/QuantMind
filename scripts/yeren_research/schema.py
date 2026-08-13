"""Machine-readable research records that preserve evidence-layer boundaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class FrozenModel(BaseModel):
    """Research records are replaced by append-only revisions, never mutated."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceStrength(StrEnum):
    EXPLICIT = "explicit"
    CREDIBLE = "credible"
    TENTATIVE = "tentative"


class ReconstructionPrecision(StrEnum):
    DAILY = "daily"
    INTRADAY = "intraday"
    DIRECTIONAL = "directional"


class StatementType(StrEnum):
    VERIFIABLE_FACT = "verifiable_fact"
    MARKET_STATE = "market_state"
    SECURITY_VIEW = "security_view"
    NEWS_OR_EARNINGS_INTERPRETATION = "news_or_earnings_interpretation"
    EXECUTED_ACTION = "executed_action"
    PLANNED_ACTION = "planned_action"
    CONDITIONAL_RULE = "conditional_rule"
    RETROSPECTIVE = "retrospective"
    TEACHING_EXAMPLE = "teaching_example"
    RHETORIC = "rhetoric"


class EvidenceKind(StrEnum):
    TRANSCRIPT = "transcript"
    AUDIO = "audio"
    VIDEO_FRAME = "video_frame"
    MARKET = "market"
    ANNOUNCEMENT = "announcement"
    FINANCIAL = "financial"
    NEWS = "news"


class EntityKind(StrEnum):
    SECURITY = "security"
    INDEX = "index"
    INDUSTRY = "industry"
    CONCEPT = "concept"
    COMPANY = "company"
    PERSON = "person"
    EVENT = "event"


class RuleRelation(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    REVISES = "revises"


class HypothesisClass(StrEnum):
    STABLE_CORE = "stable_core"
    PHASE_RULE = "phase_rule"
    PLAYBOOK_SPECIAL_CASE = "playbook_special_case"
    CANDIDATE = "candidate"


class TimeInterval(FrozenModel):
    """An interval keeps honest uncertainty instead of inventing a timestamp."""

    start: datetime | None = None
    end: datetime | None = None
    precision: Literal["timestamp", "session", "date", "unknown"]
    rationale: str

    @model_validator(mode="after")
    def ordered(self) -> TimeInterval:
        for value in (self.start, self.end):
            if value is not None and value.tzinfo is None:
                raise ValueError("time interval bounds must include a timezone")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("time interval end precedes start")
        return self


class TranscriptSpan(FrozenModel):
    """One sentence or a contiguous sentence range in the source transcript."""

    sentence_index: int = Field(ge=0)
    end_sentence_index: int | None = Field(default=None, ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    raw_text: str
    asr_revision: str | None = None
    revision_basis: str | None = None
    media_location: str | None = None

    @model_validator(mode="after")
    def ordered(self) -> TranscriptSpan:
        if self.end_ms < self.start_ms:
            raise ValueError("transcript span end precedes start")
        if (
            self.end_sentence_index is not None
            and self.end_sentence_index < self.sentence_index
        ):
            raise ValueError("transcript sentence range is reversed")
        if self.asr_revision and not self.revision_basis:
            raise ValueError("an ASR revision needs its evidence basis")
        return self


class RawEvidence(FrozenModel):
    """Literal source material; interpretation belongs in a separate record."""

    evidence_id: str
    kind: EvidenceKind
    source_ref: str
    content: str | None = None
    transcript_span: TranscriptSpan | None = None
    information_available_at: datetime | None = None

    @model_validator(mode="after")
    def transcript_has_span(self) -> RawEvidence:
        if self.kind is EvidenceKind.TRANSCRIPT and self.transcript_span is None:
            raise ValueError("transcript evidence needs a sentence span")
        if (
            self.kind is not EvidenceKind.TRANSCRIPT
            and self.transcript_span is not None
        ):
            raise ValueError("only transcript evidence may carry a sentence span")
        if (
            self.information_available_at is not None
            and self.information_available_at.tzinfo is None
        ):
            raise ValueError("evidence availability must include a timezone")
        return self


class EntityMention(FrozenModel):
    kind: EntityKind
    surface_text: str
    resolved_name: str | None = None
    resolved_identifier: str | None = None
    alternatives: tuple[str, ...] = ()
    rationale: str


class StatementObservation(FrozenModel):
    statement_id: str
    statement_type: StatementType
    evidence_ids: tuple[str, ...]
    tense: Literal["past", "present", "future", "conditional", "unclear"]
    condition: str | None = None
    action_direction: Literal[
        "buy", "add", "hold", "reduce", "sell", "avoid", "observe", "none"
    ] = "none"
    position_state: str | None = None
    market_queries: tuple[str, ...] = ()
    source_queries: tuple[str, ...] = ()


class Interpretation(FrozenModel):
    interpretation_id: str
    evidence_ids: tuple[str, ...]
    text: str
    strength: EvidenceStrength
    rationale: str
    alternative_explanations: tuple[str, ...] = ()


class RuleLink(FrozenModel):
    hypothesis_id: str
    relation: RuleRelation
    interpretation_ids: tuple[str, ...]
    explanation: str


class Ambiguity(FrozenModel):
    question: str
    alternatives: tuple[str, ...]
    trading_consequence: str


class VideoObservation(FrozenModel):
    """One video's facts and analysis, deliberately free of backtest parameters."""

    schema_version: Literal[1] = 1
    aweme_id: str
    title: str
    published_at: datetime
    duration_ms: int = Field(gt=0)
    transcript_status: Literal["available", "empty", "unavailable", "needs_media"]
    analysis_status: Literal[
        "selected", "in_progress", "analyzed", "blocked_on_media", "unavailable"
    ]
    recording_time_interval: TimeInterval
    referenced_market_intervals: tuple[TimeInterval, ...]
    earliest_action_at: datetime | None
    reconstruction_precision: ReconstructionPrecision
    evidence: tuple[RawEvidence, ...]
    entities: tuple[EntityMention, ...] = ()
    statements: tuple[StatementObservation, ...]
    interpretations: tuple[Interpretation, ...]
    rule_links: tuple[RuleLink, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()

    @model_validator(mode="after")
    def references_are_local_and_spans_fit(self) -> VideoObservation:
        if self.published_at.tzinfo is None:
            raise ValueError("publication time must include a timezone")
        if self.earliest_action_at is not None and (
            self.earliest_action_at.tzinfo is None
            or self.earliest_action_at < self.published_at
        ):
            raise ValueError(
                "earliest action must be timezone-aware and post-publication"
            )
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("duplicate evidence_id")
        for evidence_item in self.evidence:
            span = evidence_item.transcript_span
            if span is not None and span.end_ms > self.duration_ms:
                raise ValueError(
                    f"evidence {evidence_item.evidence_id} exceeds video duration"
                )
        for statement in self.statements:
            missing = set(statement.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"statement has unknown evidence: {sorted(missing)}")
        interpretation_ids = {item.interpretation_id for item in self.interpretations}
        if len(interpretation_ids) != len(self.interpretations):
            raise ValueError("duplicate interpretation_id")
        for interpretation in self.interpretations:
            missing = set(interpretation.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(
                    f"interpretation has unknown evidence: {sorted(missing)}"
                )
        for link in self.rule_links:
            missing = set(link.interpretation_ids) - interpretation_ids
            if missing:
                raise ValueError(
                    f"rule link has unknown interpretation: {sorted(missing)}"
                )
        return self


class HypothesisRevision(FrozenModel):
    schema_version: Literal[1] = 1
    hypothesis_id: str
    recorded_at: datetime
    revision_of: str | None = None
    rule_text: str
    conditions: tuple[str, ...]
    classification: HypothesisClass
    supporting_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    alternative_explanations: tuple[str, ...] = ()
    first_seen_at: datetime
    trading_consequence_if_wrong: str

    @model_validator(mode="after")
    def revision_times_are_aware(self) -> HypothesisRevision:
        if self.recorded_at.tzinfo is None or self.first_seen_at.tzinfo is None:
            raise ValueError("hypothesis times must include a timezone")
        if self.recorded_at < self.first_seen_at:
            raise ValueError("hypothesis cannot be recorded before first evidence")
        return self


class EvidenceRecord(FrozenModel):
    record_id: str
    source_kind: EvidenceKind
    source_ref: str
    information_available_at: datetime
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def available_time_is_aware(self) -> EvidenceRecord:
        if self.information_available_at.tzinfo is None:
            raise ValueError("evidence availability must include a timezone")
        return self


class EvidenceBundle(FrozenModel):
    """Physically separate evidence available by cutoff from later outcomes."""

    schema_version: Literal[1] = 1
    bundle_type: Literal["decision", "outcome"]
    case_id: str
    video_ids: tuple[str, ...]
    decision_cutoff: datetime
    earliest_action_at: datetime | None
    query: dict[str, JsonValue]
    records: tuple[EvidenceRecord, ...]
    omissions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def respects_cutoff(self) -> EvidenceBundle:
        if self.decision_cutoff.tzinfo is None:
            raise ValueError("decision cutoff must include a timezone")
        if self.earliest_action_at is not None and (
            self.earliest_action_at.tzinfo is None
            or self.earliest_action_at < self.decision_cutoff
        ):
            raise ValueError("earliest action must be timezone-aware and post-cutoff")
        for record in self.records:
            before_or_at = record.information_available_at <= self.decision_cutoff
            if self.bundle_type == "decision" and not before_or_at:
                raise ValueError(
                    f"future record in decision bundle: {record.record_id}"
                )
            if self.bundle_type == "outcome" and before_or_at:
                raise ValueError(
                    f"decision-time record in outcome bundle: {record.record_id}"
                )
        return self
