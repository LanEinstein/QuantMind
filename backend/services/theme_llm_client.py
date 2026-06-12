"""Orchestration-layer adapters for the Phase Y theme-research seam.

``ThemeInvestigator`` (``backend/theme_research``) is injection-only: it
talks to an LLM and the daily budget exclusively through its *sync*
``LlmClient`` / ``UsageReserver`` Protocols and never imports the trading
stack (P0-8-amendment-2026-06-01 §3.1). These adapters are the production
implementations of those Protocols, living in the orchestration/services
layer (P0-10-amendment-2026-06-11 §4.3):

* :class:`RouterLlmClient` — bridges the sync ``complete`` call onto the
  async ``LLMRouter`` (agent entry ``theme_investigator`` →
  kimi-k2.6 + thinking, config/agent_models.yaml).
* :class:`RouterUsageReserver` — converts the investigator's token
  estimate to RMB at a conservative single rate and pre-reserves it on
  the unified ``llm:usage:{utc_date}`` counter (¥100/day hard cap,
  P1-7); answers ``False`` on refusal so the investigator aborts
  fail-closed.

Kimi thinking note (the load-bearing subtlety): for thinking-enabled kimi
agents the router GROWS the provider request's ``max_tokens`` by the
agent's ``thinking.max_tokens`` (reasoning tokens bill as output). A
naive pass-through of the investigator's ``output_cap`` would let the
provider legally emit up to ``output_cap + thinking_budget`` tokens —
busting the investigator's per-run total bound AFTER the spend and
under-covering the reservation. :class:`RouterLlmClient` therefore
SHRINKS the forwarded ``max_tokens`` by ``thinking_budget_tokens`` so
completion + thinking stays within the caller's cap; no room left raises
BEFORE any money is spent (an auditable aborted run).

Threading contract: the investigator's ``investigate()`` is synchronous
and blocking (up to ``timeout_seconds``), so the future research cron
must run it via ``asyncio.to_thread``. Both adapters capture the running
loop at construction (which happens on the loop thread during wiring)
and bridge worker-thread calls back via ``run_coroutine_threadsafe``;
calling them *on* the loop thread raises instead of deadlocking.

The wiring of the scheduled research cron itself (plus the Feishu manual
approval channel) stays a Phase Z / owner-restart item — this module
only provides the injectables.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import structlog

from backend.services.cost_guard import (
    BudgetReservation,
    DailyBudgetExceededError,
    reserve_budget,
    settle_budget,
)
from backend.theme_research import LlmCompletion

log = structlog.get_logger(component="services.theme_llm_client")

_DEFAULT_AGENT_NAME = "theme_investigator"

# Conservative tokens→RMB conversion for the pre-call reservation: price
# EVERY estimated token at the kimi-k2.6 OUTPUT realtime list rate
# (¥27/M, owner-verified 2026-06-12; the dearest direction — cache-miss
# input is ¥6.5/M), so the reservation can only over-cover. Kept ≥
# MODEL_COST_RATES["kimi-k2.6"].output_rmb_per_million by a drift test in
# tests/services/test_theme_llm_client.py — update BOTH on any future
# kimi reprice (P0-10-amendment-2026-06-11 §6). The actual spend is
# recorded by the router's track_usage; the reservation is released by
# :meth:`RouterUsageReserver.settle` (or by the cost_guard TTL if the
# caller crashes).
_DEFAULT_RMB_PER_MILLION_TOKENS = 27.0

# Mirror of the theme_investigator thinking.max_tokens budget in
# config/agent_models.yaml (drift-tested against the yaml in
# tests/services/test_theme_llm_client.py). The router adds this much to
# the provider request's max_tokens for thinking-enabled kimi agents, so
# the adapter subtracts it first to keep the caller's bound intact.
_DEFAULT_THINKING_BUDGET_TOKENS = 10_000

# Belt-and-braces bound on waiting for the loop to answer. The router's
# own client timeout (30s single call, 0 retries — P0-10) is the real
# limit; this only prevents a wedged loop from hanging the worker thread.
_DEFAULT_RESULT_TIMEOUT_SECONDS = 60.0


def _require_nonempty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")


def _require_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


def _assert_worker_thread(loop: asyncio.AbstractEventLoop) -> None:
    """Raise when called on ``loop``'s own thread (a blocking wait there
    would deadlock the loop). No-op on worker threads.

    Public adapter methods call this BEFORE constructing any coroutine so
    the misuse error can never be confused with a provider/budget answer.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return
    if running is loop:
        raise RuntimeError(
            "theme_llm_client adapters must be called from a worker thread "
            "(asyncio.to_thread), not the event-loop thread itself — a "
            "blocking wait here would deadlock the loop"
        )


