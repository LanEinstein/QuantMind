"""Local knowledge graph (Phase Q) — SQLite + NetworkX, bitemporal, append-only.

WHY this module exists: P2-2-amendment-2026-05-24 promotes the deferred
self-evolution track to an active-discovery + knowledge-graph architecture.
The graph stores strategies / factors / heuristics / instruments and their
relations as queryable, *immutable* facts: promotion and retirement are
modelled with Graphiti-style bitemporality (``t_valid``/``t_ingest``) plus
``SUPERSEDES`` edges instead of destructive updates, so "strategy X was
retired on date D because its shadow run missed the P0-6 gate" is a graph
fact that can be queried, audited, and rolled back.

Red lines (enforced by tests in Q-004):
* import-isolated — never imports ``backend.{api,broker,risk,llm,agents,
  mirofish,data}``; the graph is a pure local artifact, NOT a runtime
  decision path.
* zero LLM — decision-bearing fields are never LLM-written; LLM text may
  only ever land in explicitly-labelled summary/rationale attributes via
  the (Q-003, human-gated) ingest pipeline.
* append-only — the store physically refuses UPDATE/DELETE on graph tables.
"""

from backend.knowledge_graph.centrality import ChokePointScore, chokepoint_scores
from backend.knowledge_graph.schema import (
    EDGE_ENDPOINTS,
    EdgeType,
    KGEdge,
    KGNode,
    NodeStatus,
    NodeType,
)
from backend.knowledge_graph.store import KnowledgeGraphStore, SqliteKGStore

__all__ = [
    "EDGE_ENDPOINTS",
    "ChokePointScore",
    "EdgeType",
    "KGEdge",
    "KGNode",
    "KnowledgeGraphStore",
    "NodeStatus",
    "NodeType",
    "SqliteKGStore",
    "chokepoint_scores",
]
