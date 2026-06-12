"""AC-005 — constrained value-slot allocation (value ≤2 / quant ≥3)."""

from __future__ import annotations

from backend.candidate_selector.selector import (
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)


def _config(final: int = 5, min_quant: int = 3) -> SelectorConfig:
    return SelectorConfig(
        version="ac5/v1",
        final_shortlist_size=final,
        min_quant_slots=min_quant,
        max_percentile_shift=0.01,
        advisory_weight=0.0,
        feature_def_hash="h",
    )


def _quant(*codes_scores: tuple[str, float]) -> list[QuantCandidate]:
    return [QuantCandidate(code=c, score=s) for c, s in codes_scores]


class TestBitIdenticalWhenNoValue:
    def test_none_value_scores_matches_legacy(self) -> None:
        sel = CandidateSelector(_config())
        quant = _quant(
            ("600001", 0.9), ("600002", 0.8), ("600003", 0.7),
            ("600004", 0.6), ("600005", 0.5), ("600006", 0.4),
        )
        legacy = sel.select(quant)
        with_none = sel.select(quant, value_scores=None)
        assert legacy.shortlist == with_none.shortlist
        assert with_none.value_selected == ()


class TestValueSlotAllocation:
    def test_value_names_fill_open_slots_by_value_score(self) -> None:
        sel = CandidateSelector(_config(final=5, min_quant=3))
        # 600001-3 are the top quant (reserved). 600004/5/6 compete for 2 open
        # slots. 600006 has the highest value score → it wins an open slot over
        # the higher-5-factor 600004 which is not VALUE-style.
        quant = _quant(
            ("600001", 0.95), ("600002", 0.90), ("600003", 0.85),
            ("600004", 0.80), ("600005", 0.50), ("600006", 0.40),
        )
        value_scores = {"600005": 0.72, "600006": 0.81}  # both VALUE
        result = sel.select(quant, value_scores=value_scores, value_gate=0.60)
        # reserved 3 quant present
        assert set(result.quant_reserved) == {"600001", "600002", "600003"}
        # value slots = the 2 VALUE names ordered by value score desc
        assert result.value_selected == ("600006", "600005")
        # 600004 (higher 5-factor but not VALUE) is squeezed out of the 2 open
        assert "600004" not in result.shortlist
        assert set(result.shortlist) == {
            "600001", "600002", "600003", "600006", "600005",
        }

    def test_reserved_quant_never_evicted_by_value(self) -> None:
        """Adversarial: a sky-high value score cannot displace a reserved quant."""
        sel = CandidateSelector(_config(final=5, min_quant=3))
        quant = _quant(
            ("600001", 0.95), ("600002", 0.90), ("600003", 0.85),
            ("600004", 0.10), ("600005", 0.05),
        )
        # 600004/5 have huge value scores but the top-3 quant are protected.
        result = sel.select(
            quant, value_scores={"600004": 0.99, "600005": 0.99}, value_gate=0.60
        )
        assert {"600001", "600002", "600003"} <= set(result.shortlist)
        assert result.quant_reserved == ("600001", "600002", "600003")

    def test_no_value_names_falls_back_to_5factor(self) -> None:
        sel = CandidateSelector(_config(final=5, min_quant=3))
        quant = _quant(
            ("600001", 0.95), ("600002", 0.90), ("600003", 0.85),
            ("600004", 0.80), ("600005", 0.50),
        )
        # value scores all below gate → no VALUE names → 5-factor fill.
        result = sel.select(
            quant, value_scores={"600004": 0.1, "600005": 0.1}, value_gate=0.60
        )
        assert result.value_selected == ()
        # open slots filled by next 5-factor: 600004, 600005
        assert set(result.shortlist) == {
            "600001", "600002", "600003", "600004", "600005",
        }

    def test_value_ordering_within_slots(self) -> None:
        sel = CandidateSelector(_config(final=5, min_quant=3))
        quant = _quant(
            ("600001", 0.95), ("600002", 0.90), ("600003", 0.85),
            ("600004", 0.70), ("600005", 0.65),
        )
        result = sel.select(
            quant, value_scores={"600004": 0.65, "600005": 0.88}, value_gate=0.60
        )
        # value-selected ordered by value score desc: 600005 (0.88) before 600004
        assert result.value_selected == ("600005", "600004")

    def test_deterministic_replay(self) -> None:
        sel = CandidateSelector(_config())
        quant = _quant(
            ("600001", 0.9), ("600002", 0.8), ("600003", 0.7), ("600004", 0.6)
        )
        vs = {"600004": 0.9}
        assert sel.select(quant, value_scores=vs) == sel.select(quant, value_scores=vs)

    def test_dirty_value_score_ignored(self) -> None:
        sel = CandidateSelector(_config(final=5, min_quant=3))
        quant = _quant(
            ("600001", 0.95), ("600002", 0.90), ("600003", 0.85),
            ("600004", 0.80), ("600005", 0.50),
        )
        result = sel.select(
            quant, value_scores={"600005": float("nan")}, value_gate=0.60
        )
        # nan score → not VALUE → 5-factor fallback
        assert result.value_selected == ()