def _call_on_loop(
    loop: asyncio.AbstractEventLoop,
    coro: Any,
    *,
    timeout_seconds: float,
) -> Any:
    """Run ``coro`` on ``loop`` from a worker thread and wait for it.

    Callers must have passed :func:`_assert_worker_thread` already.
    """
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        raise


def _serialize_completion(completion: Any) -> bytes:
    """Best-available raw bytes of the provider response for provenance.

    The OpenAI SDK response is a pydantic model — ``model_dump_json``
    is the faithful representation at this layer. The repr fallback
    keeps provenance capture alive across SDK surface changes (losing
    fidelity is better than losing the artifact).
    """
    dump = getattr(completion, "model_dump_json", None)
    if callable(dump):
        try:
            return str(dump()).encode("utf-8")
        except Exception:  # noqa: BLE001 — provenance must not break the run
            log.warning("theme_llm_serialize_fallback_repr")
    return repr(completion).encode("utf-8")


class RouterLlmClient:
    """Sync ``LlmClient`` Protocol implementation over the async router.

    Structural breakage (no choices / missing usage) raises so the
    investigator's catch-all turns the run into an auditable aborted
    run. An empty-text completion is returned as-is: the investigator
    captures the response bytes before its abort checks, and the strict
    output parse downstream yields no candidates anyway.
    """

    def __init__(
        self,
        *,
        router: Any,
        agent_name: str = _DEFAULT_AGENT_NAME,
        thinking_budget_tokens: int = _DEFAULT_THINKING_BUDGET_TOKENS,
        result_timeout_seconds: float = _DEFAULT_RESULT_TIMEOUT_SECONDS,
    ) -> None:
        _require_nonempty("agent_name", agent_name)
        _require_positive_int("thinking_budget_tokens", thinking_budget_tokens)
        _require_positive("result_timeout_seconds", result_timeout_seconds)
        self._router = router
        self._agent_name = agent_name
        self._thinking_budget = thinking_budget_tokens
        self._result_timeout = result_timeout_seconds
        # Raises outside an event loop — the adapters are wired during
        # async startup, and a silently captured wrong loop would
        # deadlock the first investigation.
        self._loop = asyncio.get_running_loop()

    def complete(self, *, prompt: str, max_tokens: int) -> LlmCompletion:
        """One bounded completion for the investigator (sync, blocking).

        ``max_tokens`` is the caller's TOTAL output allowance (completion
        + thinking). The router grows kimi thinking requests by the
        agent's thinking budget, so the forwarded cap is shrunk by
        ``thinking_budget_tokens`` first; if that leaves no completion
        room the call raises BEFORE any provider spend.
        """
        _require_nonempty("prompt", prompt)
        _require_positive_int("max_tokens", max_tokens)
        _assert_worker_thread(self._loop)

        completion_cap = max_tokens - self._thinking_budget
        if completion_cap < 1:
            raise ValueError(
                f"max_tokens {max_tokens} leaves no completion room after "
                f"the thinking budget {self._thinking_budget} — refusing "
                "before any provider spend"
            )

        completion = _call_on_loop(
            self._loop,
            self._router.complete(
                self._agent_name,
                [{"role": "user", "content": prompt}],
                max_tokens=completion_cap,
            ),
            timeout_seconds=self._result_timeout,
        )

        raw_bytes = _serialize_completion(completion)
        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(
                f"provider response has no readable choices: {exc}"
            ) from exc
        text = content if isinstance(content, str) else ""

        usage = getattr(completion, "usage", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if not isinstance(total_tokens, int) or isinstance(total_tokens, bool):
            # Without a usage count the investigator cannot prove the
            # per-run token bound held — fail closed into an aborted run.
            raise ValueError(
                "provider response carries no usage.total_tokens; cannot "
                "verify the per-run token bound"
            )

        model = getattr(completion, "model", "") or ""
        return LlmCompletion(
            text=text,
            raw_bytes=raw_bytes,
            model=str(model),
            tokens_used=total_tokens,
        )


class RouterUsageReserver:
    """Sync ``UsageReserver`` Protocol implementation over ``cost_guard``.

    ``reserve`` answers ``False`` on ANY failure (budget refusal or
    infra error) — the investigator treats ``False`` as an aborted run,
    which is the conservative direction for a budget gate. Every granted
    reservation is held until :meth:`settle` releases them all (the cron
    wrapper's job after ``investigate()`` returns); a crashed caller is
    covered by cost_guard's TTL.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        agent_name: str = _DEFAULT_AGENT_NAME,
        rmb_per_million_tokens: float = _DEFAULT_RMB_PER_MILLION_TOKENS,
        result_timeout_seconds: float = _DEFAULT_RESULT_TIMEOUT_SECONDS,
    ) -> None:
        _require_nonempty("agent_name", agent_name)
        _require_positive("rmb_per_million_tokens", rmb_per_million_tokens)
        _require_positive("result_timeout_seconds", result_timeout_seconds)
        self._redis = redis_client
        self._agent_name = agent_name
        self._rate = rmb_per_million_tokens
        self._result_timeout = result_timeout_seconds
        # Raises outside an event loop — see RouterLlmClient.__init__.
        self._loop = asyncio.get_running_loop()
        # Every granted reservation, not just the latest: overwriting a
        # handle would strand its amount on the shared daily counter
        # until the cost_guard TTL.
        self._held: list[BudgetReservation] = []

    def reserve(self, estimated_tokens: int) -> bool:
        """Pre-reserve the run's token estimate against the daily hard cap."""
        _require_positive_int("estimated_tokens", estimated_tokens)
        # Loop-thread misuse is a programming error, not a budget answer —
        # assert it OUTSIDE the fail-closed try so it surfaces instead of
        # reading as a (False) refusal.
        _assert_worker_thread(self._loop)
        estimated_rmb = estimated_tokens * self._rate / 1_000_000
        try:
            reservation = _call_on_loop(
                self._loop,
                reserve_budget(
                    self._redis,
                    agent_name=self._agent_name,
                    estimated_rmb=estimated_rmb,
                ),
                timeout_seconds=self._result_timeout,
            )
        except DailyBudgetExceededError:
            # Must precede the broad Exception clause only conceptually —
            # a budget refusal is an answer (False), logged distinctly
            # from infra failures.
            log.warning(
                "theme_research_reservation_refused",
                agent_name=self._agent_name,
                estimated_rmb=round(estimated_rmb, 4),
            )
            return False
        except Exception as exc:  # noqa: BLE001 — budget gate fails closed
            log.warning(
                "theme_research_reservation_failed",
                agent_name=self._agent_name,
                error=str(exc),
            )
            return False
        self._held.append(reservation)
        return True

    async def settle(self) -> None:
        """Release ALL held reservations (idempotent; async-native).

        Called by the cron wrapper after ``investigate()`` returns — the
        actual spend was already recorded by the router's track_usage.
        """
        held, self._held = self._held, []
        for reservation in held:
            try:
                await settle_budget(self._redis, reservation)
            except Exception as exc:  # noqa: BLE001 — TTL covers a failed release
                log.warning(
                    "theme_research_settle_failed",
                    agent_name=self._agent_name,
                    error=str(exc),
                )


__all__ = ["RouterLlmClient", "RouterUsageReserver"]
