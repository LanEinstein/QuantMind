"""O-005 forecast calibration ledger tests.

Locks:
* deterministic scoring (direction hit + Brier) — same inputs bit-exact;
* append-only outcome store (re-score is idempotent; corrupt tail safe);
* trailing note reports INSUFFICIENT_DATA below the sample floor;
* the ledger degrades (scores nothing) on reader / return-provider gaps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.mirofish.forecast_ledger import (
    MIN_SAMPLES_FOR_NOTE,
    DueForecast,
    ForecastEntryView,
    ForecastLedger,
    ForecastOutcomeStore,
    score_forecast,
    trailing_note,
)


def _forecast(trade_date: str, *entries: tuple[str, float, float]) -> DueForecast:
    return DueForecast(
        trade_date=trade_date,
        horizon_days=5,
        entries=tuple(
            ForecastEntryView(sector=s, score=sc, probability_up=p)
            for s, sc, p in entries
        ),
    )


class TestScoreForecast:
    def test_direction_hit_and_brier(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7), ("银行", -0.3, 0.2))
        # 半导体 predicted up, realized +2% → hit; 银行 predicted down,
        # realized -1% → hit.
        out = score_forecast(
            fc, {"半导体": 2.0, "银行": -1.0}, scored_as_of="2026-06-18"
        )
        assert out is not None
        assert out.hit_rate == 1.0
        semi = next(s for s in out.sectors if s.sector == "半导体")
        assert semi.hit is True
        # Brier for 半导体: (0.7 - 1)^2 = 0.09
        assert semi.brier == pytest.approx(0.09)
        bank = next(s for s in out.sectors if s.sector == "银行")
        # Brier for 银行 (predicted down, prob_up 0.2, actual 0): (0.2-0)^2=0.04
        assert bank.brier == pytest.approx(0.04)
        assert out.mean_brier == pytest.approx((0.09 + 0.04) / 2)

    def test_miss_lowers_hit_rate(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        out = score_forecast(fc, {"半导体": -2.0}, scored_as_of="2026-06-18")
        assert out is not None
        assert out.hit_rate == 0.0
        assert out.sectors[0].hit is False

    def test_deterministic(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        a = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-18")
        b = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-18")
        assert a == b

    def test_missing_realized_sector_skipped(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7), ("银行", -0.3, 0.2))
        out = score_forecast(fc, {"半导体": 1.0}, scored_as_of="2026-06-18")
        assert out is not None
        assert [s.sector for s in out.sectors] == ["半导体"]

    def test_no_realized_returns_none(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        assert score_forecast(fc, {}, scored_as_of="2026-06-18") is None

    def test_nonfinite_realized_skipped(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        assert (
            score_forecast(
                fc, {"半导体": float("nan")}, scored_as_of="2026-06-18"
            )
            is None
        )


class TestOutcomeStore:
    def test_append_and_recent(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        out = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-18")
        assert out is not None
        store.append(out)
        recent = store.recent(10)
        assert len(recent) == 1
        assert recent[0].trade_date == "2026-06-11"
        assert recent[0].hit_rate == 1.0

    def test_is_scored_idempotent(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        out = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-18")
        assert out is not None
        store.append(out)
        assert store.is_scored("2026-06-11") is True
        store.append(out)  # second append is a no-op
        assert len(store.recent(10)) == 1

    def test_corrupt_tail_safe(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        path.write_text(
            '{"trade_date": "2026-06-10", "hit_rate": 0.5, "mean_brier": 0.2, '
            '"horizon_days": 5, "scored_as_of": "2026-06-17", "sectors": []}\n'
            "{not json\n",
            encoding="utf-8",
        )
        store = ForecastOutcomeStore(path)
        recent = store.recent(10)
        assert len(recent) == 1
        assert recent[0].trade_date == "2026-06-10"


class TestTrailingNote:
    def test_insufficient_data(self) -> None:
        fc = _forecast("2026-06-11", ("半导体", 0.6, 0.7))
        out = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-18")
        assert out is not None
        note = trailing_note([out])  # 1 sector < MIN_SAMPLES_FOR_NOTE
        assert "INSUFFICIENT_DATA" in note

    def test_hit_rate_reported_above_floor(self) -> None:
        outs = []
        for i in range(MIN_SAMPLES_FOR_NOTE + 1):
            fc = _forecast(f"2026-06-{i + 1:02d}", ("半导体", 0.6, 0.7))
            o = score_forecast(fc, {"半导体": 2.0}, scored_as_of="2026-06-20")
            assert o is not None
            outs.append(o)
        note = trailing_note(outs)
        assert "命中率 100%" in note
        assert "Brier" in note


class _Reader:
    def __init__(self, forecasts: list[DueForecast], *, boom: bool = False) -> None:
        self.forecasts = forecasts
        self.boom = boom

    async def recent_forecasts(self, as_of: str) -> list[DueForecast]:
        if self.boom:
            raise RuntimeError("reader down")
        return self.forecasts


class TestForecastLedger:
    @pytest.mark.asyncio
    async def test_scores_due_and_summarizes(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        forecasts = [
            _forecast(f"2026-06-{i + 1:02d}", ("半导体", 0.6, 0.7))
            for i in range(MIN_SAMPLES_FOR_NOTE + 1)
        ]

        async def _returns(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            return {"半导体": 2.0}

        ledger = ForecastLedger(
            forecast_reader=_Reader(forecasts),
            realized_return_provider=_returns,
            outcome_store=store,
        )
        note = await ledger.score_due_and_summarize("2026-06-20")
        assert "命中率 100%" in note
        assert len(store.recent(50)) == MIN_SAMPLES_FOR_NOTE + 1

    @pytest.mark.asyncio
    async def test_skips_already_scored(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        forecasts = [_forecast("2026-06-11", ("半导体", 0.6, 0.7))]
        calls = {"n": 0}

        async def _returns(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            calls["n"] += 1
            return {"半导体": 2.0}

        ledger = ForecastLedger(
            forecast_reader=_Reader(forecasts),
            realized_return_provider=_returns,
            outcome_store=store,
        )
        await ledger.score_due_and_summarize("2026-06-20")
        await ledger.score_due_and_summarize("2026-06-21")  # same forecast
        assert calls["n"] == 1  # second run skips the already-scored forecast

    @pytest.mark.asyncio
    async def test_horizon_not_elapsed_retries(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        forecasts = [_forecast("2026-06-11", ("半导体", 0.6, 0.7))]

        async def _none(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            return None  # window not elapsed

        ledger = ForecastLedger(
            forecast_reader=_Reader(forecasts),
            realized_return_provider=_none,
            outcome_store=store,
        )
        note = await ledger.score_due_and_summarize("2026-06-12")
        assert store.recent(10) == []  # nothing scored
        assert "INSUFFICIENT_DATA" in note

    @pytest.mark.asyncio
    async def test_backlog_appended_oldest_first(self, tmp_path: Path) -> None:
        # codex O-005 P3: a backlog scored in one run must append oldest →
        # newest so recent(n) keeps the NEWEST scored forecasts.
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        # Reader returns newest-first (as Mongo sort desc would).
        forecasts = [
            _forecast("2026-06-13", ("半导体", 0.6, 0.7)),
            _forecast("2026-06-12", ("半导体", 0.6, 0.7)),
            _forecast("2026-06-11", ("半导体", 0.6, 0.7)),
        ]

        async def _returns(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            return {"半导体": 2.0}

        ledger = ForecastLedger(
            forecast_reader=_Reader(forecasts),
            realized_return_provider=_returns,
            outcome_store=store,
        )
        await ledger.score_due_and_summarize("2026-06-20")
        dates = [o.trade_date for o in store.recent(50)]
        assert dates == ["2026-06-11", "2026-06-12", "2026-06-13"]  # chronological
        # recent(1) keeps the NEWEST.
        assert store.recent(1)[0].trade_date == "2026-06-13"

    @pytest.mark.asyncio
    async def test_reader_failure_degrades(self, tmp_path: Path) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")

        async def _returns(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            return {"半导体": 2.0}

        ledger = ForecastLedger(
            forecast_reader=_Reader([], boom=True),
            realized_return_provider=_returns,
            outcome_store=store,
        )
        # Never raises; returns the (empty) trailing note.
        note = await ledger.score_due_and_summarize("2026-06-20")
        assert "INSUFFICIENT_DATA" in note

    @pytest.mark.asyncio
    async def test_return_provider_exception_skips_one(
        self, tmp_path: Path
    ) -> None:
        store = ForecastOutcomeStore(tmp_path / "out.jsonl")
        forecasts = [
            _forecast("2026-06-11", ("半导体", 0.6, 0.7)),
            _forecast("2026-06-12", ("银行", 0.5, 0.6)),
        ]

        async def _returns(fdate, horizon, sectors, as_of):  # noqa: ANN001, ANN201
            if fdate == "2026-06-11":
                raise RuntimeError("returns down")
            return {"银行": 1.0}

        ledger = ForecastLedger(
            forecast_reader=_Reader(forecasts),
            realized_return_provider=_returns,
            outcome_store=store,
        )
        await ledger.score_due_and_summarize("2026-06-20")
        scored = store.recent(10)
        assert [o.trade_date for o in scored] == ["2026-06-12"]
