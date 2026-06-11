"""Y-001 — industry-chain KG subcapability: schema, seed, choke-point centrality.

Governance: P2-2-amendment-2026-06-11-industry-chain-kg-schema (expands the
frozen 9-node/12-edge set to 12/17, only adding) + P0-8-amendment-2026-06-01
§2.10 (choke-point = NetworkX centrality on the UPSTREAM_OF subgraph) +
dossier industry-chain-reverse-deduction-2026-06-01 §4 (schema sketch).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.knowledge_graph import (
    EdgeType,
    KGEdge,
    KGNode,
    NodeType,
    SqliteKGStore,
)
from backend.knowledge_graph.centrality import ChokePointScore, chokepoint_scores
from backend.knowledge_graph.seed.industry_chain import (
    IndustryChainSeedReport,
    seed_industry_chain,
)
from backend.knowledge_graph.store import KGSchemaError

# -- schema: the frozen set expanded by +3 nodes / +5 edges (only added) -------


def test_new_node_types_present() -> None:
    for name in ("TREND", "CHAIN_LINK", "PRODUCT"):
        assert hasattr(NodeType, name), name
    # The original 9 stay (only-added invariant, amendment §3).
    for name in (
        "STRATEGY", "BACKTEST_RESULT", "FACTOR", "CONCEPT", "SECTOR",
        "INSTRUMENT", "HEURISTIC", "EVENT", "SOURCE_DOC",
    ):
        assert hasattr(NodeType, name), name
    assert len(NodeType) == 12


def test_new_edge_types_present() -> None:
    for name in ("DRIVES", "REQUIRES", "UPSTREAM_OF", "SUPPLIES_PRODUCT",
                 "MEMBER_OF"):
        assert hasattr(EdgeType, name), name
    assert len(EdgeType) == 17


def test_supersedes_endpoints_unchanged(tmp_path: Path) -> None:
    """Amendment §3: SUPERSEDES stays STRATEGY->STRATEGY; chain criticality
    updates use same-id re-versioning, NOT a loosened SUPERSEDES."""
    from backend.knowledge_graph.schema import EDGE_ENDPOINTS

    legal_src, legal_dst = EDGE_ENDPOINTS[EdgeType.SUPERSEDES]
    assert legal_src == frozenset({NodeType.STRATEGY})
    assert legal_dst == frozenset({NodeType.STRATEGY})
    # A ChainLink->ChainLink SUPERSEDES must be rejected at write time.
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    for cid in ("chainlink:a", "chainlink:b"):
        store.add_node(KGNode(node_id=cid, node_type=NodeType.CHAIN_LINK, name=cid))
    with pytest.raises(KGSchemaError):
        store.add_edge(
            KGEdge(
                edge_id="bad-supersede",
                edge_type=EdgeType.SUPERSEDES,
                src_id="chainlink:a",
                dst_id="chainlink:b",
            )
        )


# -- new node/edge round-trip + endpoint legality ------------------------------


def _chain_store(tmp_path: Path) -> SqliteKGStore:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    nodes = [
        ("trend:x", NodeType.TREND),
        ("sector:x", NodeType.SECTOR),
        ("chainlink:up", NodeType.CHAIN_LINK),
        ("chainlink:down", NodeType.CHAIN_LINK),
        ("product:x", NodeType.PRODUCT),
        ("instrument:000001", NodeType.INSTRUMENT),
    ]
    for nid, nt in nodes:
        store.add_node(KGNode(node_id=nid, node_type=nt, name=nid))
    return store


def test_industry_chain_edges_round_trip(tmp_path: Path) -> None:
    store = _chain_store(tmp_path)
    edges = [
        ("e:drives", EdgeType.DRIVES, "trend:x", "sector:x"),
        ("e:requires", EdgeType.REQUIRES, "sector:x", "chainlink:up"),
        ("e:upstream", EdgeType.UPSTREAM_OF, "chainlink:up", "chainlink:down"),
        ("e:supplies", EdgeType.SUPPLIES_PRODUCT, "instrument:000001", "product:x"),
        ("e:member", EdgeType.MEMBER_OF, "product:x", "chainlink:down"),
        ("e:belongs", EdgeType.BELONGS_TO, "instrument:000001", "sector:x"),
    ]
    for eid, et, s, d in edges:
        store.add_edge(KGEdge(edge_id=eid, edge_type=et, src_id=s, dst_id=d))
    for eid, et, s, d in edges:
        got = store.get_edge(eid)
        assert got is not None and got.edge_type == et
        assert got.src_id == s and got.dst_id == d


@pytest.mark.parametrize(
    ("edge_type", "src", "dst"),
    [
        (EdgeType.DRIVES, "sector:x", "trend:x"),       # reversed direction
        (EdgeType.REQUIRES, "trend:x", "chainlink:up"),  # wrong src type
        (EdgeType.UPSTREAM_OF, "instrument:000001", "chainlink:down"),
        (EdgeType.SUPPLIES_PRODUCT, "product:x", "instrument:000001"),
        (EdgeType.MEMBER_OF, "chainlink:up", "chainlink:down"),
    ],
)
def test_industry_chain_endpoint_legality(
    tmp_path: Path, edge_type: EdgeType, src: str, dst: str
) -> None:
    store = _chain_store(tmp_path)
    with pytest.raises(KGSchemaError):
        store.add_edge(
            KGEdge(edge_id="bad", edge_type=edge_type, src_id=src, dst_id=dst)
        )


# -- choke-point centrality: deterministic, NetworkX-only ----------------------


def _upstream_store(tmp_path: Path) -> SqliteKGStore:
    """A small DAG: 'hub' is upstream of three downstream links -> highest
    choke-point (many downstream depend on it); leaves depend on nothing."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    for cid in ("chainlink:hub", "chainlink:mid", "chainlink:d1",
                "chainlink:d2", "chainlink:leaf"):
        store.add_node(KGNode(node_id=cid, node_type=NodeType.CHAIN_LINK, name=cid))
    ups = [
        ("chainlink:hub", "chainlink:mid"),
        ("chainlink:hub", "chainlink:d1"),
        ("chainlink:mid", "chainlink:d2"),
        ("chainlink:mid", "chainlink:leaf"),
    ]
    for i, (s, d) in enumerate(ups):
        store.add_edge(
            KGEdge(edge_id=f"up:{i}", edge_type=EdgeType.UPSTREAM_OF,
                   src_id=s, dst_id=d)
        )
    return store


