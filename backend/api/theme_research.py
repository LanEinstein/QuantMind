"""Z-002 — read-only industry-chain reverse-deduction viz surface.

Surfaces the pinned industry-chain knowledge graph (Y-001 seed:
趋势→板块→产业链环节→产品→标的 + deterministic choke-point centrality) so the
``InstructionPlans`` page can render an explainable chain graph
(P1-5-amendment-2026-06-01 §1.2 direction①). The quant path stays the
qualification authority — this layer is **display-only**, never filters the
universe and never vetoes a sector.

Red lines:

* GET only — :func:`scripts/redline-check.sh` keeps the global write-endpoint
  allowlist (only the 2 locked POSTs) so this module may add no write surface.
* No ``backend.{llm,agents,risk,broker,data}`` imports — only the read-only
  ``backend.knowledge_graph`` (which itself imports no runtime trading stack)
  and the file-backed ``backend.theme_research`` pin registry.
* When the KG db is absent (not seeded) or unreadable the endpoint surfaces
  ``available=False`` and never 500s — the panel renders an empty state.
* The KG is opened READ-ONLY by path existence check first: a GET never
  creates the sqlite file (``SqliteKGStore`` would otherwise bootstrap one).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from backend.knowledge_graph.centrality import chokepoint_scores
from backend.knowledge_graph.schema import EdgeType, NodeType
from backend.knowledge_graph.store import SqliteKGStore
from backend.theme_research.candidate_registry import (
    ThemeCandidateRegistry,
    ThemeCandidateRegistryError,
)

log = logging.getLogger("backend.api.theme_research")

router = APIRouter(tags=["theme_research"])

# Repo-root-relative defaults (overridable via app.state for tests / wiring).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_KG_DB = _REPO_ROOT / "data" / "knowledge_graph" / "kg.sqlite3"
_DEFAULT_THEME_LOCK = _REPO_ROOT / "config" / "theme_candidates.lock.json"

# The industry-chain subgraph lives on these node/edge types only — the broader
# KG (811 factors / heuristics / source docs) is deliberately excluded.
_CHAIN_NODE_TYPES: frozenset[NodeType] = frozenset(
    {
        NodeType.TREND,
        NodeType.SECTOR,
        NodeType.CHAIN_LINK,
        NodeType.PRODUCT,
        NodeType.INSTRUMENT,
    }
)
# Chain-exclusive edges define membership (drag SECTOR/INSTRUMENT into the
# subgraph); BELONGS_TO is rendered only BETWEEN already-included chain nodes so
# an unrelated base-KG INSTRUMENT→SECTOR link can never leak in.
_CHAIN_MEMBERSHIP_EDGES: frozenset[EdgeType] = frozenset(
    {
        EdgeType.DRIVES,
        EdgeType.REQUIRES,
        EdgeType.UPSTREAM_OF,
        EdgeType.SUPPLIES_PRODUCT,
        EdgeType.MEMBER_OF,
    }
)
_CHAIN_RENDER_EDGES: frozenset[EdgeType] = _CHAIN_MEMBERSHIP_EDGES | frozenset(
    {EdgeType.BELONGS_TO}
)
# Chain-exclusive node types are always included even if isolated.
_CHAIN_EXCLUSIVE_NODE_TYPES: frozenset[NodeType] = frozenset(
    {NodeType.TREND, NodeType.CHAIN_LINK, NodeType.PRODUCT}
)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _kg_db_path(request: Request) -> Path:
    override = getattr(request.app.state, "kg_db_path", None)
    return Path(override) if override else _DEFAULT_KG_DB


def _theme_lock_path(request: Request) -> Path:
    override = getattr(request.app.state, "theme_candidate_lock_path", None)
    return Path(override) if override else _DEFAULT_THEME_LOCK


def _pinned_candidate_count(lock_path: Path) -> int:
    """Count human-pinned theme candidates (empty bootstrap = 0 = deny-all)."""
    if not lock_path.is_file():
        return 0
    try:
        registry = ThemeCandidateRegistry.from_lockfile(lock_path)
    except ThemeCandidateRegistryError:
        # Malformed lock = fail-closed to 0 pinned (never surface a half-trusted
        # count). The chain graph below is unaffected.
        log.warning("theme_candidate_lock_unreadable", extra={"path": str(lock_path)})
        return 0
    return len(registry.approved)


def _empty_payload(*, available: bool, note: str, pinned: int) -> dict[str, Any]:
    return {
        "available": available,
        "note": note,
        "node_count": 0,
        "edge_count": 0,
        "chain_link_count": 0,
        "nodes": [],
        "edges": [],
        "chokepoints": [],
        "theme_peer_sourcing": {
            "pinned_candidate_count": pinned,
            "note": (
                "主题研究 peer-sourcing 运行期接线待 owner 重启;无 pin 候选时"
                "纯量化路径照常运行(配额空)。"
            ),
        },
    }


def _extract_chain(graph: Any) -> dict[str, Any]:
    """Project the industry-chain subgraph + choke-point scores for the viz."""
    node_type = {n: d.get("node_type") for n, d in graph.nodes(data=True)}

    # 1) Membership: chain-exclusive-type nodes + endpoints of chain-exclusive
    #    edges (deterministic, no iteration needed — every chain edge has at
    #    least one chain-exclusive-type endpoint by the seed's construction).
    included: set[str] = {
        n
        for n, t in node_type.items()
        if t in {nt.value for nt in _CHAIN_EXCLUSIVE_NODE_TYPES}
    }
    membership_values = {e.value for e in _CHAIN_MEMBERSHIP_EDGES}
    for u, v, d in graph.edges(data=True):
        if d.get("edge_type") in membership_values:
            included.add(u)
            included.add(v)

    chain_node_values = {nt.value for nt in _CHAIN_NODE_TYPES}
    included = {n for n in included if node_type.get(n) in chain_node_values}

    # 2) Choke-point scores come from the FULL graph (the function internally
    #    filters to CHAIN_LINK + UPSTREAM_OF) so we never perturb its topology.
    scores = chokepoint_scores(graph)

    nodes: list[dict[str, Any]] = []
    for n in sorted(included):
        d = graph.nodes[n]
        score = scores.get(n)
        nodes.append(
            {
                "node_id": n,
                "node_type": d.get("node_type"),
                "name": d.get("name", n),
                "attrs": dict(d.get("attrs", {}) or {}),
                "chokepoint": (score._asdict() if score is not None else None),
            }
        )

    render_values = {e.value for e in _CHAIN_RENDER_EDGES}
    edges: list[dict[str, Any]] = []
    for u, v, key, d in graph.edges(keys=True, data=True):
        if d.get("edge_type") not in render_values:
            continue
        if u not in included or v not in included:
            continue
        edges.append(
            {
                "edge_id": str(key),
                "edge_type": d.get("edge_type"),
                "src_id": u,
                "dst_id": v,
            }
        )
    edges.sort(key=lambda e: (e["edge_type"], e["src_id"], e["dst_id"]))

    chain_link_value = NodeType.CHAIN_LINK.value
    chokepoints = [
        {
            "node_id": n["node_id"],
            "name": n["name"],
            **n["chokepoint"],
        }
        for n in nodes
        if n["node_type"] == chain_link_value and n["chokepoint"] is not None
    ]
    chokepoints.sort(key=lambda c: (-c["composite"], c["node_id"]))

    return {
        "nodes": nodes,
        "edges": edges,
        "chokepoints": chokepoints,
        "chain_link_count": sum(
            1 for n in nodes if n["node_type"] == chain_link_value
        ),
    }


@router.get("/api/theme-research/industry-chain")
async def get_industry_chain(request: Request) -> dict[str, Any]:
    """Return the pinned industry-chain subgraph + choke-point ranking.

    Display-only (P1-5-amendment-2026-06-01 §1.2). Returns ``available=False``
    with empty collections when the KG has not been seeded — the GET never
    creates the sqlite file.
    """
    pinned = _pinned_candidate_count(_theme_lock_path(request))
    db_path = _kg_db_path(request)
    if not db_path.is_file():
        return _ok(
            _empty_payload(
                available=False,
                note="产业链知识图谱尚未物化(运行 scripts/seed_kg.py 后可见)。",
                pinned=pinned,
            )
        )

    store: SqliteKGStore | None = None
    try:
        store = SqliteKGStore(db_path)
        graph = store.to_networkx()
        projected = _extract_chain(graph)
    except Exception:  # noqa: BLE001 — read endpoint never 500s (house style)
        log.exception("industry_chain_read_failed", extra={"path": str(db_path)})
        return _ok(
            _empty_payload(
                available=False,
                note="产业链知识图谱读取失败(已记录,fail-closed 不报 500)。",
                pinned=pinned,
            )
        )
    finally:
        if store is not None:
            store.close()

    return _ok(
        {
            "available": True,
            "note": "",
            "node_count": len(projected["nodes"]),
            "edge_count": len(projected["edges"]),
            "chain_link_count": projected["chain_link_count"],
            "nodes": projected["nodes"],
            "edges": projected["edges"],
            "chokepoints": projected["chokepoints"],
            "theme_peer_sourcing": {
                "pinned_candidate_count": pinned,
                "note": (
                    "主题研究 peer-sourcing 运行期接线待 owner 重启;量化仍是"
                    "资格权威,本链路 display-only 永不剪 universe / 否决板块。"
                ),
            },
        }
    )


__all__ = ["router"]
