"""CandidateSelector unit + adversarial tests (Phase M-001).

Covers the four CLAUDE.md invariants: pure-quant qualification, bounded
advisory re-rank, ≥ min_quant_slots survival, advisory-absent fallback —
plus determinism, tie-breaking, and config loading/validation.
"""

from __future__ import annotations

import math

import pytest

from backend.candidate_selector.selector import (
    AdvisorySignal,
    CandidateSelector,
    CandidateSelectorError,
    QuantCandidate,
    SelectorConfig,
    load_selector_config,
)

# A 5/3/0.01 config with a generous advisory_weight so the bounded-rerank
# machinery is exercised; max_shift = max(1, round(n * 0.01)).
CFG = SelectorConfig(
    version="test/v1",
    final_shortlist_size=5,
    min_quant_slots=3,
    max_percentile_shift=0.01,
    advisory_weight=1.0,
    feature_def_hash="deadbeef",
)


def _candidates(n: int) -> list[QuantCandidate]:
    """n candidates with strictly descending scores; codes 600001..6000NN."""
    return [
        QuantCandidate(code=f"6000{i:02d}", score=float(n - i))
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# Determinism + pure-quant ordering
# --------------------------------------------------------------------------


def test_deterministic_repeated_calls_equal() -> None:
    sel = CandidateSelector(CFG)
    cands = _candidates(20)
    a = sel.select(cands)
    b = sel.select(cands)
    assert a == b


def test_pure_quant_fallback_is_top_n_by_score() -> None:
    sel = CandidateSelector(CFG)
    cands = _candidates(20)
    result = sel.select(cands, advisory=None)
    # Top 5 by score = the 5 highest-score codes in descending order.
    assert result.shortlist == ("600000", "600001", "600002", "600003", "600004")
    assert result.advisory_applied is False
    assert result.qualified == tuple(c.code for c in cands)  # ranked, all members


def test_tie_break_is_code_ascending() -> None:
    sel = CandidateSelector(CFG)
    cands = [
        QuantCandidate(code="600003", score=1.0),
        QuantCandidate(code="600001", score=1.0),
        QuantCandidate(code="600002", score=1.0),
    ]
    result = sel.select(cands)
    assert result.shortlist == ("600001", "600002", "600003")


# --------------------------------------------------------------------------
# Red line 4 — advisory absent / removed → qualified set invariant (adversarial)
# --------------------------------------------------------------------------


def test_removing_advisory_leaves_qualified_set_unchanged() -> None:
    """Adversarial: advisory may change ORDER, never qualified-set membership."""
    sel = CandidateSelector(CFG)
    cands = _candidates(50)
    # An advisory that bullishly nudges a mid-rank code up by the max shift.
    advisory = [AdvisorySignal(code="600010", advisory_score=1.0)]

    with_adv = sel.select(cands, advisory=advisory)
    without_adv = sel.select(cands, advisory=None)

    # Qualified set (membership) identical regardless of advisory.
    assert set(with_adv.qualified) == set(without_adv.qualified)
    assert with_adv.qualified == without_adv.qualified  # same ranked order too


def test_advisory_cannot_add_or_remove_a_qualified_member() -> None:
    sel = CandidateSelector(CFG)
    cands = _candidates(30)
    # A signal referencing a code NOT in the candidate set must be ignored
    # (advisory never adds a member).
    advisory = [AdvisorySignal(code="999999", advisory_score=5.0)]
    result = sel.select(cands, advisory=advisory)
    assert "999999" not in result.qualified
    assert "999999" not in result.shortlist
    assert set(result.qualified) == {c.code for c in cands}


# --------------------------------------------------------------------------
# Red line 3 — ≥ min_quant_slots quant names survive truncation
# --------------------------------------------------------------------------


# A config with a wide shift so the re-rank can actually reorder + so the
# ≥min_quant_slots reservation has to rescue an evicted quant favorite.
# max_shift = max(1, round(5 * 1.0)) = 5; final_shortlist_size 3 < n.
CFG_WIDE = SelectorConfig(
    version="test/wide",
    final_shortlist_size=3,
    min_quant_slots=2,
    max_percentile_shift=1.0,
    advisory_weight=1.0,
    feature_def_hash="cafe",
)


def test_reservation_rescues_evicted_quant_favorites() -> None:
    """Even when advisory shoves the top quant names below the final cut, the
    ≥min_quant_slots reservation forces them back into the shortlist."""
    sel = CandidateSelector(CFG_WIDE)
    cands = _candidates(5)  # 600000(top)..600004(bottom)
    # Push the top-2 down and the bottom-3 up so the naive top-3 would be the
    # three lowest quant names.
    advisory = [
        AdvisorySignal("600000", -5.0),
        AdvisorySignal("600001", -5.0),
        AdvisorySignal("600002", 5.0),
        AdvisorySignal("600003", 5.0),
        AdvisorySignal("600004", 5.0),
    ]
    result = sel.select(cands, advisory=advisory)
    # min_quant_slots=2 → 600000 + 600001 must be present despite the re-rank.
    assert {"600000", "600001"} <= set(result.shortlist)
    assert result.quant_reserved == ("600000", "600001")
    assert set(result.qualified) == {c.code for c in cands}  # membership intact


def test_quant_reserved_count_matches_min_slots() -> None:
    sel = CandidateSelector(CFG)
    result = sel.select(_candidates(10))
    assert result.quant_reserved == ("600000", "600001", "600002")


# --------------------------------------------------------------------------
# Red line 2 — bounded re-rank (≤1 percentile); over-displacement → fallback
# --------------------------------------------------------------------------


def test_bounded_rerank_drops_advisory_on_over_displacement() -> None:
    """A re-rank that would displace a code beyond max_shift is dropped
    wholesale (fail-closed) and the pure-quant order stands.

    With n=4 + max_percentile_shift=0.5, max_shift=2; pushing the bottom code
    up by 2 while pushing the top three down by 2 would land it 3 positions
    away — over the bound — so the advisory is discarded entirely.
    """
    cfg = SelectorConfig(
        version="t",
        final_shortlist_size=4,
        min_quant_slots=1,
        max_percentile_shift=0.5,
        advisory_weight=1.0,
        feature_def_hash="x",
    )
    sel = CandidateSelector(cfg)
    cands = _candidates(4)  # max_shift = max(1, round(4*0.5)) = 2
    advisory = [
        AdvisorySignal("600003", 2.0),
        AdvisorySignal("600000", -2.0),
        AdvisorySignal("600001", -2.0),
        AdvisorySignal("600002", -2.0),
    ]
    result = sel.select(cands, advisory=advisory)
    assert result.advisory_applied is False
    assert result.shortlist == ("600000", "600001", "600002", "600003")


def test_single_bullish_pull_realizes_one_slot_move() -> None:
    """A lone +1 advisory on the 6th quant name must actually move it up one
    slot (and into a 5-name shortlist) — not be pinned by the idx tie-break
    (codex M-001 P2). max_shift=1 for a 100-name set."""
    sel = CandidateSelector(CFG)
    cands = _candidates(100)  # idx5 == "600005"
    advisory = [AdvisorySignal(code="600005", advisory_score=1.0)]
    result = sel.select(cands, advisory=advisory)
    assert result.advisory_applied is True
    # 600005 climbs past 600004 into the 5-name shortlist.
    assert "600005" in result.shortlist
    assert result.shortlist == (
        "600000",
        "600001",
        "600002",
        "600003",
        "600005",
    )
    # Qualified set membership unchanged (only order moved).
    assert set(result.qualified) == {c.code for c in cands}


def test_in_bound_rerank_changes_order_not_membership() -> None:
    """An adjacent swap (opposing deltas) is within bound → order changes,
    qualified set + final-set membership unchanged."""
    cfg = SelectorConfig(
        version="t",
        final_shortlist_size=5,
        min_quant_slots=3,
        max_percentile_shift=1.0,  # n=5 → max_shift 5
        advisory_weight=1.0,
        feature_def_hash="x",
    )
    sel = CandidateSelector(cfg)
    cands = _candidates(5)
    # Swap the adjacent pair at quant pos 3/4: push 600004 up, 600003 down.
    advisory = [
        AdvisorySignal("600004", 1.0),
        AdvisorySignal("600003", -1.0),
    ]
    result = sel.select(cands, advisory=advisory)
    assert result.advisory_applied is True
    assert result.shortlist == ("600000", "600001", "600002", "600004", "600003")
    assert set(result.qualified) == {c.code for c in cands}


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_empty_input_yields_empty_selection() -> None:
    sel = CandidateSelector(CFG)
    result = sel.select([])
    assert result.shortlist == ()
    assert result.qualified == ()
    assert result.quant_reserved == ()
    assert result.advisory_applied is False


def test_fewer_candidates_than_final_n_returns_all() -> None:
    sel = CandidateSelector(CFG)
    result = sel.select(_candidates(3))
    assert set(result.shortlist) == {"600000", "600001", "600002"}


def test_fewer_candidates_than_min_quant_slots_reserves_all() -> None:
    sel = CandidateSelector(CFG)
    result = sel.select(_candidates(2))
    assert set(result.quant_reserved) == {"600000", "600001"}
    assert set(result.shortlist) == {"600000", "600001"}


def test_duplicate_codes_fail_closed() -> None:
    sel = CandidateSelector(CFG)
    cands = [
        QuantCandidate(code="600001", score=2.0),
        QuantCandidate(code="600001", score=1.0),
    ]
    with pytest.raises(CandidateSelectorError, match="duplicate"):
        sel.select(cands)


def test_non_finite_score_fails_closed() -> None:
    sel = CandidateSelector(CFG)
    with pytest.raises(CandidateSelectorError, match="non-finite"):
        sel.select([QuantCandidate(code="600001", score=math.nan)])


def test_non_finite_advisory_is_ignored() -> None:
    sel = CandidateSelector(CFG)
    cands = _candidates(10)
    advisory = [AdvisorySignal(code="600005", advisory_score=math.inf)]
    result = sel.select(cands, advisory=advisory)
    # A non-finite advisory contributes nothing → pure-quant order, not applied.
    assert result.advisory_applied is False
    assert result.shortlist == ("600000", "600001", "600002", "600003", "600004")


# --------------------------------------------------------------------------
# Config loading + validation
# --------------------------------------------------------------------------


def test_load_real_v1_config() -> None:
    cfg = load_selector_config("config/candidate_weights/v1.yaml")
    assert cfg.version == "candidate_selector/v1"
    assert cfg.final_shortlist_size == 5
    assert cfg.min_quant_slots == 3
    assert cfg.max_percentile_shift == 0.01
    assert len(cfg.feature_def_hash) == 64  # sha256 hex


def test_feature_def_hash_is_stable() -> None:
    a = load_selector_config("config/candidate_weights/v1.yaml")
    b = load_selector_config("config/candidate_weights/v1.yaml")
    assert a.feature_def_hash == b.feature_def_hash


def test_missing_config_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_selector_config(tmp_path / "nope.yaml")


_VALID = {
    "version": "v",
    "final_shortlist_size": 5,
    "min_quant_slots": 3,
    "max_percentile_shift": 0.01,
    "advisory_weight": 1.0,
}


def _write_cfg(tmp_path, **overrides) -> object:
    import yaml

    body = {**_VALID, **overrides}
    body = {k: v for k, v in body.items() if v is not _OMIT}
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


_OMIT = object()


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": _OMIT},          # no version
        {"version": ""},             # empty version
        {"final_shortlist_size": 0},  # bad final_n
        {"min_quant_slots": 6},       # min > final
        {"max_percentile_shift": 0},  # bad shift
        {"max_percentile_shift": 2},  # shift > 1
        {"max_percentile_shift": True},  # bool posing as ratio
        {"advisory_weight": -1},      # bad weight
        {"advisory_weight": float("nan")},  # non-finite weight (codex P2)
        {"advisory_weight": float("inf")},  # non-finite weight (codex P2)
        {"advisory_weight": True},    # bool posing as weight
        {"final_shortlist_size": True},  # bool posing as count
    ],
)
def test_invalid_config_raises(tmp_path, overrides: dict) -> None:
    p = _write_cfg(tmp_path, **overrides)
    with pytest.raises(CandidateSelectorError):
        load_selector_config(p)


def test_config_property_exposes_loaded_config() -> None:
    cfg = load_selector_config("config/candidate_weights/v1.yaml")
    sel = CandidateSelector(cfg)
    assert sel.config is cfg
    assert sel.config.version == "candidate_selector/v1"
