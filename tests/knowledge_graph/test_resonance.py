"""AC-003 — read-only KG logic-resonance family count."""

from __future__ import annotations

import networkx as nx

from backend.knowledge_graph.resonance import evidence_family_ids
from backend.knowledge_graph.schema import NodeType


def _graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_node("600519", node_type=NodeType.INSTRUMENT.value, provenance_ref=None)
    g.add_node(
        "EVENT-A", node_type=NodeType.EVENT.value, provenance_ref="doc:a#sha256:1"
    )
    g.add_node(
        "CONCEPT-B", node_type=NodeType.CONCEPT.value, provenance_ref="doc:b#sha256:2"
    )
    return g


def test_distinct_neighbour_families_counted() -> None:
    g = _graph()
    g.add_edge("EVENT-A", "600519", key="e1", provenance_ref="doc:a#sha256:1")
    g.add_edge("CONCEPT-B", "600519", key="e2", provenance_ref="doc:b#sha256:2")
    fams = evidence_family_ids(g, "600519")
    assert set(fams) == {"doc:a#sha256:1", "doc:b#sha256:2"}
    assert len(fams) == 2


def test_same_run_repeats_collapse() -> None:
    """Two edges sharing a provenance_ref (one run) count as one family."""
    g = _graph()
    g.add_edge("EVENT-A", "600519", key="e1", provenance_ref="doc:a#sha256:1")
    # a second citation from the SAME source doc
    g.add_node(
        "EVENT-A2", node_type=NodeType.EVENT.value, provenance_ref="doc:a#sha256:1"
    )
    g.add_edge("EVENT-A2", "600519", key="e3", provenance_ref="doc:a#sha256:1")
    assert evidence_family_ids(g, "600519") == ("doc:a#sha256:1",)


def test_absent_code_is_empty() -> None:
    assert evidence_family_ids(_graph(), "000001") == ()


def test_non_instrument_node_is_empty() -> None:
    g = _graph()
    assert evidence_family_ids(g, "EVENT-A") == ()


def test_blank_refs_ignored() -> None:
    g = _graph()
    g.add_node("EVENT-C", node_type=NodeType.EVENT.value, provenance_ref="  ")
    g.add_edge("EVENT-C", "600519", key="e4", provenance_ref=None)
    assert evidence_family_ids(g, "600519") == ()


def test_peer_instrument_provenance_not_counted() -> None:
    """A stock-stock edge counts ONCE (edge provenance), not twice (codex P2)."""
    g = _graph()
    # 000002 is a peer INSTRUMENT with its own creation provenance.
    g.add_node(
        "000002",
        node_type=NodeType.INSTRUMENT.value,
        provenance_ref="doc:peer#sha256:9",
    )
    g.add_edge("000002", "600519", key="corr", provenance_ref="doc:corr#sha256:5")
    fams = evidence_family_ids(g, "600519")
    # Only the edge provenance counts — NOT the peer stock's node provenance.
    assert fams == ("doc:corr#sha256:5",)
    assert "doc:peer#sha256:9" not in fams


def test_deterministic_sorted_output() -> None:
    g = _graph()
    g.add_edge("CONCEPT-B", "600519", key="e2", provenance_ref="doc:b#sha256:2")
    g.add_edge("EVENT-A", "600519", key="e1", provenance_ref="doc:a#sha256:1")
    assert evidence_family_ids(g, "600519") == (
        "doc:a#sha256:1",
        "doc:b#sha256:2",
    )