def test_chokepoint_scores_rank_hub_highest(tmp_path: Path) -> None:
    store = _upstream_store(tmp_path)
    scores = chokepoint_scores(store.to_networkx())
    assert set(scores) == {
        "chainlink:hub", "chainlink:mid", "chainlink:d1",
        "chainlink:d2", "chainlink:leaf",
    }
    assert all(isinstance(v, ChokePointScore) for v in scores.values())
    assert all(0.0 <= v.composite <= 1.0 for v in scores.values())
    # The upstream hub everything traces back to is the choke point.
    top = max(scores, key=lambda k: scores[k].composite)
    assert top == "chainlink:hub"
    # A pure leaf (nothing depends on it) has the lowest composite.
    assert scores["chainlink:leaf"].composite < scores["chainlink:hub"].composite


def test_chokepoint_scores_are_deterministic(tmp_path: Path) -> None:
    store = _upstream_store(tmp_path)
    first = chokepoint_scores(store.to_networkx())
    second = chokepoint_scores(store.to_networkx())
    assert first == second  # bit-for-bit; PIT-reproducible derived feature


def test_chokepoint_ignores_non_upstream_edges(tmp_path: Path) -> None:
    """Only UPSTREAM_OF among CHAIN_LINK nodes drives the score; a DRIVES or
    BELONGS_TO edge must not leak into the supply topology."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    store.add_node(KGNode(node_id="trend:x", node_type=NodeType.TREND, name="t"))
    store.add_node(KGNode(node_id="sector:x", node_type=NodeType.SECTOR, name="s"))
    store.add_node(
        KGNode(node_id="chainlink:a", node_type=NodeType.CHAIN_LINK, name="a")
    )
    store.add_edge(
        KGEdge(edge_id="e1", edge_type=EdgeType.DRIVES,
               src_id="trend:x", dst_id="sector:x")
    )
    scores = chokepoint_scores(store.to_networkx())
    # Only the lone CHAIN_LINK node appears; trend/sector are not chain links.
    assert set(scores) == {"chainlink:a"}
    # ...and an isolated link (no UPSTREAM_OF) has NO supply evidence -> zero
    # (not a PageRank teleport baseline) so it never looks like a choke point.
    assert scores["chainlink:a"] == ChokePointScore(0.0, 0.0, 0.0, 0.0, 0.0)


def test_chokepoint_empty_graph(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    assert chokepoint_scores(store.to_networkx()) == {}


def test_chokepoint_single_edge_ranks_upstream(tmp_path: Path) -> None:
    """A 2-link chain a->b has no intermediate -> all betweenness zero; the
    upstream link is still the choke point (its downstream depends on it)."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    for cid in ("chainlink:a", "chainlink:b"):
        store.add_node(
            KGNode(node_id=cid, node_type=NodeType.CHAIN_LINK, name=cid)
        )
    store.add_edge(KGEdge(edge_id="u", edge_type=EdgeType.UPSTREAM_OF,
                          src_id="chainlink:a", dst_id="chainlink:b"))
    scores = chokepoint_scores(store.to_networkx())
    assert all(s.betweenness == 0.0 for s in scores.values())
    assert scores["chainlink:a"].composite > scores["chainlink:b"].composite


