"""RouterLlmClient + RouterUsageReserver (P0-10-amendment-2026-06-11 §4.3).

The orchestration-layer adapters that satisfy ``ThemeInvestigator``'s
injected ``LlmClient`` / ``UsageReserver`` Protocols over the async
``LLMRouter`` + ``cost_guard``. The investigator itself stays untouched
(injection-only, Phase Y red line); these adapters are the bridge a
future research cron wires in.

Key contracts pinned here:

* the forwarded ``max_tokens`` is SHRUNK by the kimi thinking budget so
  the router's thinking growth cannot bust the investigator's per-run
  total bound or under-cover the reservation; no completion room raises
  BEFORE any provider spend.
* sync→async bridging via ``run_coroutine_threadsafe`` against the
  captured loop; calling from the loop thread itself raises instead of
  deadlocking.
* structural breakage (no choices / missing usage) raises so the
  investigator's catch-all turns it into an auditable aborted run
  (fail-closed); an *empty text* completion is returned as-is so the
  response bytes still reach the provenance store.
* the reserver converts tokens → RMB at a conservative single rate
  (default ≥ the kimi-k2.6 output list price — drift-tested below) and
  answers ``False`` on ``DailyBudgetExceededError`` (the investigator
  then aborts); every granted reservation is held and settled, never
  overwritten.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.services.cost_guard import (
    BudgetReservation,
    DailyBudgetExceededError,
)
from backend.services.theme_llm_client import (
    _DEFAULT_RMB_PER_MILLION_TOKENS,
    _DEFAULT_THINKING_BUDGET_TOKENS,
    RouterLlmClient,
    RouterUsageReserver,
)
from backend.theme_research import LlmCompletion

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    prompt_tokens: int = 1200
    completion_tokens: int = 300
    total_tokens: int = 1500


@dataclass
class _FakeMessage:
    content: str | None = "structured output"


@dataclass
class _FakeChoice:
    message: _FakeMessage = field(default_factory=_FakeMessage)


class _FakeCompletion:
    """Mimics the openai ChatCompletion surface the adapter touches."""

    def __init__(
        self,
        content: str | None = "structured output",
        usage: _FakeUsage | None = _FakeUsage(),
        model: str = "kimi-k2.6",
    ) -> None:
        self.choices = [_FakeChoice(_FakeMessage(content))]
        self.usage = usage
        self.model = model

    def model_dump_json(self) -> str:
        return '{"fake": "completion"}'


class _FakeRouter:
    def __init__(self, completion: Any = None, exc: Exception | None = None) -> None:
        self.completion = completion or _FakeCompletion()
        self.exc = exc
        self.calls: list[tuple[str, list, dict]] = []

    async def complete(self, agent_name: str, messages: list, **kwargs: Any) -> Any:
        self.calls.append((agent_name, messages, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.completion


def _make_reservation(amount_rmb: float) -> BudgetReservation:
    return BudgetReservation(
        key="llm:usage:x",
        amount_rmb=amount_rmb,
        agent_name="theme_investigator",
        date="2026-06-11",
    )


# ---------------------------------------------------------------------------
# RouterLlmClient
# ---------------------------------------------------------------------------


class TestRouterLlmClient:
    def test_complete_bridges_and_maps_fields(self) -> None:
        router = _FakeRouter()

        async def harness() -> LlmCompletion:
            client = RouterLlmClient(router=router, thinking_budget_tokens=500)
            return await asyncio.to_thread(
                client.complete, prompt="investigate", max_tokens=2000
            )

        result = asyncio.run(harness())
        assert isinstance(result, LlmCompletion)
        assert result.text == "structured output"
        assert result.model == "kimi-k2.6"
        assert result.tokens_used == 1500
        assert result.raw_bytes == b'{"fake": "completion"}'
        agent_name, messages, kwargs = router.calls[0]
        assert agent_name == "theme_investigator"
        assert messages == [{"role": "user", "content": "investigate"}]
        # The forwarded cap is shrunk by the thinking budget so the
        # router's kimi thinking growth restores (not exceeds) the
        # caller's total bound: 2000 - 500 = 1500.
        assert kwargs.get("max_tokens") == 1500

    def test_no_completion_room_raises_before_any_spend(self) -> None:
        router = _FakeRouter()

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=10_000)
            with pytest.raises(ValueError, match="thinking budget"):
                await asyncio.to_thread(client.complete, prompt="p", max_tokens=10_000)

        asyncio.run(harness())
        assert router.calls == []  # refused BEFORE the provider call

    def test_loop_thread_call_raises_instead_of_deadlocking(self) -> None:
        router = _FakeRouter()

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            with pytest.raises(RuntimeError, match="loop thread"):
                client.complete(prompt="p", max_tokens=100)

        asyncio.run(harness())

    def test_missing_usage_raises_fail_closed(self) -> None:
        router = _FakeRouter(completion=_FakeCompletion(usage=None))

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            with pytest.raises(ValueError, match="usage"):
                await asyncio.to_thread(client.complete, prompt="p", max_tokens=100)

        asyncio.run(harness())

    def test_empty_text_is_returned_not_raised(self) -> None:
        # The investigator captures LLM_RESPONSE bytes before its abort
        # checks — an empty completion must still reach the provenance
        # store (strict parse downstream yields no candidates anyway).
        router = _FakeRouter(completion=_FakeCompletion(content=None))

        async def harness() -> LlmCompletion:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            return await asyncio.to_thread(client.complete, prompt="p", max_tokens=100)

        result = asyncio.run(harness())
        assert result.text == ""
        assert result.raw_bytes  # bytes still captured

    def test_router_exception_propagates(self) -> None:
        router = _FakeRouter(exc=TimeoutError("provider timeout"))

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            with pytest.raises(TimeoutError):
                await asyncio.to_thread(client.complete, prompt="p", max_tokens=100)

        asyncio.run(harness())

    def test_serialize_falls_back_to_repr_on_dump_failure(self) -> None:
        class _BrokenDump(_FakeCompletion):
            def model_dump_json(self) -> str:
                raise RuntimeError("pydantic surface changed")

        router = _FakeRouter(completion=_BrokenDump())

        async def harness() -> LlmCompletion:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            return await asyncio.to_thread(client.complete, prompt="p", max_tokens=100)

        result = asyncio.run(harness())
        # Fidelity degrades to repr but the artifact is never lost.
        assert result.raw_bytes

    def test_no_readable_choices_raises(self) -> None:
        class _NoChoices:
            choices: list = []
            usage = _FakeUsage()
            model = "kimi-k2.6"

            def model_dump_json(self) -> str:
                return "{}"

        router = _FakeRouter(completion=_NoChoices())

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            with pytest.raises(ValueError, match="choices"):
                await asyncio.to_thread(client.complete, prompt="p", max_tokens=100)

        asyncio.run(harness())

    def test_boundary_validation(self) -> None:
        router = _FakeRouter()

        async def harness() -> None:
            client = RouterLlmClient(router=router, thinking_budget_tokens=5)
            with pytest.raises(ValueError, match="prompt"):
                await asyncio.to_thread(client.complete, prompt="  ", max_tokens=100)
            with pytest.raises(ValueError, match="max_tokens"):
                await asyncio.to_thread(client.complete, prompt="p", max_tokens=0)

        asyncio.run(harness())

    def test_constructor_validation(self) -> None:
        async def harness() -> None:
            with pytest.raises(ValueError, match="agent_name"):
                RouterLlmClient(router=_FakeRouter(), agent_name=" ")
            with pytest.raises(ValueError, match="result_timeout_seconds"):
                RouterLlmClient(router=_FakeRouter(), result_timeout_seconds=0)
            with pytest.raises(ValueError, match="thinking_budget_tokens"):
                RouterLlmClient(router=_FakeRouter(), thinking_budget_tokens=0)

        asyncio.run(harness())

    def test_constructor_outside_loop_raises(self) -> None:
        with pytest.raises(RuntimeError):
            RouterLlmClient(router=_FakeRouter())


# ---------------------------------------------------------------------------
# RouterUsageReserver
# ---------------------------------------------------------------------------


class TestRouterUsageReserver:
    def test_reserve_converts_tokens_to_rmb_and_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        async def fake_reserve_budget(
            redis_client: Any, *, agent_name: str, estimated_rmb: float
        ) -> BudgetReservation:
            captured["agent_name"] = agent_name
            captured["estimated_rmb"] = estimated_rmb
            return _make_reservation(estimated_rmb)

        monkeypatch.setattr(
            "backend.services.theme_llm_client.reserve_budget",
            fake_reserve_budget,
        )

        async def harness() -> bool:
            reserver = RouterUsageReserver(redis_client=object())
            return await asyncio.to_thread(reserver.reserve, 40_000)

        assert asyncio.run(harness()) is True
        assert captured["agent_name"] == "theme_investigator"
        # 40_000 tokens × ¥27/M (kimi-k2.6 output realtime list) = ¥1.08
        assert captured["estimated_rmb"] == pytest.approx(1.08)

    def test_reserve_false_on_budget_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_reserve_budget(*args: Any, **kwargs: Any) -> Any:
            raise DailyBudgetExceededError("over the ¥100 hard cap")

        monkeypatch.setattr(
            "backend.services.theme_llm_client.reserve_budget",
            fake_reserve_budget,
        )

        async def harness() -> bool:
            reserver = RouterUsageReserver(redis_client=object())
            return await asyncio.to_thread(reserver.reserve, 40_000)

        assert asyncio.run(harness()) is False

    def test_reserve_false_on_infra_error_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_reserve_budget(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "backend.services.theme_llm_client.reserve_budget",
            fake_reserve_budget,
        )

        async def harness() -> bool:
            reserver = RouterUsageReserver(redis_client=object())
            return await asyncio.to_thread(reserver.reserve, 40_000)

        assert asyncio.run(harness()) is False

    def test_settle_releases_every_held_reservation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two reserves before one settle (e.g. a retried run) must NOT
        # strand the first reservation on the shared daily counter.
        made: list[BudgetReservation] = []
        settled: list[BudgetReservation] = []

        async def fake_reserve_budget(
            redis_client: Any, *, agent_name: str, estimated_rmb: float
        ) -> BudgetReservation:
            reservation = _make_reservation(estimated_rmb)
            made.append(reservation)
            return reservation

        async def fake_settle_budget(redis_client: Any, res: BudgetReservation) -> None:
            settled.append(res)

        monkeypatch.setattr(
            "backend.services.theme_llm_client.reserve_budget",
            fake_reserve_budget,
        )
        monkeypatch.setattr(
            "backend.services.theme_llm_client.settle_budget",
            fake_settle_budget,
        )

        async def harness() -> None:
            reserver = RouterUsageReserver(redis_client=object())
            assert await asyncio.to_thread(reserver.reserve, 10_000) is True
            assert await asyncio.to_thread(reserver.reserve, 20_000) is True
            await reserver.settle()
            # Idempotent: a second settle is a no-op.
            await reserver.settle()

        asyncio.run(harness())
        assert settled == made
        assert len(settled) == 2

    def test_settle_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # cost_guard's reservation TTL covers a failed release — settle
        # must never crash the cron wrapper.
        async def fake_reserve_budget(*args: Any, **kwargs: Any) -> Any:
            return _make_reservation(0.1)

        async def fake_settle_budget(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "backend.services.theme_llm_client.reserve_budget",
            fake_reserve_budget,
        )
        monkeypatch.setattr(
            "backend.services.theme_llm_client.settle_budget",
            fake_settle_budget,
        )

        async def harness() -> None:
            reserver = RouterUsageReserver(redis_client=object())
            assert await asyncio.to_thread(reserver.reserve, 1000) is True
            await reserver.settle()  # must not raise

        asyncio.run(harness())

    def test_reserve_boundary_validation(self) -> None:
        async def harness() -> None:
            reserver = RouterUsageReserver(redis_client=object())
            with pytest.raises(ValueError, match="estimated_tokens"):
                await asyncio.to_thread(reserver.reserve, 0)

        asyncio.run(harness())

    def test_constructor_validation(self) -> None:
        async def harness() -> None:
            with pytest.raises(ValueError, match="rmb_per_million_tokens"):
                RouterUsageReserver(redis_client=object(), rmb_per_million_tokens=0)
            with pytest.raises(ValueError, match="result_timeout_seconds"):
                RouterUsageReserver(redis_client=object(), result_timeout_seconds=0)
            with pytest.raises(ValueError, match="agent_name"):
                RouterUsageReserver(redis_client=object(), agent_name="  ")

        asyncio.run(harness())

    def test_loop_thread_reserve_raises(self) -> None:
        async def harness() -> None:
            reserver = RouterUsageReserver(redis_client=object())
            with pytest.raises(RuntimeError, match="loop thread"):
                reserver.reserve(1000)

        asyncio.run(harness())


# ---------------------------------------------------------------------------
# Drift guards — hand-copied pricing/config knowledge must track its source
# (P0-10-amendment-2026-06-11 §6: the kimi RMB price is an assumption the
# owner will correct in MODEL_COST_RATES; these tests force the dependent
# constants to move with it instead of going silently stale).
# ---------------------------------------------------------------------------


def _load_agent_models() -> dict:
    raw = (_REPO_ROOT / "config" / "agent_models.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(raw)


class TestPricingDriftGuards:
    def test_reserver_default_rate_covers_kimi_output_list(self) -> None:
        from backend.llm.fallback import MODEL_COST_RATES

        kimi = MODEL_COST_RATES["kimi-k2.6"]
        assert _DEFAULT_RMB_PER_MILLION_TOKENS >= kimi.output_rmb_per_million, (
            "RouterUsageReserver's default rate under-covers the kimi-k2.6 "
            "output list rate — update _DEFAULT_RMB_PER_MILLION_TOKENS in "
            "backend/services/theme_llm_client.py alongside MODEL_COST_RATES"
        )

    def test_adapter_thinking_budget_matches_yaml(self) -> None:
        config = _load_agent_models()
        yaml_budget = config["agents"]["theme_investigator"]["thinking"]["max_tokens"]
        assert _DEFAULT_THINKING_BUDGET_TOKENS == yaml_budget, (
            "RouterLlmClient's default thinking budget no longer mirrors "
            "config/agent_models.yaml theme_investigator.thinking.max_tokens"
        )

    def test_thesis_estimate_covers_worst_case(self) -> None:
        from backend.llm.fallback import MODEL_COST_RATES
        from backend.services.thesis_advisory import _DEFAULT_ESTIMATED_RMB

        config = _load_agent_models()
        reviewer = config["agents"]["thesis_reviewer"]
        kimi = MODEL_COST_RATES[reviewer["model"]]
        thinking_budget = reviewer["thinking"]["max_tokens"]
        default_completion = config["defaults"]["max_tokens"]
        # review() passes no max_tokens → defaults.max_tokens + router
        # thinking growth, all billable as output; prompt ≈ 2.5k tokens.
        worst_output = (default_completion + thinking_budget) * (
            kimi.output_rmb_per_million / 1_000_000
        )
        worst_input = 2_500 * (kimi.input_rmb_per_million / 1_000_000)
        assert _DEFAULT_ESTIMATED_RMB >= worst_output + worst_input, (
            "thesis_advisory._DEFAULT_ESTIMATED_RMB under-covers the "
            "worst-case kimi review spend — re-derive it from "
            "MODEL_COST_RATES + agent_models.yaml"
        )

    def test_every_routed_model_is_priced(self) -> None:
        # Every model reachable from config/agent_models.yaml (primary,
        # fallback, and provider default_model) must carry its own
        # MODEL_COST_RATES tier — otherwise it bills at the family rate,
        # which over-counts today but silently mis-prices any future
        # model dearer than its family's current max.
        from backend.llm.fallback import MODEL_COST_RATES

        config = _load_agent_models()
        routed: set[str] = set()
        for provider_cfg in config["providers"].values():
            routed.add(provider_cfg["default_model"])
        for agent_cfg in config["agents"].values():
            routed.add(agent_cfg["model"])
            fallback = agent_cfg.get("fallback")
            if fallback:
                routed.add(fallback["model"])
        unpriced = routed - set(MODEL_COST_RATES)
        assert not unpriced, (
            f"models routed in agent_models.yaml without a MODEL_COST_RATES "
            f"tier: {sorted(unpriced)}"
        )
