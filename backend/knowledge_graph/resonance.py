"""Logic-resonance count over the KG (read-only, Phase AC-003).

The value line's surface tier rewards a name supported by ≥2 **independent**
logics (owner: "多逻辑共振 ≥2 核心逻辑"). We read the pinned graph and collect
the distinct evidence-family ids touching a stock's :class:`NodeType.INSTRUMENT`
node — every edge incident to it plus the connected non-stock neighbours, keyed
by ``provenance_ref``. A ``provenance_ref`` points at one source/run in
``data/rag/provenance.jsonl``, so **repeated citations from the same run collapse
to one family** (codex P1-4) while two genuinely independent sources count twice.

Read-only + deterministic: it never mutates the graph and returns the family ids
sorted, so the surface-tier resonance score replays bit-exact. Import-isolated
(KG red line): networkx + schema only — no ``backend.{api,broker,risk,llm,…}``.
"""

from __future__ import annotations

import networkx as nx

from backend.knowledge_graph.schema import NodeType


def evidence_family_ids(graph: nx.MultiDiGraph, code: str) -> tuple[str, ...]:
    """Distinct evidence-family ids supporting INSTRUMENT ``code`` (sorted).

    Returns ``()`` when the code is absent or is not an INSTRUMENT node — a
    name with no graph support contributes zero resonance (conservative). The
    family id is the ``provenance_ref`` of each incident edge and of each
    connected non-stock neighbour node; blanks are ignored.
    """
    if code not in graph:
        return ()
    if graph.nodes[code].get("node_type") != NodeType.INSTRUMENT.value:
        return ()

    families: set[str] = set()

    def _add(ref: object) -> None:
        if isinstance(ref, str) and ref.strip():
            families.add(ref)

    # Every edge incident to the stock (either direction) is a supporting link;
    # its provenance + the connected NON-stock neighbour's provenance are the
    # family ids.
    for _u, _v, data in graph.in_edges(code, data=True):
        _add(data.get("provenance_ref"))
    for _u, _v, data in graph.out_edges(code, data=True):
        _add(data.get("provenance_ref"))
    for neighbour in set(graph.predecessors(code)) | set(graph.successors(code)):
        if neighbour == code:
            continue
        # A peer STOCK's own creation provenance is NOT an independent logic
        # supporting THIS name — counting it would let a single stock-stock
        # edge (e.g. CORRELATES_WITH) clear the ≥2 resonance gate on its own
        # (codex P2). For a peer instrument only the incident-edge provenance
        # (added above) counts; supporting EVENT/CONCEPT/TREND nodes still add
        # their node provenance.
        if graph.nodes[neighbour].get("node_type") == NodeType.INSTRUMENT.value:
            continue
        _add(graph.nodes[neighbour].get("provenance_ref"))

    return tuple(sorted(families))


__all__ = ["evidence_family_ids"]
