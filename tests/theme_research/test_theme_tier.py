"""AC-004 — theme four-tier weighting + schema compat + pin-artifact tier."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.theme_research.candidate_artifact import (
    THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION,
    ThemeCandidateArtifact,
)
from backend.theme_research.sop_schema import (
    THEME_SOP_SCHEMA_VERSION,
    SourceCitation,
    ThemeCandidate,
    ThemeResearchOutput,
    ThemeTier,
)
from backend.theme_research.tier_weights import (
    DEFAULT_THEME_TIER_WEIGHTS,
    ThemeTierWeights,
    theme_tier_weight,
)

_HASH = "a" * 64
_T = datetime(2026, 6, 12, tzinfo=UTC)


def _cite() -> SourceCitation:
    return SourceCitation(source_domain="www.gov.cn", snippet_sha256="b" * 64)


def _output(tier: ThemeTier | None = None, **kw: object) -> ThemeResearchOutput:
    extra = {"theme_tier": tier} if tier is not None else {}
    return ThemeResearchOutput(
        trend_direction="半导体国产替代",
        beneficiary_sectors=("半导体",),
        chain_links=("光刻机",),
        candidates=(
            ThemeCandidate(
                code="600519", sector="半导体", chain_link="光刻机",
                rationale="x", confidence=0.8, citations=(_cite(),),
            ),
        ),
        overall_confidence=0.7,
        trend_citations=(_cite(),),
        **extra,  # type: ignore[arg-type]
    )


class TestThemeTier:
    def test_ordering_high_to_low(self) -> None:
        assert (
            ThemeTier.NATIONAL_EVENT
            < ThemeTier.POLICY
            < ThemeTier.TECH
            < ThemeTier.STOCK
        )

    def test_int_values(self) -> None:
        assert int(ThemeTier.NATIONAL_EVENT) == 1
        assert int(ThemeTier.STOCK) == 4


class TestTierWeights:
    def test_default_weights(self) -> None:
        assert theme_tier_weight(ThemeTier.NATIONAL_EVENT) == 1.0
        assert theme_tier_weight(ThemeTier.POLICY) == 0.75
        assert theme_tier_weight(ThemeTier.TECH) == 0.5
        assert theme_tier_weight(ThemeTier.STOCK) == 0.25

    def test_default_map_matches(self) -> None:
        for tier, w in DEFAULT_THEME_TIER_WEIGHTS.items():
            assert theme_tier_weight(tier) == w

    def test_higher_tier_weighs_at_least_as_much(self) -> None:
        w = ThemeTierWeights()
        assert w.national_event >= w.policy >= w.tech >= w.stock

    def test_order_inversion_rejected(self) -> None:
        """The monotone clamp forbids a lower tier out-weighing a higher one."""
        with pytest.raises(ValueError, match="monotone non-increasing"):
            ThemeTierWeights(national_event=0.5, policy=0.9)

    def test_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            ThemeTierWeights(national_event=1.5)

    def test_custom_valid_weights(self) -> None:
        w = ThemeTierWeights(national_event=0.9, policy=0.6, tech=0.3, stock=0.1)
        assert theme_tier_weight(ThemeTier.TECH, w) == 0.3


class TestSchemaCompat:
    def test_schema_version_is_2(self) -> None:
        assert THEME_SOP_SCHEMA_VERSION == 2

    def test_v1_output_without_tier_defaults_conservative(self) -> None:
        """A v1 output (schema_version=1, no tier) reads → defaults to STOCK."""
        out = ThemeResearchOutput(
            schema_version=1,
            trend_direction="x",
            beneficiary_sectors=("s",),
            candidates=(),
            overall_confidence=0.5,
            null_result=True,
        )
        assert out.theme_tier is ThemeTier.STOCK

    def test_llm_suggested_tier_carried(self) -> None:
        out = _output(ThemeTier.NATIONAL_EVENT)
        assert out.theme_tier is ThemeTier.NATIONAL_EVENT

    def test_future_schema_version_fails_closed(self) -> None:
        with pytest.raises(ValidationError, match="out of supported range"):
            ThemeResearchOutput(
                schema_version=99,
                trend_direction="x",
                beneficiary_sectors=("s",),
                candidates=(),
                overall_confidence=0.5,
                null_result=True,
            )


class TestArtifactTier:
    def test_artifact_default_tier_is_stock(self) -> None:
        art = ThemeCandidateArtifact.from_output(
            run_id="r", prompt_version_hash=_HASH, output=_output(),
            source_promotable=True, created_at=_T,
        )
        assert art.theme_tier is ThemeTier.STOCK
        assert art.schema_version == THEME_CANDIDATE_ARTIFACT_SCHEMA_VERSION

    def test_artifact_carries_llm_suggested_tier(self) -> None:
        art = ThemeCandidateArtifact.from_output(
            run_id="r", prompt_version_hash=_HASH,
            output=_output(ThemeTier.POLICY),
            source_promotable=True, created_at=_T,
        )
        assert art.theme_tier is ThemeTier.POLICY

    def test_human_pin_overrides_tier(self) -> None:
        """Human confirms/overrides the tier at pin (it's a pin attribute)."""
        art = ThemeCandidateArtifact.from_output(
            run_id="r", prompt_version_hash=_HASH,
            output=_output(ThemeTier.STOCK),
            source_promotable=True, created_at=_T,
            theme_tier=ThemeTier.NATIONAL_EVENT,
        )
        assert art.theme_tier is ThemeTier.NATIONAL_EVENT

    def test_tier_is_bound_into_digest(self) -> None:
        """Two artifacts identical but for the tier hash differently."""
        a = ThemeCandidateArtifact.from_output(
            run_id="r", prompt_version_hash=_HASH, output=_output(),
            source_promotable=True, created_at=_T, theme_tier=ThemeTier.NATIONAL_EVENT,
        )
        b = ThemeCandidateArtifact.from_output(
            run_id="r", prompt_version_hash=_HASH, output=_output(),
            source_promotable=True, created_at=_T, theme_tier=ThemeTier.STOCK,
        )
        assert a.content_sha256 != b.content_sha256
