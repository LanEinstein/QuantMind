"""O-005 forecast-ledger adapter tests (Mongo reader + realized returns)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.data.sector_return_store import SectorReturnStore
from backend.orchestration.forecast_ledger_adapters import (
    MongoForecastReader,
    make_realized_return_provider,
)


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self._k: str | None = None
        self._rev = False
        self._lim = 0

    def sort(self, key: str, direction: int) -> _Cursor:
        self._k, self._rev = key, direction < 0
        return self

    def limit(self, n: int) -> _Cursor:
        self._lim = n
        return self

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        docs = self._docs
        if self._k:
            docs = sorted(docs, key=lambda d: d.get(self._k, ""), reverse=self._rev)
        return docs[: self._lim or length]


class _Coll:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, query: dict[str, Any]) -> _Cursor:
        lt = query["trade_date"]["$lt"]
        return _Cursor(
            [d for d in self.docs if str(d.get("trade_date", "")) < lt]
        )


class _Mongo:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        class _DB:
            def __getitem__(self, n: str) -> _Coll:
                return _Coll(docs)

        self._db = _DB()


def _doc(trade_date: str, horizon: int = 5) -> dict[str, Any]:
    return {
        "path": "sector_forecast",
        "trade_date": trade_date,
        "forecast": {
            "horizon_days": horizon,
            "entries": [
                {"sector": "半导体", "score": 0.6, "probability_up": 0.7}
            ],
        },
    }


class TestMongoForecastReader:
    @pytest.mark.asyncio
    async def test_parses_due_forecasts(self) -> None:
        reader = MongoForecastReader(mongodb=_Mongo([_doc("2026-06-11")]))
        due = await reader.recent_forecasts("2026-06-20")
        assert len(due) == 1
        assert due[0].trade_date == "2026-06-11"
        assert due[0].horizon_days == 5
        assert due[0].entries[0].sector == "半导体"

    @pytest.mark.asyncio
    async def test_excludes_future(self) -> None:
        reader = MongoForecastReader(mongodb=_Mongo([_doc("2026-06-21")]))
        assert list(await reader.recent_forecasts("2026-06-20")) == []

    @pytest.mark.asyncio
    async def test_bad_horizon_dropped(self) -> None:
        reader = MongoForecastReader(mongodb=_Mongo([_doc("2026-06-11", horizon=0)]))
        assert list(await reader.recent_forecasts("2026-06-20")) == []

    @pytest.mark.asyncio
    async def test_no_mongo_empty(self) -> None:
        reader = MongoForecastReader(mongodb=None)
        assert list(await reader.recent_forecasts("2026-06-20")) == []


class TestRealizedReturnProvider:
    @pytest.mark.asyncio
    async def test_sums_horizon_window(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        # Forecast 2026-06-11 (Thu), horizon 2 trading days → 06-12, 06-15.
        store.save("2026-06-12", {"半导体": 1.0})
        store.save("2026-06-15", {"半导体": 2.0})
        provider = make_realized_return_provider(store.load)
        realized = await provider("2026-06-11", 2, ["半导体"], "2026-06-16")
        assert realized is not None
        assert realized["半导体"] == pytest.approx(3.0)  # 1.0 + 2.0

    @pytest.mark.asyncio
    async def test_window_not_elapsed_returns_none(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        store.save("2026-06-12", {"半导体": 1.0})
        provider = make_realized_return_provider(store.load)
        # as_of is before the 2-day window completes (needs 06-15).
        realized = await provider("2026-06-11", 2, ["半导体"], "2026-06-12")
        assert realized is None

    @pytest.mark.asyncio
    async def test_missing_day_returns_none(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        store.save("2026-06-12", {"半导体": 1.0})
        # 06-15 not pinned → cannot score yet.
        provider = make_realized_return_provider(store.load)
        realized = await provider("2026-06-11", 2, ["半导体"], "2026-06-16")
        assert realized is None

    @pytest.mark.asyncio
    async def test_malformed_dates_return_none(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        provider = make_realized_return_provider(store.load)
        assert await provider("bad", 2, ["半导体"], "2026-06-16") is None

    @pytest.mark.asyncio
    async def test_sector_missing_on_one_day_excluded(self, tmp_path) -> None:
        # codex O-005 P2: a sector absent on ANY horizon day must be EXCLUDED
        # (not zero-filled) so the ledger records no false outcome for it.
        store = SectorReturnStore(tmp_path)
        store.save("2026-06-12", {"半导体": 1.0, "银行": 0.5})
        store.save("2026-06-15", {"银行": 0.7})  # 半导体 missing this day
        provider = make_realized_return_provider(store.load)
        realized = await provider(
            "2026-06-11", 2, ["半导体", "银行"], "2026-06-16"
        )
        assert realized is not None
        assert "半导体" not in realized  # excluded — present only 1 of 2 days
        assert realized["银行"] == pytest.approx(1.2)


class TestSectorReturnStore:
    def test_roundtrip(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        store.save("2026-06-12", {"半导体": 1.5, "银行": -0.5})
        assert store.load("2026-06-12") == {"半导体": 1.5, "银行": -0.5}

    def test_missing_empty(self, tmp_path) -> None:
        assert SectorReturnStore(tmp_path).load("2026-06-12") == {}

    def test_bad_date_no_file(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        store.save("../evil", {"半导体": 1.0})
        assert not (tmp_path.parent / "evil.json").exists()

    def test_nonfinite_dropped(self, tmp_path) -> None:
        store = SectorReturnStore(tmp_path)
        store.save("2026-06-12", {"半导体": float("inf"), "银行": 1.0})
        assert store.load("2026-06-12") == {"银行": 1.0}
