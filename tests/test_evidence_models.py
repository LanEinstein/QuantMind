"""Tests for backend/models/evidence.py — evidence_id 5-prefix enforcement.

Locks P0-8 §1.6.2 red line: evidence_id must start with one of
NEWS- / MIROFISH- / MARKET- / RISK- / DEBATE-. New prefixes need an
amendment; arbitrary prefixes are red-line violations.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.evidence import (
    EVIDENCE_ID_PATTERN,
    EVIDENCE_PREFIXES,
    EvidenceId,
    EvidencePrefix,
    parse_evidence_prefix,
    validate_evidence_id,
)


class TestEvidencePrefix:
    def test_five_prefixes_locked(self) -> None:
        assert {p.value for p in EvidencePrefix} == {
            "NEWS",
            "MIROFISH",
            "MARKET",
            "RISK",
            "DEBATE",
        }

    def test_prefix_tuple_matches_enum(self) -> None:
        assert set(EVIDENCE_PREFIXES) == {p.value for p in EvidencePrefix}


class TestValidateEvidenceId:
    @pytest.mark.parametrize(
        "evidence_id",
        [
            "NEWS-abc123",
            "MIROFISH-run_2026-05-12_001",
            "MARKET-600519-2026-05-12T09:30:00",
            "RISK-QM-20260512-093001-600519-BUY-001",
            "DEBATE-run123-r3",
        ],
    )
    def test_valid_examples(self, evidence_id: str) -> None:
        validate_evidence_id(evidence_id)  # must not raise

    @pytest.mark.parametrize(
        "bad",
        [
            "FOO-anything",  # unknown prefix
            "news-lowercase",  # lowercase prefix
            "NEWS_underscore",  # underscore separator (must be dash)
            "NEWS-",  # empty suffix
            "NEWS",  # no separator/suffix
            "",  # empty
            "NEWS-" + "a" * 200,  # too long
            "NEWS- whitespace",  # whitespace
            "NEWS-中文",  # non-ascii
        ],
    )
    def test_invalid_examples(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_evidence_id(bad)


class TestParseEvidencePrefix:
    def test_parses_each_prefix(self) -> None:
        assert parse_evidence_prefix("NEWS-abc") == EvidencePrefix.NEWS
        assert parse_evidence_prefix("MIROFISH-x") == EvidencePrefix.MIROFISH
        assert parse_evidence_prefix("MARKET-y") == EvidencePrefix.MARKET
        assert parse_evidence_prefix("RISK-z") == EvidencePrefix.RISK
        assert parse_evidence_prefix("DEBATE-w") == EvidencePrefix.DEBATE

    def test_invalid_prefix_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_evidence_prefix("FOO-x")


class TestEvidenceIdModel:
    def test_frozen_and_validated(self) -> None:
        ev = EvidenceId(value="NEWS-abc")
        assert ev.value == "NEWS-abc"
        assert ev.prefix is EvidencePrefix.NEWS
        with pytest.raises(ValidationError):
            ev.value = "MARKET-x"  # type: ignore[misc]

    def test_invalid_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceId(value="BOGUS-x")

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceId(value="NEWS-abc", surprise="x")  # type: ignore[call-arg]


class TestEvidencePattern:
    def test_pattern_documented(self) -> None:
        # The exported regex string is the single source of truth for both
        # backend Pydantic models and the frontend JS regex mirror (P1-5
        # §2 red line 5 / B-003 acceptance).
        assert EVIDENCE_ID_PATTERN.startswith("^(NEWS|MIROFISH|MARKET|RISK|DEBATE)-")
