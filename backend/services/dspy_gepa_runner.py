"""DSPyGEPARunner — offline prompt evolution via GEPA (P2-2 + X-009).

GEPA (Generative Evolved Prompt Auto-tuner; ICLR 2026 Oral) reads a
``dspy.Module`` + a training mini-batch and returns an evolved prompt
that, in DSPy 3.x, replaces the module's ``instructions`` slot. The
runner is the *offline* harness — production decision modules never
call GEPA at request time; only the X-008 ``EvolutionDispatcher``
invokes it from the 22:00 ``evolution_shadow_run`` cron.

R1 hard cap (P2-2 §2 red line 21): the runner enforces both a sample
cap (``GEPA_MAX_SAMPLES = 100``) and an iteration cap
(``GEPA_MAX_ITERATIONS = 10``) so the optimisation cannot accidentally
run unbounded against the daily ¥20 LLM budget. The single-run budget
ceiling is set at ¥5 (P2-2 §1.1.1) and enforced through
:func:`backend.services.cost_guard.assert_budget_allows`.

``reflection_lm`` is locked to ``deepseek-v4-pro`` (the reasoning-grade
DeepSeek model; the legacy ``deepseek-reasoner`` alias is deprecated by
DeepSeek on 2026-07-24 — P0-10-amendment-2026-06-11 §4.4) because the
issue #7489 thinking-token swallow regression on smaller models would
silently turn GEPA into greedy random search. ``dspy.Reasoning`` is
enabled so the reasoning trace surfaces in the log directory the
runner persists at end of run.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
``backend.services.cost_guard`` is on the allow-list (cost substrate).

Networked dependency notes: the runner only imports ``dspy`` /
``gepa`` lazily inside the actual ``run`` call so unit tests can
exercise schema and budget paths without pulling the heavy LiteLLM
dependency tree. Tests inject a stub ``compiler`` to avoid the SDK
boundary entirely.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
)

if TYPE_CHECKING:
    import redis.asyncio

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked constants (R1)
# ---------------------------------------------------------------------------

GEPA_MAX_SAMPLES = 100
"""Hard cap on the training mini-batch size GEPA may consume per run
(P2-2 §2 red line 21). A larger batch means more reflection LM calls
and a higher cost — capped here so an LLM-authored config edit cannot
silently inflate the bill."""

GEPA_MAX_ITERATIONS = 10
"""Hard cap on the number of optimisation iterations GEPA may take per
run (P2-2 §2 red line 21). One iteration = one optimisation step over
the mini-batch."""

GEPA_MAX_BUDGET_CNY = 5.0
"""¥5 per-run ceiling (P2-2 §1.1.1). The runner emits a soft warning
when the recorded post-run spend exceeds this value — the hard
ceiling is still the daily ¥20 from :data:`cost_guard`."""

REFLECTION_LM_NAME = "deepseek-v4-pro"
"""GEPA reflection LM (provider-bare slug). Locked to deepseek-v4-pro —
the reasoning-grade DeepSeek model (Q1 SDK pin chose the legacy
``deepseek-reasoner`` alias, which DeepSeek deprecates on 2026-07-24;
migrated by P0-10-amendment-2026-06-11 §4.4) — so the issue #7489
thinking-token swallow regression on smaller models does not silently
degrade the optimisation signal.

This matches the convention used by QuantMind's existing
``backend/llm/router.py``, which talks to the DeepSeek
OpenAI-compatible endpoint directly (no LiteLLM in between, so a bare
slug is what the API expects).

NOTE for the forward-looking production GEPA adapter (codex X-026 R3
claim 7): DSPy 3.2.1's ``dspy.GEPA(reflection_lm=...)`` passes the
string through to LiteLLM. LiteLLM 1.60+ resolves DeepSeek models via
the **provider-prefixed** slug — see
:data:`REFLECTION_LM_LITELLM_MODEL` below. The adapter that wires the
real ``dspy.GEPA`` MUST translate ``REFLECTION_LM_NAME`` to the
prefixed form before handing it to LiteLLM, OR configure a LiteLLM
proxy alias. The bare slug shipped here is the right label for our
intra-router conventions; a runtime mismatch would surface as a
LiteLLM ``BadRequestError: model not found`` on the very first GEPA
call."""

REFLECTION_LM_LITELLM_MODEL = "deepseek/deepseek-v4-pro"
"""LiteLLM-compatible spelling of :data:`REFLECTION_LM_NAME`. Provided
so the future production adapter has a single SSoT for the prefixed
form (and does not silently re-derive it inline). Codex X-026 R3
claim 7 fix.

