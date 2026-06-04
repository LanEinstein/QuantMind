"""Q-003 — ingest (extract+verify+human gate) and offline retrieval."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.knowledge_graph import EdgeType, KGNode, NodeType, SqliteKGStore
from backend.knowledge_graph.ingest import (
    JsonlProvenanceIndex,
    KGIngestError,
    KGIngestPipeline,
    ProposedNode,
    ProposedTriple,
)
from backend.knowledge_graph.retrieval import KGRetriever

_DOC = "arxiv:2401.00001"
_TEXT = "the anchored paper text: momentum predicts returns"
_SHA = hashlib.sha256(_TEXT.encode("utf-8")).hexdigest()
_NOW = datetime(2026, 6, 4, 8, 0, tzinfo=UTC)


def _triple(rationale: str = "paper links momentum factor to the strategy") -> (
    ProposedTriple
):
    return ProposedTriple(
        src=ProposedNode(
            node_id="strat:from-paper", node_type=NodeType.STRATEGY,
            name="paper momentum strategy",
        ),
        edge_type=EdgeType.USES_FACTOR,
        dst=ProposedNode(
            node_id="factor:from-paper", node_type=NodeType.FACTOR,
            name="paper momentum factor",
        ),
        rationale=rationale,
        doc_id=_DOC,
        content_sha256=_SHA,
    )


class _FakeExtractor:
    def __init__(self, triples: tuple[ProposedTriple, ...]) -> None:
        self._triples = triples

    def extract(self, doc_text: str, doc_id: str) -> tuple[ProposedTriple, ...]:
        return self._triples


class _RejectSecond:
    """Second-agent verifier that rejects any rationale containing 'bogus'."""

    def verify(self, triple: ProposedTriple, doc_text: str) -> bool:
        return "bogus" not in triple.rationale


def _provenance(tmp_path: Path, *, anchored: bool = True) -> JsonlProvenanceIndex:
    ledger = tmp_path / "provenance.jsonl"
    if anchored:
        ledger.write_text(
            json.dumps({"doc_id": _DOC, "content_sha256": _SHA}) + "\n",
            encoding="utf-8",
        )
    return JsonlProvenanceIndex(ledger)


def _pipeline(tmp_path: Path, *triples: ProposedTriple, anchored: bool = True) -> (
    KGIngestPipeline
):
    return KGIngestPipeline(
        extractor=_FakeExtractor(tuple(triples)),
        verifier=_RejectSecond(),
        provenance=_provenance(tmp_path, anchored=anchored),
        ledger_dir=tmp_path / "kg_ingest",
        now=_NOW,
    )


# -- extraction + second-pass verification -----------------------------------------


def test_verifier_filters_extracted_triples(tmp_path: Path) -> None:
    good, bad = _triple(), _triple("bogus claim")
    pipeline = _pipeline(tmp_path, good, bad)
    accepted = pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)
    assert [t.proposal_id for t in accepted] == [good.proposal_id]
    pending = pipeline.pending_path.read_text(encoding="utf-8").splitlines()
    assert len(pending) == 1  # only the verified triple is PENDING


def test_unanchored_doc_refused(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _triple(), anchored=False)
    with pytest.raises(KGIngestError, match="not anchored"):
        pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)


def test_triple_anchoring_other_doc_refused(tmp_path: Path) -> None:
    foreign = _triple().model_copy(update={"content_sha256": "b" * 64})
    pipeline = _pipeline(tmp_path, foreign)
    with pytest.raises(KGIngestError, match="different doc"):
        pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)


# -- human gate ----------------------------------------------------------------------


def test_unapproved_proposal_never_enters_graph(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    pipeline = _pipeline(tmp_path, _triple())
    pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)
    # Proposed but NOT decided -> the graph stays empty.
    assert store.get_node("strat:from-paper") is None
    assert store.get_node("factor:from-paper") is None


def test_approval_writes_graph_with_provenance(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    triple = _triple()
    pipeline = _pipeline(tmp_path, triple)
    pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)
    wrote = pipeline.decide(
        store, triple.proposal_id, decision="approve", decided_by="owner"
    )
    assert wrote is True
    node = store.get_node("strat:from-paper")
    assert node is not None and node.provenance_ref == f"{_DOC}#sha256:{_SHA}"
    edge = store.get_edge(f"ingest:{triple.proposal_id}")
    assert edge is not None
    assert edge.attrs["rationale"] == triple.rationale
    decisions = pipeline.decisions_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(decisions[0])["decided_by"] == "owner"


def test_reject_recorded_but_never_applied(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    triple = _triple()
    pipeline = _pipeline(tmp_path, triple)
    pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)
    wrote = pipeline.decide(
        store, triple.proposal_id, decision="reject", decided_by="owner"
    )
    assert wrote is False
    assert store.get_node("strat:from-paper") is None
    # A decided proposal cannot be re-decided (no approve-after-reject).
    with pytest.raises(KGIngestError, match="already decided"):
        pipeline.decide(
            store, triple.proposal_id, decision="approve", decided_by="owner"
        )


def test_gate_requires_named_human_and_known_proposal(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    pipeline = _pipeline(tmp_path, _triple())
    with pytest.raises(KGIngestError, match="decided_by"):
        pipeline.decide(store, "whatever", decision="approve", decided_by="  ")
    with pytest.raises(KGIngestError, match="unknown proposal"):
        pipeline.decide(store, "nonexistent", decision="approve", decided_by="owner")


# -- offline retrieval ---------------------------------------------------------


class _StubEmbedder:
    """Deterministic 3-dim embedder keyed on keywords (offline, zero model)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                1.0 if "momentum" in t else 0.0,
                1.0 if "volatility" in t else 0.0,
                1.0,
            ]
            for t in texts
        ]


