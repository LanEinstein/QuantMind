"""SOP output schema invariants (Y-002) — sourcing-only, no decision fields."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.theme_research.sop_schema import (
    THEME_SOP_STEPS,
    ChokePointFinding,
    SourceCitation,
    ThemeCandidate,
    ThemeResearchOutput,
    ThemeStep,
)

_SHA = "a" * 64


def _citation() -> SourceCitation:
    return SourceCitation(source_domain="www.gov.cn", snippet_sha256=_SHA)


def _candidate(code: str = "600519") -> ThemeCandidate:
    return ThemeCandidate(
        code=code,
        sector="白酒",
        chain_link="终端品牌",
        rationale="示范",
        confidence=0.7,
        citations=(_citation(),),
    )


def _output(**over: object) -> ThemeResearchOutput:
    base: dict[str, object] = dict(
        trend_direction="国产替代加速",
        beneficiary_sectors=("半导体设备",),
        chain_links=("光刻机",),
        chokepoints=(
            ChokePointFinding(
                chain_link="光刻机",
                rationale="替代难度极高",
                confidence=0.8,
                citations=(_citation(),),
            ),
        ),
        candidates=(_candidate(),),
        overall_confidence=0.6,
        trend_citations=(_citation(),),
    )
    base.update(over)
    return ThemeResearchOutput(**base)  # type: ignore[arg-type]


def test_valid_output_constructs() -> None:
    out = _output()
    assert out.beneficiary_sectors == ("半导体设备",)
    assert out.candidates[0].code == "600519"


def test_steps_are_the_frozen_five() -> None:
    assert tuple(s.value for s in THEME_SOP_STEPS) == (
        "direction",
        "sectors",
        "chain",
        "chokepoint",
        "tickers",
    )
    assert len(THEME_SOP_STEPS) == 5
    assert set(THEME_SOP_STEPS) == set(ThemeStep)


def test_null_result_must_be_empty() -> None:
    with pytest.raises(ValidationError, match="null_result"):
        _output(null_result=True)  # candidates non-empty -> reject


def test_null_result_with_empty_candidates_ok() -> None:
    out = _output(null_result=True, candidates=(), chokepoints=())
    assert out.null_result is True
    assert out.candidates == ()


def test_non_null_requires_sector() -> None:
    with pytest.raises(ValidationError, match="beneficiary sector"):
        _output(beneficiary_sectors=())


def test_duplicate_codes_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate candidate code"):
        _output(candidates=(_candidate("600519"), _candidate("600519")))


def test_bad_code_rejected() -> None:
    with pytest.raises(ValidationError, match="6-digit"):
        _candidate("6005AB")  # 6 chars but not all digits
    with pytest.raises(ValidationError):
        _candidate("60051")  # too short


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _output(overall_confidence=1.5)


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        ThemeResearchOutput(
            trend_direction="x",
            beneficiary_sectors=("s",),
            overall_confidence=0.5,
            side="BUY",  # type: ignore[call-arg]
        )


def test_candidate_has_no_decision_fields() -> None:
    """By construction the LLM cannot express a trade — no side/volume/price."""
    fields = set(ThemeCandidate.model_fields)
    assert fields.isdisjoint({"side", "volume", "limit_price", "price", "quantity"})


def test_bad_snippet_hash_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceCitation(source_domain="www.gov.cn", snippet_sha256="zz")


def test_candidate_without_citation_rejected() -> None:
    """A non-null candidate must cite a byte-pinned source (no uncited pick)."""
    uncited = ThemeCandidate(
        code="600519",
        sector="白酒",
        chain_link="终端品牌",
        rationale="示范",
        confidence=0.7,
    )
    with pytest.raises(ValidationError, match="must cite"):
        _output(candidates=(uncited,))


def test_non_null_without_trend_citation_rejected() -> None:
    with pytest.raises(ValidationError, match="cite the trend"):
        _output(trend_citations=())


def test_chokepoint_without_citation_rejected() -> None:
    bare = ChokePointFinding(chain_link="光刻机", rationale="难", confidence=0.8)
    with pytest.raises(ValidationError, match="chokepoint"):
        _output(chokepoints=(bare,))


def test_null_result_skips_citation_requirement() -> None:
    out = _output(
        null_result=True, candidates=(), chokepoints=(), trend_citations=()
    )
    assert out.null_result is True
