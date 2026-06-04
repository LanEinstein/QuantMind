"""PILOT gate live-probe wiring — cond9 / cond10a / cond10b.

P0-6-amendment-2026-05-29 (U-D6). Before this, three of the five PILOT
live-probes could never pass on a real boot: cond9 (data_quality) and cond10a
(llm_timeout) were hard-coded ``return False`` stubs, and cond10b (cost_guard)
read a non-existent ``state.daily.status`` attribute (AttributeError →
fail-closed). These tests pin the real wiring so the gate's verdict reflects
live system health, never an unimplemented stub.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APITimeoutError

from backend.llm.fallback import (
    _utc_date_str,
    read_llm_timeout_rate,
    track_llm_call,
    track_llm_timeout,
)
from backend.services.pilot_data_probe import (
    MANDATORY_ETF_CANARIES,
    canary_quotes_reachable,
)


class _FakePipe:
    """Records incr/expire and applies them to the owning store on execute()."""

    def __init__(self, parent: _FakeRedis) -> None:
        self._parent = parent
        self._ops: list[tuple[str, tuple]] = []

    def incr(self, key: str) -> _FakePipe:
        self._ops.append(("incr", (key,)))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipe:
        self._ops.append(("expire", (key, ttl)))
        return self

    async def execute(self) -> list:
        results: list = []
        for op, args in self._ops:
            if op == "incr":
                (key,) = args
                self._parent.store[key] = self._parent.store.get(key, 0) + 1
                results.append(self._parent.store[key])
            elif op == "expire":
                key, ttl = args
                self._parent.expires[key] = ttl
                results.append(True)
        return results


class _FakeRedis:
    """Minimal in-memory async redis: pipeline(incr/expire) + mget + get."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self)

    async def mget(self, *keys: str) -> list[str | None]:
        return [None if (v := self.store.get(k)) is None else str(v) for k in keys]

    async def get(self, key: str) -> str | None:
        val = self.store.get(key)
        return None if val is None else str(val)


# --------------------------------------------------------------------------- #
# cond10a — live LLM timeout-rate counters (backend/llm/fallback.py)
# --------------------------------------------------------------------------- #


class TestLlmTimeoutCounters:
    async def test_track_call_increments_calls_key(self) -> None:
        redis = _FakeRedis()
        await track_llm_call(redis)
        await track_llm_call(redis)
        assert redis.store[f"llm:calls:{_utc_date_str()}"] == 2
        # TTL is set so the counter cannot grow unbounded.
        assert redis.expires[f"llm:calls:{_utc_date_str()}"] > 0

    async def test_track_timeout_increments_timeouts_key(self) -> None:
        redis = _FakeRedis()
        await track_llm_timeout(redis)
        assert redis.store[f"llm:timeouts:{_utc_date_str()}"] == 1

    async def test_read_returns_timeouts_and_calls(self) -> None:
        redis = _FakeRedis()
        await track_llm_call(redis)
        await track_llm_call(redis)
        await track_llm_call(redis)
        await track_llm_timeout(redis)
        timeouts, calls = await read_llm_timeout_rate(redis)
        assert (timeouts, calls) == (1, 3)

    async def test_cold_start_reads_zero_zero(self) -> None:
        redis = _FakeRedis()
        assert await read_llm_timeout_rate(redis) == (0, 0)

    async def test_read_raises_when_redis_none(self) -> None:
        # The gate's _safe_await turns this into a fail-closed verdict; an
        # unreadable counter must never silently pass cond10a.
        with pytest.raises(RuntimeError):
            await read_llm_timeout_rate(None)

    async def test_track_is_noop_when_redis_none(self) -> None:
        # Best-effort counting on the hot path must tolerate a missing client.
        await track_llm_call(None)
        await track_llm_timeout(None)

    async def test_track_call_failure_does_not_raise(self) -> None:
        # A Redis write failure must never break the LLM request path
        # (fail-open infra glitch).
        redis = MagicMock()
        pipe = MagicMock()
        pipe.incr = MagicMock(return_value=pipe)
        pipe.expire = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock(side_effect=RuntimeError("redis down"))
        redis.pipeline = MagicMock(return_value=pipe)
        await track_llm_call(redis)  # no exception


# --------------------------------------------------------------------------- #
# cond10a — router instrumentation (backend/llm/router.py::_call_provider)
# --------------------------------------------------------------------------- #


def _bare_router() -> object:
    from backend.llm.router import LLMRouter

    router = LLMRouter.__new__(LLMRouter)
    router._redis = AsyncMock()
    router._log = MagicMock()
    return router


