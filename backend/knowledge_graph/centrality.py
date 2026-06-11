"""Choke-point centrality on the industry-chain UPSTREAM_OF subgraph (Y-001).

A choke point is a chain link whose supply, if cut, hurts the most downstream
("断供→整链巨大负面") AND that many downstream links depend on (dossier
industry-chain-reverse-deduction-2026-06-01 §3.2; P0-8-amendment-2026-06-01
§2.10 — choke-point ≈ NetworkX centrality on the pinned UPSTREAM_OF subgraph,
no Neo4j). We derive it deterministically from topology only:

* downstream reach — # of links that lose supply if this one is cut (the
  direct magnitude of "整链负面"); the dominant signal;
* out-degree — # of directly dependent downstream links;
* betweenness — how often it sits on a shortest supply path (a bottleneck);
* reverse-graph PageRank — recursive importance as an upstream source.

WHY reach dominates: betweenness alone scores intermediate links above the
ultimate source, but the source whose failure starves the whole chain is the
true choke point. Reach captures that directly.

The score is a DERIVED feature — never written back onto the node (the graph
is a derived index; persisting it would let it drift from topology). It is
PIT-reproducible: the same pinned graph yields bit-for-bit identical scores
(deterministic build order + fixed-precision rounding).
"""

from __future__ import annotations

from typing import NamedTuple

import networkx as nx

from backend.knowledge_graph.schema import EdgeType, NodeType

# Round derived floats so replay across runs/processes is bit-exact.
_PRECISION = 9

# Composite weights (sum to 1.0). Downstream reach is the dominant term
# because "断供→整链负面" is fundamentally a reachability property; the rest
# corroborate (bottleneck / direct fan-out / recursive source importance).
_W_REACH = 0.5
_W_OUT_DEGREE = 0.1
_W_BETWEENNESS = 0.2
_W_PAGERANK = 0.2


class ChokePointScore(NamedTuple):
    """Per-chain-link choke-point components + composite, all in [0, 1]."""

    downstream_reach: float
    out_degree: float
    betweenness: float
    pagerank: float
    composite: float


def _upstream_digraph(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Deterministic simple DiGraph of CHAIN_LINK nodes + UPSTREAM_OF edges.

    Built with sorted node/edge insertion so every downstream computation
    (PageRank matrix order included) is reproducible.
    """
    chain_nodes = sorted(
        n
        for n, d in graph.nodes(data=True)
        if d.get("node_type") == NodeType.CHAIN_LINK.value
    )
    g: nx.DiGraph = nx.DiGraph()
    g.add_nodes_from(chain_nodes)
    members = set(chain_nodes)
    up_edges = sorted(
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == EdgeType.UPSTREAM_OF.value
        and u in members
        and v in members
    )
    g.add_edges_from(up_edges)
    return g


def _normalize_by_max(values: dict[str, float]) -> dict[str, float]:
    """Scale to [0, 1] by the maximum; all-zero (or empty) -> all zero."""
    hi = max(values.values(), default=0.0)
    if hi <= 0.0:
        return dict.fromkeys(values, 0.0)
    return {k: v / hi for k, v in values.items()}


def chokepoint_scores(graph: nx.MultiDiGraph) -> dict[str, ChokePointScore]:
    """Derive choke-point scores for every CHAIN_LINK node in ``graph``.

    Returns an empty dict when the graph has no chain links. Only
    UPSTREAM_OF edges among chain links shape the score — DRIVES/REQUIRES/
    BELONGS_TO etc. are deliberately excluded from the supply topology.
    """
    g = _upstream_digraph(graph)
    if g.number_of_nodes() == 0:
        return {}

    # Only links wired into the supply topology can be choke points. We run
    # EVERY centrality on the CONNECTED subgraph, so an isolated CHAIN_LINK
    # (no UPSTREAM_OF edge) can neither look like a choke point nor perturb a
    # connected link's score — NetworkX would otherwise leak isolated nodes
    # into PageRank teleport mass and betweenness normalisation, breaking the
    # topology-only contract (codex P2). Isolated links get a flat zero.
    zero = ChokePointScore(0.0, 0.0, 0.0, 0.0, 0.0)
    connected = sorted(n for n in g.nodes if g.degree(n) > 0)
    if not connected:
        return {n: zero for n in sorted(g.nodes)}
    gc = g.subgraph(connected).copy()

    reach = {n: float(len(nx.descendants(gc, n))) for n in gc.nodes}
    out_deg = {n: float(gc.out_degree(n)) for n in gc.nodes}
    betw = nx.betweenness_centrality(gc, normalized=True)  # exact (k=None)
    try:
        # Reverse the chain so PageRank rewards links many others trace UP to.
        prank = dict(nx.pagerank(gc.reverse(copy=True)))
    except nx.PowerIterationFailedConvergence:  # pragma: no cover - defensive
        prank = dict.fromkeys(gc.nodes, 0.0)

    reach_n = _normalize_by_max(reach)
    out_n = _normalize_by_max(out_deg)
    betw_n = _normalize_by_max(dict(betw))
    pr_n = _normalize_by_max(prank)

    scores: dict[str, ChokePointScore] = {}
    for n in sorted(g.nodes):
        if n not in gc:
            scores[n] = zero
            continue
        r = round(reach_n[n], _PRECISION)
        o = round(out_n[n], _PRECISION)
        b = round(betw_n[n], _PRECISION)
        p = round(pr_n[n], _PRECISION)
        composite = round(
            _W_REACH * r
            + _W_OUT_DEGREE * o
            + _W_BETWEENNESS * b
            + _W_PAGERANK * p,
            _PRECISION,
        )
        scores[n] = ChokePointScore(
            downstream_reach=r,
            out_degree=o,
            betweenness=b,
            pagerank=p,
            composite=composite,
        )
    return scores


__all__ = ["ChokePointScore", "chokepoint_scores"]