def test_chokepoint_isolated_links_do_not_perturb_connected(tmp_path: Path) -> None:
    """Topology-only contract (codex P2): adding isolated CHAIN_LINKs must not
    change connected links' scores (no PageRank teleport / betweenness-N leak)."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    for cid in ("chainlink:a", "chainlink:b", "chainlink:c"):
        store.add_node(
            KGNode(node_id=cid, node_type=NodeType.CHAIN_LINK, name=cid)
        )
    store.add_edge(KGEdge(edge_id="u0", edge_type=EdgeType.UPSTREAM_OF,
                          src_id="chainlink:a", dst_id="chainlink:b"))
    store.add_edge(KGEdge(edge_id="u1", edge_type=EdgeType.UPSTREAM_OF,
                          src_id="chainlink:b", dst_id="chainlink:c"))
    base = chokepoint_scores(store.to_networkx())
    for i in range(5):
        store.add_node(KGNode(node_id=f"chainlink:iso{i}",
                              node_type=NodeType.CHAIN_LINK, name=f"iso{i}"))
    after = chokepoint_scores(store.to_networkx())
    for cid in ("chainlink:a", "chainlink:b", "chainlink:c"):
        assert after[cid] == base[cid], cid  # connected scores unchanged
    for i in range(5):
        assert after[f"chainlink:iso{i}"] == ChokePointScore(
            0.0, 0.0, 0.0, 0.0, 0.0
        )


# -- bitemporal re-versioning of chain criticality (append-only, not in-place) -


def test_chainlink_criticality_update_is_append_only(tmp_path: Path) -> None:
    db = tmp_path / "kg.sqlite3"
    store = SqliteKGStore(db, now=datetime(2026, 6, 11, tzinfo=UTC))
    v1 = KGNode(
        node_id="chainlink:photoresist",
        node_type=NodeType.CHAIN_LINK,
        name="光刻胶",
        attrs={"layer": "上游", "criticality": 0.6},
    )
    store.add_node(v1)
    t_after_v1 = datetime(2026, 6, 11, 12, tzinfo=UTC)
    store2 = SqliteKGStore(db, now=datetime(2026, 6, 12, tzinfo=UTC))
    v2 = v1.model_copy(update={"attrs": {"layer": "上游", "criticality": 0.9}})
    store2.add_node(v2)
    # Current view = latest version (research raised criticality).
    current = store2.get_node("chainlink:photoresist")
    assert current is not None and current.attrs["criticality"] == 0.9
    # as-of the first ingest day still returns the old value (PIT replay).
    past = store2.get_node("chainlink:photoresist", as_of=t_after_v1)
    assert past is not None and past.attrs["criticality"] == 0.6
    # History preserved (append-only).
    assert len(store2.node_history("chainlink:photoresist")) == 2


# -- the reconstructed cold-start industry-chain seed --------------------------


@pytest.fixture()
def chain_seeded(tmp_path: Path) -> tuple[SqliteKGStore, IndustryChainSeedReport]:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    report = seed_industry_chain(store)
    return store, report


def test_seed_industry_chain_counts(
    chain_seeded: tuple[SqliteKGStore, IndustryChainSeedReport],
) -> None:
    store, report = chain_seeded
    assert report.trends >= 1
    assert report.sectors >= 1
    assert report.chain_links >= 4  # acceptance: a real multi-link chain
    assert report.products >= 1
    assert report.instruments >= 1
    assert report.source_docs >= 1
    assert report.edges >= report.chain_links  # a connected chain
    # report totals describe the FULL materialised graph (codex P3).
    g = store.to_networkx()
    assert report.total_nodes == g.number_of_nodes()
    assert report.total_edges == g.number_of_edges()


def test_seed_chain_nodes_have_provenance(
    chain_seeded: tuple[SqliteKGStore, IndustryChainSeedReport],
) -> None:
    store, _ = chain_seeded
    g = store.to_networkx()
    for node_id, data in g.nodes(data=True):
        if data["node_type"] == NodeType.SOURCE_DOC.value:
            assert data["attrs"]["content_sha256"]
            continue
        node = store.get_node(node_id)
        assert node is not None and node.provenance_ref, node_id
        assert "#sha256:" in node.provenance_ref
        derived = [
            d for _, d, k in g.out_edges(node_id, keys=True)
            if k == f"derived:{node_id}"
        ]
        assert derived, f"{node_id} lacks DERIVED_FROM"


def test_seed_chain_records_noassertion_license(
    chain_seeded: tuple[SqliteKGStore, IndustryChainSeedReport],
) -> None:
    """ChainKnowledgeGraph is unlicensed; the seed must record that it is a
    reconstruction (not a copy of the NOASSERTION repo) in provenance."""
    store, _ = chain_seeded
    g = store.to_networkx()
    source_docs = [
        store.get_node(n)
        for n, d in g.nodes(data=True)
        if d["node_type"] == NodeType.SOURCE_DOC.value
    ]
    texts = " ".join(
        f"{n.name} {n.attrs.get('license', '')} {n.attrs.get('note', '')}"
        for n in source_docs if n is not None
    )
    assert "NOASSERTION" in texts
    assert "ChainKnowledgeGraph" in texts


def test_seed_chain_chokepoint_derivable(
    chain_seeded: tuple[SqliteKGStore, IndustryChainSeedReport],
) -> None:
    """The seeded chain yields a non-trivial, deterministic choke-point map."""
    store, _ = chain_seeded
    scores = chokepoint_scores(store.to_networkx())
    assert scores  # at least one chain link scored
    assert max(v.composite for v in scores.values()) > 0.0


def test_seed_report_total_source_docs_matches_graph(tmp_path: Path) -> None:
    """Folding chain seeding into cold-start adds 2 SourceDocs; the report's
    total must match the graph (codex P3 — no provenance undercount)."""
    import json

    from backend.knowledge_graph.seed import seed_knowledge_graph

    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps([{"id": 1, "formula": "X"}]), encoding="utf-8")
    report = seed_knowledge_graph(store, wq101_path=fixture, gtja191_path=fixture)
    g = store.to_networkx()
    n_source = sum(
        1
        for _, d in g.nodes(data=True)
        if d["node_type"] == NodeType.SOURCE_DOC.value
    )
    assert report.total_source_docs == n_source
    assert report.total_source_docs == report.source_docs + 2


def test_seed_industry_chain_idempotent(tmp_path: Path) -> None:
    """Re-running is a no-op for nodes; the current view is unchanged."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    r1 = seed_industry_chain(store)
    n_before = len(store.to_networkx().nodes)
    r2 = seed_industry_chain(store)
    assert r1 == r2
    assert len(store.to_networkx().nodes) == n_before