class TestRouterTimeoutCounting:
    async def test_successful_call_counts_call_not_timeout(self) -> None:
        from backend.llm.providers import ThinkingConfig

        router = _bare_router()
        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=MagicMock(usage=None))
        with (
            patch.object(router, "_get_client", return_value=client),
            patch.object(router, "_normalize_provider_kwargs", return_value={}),
            patch(
                "backend.llm.router.track_llm_call", new_callable=AsyncMock
            ) as call_ctr,
            patch(
                "backend.llm.router.track_llm_timeout", new_callable=AsyncMock
            ) as to_ctr,
        ):
            await router._call_provider(
                provider_name="qwen",
                model="qwen3.6-plus",
                messages=[{"role": "user", "content": "hi"}],
                agent_name="x",
                thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
            )
        call_ctr.assert_awaited_once()
        to_ctr.assert_not_awaited()

    async def test_timeout_counts_both_and_reraises(self) -> None:
        from backend.llm.providers import ThinkingConfig

        router = _bare_router()
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=APITimeoutError(
                request=httpx.Request("POST", "https://example.test")
            )
        )
        with (
            patch.object(router, "_get_client", return_value=client),
            patch.object(router, "_normalize_provider_kwargs", return_value={}),
            patch(
                "backend.llm.router.track_llm_call", new_callable=AsyncMock
            ) as call_ctr,
            patch(
                "backend.llm.router.track_llm_timeout", new_callable=AsyncMock
            ) as to_ctr,
        ):
            with pytest.raises(APITimeoutError):
                await router._call_provider(
                    provider_name="qwen",
                    model="qwen3.6-plus",
                    messages=[{"role": "user", "content": "hi"}],
                    agent_name="x",
                    thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
                )
        call_ctr.assert_awaited_once()
        to_ctr.assert_awaited_once()


# --------------------------------------------------------------------------- #
# cond9 — ETF quote-reachability (backend/services/pilot_data_probe.py)
# --------------------------------------------------------------------------- #


def _market_data(leg_results: dict[str, tuple[bool, bool]]):
    md = MagicMock()
    md.probe_quote_vendor_reachability = AsyncMock(
        side_effect=lambda code: leg_results[code]
    )
    return md


class TestCanaryReachable:
    async def test_all_legs_serving_is_reachable(self) -> None:
        md = _market_data({c: (True, True) for c in MANDATORY_ETF_CANARIES})
        assert await canary_quotes_reachable(md, MANDATORY_ETF_CANARIES) is True

    async def test_single_leg_serving_is_reachable(self) -> None:
        # infra reachability only needs ONE leg (staleness/divergence are not
        # gated at boot — amendment §1.1).
        md = _market_data({c: (True, False) for c in MANDATORY_ETF_CANARIES})
        assert await canary_quotes_reachable(md, MANDATORY_ETF_CANARIES) is True

    async def test_preopen_fallback_only_leg_is_reachable(self) -> None:
        # P0-6-amendment-2026-06-04 regression: pre-open only the sina leg
        # serves a row (PRICE==0, rejected by the trading-path parser) —
        # that must read as reachable, never as a vendor outage.
        md = _market_data({c: (False, True) for c in MANDATORY_ETF_CANARIES})
        assert await canary_quotes_reachable(md, MANDATORY_ETF_CANARIES) is True

    async def test_both_legs_down_is_unreachable(self) -> None:
        results = {c: (True, True) for c in MANDATORY_ETF_CANARIES}
        results[MANDATORY_ETF_CANARIES[1]] = (False, False)
        md = _market_data(results)
        assert await canary_quotes_reachable(md, MANDATORY_ETF_CANARIES) is False

    async def test_market_data_none_fails_closed(self) -> None:
        assert await canary_quotes_reachable(None, MANDATORY_ETF_CANARIES) is False

    async def test_empty_codes_fails_closed(self) -> None:
        md = _market_data({})
        assert await canary_quotes_reachable(md, ()) is False

    async def test_probe_exception_is_unreachable(self) -> None:
        md = MagicMock()
        md.probe_quote_vendor_reachability = AsyncMock(
            side_effect=RuntimeError("vendor blew up")
        )
        assert await canary_quotes_reachable(md, ("510300",)) is False

    def test_mandatory_canaries_are_the_locked_etfs(self) -> None:
        # P0-9 §2 redline — these three broad-based ETFs are always in universe.
        assert MANDATORY_ETF_CANARIES == ("510300", "510500", "159949")

    def test_canaries_match_riskconfig_etf_whitelist(self) -> None:
        # Divergence guard (codex cleanup #3): the canary set duplicates the
        # P0-9-locked broad-based ETF triple that also defaults
        # ConcentrationException.etf_whitelist. If an amendment ever changes the
        # mandatory ETFs, this test fails so the two sites are reconciled by a
        # human rather than silently drifting.
        from backend.broker.models import ConcentrationExceptionConfig

        assert MANDATORY_ETF_CANARIES == ConcentrationExceptionConfig().etf_whitelist


