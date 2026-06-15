"""AE-004 Lane-2 golden-vector decision oracle — fixed-point compare."""

from __future__ import annotations

from backend.backtest.golden_vector import (
    DecisionVector,
    verify_decision_vectors,
)


def test_exact_match() -> None:
    g = [
        DecisionVector(
            trade_date="20260102",
            shortlist=("600000", "600001"),
            buy_codes=("600000",),
        )
    ]
    p = [
        DecisionVector(
            trade_date="20260102",
            shortlist=("600000", "600001"),
            buy_codes=("600000",),
        )
    ]
    result = verify_decision_vectors(p, g)
    assert result.matched
    assert result.divergences == ()


def test_shortlist_mismatch() -> None:
    g = [DecisionVector(trade_date="20260102", shortlist=("600000",))]
    p = [DecisionVector(trade_date="20260102", shortlist=("600001",))]
    result = verify_decision_vectors(p, g)
    assert not result.matched
    assert any(d.field_name == "shortlist" for d in result.divergences)


def test_length_mismatch() -> None:
    g = [DecisionVector(trade_date="20260102")]
    result = verify_decision_vectors([], g)
    assert not result.matched
    assert result.divergences[0].field_name == "length"


def test_date_misalignment() -> None:
    g = [DecisionVector(trade_date="20260102")]
    p = [DecisionVector(trade_date="20260103")]
    result = verify_decision_vectors(p, g)
    assert not result.matched
    assert any(d.field_name == "trade_date" for d in result.divergences)


def test_score_subulp_difference_passes() -> None:
    # 0.1 + 0.2 != 0.3 in float, but they quantise equal at 1e-9.
    g = [DecisionVector(trade_date="20260102", scores={"600000": 0.3})]
    p = [DecisionVector(trade_date="20260102", scores={"600000": 0.1 + 0.2})]
    assert verify_decision_vectors(p, g).matched


def test_real_score_difference_diverges() -> None:
    g = [DecisionVector(trade_date="20260102", scores={"600000": 0.50})]
    p = [DecisionVector(trade_date="20260102", scores={"600000": 0.55})]
    result = verify_decision_vectors(p, g)
    assert not result.matched
    assert any("score[600000]" == d.field_name for d in result.divergences)


def test_missing_score_diverges() -> None:
    g = [DecisionVector(trade_date="20260102", scores={"600000": 0.5})]
    p = [DecisionVector(trade_date="20260102", scores={})]
    result = verify_decision_vectors(p, g)
    assert not result.matched
    assert any(d.produced == "<missing>" for d in result.divergences)
