"""CandidateSelector peer-sourcing merge invariants (Y-004).

Theme peer-sourced codes reserve at most ``final − min_quant_slots`` (≤2) slots,
≥ ``min_quant_slots`` quant names always survive, pure-quant is never evicted by
theme, and with no peer-sourced codes the result is bit-identical to pure-quant.
"""

from __future__ import annotations

from backend.candidate_selector.selector import (
    AdvisorySignal,
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)

_CONFIG = SelectorConfig(
    version="test/v1",
    final_shortlist_size=5,
    min_quant_slots=3,
    max_percentile_shift=0.01,
    advisory_weight=1.0,
    feature_def_hash="f" * 64,
)


def _quant(*codes: str) -> list[QuantCandidate]:
    # descending scores so order == argument order
    return [QuantCandidate(code=c, score=100.0 - i) for i, c in enumerate(codes)]


def _sel() -> CandidateSelector:
    return CandidateSelector(_CONFIG)


def test_no_peer_is_bit_identical_to_pure_quant() -> None:
    quant = _quant("000001", "000002", "000003", "000004", "000005", "000006")
    base = _sel().select(quant)
    withp = _sel().select(quant, peer_sourced=None)
    assert base.shortlist == withp.shortlist
    assert withp.peer_sourced == ()


def test_theme_quota_capped_at_two() -> None:
    quant = _quant("000001", "000002", "000003", "000004", "000005")
    sel = _sel().select(quant, peer_sourced=["900001", "900002", "900003", "900004"])
    assert len(sel.peer_sourced) == 2  # final(5) - min_quant(3)
    assert len(sel.shortlist) == 5


def test_at_least_three_quant_survive() -> None:
    quant = _quant("000001", "000002", "000003", "000004", "000005")
    sel = _sel().select(quant, peer_sourced=["900001", "900002"])
    quant_in = [c for c in sel.shortlist if c.startswith("0000")]
    assert len(quant_in) >= 3
    # the top-3 quant favorites are all present (never evicted by theme)
    assert {"000001", "000002", "000003"}.issubset(set(sel.shortlist))


def test_pure_quant_top_names_never_evicted() -> None:
    quant = _quant("000001", "000002", "000003")
    sel = _sel().select(quant, peer_sourced=["900001", "900002"])
    # only 3 quant -> all kept; theme fills the 2 remaining slots
    assert set(sel.shortlist) == {"000001", "000002", "000003", "900001", "900002"}
    assert sel.peer_sourced == ("900001", "900002")


def test_peer_overlap_with_quant_not_double_counted() -> None:
    quant = _quant("000001", "000002", "000003", "000004", "000005")
    # 000004 is already a quant name -> not counted as theme
    sel = _sel().select(quant, peer_sourced=["000004", "900001"])
    assert sel.peer_sourced == ("900001",)  # only the genuinely new theme code


def test_empty_peer_list_changes_nothing() -> None:
    quant = _quant("000001", "000002", "000003", "000004", "000005")
    assert _sel().select(quant, peer_sourced=[]).peer_sourced == ()


def test_theme_only_when_no_quant() -> None:
    # degenerate: no quant qualified, theme still bounded by quota
    sel = _sel().select([], peer_sourced=["900001", "900002", "900003"])
    assert len(sel.peer_sourced) <= 2
    assert all(c.startswith("9000") for c in sel.shortlist)


def test_advisory_not_applied_on_empty_quant() -> None:
    # advisory cannot have applied when there was no quant to re-rank
    sel = _sel().select(
        [], advisory=[AdvisorySignal(code="X", advisory_score=1.0)],
        peer_sourced=["900001"],
    )
    assert sel.advisory_applied is False


def test_shortlist_never_exceeds_cap_even_with_bad_config() -> None:
    """Defense-in-depth: a directly-built config with min_quant > final must not
    overflow the shortlist past final_shortlist_size."""
    bad = SelectorConfig(
        version="bad/v1",
        final_shortlist_size=3,
        min_quant_slots=5,  # > final — loader would reject, but guard anyway
        max_percentile_shift=0.01,
        advisory_weight=1.0,
        feature_def_hash="f" * 64,
    )
    quant = _quant("000001", "000002", "000003", "000004", "000005")
    sel = CandidateSelector(bad).select(quant, peer_sourced=["900001", "900002"])
    assert len(sel.shortlist) <= 3
