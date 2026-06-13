"""O-002 sector forecast tests.

Locks:
* Evidence-only by construction — forecast persists via output_writer
  (MIROFISH- prefix, ``sector_forecast`` path) and the payload carries
  zero decision / size / direction fields.
* Bounded sector-score — score is clamped to [-1, 1] at parse time;
  out-of-range entries are dropped, never coerced silently into range.
* Anti-hallucination — sectors outside the digest vocabulary are
  dropped; zero surviving entries → ``None`` (quant fallback).
* probability_up is explicitly labeled uncalibrated in the payload.
"""

from __future__ import annotations

import json

import pytest

from backend.mirofish.info_digest import (
    DailyStockRow,
    build_info_digest,
)
from backend.mirofish.output_writer import (
    build_sector_forecast_evidence,
    make_forecast_evidence_id,
)
from backend.mirofish.sector_forecast import (
    FORECAST_SCHEMA_VERSION,
    MAX_FORECAST_SECTORS,
    SectorForecast,
    SectorForecastEntry,
    SectorForecaster,
    digest_sha256,
    parse_forecast_response,
)
from backend.models.evidence import validate_evidence_id

TRADE_DATE = "2026-06-12"
SHA = "a" * 64
ALLOWED = ("半导体", "银行", "光伏")


def _entry_dict(sector: str = "半导体", **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sector": sector,
        "score": 0.6,
        "probability_up": 0.62,
        "causal_chain": "出口管制升级→国产替代预期→板块资金流入",
        "uncertainty": "medium",
    }
    base.update(overrides)
    return base


def _raw(entries: list[dict[str, object]]) -> str:
    return json.dumps({"forecasts": entries}, ensure_ascii=False)


def _parse(raw: str) -> SectorForecast | None:
    return parse_forecast_response(
        raw, allowed_sectors=ALLOWED, trade_date=TRADE_DATE, digest_sha=SHA
    )


class TestParse:
    def test_happy_path(self) -> None:
        fc = _parse(_raw([_entry_dict(), _entry_dict("银行", score=-0.3)]))
        assert fc is not None
        assert [e.sector for e in fc.entries] == ["半导体", "银行"]
        assert fc.trade_date == TRADE_DATE
        assert fc.digest_sha256 == SHA

    def test_code_fences_tolerated(self) -> None:
        raw = "```json\n" + _raw([_entry_dict()]) + "\n```"
        assert _parse(raw) is not None

    def test_hallucinated_sector_dropped(self) -> None:
        fc = _parse(_raw([_entry_dict("量子计算"), _entry_dict("银行")]))
        assert fc is not None
        assert [e.sector for e in fc.entries] == ["银行"]

    def test_out_of_range_score_dropped_not_clamped(self) -> None:
        fc = _parse(_raw([_entry_dict(score=1.5), _entry_dict("银行")]))
        assert fc is not None
        assert [e.sector for e in fc.entries] == ["银行"]

    def test_bad_probability_dropped(self) -> None:
        assert _parse(_raw([_entry_dict(probability_up=1.2)])) is None

    def test_duplicate_sector_keeps_first(self) -> None:
        fc = _parse(
            _raw([_entry_dict(score=0.5), _entry_dict(score=-0.5)])
        )
        assert fc is not None
        assert len(fc.entries) == 1
        assert fc.entries[0].score == 0.5

    def test_cap_enforced(self) -> None:
        allowed = tuple(f"板块{i}" for i in range(20))
        raw = _raw([_entry_dict(s) for s in allowed])
        fc = parse_forecast_response(
            raw,
            allowed_sectors=allowed,
            trade_date=TRADE_DATE,
            digest_sha=SHA,
        )
        assert fc is not None
        assert len(fc.entries) == MAX_FORECAST_SECTORS

    def test_non_json_returns_none(self) -> None:
        assert _parse("我觉得半导体会涨") is None

    def test_zero_entries_returns_none(self) -> None:
        assert _parse(_raw([])) is None

    def test_extra_field_dropped(self) -> None:
        # extra="forbid": an entry smuggling a decision-ish field is
        # rejected wholesale, not silently stripped.
        fc = _parse(
            _raw([_entry_dict(side="BUY"), _entry_dict("银行")])
        )
        assert fc is not None
        assert [e.sector for e in fc.entries] == ["银行"]


