"""O-002 EOD pipeline runner tests.

Locks:
* Every step degrades independently — inputs failure still writes the
  EOD audit row; forecast gate denial still persists the digest.
* The forecast LLM fires only behind a granted cost_guard slot, and the
  budget reservation is settled even when the LLM call degrades.
* Evidence routing: digest → InfoDigestEvidenceWriter (NEWS-/MARKET-),
  forecast + EOD row → MiroFishEvidenceWriter (MIROFISH-).
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from backend.mirofish.digest_evidence import InfoDigestEvidence
from backend.mirofish.info_digest import DailyStockRow
from backend.mirofish.output_writer import MiroFishEvidence
from backend.mirofish.sector_forecast import SectorForecaster
from backend.orchestration.mirofish_eod_runner import (
    DigestInputs,
    DigestInputsError,
    MiroFishEodRunner,
)

TRADE_DATE = "2026-06-12"
_NOW = dt.datetime(2026, 6, 12, 17, 0, tzinfo=dt.UTC)


class _Inputs:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def fetch(self, requested_date: str) -> DigestInputs:
        if self.fail:
            raise DigestInputsError("no data")
        rows = tuple(
            DailyStockRow(code=f"60000{i}.SH", pct_chg=1.0, amount=100.0)
            for i in range(3)
        )
        return DigestInputs(
            trade_date=TRADE_DATE,
            index_bars=(),
            daily_rows=rows,
            industry_by_code={f"60000{i}.SH": "半导体" for i in range(3)},
            news=(),
        )


class _DigestWriter:
    def __init__(self) -> None:
        self.written: list[InfoDigestEvidence] = []

    async def write(self, evidence: InfoDigestEvidence) -> bool:
        self.written.append(evidence)
        return True


class _MiroWriter:
    def __init__(self) -> None:
        self.written: list[MiroFishEvidence] = []

    async def write(self, evidence: MiroFishEvidence) -> bool:
        self.written.append(evidence)
        return True


class _FakeRedis:
    """Minimal async redis surface for the cost_guard slot + reservation."""

    def __init__(self) -> None:
        self.kv: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}

    async def sadd(self, key: str, member: str) -> int:
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        self.sets.get(key, set()).discard(member)
        return 1

    async def incr(self, key: str) -> int:
        self.kv[key] = self.kv.get(key, 0) + 1
        return int(self.kv[key])

    async def decr(self, key: str) -> int:
        self.kv[key] = self.kv.get(key, 0) - 1
        return int(self.kv[key])

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.kv[key] = self.kv.get(key, 0.0) + amount
        return self.kv[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def hvals(self, key: str) -> list[Any]:
        return []

    async def keys(self, pattern: str) -> list[str]:
        return []


def _forecast_raw() -> str:
    return json.dumps(
        {
            "forecasts": [
                {
                    "sector": "半导体",
                    "score": 0.5,
                    "probability_up": 0.6,
                    "causal_chain": "热度+政策",
                    "uncertainty": "medium",
                }
            ]
        },
        ensure_ascii=False,
    )


class _Caller:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = 0

    async def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _runner(
    *,
    inputs: _Inputs | None = None,
    caller: _Caller | None = None,
    redis: _FakeRedis | None = None,
    ledger: Any = None,
) -> tuple[MiroFishEodRunner, _DigestWriter, _MiroWriter]:
    digest_writer = _DigestWriter()
    miro_writer = _MiroWriter()
    forecaster = (
        SectorForecaster(caller) if caller is not None else None
    )
    runner = MiroFishEodRunner(
        inputs_provider=inputs or _Inputs(),
        digest_writer=digest_writer,  # type: ignore[arg-type]
        mirofish_writer=miro_writer,  # type: ignore[arg-type]
        forecaster=forecaster,
        redis_client=redis,  # type: ignore[arg-type]
        ledger=ledger,
        now_fn=lambda: _NOW,
    )
    return runner, digest_writer, miro_writer


class TestEodRunner:
    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        caller = _Caller(_forecast_raw())
        runner, digest_writer, miro_writer = _runner(
            caller=caller, redis=_FakeRedis()
        )
        await runner.run()
        ids = [e.evidence_id for e in digest_writer.written]
        assert ids == ["MARKET-DIGEST-20260612", "NEWS-DIGEST-20260612"]
        miro_ids = [e.evidence_id for e in miro_writer.written]
        assert miro_ids == [
            "MIROFISH-FORECAST-20260612",
            "MIROFISH-EOD-20260612",
        ]
        assert caller.calls == 1

    @pytest.mark.asyncio
    async def test_inputs_failure_still_writes_eod_row(self) -> None:
        caller = _Caller(_forecast_raw())
        runner, digest_writer, miro_writer = _runner(
            inputs=_Inputs(fail=True), caller=caller, redis=_FakeRedis()
        )
        await runner.run()
        assert digest_writer.written == []
        assert caller.calls == 0
        assert [e.path for e in miro_writer.written] == ["eod_review"]

    @pytest.mark.asyncio
    async def test_gate_denial_skips_llm_but_keeps_digest(self) -> None:
        caller = _Caller(_forecast_raw())
        redis = _FakeRedis()
        runner, digest_writer, miro_writer = _runner(
            caller=caller, redis=redis
        )
        # Pre-seed the dedup set so the slot is denied (same-day re-run).
        await runner.run()
        first_llm_calls = caller.calls
        await runner.run()
        assert caller.calls == first_llm_calls == 1  # second run deduped
        # Digest writes attempted both runs (writer dedups in real Mongo).
        assert len(digest_writer.written) == 4

    @pytest.mark.asyncio
    async def test_no_redis_skips_forecast(self) -> None:
        caller = _Caller(_forecast_raw())
        runner, _, miro_writer = _runner(caller=caller, redis=None)
        await runner.run()
        assert caller.calls == 0
        assert [e.path for e in miro_writer.written] == ["eod_review"]

    @pytest.mark.asyncio
    async def test_llm_failure_settles_reservation_and_writes_eod(self) -> None:
        redis = _FakeRedis()
        runner, _, miro_writer = _runner(
            caller=_Caller(RuntimeError("llm down")), redis=redis
        )
        await runner.run()
        assert [e.path for e in miro_writer.written] == ["eod_review"]
        # Reservation settled: the reserved counter nets back to ~0.
        reserved = [v for k, v in redis.kv.items() if k.endswith(":reserved")]
        assert reserved and abs(reserved[0]) < 1e-9

    @pytest.mark.asyncio
    async def test_ledger_note_lands_in_forecast_content(self) -> None:
        class _Ledger:
            async def score_due_and_summarize(self, as_of: str) -> str:
                return "近20份方向命中率 55%"

        runner, _, miro_writer = _runner(
            caller=_Caller(_forecast_raw()),
            redis=_FakeRedis(),
            ledger=_Ledger(),
        )
        await runner.run()
        forecast_docs = [
            e for e in miro_writer.written if e.path == "sector_forecast"
        ]
        assert forecast_docs and "近20份方向命中率 55%" in forecast_docs[0].content

    @pytest.mark.asyncio
    async def test_ledger_failure_never_blocks(self) -> None:
        class _Ledger:
            async def score_due_and_summarize(self, as_of: str) -> str:
                raise RuntimeError("scoring broke")

        runner, digest_writer, miro_writer = _runner(
            caller=_Caller(_forecast_raw()),
            redis=_FakeRedis(),
            ledger=_Ledger(),
        )
        await runner.run()
        assert len(digest_writer.written) == 2
        assert [e.path for e in miro_writer.written] == [
            "sector_forecast",
            "eod_review",
        ]
