"""Q-001 — KG store: roundtrip, bitemporal queries, SUPERSEDES, append-only."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.knowledge_graph import (
    EDGE_ENDPOINTS,
    EdgeType,
    KGEdge,
    KGNode,
    NodeStatus,
    NodeType,
    SqliteKGStore,
)
from backend.knowledge_graph.store import KGSchemaError

_T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
_T1 = datetime(2026, 6, 2, 9, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 3, 9, 0, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteKGStore:
    return SqliteKGStore(tmp_path / "kg.sqlite3", now=_T0)


def _strategy(node_id: str = "strat:momo-1", **over: object) -> KGNode:
    base: dict = {
        "node_id": node_id,
        "node_type": NodeType.STRATEGY,
        "name": "dual momentum v1",
        "status": NodeStatus.CANDIDATE,
        "attrs": {"family": "trend"},
        "provenance_ref": "doc:abc#sha256:def",
    }
    base.update(over)
    return KGNode(**base)


def _factor(node_id: str = "factor:mom-20d") -> KGNode:
    return KGNode(
        node_id=node_id,
        node_type=NodeType.FACTOR,
        name="20d momentum",
        attrs={"category": "price"},
        provenance_ref="doc:abc#sha256:def",
    )


# -- schema -------------------------------------------------------------------


def test_schema_node_and_edge_type_counts() -> None:
    # Base 9 nodes / 12 edges (P2-2-amendment-2026-05-24) + industry-chain
    # 3 nodes / 5 edges (P2-2-amendment-2026-06-11, Y-001) = 12 / 17. The
    # set only grew — see test_industry_chain for the only-added invariant.
    assert len(NodeType) == 12
    assert len(EdgeType) == 17
    assert set(EDGE_ENDPOINTS) == set(EdgeType)


def test_models_are_frozen_and_strict() -> None:
    node = _strategy()
    with pytest.raises(ValidationError):
        node.name = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        KGNode(
            node_id="x", node_type=NodeType.FACTOR, name="f",
            unexpected="field",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        KGNode(node_id="  ", node_type=NodeType.FACTOR, name="f")


# -- roundtrip ------------------------------------------------------------------


def test_node_and_edge_roundtrip(store: SqliteKGStore) -> None:
    store.add_node(_strategy())
    store.add_node(_factor())
    store.add_edge(
        KGEdge(
            edge_id="e:uses-1",
            edge_type=EdgeType.USES_FACTOR,
            src_id="strat:momo-1",
            dst_id="factor:mom-20d",
            attrs={"weight": 1.0},
        )
    )
    node = store.get_node("strat:momo-1")
    assert node is not None
    assert node.attrs == {"family": "trend"}
    assert node.provenance_ref == "doc:abc#sha256:def"
    edge = store.get_edge("e:uses-1")
    assert edge is not None
    assert edge.edge_type is EdgeType.USES_FACTOR
    assert edge.attrs == {"weight": 1.0}


def test_edge_endpoint_legality_rejected(store: SqliteKGStore) -> None:
    store.add_node(_strategy())
    store.add_node(_factor())
    # USES_FACTOR demands Strategy -> Factor; Factor -> Strategy must fail.
    with pytest.raises(KGSchemaError):
        store.add_edge(
            KGEdge(
                edge_id="e:bad",
                edge_type=EdgeType.USES_FACTOR,
                src_id="factor:mom-20d",
                dst_id="strat:momo-1",
            )
        )


def test_edge_with_missing_endpoint_rejected(store: SqliteKGStore) -> None:
    store.add_node(_strategy())
    with pytest.raises(KGSchemaError):
        store.add_edge(
            KGEdge(
                edge_id="e:dangling",
                edge_type=EdgeType.USES_FACTOR,
                src_id="strat:momo-1",
                dst_id="factor:ghost",
            )
        )


# -- bitemporal -----------------------------------------------------------------


def test_bitemporal_as_of_replays_past_state(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3", now=_T0)
    store.add_node(_strategy(status=NodeStatus.CANDIDATE))
    store._fixed_now = _T1  # advance the injected clock
    store.add_node(_strategy(status=NodeStatus.ACTIVE))
    # Current view = latest version.
    now_node = store.get_node("strat:momo-1")
    assert now_node is not None and now_node.status is NodeStatus.ACTIVE
    # As-of T0 = the version ingested then (ingest-time travel).
    past = store.get_node("strat:momo-1", as_of=_T0)
    assert past is not None and past.status is NodeStatus.CANDIDATE
    # Domain time (t_valid) travels independently of ingest time.
    history = store.node_history("strat:momo-1")
    assert [s.status for s, _ in history] == [
        NodeStatus.CANDIDATE, NodeStatus.ACTIVE,
    ]
    assert [t for _, t in history] == [_T0, _T1]


# -- SUPERSEDES promotion/retirement ---------------------------------------------


def test_supersedes_retires_old_keeps_history(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3", now=_T0)
    store.add_node(_strategy("strat:v1", status=NodeStatus.ACTIVE))
    store.add_node(_strategy("strat:v2", name="dual momentum v2"))
    store._fixed_now = _T2
    store.supersede_strategy(
        old_id="strat:v1", new_id="strat:v2", t_valid=_T2,
        provenance_ref="doc:shadow-45d#sha256:123",
    )
    old = store.get_node("strat:v1")
    assert old is not None and old.status is NodeStatus.RETIRED
    assert old.t_valid == _T2
    edge = store.get_edge("supersedes:strat:v2->strat:v1")
    assert edge is not None and edge.edge_type is EdgeType.SUPERSEDES
    # The retired node REMAINS in the graph (no delete) with full history.
    g = store.to_networkx()
    assert "strat:v1" in g and "strat:v2" in g
    assert g.nodes["strat:v1"]["status"] == "retired"
    # Pre-retirement state is still queryable (rollback requirement).
    before = store.get_node("strat:v1", as_of=_T0)
    assert before is not None and before.status is NodeStatus.ACTIVE


def test_supersede_missing_node_rejected(store: SqliteKGStore) -> None:
    store.add_node(_strategy("strat:v1"))
    with pytest.raises(KGSchemaError):
        store.supersede_strategy(old_id="strat:v1", new_id="strat:ghost", t_valid=_T1)


# -- append-only -----------------------------------------------------------------


def test_update_and_delete_physically_denied(store: SqliteKGStore) -> None:
    store.add_node(_strategy())
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute(
            "UPDATE kg_node_versions SET name = 'rewritten history'"
        )
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("DELETE FROM kg_node_versions")
    # The row is untouched.
    node = store.get_node("strat:momo-1")
    assert node is not None and node.name == "dual momentum v1"


def test_versions_accumulate_never_replace(store: SqliteKGStore) -> None:
    v1 = store.add_node(_strategy(status=NodeStatus.CANDIDATE))
    v2 = store.add_node(_strategy(status=NodeStatus.SHADOW))
    assert v2 > v1  # monotonic version ids
    assert len(store.node_history("strat:momo-1")) == 2


# -- networkx view ----------------------------------------------------------------


def test_networkx_view_survives_reserved_attr_names(store: SqliteKGStore) -> None:
    # A legal domain attr named "name"/"status" must not collide with the
    # reserved NetworkX keywords (codex P2) — attrs stay nested.
    store.add_node(
        _strategy(attrs={"name": "shadow-name", "status": "weird", "family": "trend"})
    )
    g = store.to_networkx()
    assert g.nodes["strat:momo-1"]["name"] == "dual momentum v1"
    assert g.nodes["strat:momo-1"]["attrs"]["name"] == "shadow-name"


def test_networkx_view_reflects_as_of(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3", now=_T0)
    store.add_node(_strategy())
    store._fixed_now = _T1
    store.add_node(_factor())
    g_t0 = store.to_networkx(as_of=_T0)
    assert set(g_t0.nodes) == {"strat:momo-1"}
    g_now = store.to_networkx()
    assert set(g_now.nodes) == {"factor:mom-20d", "strat:momo-1"}
