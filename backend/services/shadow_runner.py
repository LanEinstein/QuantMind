"""Phase 5B exit shadow-test runner.

Consumes a finished :class:`~backend.agents.records.AnalysisRecord`,
re-runs ``fund_manager`` against a Kimi-only baseline agent
(``fund_manager_shadow_baseline``), and writes the resulting
``(baseline, routed)`` pair to the ``shadow_decisions`` collection so
``scripts/shadow_compare.py`` can compute the action-consistency /
confidence-deviation gate.

Operational contract
--------------------
* **Opt-in via env**. Default behaviour is a no-op so production is
  unaffected. Set ``QUANTMIND_SHADOW_ENABLED=1`` (along with the
  baseline agent in ``config/agent_models.yaml``) to start collection.
* **Sample rate via env**. ``QUANTMIND_SHADOW_SAMPLE_RATE`` ∈ ``(0,1]``
  scales the per-call probability. Defaults to ``1.0`` so a 7-day
  collection window fills as fast as possible; operators tune it down
  if budget pressure spikes.
* **Cost-guard checked**. Before incurring a fresh Kimi call we read
  the current ``BudgetState`` and skip on ``hard_breach`` (and on
  ``soft_breach`` when shadow is the lowest-priority workload).
* **Fire-and-forget**. The caller (``analysis_scheduler``) wraps the
  invocation in ``asyncio.create_task`` so a shadow failure cannot
  block the live trading pipeline; we additionally swallow every
  exception here as a defence-in-depth.
* **No risk-engine coupling**. ``backend/risk/`` redline holds — this
  module reads only from ``backend.services``, ``backend.llm``, and
  ``backend.agents.records`` typing.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from backend.agents.base import extract_json_from_response
from backend.services.cost_guard import get_budget_state
from backend.services.shadow_recorder import (
    ShadowDecisionEntry,
    ShadowDecisionLeg,
    record_shadow_decision,
)

if TYPE_CHECKING:
    import redis.asyncio

    from backend.agents.models import AnalysisServices
    from backend.agents.records import AnalysisRecord

log = structlog.get_logger(component="shadow_runner")

SHADOW_BASELINE_AGENT = "fund_manager_shadow_baseline"
"""Agent key in ``config/agent_models.yaml`` whose YAML must NOT have
a ``routing`` block — the whole point of the shadow leg is to bypass
tiered routing and replay against the original Kimi-only behaviour."""

SHADOW_ENABLED_ENV = "QUANTMIND_SHADOW_ENABLED"
SHADOW_SAMPLE_RATE_ENV = "QUANTMIND_SHADOW_SAMPLE_RATE"

_DEFAULT_SAMPLE_RATE = 1.0
_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})

# Serialise budget-check + Kimi call across all in-flight shadow tasks.
# Without this lock, several fire-and-forget shadow coroutines could
# pass the cost-guard probe in parallel before any of their Kimi
# usage was tracked back into Redis, blowing past the daily ceiling
# even though each one individually saw "ok" (codex P5B-shadow R1 P2).
# The lock is process-local — Phase 5B targets WEB_CONCURRENCY=1 so
# one process owns the cron + API. Cross-process serialisation would
# need a Redis reservation; tracked as a Phase 5C deferral.
_shadow_gate: asyncio.Lock | None = None

# Admission-control bound (codex P5B-shadow R3 P2). With the global
# gate serialising baseline calls, a stuck Kimi call would otherwise
# accumulate one fire-and-forget task per completed analysis, each
# retaining its full ``AnalysisRecord`` and rebuilt prompt. Cap the
# in-flight backlog so a slow window degrades gracefully (drops
# samples + warns) instead of blowing memory.
_MAX_INFLIGHT_SHADOW = 4
_inflight_shadow = 0

# Hard timeout around the baseline Kimi call. Mirrors the slow-bucket
# pipeline timeout (900s) — the baseline replays a slow run, so this
# is the matching ceiling. A request that hits the timeout drops
# instead of holding the gate forever and starving the queue.
_BASELINE_CALL_TIMEOUT_SEC = 900.0


def _get_shadow_gate() -> asyncio.Lock:
    """Lazy-init the shadow gate inside the running loop.

    Creating the Lock at module import binds it to whatever loop
    happens to exist at that moment (often the test runner's),
    triggering "attached to a different loop" errors in pytest.
    Lazy creation defers binding to the first ``run_shadow`` call.
    """
    global _shadow_gate
    if _shadow_gate is None:
        _shadow_gate = asyncio.Lock()
    return _shadow_gate


def is_enabled() -> bool:
    """Return True iff ``QUANTMIND_SHADOW_ENABLED`` is set to a truthy value."""
    raw = os.environ.get(SHADOW_ENABLED_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sample_rate() -> float:
    """Read ``QUANTMIND_SHADOW_SAMPLE_RATE`` clamped to ``[0.0, 1.0]``.

    Malformed values fall back to the default rather than crash the
    pipeline — shadow recording is observability, never a hard
    dependency.
    """
    raw = os.environ.get(SHADOW_SAMPLE_RATE_ENV, "").strip()
    if not raw:
        return _DEFAULT_SAMPLE_RATE
    try:
        value = float(raw)
    except ValueError:
        # Codex P5B-shadow R5 LOW: don't echo raw env-var contents
        # into the structured log — the security rule is "no env
        # values in logs". A typed marker is enough for ops to
        # diagnose without the value itself surfacing.
        log.warning(
            "shadow_sample_rate_parse_failed",
            raw_type=type(raw).__name__,
            raw_len=len(raw),
            fallback=_DEFAULT_SAMPLE_RATE,
        )
        return _DEFAULT_SAMPLE_RATE
    if not math.isfinite(value):
        return _DEFAULT_SAMPLE_RATE
    return max(0.0, min(1.0, value))


def _rebuild_user_content(record: AnalysisRecord) -> str | None:
    """Reproduce the prompt that ``fund_manager_node`` originally received.

    The live node assembles the prompt from the shared LangGraph state
    (``backend.agents.fund_manager.fund_manager_node``). We rebuild the
    same string from the persisted record so the baseline call sees an
    identical context. Any missing key drops the shadow attempt — the
    point of the comparison is to exercise the SAME prompt twice, not
    to fabricate a partial one.

    Returns ``None`` when the record is too thin to reconstruct (e.g.
    a partial-failure run).
    """
    by_agent = {step.agent: step.content for step in record.steps}
    required = (
        "news_crawler",
        "sentiment_analyst",
        "fundamental_analyst",
        "technical_analyst",
        "intelligence_officer",
        "risk_officer",
    )
    if any(name not in by_agent for name in required):
        return None

    debate_history = _join_debates(record)

    return (
        f"目标股票: {record.stock_code} {record.stock_name}\n"
        f"分析日期: {record.trade_date}\n\n"
        f"=== 新闻分析 ===\n{by_agent['news_crawler']}\n\n"
        f"=== 情绪分析 ===\n{by_agent['sentiment_analyst']}\n\n"
        f"=== 基本面分析 ===\n{by_agent['fundamental_analyst']}\n\n"
        f"=== 技术分析 ===\n{by_agent['technical_analyst']}\n\n"
        f"=== 情报研判 ===\n{by_agent['intelligence_officer']}\n\n"
        f"=== 多空辩论记录 ===\n{debate_history}\n\n"
        f"=== 风控评估 ===\n{by_agent['risk_officer']}"
    )


def _join_debates(record: AnalysisRecord) -> str:
    """Serialise the debate rounds in the live ``state.debate.history`` shape.

    The live ``debate_state["history"]`` is built by
    :mod:`backend.agents.bull_researcher` / :mod:`bear_researcher`
    appending ``"\\n\\n【看多研究员】\\n{argument}"`` and
    ``"\\n\\n【看空研究员】\\n{argument}"`` per turn. ``current_response``
    uses the ``Bull: ...`` / ``Bear: ...`` shorthand — but that's the
    intermediate routing token, NOT what fund_manager actually sees.
    Replaying with the wrong format would make the baseline measure
    prompt-format drift instead of routing impact (codex P5B-shadow R1
    P1).
    """
    parts: list[str] = []
    for round_record in record.debates:
        if round_record.bull is not None:
            parts.append(f"\n\n【看多研究员】\n{round_record.bull.content}")
        if round_record.bear is not None:
            parts.append(f"\n\n【看空研究员】\n{round_record.bear.content}")
    return "".join(parts)


def _parse_baseline_response(raw: str) -> tuple[str, float, bool]:
    """Parse the baseline JSON envelope using the live extractor.

    Reuses :func:`backend.agents.base.extract_json_from_response` so
    the baseline parse contract matches what production fund_manager
    accepts (codex P5B-shadow R2 P2): a stricter shadow-only regex
    would otherwise mark valid responses as parse-failed and inflate
    the parse-failure metric. Coercion mirrors
    ``backend.agents.fund_manager._parse_signal``: ``confidence`` is
    accepted as int / float / numeric string, then range-validated.

    Returns ``(action, confidence, parse_ok)``. On any contract
    violation we fall back to the synthetic ``持有 / 0.5`` placeholder
    and ``parse_ok=False`` — shadow_compare excludes those from the
    gate math but the entry still lands so parse-failure rate is
    observable per leg.
    """
    if not isinstance(raw, str) or not raw:
        return "持有", 0.5, False
    data = extract_json_from_response(raw)
    if not isinstance(data, dict):
        return "持有", 0.5, False

    # Match live ``_parse_signal``'s defaults: missing ``action`` ⇒
    # 持有, missing ``confidence`` ⇒ 0.5, both with parse_ok=True
    # because the JSON envelope itself was valid (codex P5B-shadow R6
    # UNRESOLVED). Treating those as parse_failed would over-count
    # baseline parse failures relative to the production rate.
    action = data.get("action", "持有")
    if action not in _VALID_ACTIONS:
        return "持有", 0.5, False

    confidence_raw = data.get("confidence", 0.5)
    if isinstance(confidence_raw, bool):
        # bool ⊂ int — exclude explicitly so True/False can't slip past.
        return "持有", 0.5, False
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        return "持有", 0.5, False
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        return "持有", 0.5, False
    return action, confidence, True


def _routed_leg_from_record(record: AnalysisRecord) -> ShadowDecisionLeg | None:
    """Build the routed leg from the production AnalysisRecord.

    ``parse_ok`` is lifted from :class:`FundManagerRecord.parse_ok`
    (defaults to True for legacy records). When the live decision was
    a synthetic 持有/0.5 fallback, the flag is False and shadow_compare
    excludes the pair from gate math (codex P5B-shadow R2 P2).
    """
    decision = record.decision
    if decision is None:
        return None
    step = decision.step
    started = step.started_at
    completed = step.completed_at
    if started is None or completed is None or completed < started:
        latency_ms = 0.0
    else:
        latency_ms = (completed - started).total_seconds() * 1000.0

    try:
        return ShadowDecisionLeg(
            action=decision.action,
            confidence=float(decision.confidence),
            # The router's per-call route taken (triage vs escalation)
            # is observable only through ``llm:escalations`` Redis
            # counters; it is an aggregate signal, not per-run. Using
            # a stable label here keeps the schema honest while the
            # aggregate stays accessible via /api/monitoring/llm/escalations.
            model=step.model_id or "routed-fund-manager",
            latency_ms=latency_ms,
            escalated=False,
            parse_ok=decision.parse_ok,
        )
    except ValueError as exc:
        log.warning(
            "shadow_routed_leg_invalid",
            run_id=record.run_id,
            error=str(exc),
        )
        return None


async def _budget_allows(
    redis_client: redis.asyncio.Redis | None,
) -> bool:
    """Return True when the daily budget has headroom for one more Kimi call.

    A Redis hiccup returns False (fail-closed): we'd rather skip a
    shadow record than incur a Kimi call we can't account for. The
    cost-guard module itself fails-closed on corrupt cost data.
    """
    if redis_client is None:
        # Without Redis we can't reason about today's spend. Skip rather
        # than risk silently amplifying cost during the 7-day window.
        return False
    try:
        state = await get_budget_state(redis_client)
    except Exception as exc:
        log.warning("shadow_budget_check_failed", error=str(exc))
        return False
    return state.status == "ok"


async def run_shadow(
    services: AnalysisServices,
    record: AnalysisRecord,
    redis_client: redis.asyncio.Redis | None,
) -> bool:
    """Re-run fund_manager against the kimi-only baseline + persist the pair.

    This is the entry point ``analysis_scheduler`` schedules via
    ``asyncio.create_task``. Returns True on a successful write,
    False otherwise. Callers ignore the return value — it exists only
    for tests and structured logging.
    """
    if not is_enabled():
        return False

    rate = _sample_rate()
    if rate <= 0.0 or random.random() > rate:
        return False

    if record.decision is None:
        log.info("shadow_skipped_no_decision", run_id=record.run_id)
        return False

    # Check Mongo BEFORE the budget probe + Kimi call. Without Mongo
    # the eventual ``record_shadow_decision`` cannot land — burning a
    # paid Kimi call only to discard the result is a silent leak
    # (codex P5B-shadow R2 P2).
    if services.mongodb is None:
        log.info("shadow_skipped_no_mongo", run_id=record.run_id)
        return False

    user_content = _rebuild_user_content(record)
    if user_content is None:
        log.info("shadow_skipped_partial_record", run_id=record.run_id)
        return False

    routed_leg = _routed_leg_from_record(record)
    if routed_leg is None:
        return False

    # Imported lazily so a missing prompt module never crashes module
    # load (production has it; tests stub the agent).
    from backend.agents.prompts import FUND_MANAGER_PROMPT

    # Serialise the budget probe + Kimi call so concurrent fire-and-
    # forget tasks cannot all observe "ok" before any of their usage
    # has been tracked back into Redis. Without this, a 5-stock cron
    # tick could launch 5 simultaneous baseline calls past the daily
    # ceiling on a tight-budget day (codex P5B-shadow R1 P2).
    async with _get_shadow_gate():
        if not await _budget_allows(redis_client):
            log.info("shadow_skipped_budget", run_id=record.run_id)
            return False

        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                services.llm_router.complete(
                    agent_name=SHADOW_BASELINE_AGENT,
                    messages=[
                        {"role": "system", "content": FUND_MANAGER_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                ),
                timeout=_BASELINE_CALL_TIMEOUT_SEC,
            )
        except TimeoutError:
            # Drop the slow baseline rather than hold the gate
            # forever — every other queued shadow task is waiting
            # behind it (codex P5B-shadow R3 P2).
            log.warning(
                "shadow_baseline_call_timeout",
                run_id=record.run_id,
                timeout=_BASELINE_CALL_TIMEOUT_SEC,
            )
            return False
        except Exception as exc:
            log.warning(
                "shadow_baseline_call_failed",
                run_id=record.run_id,
                error=str(exc),
            )
            return False
        latency_ms = (time.perf_counter() - started) * 1000.0

    raw = ""
    try:
        raw = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        raw = ""

    action, confidence, parse_ok = _parse_baseline_response(raw)
    try:
        baseline_leg = ShadowDecisionLeg(
            action=action,
            confidence=confidence,
            model="kimi-k2.6-baseline",
            latency_ms=latency_ms,
            escalated=False,
            parse_ok=parse_ok,
        )
    except ValueError as exc:
        log.warning(
            "shadow_baseline_leg_invalid",
            run_id=record.run_id,
            error=str(exc),
        )
        return False

    try:
        entry = ShadowDecisionEntry(
            run_id=record.run_id,
            stock_code=record.stock_code,
            trade_date=record.trade_date,
            created_at=datetime.now(tz=UTC),
            baseline=baseline_leg,
            routed=routed_leg,
        )
    except ValueError as exc:
        log.warning(
            "shadow_entry_build_failed",
            run_id=record.run_id,
            error=str(exc),
        )
        return False

    return await record_shadow_decision(services.mongodb, entry)


def schedule_shadow_run(
    services: AnalysisServices,
    record: AnalysisRecord,
    redis_client: redis.asyncio.Redis | None,
) -> asyncio.Task[bool] | None:
    """Schedule :func:`run_shadow` as a fire-and-forget background task.

    Returns the scheduled :class:`asyncio.Task` (so tests can await it)
    or ``None`` when shadow is disabled / the record is unfit / the
    in-flight backlog is full.

    The scheduler intentionally does NOT await — a shadow run can take
    seconds to minutes (a fresh Kimi call), and blocking the analysis
    scheduler on it would defeat the whole "fire and forget" property.
    Errors inside the task are absorbed by ``run_shadow`` itself; we
    additionally attach a done-callback so an unexpected exception is
    logged rather than warned about by the asyncio runtime.

    Admission control (codex P5B-shadow R3 P2): drop new shadow runs
    once ``_MAX_INFLIGHT_SHADOW`` are already pending. The global
    gate serialises them, so a slow baseline call would otherwise
    queue an unbounded number of tasks each retaining their full
    ``AnalysisRecord``.
    """
    if not is_enabled():
        return None

    global _inflight_shadow
    if _inflight_shadow >= _MAX_INFLIGHT_SHADOW:
        log.warning(
            "shadow_skipped_backlog_full",
            run_id=record.run_id,
            inflight=_inflight_shadow,
            cap=_MAX_INFLIGHT_SHADOW,
        )
        return None

    _inflight_shadow += 1
    coro = run_shadow(services, record, redis_client)
    task = asyncio.create_task(coro, name=f"shadow_run:{record.run_id}")

    def _on_done(t: asyncio.Task[bool]) -> None:
        global _inflight_shadow
        _inflight_shadow -= 1
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.warning(
                "shadow_run_task_exception",
                run_id=record.run_id,
                error=repr(exc),
            )

    task.add_done_callback(_on_done)
    return task


__all__ = [
    "SHADOW_BASELINE_AGENT",
    "SHADOW_ENABLED_ENV",
    "SHADOW_SAMPLE_RATE_ENV",
    "is_enabled",
    "run_shadow",
    "schedule_shadow_run",
]
