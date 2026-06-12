"""Z-002 — backend/api/theme_research.py tests (industry-chain viz surface)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.theme_research import router as theme_research_router
from backend.knowledge_graph.schema import EdgeType, KGEdge, KGNode, NodeType
from backend.knowledge_graph.store import SqliteKGStore


def _node(node_id: str, node_type: NodeType, name: str) -> KGNode:
    return KGNode(node_id=node_id, node_type=node_type, name=name)


def _edge(edge_id: str, edge_type: EdgeType, src: str, dst: str) -> KGEdge:
    return KGEdge(edge_id=edge_id, edge_type=edge_type, src_id=src, dst_id=dst)


def _seed_chain_kg(db_path: Path) -> None:
    """Build a tiny industry-chain KG + one non-chain factor (exclusion proof)."""
    store = SqliteKGStore(db_path)
    try:
        for node in (
            _node("trend:semi", NodeType.TREND, "半导体国产替代"),
            _node("sector:semi", NodeType.SECTOR, "半导体"),
            _node("link:litho", NodeType.CHAIN_LINK, "光刻"),
            _node("link:photoresist", NodeType.CHAIN_LINK, "光刻胶"),
            _node("prod:arf", NodeType.PRODUCT, "ArF光刻胶"),
            _node("inst:002371", NodeType.INSTRUMENT, "北方华创"),
            # Non-chain noise — must be EXCLUDED from the projection.
            _node("factor:a001", NodeType.FACTOR, "ALPHA001"),
            _node("doc:1", NodeType.SOURCE_DOC, "研报A"),
        ):
            store.add_node(node)
        for edge in (
            _edge("e:drives", EdgeType.DRIVES, "trend:semi", "sector:semi"),
            _edge("e:req", EdgeType.REQUIRES, "sector:semi", "link:litho"),
            # photoresist is upstream of litho -> photoresist reaches litho.
            _edge("e:up", EdgeType.UPSTREAM_OF, "link:photoresist", "link:litho"),
            _edge("e:supp", EdgeType.SUPPLIES_PRODUCT, "inst:002371", "prod:arf"),
            _edge("e:mem", EdgeType.MEMBER_OF, "prod:arf", "link:photoresist"),
            _edge("e:belong", EdgeType.BELONGS_TO, "inst:002371", "sector:semi"),
            # Non-chain edge — must be excluded from the projection.
            _edge("e:prov", EdgeType.DERIVED_FROM, "factor:a001", "doc:1"),
        ):
            store.add_edge(edge)
    finally:
        store.close()


def _build_app(
    *, kg_db_path: Path | None, theme_lock_path: Path | None = None
) -> FastAPI:
    app = FastAPI()
    if kg_db_path is not None:
        app.state.kg_db_path = str(kg_db_path)
    if theme_lock_path is not None:
        app.state.theme_candidate_lock_path = str(theme_lock_path)
    app.include_router(theme_research_router)
    return app


async def _get_chain(app: FastAPI) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/theme-research/industry-chain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


@pytest.mark.asyncio
async def test_unavailable_when_kg_absent(tmp_path: Path) -> None:
    missing = tmp_path / "nope.sqlite3"
    data = await _get_chain(_build_app(kg_db_path=missing))
    assert data["available"] is False
    assert data["nodes"] == []
    assert data["chokepoints"] == []
    # A GET must NOT create the sqlite file (read-only surface).
    assert not missing.exists()


@pytest.mark.asyncio
async def test_chain_projection_excludes_non_chain(tmp_path: Path) -> None:
    db = tmp_path / "kg.sqlite3"
    _seed_chain_kg(db)
    data = await _get_chain(_build_app(kg_db_path=db))

    assert data["available"] is True
    node_ids = {n["node_id"] for n in data["nodes"]}
    assert node_ids == {
        "trend:semi",
        "sector:semi",
        "link:litho",
        "link:photoresist",
        "prod:arf",
        "inst:002371",
    }
    # The factor + source doc + provenance edge are NOT in the projection.
    assert "factor:a001" not in node_ids
    assert "doc:1" not in node_ids
    edge_types = {e["edge_type"] for e in data["edges"]}
    assert "DERIVED_FROM" not in edge_types
    assert edge_types == {
        "DRIVES",
        "REQUIRES",
        "UPSTREAM_OF",
        "SUPPLIES_PRODUCT",
        "MEMBER_OF",
        "BELONGS_TO",
    }


@pytest.mark.asyncio
async def test_chokepoint_ranking_upstream_dominates(tmp_path: Path) -> None:
    db = tmp_path / "kg.sqlite3"
    _seed_chain_kg(db)
    data = await _get_chain(_build_app(kg_db_path=db))

    assert data["chain_link_count"] == 2
    cps = data["chokepoints"]
    # The upstream source (photoresist) ranks first by downstream reach.
    assert cps[0]["node_id"] == "link:photoresist"
    photoresist = next(c for c in cps if c["node_id"] == "link:photoresist")
    assert photoresist["downstream_reach"] >= cps[-1]["downstream_reach"]
    assert 0.0 <= photoresist["composite"] <= 1.0


@pytest.mark.asyncio
async def test_pinned_candidate_count_from_lock(tmp_path: Path) -> None:
    db = tmp_path / "kg.sqlite3"
    _seed_chain_kg(db)
    lock = tmp_path / "theme_candidates.lock.json"
    lock.write_text(
        json.dumps(
            {
                "version": "1.0",
                "updated_at": "2026-06-12T00:00:00+00:00",
                "approved": ["a" * 64, "b" * 64],
            }
        ),
        encoding="utf-8",
    )
    data = await _get_chain(_build_app(kg_db_path=db, theme_lock_path=lock))
    assert data["theme_peer_sourcing"]["pinned_candidate_count"] == 2


@pytest.mark.asyncio
async def test_missing_lock_means_zero_pinned(tmp_path: Path) -> None:
    db = tmp_path / "kg.sqlite3"
    _seed_chain_kg(db)
    app = _build_app(kg_db_path=db, theme_lock_path=tmp_path / "absent.json")
    data = await _get_chain(app)
    assert data["theme_peer_sourcing"]["pinned_candidate_count"] == 0


def test_router_is_get_only() -> None:
    source = Path("backend/api/theme_research.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(
                    deco.func, ast.Attribute
                ):
                    if deco.func.attr in write_verbs:
                        seen.append(node.name)
    assert seen == []


def test_module_imports_no_trading_stack() -> None:
    """Display-only surface: no llm/agents/risk/broker/data imports."""
    source = Path("backend/api/theme_research.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"llm", "agents", "risk", "broker", "data", "mirofish"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in forbidden:
                bad.append(node.module)
    assert bad == []
