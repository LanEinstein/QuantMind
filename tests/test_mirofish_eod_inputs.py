"""O-002 EOD input provider + KG adjacency tests (deterministic pieces).

Covers the data-shaping logic the orchestration core delegates to:
* ``kg_related_sectors`` — read-only chain-edge adjacency, best-effort.
* ``LiveDigestInputsProvider`` — Tushare/news → DigestInputs row parsing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from backend.knowledge_graph.schema import EdgeType, KGEdge, KGNode, NodeType
from backend.knowledge_graph.store import SqliteKGStore
from backend.orchestration.mirofish_eod_runner import (
    DigestInputsError,
    LiveDigestInputsProvider,
    kg_related_sectors,
)

# ---------------------------------------------------------------------------
# kg_related_sectors
# ---------------------------------------------------------------------------


def _seed_kg(path: Path) -> None:
    store = SqliteKGStore(path)
    store.add_node(
        KGNode(node_id="sec-semi", node_type=NodeType.SECTOR, name="半导体")
    )
    store.add_node(
        KGNode(node_id="link-wafer", node_type=NodeType.CHAIN_LINK, name="晶圆制造")
    )
    store.add_node(
        KGNode(node_id="link-eda", node_type=NodeType.CHAIN_LINK, name="EDA工具")
    )
    # 半导体 -REQUIRES-> 晶圆制造 ; 晶圆制造 <-UPSTREAM_OF- EDA工具
    store.add_edge(
        KGEdge(
            edge_id="e1",
            edge_type=EdgeType.REQUIRES,
            src_id="sec-semi",
            dst_id="link-wafer",
        )
    )
    store.add_edge(
        KGEdge(
            edge_id="e2",
            edge_type=EdgeType.UPSTREAM_OF,
            src_id="link-eda",
            dst_id="link-wafer",
        )
    )
    store.close()


class TestKgRelatedSectors:
    def test_adjacency_exact_name(self, tmp_path: Path) -> None:
        db = tmp_path / "kg.sqlite3"
        _seed_kg(db)
        related = kg_related_sectors(db, ("半导体",))
        assert related == (("半导体", ("晶圆制造",)),)

    def test_unmapped_sector_yields_nothing(self, tmp_path: Path) -> None:
        db = tmp_path / "kg.sqlite3"
        _seed_kg(db)
        assert kg_related_sectors(db, ("不存在板块",)) == ()

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        assert kg_related_sectors(tmp_path / "nope.sqlite3", ("半导体",)) == ()

    def test_deterministic(self, tmp_path: Path) -> None:
        db = tmp_path / "kg.sqlite3"
        _seed_kg(db)
        a = kg_related_sectors(db, ("半导体",))
        b = kg_related_sectors(db, ("半导体",))
        assert a == b


# ---------------------------------------------------------------------------
# LiveDigestInputsProvider
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(
        self,
        *,
        daily: pd.DataFrame,
        basic: pd.DataFrame,
        index: pd.DataFrame,
    ) -> None:
        self._daily = daily
        self._basic = basic
        self._index = index
        self.daily_calls: list[str] = []

    async def daily(self, trade_date: str) -> pd.DataFrame:
        self.daily_calls.append(trade_date)
        return self._daily

    async def stock_basic(self, *, fields: str = "") -> pd.DataFrame:
        return self._basic

    async def index_daily(
        self, ts_code: str, *, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        return self._index


class _FakeNews:
    async def fetch_latest_news(
        self, *, limit: int = 50, include_cctv: bool = False
    ) -> list[object]:
        return []


def _daily_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts_code": "600001.SH", "pct_chg": 2.0, "amount": 1000.0},
            {"ts_code": "600002.SH", "pct_chg": -1.0, "amount": 500.0},
            # malformed (no pct_chg) → dropped
            {"ts_code": "600003.SH", "pct_chg": None, "amount": 100.0},
        ]
    )


def _basic_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts_code": "600001.SH", "industry": "半导体"},
            {"ts_code": "600002.SH", "industry": "银行"},
            {"ts_code": "600003.SH", "industry": None},
        ]
    )


def _index_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_date": "20260610", "close": 3000.0},
            {"trade_date": "20260612", "close": 3030.0},
            {"trade_date": "20260611", "close": 3010.0},
        ]
    )


class TestLiveDigestInputsProvider:
    @pytest.mark.asyncio
    async def test_row_and_industry_parsing(self) -> None:
        client = _FakeClient(
            daily=_daily_df(), basic=_basic_df(), index=_index_df()
        )
        provider = LiveDigestInputsProvider(
            tushare_factory=lambda: client,  # type: ignore[arg-type]
            news_crawler=_FakeNews(),  # type: ignore[arg-type]
        )
        inputs = await provider.fetch("2026-06-12")
        codes = {r.code for r in inputs.daily_rows}
        assert codes == {"600001.SH", "600002.SH"}  # malformed dropped
        assert inputs.industry_by_code["600001.SH"] == "半导体"
        assert "600003.SH" not in inputs.industry_by_code  # None industry
        # One bar per configured index; closes sorted ascending by trade_date.
        assert len(inputs.index_bars) == 3
        assert inputs.index_bars[0].closes == (3000.0, 3010.0, 3030.0)
        dt.date.fromisoformat(inputs.trade_date)  # valid ISO date

    @pytest.mark.asyncio
    async def test_empty_daily_raises_after_fallback(self) -> None:
        client = _FakeClient(
            daily=pd.DataFrame(), basic=_basic_df(), index=_index_df()
        )
        provider = LiveDigestInputsProvider(
            tushare_factory=lambda: client,  # type: ignore[arg-type]
            news_crawler=_FakeNews(),  # type: ignore[arg-type]
        )
        with pytest.raises(DigestInputsError):
            await provider.fetch("2026-06-12")
        # Tried the requested/normalized date then fell back once.
        assert len(client.daily_calls) >= 2
