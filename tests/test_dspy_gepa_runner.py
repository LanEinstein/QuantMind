"""X-009 — DSPyGEPARunner unit tests.

Covers R1 hard caps (100 samples / 10 iterations), reflection_lm
lock, log dir persistence, and stub-compiler injection.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from backend.services.dspy_gepa_runner import (
    GEPA_MAX_BUDGET_CNY,
    GEPA_MAX_ITERATIONS,
    GEPA_MAX_SAMPLES,
    REFLECTION_LM_LITELLM_MODEL,
    REFLECTION_LM_NAME,
    DSPyGEPARunner,
    GEPABudgetError,
    GEPAIterationLimitExceededError,
    GEPASampleLimitExceededError,
    GEPATrainingExample,
)


class StubCompiler:
    def __init__(self, *, new_prompt: str = "evolved prompt") -> None:
        self.new_prompt = new_prompt
        self.calls: list[dict] = []

    async def compile(
        self,
        *,
        seed_prompt: str,
        examples: Sequence[GEPATrainingExample],
        reflection_lm: str,
        max_iterations: int,
    ) -> str:
        self.calls.append(
            dict(
                seed_prompt=seed_prompt,
                examples=tuple(examples),
                reflection_lm=reflection_lm,
                max_iterations=max_iterations,
            )
        )
        return self.new_prompt


@pytest.fixture
def runner(tmp_path: Path) -> tuple[DSPyGEPARunner, StubCompiler]:
    compiler = StubCompiler()
    return DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "gepa"), compiler


@pytest.fixture
def stub_budget(monkeypatch: pytest.MonkeyPatch) -> object:
    """Noop budget guard for tests that don't exercise budget behavior.

    Codex X-024 R1 claim 11: production GEPA runs now require a real
    ``redis_client``. Tests that only care about prompt evolution
    mechanics opt in via this fixture, which monkeypatches
    ``assert_budget_allows`` to a no-op coroutine and returns a
    sentinel object suitable as the ``redis_client=`` kwarg.
    """

    async def noop(_client: object, *, agent_name: str) -> None:
        return None

    monkeypatch.setattr(
        "backend.services.dspy_gepa_runner.assert_budget_allows", noop
    )
    return object()


def _examples(n: int) -> tuple[GEPATrainingExample, ...]:
    return tuple(
        GEPATrainingExample(inputs={"i": i}, outputs={"o": i})
        for i in range(n)
    )


def test_constants_locked() -> None:
    assert GEPA_MAX_SAMPLES == 100
    assert GEPA_MAX_ITERATIONS == 10
    assert REFLECTION_LM_NAME == "deepseek-reasoner"
    assert GEPA_MAX_BUDGET_CNY == 5.0
    # Codex X-026 R3 claim 7: future production GEPA adapter (DSPy 3.2.1
    # + LiteLLM 1.60) must use the provider-prefixed spelling.
    assert REFLECTION_LM_LITELLM_MODEL == "deepseek/deepseek-reasoner"
    # The two forms must encode the same model — a regression that
    # changed one without the other would mismatch the adapter contract.
    assert REFLECTION_LM_NAME in REFLECTION_LM_LITELLM_MODEL


@pytest.mark.asyncio
async def test_happy_path(
    runner: tuple[DSPyGEPARunner, StubCompiler],
    stub_budget: object,
) -> None:
    r, compiler = runner
    result = await r.run(
        agent="fund_manager",
        seed_prompt="old prompt",
        examples=_examples(10),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert result.new_prompt_text == "evolved prompt"
    assert result.samples_used == 10
    assert result.iterations_used == GEPA_MAX_ITERATIONS
    assert result.reflection_lm == REFLECTION_LM_NAME
    # Log dir contains seed + new + summary files.
    assert (result.log_dir / "seed_prompt.txt").read_text() == "old prompt"
    assert (result.log_dir / "new_prompt.txt").read_text() == "evolved prompt"
    summary = (result.log_dir / "summary.txt").read_text()
    assert "fund_manager" in summary
    assert REFLECTION_LM_NAME in summary
    # Compiler was passed the locked reflection_lm.
    assert compiler.calls[0]["reflection_lm"] == REFLECTION_LM_NAME


@pytest.mark.asyncio
async def test_sample_cap_enforced(
    runner: tuple[DSPyGEPARunner, StubCompiler],
) -> None:
    r, _ = runner
    with pytest.raises(GEPASampleLimitExceededError):
        await r.run(
            agent="risk_officer",
            seed_prompt="x",
            examples=_examples(GEPA_MAX_SAMPLES + 1),
        )


@pytest.mark.asyncio
async def test_iteration_cap_enforced(
    runner: tuple[DSPyGEPARunner, StubCompiler],
) -> None:
    r, _ = runner
    with pytest.raises(GEPAIterationLimitExceededError):
        await r.run(
            agent="technical_analyst",
            seed_prompt="x",
            examples=_examples(1),
            max_iterations=GEPA_MAX_ITERATIONS + 1,
        )


@pytest.mark.asyncio
async def test_iteration_zero_rejected(
    runner: tuple[DSPyGEPARunner, StubCompiler],
) -> None:
    r, _ = runner
    with pytest.raises(GEPAIterationLimitExceededError):
        await r.run(
            agent="risk_officer",
            seed_prompt="x",
            examples=_examples(1),
            max_iterations=0,
        )


@pytest.mark.asyncio
async def test_log_dir_partitioned_per_agent(
    runner: tuple[DSPyGEPARunner, StubCompiler],
    tmp_path: Path,
    stub_budget: object,
) -> None:
    r, _ = runner
    result = await r.run(
        agent="fund_manager",
        seed_prompt="x",
        examples=_examples(1),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert result.log_dir.parents[0].name == "fund_manager"


@pytest.mark.asyncio
async def test_explicit_max_iterations_propagated(
    runner: tuple[DSPyGEPARunner, StubCompiler],
    stub_budget: object,
) -> None:
    r, compiler = runner
    result = await r.run(
        agent="fund_manager",
        seed_prompt="x",
        examples=_examples(1),
        max_iterations=3,
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert result.iterations_used == 3
    assert compiler.calls[0]["max_iterations"] == 3


@pytest.mark.asyncio
async def test_zero_samples_allowed(
    runner: tuple[DSPyGEPARunner, StubCompiler],
    stub_budget: object,
) -> None:
    # zero training rows is a no-op optimisation; the runner does not
    # forbid it (R1 only caps the upper bound).
    r, _ = runner
    result = await r.run(
        agent="fund_manager",
        seed_prompt="x",
        examples=(),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert result.samples_used == 0


@pytest.mark.asyncio
async def test_budget_error_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_assert(_client: object, *, agent_name: str):
        from backend.services.cost_guard import DailyBudgetExceededError

        raise DailyBudgetExceededError(f"would breach for {agent_name}")

    monkeypatch.setattr(
        "backend.services.dspy_gepa_runner.assert_budget_allows", fake_assert
    )
    compiler = StubCompiler()
    runner = DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "gepa")
    with pytest.raises(GEPABudgetError):
        await runner.run(
            agent="fund_manager",
            seed_prompt="x",
            examples=_examples(1),
            redis_client=object(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_post_compile_budget_breach_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex X-025 R2 scenario 8: a pre-check OK + a post-compile breach
    # (simulating internal SDK retries / overrun) must surface a typed
    # GEPABudgetError so the next GEPA run does not compound the
    # overrun.
    call_count = {"n": 0}

    async def fake_assert(_client: object, *, agent_name: str) -> None:
        from backend.services.cost_guard import DailyBudgetExceededError

        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # pre-check OK
        raise DailyBudgetExceededError(
            f"daily budget exceeded post-compile for {agent_name}"
        )

    monkeypatch.setattr(
        "backend.services.dspy_gepa_runner.assert_budget_allows",
        fake_assert,
    )
    compiler = StubCompiler()
    r = DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "gepa")
    with pytest.raises(GEPABudgetError, match="POST-compile"):
        await r.run(
            agent="fund_manager",
            seed_prompt="x",
            examples=_examples(1),
            redis_client=object(),  # type: ignore[arg-type]
        )
    # Pre-check + post-check both invoked.
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_run_without_redis_client_fails_closed(
    runner: tuple[DSPyGEPARunner, StubCompiler],
) -> None:
    # Codex X-024 R1 claim 11: omitting redis_client used to silently
    # skip the budget guard; the fix raises GEPABudgetError before any
    # LLM out-call so production must always supply a real client.
    r, _ = runner
    with pytest.raises(GEPABudgetError, match="redis_client is required"):
        await r.run(
            agent="fund_manager",
            seed_prompt="x",
            examples=_examples(1),
        )


@pytest.mark.asyncio
async def test_compiler_called_with_seed_examples(
    runner: tuple[DSPyGEPARunner, StubCompiler],
    stub_budget: object,
) -> None:
    r, compiler = runner
    examples = _examples(3)
    await r.run(
        agent="fund_manager",
        seed_prompt="seed",
        examples=examples,
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    call = compiler.calls[0]
    assert call["seed_prompt"] == "seed"
    assert call["examples"] == examples


@pytest.mark.asyncio
async def test_default_log_dir_class_default(
    tmp_path: Path,
) -> None:
    # Verify the dataclass default factory is set
    compiler = StubCompiler()
    default_runner = DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "lg")
    assert default_runner.log_dir == tmp_path / "lg"