class TestPayload:
    def test_payload_shape_and_uncalibrated_label(self) -> None:
        fc = _parse(_raw([_entry_dict()]))
        assert fc is not None
        payload = fc.to_payload()
        assert payload["schema_version"] == FORECAST_SCHEMA_VERSION
        assert payload["probability_note"] == "uncalibrated_llm_estimate"
        assert payload["horizon_days"] == 5
        entries = payload["entries"]
        assert isinstance(entries, list) and len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, dict)
        # Zero decision fields, by construction.
        forbidden = {"side", "volume", "limit_price", "direction", "size"}
        assert forbidden.isdisjoint(entry.keys())


class TestEvidence:
    def test_forecast_evidence_id_and_payload(self) -> None:
        fc = _parse(_raw([_entry_dict()]))
        assert fc is not None
        ev = build_sector_forecast_evidence(fc, calibration_note="命中率 60%")
        assert ev.evidence_id == make_forecast_evidence_id(TRADE_DATE)
        validate_evidence_id(ev.evidence_id)
        assert ev.path == "sector_forecast"
        assert ev.forecast is not None
        doc = ev.to_mongo()
        assert doc["forecast"] == fc.to_payload()
        assert "uncalibrated" in str(doc["content"])
        assert "命中率 60%" in str(doc["content"])
        # No RiskCheckSummary plumbing, no decision fields in the doc.
        assert "risk_check" not in doc
        assert "side" not in doc

    def test_non_forecast_evidence_has_no_payload_key(self) -> None:
        from backend.mirofish.output_writer import build_eod_evidence

        doc = build_eod_evidence(events=(), trade_date=TRADE_DATE).to_mongo()
        assert "forecast" not in doc


class _StubCaller:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _digest_with_sectors() -> object:
    rows = tuple(
        DailyStockRow(code=f"60000{i}.SH", pct_chg=1.0, amount=100.0)
        for i in range(3)
    )
    return build_info_digest(
        trade_date=TRADE_DATE,
        index_bars=(),
        daily_rows=rows,
        industry_by_code={f"60000{i}.SH": "半导体" for i in range(3)},
        news=(),
    )


class TestForecaster:
    @pytest.mark.asyncio
    async def test_happy_path_binds_digest_sha(self) -> None:
        caller = _StubCaller(_raw([_entry_dict()]))
        forecaster = SectorForecaster(caller)
        digest = _digest_with_sectors()
        fc = await forecaster.forecast(digest)  # type: ignore[arg-type]
        assert fc is not None
        # The recorded sha must hash the exact text the LLM saw.
        _, user_content = caller.calls[0]
        assert fc.digest_sha256 == digest_sha256(user_content)

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self) -> None:
        forecaster = SectorForecaster(_StubCaller(RuntimeError("boom")))
        assert await forecaster.forecast(_digest_with_sectors()) is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self) -> None:
        forecaster = SectorForecaster(_StubCaller("   "))
        assert await forecaster.forecast(_digest_with_sectors()) is None  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_empty_digest_skips_llm(self) -> None:
        caller = _StubCaller(_raw([_entry_dict()]))
        forecaster = SectorForecaster(caller)
        empty = build_info_digest(
            trade_date=TRADE_DATE,
            index_bars=(),
            daily_rows=(),
            industry_by_code={},
            news=(),
        )
        assert await forecaster.forecast(empty) is None
        assert caller.calls == []


class TestModelBounds:
    def test_entry_rejects_out_of_band(self) -> None:
        with pytest.raises(Exception):
            SectorForecastEntry(
                sector="半导体",
                score=2.0,
                probability_up=0.5,
                causal_chain="x",
                uncertainty="low",
            )
        with pytest.raises(Exception):
            SectorForecastEntry(
                sector="半导体",
                score=0.5,
                probability_up=0.5,
                causal_chain="x",
                uncertainty="extreme",  # type: ignore[arg-type]
            )
