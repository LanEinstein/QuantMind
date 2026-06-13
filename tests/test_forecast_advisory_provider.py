"""O-003 forecast advisory provider tests.

Locks PIT (consume the most-recent forecast strictly BEFORE T) +
fail-open (any gap → None → pure-quant path) + correct sector→code
mapping with code-form normalization.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.orchestration.forecast_advisory_provider import (
    ForecastAdvisoryProvider,
)


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self._sort_key: str | None = None
        self._reverse = False
        self._limit = 0

    def sort(self, key: str, direction: int) -> _Cursor:
        self._sort_key = key
        self._reverse = direction < 0
        return self

    def limit(self, n: int) -> _Cursor:
        self._limit = n
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        docs = self._docs
        if self._sort_key is not None:
            docs = sorted(
                docs, key=lambda d: d.get(self._sort_key, ""), reverse=self._reverse
            )
        return docs[: self._limit or length]


class _Collection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.last_query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> _Cursor:
        self.last_query = query
        matched = [d for d in self.docs if _matches(d, query)]
        return _Cursor(matched)


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$lt" in v:
            if not (str(doc.get(k, "")) < v["$lt"]):
                return False
        elif doc.get(k) != v:
            return False
    return True


class _DB:
    def __init__(self, coll: _Collection) -> None:
        self._coll = coll

    def __getitem__(self, name: str) -> _Collection:
        return self._coll


class _Mongo:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._db = _DB(_Collection(docs))


def _forecast_doc(trade_date: str, scores: dict[str, float]) -> dict[str, Any]:
    return {
        "path": "sector_forecast",
        "trade_date": trade_date,
        "forecast": {
            "schema_version": "mirofish.sector_forecast/v1",
            "entries": [
                {"sector": s, "score": v} for s, v in scores.items()
            ],
        },
    }


async def _semi_map(forecast_date: str) -> dict[str, str]:
    return {"600001.SH": "半导体", "600002.SH": "银行"}


class TestForecastAdvisoryProvider:
    @pytest.mark.asyncio
    async def test_happy_path_maps_t_minus_1(self) -> None:
        mongo = _Mongo([_forecast_doc("2026-06-11", {"半导体": 0.6})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map
        )
        signals = await provider(
            ["600001.SH", "600002.SH"], trade_date="2026-06-12"
        )
        assert signals is not None
        by_code = {s.code: s.advisory_score for s in signals}
        assert by_code == {"600001.SH": 0.6}

    @pytest.mark.asyncio
    async def test_excludes_same_day_and_future(self) -> None:
        mongo = _Mongo(
            [
                _forecast_doc("2026-06-12", {"半导体": 0.9}),  # same day
                _forecast_doc("2026-06-13", {"半导体": 0.9}),  # future
            ]
        )
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map
        )
        # No forecast strictly before T → pure-quant fallback.
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_picks_most_recent_prior(self) -> None:
        mongo = _Mongo(
            [
                _forecast_doc("2026-06-09", {"半导体": 0.2}),
                _forecast_doc("2026-06-11", {"半导体": 0.8}),
            ]
        )
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map
        )
        signals = await provider(["600001.SH"], trade_date="2026-06-12")
        assert signals is not None
        assert signals[0].advisory_score == 0.8  # the 06-11 one

    @pytest.mark.asyncio
    async def test_stale_forecast_rejected(self) -> None:
        mongo = _Mongo([_forecast_doc("2026-06-01", {"半导体": 0.8})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map, max_age_days=5
        )
        # 11 days old > 5 → too stale → None.
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_no_forecast_returns_none(self) -> None:
        provider = ForecastAdvisoryProvider(
            mongodb=_Mongo([]), industry_map_loader=_semi_map
        )
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_no_mongo_returns_none(self) -> None:
        provider = ForecastAdvisoryProvider(
            mongodb=None, industry_map_loader=_semi_map
        )
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_sector_map_failure_fails_open(self) -> None:
        async def _boom(forecast_date: str) -> dict[str, str]:
            raise RuntimeError("industry map load failed")

        mongo = _Mongo([_forecast_doc("2026-06-11", {"半导体": 0.6})])
        provider = ForecastAdvisoryProvider(mongodb=mongo, industry_map_loader=_boom)
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_empty_industry_map_returns_none(self) -> None:
        async def _empty(forecast_date: str) -> dict[str, str]:
            return {}

        mongo = _Mongo([_forecast_doc("2026-06-11", {"半导体": 0.6})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_empty
        )
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_industry_map_loaded_co_dated_with_forecast(self) -> None:
        # PIT: the loader is keyed by the CONSUMED forecast's date (T-1),
        # not the selection day (T) — so a replay re-derives the same map.
        seen: list[str] = []

        async def _spy_loader(forecast_date: str) -> dict[str, str]:
            seen.append(forecast_date)
            return {"600001.SH": "半导体"}

        mongo = _Mongo([_forecast_doc("2026-06-11", {"半导体": 0.6})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_spy_loader
        )
        await provider(["600001.SH"], trade_date="2026-06-12")
        assert seen == ["2026-06-11"]  # forecast date, not selection day

    @pytest.mark.asyncio
    async def test_empty_scores_returns_none(self) -> None:
        mongo = _Mongo([_forecast_doc("2026-06-11", {})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map
        )
        assert await provider(["600001.SH"], trade_date="2026-06-12") is None

    @pytest.mark.asyncio
    async def test_bare_code_input_normalizes(self) -> None:
        # Caller may pass bare 6-digit codes; the industry map is dotted.
        mongo = _Mongo([_forecast_doc("2026-06-11", {"半导体": 0.6})])
        provider = ForecastAdvisoryProvider(
            mongodb=mongo, industry_map_loader=_semi_map
        )
        signals = await provider(["600001"], trade_date="2026-06-12")
        assert signals is not None
        assert signals[0].code == "600001"
        assert signals[0].advisory_score == 0.6
