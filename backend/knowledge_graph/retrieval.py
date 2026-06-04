"""Offline read-only KG retrieval, LightRAG-style (Q-003).

Scope decision (recorded for the owner): the literal ``lightrag-hku``
library was evaluated and NOT adopted as a runtime dependency — its
``insert`` pipeline performs its own UNGATED LLM entity extraction,
which would bypass the Q-003 human gate and put an LLM in the graph's
write path (red line). What the dossier actually needs first is its
RETRIEVAL shape: dense similarity over entities plus local graph
expansion, strictly offline. That shape is implemented natively here
over our own store:

    query text --(injected embedder)--> top-k similar nodes ("local"
    seeds) --> one-hop neighbourhood expansion --> nodes + edges

Read-only by construction (never calls ``add_node``/``add_edge``);
zero LLM; the embedder is an injected Protocol (production wires the
local Qwen3-Embedding-0.6B exactly like exemplar_selector; tests inject
a deterministic stub — house pattern, P2-2 §2).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import NamedTuple, Protocol

import structlog

from backend.knowledge_graph.schema import KGEdge, KGNode
from backend.knowledge_graph.store import SqliteKGStore

log = structlog.get_logger(__name__)


class Embedder(Protocol):
    """Maps texts to fixed-dimension vectors (offline, injected)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class RetrievalHit(NamedTuple):
    node: KGNode
    score: float
    neighbours: tuple[KGNode, ...]
    edges: tuple[KGEdge, ...]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _node_text(node: KGNode) -> str:
    """The text a node is indexed under — name + scalar attrs."""
    parts = [node.name]
    parts.extend(f"{k}={v}" for k, v in sorted(node.attrs.items()) if v is not None)
    return " | ".join(parts)


class KGRetriever:
    """Offline dense + one-hop-graph retrieval over the CURRENT graph view.

    The index is built once per instance from a point-in-time snapshot of
    the store (``as_of`` optional) and never mutates the store. Rebuild by
    constructing a new instance — retrieval freshness is the caller's
    explicit choice, matching the "offline read-only first" boundary
    (live-debate use would additionally need index versioning + audit,
    deliberately out of Q-003 scope).
    """

    def __init__(
        self,
        store: SqliteKGStore,
        embedder: Embedder,
        *,
        as_of: datetime | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._graph = store.to_networkx(as_of=as_of)
        node_ids = sorted(self._graph.nodes)
        self._node_ids: list[str] = []
        texts: list[str] = []
        for node_id in node_ids:
            node = store.get_node(node_id, as_of=as_of)
            if node is None:  # pragma: no cover — view/store race-free here
                continue
            self._node_ids.append(node_id)
            texts.append(_node_text(node))
        self._vectors = self._embedder.embed(texts) if texts else []
        self._as_of = as_of
        log.info("kg_retriever_indexed", nodes=len(self._node_ids))

    def query(self, text: str, *, k: int = 5) -> tuple[RetrievalHit, ...]:
        """Top-k similar nodes, each with its one-hop neighbourhood."""
        if k <= 0 or not self._node_ids:
            return ()
        [query_vec] = self._embedder.embed([text])
        scored = sorted(
            (
                (_cosine(query_vec, vec), node_id)
                for node_id, vec in zip(self._node_ids, self._vectors, strict=True)
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )[:k]
        hits: list[RetrievalHit] = []
        for score, node_id in scored:
            node = self._store.get_node(node_id, as_of=self._as_of)
            if node is None:  # pragma: no cover
                continue
            neighbour_ids = set(self._graph.successors(node_id)) | set(
                self._graph.predecessors(node_id)
            )
            neighbours = tuple(
                n
                for nid in sorted(neighbour_ids)
                if (n := self._store.get_node(nid, as_of=self._as_of)) is not None
            )
            edge_ids = {
                key
                for _, _, key in self._graph.in_edges(node_id, keys=True)
            } | {
                key
                for _, _, key in self._graph.out_edges(node_id, keys=True)
            }
            edges = tuple(
                e
                for eid in sorted(edge_ids)
                if (e := self._store.get_edge(eid, as_of=self._as_of)) is not None
            )
            hits.append(
                RetrievalHit(node=node, score=score, neighbours=neighbours, edges=edges)
            )
        return tuple(hits)


__all__ = ["Embedder", "KGRetriever", "RetrievalHit"]
