"""Industry-chain cold-start seed (Y-001) — reconstructed, not copied.

The only open A-share industry-chain dataset is ``liuhuanyong/Chain
KnowledgeGraph``, which is UNLICENSED (NOASSERTION). Mirroring the Q-002
license discipline (the unlicensed repo's code/JSON is never copied), this
seeds a small but REAL semiconductor-localization chain RECONSTRUCTED from
public-domain knowledge (the "卡脖子" panorama + equipment-localization
research surveyed in
``docs/research/industry-chain-reverse-deduction-2026-06-01.md`` §1.4/§5).

Pipeline shape (dossier §4): 趋势 -DRIVES-> 板块 -REQUIRES-> 产业链环节
<-UPSTREAM_OF- 上游环节; 标的 -SUPPLIES_PRODUCT-> 产品 -MEMBER_OF-> 环节;
标的 -BELONGS_TO-> 板块. Every reconstructed node carries provenance: a
``provenance_ref`` pinning the seed artifact sha256 + a DERIVED_FROM edge to
its SourceDoc (an unsourced node is low-trust, dossier §1.4). criticality
values are research-sourced, so each ChainLink ALSO derives from the
choke-point panorama doc — making the "卡脖子" basis auditable.

Purely offline + zero-LLM (criticality is human/research input, not LLM
output) + import-isolated. The seed builds STRUCTURE only; choke-point
scores are DERIVED on demand by ``backend.knowledge_graph.centrality``.
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

import structlog

from backend.knowledge_graph.schema import (
    AttrValue,
    EdgeType,
    KGEdge,
    KGNode,
    NodeType,
)
from backend.knowledge_graph.store import SqliteKGStore

log = structlog.get_logger(__name__)

_DOC_CHAIN = "sourcedoc:chainknowledgegraph"
_DOC_PANORAMA = "sourcedoc:semiconductor-chokepoint-panorama"


class _Node(NamedTuple):
    node_id: str
    node_type: NodeType
    name: str
    attrs: dict[str, AttrValue]
    source_doc_id: str


class _Edge(NamedTuple):
    edge_id: str
    edge_type: EdgeType
    src_id: str
    dst_id: str
    attrs: dict[str, AttrValue]


# -- the reconstructed semiconductor-localization chain ------------------------

_TREND = "trend:semiconductor-localization"
_SEC_SEMI = "sector:semiconductor"
_SEC_EQUIP = "sector:semiconductor-equipment"

# layer/criticality/substitution_difficulty: research inputs (panorama §1.4),
# NOT LLM output and NOT a derived score (chokepoint_score is derived later).
_CHAIN_LINKS: tuple[tuple[str, str, str, float, float], ...] = (
    # (id, name, layer, criticality, substitution_difficulty)
    ("chainlink:lithography-machine", "光刻机", "上游", 0.95, 0.95),
    ("chainlink:photoresist", "光刻胶", "上游", 0.85, 0.80),
    ("chainlink:eda-tools", "EDA 工具", "上游", 0.90, 0.90),
    ("chainlink:electrostatic-chuck", "静电吸盘", "上游", 0.80, 0.85),
    ("chainlink:etching-equipment", "刻蚀设备", "中游", 0.55, 0.45),
    ("chainlink:wafer-fab", "晶圆制造", "中游", 0.70, 0.60),
    ("chainlink:packaging-test", "封装测试", "下游", 0.35, 0.25),
)

# UPSTREAM_OF: upstream link supplies into downstream link.
_UPSTREAM: tuple[tuple[str, str], ...] = (
    ("chainlink:lithography-machine", "chainlink:wafer-fab"),
    ("chainlink:photoresist", "chainlink:wafer-fab"),
    ("chainlink:eda-tools", "chainlink:wafer-fab"),
    ("chainlink:electrostatic-chuck", "chainlink:etching-equipment"),
    ("chainlink:etching-equipment", "chainlink:wafer-fab"),
    ("chainlink:wafer-fab", "chainlink:packaging-test"),
)

_PRODUCTS: tuple[tuple[str, str, str], ...] = (
    # (id, name, member_of_chainlink)
    ("product:arf-photoresist", "ArF 光刻胶", "chainlink:photoresist"),
    ("product:dielectric-etcher", "介质刻蚀机", "chainlink:etching-equipment"),
)

# A-share chain participants — main-board codes (the KG documents industry
# STRUCTURE; the tradable universe + exclusion-four-piece apply later at
# screening, not here). (code, name, supplies_product, belongs_to_sector)
_INSTRUMENTS: tuple[tuple[str, str, str, str], ...] = (
    ("instrument:002371", "北方华创", "product:dielectric-etcher", _SEC_EQUIP),
    ("instrument:002409", "雅克科技", "product:arf-photoresist", _SEC_SEMI),
)


class IndustryChainSeedReport(NamedTuple):
    """The chain this seed DEFINES (node/edge counts of a fresh cold-start).

    ``edges`` counts STRUCTURAL chain edges only (DRIVES/REQUIRES/UPSTREAM_OF/
    SUPPLIES_PRODUCT/MEMBER_OF/BELONGS_TO); the DERIVED_FROM provenance edges
    (one per chain node + one choke-point edge per ChainLink) live in
    ``total_edges`` so the CLI/audit reports the full materialised graph
    rather than under-counting (codex P3).
    """

    trends: int
    sectors: int
    chain_links: int
    products: int
    instruments: int
    source_docs: int
    edges: int

    @property
    def chain_nodes(self) -> int:
        return (
            self.trends
            + self.sectors
            + self.chain_links
            + self.products
            + self.instruments
        )

    @property
    def total_nodes(self) -> int:
        """All nodes a fresh run materialises (chain nodes + SourceDocs)."""
        return self.chain_nodes + self.source_docs

    @property
    def total_edges(self) -> int:
        """Structural + provenance edges a fresh run materialises: one
        DERIVED_FROM per chain node + one choke-point edge per ChainLink."""
        return self.edges + self.chain_nodes + self.chain_links


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _build_nodes() -> tuple[_Node, ...]:
    nodes: list[_Node] = [
        _Node(_TREND, NodeType.TREND, "半导体国产替代",
              {"horizon": "multi-year", "confidence": 0.7}, _DOC_PANORAMA),
        _Node(_SEC_SEMI, NodeType.SECTOR, "半导体",
              {"src": "SW", "level": "L2"}, _DOC_CHAIN),
        _Node(_SEC_EQUIP, NodeType.SECTOR, "半导体设备",
              {"src": "SW", "level": "L3"}, _DOC_CHAIN),
    ]
    for cid, name, layer, crit, sub in _CHAIN_LINKS:
        nodes.append(
            _Node(cid, NodeType.CHAIN_LINK, name,
                  {"layer": layer, "criticality": crit,
                   "substitution_difficulty": sub}, _DOC_CHAIN)
        )
    for pid, name, _member in _PRODUCTS:
        nodes.append(
            _Node(pid, NodeType.PRODUCT, name, {"category": "材料/设备"},
                  _DOC_CHAIN)
        )
    for code, name, _prod, _sec in _INSTRUMENTS:
        nodes.append(
            _Node(code, NodeType.INSTRUMENT, name,
                  {"board": "main", "ts_code": code.split(":", 1)[1]},
                  _DOC_CHAIN)
        )
    return tuple(nodes)


def _build_edges() -> tuple[_Edge, ...]:
    edges: list[_Edge] = [
        _Edge("drives:semi", EdgeType.DRIVES, _TREND, _SEC_SEMI, {}),
        _Edge("drives:equip", EdgeType.DRIVES, _TREND, _SEC_EQUIP, {}),
    ]
    # Beneficiary sector reverse-deduces its UPSTREAM necessities (倒推) — a
    # downstream consumer (下游, e.g. 封装测试) is NOT a required upstream input,
    # so it gets no REQUIRES edge (it stays reachable via UPSTREAM_OF).
    for cid, _n, layer, _c, _s in _CHAIN_LINKS:
        if layer == "下游":
            continue
        edges.append(
            _Edge(f"requires:{cid}", EdgeType.REQUIRES, _SEC_EQUIP, cid, {})
        )
    for i, (src, dst) in enumerate(_UPSTREAM):
        edges.append(
            _Edge(f"upstream:{i}", EdgeType.UPSTREAM_OF, src, dst, {})
        )
    for pid, _name, member in _PRODUCTS:
        edges.append(
            _Edge(f"member:{pid}", EdgeType.MEMBER_OF, pid, member, {})
        )
    for code, _name, prod, sec in _INSTRUMENTS:
        edges.append(
            _Edge(f"supplies:{code}", EdgeType.SUPPLIES_PRODUCT, code, prod, {})
        )
        edges.append(
            _Edge(f"belongs:{code}", EdgeType.BELONGS_TO, code, sec, {})
        )
    return tuple(edges)


def _artifact_hash(nodes: tuple[_Node, ...], edges: tuple[_Edge, ...]) -> str:
    """Canonical hash of the FULL reconstructed records (nodes + edges).

    Any change to structure or criticality shifts the anchor — two different
    chain seeds can never share one provenance hash (Q-002 discipline).
    """
    payload = {
        "nodes": [n._asdict() for n in nodes],
        "edges": [e._asdict() for e in edges],
    }
    return _sha256_bytes(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def _add_node_if_absent(store: SqliteKGStore, node: KGNode) -> bool:
    """Add ``node`` ONLY if its id is not already in the graph.

    WHY (codex P2): these chain ids (e.g. ``sector:semiconductor``,
    ``instrument:002371``) can also be created by the human-gated ingest
    pipeline. Re-adding would append a latest version carrying only seed
    attrs/provenance, masking approved data. The cold-start seed must
    bootstrap MISSING nodes, never overwrite an existing one.
    """
    if store.get_node(node.node_id) is not None:
        return False
    store.add_node(node)
    return True


def _add_edge_if_absent(store: SqliteKGStore, edge: KGEdge) -> bool:
    """Add ``edge`` ONLY if its id is not already in the graph.

    Symmetric to ``_add_node_if_absent`` (codex P2): an approved correction
    or a prior materialised chain edge sharing this id (e.g. ``upstream:0``)
    would otherwise be silently superseded by appending a seed version, since
    ``get_edge``/``to_networkx`` resolve the current edge by latest version.
    """
    if store.get_edge(edge.edge_id) is not None:
        return False
    store.add_edge(edge)
    return True


def seed_industry_chain(store: SqliteKGStore) -> IndustryChainSeedReport:
    """Seed the reconstructed semiconductor chain; fill-missing + idempotent.

    Nodes are added only when absent (never clobbering ingest-approved nodes,
    codex P2); structural relations are appended (idempotent in the current
    view). The returned report describes the chain this seed DEFINES.
    """
    nodes = _build_nodes()
    edges = _build_edges()
    chain_hash = _artifact_hash(nodes, edges)

    # SourceDocs first (DERIVED_FROM targets must exist before the edge).
    _add_node_if_absent(store, KGNode(
        node_id=_DOC_CHAIN, node_type=NodeType.SOURCE_DOC,
        name="liuhuanyong/ChainKnowledgeGraph (A 股产业链图谱底料,重建非拷贝)",
        attrs={
            "url": "https://github.com/liuhuanyong/ChainKnowledgeGraph",
            "license": "NOASSERTION",
            "note": "structure reconstructed from public-domain industry "
                    "knowledge; repo code/JSON NOT copied (Q-002 discipline)",
            "content_sha256": chain_hash,
        },
    ))
    _add_node_if_absent(store, KGNode(
        node_id=_DOC_PANORAMA, node_type=NodeType.SOURCE_DOC,
        name="半导体卡脖子国产替代全景 (研报/政策抽取,人工 gate)",
        attrs={
            "url": "https://www.hvchan.com/news/view.html?id=574",
            "note": "criticality / substitution_difficulty are research "
                    "inputs under human gate — never LLM-written decisions",
            "content_sha256": chain_hash,
        },
    ))

    for node in nodes:
        provenance = f"{node.source_doc_id}#sha256:{chain_hash}"
        # Provenance edges are tied to the seed CREATING the node; if it
        # already exists, its own provenance (e.g. ingest's) is authoritative.
        if not _add_node_if_absent(store, KGNode(
            node_id=node.node_id, node_type=node.node_type, name=node.name,
            attrs=node.attrs, provenance_ref=provenance,
        )):
            continue
        _add_edge_if_absent(store, KGEdge(
            edge_id=f"derived:{node.node_id}", edge_type=EdgeType.DERIVED_FROM,
            src_id=node.node_id, dst_id=node.source_doc_id,
            provenance_ref=provenance,
        ))
        # criticality basis is auditable: each newly-seeded ChainLink also
        # derives from the choke-point panorama (a distinctly-keyed edge).
        if node.node_type == NodeType.CHAIN_LINK:
            _add_edge_if_absent(store, KGEdge(
                edge_id=f"derived-chokepoint:{node.node_id}",
                edge_type=EdgeType.DERIVED_FROM,
                src_id=node.node_id, dst_id=_DOC_PANORAMA,
                provenance_ref=f"{_DOC_PANORAMA}#sha256:{chain_hash}",
            ))
    for edge in edges:
        _add_edge_if_absent(store, KGEdge(
            edge_id=edge.edge_id, edge_type=edge.edge_type,
            src_id=edge.src_id, dst_id=edge.dst_id, attrs=edge.attrs,
            provenance_ref=f"{_DOC_CHAIN}#sha256:{chain_hash}",
        ))

    report = IndustryChainSeedReport(
        trends=sum(1 for n in nodes if n.node_type == NodeType.TREND),
        sectors=sum(1 for n in nodes if n.node_type == NodeType.SECTOR),
        chain_links=sum(1 for n in nodes if n.node_type == NodeType.CHAIN_LINK),
        products=sum(1 for n in nodes if n.node_type == NodeType.PRODUCT),
        instruments=sum(1 for n in nodes if n.node_type == NodeType.INSTRUMENT),
        source_docs=2,
        edges=len(edges),
    )
    log.info("kg_industry_chain_seed_complete", **report._asdict())
    return report


__all__ = ["IndustryChainSeedReport", "seed_industry_chain"]