# --------------------------------------------------------------------------- #
# Integration — _build_pilot_probe wires all three closures correctly.
# This test would have caught the original cond10b AttributeError and the
# cond9/cond10a return-False stubs.
# --------------------------------------------------------------------------- #


def _app(*, redis: object, market_data: object) -> object:
    return SimpleNamespace(
        state=SimpleNamespace(
            redis=redis,
            market_data=market_data,
            reconciliation_ticket_repository=None,
        )
    )


class TestBuildPilotProbeWiring:
    async def test_cond9_clear_when_canaries_reachable(self) -> None:
        from backend.main import _build_pilot_probe

        md = _market_data({c: (True, False) for c in MANDATORY_ETF_CANARIES})
        probe = _build_pilot_probe(_app(redis=_FakeRedis(), market_data=md), object())
        assert await probe.data_quality_clear() is True

    async def test_cond9_unmet_when_market_data_unwired(self) -> None:
        from backend.main import _build_pilot_probe

        probe = _build_pilot_probe(_app(redis=_FakeRedis(), market_data=None), object())
        assert await probe.data_quality_clear() is False

    async def test_cond10a_met_on_cold_start(self) -> None:
        from backend.main import _build_pilot_probe

        probe = _build_pilot_probe(_app(redis=_FakeRedis(), market_data=None), object())
        assert await probe.llm_timeout_within_ceiling() is True

    async def test_cond10a_unmet_above_ceiling(self) -> None:
        from backend.main import _build_pilot_probe

        redis = _FakeRedis()
        # 2 timeouts out of 10 calls = 20% > 5% (≥2 so the single-transient
        # grace does not apply; the ratio decides — P0-6-amendment-2026-06-01).
        for _ in range(10):
            await track_llm_call(redis)
        await track_llm_timeout(redis)
        await track_llm_timeout(redis)
        probe = _build_pilot_probe(_app(redis=redis, market_data=None), object())
        assert await probe.llm_timeout_within_ceiling() is False

    async def test_cond10a_single_transient_timeout_grace(self) -> None:
        # P0-6-amendment-2026-06-01 regression — a lone transient timeout on a
        # low-volume morning (1/18 = 5.56% > 5%) must NOT dead-lock the gate.
        from backend.main import _build_pilot_probe

        redis = _FakeRedis()
        for _ in range(18):
            await track_llm_call(redis)
        await track_llm_timeout(redis)
        probe = _build_pilot_probe(_app(redis=redis, market_data=None), object())
        assert await probe.llm_timeout_within_ceiling() is True

    async def test_cond10a_catastrophic_small_sample_unmet(self) -> None:
        # The single-timeout grace must NOT blind catastrophic startup failure:
        # 5 timeouts out of 5 calls (100%) is ≥2 timeouts AND above ceiling.
        from backend.main import _build_pilot_probe

        redis = _FakeRedis()
        for _ in range(5):
            await track_llm_call(redis)
            await track_llm_timeout(redis)
        probe = _build_pilot_probe(_app(redis=redis, market_data=None), object())
        assert await probe.llm_timeout_within_ceiling() is False

    async def test_cond10a_met_at_exactly_ceiling(self) -> None:
        from backend.main import _build_pilot_probe

        redis = _FakeRedis()
        # 1 timeout out of 20 calls = 5% == ceiling (≤ is met).
        for _ in range(20):
            await track_llm_call(redis)
        await track_llm_timeout(redis)
        probe = _build_pilot_probe(_app(redis=redis, market_data=None), object())
        assert await probe.llm_timeout_within_ceiling() is True

    async def test_cond10a_unmet_when_redis_none(self) -> None:
        from backend.main import _build_pilot_probe

        probe = _build_pilot_probe(_app(redis=None, market_data=None), object())
        # Fail-closed on unwired Redis (same convention as cost_guard probe).
        assert await probe.llm_timeout_within_ceiling() is False

    async def test_cond10b_met_when_status_ok(self) -> None:
        from backend.main import _build_pilot_probe

        with patch(
            "backend.services.cost_guard.get_daily_budget_state",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(status="ok"),
        ):
            probe = _build_pilot_probe(
                _app(redis=_FakeRedis(), market_data=None), object()
            )
            assert await probe.cost_guard_hard_reserve_active() is True

    async def test_cond10b_unmet_when_hard_breach(self) -> None:
        from backend.main import _build_pilot_probe

        with patch(
            "backend.services.cost_guard.get_daily_budget_state",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(status="hard_breach"),
        ):
            probe = _build_pilot_probe(
                _app(redis=_FakeRedis(), market_data=None), object()
            )
            assert await probe.cost_guard_hard_reserve_active() is False

    async def test_cond10b_unmet_when_redis_none(self) -> None:
        from backend.main import _build_pilot_probe

        probe = _build_pilot_probe(_app(redis=None, market_data=None), object())
        assert await probe.cost_guard_hard_reserve_active() is False
