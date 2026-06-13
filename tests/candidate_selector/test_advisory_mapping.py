"""O-003 advisory mapping + bounded-rerank adversarial tests.

Red lines (P0-8-amendment-2026-05-24 §2.3 / CandidateSelector CLAUDE.md):
* Removing MiroFish evidence must leave the QUALIFIED SET unchanged
  (only ordering may differ) — MiroFish never gates qualification.
* The bounded re-rank is ≤1-percentile; an out-of-band pull is dropped
  wholesale (fail-closed), never realized.
* The top ``min_quant_slots`` (≥3) pure-quant names always survive — a
  MiroFish sector score can never indirectly evict them.
* candidate_selector stays free of backend.{mirofish,llm,agents}.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.candidate_selector.advisory_mapping import build_advisory_signals
from backend.candidate_selector.selector import (
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)


def _config(**overrides: object) -> SelectorConfig:
    base = {
        "version": "test/v1",
        "final_shortlist_size": 5,
        "min_quant_slots": 3,
        "max_percentile_shift": 0.2,  # generous so a 1-pos pull realizes
        "advisory_weight": 1.0,
        "feature_def_hash": "deadbeef",
    }
    base.update(overrides)
    return SelectorConfig(**base)  # type: ignore[arg-type]


def _quant(*codes_scores: tuple[str, float]) -> list[QuantCandidate]:
    return [QuantCandidate(code=c, score=s) for c, s in codes_scores]


# ---------------------------------------------------------------------------
# build_advisory_signals — pure mapping
# ---------------------------------------------------------------------------


class TestBuildAdvisorySignals:
    def test_maps_sector_score_to_member_codes(self) -> None:
        signals = build_advisory_signals(
            sector_scores={"半导体": 0.5, "银行": -0.3},
            sector_by_code={"600001": "半导体", "600002": "银行"},
            candidate_codes=["600001", "600002"],
        )
        by_code = {s.code: s.advisory_score for s in signals}
        assert by_code == {"600001": 0.5, "600002": -0.3}

    def test_code_without_sector_gets_no_signal(self) -> None:
        signals = build_advisory_signals(
            sector_scores={"半导体": 0.5},
            sector_by_code={"600001": "半导体"},
            candidate_codes=["600001", "600999"],
        )
        assert [s.code for s in signals] == ["600001"]

    def test_sector_without_score_gets_no_signal(self) -> None:
        signals = build_advisory_signals(
            sector_scores={"半导体": 0.5},
            sector_by_code={"600002": "银行"},
            candidate_codes=["600002"],
        )
        assert signals == ()

    def test_out_of_range_and_nonfinite_scores_ignored(self) -> None:
        signals = build_advisory_signals(
            sector_scores={
                "a": 1.5,
                "b": float("nan"),
                "c": float("inf"),
                "d": 0.0,
                "e": 0.4,
            },
            sector_by_code={
                "1": "a",
                "2": "b",
                "3": "c",
                "4": "d",
                "5": "e",
            },
            candidate_codes=["1", "2", "3", "4", "5"],
        )
        assert [(s.code, s.advisory_score) for s in signals] == [("5", 0.4)]

    def test_duplicate_codes_collapse(self) -> None:
        signals = build_advisory_signals(
            sector_scores={"半导体": 0.5},
            sector_by_code={"600001": "半导体"},
            candidate_codes=["600001", "600001"],
        )
        assert len(signals) == 1

    def test_order_follows_candidate_codes(self) -> None:
        signals = build_advisory_signals(
            sector_scores={"a": 0.2, "b": 0.3},
            sector_by_code={"x": "a", "y": "b"},
            candidate_codes=["y", "x"],
        )
        assert [s.code for s in signals] == ["y", "x"]


# ---------------------------------------------------------------------------
# Adversarial: MiroFish can reorder but never gate / evict
# ---------------------------------------------------------------------------


class TestAdversarialRerank:
    def test_removing_advisory_keeps_qualified_set(self) -> None:
        quant = _quant(
            ("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0), ("e", 5.0),
            ("f", 4.0),
        )
        selector = CandidateSelector(_config())
        advisory = build_advisory_signals(
            sector_scores={"hot": 0.9, "cold": -0.9},
            sector_by_code={"f": "hot", "a": "cold"},
            candidate_codes=[c.code for c in quant],
        )
        with_adv = selector.select(quant, advisory=advisory)
        without = selector.select(quant, advisory=None)
        # Qualified set (membership) identical — only order may differ.
        assert set(with_adv.qualified) == set(without.qualified)
        assert with_adv.qualified == without.qualified  # qualified order is pure quant

    def test_low_sector_score_cannot_veto_a_code(self) -> None:
        quant = _quant(("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0))
        selector = CandidateSelector(_config())
        # Tank every sector — MiroFish maximally bearish.
        advisory = build_advisory_signals(
            sector_scores={"x": -1.0},
            sector_by_code={c.code: "x" for c in quant},
            candidate_codes=[c.code for c in quant],
        )
        sel = selector.select(quant, advisory=advisory)
        # All four still qualified — no silent pruning.
        assert set(sel.qualified) == {"a", "b", "c", "d"}

    def test_reserved_quant_survives_bounded_rerank(self) -> None:
        quant = _quant(
            ("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0), ("e", 5.0),
            ("f", 4.0), ("g", 3.0),
        )
        selector = CandidateSelector(_config(max_percentile_shift=0.1))
        # Push the bottom name hard; min_quant_slots=3 top names must remain.
        advisory = build_advisory_signals(
            sector_scores={"hot": 1.0},
            sector_by_code={"g": "hot"},
            candidate_codes=[c.code for c in quant],
        )
        sel = selector.select(quant, advisory=advisory)
        assert set(sel.quant_reserved) == {"a", "b", "c"}
        for code in ("a", "b", "c"):
            assert code in sel.shortlist

    def test_advisory_actually_reorders_within_bound(self) -> None:
        quant = _quant(("a", 9.0), ("b", 8.0), ("c", 7.0))
        selector = CandidateSelector(_config(min_quant_slots=1))
        advisory = build_advisory_signals(
            sector_scores={"hot": 1.0},
            sector_by_code={"c": "hot"},
            candidate_codes=["a", "b", "c"],
        )
        sel = selector.select(quant, advisory=advisory)
        assert sel.advisory_applied is True
        # 'c' pulled up from last; order changed vs pure quant (a,b,c).
        assert sel.shortlist != ("a", "b", "c")


# ---------------------------------------------------------------------------
# Module isolation
# ---------------------------------------------------------------------------


def test_advisory_mapping_no_llm_imports() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "candidate_selector"
        / "advisory_mapping.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = ("backend.mirofish", "backend.llm", "backend.agents")
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not any(
                name == b or name.startswith(b + ".") for b in banned
            ), f"advisory_mapping imports banned module {name}"
