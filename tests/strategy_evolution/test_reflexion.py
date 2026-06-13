"""T-004 — Reflexion lessons + FinMem decaying exemplars (≤3, passed-cases only).

Covers the deterministic curation (passed + profitable only, policy-linted,
FinMem decay, ≤3 cap), the content-addressed pinnable artifact, and the proposed
persona-card version that closes the loop against the immutable persona skeleton
+ the LiveArtifactRegistry PROMPT_VERSION pin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.agents_team.persona_registry import (
    MAX_PERSONA_EXEMPLARS,
    validate_persona_skeleton,
)
from backend.strategy_evolution.reflexion import (
    MAX_EXEMPLARS,
    Exemplar,
    ExemplarArtifact,
    ReflexionOutcome,
    build_artifact,
    curate_exemplars,
    is_promotable,
    propose_persona_card_version,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CARD = _REPO_ROOT / "config" / "prompts" / "trader_momentum" / "v1.yaml"

_PID = "trader_momentum"


def _outcome(
    text: str,
    *,
    rank: int,
    risk_passed: bool = True,
    profitable: bool = True,
    persona: str = _PID,
) -> ReflexionOutcome:
    return ReflexionOutcome(
        persona_id=persona,
        advice_text=text,
        risk_passed=risk_passed,
        profitable=profitable,
        recency_rank=rank,
        occurred_at=datetime(2026, 6, 13, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# curate_exemplars — filters + cap + decay + determinism
# ---------------------------------------------------------------------------


class TestCurate:
    @pytest.mark.unit
    def test_only_passed_and_profitable(self) -> None:
        outcomes = (
            _outcome("回踩确认后进场, 标准仓", rank=0),
            _outcome("追高满仓, 亏损出场", rank=1, profitable=False),
            _outcome("入场但被风控拒", rank=2, risk_passed=False),
        )
        ex = curate_exemplars(outcomes, persona_id=_PID)
        texts = {e.text for e in ex}
        assert texts == {"回踩确认后进场, 标准仓"}

    @pytest.mark.unit
    def test_lint_drops_order_text(self) -> None:
        """An exemplar smuggling a sized order is dropped, never promoted."""
        outcomes = (
            _outcome("立即买入 5000 股满仓 限价 99.99", rank=0),  # forbidden
            _outcome("趋势确认, 标准仓位", rank=1),
        )
        ex = curate_exemplars(outcomes, persona_id=_PID)
        assert [e.text for e in ex] == ["趋势确认, 标准仓位"]

    @pytest.mark.unit
    def test_cap_at_three(self) -> None:
        outcomes = tuple(_outcome(f"良好示范{i}", rank=i) for i in range(6))
        ex = curate_exemplars(outcomes, persona_id=_PID)
        assert len(ex) == MAX_EXEMPLARS == 3

    @pytest.mark.unit
    def test_finmem_decay_orders_recent_first(self) -> None:
        outcomes = (
            _outcome("旧案例", rank=10),
            _outcome("新案例", rank=0),
        )
        ex = curate_exemplars(outcomes, persona_id=_PID)
        assert ex[0].text == "新案例"
        assert ex[0].weight > ex[1].weight

    @pytest.mark.unit
    def test_dedup_keeps_most_recent(self) -> None:
        outcomes = (
            _outcome("同一句", rank=8),
            _outcome("同一句", rank=2),
        )
        ex = curate_exemplars(outcomes, persona_id=_PID)
        assert len(ex) == 1
        assert ex[0].weight == pytest.approx(0.5 ** (2 / 5.0), rel=1e-6)

    @pytest.mark.unit
    def test_persona_filtering(self) -> None:
        outcomes = (
            _outcome("动量示范", rank=0, persona="trader_momentum"),
            _outcome("回归示范", rank=0, persona="trader_mean_reversion"),
        )
        ex = curate_exemplars(outcomes, persona_id="trader_momentum")
        assert [e.text for e in ex] == ["动量示范"]

    @pytest.mark.unit
    def test_empty_history_is_noop(self) -> None:
        assert curate_exemplars((), persona_id=_PID) == ()

    @pytest.mark.unit
    def test_deterministic(self) -> None:
        outcomes = tuple(_outcome(f"示范{i}", rank=i) for i in range(5))
        assert curate_exemplars(outcomes, persona_id=_PID) == curate_exemplars(
            outcomes, persona_id=_PID
        )


# ---------------------------------------------------------------------------
# ExemplarArtifact — content-addressed + fail-closed validators
# ---------------------------------------------------------------------------


class TestArtifact:
    @pytest.mark.unit
    def test_content_hash_stable_and_deterministic(self) -> None:
        a = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("甲", "乙")
        )
        b = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("甲", "乙")
        )
        assert a.content_hash() == b.content_hash()
        assert len(a.content_hash()) == 64

    @pytest.mark.unit
    def test_order_changes_hash(self) -> None:
        a = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("甲", "乙")
        )
        b = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("乙", "甲")
        )
        assert a.content_hash() != b.content_hash()

    @pytest.mark.unit
    def test_over_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="FinMem cap"):
            ExemplarArtifact(
                persona_id=_PID,
                base_version="v1",
                exemplar_texts=("a", "b", "c", "d"),
            )

    @pytest.mark.unit
    def test_forbidden_text_rejected(self) -> None:
        with pytest.raises(ValueError, match="deny-list"):
            ExemplarArtifact(
                persona_id=_PID,
                base_version="v1",
                exemplar_texts=("买入 3000 股",),
            )

    @pytest.mark.unit
    def test_frozen(self) -> None:
        a = ExemplarArtifact(persona_id=_PID, base_version="v1")
        with pytest.raises((TypeError, ValueError)):
            a.persona_id = "x"  # type: ignore[misc]

    @pytest.mark.unit
    def test_build_artifact_from_exemplars(self) -> None:
        ex = (Exemplar(persona_id=_PID, text="示范甲", weight=1.0),)
        art = build_artifact(ex, persona_id=_PID, base_version="v1")
        assert art.exemplar_texts == ("示范甲",)

    @pytest.mark.unit
    def test_is_promotable(self) -> None:
        art = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("示范甲",)
        )
        assert is_promotable(art) is True

    @pytest.mark.unit
    def test_build_artifact_rejects_cross_persona(self) -> None:
        """codex T-004 P2: an exemplar from another persona is rejected."""
        ex = (Exemplar(persona_id="trader_mean_reversion", text="x", weight=1.0),)
        with pytest.raises(ValueError, match="cross-persona"):
            build_artifact(ex, persona_id="trader_momentum", base_version="v1")


# ---------------------------------------------------------------------------
# propose_persona_card_version — closes the loop to the pinnable card
# ---------------------------------------------------------------------------


class TestProposeCard:
    @pytest.mark.unit
    def test_proposed_card_passes_skeleton_and_pins(self) -> None:
        """Curated exemplars → a card v2 that the persona registry accepts."""
        outcomes = tuple(_outcome(f"良好示范{i}", rank=i) for i in range(3))
        ex = curate_exemplars(outcomes, persona_id=_PID)
        art = build_artifact(ex, persona_id=_PID, base_version="v1")
        base = _CARD.read_text(encoding="utf-8")
        proposed = propose_persona_card_version(base, art, new_version="v2")
        assert proposed.version == "v2"
        assert len(proposed.sha256) == 64
        # The proposed card keeps the frozen skeleton + carries ≤3 exemplars,
        # so the persona registry's loader would accept it once pinned.
        doc = validate_persona_skeleton(proposed.content, expected_persona_id=_PID)
        assert doc["version"] == "v2"
        assert len(doc["exemplars"]) <= MAX_PERSONA_EXEMPLARS
        assert doc["exemplars"] == list(art.exemplar_texts)
        # Identity skeleton is carried over verbatim (immutable).
        base_doc = validate_persona_skeleton(base, expected_persona_id=_PID)
        assert doc["identity"] == base_doc["identity"]
        assert doc["mandate"] == base_doc["mandate"]
        assert doc["output_contract"] == base_doc["output_contract"]

    @pytest.mark.unit
    def test_persona_id_mismatch_rejected(self) -> None:
        art = ExemplarArtifact(
            persona_id="trader_mean_reversion", base_version="v1",
            exemplar_texts=("x",),
        )
        base = _CARD.read_text(encoding="utf-8")  # this is trader_momentum
        with pytest.raises(ValueError, match="persona_id"):
            propose_persona_card_version(base, art, new_version="v2")

    @pytest.mark.unit
    def test_malformed_base_rejected(self) -> None:
        art = ExemplarArtifact(persona_id=_PID, base_version="v1")
        with pytest.raises(ValueError):
            propose_persona_card_version("- not a mapping", art, new_version="v2")

    @pytest.mark.unit
    def test_missing_skeleton_key_rejected(self) -> None:
        art = ExemplarArtifact(persona_id=_PID, base_version="v1")
        base = "persona_id: trader_momentum\nversion: v1\nidentity: x\n"
        with pytest.raises(ValueError, match="skeleton"):
            propose_persona_card_version(base, art, new_version="v2")

    @pytest.mark.unit
    def test_base_version_mismatch_rejected(self) -> None:
        """codex T-004 P2: artifact must be curated against the applied base."""
        art = ExemplarArtifact(persona_id=_PID, base_version="v99")
        base = _CARD.read_text(encoding="utf-8")  # this card is version v1
        with pytest.raises(ValueError, match="base_version"):
            propose_persona_card_version(base, art, new_version="v2")

    @pytest.mark.unit
    def test_byte_preserves_frozen_sections_and_comments(self) -> None:
        """codex T-004 P2: only version + exemplars change; comments survive."""
        art = ExemplarArtifact(
            persona_id=_PID, base_version="v1", exemplar_texts=("回踩确认后标准仓",)
        )
        base = _CARD.read_text(encoding="utf-8")
        proposed = propose_persona_card_version(base, art, new_version="v2")
        # Governance comment from the card header is carried over verbatim.
        assert "# 交易员人格卡 — 动量交易员" in proposed.content
        # version replaced, old version line gone.
        assert "version: v2" in proposed.content
        assert "version: v1\n" not in proposed.content
        # The new exemplar is present; the empty placeholder is gone.
        assert "回踩确认后标准仓" in proposed.content
        assert "exemplars: []" not in proposed.content
