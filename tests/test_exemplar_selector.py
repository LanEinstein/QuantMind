"""X-006 unit tests — ExemplarRecord / ExemplarSelector / stratify / k cap.

All embedding work happens through the Protocol-injected stub so the
600 MB Qwen3 checkpoint is never required to exercise the selection
logic. A dedicated test verifies the model directory fail-fast.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.services.exemplar_selector import (
    AGENT_NAMES,
    DEFAULT_MODEL_PATH,
    DEFAULT_WINDOW_DAYS,
    EMBEDDING_DIM,
    LAYER_NAMES,
    MAX_K,
    EmbeddingModelNotReadyError,
    ExemplarKCapExceededError,
    ExemplarRecord,
    ExemplarSelector,
    InMemoryExemplarStore,
    LocalQwen3Embedding,
    UnknownAgentRoleError,
    date_window,
    list_candidate_pool,
    make_unit_vector,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 5, 18)


# ---------------------------------------------------------------------------
# Stub embedding model
# ---------------------------------------------------------------------------


@dataclass
class _StubEmbedding:
    """Deterministic embedding that returns the seed encoded in the
    first slot — easy to write similarity assertions against."""

    text_to_seed: dict[str, int] = field(default_factory=dict)
    default_seed: int = 0
    calls: list[str] = field(default_factory=list)

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        seed = self.text_to_seed.get(text, self.default_seed)
        return make_unit_vector(seed)

    def embed_batch(
        self, texts: Sequence[str]
    ) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed(t) for t in texts)


def _make_record(
    *,
    layer: str = "deep",
    agent_role: str = "fundamental_analyst",
    decision_date: date = TODAY,
    is_anti_exemplar: bool = False,
    outcome: str = "profit",
    seed: int | None = None,
    exemplar_id: str | None = None,
) -> ExemplarRecord:
    rid = exemplar_id or f"EXEMPLAR-{agent_role}-{layer}-{seed or 0}"
    return ExemplarRecord(
        exemplar_id=rid,
        instruction_id=f"QM-20260518-100000-000001-BUY-{seed or 0:03d}",
        decision_date=decision_date,
        agent_role=agent_role,  # type: ignore[arg-type]
        stock_code="600519",
        action="BUY",
        reasoning_excerpt="example reasoning",
        evidence_ids=("NEWS-2026-05-18-AAA",),
        confidence_at_decision=0.7,
        outcome=outcome,  # type: ignore[arg-type]
        layer=layer,  # type: ignore[arg-type]
        outcome_pnl_bp=120 if outcome == "profit" else None,
        embedding=make_unit_vector(seed) if seed is not None else None,
        is_anti_exemplar=is_anti_exemplar,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_max_k_locked_to_three() -> None:
    assert MAX_K == 3


def test_embedding_dim_locked_to_1024() -> None:
    assert EMBEDDING_DIM == 1024


def test_window_default_ninety_days() -> None:
    assert DEFAULT_WINDOW_DAYS == 90


def test_agent_names_four_mandatory_agents() -> None:
    assert set(AGENT_NAMES) == {
        "fundamental_analyst",
        "technical_analyst",
        "risk_officer",
        "fund_manager",
    }


def test_layer_names_three_finmem_layers() -> None:
    assert LAYER_NAMES == ("shallow", "intermediate", "deep")


def test_default_model_path_under_data_models() -> None:
    assert DEFAULT_MODEL_PATH == Path("data/models/Qwen3-Embedding-0.6B")


# ---------------------------------------------------------------------------
# ExemplarRecord
# ---------------------------------------------------------------------------


def test_record_happy_path_without_embedding() -> None:
    rec = _make_record()
    assert rec.embedding is None
    assert rec.is_anti_exemplar is False
    assert rec.layer == "deep"


def test_record_happy_path_with_embedding() -> None:
    rec = _make_record(seed=42)
    assert rec.embedding is not None
    assert len(rec.embedding) == EMBEDDING_DIM


def test_record_is_frozen() -> None:
    rec = _make_record()
    with pytest.raises(ValidationError):
        rec.action = "SELL"  # type: ignore[misc]


def test_record_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        ExemplarRecord(
            exemplar_id="x",
            instruction_id="QM-20260518-100000-000001-BUY-001",
            decision_date=TODAY,
            agent_role="fundamental_analyst",
            stock_code="600519",
            action="BUY",
            reasoning_excerpt="r",
            evidence_ids=(),
            confidence_at_decision=0.5,
            outcome="profit",
            layer="deep",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_record_rejects_bad_embedding_dim() -> None:
    bad_embedding = (0.0,) * (EMBEDDING_DIM - 1)
    with pytest.raises(ValidationError):
        ExemplarRecord(
            exemplar_id="x",
            instruction_id="QM-20260518-100000-000001-BUY-001",
            decision_date=TODAY,
            agent_role="fundamental_analyst",
            stock_code="600519",
            action="BUY",
            reasoning_excerpt="r",
            evidence_ids=(),
            confidence_at_decision=0.5,
            outcome="profit",
            layer="deep",
            embedding=bad_embedding,
        )


def test_record_rejects_pending_anti_exemplar() -> None:
    with pytest.raises(ValidationError, match="pending"):
        _make_record(outcome="pending", is_anti_exemplar=True)


def test_record_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ExemplarRecord(
            exemplar_id="x",
            instruction_id="QM-20260518-100000-000001-BUY-001",
            decision_date=TODAY,
            agent_role="fundamental_analyst",
            stock_code="600519",
            action="BUY",
            reasoning_excerpt="r",
            evidence_ids=(),
            confidence_at_decision=1.5,
            outcome="profit",
            layer="deep",
        )


# ---------------------------------------------------------------------------
# make_unit_vector + date_window helpers
# ---------------------------------------------------------------------------


def test_make_unit_vector_is_unit_length() -> None:
    vec = make_unit_vector(7)
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-6


def test_make_unit_vector_seeded_distinct() -> None:
    assert make_unit_vector(1) != make_unit_vector(2)


def test_date_window_subtracts_correctly() -> None:
    assert date_window(TODAY, 30) == TODAY - timedelta(days=30)


def test_list_candidate_pool_filters_by_role_and_window() -> None:
    pool = [
        _make_record(agent_role="fundamental_analyst", decision_date=TODAY),
        _make_record(agent_role="technical_analyst", decision_date=TODAY),
        _make_record(
            agent_role="fundamental_analyst",
            decision_date=TODAY - timedelta(days=200),  # outside 90-day window
            exemplar_id="too-old",
        ),
    ]
    selected = list_candidate_pool(
        candidates=pool,
        agent_role="fundamental_analyst",
        as_of=TODAY,
        window_days=90,
    )
    assert {r.exemplar_id for r in selected} != {"too-old"}
    assert all(r.agent_role == "fundamental_analyst" for r in selected)


# ---------------------------------------------------------------------------
# Selector — k cap + unknown role
# ---------------------------------------------------------------------------


def test_selector_k_over_cap_raises() -> None:
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=[]),
    )
    with pytest.raises(ExemplarKCapExceededError):
        selector.retrieve(
            agent_role="fundamental_analyst",
            query_context="anything",
            k=4,
        )


def test_selector_k_zero_returns_empty() -> None:
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=[]),
    )
    assert (
        selector.retrieve(
            agent_role="fundamental_analyst",
            query_context="anything",
            k=0,
        )
        == ()
    )


def test_selector_unknown_role_raises() -> None:
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=[]),
    )
    with pytest.raises(UnknownAgentRoleError, match="news_crawler"):
        selector.retrieve(
            agent_role="news_crawler",
            query_context="anything",
        )


def test_selector_empty_pool_returns_empty_tuple() -> None:
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=[]),
    )
    assert (
        selector.retrieve(
            agent_role="fundamental_analyst",
            query_context="q",
            as_of=TODAY,
        )
        == ()
    )


# ---------------------------------------------------------------------------
# Selector — per-agent stratification
# ---------------------------------------------------------------------------


def _build_pool(agent_role: str) -> list[ExemplarRecord]:
    pool: list[ExemplarRecord] = []
    seed = 0
    for layer in ("shallow", "intermediate", "deep"):
        for _ in range(3):
            pool.append(
                _make_record(
                    agent_role=agent_role,
                    layer=layer,
                    seed=seed,
                )
            )
            seed += 1
    return pool


def test_fundamental_analyst_picks_deep_two_intermediate_one() -> None:
    pool = _build_pool("fundamental_analyst")
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(default_seed=0),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="fundamental_analyst",
        query_context="query",
        as_of=TODAY,
    )
    layers = [r.layer for r in result]
    assert layers.count("deep") == 2
    assert layers.count("intermediate") == 1
    assert layers.count("shallow") == 0


def test_technical_analyst_picks_three_shallow() -> None:
    pool = _build_pool("technical_analyst")
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="technical_analyst",
        query_context="query",
        as_of=TODAY,
    )
    assert [r.layer for r in result] == ["shallow", "shallow", "shallow"]


def test_fund_manager_picks_each_layer_once() -> None:
    pool = _build_pool("fund_manager")
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="fund_manager",
        query_context="query",
        as_of=TODAY,
    )
    layers = sorted(r.layer for r in result)
    assert layers == ["deep", "intermediate", "shallow"]


def test_risk_officer_forces_anti_exemplar() -> None:
    # Pool has 3 shallow positives + 1 anti-exemplar (also shallow);
    # the anti-exemplar has lower similarity than the top-2 positives.
    pool: list[ExemplarRecord] = [
        _make_record(
            agent_role="risk_officer", layer="shallow", seed=1,
            exemplar_id="positive-A",
        ),
        _make_record(
            agent_role="risk_officer", layer="shallow", seed=2,
            exemplar_id="positive-B",
        ),
        _make_record(
            agent_role="risk_officer", layer="shallow", seed=3,
            exemplar_id="positive-C",
        ),
        _make_record(
            agent_role="risk_officer", layer="shallow", seed=99,
            is_anti_exemplar=True, outcome="loss",
            exemplar_id="anti-D",
        ),
    ]
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(default_seed=0),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="risk_officer",
        query_context="anything",
        as_of=TODAY,
    )
    assert any(r.is_anti_exemplar for r in result)
    assert "anti-D" in {r.exemplar_id for r in result}


def test_risk_officer_without_any_anti_exemplar_returns_chosen_unchanged() -> None:
    pool = [
        _make_record(
            agent_role="risk_officer", layer="shallow", seed=i,
            exemplar_id=f"pos-{i}",
        )
        for i in range(3)
    ]
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="risk_officer",
        query_context="q",
        as_of=TODAY,
    )
    assert not any(r.is_anti_exemplar for r in result)
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# Selector — window filter
# ---------------------------------------------------------------------------


def test_selector_filters_outside_window() -> None:
    pool = [
        _make_record(agent_role="fundamental_analyst", layer="deep",
                     decision_date=TODAY, seed=10),
        _make_record(
            agent_role="fundamental_analyst",
            layer="deep",
            decision_date=TODAY - timedelta(days=200),
            seed=11,
            exemplar_id="too-old",
        ),
    ]
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=pool),
        window_days=90,
    )
    result = selector.retrieve(
        agent_role="fundamental_analyst",
        query_context="q",
        as_of=TODAY,
    )
    assert "too-old" not in {r.exemplar_id for r in result}


# ---------------------------------------------------------------------------
# Selector — k <= 3 enforced post-stratify when more candidates exist
# ---------------------------------------------------------------------------


def test_selector_returns_at_most_max_k() -> None:
    pool = _build_pool("fundamental_analyst")
    selector = ExemplarSelector(
        embedding_model=_StubEmbedding(),
        store=InMemoryExemplarStore(pool=pool),
    )
    result = selector.retrieve(
        agent_role="fundamental_analyst",
        query_context="q",
        as_of=TODAY,
    )
    assert len(result) <= MAX_K


# ---------------------------------------------------------------------------
# LocalQwen3Embedding — directory fail-fast
# ---------------------------------------------------------------------------


def test_local_qwen3_fail_fast_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-model"
    with pytest.raises(EmbeddingModelNotReadyError, match="missing"):
        LocalQwen3Embedding.fail_fast_validate(model_dir=missing)


def test_local_qwen3_fail_fast_missing_config_json(tmp_path: Path) -> None:
    model_dir = tmp_path / "Qwen3-Embedding-0.6B"
    model_dir.mkdir()
    with pytest.raises(EmbeddingModelNotReadyError, match="config.json"):
        LocalQwen3Embedding.fail_fast_validate(model_dir=model_dir)


def test_local_qwen3_fail_fast_happy(tmp_path: Path) -> None:
    model_dir = tmp_path / "Qwen3-Embedding-0.6B"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    LocalQwen3Embedding.fail_fast_validate(model_dir=model_dir)


# ---------------------------------------------------------------------------
# Import-gate red line — selector module avoids forbidden imports
# ---------------------------------------------------------------------------


def test_exemplar_selector_module_avoids_forbidden_imports() -> None:
    src = (
        REPO_ROOT / "backend/services/exemplar_selector.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "from backend.api",
        "from backend.broker",
        "from backend.risk",
        "from backend.llm",
        "from backend.agents",
        "from backend.mirofish",
        "from backend.data",
        "import backend.api",
        "import backend.broker",
        "import backend.risk",
        "import backend.llm",
        "import backend.agents",
        "import backend.mirofish",
        "import backend.data",
    ):
        assert forbidden not in src, (
            f"P2-2 §2 red line 17 violation: exemplar_selector.py contains "
            f"{forbidden!r}"
        )
