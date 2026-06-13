"""O-004 off-market briefing provider tests."""

from __future__ import annotations

from typing import Any

import pytest

from backend.orchestration.off_market_provider import (
    MAX_BRIEFING_CHARS,
    OffMarketBriefingProvider,
)


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self._key: str | None = None
        self._rev = False
        self._limit = 0

    def sort(self, key: str, direction: int) -> _Cursor:
        self._key, self._rev = key, direction < 0
        return self

    def limit(self, n: int) -> _Cursor:
        self._limit = n
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        docs = self._docs
        if self._key:
            docs = sorted(docs, key=lambda d: d.get(self._key, ""), reverse=self._rev)
        return docs[: self._limit or length]


class _Collection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, query: dict[str, Any]) -> _Cursor:
        rng = query["evidence_id"]
        lo, hi = rng["$gte"], rng["$lt"]
        return _Cursor([d for d in self.docs if lo <= d["evidence_id"] < hi])


class _DB:
    def __init__(self, coll: _Collection) -> None:
        self._coll = coll

    def __getitem__(self, name: str) -> _Collection:
        return self._coll


class _Mongo:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._db = _DB(_Collection(docs))


def _ev(eid: str, content: str) -> dict[str, Any]:
    return {"evidence_id": eid, "content": content}


class TestOffMarketProvider:
    @pytest.mark.asyncio
    async def test_assembles_all_families(self) -> None:
        mongo = _Mongo(
            [
                _ev("MIROFISH-FORECAST-20260611", "半导体 +0.6"),
                _ev("MARKET-DIGEST-20260611", "上证 +1.2%"),
                _ev("NEWS-DIGEST-20260611", "芯片利好"),
            ]
        )
        provider = OffMarketBriefingProvider(mongodb=mongo)
        text = await provider(["600001"], trade_date="2026-06-12")
        assert "板块推演" in text and "半导体 +0.6" in text
        assert "市场汇总" in text and "上证 +1.2%" in text
        assert "资讯汇总" in text and "芯片利好" in text

    @pytest.mark.asyncio
    async def test_excludes_same_and_future_day(self) -> None:
        mongo = _Mongo(
            [
                _ev("MARKET-DIGEST-20260612", "今天(不应入)"),
                _ev("MARKET-DIGEST-20260613", "未来(不应入)"),
            ]
        )
        provider = OffMarketBriefingProvider(mongodb=mongo)
        text = await provider(["600001"], trade_date="2026-06-12")
        assert text == ""  # nothing strictly before the selection day

    @pytest.mark.asyncio
    async def test_keeps_recent_drops_oldest_beyond_limit(self) -> None:
        # DIGEST_LIMIT is 2 → the two most-recent survive, the oldest is cut.
        mongo = _Mongo(
            [
                _ev("MARKET-DIGEST-20260605", "最旧"),
                _ev("MARKET-DIGEST-20260609", "中"),
                _ev("MARKET-DIGEST-20260611", "新"),
            ]
        )
        provider = OffMarketBriefingProvider(mongodb=mongo)
        text = await provider(["600001"], trade_date="2026-06-12")
        assert "新" in text and "中" in text
        assert "最旧" not in text
        # Most-recent first.
        assert text.index("新") < text.index("中")

    @pytest.mark.asyncio
    async def test_stale_evidence_rejected(self) -> None:
        # A weeks-old digest (EOD pipeline stalled) must NOT be injected
        # (codex O-004 staleness guard, mirrors the O-003 advisory recency).
        mongo = _Mongo([_ev("MARKET-DIGEST-20260601", "陈旧")])
        provider = OffMarketBriefingProvider(mongodb=mongo)
        text = await provider(["600001"], trade_date="2026-06-12")
        assert text == ""  # 11 days old > MAX_EVIDENCE_AGE_DAYS

    @pytest.mark.asyncio
    async def test_no_evidence_returns_empty(self) -> None:
        provider = OffMarketBriefingProvider(mongodb=_Mongo([]))
        assert await provider(["600001"], trade_date="2026-06-12") == ""

    @pytest.mark.asyncio
    async def test_no_mongo_returns_empty(self) -> None:
        provider = OffMarketBriefingProvider(mongodb=None)
        assert await provider(["600001"], trade_date="2026-06-12") == ""

    @pytest.mark.asyncio
    async def test_empty_trade_date_returns_empty(self) -> None:
        mongo = _Mongo([_ev("MARKET-DIGEST-20260611", "x")])
        provider = OffMarketBriefingProvider(mongodb=mongo)
        assert await provider(["600001"], trade_date="") == ""

    @pytest.mark.asyncio
    async def test_briefing_is_bounded(self) -> None:
        mongo = _Mongo(
            [_ev("MARKET-DIGEST-20260611", "长" * 10000)]
        )
        provider = OffMarketBriefingProvider(mongodb=mongo)
        text = await provider(["600001"], trade_date="2026-06-12")
        assert len(text) <= MAX_BRIEFING_CHARS

    @pytest.mark.asyncio
    async def test_query_error_fails_open(self) -> None:
        class _BoomColl:
            def find(self, q: dict[str, Any]) -> Any:
                raise RuntimeError("mongo down")

        class _BoomDB:
            def __getitem__(self, n: str) -> Any:
                return _BoomColl()

        class _BoomMongo:
            _db = _BoomDB()

        provider = OffMarketBriefingProvider(mongodb=_BoomMongo())
        assert await provider(["600001"], trade_date="2026-06-12") == ""