def _seeded_store(tmp_path: Path) -> SqliteKGStore:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    store.add_node(
        KGNode(node_id="factor:momo", node_type=NodeType.FACTOR, name="momentum 20d")
    )
    store.add_node(
        KGNode(node_id="factor:vol", node_type=NodeType.FACTOR, name="volatility 20d")
    )
    store.add_node(
        KGNode(node_id="strat:m", node_type=NodeType.STRATEGY, name="momo strategy")
    )
    from backend.knowledge_graph import KGEdge

    store.add_edge(
        KGEdge(
            edge_id="e:uses", edge_type=EdgeType.USES_FACTOR,
            src_id="strat:m", dst_id="factor:momo",
        )
    )
    return store


def test_retrieval_returns_similar_node_with_neighbourhood(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    retriever = KGRetriever(store, _StubEmbedder())
    hits = retriever.query("momentum factor", k=1)
    assert len(hits) == 1
    assert hits[0].node.node_id == "factor:momo"
    assert [n.node_id for n in hits[0].neighbours] == ["strat:m"]
    assert [e.edge_id for e in hits[0].edges] == ["e:uses"]


def test_retrieval_is_read_only(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path)
    before = len(store.to_networkx().nodes)
    retriever = KGRetriever(store, _StubEmbedder())
    retriever.query("volatility", k=2)
    assert len(store.to_networkx().nodes) == before
    # And history untouched: every node still has exactly one version.
    for node_id in ("factor:momo", "factor:vol", "strat:m"):
        assert len(store.node_history(node_id)) == 1


def test_retrieval_empty_graph_and_zero_k(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    retriever = KGRetriever(store, _StubEmbedder())
    assert retriever.query("anything", k=3) == ()
    (tmp_path / "other").mkdir()
    seeded = _seeded_store(tmp_path / "other")
    assert KGRetriever(seeded, _StubEmbedder()).query("x", k=0) == ()


# -- codex hardening (P1/P1/P2) ---------------------------------------------------


def test_text_not_matching_anchor_refused(tmp_path: Path) -> None:
    # The LLM must only ever read the exact anchored bytes (codex P1).
    pipeline = _pipeline(tmp_path, _triple())
    with pytest.raises(KGIngestError, match="refuse"):
        pipeline.propose(
            doc_text=_TEXT + " [edited]", doc_id=_DOC, content_sha256=_SHA
        )


def test_superseded_or_rejected_provenance_not_anchored(tmp_path: Path) -> None:
    ledger = tmp_path / "provenance.jsonl"
    new_sha = hashlib.sha256(b"v2").hexdigest()
    # Row 1: original hash. Row 2 (LATEST): re-ingested with a new hash.
    ledger.write_text(
        json.dumps({"doc_id": _DOC, "content_sha256": _SHA}) + "\n"
        + json.dumps({"doc_id": _DOC, "content_sha256": new_sha}) + "\n",
        encoding="utf-8",
    )
    index = JsonlProvenanceIndex(ledger)
    assert index.is_anchored(_DOC, _SHA) is False  # old hash superseded
    assert index.is_anchored(_DOC, new_sha) is True
    # A latest row carrying a rejection anchors NOTHING.
    ledger.write_text(
        json.dumps(
            {"doc_id": _DOC, "content_sha256": _SHA, "rejection_reason": "spam"}
        ) + "\n",
        encoding="utf-8",
    )
    assert JsonlProvenanceIndex(ledger).is_anchored(_DOC, _SHA) is False


def test_illegal_endpoint_approval_fails_clean_and_retryable(tmp_path: Path) -> None:
    # USES_FACTOR forbids Factor -> Strategy; the approval must raise BEFORE
    # any write or decision record, leaving the proposal retryable (codex P2).
    bad = ProposedTriple(
        src=ProposedNode(
            node_id="factor:x", node_type=NodeType.FACTOR, name="factor x",
        ),
        edge_type=EdgeType.USES_FACTOR,
        dst=ProposedNode(
            node_id="strat:x", node_type=NodeType.STRATEGY, name="strategy x",
        ),
        rationale="inverted by the extractor",
        doc_id=_DOC,
        content_sha256=_SHA,
    )

    class _ApproveAll:
        def verify(self, triple: ProposedTriple, doc_text: str) -> bool:
            return True

    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    pipeline = KGIngestPipeline(
        extractor=_FakeExtractor((bad,)),
        verifier=_ApproveAll(),
        provenance=_provenance(tmp_path),
        ledger_dir=tmp_path / "kg_ingest",
        now=_NOW,
    )
    pipeline.propose(doc_text=_TEXT, doc_id=_DOC, content_sha256=_SHA)
    with pytest.raises(KGIngestError, match="forbids"):
        pipeline.decide(store, bad.proposal_id, decision="approve", decided_by="owner")
    # Nothing written, nothing decided — still retryable after a fix.
    assert store.get_node("factor:x") is None
    assert not pipeline.decisions_path.exists()
