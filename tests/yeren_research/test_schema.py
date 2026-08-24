from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from scripts.yeren_research.schema import (
    EvidenceKind,
    EvidenceStrength,
    Interpretation,
    RawEvidence,
    ReconstructionPrecision,
    StatementObservation,
    StatementType,
    TimeInterval,
    TranscriptSpan,
    VideoObservation,
)

PUBLISHED = datetime.fromisoformat("2024-01-02T16:00:00+08:00")


def _observation() -> VideoObservation:
    evidence = RawEvidence(
        evidence_id="quote-1",
        kind=EvidenceKind.TRANSCRIPT,
        source_ref="data/yeren_corpus/transcripts/v1.json#sentence=0",
        transcript_span=TranscriptSpan(
            sentence_index=0,
            start_ms=100,
            end_ms=900,
            raw_text="今天先试错。",
        ),
        information_available_at=PUBLISHED,
    )
    return VideoObservation(
        aweme_id="v1",
        title="1月2日复盘",
        published_at=PUBLISHED,
        duration_ms=1_000,
        transcript_status="available",
        analysis_status="analyzed",
        recording_time_interval=TimeInterval(
            start=None,
            end=PUBLISHED,
            precision="date",
            rationale="Only the publication date and spoken reference are known.",
        ),
        referenced_market_intervals=(),
        earliest_action_at=datetime.fromisoformat("2024-01-03T09:30:00+08:00"),
        reconstruction_precision=ReconstructionPrecision.DAILY,
        evidence=(evidence,),
        statements=(
            StatementObservation(
                statement_id="statement-1",
                statement_type=StatementType.EXECUTED_ACTION,
                evidence_ids=("quote-1",),
                tense="past",
                action_direction="buy",
            ),
        ),
        interpretations=(
            Interpretation(
                interpretation_id="interpretation-1",
                evidence_ids=("quote-1",),
                text="The speaker describes a small initial position.",
                strength=EvidenceStrength.EXPLICIT,
                rationale="The action is stated in the past tense.",
            ),
        ),
    )


def test_observation_preserves_three_layers() -> None:
    observation = _observation()

    assert observation.evidence[0].transcript_span is not None
    assert observation.interpretations[0].evidence_ids == ("quote-1",)
    assert observation.rule_links == ()


def test_observation_rejects_dangling_evidence_reference() -> None:
    payload = _observation().model_dump()
    payload["statements"][0]["evidence_ids"] = ("missing",)

    with pytest.raises(ValidationError, match="unknown evidence"):
        VideoObservation.model_validate(payload)


def test_asr_revision_requires_basis() -> None:
    with pytest.raises(ValidationError, match="revision needs"):
        TranscriptSpan(
            sentence_index=0,
            start_ms=0,
            end_ms=1,
            raw_text="错词",
            asr_revision="术语",
        )


def test_transcript_sentence_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="sentence range is reversed"):
        TranscriptSpan(
            sentence_index=2,
            end_sentence_index=1,
            start_ms=0,
            end_ms=1,
            raw_text="跨句段",
        )


def test_non_transcript_evidence_cannot_carry_sentence_span() -> None:
    with pytest.raises(ValidationError, match="only transcript"):
        RawEvidence(
            evidence_id="market-1",
            kind=EvidenceKind.MARKET,
            source_ref="snapshot:1",
            transcript_span=TranscriptSpan(
                sentence_index=0,
                start_ms=0,
                end_ms=1,
                raw_text="不是行情字段",
            ),
        )


def test_observation_rejects_action_before_publication() -> None:
    payload = _observation().model_dump()
    payload["earliest_action_at"] = datetime.fromisoformat("2024-01-02T15:59:59+08:00")

    with pytest.raises(ValidationError, match="post-publication"):
        VideoObservation.model_validate(payload)


def test_time_interval_rejects_naive_bounds() -> None:
    with pytest.raises(ValidationError, match="include a timezone"):
        TimeInterval(
            start=datetime(2024, 1, 1),
            end=None,
            precision="date",
            rationale="A naive date would make as-of comparison ambiguous.",
        )
