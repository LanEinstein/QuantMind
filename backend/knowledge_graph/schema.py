"""KG schema — 9 node / 12 edge types, frozen Pydantic v2 strict (Q-001).

Source of truth: ``docs/research/knowledge-graph-and-anomaly-detection.md``
§1.4 (locked by P2-2-amendment-2026-05-24). The schema is deliberately
generic-attributed: every node/edge carries a small set of REQUIRED common
fields (identity, type, bitemporal stamps, provenance) plus a validated
scalar ``attrs`` map for the per-type keys the dossier lists (e.g. a
``Strategy``'s ``family``, a ``BacktestResult``'s ``max_drawdown``). WHY:
the per-type key sets will grow with Q-002 seeding and Q-003 ingest; the
invariants worth freezing in code are identity, time, provenance and edge
endpoint legality — not each evolving attribute list.

Bitemporality (Graphiti-style):
* ``t_valid``  — when the fact became true in the DOMAIN (e.g. the day a
  strategy was retired). ``None`` = "since always / unknown".
* ``t_ingest`` — when this VERSION row was written to the store. Assigned
  by the store, monotonically per node/edge; never client-supplied trust.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator

# Scalar-only attribute values: keeps every version row JSON-serialisable,
# diffable and hash-stable (no nested mutable structures to drift).
AttrValue = str | int | float | bool | None


class NodeType(StrEnum):
    """The KG node labels — original 9 (dossier §1.4) + 3 industry-chain.

    The first 9 are the frozen base (P2-2-amendment-2026-05-24). The last 3
    (Trend/ChainLink/Product) were ADDED for the industry-chain reverse-
    deduction subcapability (P2-2-amendment-2026-06-11, Y-001); the addition
    only grows the set — no existing label changed. ``Stock`` reuses the
    existing ``Instrument`` label (chain attrs live in its ``attrs`` map).
    """

    STRATEGY = "Strategy"
    BACKTEST_RESULT = "BacktestResult"
    FACTOR = "Factor"
    CONCEPT = "Concept"
    SECTOR = "Sector"
    INSTRUMENT = "Instrument"
    HEURISTIC = "Heuristic"
    EVENT = "Event"
    SOURCE_DOC = "SourceDoc"
    # Industry-chain subcapability (P2-2-amendment-2026-06-11, Y-001).
    TREND = "Trend"
    CHAIN_LINK = "ChainLink"
    PRODUCT = "Product"


class EdgeType(StrEnum):
    """The KG relationship types — original 12 (dossier §1.4) + 5 chain.

    The last 5 (DRIVES/REQUIRES/UPSTREAM_OF/SUPPLIES_PRODUCT/MEMBER_OF) were
    ADDED for the industry-chain subcapability (P2-2-amendment-2026-06-11,
    Y-001); the existing 12 and their endpoint legality are unchanged. The
    "Stock belongs to Sector" relation reuses the existing ``BELONGS_TO``.
    """

    USES_FACTOR = "USES_FACTOR"
    BACKTESTED_AS = "BACKTESTED_AS"
    APPLIES_TO = "APPLIES_TO"
    BELONGS_TO = "BELONGS_TO"
    CORRELATES_WITH = "CORRELATES_WITH"
    EXPLAINS = "EXPLAINS"
    DEFINED_BY = "DEFINED_BY"
    RECOMMENDS_ACTION = "RECOMMENDS_ACTION"
    DERIVED_FROM = "DERIVED_FROM"
    SUPERSEDES = "SUPERSEDES"
    TRIGGERED_BY = "TRIGGERED_BY"
    AFFECTS = "AFFECTS"
    # Industry-chain subcapability (P2-2-amendment-2026-06-11, Y-001).
    DRIVES = "DRIVES"
    REQUIRES = "REQUIRES"
    UPSTREAM_OF = "UPSTREAM_OF"
    SUPPLIES_PRODUCT = "SUPPLIES_PRODUCT"
    MEMBER_OF = "MEMBER_OF"


class NodeStatus(StrEnum):
    """Strategy lifecycle (dossier §1.4); other node types stay ACTIVE.

    Retirement is a NEW VERSION ROW with status=RETIRED (+ a SUPERSEDES
    edge when a successor exists) — never a delete: retired nodes stay in
    the graph as queryable history (P2-2 rollback requirement).
    """

    CANDIDATE = "candidate"
    SHADOW = "shadow"
    ACTIVE = "active"
    RETIRED = "retired"


_ANY_SOURCE: Final[frozenset[NodeType]] = frozenset(NodeType)

# Edge endpoint legality (dossier §1.4 table). An edge whose (src, dst)
# node types fall outside this table is rejected at write time — schema
# drift fails fast instead of silently growing an untyped graph.
_Endpoints = tuple[frozenset[NodeType], frozenset[NodeType]]
EDGE_ENDPOINTS: Final[dict[EdgeType, _Endpoints]] = {
    EdgeType.USES_FACTOR: (
        frozenset({NodeType.STRATEGY}),
        frozenset({NodeType.FACTOR}),
    ),
    EdgeType.BACKTESTED_AS: (
        frozenset({NodeType.STRATEGY}),
        frozenset({NodeType.BACKTEST_RESULT}),
    ),
    EdgeType.APPLIES_TO: (
        frozenset({NodeType.STRATEGY}),
        frozenset({NodeType.INSTRUMENT, NodeType.SECTOR}),
    ),
    EdgeType.BELONGS_TO: (
        frozenset({NodeType.INSTRUMENT}),
        frozenset({NodeType.SECTOR}),
    ),
    EdgeType.CORRELATES_WITH: (
        frozenset({NodeType.INSTRUMENT}),
        frozenset({NodeType.INSTRUMENT}),
    ),
    EdgeType.EXPLAINS: (
        frozenset({NodeType.CONCEPT}),
        frozenset({NodeType.FACTOR}),
    ),
    EdgeType.DEFINED_BY: (
        frozenset({NodeType.FACTOR}),
        frozenset({NodeType.CONCEPT}),
    ),
    EdgeType.RECOMMENDS_ACTION: (
        frozenset({NodeType.HEURISTIC}),
        frozenset({NodeType.CONCEPT, NodeType.FACTOR}),
    ),
    # Provenance edge — every node SHOULD have one (a node without a
    # DERIVED_FROM edge is low-trust by convention, dossier §1.4).
    EdgeType.DERIVED_FROM: (
        _ANY_SOURCE,
        frozenset({NodeType.SOURCE_DOC}),
    ),
    EdgeType.SUPERSEDES: (
        frozenset({NodeType.STRATEGY}),
        frozenset({NodeType.STRATEGY}),
    ),
    EdgeType.TRIGGERED_BY: (
        frozenset({NodeType.EVENT}),
        frozenset({NodeType.INSTRUMENT}),
    ),
    EdgeType.AFFECTS: (
        frozenset({NodeType.EVENT}),
        frozenset({NodeType.SECTOR, NodeType.CONCEPT}),
    ),
    # -- industry-chain reverse deduction (P2-2-amendment-2026-06-11) --------
    # 趋势 -DRIVES-> 板块 -REQUIRES-> 产业链环节 <-UPSTREAM_OF- 上游环节;
    # 标的 -SUPPLIES_PRODUCT-> 产品 -MEMBER_OF-> 环节 (dossier §4).
    EdgeType.DRIVES: (
        frozenset({NodeType.TREND}),
        frozenset({NodeType.SECTOR}),
    ),
    EdgeType.REQUIRES: (
        frozenset({NodeType.SECTOR}),
        frozenset({NodeType.CHAIN_LINK}),
    ),
    EdgeType.UPSTREAM_OF: (
        frozenset({NodeType.CHAIN_LINK}),
        frozenset({NodeType.CHAIN_LINK}),
    ),
    EdgeType.SUPPLIES_PRODUCT: (
        frozenset({NodeType.INSTRUMENT}),
        frozenset({NodeType.PRODUCT}),
    ),
    EdgeType.MEMBER_OF: (
        frozenset({NodeType.PRODUCT}),
        frozenset({NodeType.CHAIN_LINK}),
    ),
}


class _FrozenStrict(BaseModel):
    """Project-standard model base: frozen + strict + extra forbidden."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class KGNode(_FrozenStrict):
    """One VERSION of a graph node (append-only versioning lives in the store).

    ``t_ingest`` is stamped by the store at write time; a client-supplied
    value is rejected there (single time authority — mirrors BrokerSnapshot's
    server-side stamping so bitemporal queries cannot be forged).
    """

    node_id: str
    node_type: NodeType
    name: str
    status: NodeStatus = NodeStatus.ACTIVE
    attrs: dict[str, AttrValue] = {}
    # Pointer into data/rag/provenance.jsonl (doc_id+sha256) — the file is
    # the single source of truth; the graph is a derived index (dossier §6.3).
    provenance_ref: str | None = None
    t_valid: datetime | None = None

    @field_validator("node_id", "name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v


class KGEdge(_FrozenStrict):
    """One VERSION of a directed edge between two node_ids."""

    edge_id: str
    edge_type: EdgeType
    src_id: str
    dst_id: str
    attrs: dict[str, AttrValue] = {}
    provenance_ref: str | None = None
    t_valid: datetime | None = None

    @field_validator("edge_id", "src_id", "dst_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v


__all__ = [
    "EDGE_ENDPOINTS",
    "AttrValue",
    "EdgeType",
    "KGEdge",
    "KGNode",
    "NodeStatus",
    "NodeType",
]
