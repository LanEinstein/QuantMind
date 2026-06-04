"""Q-002 — cold-start seed: generators, loader, provenance completeness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.knowledge_graph import NodeType, SqliteKGStore
from backend.knowledge_graph.seed import SeedReport, seed_knowledge_graph
from backend.knowledge_graph.seed.heuristics import HEURISTICS
from backend.knowledge_graph.seed.qlib_factors import (
    alpha158_factors,
    alpha360_factors,
)

_REAL_WQ101 = Path("data/kg_seed/wq101.json")
_REAL_GTJA = Path("data/kg_seed/gtja191.json")


def _write_fixture(path: Path, n: int) -> Path:
    path.write_text(
        json.dumps([{"id": i, "formula": f"RANK(DELTA($close,{i}))"} for i in
                    range(1, n + 1)]),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def seeded(tmp_path: Path) -> tuple[SqliteKGStore, SeedReport]:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    report = seed_knowledge_graph(
        store,
        wq101_path=_write_fixture(tmp_path / "wq101.json", 3),
        gtja191_path=_write_fixture(tmp_path / "gtja.json", 2),
    )
    return store, report


# -- generators -----------------------------------------------------------------


def test_alpha158_structure() -> None:
    factors = alpha158_factors()
    # 9 kbar + 4 price + 1 volume + 29 rolling ops x 5 windows = 159 features
    # (the canonical qlib config grid; recorded count is what we generate).
    assert len(factors) == 159
    assert len({f.factor_id for f in factors}) == len(factors)
    by_id = {f.factor_id: f for f in factors}
    assert by_id["factor:alpha158:KMID"].definition == "($close-$open)/$open"
    assert by_id["factor:alpha158:ROC60"].definition == "Ref($close,60)/$close"
    assert by_id["factor:alpha158:VSUMD5"].category == "volume"


def test_alpha360_structure() -> None:
    factors = alpha360_factors()
    assert len(factors) == 360  # 6 fields x 60 lags, exact
    by_id = {f.factor_id: f for f in factors}
    assert by_id["factor:alpha360:CLOSE0"].definition == "$close/$close"
    assert by_id["factor:alpha360:VOLUME59"].definition == "Ref($volume,59)/$volume"


# -- loader ----------------------------------------------------------------------


def test_seed_loads_all_tiers(seeded: tuple[SqliteKGStore, SeedReport]) -> None:
    store, report = seeded
    assert report.alpha158 == 159 and report.alpha360 == 360
    assert report.wq101 == 3 and report.gtja191 == 2
    assert report.heuristics == len(HEURISTICS)
    g = store.to_networkx()
    factor_nodes = [
        n for n, d in g.nodes(data=True) if d["node_type"] == NodeType.FACTOR.value
    ]
    assert len(factor_nodes) == report.factors


def test_seeded_factor_is_referencable_by_id(
    seeded: tuple[SqliteKGStore, SeedReport],
) -> None:
    store, _ = seeded
    # screening/backtest layers reference factors by stable factor_id.
    node = store.get_node("factor:wq101:001")
    assert node is not None
    assert node.attrs["definition"] == "RANK(DELTA($close,1))"
    assert node.attrs["category"] == "price"


def test_provenance_complete(seeded: tuple[SqliteKGStore, SeedReport]) -> None:
    store, _ = seeded
    g = store.to_networkx()
    for node_id, data in g.nodes(data=True):
        if data["node_type"] == NodeType.SOURCE_DOC.value:
            assert data["attrs"]["content_sha256"]
            continue
        # Every non-source node: provenance_ref + a DERIVED_FROM edge.
        node = store.get_node(node_id)
        assert node is not None and node.provenance_ref, node_id
        assert "#sha256:" in node.provenance_ref
        derived = [
            d for _, d, k in g.out_edges(node_id, keys=True)
            if k == f"derived:{node_id}"
        ]
        assert derived, f"{node_id} lacks DERIVED_FROM"


def test_malformed_seed_file_fails_fast(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"id": "one", "formula": 42}]), encoding="utf-8")
    with pytest.raises((ValueError, KeyError)):
        seed_knowledge_graph(
            store, wq101_path=bad,
            gtja191_path=_write_fixture(tmp_path / "g.json", 1),
        )


# -- the real in-repo seed artifacts ----------------------------------------------


@pytest.mark.skipif(
    not (_REAL_WQ101.exists() and _REAL_GTJA.exists()),
    reason="real seed artifacts not present",
)
def test_real_seed_artifacts_load_600_plus(tmp_path: Path) -> None:
    store = SqliteKGStore(tmp_path / "kg.sqlite3")
    report = seed_knowledge_graph(store)
    assert report.wq101 == 101
    assert report.gtja191 >= 150  # report ships 191; a few may be unverifiable
    assert report.factors >= 600  # acceptance: ~600+ factors
