"""AC-001 — deterministic StyleClassifier unit tests."""

from __future__ import annotations

import math

import pytest

from backend.style import (
    STYLE_FEATURE_CODE_VERSION,
    StyleClassifierConfig,
    StyleInputs,
    StyleTag,
    classify_style,
)


def _short_term_inputs() -> StyleInputs:
    return StyleInputs(
        momentum_20d=0.12,
        volatility_20d=0.03,
        ma_ratio_5_20=1.05,
        value_score=None,
        thesis_derivable=True,
    )


class TestClassifyStyle:
    def test_no_value_score_is_short_term(self) -> None:
        """Pre-AC-003: no value_score → SHORT_TERM (legacy bit-identical path)."""
        result = classify_style(_short_term_inputs())
        assert result.style is StyleTag.SHORT_TERM
        assert result.value_score is None
        assert result.feature_code_version == STYLE_FEATURE_CODE_VERSION

    def test_value_score_clears_gate_is_value(self) -> None:
        result = classify_style(
            StyleInputs(value_score=0.72, thesis_derivable=True),
            StyleClassifierConfig(value_gate=0.60),
        )
        assert result.style is StyleTag.VALUE
        assert result.value_score == 0.72
        assert "value" in result.reason

    def test_value_score_below_gate_is_short_term(self) -> None:
        result = classify_style(
            StyleInputs(value_score=0.40, thesis_derivable=True),
            StyleClassifierConfig(value_gate=0.60),
        )
        assert result.style is StyleTag.SHORT_TERM
        assert "<gate" in result.reason

    def test_value_score_at_gate_is_value(self) -> None:
        """The gate is inclusive (>=)."""
        result = classify_style(
            StyleInputs(value_score=0.60, thesis_derivable=True),
            StyleClassifierConfig(value_gate=0.60),
        )
        assert result.style is StyleTag.VALUE

    def test_high_score_without_thesis_is_short_term(self) -> None:
        """A value name must have a derivable thesis — fail-closed otherwise."""
        result = classify_style(
            StyleInputs(value_score=0.90, thesis_derivable=False),
            StyleClassifierConfig(value_gate=0.60),
        )
        assert result.style is StyleTag.SHORT_TERM
        assert "no derivable thesis" in result.reason

    @pytest.mark.parametrize("dirty", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_value_score_is_short_term(self, dirty: float) -> None:
        """Never fabricate VALUE from a dirty number (fail-closed, never raises)."""
        result = classify_style(StyleInputs(value_score=dirty, thesis_derivable=True))
        assert result.style is StyleTag.SHORT_TERM
        assert result.value_score is None

    def test_deterministic_replay_bit_identical(self) -> None:
        """Same inputs + config → identical classification (replay)."""
        inputs = StyleInputs(value_score=0.81, thesis_derivable=True)
        cfg = StyleClassifierConfig(value_gate=0.55)
        a = classify_style(inputs, cfg)
        b = classify_style(inputs, cfg)
        assert a == b

    def test_reason_is_replay_stable_no_wallclock(self) -> None:
        """The reason string is fixed-precision, deterministic — no timestamps."""
        result = classify_style(_short_term_inputs())
        assert result.reason == classify_style(_short_term_inputs()).reason
        # fixed 4-dp formatting, no locale/float drift
        assert "mom=0.1200" in result.reason

    def test_gate_sensitivity(self) -> None:
        """A stricter gate can flip a borderline name to SHORT_TERM."""
        inputs = StyleInputs(value_score=0.65, thesis_derivable=True)
        assert (
            classify_style(inputs, StyleClassifierConfig(0.60)).style is StyleTag.VALUE
        )
        assert (
            classify_style(inputs, StyleClassifierConfig(0.70)).style
            is StyleTag.SHORT_TERM
        )

    def test_style_tag_string_values(self) -> None:
        assert StyleTag.SHORT_TERM.value == "short_term"
        assert StyleTag.VALUE.value == "value"

    def test_classification_is_total_never_raises(self) -> None:
        """Every combination of dirty inputs returns a verdict (no exception)."""
        for vs in (None, 0.0, -1.0, math.nan, 1e9):
            for td in (True, False):
                out = classify_style(StyleInputs(value_score=vs, thesis_derivable=td))
                assert out.style in (StyleTag.SHORT_TERM, StyleTag.VALUE)