def test_seed_does_not_clobber_existing_node(tmp_path: Path) -> None:
    """A node id the seed shares with the human-gated ingest pipeline must
    keep its APPROVED attrs/provenance — the seed only fills missing (codex P2).
    """
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    # Simulate an ingest-approved sector node already in the graph.
    store.add_node(KGNode(
        node_id="sector:semiconductor",
        node_type=NodeType.SECTOR,
        name="半导体(人工批准)",
        attrs={"approved": True, "src": "ingest"},
        provenance_ref="data/rag/provenance.jsonl#sha256:approved",
    ))
    seed_industry_chain(store)
    kept = store.get_node("sector:semiconductor")
    assert kept is not None
    assert kept.attrs == {"approved": True, "src": "ingest"}
    assert kept.provenance_ref == "data/rag/provenance.jsonl#sha256:approved"
    # Exactly one version — the seed did NOT append a clobbering version.
    assert len(store.node_history("sector:semiconductor")) == 1


def test_seed_does_not_clobber_existing_edge(tmp_path: Path) -> None:
    """Symmetric to nodes (codex P2): a pre-existing edge id the seed also
    defines (e.g. an approved UPSTREAM_OF correction) is preserved, not
    superseded by a seed version."""
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    for cid in ("chainlink:lithography-machine", "chainlink:wafer-fab"):
        store.add_node(
            KGNode(node_id=cid, node_type=NodeType.CHAIN_LINK, name=cid)
        )
    # "upstream:0" is an id the seed also writes (lithography -> wafer-fab).
    store.add_edge(KGEdge(
        edge_id="upstream:0", edge_type=EdgeType.UPSTREAM_OF,
        src_id="chainlink:lithography-machine", dst_id="chainlink:wafer-fab",
        attrs={"approved": True},
    ))
    seed_industry_chain(store)
    kept = store.get_edge("upstream:0")
    assert kept is not None and kept.attrs == {"approved": True}