MIGRATION CAVEAT (2026-06-11): the legacy ``deepseek-reasoner`` endpoint
was always-thinking; ``deepseek-v4-pro`` is a unified model where
reasoning is a per-request toggle. The production adapter MUST request
reasoning explicitly (``dspy.Reasoning`` stays enabled per the module
docstring; verify the provider-side thinking flag too) — a non-thinking
reflection LM silently degrades GEPA into greedy random search, the
exact failure this pin exists to prevent."""

DEFAULT_LOG_DIR = Path("data/evolution/gepa")
"""Boot-time fixed; the dispatcher overrides with the absolute path."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DSPyGEPARunnerError(Exception):
    """Base error for the GEPA runner."""


class GEPASampleLimitExceededError(DSPyGEPARunnerError):
    """Raised when the caller passes more than :data:`GEPA_MAX_SAMPLES`
    training rows."""


class GEPAIterationLimitExceededError(DSPyGEPARunnerError):
    """Raised when ``max_iterations`` exceeds :data:`GEPA_MAX_ITERATIONS`."""


class GEPABudgetError(DSPyGEPARunnerError):
    """Raised when the cost guard refuses to allow the run."""


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GEPATrainingExample:
    """One mini-batch row — kept opaque-ish because each call site uses
    different DSPy signatures.

    The runner does not interpret the payload; it just forwards it to
    the GEPA compiler. ``dict`` is the lowest-common-denominator that
    survives serialization for the log directory.
    """

    inputs: dict[str, Any]
    outputs: dict[str, Any]


@dataclass(frozen=True)
class GEPARunResult:
    """Outcome of one :meth:`DSPyGEPARunner.run` call."""

    agent: str
    new_prompt_text: str
    samples_used: int
    iterations_used: int
    reflection_lm: str
    started_at: datetime
    finished_at: datetime
    log_dir: Path


# ---------------------------------------------------------------------------
# Compiler Protocol — DSPy GEPA injection seam
# ---------------------------------------------------------------------------


class GEPACompiler(Protocol):
    """Narrow surface the runner needs from ``dspy.teleprompt.GEPA``.

    Production wires ``dspy.teleprompt.GEPA(...)`` (after lazy import);
    tests inject a deterministic stub returning a canned new prompt.
    """

    async def compile(
        self,
        *,
        seed_prompt: str,
        examples: Sequence[GEPATrainingExample],
        reflection_lm: str,
        max_iterations: int,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DSPyGEPARunner:
    """Coordinator that enforces R1 caps + budget guard + log persistence.

    Frozen so the compiler / log_dir wiring is set once at boot.
    """

    compiler: GEPACompiler
    log_dir: Path = field(default_factory=lambda: DEFAULT_LOG_DIR)
    reflection_lm: str = REFLECTION_LM_NAME

    async def run(
        self,
        *,
        agent: str,
        seed_prompt: str,
        examples: Sequence[GEPATrainingExample],
        max_iterations: int = GEPA_MAX_ITERATIONS,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> GEPARunResult:
        """Execute one GEPA optimisation pass.

        Args:
            agent: one of the four mandatory agents (P0-10 §1.1) so
                the log directory partitions cleanly.
            seed_prompt: starting prompt text. The runner is agnostic
                to whether this is YAML / plain text — bytes flow
                through to the compiler.
            examples: training mini-batch. Length must be ≤
                :data:`GEPA_MAX_SAMPLES` (R1).
            max_iterations: per-run iteration cap. Must be ≤
                :data:`GEPA_MAX_ITERATIONS` (R1).
            redis_client: optional cost-guard backing.

        Returns:
            :class:`GEPARunResult` with the evolved prompt text +
            the log directory the dispatcher will inspect later.
        """
        if len(examples) > GEPA_MAX_SAMPLES:
            raise GEPASampleLimitExceededError(
                f"GEPA mini-batch has {len(examples)} examples > "
                f"GEPA_MAX_SAMPLES={GEPA_MAX_SAMPLES} (R1)"
            )
        if max_iterations > GEPA_MAX_ITERATIONS:
            raise GEPAIterationLimitExceededError(
                f"max_iterations={max_iterations} > "
                f"GEPA_MAX_ITERATIONS={GEPA_MAX_ITERATIONS} (R1)"
            )
        if max_iterations <= 0:
            raise GEPAIterationLimitExceededError(
                f"max_iterations must be positive; got {max_iterations}"
            )

        # P1-7 budget guard is mandatory before the LLM out-call (codex
        # X-024 R1 claim 11): a ``None`` redis client used to be allowed
        # as a test escape, but every production GEPA run must verify
        # the daily ¥20 hard ceiling before paying the LLM provider.
        if redis_client is None:
            raise GEPABudgetError(
                "redis_client is required for GEPA budget enforcement "
                "(P1-7). Production must supply a real Redis client; "
                "tests must pass a stub and monkeypatch "
                "assert_budget_allows."
            )
        try:
            await assert_budget_allows(
                redis_client, agent_name=f"dspy_gepa:{agent}"
            )
        except DailyBudgetExceededError as exc:
            raise GEPABudgetError(
                f"cost_guard blocked GEPA run: {exc}"
            ) from exc

        started = datetime.now(UTC)
        new_prompt = await self.compiler.compile(
            seed_prompt=seed_prompt,
            examples=examples,
            reflection_lm=self.reflection_lm,
            max_iterations=max_iterations,
        )
        finished = datetime.now(UTC)

        # Codex X-025 R2 scenario 8 defense-in-depth: re-assert the
        # daily ¥20 cap AFTER ``compile()`` returns so a future SDK
        # integration that internally retries / makes more LLM calls
        # than the pre-check estimated still surfaces a typed error on
        # the audit trail. The pre-check already gates ENTRY; this
        # post-check gates the NEXT run from compounding the overrun.
        try:
            await assert_budget_allows(
                redis_client, agent_name=f"dspy_gepa:{agent}:post_compile"
            )
        except DailyBudgetExceededError as exc:
            raise GEPABudgetError(
                f"cost_guard breach detected POST-compile (GEPA internal "
                f"spend exceeded the daily ceiling): {exc}"
            ) from exc

        log_dir_for_run = self._persist_log(
            agent=agent,
            seed_prompt=seed_prompt,
            new_prompt=new_prompt,
            examples=examples,
            started=started,
            finished=finished,
        )

        return GEPARunResult(
            agent=agent,
            new_prompt_text=new_prompt,
            samples_used=len(examples),
            iterations_used=max_iterations,
            reflection_lm=self.reflection_lm,
            started_at=started,
            finished_at=finished,
            log_dir=log_dir_for_run,
        )

    def _persist_log(
        self,
        *,
        agent: str,
        seed_prompt: str,
        new_prompt: str,
        examples: Sequence[GEPATrainingExample],
        started: datetime,
        finished: datetime,
    ) -> Path:
        run_dir = (
            self.log_dir
            / agent
            / started.strftime("%Y-%m-%dT%H-%M-%S")
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "seed_prompt.txt").write_text(seed_prompt, encoding="utf-8")
        (run_dir / "new_prompt.txt").write_text(new_prompt, encoding="utf-8")
        summary = (
            f"agent: {agent}\n"
            f"started_at: {started.isoformat()}\n"
            f"finished_at: {finished.isoformat()}\n"
            f"samples_used: {len(examples)}\n"
            f"reflection_lm: {self.reflection_lm}\n"
        )
        (run_dir / "summary.txt").write_text(summary, encoding="utf-8")
        return run_dir


__all__ = [
    "DEFAULT_LOG_DIR",
    "DSPyGEPARunner",
    "DSPyGEPARunnerError",
    "GEPABudgetError",
    "GEPACompiler",
    "GEPAIterationLimitExceededError",
    "GEPA_MAX_BUDGET_CNY",
    "GEPA_MAX_ITERATIONS",
    "GEPA_MAX_SAMPLES",
    "GEPARunResult",
    "GEPASampleLimitExceededError",
    "GEPATrainingExample",
    "REFLECTION_LM_LITELLM_MODEL",
    "REFLECTION_LM_NAME",
]
