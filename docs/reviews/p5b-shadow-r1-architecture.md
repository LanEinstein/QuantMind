OpenAI Codex v0.128.0 (research preview)
--------
workdir: /home/ps/papers/QuantMind
model: gpt-5.5
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, /home/ps/.codex/memories] (network access enabled)
reasoning effort: xhigh
reasoning summaries: none
session id: 019de841-3c6d-7242-8713-d7b76aa86ac3
--------
user
current changes
exec
/bin/bash -lc 'git status --short && git rev-parse --show-toplevel' in /home/ps/papers/QuantMind
 succeeded in 0ms:
M  backend/data/analysis_scheduler.py
A  backend/services/shadow_runner.py
M  config/agent_models.yaml
M  tests/test_llm_router_thinking.py
A  tests/test_shadow_runner.py
/home/ps/papers/QuantMind

exec
/bin/bash -lc 'git diff --cached --stat && git diff --cached -- backend/data/analysis_scheduler.py backend/services/shadow_runner.py config/agent_models.yaml tests/test_llm_router_thinking.py tests/test_shadow_runner.py' in /home/ps/papers/QuantMind
 succeeded in 0ms:
 backend/data/analysis_scheduler.py |  13 +
 backend/services/shadow_runner.py  | 408 ++++++++++++++++++++++++++
 config/agent_models.yaml           |  17 ++
 tests/test_llm_router_thinking.py  |   7 +-
 tests/test_shadow_runner.py        | 567 +++++++++++++++++++++++++++++++++++++
 5 files changed, 1011 insertions(+), 1 deletion(-)
diff --git a/backend/data/analysis_scheduler.py b/backend/data/analysis_scheduler.py
index 03ff249..cacfd1c 100644
--- a/backend/data/analysis_scheduler.py
+++ b/backend/data/analysis_scheduler.py
@@ -54,6 +54,7 @@ from backend.services.cost_guard import (
     DailyBudgetExceededError,
     assert_budget_allows,
 )
+from backend.services.shadow_runner import schedule_shadow_run
 from backend.services.watchlist_policy import (
     Category,
     WatchlistPolicy,
@@ -540,6 +541,18 @@ class AnalysisScheduler:
                 error=str(exc),
             )
 
+        # Phase 5B exit shadow-test: opt-in via QUANTMIND_SHADOW_ENABLED.
+        # Schedule the baseline replay as fire-and-forget so a slow Kimi
+        # call cannot stall the next stock in the cron tick.
+        try:
+            schedule_shadow_run(services, record_with_signal, self._redis)
+        except Exception as exc:
+            log.warning(
+                "shadow_schedule_failed",
+                code=stock_code,
+                error=str(exc),
+            )
+
         await self._publish_signal(signal_dict)
         return signal
 
diff --git a/backend/services/shadow_runner.py b/backend/services/shadow_runner.py
new file mode 100644
index 0000000..2ad31d2
--- /dev/null
+++ b/backend/services/shadow_runner.py
@@ -0,0 +1,408 @@
+"""Phase 5B exit shadow-test runner.
+
+Consumes a finished :class:`~backend.agents.records.AnalysisRecord`,
+re-runs ``fund_manager`` against a Kimi-only baseline agent
+(``fund_manager_shadow_baseline``), and writes the resulting
+``(baseline, routed)`` pair to the ``shadow_decisions`` collection so
+``scripts/shadow_compare.py`` can compute the action-consistency /
+confidence-deviation gate.
+
+Operational contract
+--------------------
+* **Opt-in via env**. Default behaviour is a no-op so production is
+  unaffected. Set ``QUANTMIND_SHADOW_ENABLED=1`` (along with the
+  baseline agent in ``config/agent_models.yaml``) to start collection.
+* **Sample rate via env**. ``QUANTMIND_SHADOW_SAMPLE_RATE`` ∈ ``(0,1]``
+  scales the per-call probability. Defaults to ``1.0`` so a 7-day
+  collection window fills as fast as possible; operators tune it down
+  if budget pressure spikes.
+* **Cost-guard checked**. Before incurring a fresh Kimi call we read
+  the current ``BudgetState`` and skip on ``hard_breach`` (and on
+  ``soft_breach`` when shadow is the lowest-priority workload).
+* **Fire-and-forget**. The caller (``analysis_scheduler``) wraps the
+  invocation in ``asyncio.create_task`` so a shadow failure cannot
+  block the live trading pipeline; we additionally swallow every
+  exception here as a defence-in-depth.
+* **No risk-engine coupling**. ``backend/risk/`` redline holds — this
+  module reads only from ``backend.services``, ``backend.llm``, and
+  ``backend.agents.records`` typing.
+"""
+
+from __future__ import annotations
+
+import asyncio
+import json
+import math
+import os
+import random
+import re
+import time
+from datetime import UTC, datetime
+from typing import TYPE_CHECKING
+
+import structlog
+
+from backend.services.cost_guard import get_budget_state
+from backend.services.shadow_recorder import (
+    ShadowDecisionEntry,
+    ShadowDecisionLeg,
+    record_shadow_decision,
+)
+
+if TYPE_CHECKING:
+    import redis.asyncio
+
+    from backend.agents.models import AnalysisServices
+    from backend.agents.records import AnalysisRecord
+
+log = structlog.get_logger(component="shadow_runner")
+
+SHADOW_BASELINE_AGENT = "fund_manager_shadow_baseline"
+"""Agent key in ``config/agent_models.yaml`` whose YAML must NOT have
+a ``routing`` block — the whole point of the shadow leg is to bypass
+tiered routing and replay against the original Kimi-only behaviour."""
+
+SHADOW_ENABLED_ENV = "QUANTMIND_SHADOW_ENABLED"
+SHADOW_SAMPLE_RATE_ENV = "QUANTMIND_SHADOW_SAMPLE_RATE"
+
+_DEFAULT_SAMPLE_RATE = 1.0
+_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})
+# Match :func:`backend.agents.base.extract_json_from_response`'s contract:
+# accept either a bare JSON object or one wrapped in a fenced block.
+_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
+
+
+def is_enabled() -> bool:
+    """Return True iff ``QUANTMIND_SHADOW_ENABLED`` is set to a truthy value."""
+    raw = os.environ.get(SHADOW_ENABLED_ENV, "")
+    return raw.strip().lower() in {"1", "true", "yes", "on"}
+
+
+def _sample_rate() -> float:
+    """Read ``QUANTMIND_SHADOW_SAMPLE_RATE`` clamped to ``[0.0, 1.0]``.
+
+    Malformed values fall back to the default rather than crash the
+    pipeline — shadow recording is observability, never a hard
+    dependency.
+    """
+    raw = os.environ.get(SHADOW_SAMPLE_RATE_ENV, "").strip()
+    if not raw:
+        return _DEFAULT_SAMPLE_RATE
+    try:
+        value = float(raw)
+    except ValueError:
+        log.warning(
+            "shadow_sample_rate_parse_failed",
+            raw=raw,
+            fallback=_DEFAULT_SAMPLE_RATE,
+        )
+        return _DEFAULT_SAMPLE_RATE
+    if not math.isfinite(value):
+        return _DEFAULT_SAMPLE_RATE
+    return max(0.0, min(1.0, value))
+
+
+def _rebuild_user_content(record: AnalysisRecord) -> str | None:
+    """Reproduce the prompt that ``fund_manager_node`` originally received.
+
+    The live node assembles the prompt from the shared LangGraph state
+    (``backend.agents.fund_manager.fund_manager_node``). We rebuild the
+    same string from the persisted record so the baseline call sees an
+    identical context. Any missing key drops the shadow attempt — the
+    point of the comparison is to exercise the SAME prompt twice, not
+    to fabricate a partial one.
+
+    Returns ``None`` when the record is too thin to reconstruct (e.g.
+    a partial-failure run).
+    """
+    by_agent = {step.agent: step.content for step in record.steps}
+    required = (
+        "news_crawler",
+        "sentiment_analyst",
+        "fundamental_analyst",
+        "technical_analyst",
+        "intelligence_officer",
+        "risk_officer",
+    )
+    if any(name not in by_agent for name in required):
+        return None
+
+    debate_history = _join_debates(record)
+
+    return (
+        f"目标股票: {record.stock_code} {record.stock_name}\n"
+        f"分析日期: {record.trade_date}\n\n"
+        f"=== 新闻分析 ===\n{by_agent['news_crawler']}\n\n"
+        f"=== 情绪分析 ===\n{by_agent['sentiment_analyst']}\n\n"
+        f"=== 基本面分析 ===\n{by_agent['fundamental_analyst']}\n\n"
+        f"=== 技术分析 ===\n{by_agent['technical_analyst']}\n\n"
+        f"=== 情报研判 ===\n{by_agent['intelligence_officer']}\n\n"
+        f"=== 多空辩论记录 ===\n{debate_history}\n\n"
+        f"=== 风控评估 ===\n{by_agent['risk_officer']}"
+    )
+
+
+def _join_debates(record: AnalysisRecord) -> str:
+    """Serialise the debate rounds back into the ``Bull:/Bear:`` log form.
+
+    The live node consumes ``state["debate_state"]["history"]`` which is
+    ``"Bull: ...\\nBear: ..."`` per turn. The collector saves bull/bear
+    contents per-round; we recombine in round order so the baseline
+    call sees the same conversation transcript.
+    """
+    lines: list[str] = []
+    for round_record in record.debates:
+        if round_record.bull is not None:
+            lines.append(f"Bull: {round_record.bull.content}")
+        if round_record.bear is not None:
+            lines.append(f"Bear: {round_record.bear.content}")
+    return "\n".join(lines)
+
+
+def _parse_baseline_response(raw: str) -> tuple[str, float, bool]:
+    """Best-effort parse of the baseline JSON envelope.
+
+    Returns ``(action, confidence, parse_ok)``. The ``parse_ok`` flag
+    is the single source of truth for whether the leg ought to count
+    as a "clean" sample upstream. Out-of-contract responses still get
+    an entry written (parse_ok=False) so shadow_compare can surface
+    parse-failure rate as a quality metric.
+    """
+    if not isinstance(raw, str) or not raw:
+        return "持有", 0.5, False
+
+    match = _JSON_BLOCK_RE.search(raw)
+    if match is None:
+        return "持有", 0.5, False
+    try:
+        data = json.loads(match.group(0))
+    except (json.JSONDecodeError, ValueError):
+        return "持有", 0.5, False
+    if not isinstance(data, dict):
+        return "持有", 0.5, False
+
+    action = data.get("action")
+    confidence_raw = data.get("confidence")
+
+    if action not in _VALID_ACTIONS:
+        return "持有", 0.5, False
+    if isinstance(confidence_raw, bool) or not isinstance(
+        confidence_raw, (int, float)
+    ):
+        return "持有", 0.5, False
+    confidence = float(confidence_raw)
+    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
+        return "持有", 0.5, False
+    return action, confidence, True
+
+
+def _routed_leg_from_record(record: AnalysisRecord) -> ShadowDecisionLeg | None:
+    """Build the routed leg from the production AnalysisRecord."""
+    decision = record.decision
+    if decision is None:
+        return None
+    step = decision.step
+    started = step.started_at
+    completed = step.completed_at
+    if started is None or completed is None or completed < started:
+        latency_ms = 0.0
+    else:
+        latency_ms = (completed - started).total_seconds() * 1000.0
+
+    try:
+        return ShadowDecisionLeg(
+            action=decision.action,
+            confidence=float(decision.confidence),
+            # The router's per-call route taken (triage vs escalation)
+            # is observable only through ``llm:escalations`` Redis
+            # counters; it is an aggregate signal, not per-run. Using
+            # a stable label here keeps the schema honest while the
+            # aggregate stays accessible via /api/monitoring/llm/escalations.
+            model=step.model_id or "routed-fund-manager",
+            latency_ms=latency_ms,
+            escalated=False,
+            parse_ok=True,
+        )
+    except ValueError as exc:
+        log.warning(
+            "shadow_routed_leg_invalid",
+            run_id=record.run_id,
+            error=str(exc),
+        )
+        return None
+
+
+async def _budget_allows(
+    redis_client: redis.asyncio.Redis | None,
+) -> bool:
+    """Return True when the daily budget has headroom for one more Kimi call.
+
+    A Redis hiccup returns False (fail-closed): we'd rather skip a
+    shadow record than incur a Kimi call we can't account for. The
+    cost-guard module itself fails-closed on corrupt cost data.
+    """
+    if redis_client is None:
+        # Without Redis we can't reason about today's spend. Skip rather
+        # than risk silently amplifying cost during the 7-day window.
+        return False
+    try:
+        state = await get_budget_state(redis_client)
+    except Exception as exc:
+        log.warning("shadow_budget_check_failed", error=str(exc))
+        return False
+    return state.status == "ok"
+
+
+async def run_shadow(
+    services: AnalysisServices,
+    record: AnalysisRecord,
+    redis_client: redis.asyncio.Redis | None,
+) -> bool:
+    """Re-run fund_manager against the kimi-only baseline + persist the pair.
+
+    This is the entry point ``analysis_scheduler`` schedules via
+    ``asyncio.create_task``. Returns True on a successful write,
+    False otherwise. Callers ignore the return value — it exists only
+    for tests and structured logging.
+    """
+    if not is_enabled():
+        return False
+
+    rate = _sample_rate()
+    if rate <= 0.0 or random.random() > rate:
+        return False
+
+    if record.decision is None:
+        log.info("shadow_skipped_no_decision", run_id=record.run_id)
+        return False
+
+    if not await _budget_allows(redis_client):
+        log.info("shadow_skipped_budget", run_id=record.run_id)
+        return False
+
+    user_content = _rebuild_user_content(record)
+    if user_content is None:
+        log.info("shadow_skipped_partial_record", run_id=record.run_id)
+        return False
+
+    routed_leg = _routed_leg_from_record(record)
+    if routed_leg is None:
+        return False
+
+    # Imported lazily so a missing prompt module never crashes module
+    # load (production has it; tests stub the agent).
+    from backend.agents.prompts import FUND_MANAGER_PROMPT
+
+    started = time.perf_counter()
+    try:
+        response = await services.llm_router.complete(
+            agent_name=SHADOW_BASELINE_AGENT,
+            messages=[
+                {"role": "system", "content": FUND_MANAGER_PROMPT},
+                {"role": "user", "content": user_content},
+            ],
+        )
+    except Exception as exc:
+        log.warning(
+            "shadow_baseline_call_failed",
+            run_id=record.run_id,
+            error=str(exc),
+        )
+        return False
+    latency_ms = (time.perf_counter() - started) * 1000.0
+
+    raw = ""
+    try:
+        raw = response.choices[0].message.content or ""
+    except (AttributeError, IndexError, TypeError):
+        raw = ""
+
+    action, confidence, parse_ok = _parse_baseline_response(raw)
+    try:
+        baseline_leg = ShadowDecisionLeg(
+            action=action,
+            confidence=confidence,
+            model="kimi-k2.6-baseline",
+            latency_ms=latency_ms,
+            escalated=False,
+            parse_ok=parse_ok,
+        )
+    except ValueError as exc:
+        log.warning(
+            "shadow_baseline_leg_invalid",
+            run_id=record.run_id,
+            error=str(exc),
+        )
+        return False
+
+    try:
+        entry = ShadowDecisionEntry(
+            run_id=record.run_id,
+            stock_code=record.stock_code,
+            trade_date=record.trade_date,
+            created_at=datetime.now(tz=UTC),
+            baseline=baseline_leg,
+            routed=routed_leg,
+        )
+    except ValueError as exc:
+        log.warning(
+            "shadow_entry_build_failed",
+            run_id=record.run_id,
+            error=str(exc),
+        )
+        return False
+
+    mongodb = services.mongodb
+    if mongodb is None:
+        log.info("shadow_skipped_no_mongo", run_id=record.run_id)
+        return False
+
+    return await record_shadow_decision(mongodb, entry)
+
+
+def schedule_shadow_run(
+    services: AnalysisServices,
+    record: AnalysisRecord,
+    redis_client: redis.asyncio.Redis | None,
+) -> asyncio.Task[bool] | None:
+    """Schedule :func:`run_shadow` as a fire-and-forget background task.
+
+    Returns the scheduled :class:`asyncio.Task` (so tests can await it)
+    or ``None`` when shadow is disabled / the record is unfit.
+
+    The scheduler intentionally does NOT await — a shadow run can take
+    seconds to minutes (a fresh Kimi call), and blocking the analysis
+    scheduler on it would defeat the whole "fire and forget" property.
+    Errors inside the task are absorbed by ``run_shadow`` itself; we
+    additionally attach a done-callback so an unexpected exception is
+    logged rather than warned about by the asyncio runtime.
+    """
+    if not is_enabled():
+        return None
+
+    coro = run_shadow(services, record, redis_client)
+    task = asyncio.create_task(coro, name=f"shadow_run:{record.run_id}")
+
+    def _on_done(t: asyncio.Task[bool]) -> None:
+        if t.cancelled():
+            return
+        exc = t.exception()
+        if exc is not None:
+            log.warning(
+                "shadow_run_task_exception",
+                run_id=record.run_id,
+                error=repr(exc),
+            )
+
+    task.add_done_callback(_on_done)
+    return task
+
+
+__all__ = [
+    "SHADOW_BASELINE_AGENT",
+    "SHADOW_ENABLED_ENV",
+    "SHADOW_SAMPLE_RATE_ENV",
+    "is_enabled",
+    "run_shadow",
+    "schedule_shadow_run",
+]
diff --git a/config/agent_models.yaml b/config/agent_models.yaml
index 06d25cc..8e9af7f 100644
--- a/config/agent_models.yaml
+++ b/config/agent_models.yaml
@@ -176,3 +176,20 @@ agents:
       keep: last_round
     frequency: "per_trading_day"
     task: "综合所有Agent报告，输出最终买卖信号"
+
+  # Phase 5B exit shadow-test baseline (consumed only by
+  # backend.services.shadow_runner when QUANTMIND_SHADOW_ENABLED=1).
+  # Identical to fund_manager EXCEPT the routing block is intentionally
+  # absent — this leg always calls Kimi directly so we can compare the
+  # tiered-routing decision against the original kimi-only baseline.
+  fund_manager_shadow_baseline:
+    name: "基金经理 (Shadow Baseline / Kimi-only)"
+    provider: kimi
+    model: kimi-k2.6
+    fallback: { provider: deepseek, model: deepseek-v4-pro }
+    thinking:
+      type: enabled
+      max_tokens: 8000
+      keep: last_round
+    frequency: "shadow_only"
+    task: "Phase 5B 出口 7-day shadow window 的 baseline leg；仅在 shadow_runner 启用时被调用，永远不进入实盘决策路径"
diff --git a/tests/test_llm_router_thinking.py b/tests/test_llm_router_thinking.py
index db908dd..d87ae7c 100644
--- a/tests/test_llm_router_thinking.py
+++ b/tests/test_llm_router_thinking.py
@@ -327,6 +327,11 @@ _PROD_THINKING_TABLE: dict[str, tuple[str, int, str]] = {
     "bear_researcher": ("enabled", 8_000, "all"),
     "risk_officer": ("enabled", 6_000, "last_round"),
     "fund_manager": ("enabled", 8_000, "last_round"),
+    # Phase 5B exit shadow-test baseline — kimi-only clone of
+    # fund_manager (no routing block) consumed only by
+    # backend.services.shadow_runner. Same thinking config so the
+    # baseline reasoning footprint matches the routed-tier kimi call.
+    "fund_manager_shadow_baseline": ("enabled", 8_000, "last_round"),
 }
 
 
@@ -339,7 +344,7 @@ def production_router_config() -> RouterConfig:
 
 @pytest.mark.integration
 class TestProductionConfigRoundTrip:
-    def test_all_ten_agents_present(
+    def test_all_agents_present(
         self, production_router_config: RouterConfig
     ) -> None:
         assert set(production_router_config.agents.keys()) == set(
diff --git a/tests/test_shadow_runner.py b/tests/test_shadow_runner.py
new file mode 100644
index 0000000..5c9b8b3
--- /dev/null
+++ b/tests/test_shadow_runner.py
@@ -0,0 +1,567 @@
+"""Tests for backend.services.shadow_runner.
+
+Covers:
+* env-flag enable / sample rate clamping / parsing
+* prompt rebuild from AnalysisRecord (includes debate transcript)
+* parsing the baseline kimi response (valid / malformed / NaN /
+  out-of-range / non-bool / fenced / plain JSON)
+* run_shadow gating: disabled, sample-rate=0, no decision, partial
+  record, budget breach, mongo unavailable
+* run_shadow happy path: builds correct entry + writes via
+  record_shadow_decision
+* schedule_shadow_run fire-and-forget contract
+"""
+
+from __future__ import annotations
+
+import datetime as _dt
+from typing import Any
+from unittest.mock import AsyncMock, MagicMock, patch
+
+import pytest
+
+from backend.agents.records import (
+    AgentStepRecord,
+    AnalysisRecord,
+    DebateRoundRecord,
+    FundManagerRecord,
+)
+from backend.services import shadow_runner
+from backend.services.shadow_recorder import (
+    ShadowDecisionEntry,
+)
+
+# ----------------------------------------------------------------------
+# Fixtures
+# ----------------------------------------------------------------------
+
+
+def _step(
+    agent: str,
+    content: str,
+    started: _dt.datetime | None = None,
+    completed: _dt.datetime | None = None,
+) -> AgentStepRecord:
+    started = started or _dt.datetime(2026, 5, 2, 9, 0, tzinfo=_dt.UTC)
+    return AgentStepRecord(
+        agent=agent,  # type: ignore[arg-type]
+        content=content,
+        started_at=started,
+        completed_at=completed
+        or started + _dt.timedelta(seconds=2),
+    )
+
+
+def _make_record(
+    *,
+    decision: FundManagerRecord | None = None,
+    debates: list[DebateRoundRecord] | None = None,
+    drop_steps: tuple[str, ...] = (),
+) -> AnalysisRecord:
+    base_steps = [
+        _step("news_crawler", "新闻摘要内容"),
+        _step("sentiment_analyst", "情绪分数 0.6"),
+        _step("fundamental_analyst", "PE 30 ROE 25"),
+        _step("technical_analyst", "MACD 金叉"),
+        _step("intelligence_officer", "情报融合"),
+        _step("risk_officer", "风控 OK"),
+    ]
+    steps = [s for s in base_steps if s.agent not in drop_steps]
+    if decision is None and "fund_manager" not in drop_steps:
+        decision_step = _step("fund_manager", "决策")
+        decision = FundManagerRecord(
+            action="买入",
+            confidence=0.8,
+            risk_score=0.3,
+            reasoning="多空一致",
+            step=decision_step,
+        )
+        steps = [*steps, decision_step]
+    return AnalysisRecord(
+        run_id="run-1",
+        stock_code="600519",
+        stock_name="贵州茅台",
+        trade_date="2026-05-02",
+        status="completed",
+        steps=steps,
+        debates=debates or [],
+        decision=decision,
+    )
+
+
+def _make_services(router: AsyncMock | None = None, mongodb: Any = None) -> Any:
+    services = MagicMock()
+    services.llm_router = router or AsyncMock()
+    services.mongodb = mongodb if mongodb is not None else MagicMock()
+    return services
+
+
+def _make_response(content: str) -> MagicMock:
+    msg = MagicMock()
+    msg.content = content
+    choice = MagicMock()
+    choice.message = msg
+    resp = MagicMock()
+    resp.choices = [choice]
+    return resp
+
+
+# ----------------------------------------------------------------------
+# Group 1: env helpers
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestEnvHelpers:
+    @pytest.mark.parametrize(
+        ("raw", "expected"),
+        [
+            ("1", True),
+            ("true", True),
+            ("YES", True),
+            ("on", True),
+            ("0", False),
+            ("", False),
+            ("disabled", False),
+        ],
+    )
+    def test_is_enabled(
+        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, raw)
+        assert shadow_runner.is_enabled() is expected
+
+    def test_is_enabled_unset(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
+        assert shadow_runner.is_enabled() is False
+
+    @pytest.mark.parametrize(
+        ("raw", "expected"),
+        [
+            ("0.5", 0.5),
+            ("1.0", 1.0),
+            ("0", 0.0),
+            ("2", 1.0),  # clamped
+            ("-0.1", 0.0),  # clamped
+            ("nan", 1.0),  # default
+            ("not-a-float", 1.0),  # default
+            ("", 1.0),  # default
+        ],
+    )
+    def test_sample_rate(
+        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
+    ) -> None:
+        if raw == "":
+            monkeypatch.delenv(
+                shadow_runner.SHADOW_SAMPLE_RATE_ENV, raising=False
+            )
+        else:
+            monkeypatch.setenv(shadow_runner.SHADOW_SAMPLE_RATE_ENV, raw)
+        assert shadow_runner._sample_rate() == expected
+
+
+# ----------------------------------------------------------------------
+# Group 2: prompt rebuild
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRebuildUserContent:
+    def test_full_record_rebuilds(self) -> None:
+        debates = [
+            DebateRoundRecord(
+                round=1,
+                bull=_step("bull_researcher", "看涨论点"),
+                bear=_step("bear_researcher", "看空论点"),
+            )
+        ]
+        record = _make_record(debates=debates)
+        out = shadow_runner._rebuild_user_content(record)
+        assert out is not None
+        assert "目标股票: 600519 贵州茅台" in out
+        assert "新闻摘要内容" in out
+        assert "情绪分数 0.6" in out
+        assert "Bull: 看涨论点" in out
+        assert "Bear: 看空论点" in out
+        assert "风控 OK" in out
+
+    def test_returns_none_when_step_missing(self) -> None:
+        record = _make_record(drop_steps=("intelligence_officer",))
+        assert shadow_runner._rebuild_user_content(record) is None
+
+    def test_empty_debate_renders_empty_section(self) -> None:
+        record = _make_record(debates=[])
+        out = shadow_runner._rebuild_user_content(record)
+        assert out is not None
+        # The prompt template expects the debate block, even when empty.
+        assert "=== 多空辩论记录 ===" in out
+
+
+# ----------------------------------------------------------------------
+# Group 3: response parsing
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestParseBaselineResponse:
+    def test_plain_json_happy_path(self) -> None:
+        action, conf, ok = shadow_runner._parse_baseline_response(
+            '{"action":"买入","confidence":0.75}'
+        )
+        assert (action, conf, ok) == ("买入", 0.75, True)
+
+    def test_fenced_json_extracted(self) -> None:
+        raw = '```json\n{"action":"卖出","confidence":0.42}\n```'
+        action, conf, ok = shadow_runner._parse_baseline_response(raw)
+        assert (action, conf, ok) == ("卖出", 0.42, True)
+
+    @pytest.mark.parametrize(
+        "raw",
+        [
+            "",
+            "not json at all",
+            "{not even kinda json}",
+            '{"action":"buy","confidence":0.5}',  # invalid action
+            '{"action":"持有","confidence":1.5}',  # out of range
+            '{"action":"持有","confidence":"high"}',  # wrong type
+            '{"action":"持有","confidence":NaN}',  # python json accepts but we reject
+            '{"action":"持有","confidence":true}',  # bool not int
+            '{"action":"持有"}',  # missing confidence
+        ],
+    )
+    def test_malformed_returns_parse_failed(self, raw: str) -> None:
+        action, conf, ok = shadow_runner._parse_baseline_response(raw)
+        assert ok is False
+        assert action == "持有"
+        assert conf == 0.5
+
+
+# ----------------------------------------------------------------------
+# Group 4: routed leg extraction
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRoutedLegFromRecord:
+    def test_happy_path(self) -> None:
+        record = _make_record()
+        leg = shadow_runner._routed_leg_from_record(record)
+        assert leg is not None
+        assert leg.action == "买入"
+        assert leg.confidence == 0.8
+        assert leg.latency_ms == 2000.0  # 2s
+
+    def test_no_decision_returns_none(self) -> None:
+        record = _make_record(drop_steps=("fund_manager",))
+        record = record.model_copy(update={"decision": None})
+        assert shadow_runner._routed_leg_from_record(record) is None
+
+    def test_completed_before_started_yields_zero_latency(self) -> None:
+        bad_step = AgentStepRecord(
+            agent="fund_manager",
+            content="决策",
+            started_at=_dt.datetime(2026, 5, 2, 10, 0, tzinfo=_dt.UTC),
+            completed_at=_dt.datetime(2026, 5, 2, 9, 0, tzinfo=_dt.UTC),
+        )
+        decision = FundManagerRecord(
+            action="持有",
+            confidence=0.5,
+            risk_score=0.5,
+            step=bad_step,
+        )
+        record = _make_record(decision=decision)
+        leg = shadow_runner._routed_leg_from_record(record)
+        assert leg is not None
+        assert leg.latency_ms == 0.0
+
+
+# ----------------------------------------------------------------------
+# Group 5: budget gate
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestBudgetAllows:
+    async def test_no_redis_returns_false(self) -> None:
+        # Without Redis we can't verify spend → fail-closed.
+        assert await shadow_runner._budget_allows(None) is False
+
+    async def test_ok_state_returns_true(self) -> None:
+        redis = MagicMock()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ):
+            assert await shadow_runner._budget_allows(redis) is True
+
+    async def test_soft_breach_returns_false(self) -> None:
+        # During the 7-day window we treat soft_breach as "skip shadow"
+        # so production decision quality stays the priority.
+        redis = MagicMock()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="soft_breach")),
+        ):
+            assert await shadow_runner._budget_allows(redis) is False
+
+    async def test_hard_breach_returns_false(self) -> None:
+        redis = MagicMock()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="hard_breach")),
+        ):
+            assert await shadow_runner._budget_allows(redis) is False
+
+    async def test_exception_returns_false(self) -> None:
+        redis = MagicMock()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(side_effect=RuntimeError("redis down")),
+        ):
+            assert await shadow_runner._budget_allows(redis) is False
+
+
+# ----------------------------------------------------------------------
+# Group 6: run_shadow integration
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRunShadow:
+    async def test_disabled_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
+        services = _make_services()
+        ok = await shadow_runner.run_shadow(
+            services, _make_record(), MagicMock()
+        )
+        assert ok is False
+        services.llm_router.complete.assert_not_called()
+
+    async def test_zero_sample_rate_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        monkeypatch.setenv(shadow_runner.SHADOW_SAMPLE_RATE_ENV, "0")
+        services = _make_services()
+        ok = await shadow_runner.run_shadow(
+            services, _make_record(), MagicMock()
+        )
+        assert ok is False
+        services.llm_router.complete.assert_not_called()
+
+    async def test_no_decision_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        services = _make_services()
+        record = _make_record()
+        record = record.model_copy(update={"decision": None})
+        ok = await shadow_runner.run_shadow(services, record, MagicMock())
+        assert ok is False
+        services.llm_router.complete.assert_not_called()
+
+    async def test_budget_breach_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        services = _make_services()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="hard_breach")),
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, _make_record(), MagicMock()
+            )
+        assert ok is False
+        services.llm_router.complete.assert_not_called()
+
+    async def test_partial_record_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        record = _make_record(drop_steps=("intelligence_officer",))
+        services = _make_services()
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, record, MagicMock()
+            )
+        assert ok is False
+
+    async def test_no_mongo_short_circuits(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        router = AsyncMock()
+        router.complete = AsyncMock(
+            return_value=_make_response('{"action":"买入","confidence":0.8}')
+        )
+        services = _make_services(router=router, mongodb=None)
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, _make_record(), MagicMock()
+            )
+        assert ok is False
+
+    async def test_happy_path_writes_entry(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        router = AsyncMock()
+        router.complete = AsyncMock(
+            return_value=_make_response(
+                '{"action":"持有","confidence":0.55}'
+            )
+        )
+        mongodb = MagicMock()
+        services = _make_services(router=router, mongodb=mongodb)
+
+        recorded: list[ShadowDecisionEntry] = []
+
+        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
+            recorded.append(entry)
+            return True
+
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ), patch.object(
+            shadow_runner, "record_shadow_decision", _capture
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, _make_record(), MagicMock()
+            )
+
+        assert ok is True
+        assert len(recorded) == 1
+        entry = recorded[0]
+        assert entry.run_id == "run-1"
+        assert entry.routed.action == "买入"
+        assert entry.baseline.action == "持有"
+        assert entry.baseline.confidence == 0.55
+        assert entry.baseline.parse_ok is True
+        # Sanity: baseline call hit the dedicated baseline agent.
+        called_kwargs = router.complete.call_args.kwargs
+        assert called_kwargs["agent_name"] == shadow_runner.SHADOW_BASELINE_AGENT
+
+    async def test_router_failure_swallowed(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        router = AsyncMock()
+        router.complete = AsyncMock(side_effect=RuntimeError("kimi down"))
+        services = _make_services(router=router)
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, _make_record(), MagicMock()
+            )
+        assert ok is False  # absorbed, not raised
+
+    async def test_baseline_parse_failure_still_records(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        # Parse failures are themselves a quality signal; the entry
+        # must land so shadow_compare can surface parse_ok rate.
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        router = AsyncMock()
+        router.complete = AsyncMock(
+            return_value=_make_response("not-even-json")
+        )
+        services = _make_services(router=router)
+        recorded: list[ShadowDecisionEntry] = []
+
+        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
+            recorded.append(entry)
+            return True
+
+        with patch.object(
+            shadow_runner,
+            "get_budget_state",
+            AsyncMock(return_value=MagicMock(status="ok")),
+        ), patch.object(
+            shadow_runner, "record_shadow_decision", _capture
+        ):
+            ok = await shadow_runner.run_shadow(
+                services, _make_record(), MagicMock()
+            )
+        assert ok is True
+        assert recorded[0].baseline.parse_ok is False
+
+
+# ----------------------------------------------------------------------
+# Group 7: schedule_shadow_run
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestScheduleShadowRun:
+    def test_returns_none_when_disabled(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
+        services = _make_services()
+        task = shadow_runner.schedule_shadow_run(
+            services, _make_record(), MagicMock()
+        )
+        assert task is None
+
+    async def test_creates_named_task_when_enabled(
+        self, monkeypatch: pytest.MonkeyPatch
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        services = _make_services()
+        # Stub run_shadow so the task completes immediately.
+        with patch.object(
+            shadow_runner,
+            "run_shadow",
+            AsyncMock(return_value=False),
+        ):
+            task = shadow_runner.schedule_shadow_run(
+                services, _make_record(), MagicMock()
+            )
+            assert task is not None
+            assert task.get_name().startswith("shadow_run:")
+            await task
+
+    async def test_task_exception_logged_not_raised(
+        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
+    ) -> None:
+        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
+        services = _make_services()
+        with patch.object(
+            shadow_runner,
+            "run_shadow",
+            AsyncMock(side_effect=RuntimeError("boom")),
+        ):
+            task = shadow_runner.schedule_shadow_run(
+                services, _make_record(), MagicMock()
+            )
+            assert task is not None
+            # awaiting the task surfaces the exception via .exception(),
+            # which our done-callback handles. Awaiting from the test
+            # propagates it though, so use gather with return_exceptions.
+            import asyncio
+
+            results = await asyncio.gather(task, return_exceptions=True)
+            assert isinstance(results[0], RuntimeError)

exec
/bin/bash -lc "sed -n '1,700p' backend/data/analysis_scheduler.py && sed -n '1,260p' backend/services/shadow_recorder.py && sed -n '1,260p' backend/agents/records.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Daily stock analysis orchestrator.

Phase 5B-T02 added a Fast/Slow split: when a :class:`WatchlistPolicy`
is supplied at construction, ``start()`` registers two cron jobs (one
per category) and each per-stock run rebuilds the agent services with
that category's :class:`PipelineConfig` (debate rounds + timeout). When
no policy is supplied the scheduler falls back to the legacy single
09:45 CST cron with the base config — that path is what every test in
``tests/test_analysis_scheduler*.py`` predating T02 exercises, so the
default remains backwards-compatible.

SLA caveats (Codex R4 perf review):

* The bucket ``pipeline_timeout_seconds`` (480s fast / 900s slow) is
  applied via ``asyncio.wait_for`` around ``run_analysis`` only. It
  does NOT include lock-wait, watchlist scan, the 10s inter-stock
  rate-limit, or Mongo/Redis persistence. Operators measuring p95
  end-to-end SLA should track ``category_analysis_complete`` log
  duration and tighten the per-stock timeout if there is consistent
  head-room.
* ``self._run_lock`` is process-wide on purpose: it serialises the
  budget probe + LLM call so a parallel manual trigger cannot
  double-spend the daily ceiling. The trade-off is that fast and
  slow buckets share the lane — if the YAML schedules them at the
  same minute (the default 09:00 overlap), the first fast tick can
  wait up to 900s for an in-flight slow stock to finish before its
  own 480s budget starts. Phase 5C should consider per-bucket locks
  paired with a Redis-backed budget reservation to keep both fairness
  and the cost ceiling.
* ``QUANTMIND_DAILY_BUDGET`` (cost_guard) — not the bucket timeout —
  is what enforces the ¥1.20 daily ceiling. The timeout protects p95
  latency, not spend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.agents.graph import AnalysisRunError, run_analysis
from backend.agents.models import PipelineConfig, TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    assert_budget_allows,
)
from backend.services.shadow_runner import schedule_shadow_run
from backend.services.watchlist_policy import (
    Category,
    WatchlistPolicy,
    assign_category,
)

if TYPE_CHECKING:
    import redis.asyncio

    from backend.agents.models import AnalysisServices
    from backend.data.database import MongoDBService
    from backend.data.watchlist import WatchlistService

log = structlog.get_logger(component="analysis_scheduler")

CHANNEL_ANALYSIS = "analysis:signals"

SHANGHAI = ZoneInfo("Asia/Shanghai")
CATCH_UP_CUTOFF = dt_time(hour=9, minute=45)


class AnalysisScheduler:
    """Daily stock analysis orchestrator.

    Runs at 09:45 CST on trading days. For each active watchlist stock:
    1. Call run_analysis() (9-agent LangGraph pipeline)
    2. Persist signal to MongoDB
    3. Publish signal to Redis for real-time frontend updates
    Rate-limits 10s between stocks to avoid LLM API throttling.
    """

    def __init__(
        self,
        watchlist: WatchlistService,
        services: AnalysisServices,
        mongodb: MongoDBService,
        redis_client: redis.asyncio.Redis | None,
        policy: WatchlistPolicy | None = None,
    ) -> None:
        self._watchlist = watchlist
        self._services = services
        self._mongodb = mongodb
        self._redis = redis_client
        self._policy = policy
        self._scheduler: AsyncIOScheduler | None = None
        # Serializes _run_and_persist so a manual API call cannot race
        # against the cron-driven daily loop and double-spend the daily
        # budget by both observing the same under-cap snapshot. Within
        # one process that's enough; cross-process races would require
        # a Redis lock and are out of scope while the eval-period
        # backend runs as a single instance.
        self._run_lock = asyncio.Lock()

    @property
    def policy(self) -> WatchlistPolicy | None:
        """Current Fast/Slow watchlist policy (None ⇒ legacy mode)."""
        return self._policy

    def update_policy(self, policy: WatchlistPolicy) -> None:
        """Swap the in-memory policy.

        Cron strings on the running scheduler are NOT rewritten — only
        per-code overrides take effect immediately because each cron tick
        re-reads ``self._policy`` to partition the live watchlist. Cron
        cadence changes still require a process restart; that is an
        intentional simplification while the eval-period scheduler runs
        as a single instance.

        Single-process assumption: this swap is not synchronised across
        workers. Phase 5B targets ``WEB_CONCURRENCY=1`` so the API +
        cron share one process and one ``app.state``. Multi-worker
        deployment would need a Redis pub/sub broadcast (or leader
        election) to keep all schedulers consistent — out of scope here.
        """
        self._policy = policy

    async def start(self) -> None:
        """Register cron job(s) and run catch-up if today's run was missed.

        Two scheduling modes:

        * **Legacy** (``policy is None``): one job at 09:45 CST Mon-Fri
          calling :meth:`run_daily_analysis` over the full watchlist.
        * **Fast/Slow** (``policy`` set): two jobs from the policy's
          ``fast.cron`` and ``slow.cron``, each calling
          :meth:`run_category_analysis` with its category. The watchlist
          is partitioned at job time, so per-code overrides applied via
          the API take effect on the next tick without restart.

        Catch-up trigger (all must hold):
          1. Today is a weekday (Mon-Fri); A-share trading calendar is
             not loaded here so holidays still trigger a run — the
             watchlist analysis itself is tolerant of empty market data.
          2. Current time is past 09:45 Asia/Shanghai.
          3. At least one active watchlist stock has no trading_signals
             row for today.

        By-stock granularity matters: a previous run may have succeeded
        for 3/5 stocks, and we must only re-run the 2 missing ones to
        stay under the daily cost budget. The catch-up itself is
        category-aware when a policy is loaded.
        """
        self._scheduler = AsyncIOScheduler()
        if self._policy is None:
            self._register_legacy_cron()
        else:
            try:
                self._add_category_cron("fast", self._policy.fast.cron)
                self._add_category_cron("slow", self._policy.slow.cron)
            except ValueError as exc:
                # Malformed cron in either bucket — drop both jobs and
                # fall back to the legacy single-cron mode so the rest
                # of the eval loop keeps running. Operators see the
                # warning in logs and can fix the YAML without an
                # outage.
                log.warning(
                    "watchlist_policy_cron_invalid",
                    fast_cron=self._policy.fast.cron,
                    slow_cron=self._policy.slow.cron,
                    error=str(exc),
                )
                for job_id in ("fast_analysis", "slow_analysis"):
                    if self._scheduler.get_job(job_id) is not None:
                        self._scheduler.remove_job(job_id)
                self._policy = None
                self._register_legacy_cron()
            else:
                log.info(
                    "analysis_scheduler_started",
                    mode="fast_slow",
                    fast_cron=self._policy.fast.cron,
                    slow_cron=self._policy.slow.cron,
                )
        self._scheduler.start()

        try:
            missed = await self._compute_catch_up_targets()
        except Exception as exc:
            log.warning("catch_up_probe_failed", error=str(exc))
            missed = []
        if missed:
            log.info("catch_up_scheduling", missed=missed)
            asyncio.create_task(self._run_catch_up(missed))

    def _add_category_cron(self, category: Category, cron_expr: str) -> None:
        """Register one cron job per category, parsed from the YAML string.

        Raises ``ValueError`` when ``cron_expr`` is malformed — the
        caller (:meth:`start`) catches this and falls back to legacy
        single-cron mode so a typo cannot bring the scheduler down.
        """
        if self._scheduler is None:  # defensive — start() owns scheduler
            return
        trigger = CronTrigger.from_crontab(cron_expr, timezone="Asia/Shanghai")
        self._scheduler.add_job(
            self.run_category_analysis,
            trigger=trigger,
            args=[category],
            id=f"{category}_analysis",
            name=f"{category.capitalize()} watchlist analysis",
        )

    def _register_legacy_cron(self) -> None:
        """Register the original 09:45 CST single-cron job."""
        if self._scheduler is None:
            return
        self._scheduler.add_job(
            self.run_daily_analysis,
            "cron",
            hour=9,
            minute=45,
            day_of_week="mon-fri",
            timezone="Asia/Shanghai",
            id="daily_analysis",
            name="Daily watchlist analysis",
        )
        log.info(
            "analysis_scheduler_started",
            mode="legacy_single_cron",
            schedule="09:45 CST Mon-Fri",
        )

    async def stop(self) -> None:
        """Shutdown scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("analysis_scheduler_stopped")
        self._scheduler = None

    async def run_daily_analysis(self) -> list[TradingSignal]:
        """Execute analysis for every active watchlist stock.

        Manual entry point (``POST /api/watchlist/analyze-now``) and the
        legacy 09:45 cron. With a policy loaded, each stock is dispatched
        through :meth:`_run_and_persist` with its assigned category so
        the per-bucket pipeline knobs still apply even on a manual sweep.
        """
        stocks = await self._watchlist.list_stocks()
        if not stocks:
            log.info("daily_analysis_skipped", reason="empty_watchlist")
            return []

        codes = [stock["stock_code"] for stock in stocks]
        log.info("daily_analysis_started", stock_count=len(codes))
        signals = await self._run_codes(codes, category=None)
        log.info(
            "daily_analysis_complete", total=len(codes), success=len(signals)
        )
        return signals

    async def run_category_analysis(
        self, category: Category
    ) -> list[TradingSignal]:
        """Run the pipeline only for stocks assigned to ``category``.

        Cron entry point when a :class:`WatchlistPolicy` is loaded —
        ``fast`` ticks 4x/day for short-horizon names with the fast
        bucket's :class:`PipelineConfig`; ``slow`` ticks once/day for
        long-horizon names with the slow bucket's deeper config.

        We snapshot ``self._policy`` once at the start: a concurrent
        :meth:`update_policy` call (e.g. from the API endpoint) must
        not change the partition mid-run, otherwise different stocks
        in the same tick would see different bucket assignments.
        """
        policy = self._policy
        if policy is None:
            log.warning(
                "category_analysis_no_policy", category=category
            )
            return []

        stocks = await self._watchlist.list_stocks()
        all_codes = [stock["stock_code"] for stock in stocks]
        matched = [
            code
            for code in all_codes
            if assign_category(code, policy) == category
        ]
        bucket = policy.bucket_for(category)
        if not matched:
            log.info(
                "category_analysis_skipped",
                category=category,
                reason="no_matched_codes",
                total_watchlist=len(all_codes),
                policy_version=policy.policy_version,
            )
            return []

        log.info(
            "category_analysis_started",
            category=category,
            matched_codes=matched,
            stock_count=len(matched),
            total_watchlist=len(all_codes),
            policy_version=policy.policy_version,
            cron=bucket.cron,
            timeout_seconds=bucket.pipeline_timeout_seconds,
            max_debate_rounds=bucket.max_debate_rounds,
        )
        signals = await self._run_codes(
            matched, category=category, policy=policy
        )
        log.info(
            "category_analysis_complete",
            category=category,
            total=len(matched),
            success=len(signals),
            failed=len(matched) - len(signals),
            policy_version=policy.policy_version,
        )
        return signals

    async def _run_codes(
        self,
        codes: Iterable[str],
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> list[TradingSignal]:
        """Sequentially dispatch ``codes`` through the per-stock pipeline.

        Shared body for both daily and category-scoped runs; isolates
        rate-limiting and per-stock error handling so the cron-facing
        coroutines stay short.

        ``policy`` is an optional snapshot — when supplied, every stock
        in the loop sees the SAME policy even if a concurrent
        :meth:`update_policy` call swapped ``self._policy`` mid-tick
        (Codex R6 MEDIUM #6).
        """
        ordered = list(codes)
        signals: list[TradingSignal] = []
        for i, code in enumerate(ordered):
            stock_category = self._resolve_category(code, category, policy)
            try:
                signal = await self._run_and_persist(
                    code, stock_category, policy
                )
                if signal is not None:
                    signals.append(signal)
                    log.info(
                        "stock_analysis_complete",
                        code=code,
                        category=stock_category,
                        action=signal.action,
                    )
            except Exception as exc:
                log.error(
                    "stock_analysis_failed",
                    code=code,
                    category=stock_category,
                    error=str(exc),
                )
            if i < len(ordered) - 1:
                await asyncio.sleep(10)
        return signals

    def _resolve_category(
        self,
        stock_code: str,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> Category | None:
        """Return the effective category for ``stock_code``.

        Explicit category wins (used by the per-bucket cron jobs); when
        omitted we fall back to whatever the policy assigns. Returns
        ``None`` when no policy is loaded so the legacy single-cron
        path keeps using the base :class:`PipelineConfig` unchanged.

        ``policy`` is an optional snapshot — pass it through from the
        cron entry so that every stock in one tick sees a consistent
        policy even if a concurrent API mutation swaps ``self._policy``.
        """
        if category is not None:
            return category
        effective_policy = policy if policy is not None else self._policy
        if effective_policy is None:
            return None
        return assign_category(stock_code, effective_policy)

    async def run_single_analysis(
        self, stock_code: str, *, category: Category | None = None
    ) -> TradingSignal | None:
        """Analyze a single stock on demand.

        ``category`` defaults to whatever the loaded policy assigns;
        callers can override (e.g. an operator forcing a deep slow run
        on a fast-bucket stock without mutating the policy).
        """
        effective = self._resolve_category(stock_code, category)
        try:
            return await self._run_and_persist(stock_code, effective)
        except Exception as exc:
            log.error(
                "single_analysis_failed", code=stock_code, error=str(exc)
            )
            return None

    async def _run_and_persist(
        self,
        stock_code: str,
        category: Category | None = None,
        policy: WatchlistPolicy | None = None,
    ) -> TradingSignal | None:
        """Run pipeline and persist both signal and full record.

        Returns the TradingSignal on success, None when run_analysis
        raises (caller logs). Record is saved even when signal persist
        later fails so the analysis trail is preserved.

        AnalysisRunError carries the failed AnalysisRecord; it is
        persisted before re-raising so /history shows the failure.

        Cost ceiling enforcement (P5A-T02): before kicking off a new
        run we check the daily LLM budget against ``assert_budget_allows``.
        On ``hard_breach`` we record a synthetic failed analysis (so
        ``/history`` reflects the skip) and return ``None`` instead of
        paying for another LLM call. The Redis-less / probe failure
        paths fall through to the normal pipeline so we never
        accidentally wedge runs on transient infra glitches.

        ``self._run_lock`` serializes concurrent calls so the cron loop
        and a manual API call cannot both observe the same under-cap
        snapshot and double-spend.

        Phase 5B-T02: when ``category`` is provided (and a policy is
        loaded), the pipeline runs against a per-category clone of the
        agent services with that bucket's ``max_debate_rounds`` and a
        hard ``asyncio.wait_for`` matching the bucket's
        ``pipeline_timeout_seconds`` SLA. Without a category we keep
        the legacy code path bit-for-bit (no timeout, base config).
        """
        async with self._run_lock:
            return await self._run_and_persist_locked(
                stock_code, category, policy
            )

    async def _run_and_persist_locked(
        self,
        stock_code: str,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> TradingSignal | None:
        if self._redis is not None:
            try:
                state = await assert_budget_allows(
                    self._redis, agent_name="pipeline"
                )
            except DailyBudgetExceededError as exc:
                await self._persist_cost_skip(stock_code, exc)
                return None
            except Exception as probe_exc:
                # A Redis hiccup must NOT block analysis — log and proceed.
                log.warning(
                    "cost_guard_probe_failed",
                    code=stock_code,
                    error=str(probe_exc),
                )
            else:
                if state.status == "soft_breach":
                    # Phase 5B will degrade thinking; for now we just
                    # record the warning so operators can see it.
                    log.warning(
                        "cost_soft_breach_observed",
                        code=stock_code,
                        spent=state.spent_today,
                        soft_ceiling=state.soft_ceiling,
                    )

        services, timeout = self._resolve_services_and_timeout(
            category, policy
        )
        try:
            if timeout is None:
                result = await run_analysis(stock_code, services)
            else:
                result = await asyncio.wait_for(
                    run_analysis(stock_code, services), timeout=timeout
                )
        except TimeoutError:
            await self._persist_timeout_skip(stock_code, category, timeout)
            return None
        except AnalysisRunError as exc:
            try:
                await self._mongodb.save_analysis_record(
                    exc.record.model_dump(mode="json")
                )
            except Exception as persist_exc:
                log.warning(
                    "save_failed_record_failed",
                    code=stock_code,
                    error=str(persist_exc),
                )
            raise

        if not isinstance(result, AnalysisRunResult):  # safety guard
            raise TypeError(
                f"run_analysis must return AnalysisRunResult, got {type(result)!r}"
            )

        signal = result.signal
        record = result.record

        signal_dict = signal.model_dump(mode="json")
        signal_dict["created_at"] = datetime.now(UTC).isoformat()
        signal_id = await self._mongodb.save_signal(signal_dict)

        record_with_signal = record.model_copy(update={"signal_id": signal_id})
        try:
            await self._mongodb.save_analysis_record(
                record_with_signal.model_dump(mode="json")
            )
        except AttributeError:
            # MongoDB service predates A1.4 — skip with warning.
            log.warning(
                "save_analysis_record_unavailable", code=stock_code
            )
        except Exception as exc:
            log.warning(
                "save_analysis_record_failed",
                code=stock_code,
                error=str(exc),
            )

        # Phase 5B exit shadow-test: opt-in via QUANTMIND_SHADOW_ENABLED.
        # Schedule the baseline replay as fire-and-forget so a slow Kimi
        # call cannot stall the next stock in the cron tick.
        try:
            schedule_shadow_run(services, record_with_signal, self._redis)
        except Exception as exc:
            log.warning(
                "shadow_schedule_failed",
                code=stock_code,
                error=str(exc),
            )

        await self._publish_signal(signal_dict)
        return signal

    def _resolve_services_and_timeout(
        self,
        category: Category | None,
        policy: WatchlistPolicy | None = None,
    ) -> tuple[AnalysisServices, int | None]:
        """Build per-category services + timeout, or fall back to base.

        Returning ``(self._services, None)`` for the no-category path
        keeps the legacy single-cron behaviour untouched: no
        ``asyncio.wait_for`` wrapper and the original ``PipelineConfig``
        is reused. With a category and a loaded policy we clone services
        with a fresh ``PipelineConfig`` carrying the bucket's debate
        depth + timeout, and return the timeout for ``wait_for``.

        ``policy`` (when supplied) is the cron-tick snapshot — used so
        every stock in one tick consumes the SAME bucket config even
        if a concurrent :meth:`update_policy` swaps ``self._policy``.
        """
        effective_policy = policy if policy is not None else self._policy
        if category is None or effective_policy is None:
            return self._services, None
        bucket = effective_policy.bucket_for(category)
        new_config = PipelineConfig(
            max_debate_rounds=bucket.max_debate_rounds,
            analysis_timeout_seconds=bucket.pipeline_timeout_seconds,
        )
        services = self._services.model_copy(
            update={"pipeline_config": new_config}
        )
        return services, bucket.pipeline_timeout_seconds

    async def _persist_timeout_skip(
        self,
        stock_code: str,
        category: Category | None,
        timeout: int | None,
    ) -> None:
        """Record a synthetic failed analysis when the SLA timeout fires.

        ``/history`` operators need to see WHICH bucket missed its SLA
        so the structured error prefix names the category. The legacy
        path never times out (timeout=None) so this is only reachable
        from the Fast/Slow code path.
        """
        suffix = (
            f"category={category} timeout={timeout}s"
            if category is not None
            else f"timeout={timeout}s"
        )
        record = AnalysisRecord(
            run_id=str(uuid.uuid4()),
            stock_code=stock_code,
            stock_name=stock_code,
            trade_date=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
            status="failed",
            error=f"pipeline_timeout: {suffix}",
        )
        try:
            await self._mongodb.save_analysis_record(
                record.model_dump(mode="json")
            )
        except Exception as persist_exc:
            log.warning(
                "save_timeout_skip_record_failed",
                code=stock_code,
                error=str(persist_exc),
            )
        log.warning(
            "pipeline_timeout",
            code=stock_code,
            category=category,
            timeout=timeout,
        )

    async def _persist_cost_skip(
        self, stock_code: str, exc: DailyBudgetExceededError
    ) -> None:
        """Record a synthetic failed analysis when the budget hard-caps us.

        We do not have a TradingSignal in this branch; only the record
        is written so ``/history`` reflects the skip with a structured
        ``error`` prefix that downstream tooling can grep for.
        """
        record = AnalysisRecord(
            run_id=str(uuid.uuid4()),
            stock_code=stock_code,
            stock_name=stock_code,
            trade_date=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
            status="failed",
            error=f"cost_ceiling_breached: {exc}",
        )
        try:
            await self._mongodb.save_analysis_record(
                record.model_dump(mode="json")
            )
        except Exception as persist_exc:
            log.warning(
                "save_cost_skip_record_failed",
                code=stock_code,
                error=str(persist_exc),
            )

    async def _publish_signal(self, signal_dict: dict[str, Any]) -> None:
        """Publish signal to Redis for WebSocket clients."""
        if self._redis is None:
            return
        try:
            payload = json.dumps(signal_dict, ensure_ascii=False)
            await self._redis.publish(CHANNEL_ANALYSIS, payload)
        except Exception as exc:
            log.warning("signal_publish_failed", error=str(exc))

    async def _compute_catch_up_targets(self) -> list[str]:
        """Return watchlist stock codes that still need today's analysis.

        Returns empty list when the catch-up preconditions aren't met
        (too early, weekend, empty watchlist, all stocks covered).
        """
        now_sh = datetime.now(tz=SHANGHAI)
        if now_sh.weekday() > 4:  # 5=Sat, 6=Sun
            return []
        if now_sh.time() < CATCH_UP_CUTOFF:
            return []

        stocks = await self._watchlist.list_stocks()
        if not stocks:
            return []

        trade_date = now_sh.strftime("%Y-%m-%d")
        codes = [stock["stock_code"] for stock in stocks]
        try:
            signals = await self._mongodb.query_signals_for_trade_date(
                trade_date=trade_date,
                stock_codes=codes,
            )
        except Exception as exc:
            log.warning(
                "catch_up_query_failed", trade_date=trade_date, error=str(exc)
            )
            return []

        covered_codes = {
"""Shadow decision recording for Phase 5B exit verification.

This module is the data-layer half of the shadow-test harness. It defines
the immutable ``ShadowDecisionEntry`` schema and the read/write API
against the ``shadow_decisions`` MongoDB collection. The companion CLI
``scripts/shadow_compare.py`` consumes these documents to produce the
action-consistency / confidence-deviation report Phase 5B exit gates on.

Design notes
------------

* **Pure data-layer.** This module is intentionally NOT wired into the
  live LangGraph pipeline. Doubling LLM calls in production would
  invalidate the cost-savings story P5B-T03 was built to tell. Operators
  wire the recorder through a separate scheduled job once deployment
  starts (Phase 5C deployment task). Tests therefore drive it directly.
* **Immutable entries.** Every field is frozen so a record cannot drift
  between the moment it is built and the moment it lands in Mongo —
  protects against subtle aliasing bugs in async pipelines.
* **UTC clock.** Matches the convention pinned by
  ``backend.llm.fallback._utc_date_str()`` so daily rollups elsewhere in
  the system line up; do NOT switch to ``datetime.now()`` (no tz). See
  P5B-T03 codex R6 for the timezone-drift bug this convention prevents.
* **Fail-soft writes.** The recorder swallows Mongo errors and logs a
  structured warning. Shadow recording is observability — a Mongo blip
  must not crash the calling job.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.data.database import MongoDBService

log = structlog.get_logger(component="shadow_recorder")

SHADOW_COLLECTION = "shadow_decisions"
_TTL_DAYS_DEFAULT = 30
_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})


@dataclass(frozen=True)
class ShadowDecisionLeg:
    """One side (baseline or routed) of a shadow comparison.

    ``parse_ok`` records whether the LLM response was JSON-parseable.
    The harness keeps unparseable runs because they are themselves a
    quality signal — a routing change that drives parse-failure rate up
    is a regression even if the surviving runs still match.

    ``escalated`` is meaningful only for the routed leg; the baseline leg
    sets it to ``False`` by convention. Storing both keeps the document
    schema-symmetric and the consumer code branch-free.
    """

    action: str
    confidence: float
    model: str
    latency_ms: float
    escalated: bool
    parse_ok: bool

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ):
            raise ValueError(
                f"confidence must be a finite float in [0,1], got "
                f"{self.confidence!r}"
            )
        conf = float(self.confidence)
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise ValueError(
                f"confidence must be a finite float in [0,1], got {conf!r}"
            )
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError(
                f"latency_ms must be a finite, non-negative float, got "
                f"{self.latency_ms!r}"
            )


@dataclass(frozen=True)
class ShadowDecisionEntry:
    """A baseline-vs-routed pair of fund_manager decisions for one run.

    The pair shares ``run_id`` so each entry carries both decisions
    side-by-side and the consumer never has to join two collections.
    """

    run_id: str
    stock_code: str
    trade_date: str
    created_at: datetime.datetime
    baseline: ShadowDecisionLeg
    routed: ShadowDecisionLeg

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be a non-empty string")
        if not self.stock_code:
            raise ValueError("stock_code must be a non-empty string")
        if not self.trade_date:
            raise ValueError("trade_date must be a non-empty string")
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware (UTC); naive datetimes "
                "drift across daylight-saving boundaries"
            )

    def to_document(self) -> dict[str, Any]:
        """Serialise to a Mongo-friendly dict.

        Keeps ``created_at`` as a real ``datetime`` (Mongo encodes it as
        BSON Date) so range queries work; everything else is plain JSON.
        """
        doc: dict[str, Any] = {
            "run_id": self.run_id,
            "stock_code": self.stock_code,
            "trade_date": self.trade_date,
            "created_at": self.created_at,
            "baseline": asdict(self.baseline),
            "routed": asdict(self.routed),
        }
        return doc


async def record_shadow_decision(
    mongodb: MongoDBService,
    entry: ShadowDecisionEntry,
) -> bool:
    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.

    Upsert key is ``run_id`` so re-runs (e.g. operator replays) overwrite
    rather than accumulate noise. Returns True on success, False on Mongo
    error — the caller logs but does not raise. Shadow tracking is
    observability and must never propagate a failure into a real trading
    run.
    """
    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        await coll.update_one(
            {"run_id": entry.run_id},
            {"$set": entry.to_document()},
            upsert=True,
        )
        return True
    except Exception as exc:
        log.warning(
            "shadow_record_failed",
            run_id=entry.run_id,
            stock_code=entry.stock_code,
            error=str(exc),
        )
        return False


async def query_shadow_decisions(
    mongodb: MongoDBService,
    *,
    days: int = 7,
    now: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """Return shadow_decisions documents for the last ``days`` days.

    ``now`` is injectable so tests can pin the clock without monkey-
    patching ``datetime.datetime``. The cutoff is computed in UTC to
    match the writer convention.

    Empty result is normal (no shadow data collected yet) and is
    returned as ``[]`` — never ``None`` — so consumers can iterate
    without a None check.
    """
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    cutoff = (
        now.astimezone(datetime.UTC)
        if now is not None
        else datetime.datetime.now(tz=datetime.UTC)
    ) - datetime.timedelta(days=days)

    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        cursor = coll.find({"created_at": {"$gte": cutoff}})
        # Drop the Mongo ObjectId so consumers (script + tests) can
        # JSON-serialise the result without bespoke encoders.
        return [
            {k: v for k, v in doc.items() if k != "_id"}
            async for doc in cursor
        ]
    except Exception as exc:
        log.warning(
            "shadow_query_failed",
            days=days,
            error=str(exc),
        )
        return []


__all__ = [
    "SHADOW_COLLECTION",
    "ShadowDecisionEntry",
    "ShadowDecisionLeg",
    "query_shadow_decisions",
    "record_shadow_decision",
]
"""Analysis record data models for full multi-agent run persistence.

Separate from TradingSignal to avoid polluting the terminal decision model.
Used by graph.run_analysis() instrumentation and the analysis history API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.models import TradingSignal

AgentName = Literal[
    "news_crawler",
    "sentiment_analyst",
    "fundamental_analyst",
    "technical_analyst",
    "intelligence_officer",
    "bull_researcher",
    "bear_researcher",
    "risk_officer",
    "fund_manager",
]

AgentStepStatus = Literal["running", "completed", "failed"]

AnalysisRunStatus = Literal["running", "completed", "failed"]


class EvidenceItem(BaseModel):
    """Evidence citation attached to an agent step."""

    model_config = ConfigDict(frozen=True)

    source: str
    snippet: str = ""
    sentiment: Literal["positive", "mixed", "negative"] = "mixed"


class AgentStepRecord(BaseModel):
    """Single agent invocation outcome in a run timeline.

    Tokens and cost default to 0 when the LLM SDK does not expose usage
    data; they must never be fabricated. Aggregate cost is tracked via
    cost_tracking collection, not here.
    """

    model_config = ConfigDict(frozen=True)

    agent: AgentName
    round: int = 0
    content: str = ""
    model_label: str = ""
    model_id: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_cny: float = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    status: AgentStepStatus = "completed"
    error: str | None = None


class DebateRoundRecord(BaseModel):
    """One debate round — bull then bear (either may be missing if the
    debate terminated mid-round, or if a round only had one side)."""

    model_config = ConfigDict(frozen=True)

    round: int
    bull: AgentStepRecord | None = None
    bear: AgentStepRecord | None = None


class RiskAssessmentRecord(BaseModel):
    """Risk officer structured output.

    `checks` may legitimately be empty — do not fabricate pass items.
    """

    model_config = ConfigDict(frozen=True)

    content: str = ""
    checks: list[dict] = Field(default_factory=list)
    step: AgentStepRecord


class FundManagerRecord(BaseModel):
    """Fund manager final decision, mirrored from TradingSignal."""

    model_config = ConfigDict(frozen=True)

    action: Literal["买入", "持有", "卖出"]
    target_price: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    step: AgentStepRecord


class AnalysisRecord(BaseModel):
    """Complete multi-agent analysis run record.

    One per run. Persisted in `analysis_records` MongoDB collection keyed
    by `run_id`. History view and detail view both read from here.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus = "running"
    max_rounds: int = 2
    current_round: int = 0

    steps: list[AgentStepRecord] = Field(default_factory=list)
    analysts: list[AgentStepRecord] = Field(default_factory=list)
    intelligence_officer: AgentStepRecord | None = None
    debates: list[DebateRoundRecord] = Field(default_factory=list)
    risk_assessment: RiskAssessmentRecord | None = None
    decision: FundManagerRecord | None = None

    signal_id: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    completed_at: datetime | None = None
    error: str | None = None


class AnalysisRunResult(BaseModel):
    """Bundle returned by run_analysis(): terminal signal + full record."""

    model_config = ConfigDict(frozen=True)

    signal: TradingSignal
    record: AnalysisRecord


class AnalysisSummary(BaseModel):
    """Compact row for the history list endpoint."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus
    action: Literal["买入", "持有", "卖出"] | None = None
    confidence: float | None = None
    risk_score: float | None = None
    signal_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

exec
/bin/bash -lc "grep -R \"class LLMRouter\\|def complete\\|async def complete\" -n backend/llm backend | head -100 && sed -n '1,280p' backend/llm/router.py && sed -n '1,260p' backend/agents/fund_manager.py && grep -R \"fund_manager_node\" -n backend/agents" in /home/ps/papers/QuantMind
 succeeded in 0ms:
backend/llm/router.py:61:class LLMRouter:
backend/llm/router.py:143:    async def complete(
backend/llm/router.py:61:class LLMRouter:
backend/llm/router.py:143:    async def complete(
"""Core LLM router for multi-provider request routing."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from openai import AsyncOpenAI

from backend.llm.fallback import (
    RETRYABLE_EXCEPTIONS,
    track_escalation,
    track_fallback,
    track_usage,
)
from backend.llm.providers import (
    AgentConfig,
    RouterConfig,
    RoutingConfig,
    ThinkingConfig,
    create_openai_client,
    load_router_config,
)

if TYPE_CHECKING:
    import redis.asyncio
    from openai.types import CompletionUsage
    from openai.types.chat import ChatCompletion


# Maximum UTF-8-encoded byte length of a triage response to attempt
# JSON-parsing. Anything larger is treated as a malformed contract and
# conservatively escalates. Bounded so adversarial / runaway LLM output
# cannot DoS the parser (R5 MEDIUM, R6 LOW: the original name suggested
# bytes but compared char count — multibyte content could exceed budget).
_MAX_TRIAGE_JSON_BYTES: int = 65_536


def _extract_reasoning_tokens(usage: CompletionUsage) -> int:
    """Best-effort lift of Kimi reasoning_tokens from usage details.

    The Moonshot SDK exposes reasoning consumption via
    ``completion_tokens_details.reasoning_tokens``. When the field is
    absent (non-Kimi provider, thinking disabled, or older response
    schema) this returns 0 — used purely for observability so it must
    never raise.
    """
    details = getattr(usage, "completion_tokens_details", None)
    if details is None:
        return 0
    reasoning = getattr(details, "reasoning_tokens", None)
    if isinstance(reasoning, int) and reasoning >= 0:
        return reasoning
    return 0


class LLMRouter:
    """Routes agent LLM requests to the appropriate provider.

    Manages AsyncOpenAI client instances, config hot-reload,
    and automatic fallback on provider failure.

    Usage::

        router = LLMRouter(config_path="config/agent_models.yaml")
        await router.initialize(redis_client=redis_pool)
        response = await router.complete("news_crawler", messages=[...])
        await router.close()
    """

    def __init__(self, config_path: str | Path) -> None:
        """Initialize the router with the path to agent_models.yaml.

        Does NOT load config or create clients — call initialize() first.
        """
        self._config_path = Path(config_path)
        self._config: RouterConfig | None = None
        self._config_mtime: float = 0.0
        self._clients: dict[str, AsyncOpenAI] = {}
        self._redis: redis.asyncio.Redis | None = None
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(component="llm_router")

    async def initialize(
        self,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> None:
        """Load config, create clients, store Redis reference.

        Must be called before complete(). Typically called in
        FastAPI lifespan.
        """
        self._redis = redis_client
        await self._reload_config()

    async def close(self) -> None:
        """Close all AsyncOpenAI clients. Call in FastAPI shutdown."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    @property
    def config(self) -> RouterConfig:
        """Return the current (immutable) router configuration."""
        if self._config is None:
            raise RuntimeError(
                "Router not initialized. Call initialize() first."
            )
        return self._config

    def preflight(self) -> dict[str, bool]:
        """Snapshot which providers currently hold a resolvable API key.

        Inspects the config's ``api_key`` entry for each provider. A
        literal key is always present; a ``${ENV}`` reference is present
        only when the environment variable is non-empty at call time.

        Returns a mapping ``{provider_name: True/False}``. Does not make
        any network calls — callers use this for a fast 503 cascade
        decision before booting the pipeline.
        """
        import os
        import re

        env_pattern = re.compile(r"^\$\{(\w+)\}$")
        if self._config is None:
            return {}
        status: dict[str, bool] = {}
        for name, provider_cfg in self._config.providers.items():
            raw = provider_cfg.api_key
            m = env_pattern.match(raw)
            if m is None:
                # Literal key in config — assume valid.
                status[name] = bool(raw)
            else:
                status[name] = bool(os.environ.get(m.group(1)))
        return status

    async def complete(
        self,
        agent_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ChatCompletion:
        """Route a chat completion request for the given agent.

        1. Check for config hot-reload
        2. Resolve agent -> provider -> client
        3. Call chat.completions.create
        4. On retryable failure, try fallback provider
        5. Track token usage in Redis

        Args:
            agent_name: Key from agents section of YAML.
            messages: OpenAI-format message list.
            **kwargs: Override temperature, max_tokens, etc.

        Returns:
            ChatCompletion response from the provider.

        Raises:
            KeyError: If agent_name is not in config.
            openai.APIError: If both primary and fallback fail.
        """
        await self._maybe_reload_config()

        config = self.config

        if agent_name not in config.agents:
            raise KeyError(
                f"Unknown agent '{agent_name}'. "
                f"Available: {sorted(config.agents.keys())}"
            )

        agent_cfg = config.agents[agent_name]

        call_kwargs: dict[str, Any] = {
            "temperature": kwargs.pop("temperature", config.defaults.temperature),
            "max_tokens": kwargs.pop("max_tokens", config.defaults.max_tokens),
            **kwargs,
        }

        # Try primary (or routing.triage) provider
        primary_provider, primary_model = self._select_primary(agent_cfg)
        is_tiered = agent_cfg.routing is not None
        primary_stage = "triage" if is_tiered else "primary"
        # Suffix the cost-tracking name for tiered agents so daily reports
        # can split triage vs escalation spend per agent (P5B-T03 trace
        # requirement). Non-tiered agents keep a flat name unchanged.
        primary_track_name = (
            f"{agent_name}/triage" if is_tiered else agent_name
        )
        try:
            response = await self._call_provider(
                provider_name=primary_provider,
                model=primary_model,
                messages=messages,
                agent_name=primary_track_name,
                thinking=agent_cfg.thinking,
                route_stage=primary_stage,
                **call_kwargs,
            )
        except RETRYABLE_EXCEPTIONS as exc:
            self._log.warning(
                "primary_provider_failed",
                agent_name=agent_name,
                provider=primary_provider,
                model=primary_model,
                error=str(exc),
            )

            if agent_cfg.fallback is None:
                raise

            await track_fallback(
                self._redis,
                agent_name,
                primary_provider,
                agent_cfg.fallback.provider,
            )

            self._log.info(
                "trying_fallback_provider",
                agent_name=agent_name,
                fallback_provider=agent_cfg.fallback.provider,
                fallback_model=agent_cfg.fallback.model,
            )

            return await self._call_provider(
                provider_name=agent_cfg.fallback.provider,
                model=agent_cfg.fallback.model,
                messages=messages,
                agent_name=agent_name,
                thinking=agent_cfg.thinking,
                route_stage="fallback",
                **call_kwargs,
            )

        if is_tiered:
            should_esc, reason = self._should_escalate(
                agent_cfg.routing, response
            )
            esc_provider = agent_cfg.routing.escalation_provider  # type: ignore[union-attr]
            esc_model = agent_cfg.routing.escalation_model  # type: ignore[union-attr]
            if should_esc and esc_provider is not None and esc_model is not None:
                await track_escalation(
                    self._redis,
                    agent_name,
                    primary_provider,
                    esc_provider,
                    reason,
                )
                threshold = (
                    agent_cfg.routing.escalation_condition.confidence_lt  # type: ignore[union-attr]
                    if agent_cfg.routing.escalation_condition  # type: ignore[union-attr]
                    else None
                )
                self._log.info(
                    "escalating_to_expensive_provider",
                    agent_name=agent_name,
                    triage_provider=primary_provider,
                    triage_model=primary_model,
                    escalation_provider=esc_provider,
                    escalation_model=esc_model,
                    reason=reason,
                    confidence_threshold=threshold,
                )
                return await self._call_provider(
                    provider_name=esc_provider,
                    model=esc_model,
                    messages=messages,
                    agent_name=f"{agent_name}/escalation",
                    thinking=agent_cfg.thinking,
                    route_stage="escalation",
                    **call_kwargs,
                )
"""Fund manager agent: makes final trading decision and outputs TradingSignal."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent, extract_json_from_response
from backend.agents.models import AnalysisServices, AnalysisState, TradingSignal
from backend.agents.prompts import FUND_MANAGER_PROMPT

log = structlog.get_logger(component="agent.fund_manager")


def _parse_signal(
    raw: str, stock_code: str, stock_name: str, trade_date: str
) -> TradingSignal:
    """Parse LLM response into a TradingSignal, with fallback."""
    data = extract_json_from_response(raw)
    if data is not None:
        try:
            return TradingSignal(
                action=data.get("action", "持有"),
                target_price=data.get("target_price"),
                confidence=float(data.get("confidence", 0.5)),
                risk_score=float(data.get("risk_score", 0.5)),
                reasoning=data.get("reasoning", raw[:200]),
                stock_code=stock_code,
                stock_name=stock_name,
                trade_date=trade_date,
            )
        except Exception as exc:
            log.warning("signal_parse_validation_failed", error=str(exc))

    # Fallback: default hold signal with raw reasoning
    return TradingSignal(
        action="持有",
        confidence=0.5,
        risk_score=0.5,
        reasoning=raw[:500] if raw else "LLM response could not be parsed",
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
    )


async def fund_manager_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Synthesize all reports and produce final TradingSignal.

    Returns:
        Dict with 'trading_signal' key (serialized TradingSignal dict).
    """
    debate = state["debate_state"]
    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}\n\n"
        f"=== 多空辩论记录 ===\n{debate['history']}\n\n"
        f"=== 风控评估 ===\n{state['risk_assessment']}"
    )
    raw_response = await call_agent(
        services.llm_router,
        "fund_manager",
        FUND_MANAGER_PROMPT,
        user_content,
    )
    signal = _parse_signal(
        raw_response,
        state["stock_code"],
        state["stock_name"],
        state["trade_date"],
    )
    return {"trading_signal": signal.model_dump()}
grep: backend/agents/__pycache__/fund_manager.cpython-312.pyc: binary file matches
backend/agents/graph.py:15:from backend.agents.fund_manager import fund_manager_node
backend/agents/graph.py:207:        _make_node("fund_manager", fund_manager_node, services, collector),
grep: backend/agents/__pycache__/graph.cpython-312.pyc: binary file matches
backend/agents/fund_manager.py:48:async def fund_manager_node(

exec
/bin/bash -lc "sed -n '280,620p' backend/llm/router.py && sed -n '1,260p' backend/agents/base.py && sed -n '1,360p' backend/agents/graph.py && sed -n '1,280p' backend/agents/collector.py 2>/dev/null || true && grep -R \"class.*Collector\\|FundManagerRecord\\|model_id\\|model_label\" -n backend/agents" in /home/ps/papers/QuantMind
 succeeded in 0ms:
                )

        return response

    async def _call_provider(
        self,
        provider_name: str,
        model: str,
        messages: list[dict[str, str]],
        agent_name: str,
        thinking: ThinkingConfig,
        route_stage: str = "primary",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Execute a chat completion call against a specific provider."""
        client = self._get_client(provider_name)

        self._log.debug(
            "llm_call_start",
            agent_name=agent_name,
            provider=provider_name,
            model=model,
            route_stage=route_stage,
            thinking_type=thinking.type,
        )

        call_kwargs = self._normalize_provider_kwargs(
            provider_name=provider_name,
            model=model,
            base_kwargs=kwargs,
            thinking=thinking,
        )

        response = await client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            **call_kwargs,
        )

        if response.usage:
            await track_usage(
                self._redis,
                agent_name,
                provider_name,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
            self._log.info(
                "llm_call_complete",
                agent_name=agent_name,
                provider=provider_name,
                model=model,
                route_stage=route_stage,
                thinking_type=thinking.type,
                thinking_max_tokens=thinking.max_tokens,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                reasoning_tokens=_extract_reasoning_tokens(response.usage),
            )

        return response

    @staticmethod
    def _normalize_provider_kwargs(
        provider_name: str,
        model: str,
        base_kwargs: dict[str, Any],
        thinking: ThinkingConfig,
    ) -> dict[str, Any]:
        """Apply Kimi K2.x thinking-mode + temperature constraints.

        Kimi exposes thinking via the OpenAI-compatible ``extra_body``
        envelope (it is not part of the upstream Chat Completions
        schema). Reasoning tokens count against the request's total
        ``max_tokens`` budget, so when thinking is enabled the request
        budget is grown by the configured reasoning cap to keep room
        for the actual completion. Temperature is pinned per Moonshot
        spec: 1.0 in thinking mode, 0.6 in non-thinking mode.

        Non-Kimi providers receive the kwargs unchanged — thinking is
        silently dropped.
        """
        normalized = dict(base_kwargs)

        if not (provider_name == "kimi" and model.startswith("kimi-k2")):
            return normalized

        existing_extra = normalized.get("extra_body")
        extra_body: dict[str, Any] = (
            dict(existing_extra) if isinstance(existing_extra, dict) else {}
        )

        if thinking.type == "enabled":
            extra_body["thinking"] = {
                "type": "enabled",
                "max_tokens": thinking.max_tokens,
            }
            normalized["temperature"] = 1
            caller_max = normalized.get("max_tokens")
            if isinstance(caller_max, int):
                normalized["max_tokens"] = caller_max + thinking.max_tokens
        else:
            extra_body["thinking"] = {"type": "disabled"}
            # Kimi rejects arbitrary values when thinking is off; pin to
            # the documented non-thinking constant.
            normalized["temperature"] = 0.6

        normalized["extra_body"] = extra_body
        return normalized

    @staticmethod
    def _select_primary(agent_cfg: AgentConfig) -> tuple[str, str]:
        """Resolve the first-call (provider, model) for an agent.

        With routing.triage_* set, the cheap triage path is the primary;
        otherwise fall back to the agent's own provider/model. P5B-T01
        wires the plumbing — full escalation lives in
        :meth:`_should_escalate` (P5B-T03).
        """
        if agent_cfg.routing is not None:
            return (
                agent_cfg.routing.triage_provider,
                agent_cfg.routing.triage_model,
            )
        return (agent_cfg.provider, agent_cfg.model)

    @staticmethod
    def _should_escalate(
        routing: RoutingConfig | None,
        response: ChatCompletion,
    ) -> tuple[bool, str]:
        """Decide whether the cheap triage answer must be escalated.

        Returns ``(escalate, reason)``. Reason is one of:

        - ``no_routing``      tiered routing not configured for the agent
        - ``no_condition``    routing has no escalation_condition rule
        - ``parse_failed``    triage response was not parseable JSON,
                              had a missing/non-finite/out-of-range
                              ``confidence`` field, or was structurally
                              broken (no choices, no message, etc.).
                              Conservatively escalates so the request
                              never silently degrades to junk output
                              (spec §P5B-T03 fail-open).
        - ``low_confidence``  parsed ``confidence`` field below threshold
        - ``ok``              triage answer is trustworthy, return as-is

        Out-of-range confidence (``< 0`` or ``> 1``), ``NaN``, ``Infinity``
        and ``bool`` are all treated as ``parse_failed``. Python's
        ``json.loads`` accepts NaN/Infinity by default, so we cannot rely
        on parse rejection alone — we explicitly check finiteness and
        bounds.
        """
        if routing is None:
            return False, "no_routing"
        cond = routing.escalation_condition
        if cond is None:
            return False, "no_condition"

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError):
            return True, "parse_failed"
        if not isinstance(content, str) or not content:
            return True, "parse_failed"
        # Cap parser cost — adversarial / runaway LLM output should not
        # be allowed to spend unbounded CPU/memory on json.loads. The
        # contract is a small JSON envelope; 65 KB is generous for the
        # `confidence`/`action`/`reasoning` shape we expect. We measure
        # the UTF-8-encoded byte length so multibyte (e.g. Chinese)
        # content cannot smuggle past a char-count budget.
        if len(content.encode("utf-8")) > _MAX_TRIAGE_JSON_BYTES:
            return True, "parse_failed"
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return True, "parse_failed"
        if not isinstance(parsed, dict):
            return True, "parse_failed"

        if cond.confidence_lt is not None:
            conf = parsed.get("confidence")
            # Python ``bool`` is a subclass of ``int``; reject explicitly
            # so ``True`` / ``False`` don't bypass the numeric gate.
            if isinstance(conf, bool):
                return True, "parse_failed"
            if not isinstance(conf, (int, float)):
                return True, "parse_failed"
            conf_f = float(conf)
            if not math.isfinite(conf_f):
                return True, "parse_failed"
            if conf_f < 0.0 or conf_f > 1.0:
                return True, "parse_failed"
            if conf_f < cond.confidence_lt:
                return True, "low_confidence"
        return False, "ok"

    def _get_client(self, provider_name: str) -> AsyncOpenAI:
        """Get a pre-initialized client for the given provider.

        Clients are eagerly created during config reload, so this is
        a pure read with no race condition.
        """
        if provider_name not in self._clients:
            raise KeyError(
                f"Unknown provider '{provider_name}'. "
                f"Available: {sorted(self._clients.keys())}"
            )
        return self._clients[provider_name]

    async def _maybe_reload_config(self) -> None:
        """Check file mtime and reload config if changed."""
        try:
            current_mtime = self._config_path.stat().st_mtime
        except OSError:
            return

        if current_mtime <= self._config_mtime:
            return

        async with self._lock:
            # Double-check inside lock
            try:
                current_mtime = self._config_path.stat().st_mtime
            except OSError:
                return
            if current_mtime <= self._config_mtime:
                return
            await self._reload_config()

    async def _reload_config(self) -> None:
        """Reload configuration from YAML file.

        Uses asyncio.to_thread for blocking file I/O to avoid
        stalling the event loop. Eagerly creates clients for all
        providers so the read path (_get_client) is pure lookup.
        """
        new_config = await asyncio.to_thread(
            load_router_config, self._config_path
        )
        old_config = self._config

        # Close clients for providers whose config changed or were removed
        if old_config is not None:
            for name in list(self._clients.keys()):
                if name not in new_config.providers:
                    await self._clients.pop(name).close()
                elif new_config.providers[name] != old_config.providers.get(name):
                    await self._clients.pop(name).close()

        # Eagerly create clients for all providers not already cached
        for name, provider_cfg in new_config.providers.items():
            if name not in self._clients:
                self._clients[name] = create_openai_client(provider_cfg)

        self._config = new_config
        self._config_mtime = self._config_path.stat().st_mtime

        self._log.info(
            "config_reloaded",
            providers=sorted(new_config.providers.keys()),
            agents=sorted(new_config.agents.keys()),
        )
"""Base helpers for agent LLM calls and response parsing."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.llm.router import LLMRouter

log = structlog.get_logger(component="agent_base")

_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


async def call_agent(
    router: LLMRouter,
    agent_name: str,
    system_prompt: str,
    user_content: str,
) -> str:
    """Call the LLM via router for a given agent.

    Args:
        router: The LLM router instance.
        agent_name: Key in agent_models.yaml (e.g. "news_crawler").
        system_prompt: Chinese system prompt for the agent.
        user_content: User message with context data.

    Returns:
        LLM response content string. On failure, returns an error
        description string (graceful degradation, never raises).
    """
    try:
        response = await router.complete(
            agent_name,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        if not response.choices:
            log.warning("agent_empty_response", agent_name=agent_name)
            return f"[{agent_name} error: empty response]"
        content = response.choices[0].message.content or ""
        log.info(
            "agent_call_complete",
            agent_name=agent_name,
            content_length=len(content),
        )
        return content
    except Exception as exc:
        log.warning(
            "agent_call_failed",
            agent_name=agent_name,
            error=str(exc),
        )
        return f"[{agent_name} error: {exc}]"


def extract_json_from_response(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from LLM response text.

    Args:
        text: Raw LLM response that may contain JSON embedded in text.

    Returns:
        Parsed dict if valid JSON found, None otherwise.
    """
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except (json.JSONDecodeError, ValueError):
        return None
"""LangGraph state graph for the multi-agent analysis pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph

from backend.agents.bear_researcher import bear_researcher_node
from backend.agents.bull_researcher import bull_researcher_node
from backend.agents.collector import EventEmitter, RunCollector
from backend.agents.fund_manager import fund_manager_node
from backend.agents.fundamental_analyst import fundamental_analyst_node
from backend.agents.intelligence_officer import intelligence_officer_node
from backend.agents.models import (
    AnalysisServices,
    AnalysisState,
    DebateState,
    PipelineConfig,
    TradingSignal,
)
from backend.agents.news_crawler import news_crawler_node
from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.agents.risk_officer import risk_officer_node
from backend.agents.sentiment_analyst import sentiment_analyst_node
from backend.agents.technical_analyst import technical_analyst_node

log = structlog.get_logger(component="analysis_graph")

DEBATE_AGENTS = ("bull_researcher", "bear_researcher")


def should_continue_debate(
    state: AnalysisState, config: PipelineConfig
) -> str:
    """Determine next node after a debate turn.

    Faithful port of TradingAgents-CN conditional logic:
    - count >= 2 * max_rounds → risk_officer (end debate)
    - last speaker was Bull → bear_researcher
    - last speaker was Bear or count == 0 → bull_researcher
    """
    debate = state["debate_state"]
    count = debate["count"]
    max_count = 2 * config.max_debate_rounds

    if count >= max_count:
        return "risk_officer"

    current = debate["current_response"]
    if current.startswith("Bull:"):
        return "bear_researcher"
    return "bull_researcher"


async def _init_debate_node(state: AnalysisState) -> dict[str, Any]:
    """Initialize debate state with empty values. Not recorded as agent step."""
    return {
        "debate_state": DebateState(
            history="",
            bull_history="",
            bear_history="",
            current_response="",
            count=0,
        )
    }


def _make_node(
    node_name: str,
    fn: Any,
    services: AnalysisServices,
    collector: RunCollector | None,
) -> Any:
    """Wrap an agent node function to inject services and record steps.

    When `collector` is provided, each call emits agent_started /
    agent_completed events and appends an AgentStepRecord. Errors from
    `fn` are forwarded after emitting a failed step + error event, so
    LangGraph can still terminate the run; callers decide whether to
    re-raise.
    """

    async def wrapper(state: AnalysisState) -> dict[str, Any]:
        round_ = 0
        if node_name in DEBATE_AGENTS:
            current_count = state.get("debate_state", {}).get("count", 0)
            round_ = (current_count // 2) + 1

        if collector is not None:
            started_at = await collector.on_agent_started(node_name, round_)
        else:
            started_at = datetime.now(tz=UTC)

        try:
            result = await fn(state, services)
        except Exception as exc:
            if collector is not None:
                # Record a proper failed step (was previously emitted as
                # status=completed with empty content, which masked the
                # failure in the persisted record).
                await collector.on_agent_failed(
                    node_name, round_, started_at, str(exc)
                )
                await collector.on_error(f"{node_name}: {exc}")
            raise

        if collector is not None:
            await collector.on_agent_completed(
                node_name, round_, started_at, result
            )
        return result

    wrapper.__name__ = fn.__name__
    return wrapper


def build_analysis_graph(
    services: AnalysisServices,
    *,
    collector: RunCollector | None = None,
) -> Any:
    """Build and compile the LangGraph analysis pipeline.

    Pipeline:
    1. Parallel: news, sentiment, fundamental, technical analysts
    2. Sequential: intelligence_officer (reads all 4 reports)
    3. Init debate → Bull/Bear alternating debate with conditional edges
    4. Sequential: risk_officer → fund_manager

    Args:
        services: Bundle of LLM router and data services.
        collector: Optional run collector for step recording and SSE
            event emission. When None, graph runs without instrumentation
            (legacy callers).

    Returns:
        Compiled LangGraph graph ready for ainvoke().
    """
    config = services.pipeline_config
    graph = StateGraph(AnalysisState)

    # Stage 1: parallel analysts
    graph.add_node(
        "news_crawler",
        _make_node("news_crawler", news_crawler_node, services, collector),
    )
    graph.add_node(
        "sentiment_analyst",
        _make_node(
            "sentiment_analyst", sentiment_analyst_node, services, collector
        ),
    )
    graph.add_node(
        "fundamental_analyst",
        _make_node(
            "fundamental_analyst",
            fundamental_analyst_node,
            services,
            collector,
        ),
    )
    graph.add_node(
        "technical_analyst",
        _make_node(
            "technical_analyst",
            technical_analyst_node,
            services,
            collector,
        ),
    )
    graph.add_node(
        "intelligence_officer",
        _make_node(
            "intelligence_officer",
            intelligence_officer_node,
            services,
            collector,
        ),
    )

    # Stage 2: debate. init_debate is not recorded as an agent step.
    graph.add_node("init_debate", _init_debate_node)
    graph.add_node(
        "bull_researcher",
        _make_node(
            "bull_researcher", bull_researcher_node, services, collector
        ),
    )
    graph.add_node(
        "bear_researcher",
        _make_node(
            "bear_researcher", bear_researcher_node, services, collector
        ),
    )

    # Stage 3: decision
    graph.add_node(
        "risk_officer",
        _make_node("risk_officer", risk_officer_node, services, collector),
    )
    graph.add_node(
        "fund_manager",
        _make_node("fund_manager", fund_manager_node, services, collector),
    )

    # Edges: START → 4 parallel analysts
    graph.add_edge(START, "news_crawler")
    graph.add_edge(START, "sentiment_analyst")
    graph.add_edge(START, "fundamental_analyst")
    graph.add_edge(START, "technical_analyst")

    # 4 analysts → intelligence_officer
    graph.add_edge("news_crawler", "intelligence_officer")
    graph.add_edge("sentiment_analyst", "intelligence_officer")
    graph.add_edge("fundamental_analyst", "intelligence_officer")
    graph.add_edge("technical_analyst", "intelligence_officer")

    # intelligence_officer → init_debate → debate loop
    graph.add_edge("intelligence_officer", "init_debate")

    # Debate conditional routing
    def _debate_router(state: AnalysisState) -> str:
        return should_continue_debate(state, config)

    graph.add_conditional_edges(
        "init_debate",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )
    graph.add_conditional_edges(
        "bull_researcher",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )
    graph.add_conditional_edges(
        "bear_researcher",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )

    # Decision: risk → fund_manager → END
    graph.add_edge("risk_officer", "fund_manager")
    graph.add_edge("fund_manager", END)

    return graph.compile()


async def run_analysis(
    stock_code: str,
    services: AnalysisServices,
    *,
    run_id: str | None = None,
    emitter: EventEmitter | None = None,
) -> AnalysisRunResult:
    """Run the full multi-agent analysis pipeline for a stock.

    Args:
        stock_code: 6-digit A-share stock code.
        services: Bundle of LLM router and data services.
        run_id: Optional pre-assigned UUID (the jobs API assigns one so
            the stream can key events before run_analysis starts).
        emitter: Optional async callable that receives SSE event dicts.
            When provided, per-agent started/completed events are pushed
            as the pipeline progresses (Session A2).

    Returns:
        AnalysisRunResult containing the terminal TradingSignal and the
        complete AnalysisRecord. `record.signal_id` stays None until the
        caller persists the signal and assigns it.
    """
    resolved_run_id = run_id or str(uuid.uuid4())
    log.info("analysis_started", stock_code=stock_code, run_id=resolved_run_id)

    # Look up stock name
    stock_name = stock_code
    try:
        quote = await services.market_data.get_stock_realtime(stock_code)
        stock_name = getattr(quote, "name", stock_code)
    except Exception as exc:
        log.warning(
            "stock_name_lookup_failed", stock_code=stock_code, error=str(exc)
        )

    trade_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    collector = RunCollector(
        run_id=resolved_run_id,
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
        max_rounds=services.pipeline_config.max_debate_rounds,
        emitter=emitter,
    )

    initial_state: AnalysisState = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "news_report": "",
        "sentiment_report": "",
        "fundamental_report": "",
        "technical_report": "",
        "intelligence_report": "",
        "debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_assessment": "",
        "trading_signal": {},
    }

    compiled = build_analysis_graph(services, collector=collector)

    try:
        result = await compiled.ainvoke(initial_state)
    except Exception as exc:
        log.error(
            "analysis_pipeline_failed",
            stock_code=stock_code,
            run_id=resolved_run_id,
            error=str(exc),
        )
        record = collector.finalize(
            status="failed", signal=None, error=str(exc)
        )
        raise AnalysisRunError(record) from exc

    # When the graph completes but at least one agent finalized as
    # failed (either a graceful "[agent error: ...]" string from
    # call_agent or a hard exception caught in _make_node), the run is
    # NOT a clean success. Promoting it to status=completed with a
    # synthetic neutral signal would silently bypass the failure
    # instead of surfacing it through /history and the SSE error path.
    if collector.has_failed_steps():
        summary = collector.first_failure_summary() or "agent failed"
        log.warning(
            "analysis_pipeline_partial_failure",
            stock_code=stock_code,
            run_id=resolved_run_id,
            failure=summary,
        )
"""Run collector: accumulates per-agent steps from the LangGraph pipeline.

Used by run_analysis() to build an AnalysisRecord alongside the terminal
TradingSignal, and by the live SSE stream API (Session A2) to push events
to subscribers as agents complete.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from backend.agents.models import TradingSignal
from backend.agents.records import (
    AgentStepRecord,
    AnalysisRecord,
    DebateRoundRecord,
    FundManagerRecord,
    RiskAssessmentRecord,
)

log = structlog.get_logger(component="run_collector")

EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]

ANALYST_AGENTS = (
    "news_crawler",
    "sentiment_analyst",
    "fundamental_analyst",
    "technical_analyst",
)


def extract_content(node_name: str, delta: dict[str, Any]) -> str:
    """Extract the human-visible content a node produced into state.

    Uses exact state keys from backend/agents/*.py return values.
    Strips Bull:/Bear: prefix from debate current_response per plan §5.1.
    """
    if node_name == "news_crawler":
        return delta.get("news_report", "") or ""
    if node_name == "sentiment_analyst":
        return delta.get("sentiment_report", "") or ""
    if node_name == "fundamental_analyst":
        return delta.get("fundamental_report", "") or ""
    if node_name == "technical_analyst":
        return delta.get("technical_report", "") or ""
    if node_name == "intelligence_officer":
        return delta.get("intelligence_report", "") or ""
    if node_name in ("bull_researcher", "bear_researcher"):
        debate = delta.get("debate_state") or {}
        current = debate.get("current_response", "") or ""
        for prefix in ("Bull: ", "Bear: "):
            if current.startswith(prefix):
                return current[len(prefix) :]
        return current
    if node_name == "risk_officer":
        return delta.get("risk_assessment", "") or ""
    if node_name == "fund_manager":
        signal = delta.get("trading_signal") or {}
        return signal.get("reasoning", "") or ""
    return ""


def classify_status(
    agent: str, content: str
) -> tuple[str, str | None]:
    """Detect call_agent() graceful-error string and mark step failed."""
    err_prefix = f"[{agent} error:"
    if content.startswith(err_prefix):
        return ("failed", content)
    return ("completed", None)


class RunCollector:
    """Accumulates per-agent step records for one analysis run.

    Thread-safety: all mutations happen on the asyncio event loop; appends
    to list under GIL are atomic. Do not share across loops.
    """

    def __init__(
        self,
        *,
        run_id: str,
        stock_code: str,
        stock_name: str,
        trade_date: str,
        max_rounds: int,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._run_id = run_id
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._trade_date = trade_date
        self._max_rounds = max_rounds
        self._emitter = emitter
        self._steps: list[AgentStepRecord] = []
        self._created_at = datetime.now(tz=UTC)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def steps(self) -> list[AgentStepRecord]:
        return list(self._steps)

    async def on_agent_started(self, agent: str, round_: int) -> datetime:
        """Emit agent_started event and return started_at timestamp."""
        started_at = datetime.now(tz=UTC)
        await self._emit(
            {
                "event_type": "agent_started",
                "agent": agent,
                "round": round_,
                "timestamp": started_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return started_at

    async def on_agent_completed(
        self,
        agent: str,
        round_: int,
        started_at: datetime,
        delta: dict[str, Any],
    ) -> AgentStepRecord:
        """Extract content from node delta, record step, emit event."""
        content = extract_content(agent, delta)
        status, error = classify_status(agent, content)
        completed_at = datetime.now(tz=UTC)
        step = AgentStepRecord(
            agent=agent,
            round=round_,
            content=content,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            error=error,
        )
        self._steps.append(step)
        await self._emit(
            {
                "event_type": "agent_completed",
                "agent": agent,
                "round": round_,
                "content": content,
                "model_label": step.model_label,
                "model_id": step.model_id,
                "status": status,
                "error": error,
                "timestamp": completed_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return step

    async def on_agent_failed(
        self,
        agent: str,
        round_: int,
        started_at: datetime,
        error: str,
    ) -> AgentStepRecord:
        """Record a hard failure (raised exception) as a failed step.

        Distinct from `on_agent_completed` with a graceful-error string:
        here the node fn never returned, so there is no state delta and
        content stays empty. The emitted event still uses
        ``agent_completed`` so the frontend's discriminated union does
        not need a third case, but ``status="failed"`` carries the signal.
        """
        completed_at = datetime.now(tz=UTC)
        step = AgentStepRecord(
            agent=agent,  # type: ignore[arg-type]
            round=round_,
            content="",
            started_at=started_at,
            completed_at=completed_at,
            status="failed",
            error=error,
        )
        self._steps.append(step)
        await self._emit(
            {
                "event_type": "agent_completed",
                "agent": agent,
                "round": round_,
                "content": "",
                "model_label": step.model_label,
                "model_id": step.model_id,
                "status": "failed",
                "error": error,
                "timestamp": completed_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return step

    def has_failed_steps(self) -> bool:
        """True when any recorded step finalized with status='failed'."""
        return any(s.status == "failed" for s in self._steps)

    def first_failure_summary(self) -> str | None:
        """Compact human-readable summary of the first failed step, or None."""
        for s in self._steps:
            if s.status == "failed":
                err = s.error or "agent failed"
                return f"{s.agent}: {err}"
        return None

    async def on_pipeline_completed(
        self, *, record_id: str | None, signal_id: str | None
    ) -> None:
        await self._emit(
            {
                "event_type": "pipeline_completed",
                "run_id": self._run_id,
                "record_id": record_id,
                "signal_id": signal_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def on_error(self, message: str) -> None:
        await self._emit(
            {
                "event_type": "error",
                "message": message,
                "run_id": self._run_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._emitter is None:
            return
        try:
            await self._emitter(event)
        except Exception as exc:
            log.warning(
                "collector_emit_failed",
                run_id=self._run_id,
                error=str(exc),
                event_type=event.get("event_type"),
            )

    def finalize(
        self,
        *,
        status: str,
        signal: TradingSignal | None,
        error: str | None = None,
    ) -> AnalysisRecord:
        """Build the terminal AnalysisRecord from accumulated steps."""
        analysts = [s for s in self._steps if s.agent in ANALYST_AGENTS]

        intelligence = next(
            (s for s in self._steps if s.agent == "intelligence_officer"),
            None,
        )

        debates = self._build_debate_rounds()

        risk_step = next(
            (s for s in self._steps if s.agent == "risk_officer"), None
        )
        risk = (
            RiskAssessmentRecord(content=risk_step.content, step=risk_step)
            if risk_step is not None
            else None
        )

        fund_step = next(
            (s for s in self._steps if s.agent == "fund_manager"), None
grep: backend/agents/__pycache__/collector.cpython-312.pyc: binary file matches
backend/agents/collector.py:21:    FundManagerRecord,
backend/agents/collector.py:78:class RunCollector:
backend/agents/collector.py:153:                "model_label": step.model_label,
backend/agents/collector.py:154:                "model_id": step.model_id,
backend/agents/collector.py:195:                "model_label": step.model_label,
backend/agents/collector.py:196:                "model_id": step.model_id,
backend/agents/collector.py:284:            decision = FundManagerRecord(
grep: backend/agents/__pycache__/records.cpython-312.pyc: binary file matches
backend/agents/records.py:56:    model_label: str = ""
backend/agents/records.py:57:    model_id: str = ""
backend/agents/records.py:92:class FundManagerRecord(BaseModel):
backend/agents/records.py:127:    decision: FundManagerRecord | None = None

exec
/bin/bash -lc "sed -n '240,360p' backend/agents/graph.py && sed -n '260,340p' backend/agents/collector.py && sed -n '1,240p' backend/agents/models.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )
    graph.add_conditional_edges(
        "bear_researcher",
        _debate_router,
        {
            "bull_researcher": "bull_researcher",
            "bear_researcher": "bear_researcher",
            "risk_officer": "risk_officer",
        },
    )

    # Decision: risk → fund_manager → END
    graph.add_edge("risk_officer", "fund_manager")
    graph.add_edge("fund_manager", END)

    return graph.compile()


async def run_analysis(
    stock_code: str,
    services: AnalysisServices,
    *,
    run_id: str | None = None,
    emitter: EventEmitter | None = None,
) -> AnalysisRunResult:
    """Run the full multi-agent analysis pipeline for a stock.

    Args:
        stock_code: 6-digit A-share stock code.
        services: Bundle of LLM router and data services.
        run_id: Optional pre-assigned UUID (the jobs API assigns one so
            the stream can key events before run_analysis starts).
        emitter: Optional async callable that receives SSE event dicts.
            When provided, per-agent started/completed events are pushed
            as the pipeline progresses (Session A2).

    Returns:
        AnalysisRunResult containing the terminal TradingSignal and the
        complete AnalysisRecord. `record.signal_id` stays None until the
        caller persists the signal and assigns it.
    """
    resolved_run_id = run_id or str(uuid.uuid4())
    log.info("analysis_started", stock_code=stock_code, run_id=resolved_run_id)

    # Look up stock name
    stock_name = stock_code
    try:
        quote = await services.market_data.get_stock_realtime(stock_code)
        stock_name = getattr(quote, "name", stock_code)
    except Exception as exc:
        log.warning(
            "stock_name_lookup_failed", stock_code=stock_code, error=str(exc)
        )

    trade_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    collector = RunCollector(
        run_id=resolved_run_id,
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
        max_rounds=services.pipeline_config.max_debate_rounds,
        emitter=emitter,
    )

    initial_state: AnalysisState = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "trade_date": trade_date,
        "news_report": "",
        "sentiment_report": "",
        "fundamental_report": "",
        "technical_report": "",
        "intelligence_report": "",
        "debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "risk_assessment": "",
        "trading_signal": {},
    }

    compiled = build_analysis_graph(services, collector=collector)

    try:
        result = await compiled.ainvoke(initial_state)
    except Exception as exc:
        log.error(
            "analysis_pipeline_failed",
            stock_code=stock_code,
            run_id=resolved_run_id,
            error=str(exc),
        )
        record = collector.finalize(
            status="failed", signal=None, error=str(exc)
        )
        raise AnalysisRunError(record) from exc

    # When the graph completes but at least one agent finalized as
    # failed (either a graceful "[agent error: ...]" string from
    # call_agent or a hard exception caught in _make_node), the run is
    # NOT a clean success. Promoting it to status=completed with a
    # synthetic neutral signal would silently bypass the failure
    # instead of surfacing it through /history and the SSE error path.
    if collector.has_failed_steps():
        summary = collector.first_failure_summary() or "agent failed"
        log.warning(
            "analysis_pipeline_partial_failure",
            stock_code=stock_code,
            run_id=resolved_run_id,
            failure=summary,
        )
        """Build the terminal AnalysisRecord from accumulated steps."""
        analysts = [s for s in self._steps if s.agent in ANALYST_AGENTS]

        intelligence = next(
            (s for s in self._steps if s.agent == "intelligence_officer"),
            None,
        )

        debates = self._build_debate_rounds()

        risk_step = next(
            (s for s in self._steps if s.agent == "risk_officer"), None
        )
        risk = (
            RiskAssessmentRecord(content=risk_step.content, step=risk_step)
            if risk_step is not None
            else None
        )

        fund_step = next(
            (s for s in self._steps if s.agent == "fund_manager"), None
        )
        decision = None
        if fund_step is not None and signal is not None:
            decision = FundManagerRecord(
                action=signal.action,
                target_price=signal.target_price,
                confidence=signal.confidence,
                risk_score=signal.risk_score,
                reasoning=signal.reasoning,
                step=fund_step,
            )

        current_round = max(
            (s.round for s in self._steps if s.round > 0),
            default=0,
        )

        completed_at = datetime.now(tz=UTC) if status != "running" else None

        return AnalysisRecord(
            run_id=self._run_id,
            stock_code=self._stock_code,
            stock_name=self._stock_name,
            trade_date=self._trade_date,
            status=status,  # type: ignore[arg-type]
            max_rounds=self._max_rounds,
            current_round=current_round,
            steps=list(self._steps),
            analysts=analysts,
            intelligence_officer=intelligence,
            debates=debates,
            risk_assessment=risk,
            decision=decision,
            signal_id=None,
            created_at=self._created_at,
            completed_at=completed_at,
            error=error,
        )

    def _build_debate_rounds(self) -> list[DebateRoundRecord]:
        """Group bull/bear steps by round number."""
        by_round: dict[int, dict[str, AgentStepRecord]] = {}
        for step in self._steps:
            if step.agent not in ("bull_researcher", "bear_researcher"):
                continue
            if step.round <= 0:
                continue
            bucket = by_round.setdefault(step.round, {})
            side = "bull" if step.agent == "bull_researcher" else "bear"
            bucket[side] = step
        rounds: list[DebateRoundRecord] = []
        for r in sorted(by_round.keys()):
            b = by_round[r]
            rounds.append(
                DebateRoundRecord(
                    round=r,
                    bull=b.get("bull"),
                    bear=b.get("bear"),
                )
            )
"""Pydantic models and TypedDict state for the multi-agent analysis pipeline."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class DebateState(TypedDict):
    """State tracking for Bull/Bear debate rounds."""

    history: str
    bull_history: str
    bear_history: str
    current_response: str
    count: int


class AnalysisState(TypedDict):
    """LangGraph state passed through the analysis pipeline."""

    stock_code: str
    stock_name: str
    trade_date: str
    # Stage 1: analysis reports
    news_report: str
    sentiment_report: str
    fundamental_report: str
    technical_report: str
    intelligence_report: str
    # Stage 2: debate
    debate_state: DebateState
    # Stage 3: decision
    risk_assessment: str
    trading_signal: dict[str, Any]


class TradingSignal(BaseModel):
    """Final output of the multi-agent analysis pipeline."""

    model_config = ConfigDict(frozen=True)

    action: Literal["买入", "持有", "卖出"]
    target_price: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    stock_code: str
    stock_name: str
    trade_date: str


class PipelineConfig(BaseModel):
    """Configuration for the analysis pipeline."""

    model_config = ConfigDict(frozen=True)

    max_debate_rounds: int = 2
    analysis_timeout_seconds: int = 300


class AnalysisServices(BaseModel):
    """Bundle of services injected into agent nodes."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    llm_router: Any  # LLMRouter
    market_data: Any  # MarketDataService
    history_data: Any  # HistoryDataService
    news_crawler: Any  # NewsCrawlerService
    mirofish_simulator: Any = None  # MiroFishSimulator (optional)
    mongodb: Any = None  # MongoDBService (optional, for simulation persistence)
    pipeline_config: PipelineConfig = PipelineConfig()

exec
/bin/bash -lc "sed -n '340,430p' backend/agents/graph.py && sed -n '220,340p' backend/agents/collector.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
            error=str(exc),
        )
        record = collector.finalize(
            status="failed", signal=None, error=str(exc)
        )
        raise AnalysisRunError(record) from exc

    # When the graph completes but at least one agent finalized as
    # failed (either a graceful "[agent error: ...]" string from
    # call_agent or a hard exception caught in _make_node), the run is
    # NOT a clean success. Promoting it to status=completed with a
    # synthetic neutral signal would silently bypass the failure
    # instead of surfacing it through /history and the SSE error path.
    if collector.has_failed_steps():
        summary = collector.first_failure_summary() or "agent failed"
        log.warning(
            "analysis_pipeline_partial_failure",
            stock_code=stock_code,
            run_id=resolved_run_id,
            failure=summary,
        )
        record = collector.finalize(
            status="failed", signal=None, error=summary
        )
        raise AnalysisRunError(record)

    signal_data = result.get("trading_signal", {})
    signal = TradingSignal(
        action=signal_data.get("action", "持有"),
        target_price=signal_data.get("target_price"),
        confidence=signal_data.get("confidence", 0.5),
        risk_score=signal_data.get("risk_score", 0.5),
        reasoning=signal_data.get("reasoning", "Pipeline completed"),
        stock_code=stock_code,
        stock_name=stock_name,
        trade_date=trade_date,
    )

    record = collector.finalize(status="completed", signal=signal)

    log.info(
        "analysis_completed",
        stock_code=stock_code,
        run_id=resolved_run_id,
        action=signal.action,
        confidence=signal.confidence,
    )
    return AnalysisRunResult(signal=signal, record=record)


class AnalysisRunError(Exception):
    """Surfaces an AnalysisRecord through an exception path.

    Callers (jobs API, /stock, scheduler) catch this and persist
    ``record`` so failed runs still appear in /history. The exception
    message comes from ``record.error`` when available.
    """

    def __init__(self, record: AnalysisRecord) -> None:
        super().__init__(record.error or "analysis failed")
        self.record = record
        await self._emit(
            {
                "event_type": "pipeline_completed",
                "run_id": self._run_id,
                "record_id": record_id,
                "signal_id": signal_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def on_error(self, message: str) -> None:
        await self._emit(
            {
                "event_type": "error",
                "message": message,
                "run_id": self._run_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._emitter is None:
            return
        try:
            await self._emitter(event)
        except Exception as exc:
            log.warning(
                "collector_emit_failed",
                run_id=self._run_id,
                error=str(exc),
                event_type=event.get("event_type"),
            )

    def finalize(
        self,
        *,
        status: str,
        signal: TradingSignal | None,
        error: str | None = None,
    ) -> AnalysisRecord:
        """Build the terminal AnalysisRecord from accumulated steps."""
        analysts = [s for s in self._steps if s.agent in ANALYST_AGENTS]

        intelligence = next(
            (s for s in self._steps if s.agent == "intelligence_officer"),
            None,
        )

        debates = self._build_debate_rounds()

        risk_step = next(
            (s for s in self._steps if s.agent == "risk_officer"), None
        )
        risk = (
            RiskAssessmentRecord(content=risk_step.content, step=risk_step)
            if risk_step is not None
            else None
        )

        fund_step = next(
            (s for s in self._steps if s.agent == "fund_manager"), None
        )
        decision = None
        if fund_step is not None and signal is not None:
            decision = FundManagerRecord(
                action=signal.action,
                target_price=signal.target_price,
                confidence=signal.confidence,
                risk_score=signal.risk_score,
                reasoning=signal.reasoning,
                step=fund_step,
            )

        current_round = max(
            (s.round for s in self._steps if s.round > 0),
            default=0,
        )

        completed_at = datetime.now(tz=UTC) if status != "running" else None

        return AnalysisRecord(
            run_id=self._run_id,
            stock_code=self._stock_code,
            stock_name=self._stock_name,
            trade_date=self._trade_date,
            status=status,  # type: ignore[arg-type]
            max_rounds=self._max_rounds,
            current_round=current_round,
            steps=list(self._steps),
            analysts=analysts,
            intelligence_officer=intelligence,
            debates=debates,
            risk_assessment=risk,
            decision=decision,
            signal_id=None,
            created_at=self._created_at,
            completed_at=completed_at,
            error=error,
        )

    def _build_debate_rounds(self) -> list[DebateRoundRecord]:
        """Group bull/bear steps by round number."""
        by_round: dict[int, dict[str, AgentStepRecord]] = {}
        for step in self._steps:
            if step.agent not in ("bull_researcher", "bear_researcher"):
                continue
            if step.round <= 0:
                continue
            bucket = by_round.setdefault(step.round, {})
            side = "bull" if step.agent == "bull_researcher" else "bear"
            bucket[side] = step
        rounds: list[DebateRoundRecord] = []
        for r in sorted(by_round.keys()):
            b = by_round[r]
            rounds.append(
                DebateRoundRecord(
                    round=r,
                    bull=b.get("bull"),
                    bear=b.get("bear"),
                )
            )

exec
/bin/bash -lc "sed -n '1,260p' backend/llm/providers.py && sed -n '1,230p' config/agent_models.yaml" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""LLM provider configuration models and client factory."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ENV_PATTERN = re.compile(r"^\$\{(\w+)\}$")


def resolve_env_var(value: str) -> str:
    """Resolve '${ENV_VAR}' syntax to actual environment variable value.

    Plain strings are returned unchanged. Raises ValueError if the
    referenced environment variable is not set or is empty.
    """
    match = _ENV_PATTERN.match(value)
    if not match:
        return value
    var_name = match.group(1)
    resolved = os.environ.get(var_name)
    if not resolved:
        raise ValueError(
            f"Environment variable {var_name} is not set or empty"
        )
    return resolved


# -- Frozen Pydantic config models --


class FallbackConfig(BaseModel):
    """Fallback provider specification for an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class EscalationCondition(BaseModel):
    """Triggers that promote a triage answer to the escalation provider.

    Today only ``confidence_lt`` (numeric threshold against the parsed
    JSON ``confidence`` field) is implemented; the model is the typed
    schema deferred from P5B-T01. ``extra='forbid'`` keeps a typo from
    silently disabling escalation, and the post-init validator forces at
    least one rule to be set so an empty mapping never reaches the
    router with the appearance of "configured but inert".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    confidence_lt: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> EscalationCondition:
        if self.confidence_lt is None:
            raise ValueError(
                "escalation_condition must define at least one rule "
                "(currently supported: confidence_lt)"
            )
        return self


class RoutingConfig(BaseModel):
    """Tiered triage→escalation routing for an agent.

    Triage runs the cheap provider first; if escalation_condition fires
    (confidence below threshold, parse failure, …) the router re-runs
    against the expensive provider. The actual escalation decision lives
    in :meth:`LLMRouter._should_escalate` (P5B-T03).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    triage_provider: str = Field(min_length=1)
    triage_model: str = Field(min_length=1)
    escalation_provider: str | None = Field(default=None, min_length=1)
    escalation_model: str | None = Field(default=None, min_length=1)
    escalation_condition: EscalationCondition | None = Field(default=None)

    @model_validator(mode="after")
    def _check_escalation_pair(self) -> RoutingConfig:
        has_provider = self.escalation_provider is not None
        has_model = self.escalation_model is not None
        if has_provider != has_model:
            raise ValueError(
                "escalation_provider and escalation_model must be set "
                "together (or both omitted)"
            )
        if self.escalation_condition is not None and not has_provider:
            raise ValueError(
                "escalation_condition requires both escalation_provider "
                "and escalation_model"
            )
        return self


class ThinkingConfig(BaseModel):
    """Per-agent Kimi K2.6 thinking-mode configuration.

    keep="all" keeps every round's reasoning_content in context (needed
    for multi-round bull/bear debate); "last_round" only keeps the most
    recent for terminal judgement; "none" pairs with type=disabled to
    drop reasoning entirely for cheap summary agents. Bounds match the
    Moonshot K2.6 reasoning cap (32k upper, 0 lower).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["enabled", "disabled"] = "enabled"
    max_tokens: int = Field(default=8000, ge=0, le=32_000)
    keep: Literal["all", "last_round", "none"] = "all"

    @model_validator(mode="after")
    def _check_disabled_invariant(self) -> ThinkingConfig:
        if self.type == "disabled":
            if self.max_tokens != 0 or self.keep != "none":
                raise ValueError(
                    "thinking.type='disabled' requires max_tokens=0 and "
                    "keep='none'"
                )
        elif self.max_tokens == 0:
            raise ValueError(
                "thinking.type='enabled' requires max_tokens > 0"
            )
        return self


class AgentConfig(BaseModel):
    """Per-agent LLM routing configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    fallback: FallbackConfig | None = None
    routing: RoutingConfig | None = None
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    frequency: str = ""
    task: str = ""


class ProviderConfig(BaseModel):
    """LLM provider connection configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    default_model: str = Field(min_length=1)


class DefaultsConfig(BaseModel):
    """Default parameters for LLM calls."""

    model_config = ConfigDict(frozen=True)

    temperature: float = 0.3
    max_tokens: int = 4096


class RouterConfig(BaseModel):
    """Complete YAML configuration schema for the LLM router."""

    model_config = ConfigDict(frozen=True)

    providers: dict[str, ProviderConfig]
    agents: dict[str, AgentConfig]
    defaults: DefaultsConfig = DefaultsConfig()

    @model_validator(mode="after")
    def _check_provider_references(self) -> RouterConfig:
        """Fail fast on agent.provider / fallback / routing typos.

        Without this, a bad provider name only surfaces at runtime as
        ``Unknown provider`` from inside the request hot path, with no
        agent context. Catching it here gives the operator a single
        line pointing at the offending YAML key.
        """
        known = set(self.providers)
        for agent_name, agent in self.agents.items():
            if agent.provider not in known:
                raise ValueError(
                    f"agents.{agent_name}.provider='{agent.provider}' "
                    f"not in providers={sorted(known)}"
                )
            if agent.fallback is not None and agent.fallback.provider not in known:
                raise ValueError(
                    f"agents.{agent_name}.fallback.provider="
                    f"'{agent.fallback.provider}' not in providers="
                    f"{sorted(known)}"
                )
            if agent.routing is not None:
                if agent.routing.triage_provider not in known:
                    raise ValueError(
                        f"agents.{agent_name}.routing.triage_provider="
                        f"'{agent.routing.triage_provider}' not in "
                        f"providers={sorted(known)}"
                    )
                esc = agent.routing.escalation_provider
                if esc is not None and esc not in known:
                    raise ValueError(
                        f"agents.{agent_name}.routing.escalation_provider="
                        f"'{esc}' not in providers={sorted(known)}"
                    )
        return self


# -- Client factory --


def create_openai_client(provider_config: ProviderConfig) -> AsyncOpenAI:
    """Create an AsyncOpenAI client from a provider configuration.

    Resolves ${ENV_VAR} syntax in the api_key field before creating
    the client.
    """
    api_key = resolve_env_var(provider_config.api_key)
    return AsyncOpenAI(
        base_url=provider_config.base_url,
        api_key=api_key,
    )


# -- YAML loading --


def load_router_config(yaml_path: str | Path) -> RouterConfig:
    """Load and validate router configuration from a YAML file.

    Returns an immutable RouterConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the schema is invalid.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return RouterConfig.model_validate(raw)
# config/agent_models.yaml — LLM Router core configuration
# Blueprint V3 §2.2 + Phase 5B §2.8 (per-agent thinking/routing)

providers:
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    api_key: "${DEEPSEEK_API_KEY}"
    default_model: "deepseek-v4-pro"
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    default_model: "qwen3.6-plus"
  kimi:
    base_url: "https://api.moonshot.cn/v1"
    api_key: "${MOONSHOT_API_KEY}"
    default_model: "kimi-k2.6"
  # --- Reserved for future expansion ---
  # claude:
  #   base_url: "https://api.anthropic.com/v1"
  #   api_key: "${CLAUDE_API_KEY}"
  #   default_model: "claude-sonnet-4-6"
  # openai:
  #   base_url: "https://api.openai.com/v1"
  #   api_key: "${OPENAI_API_KEY}"
  #   default_model: "gpt-4o"

defaults:
  temperature: 0.3
  max_tokens: 4096

# Per-agent thinking config (Phase 5B §2.8):
#   - "disabled" pairs with cheap-summary agents (deepseek/qwen) — drops
#     reasoning_content, saves output budget.
#   - "enabled" only meaningful for Kimi K2.x; max_tokens caps the
#     reasoning_content portion (separate from completion max_tokens).
#   - keep ∈ {all, last_round, none}: which rounds' reasoning to retain
#     in subsequent turns of the same conversation (consumed by P5B-T03).
agents:
  news_crawler:
    name: "新闻爬取员"
    provider: deepseek
    model: deepseek-v4-pro
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "every_5min"
    task: "财经新闻摘要、分类、重要性评分(0-10)"

  sentiment_analyst:
    name: "情绪分析师"
    provider: deepseek
    model: deepseek-v4-pro
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "every_30min"
    task: "社交媒体情绪、论坛情感、恐慌贪婪指数"

  data_cleaner:
    name: "数据清洗员"
    provider: deepseek
    model: deepseek-v4-pro
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "realtime"
    task: "原始数据标准化、异常值标记、格式转换"

  fundamental_analyst:
    name: "基本面分析师"
    provider: qwen
    model: qwen3.6-plus
    fallback: { provider: deepseek, model: deepseek-v4-pro }
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "daily_or_event"
    task: "财报解读、PE/PB估值、行业对比"

  technical_analyst:
    name: "技术分析师"
    provider: qwen
    model: qwen3.6-plus
    fallback: { provider: deepseek, model: deepseek-v4-pro }
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "daily"
    task: "K线形态、MACD/RSI/布林带、趋势判断"

  # Kimi-using agents (Phase 5B §2.8 tiered routing):
  #
  # IMPORTANT — tiered routing only applies where the agent prompt
  # produces a JSON envelope with a top-level numeric ``confidence`` field
  # in [0, 1]. The prose-only researchers (intelligence/bull/bear/risk)
  # currently emit free-form Chinese reports — running them through a
  # ``confidence_lt`` gate would force ``parse_failed`` on every triage
  # answer and turn the supposed cost saver into a 2x cost amplifier.
  #
  # T03 therefore enables tiered routing ONLY on ``fund_manager`` (the
  # single agent whose prompt enforces a JSON contract with confidence).
  # Extending routing to the prose agents is tracked as a follow-up:
  # update their prompts to a JSON contract (and downstream parsers)
  # before flipping the YAML.
  intelligence_officer:
    name: "情报研判员（含MiroFish）"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: enabled
      max_tokens: 10000
      keep: last_round
    frequency: "event_triggered"
    task: "信息融合、隐性变量推演、驱动MiroFish仿真"

  bull_researcher:
    name: "看多研究员"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: enabled
      max_tokens: 8000
      keep: all
    frequency: "per_trading_day"
    task: "构建看多论点、寻找上涨催化剂"

  bear_researcher:
    name: "看空研究员"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: enabled
      max_tokens: 8000
      keep: all
    frequency: "per_trading_day"
    task: "构建看空论点、寻找下跌风险"

  risk_officer:
    name: "风控官"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: enabled
      max_tokens: 6000
      keep: last_round
    frequency: "per_trading_day"
    task: "投组风险评估、仓位建议、否决权"

  fund_manager:
    name: "基金经理（终局决策）"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: deepseek, model: deepseek-v4-pro }
    routing:
      triage_provider: qwen
      triage_model: qwen3.6-plus
      escalation_provider: kimi
      escalation_model: kimi-k2.6
      escalation_condition:
        confidence_lt: 0.6
    thinking:
      type: enabled
      max_tokens: 8000
      keep: last_round
    frequency: "per_trading_day"
    task: "综合所有Agent报告，输出最终买卖信号"

  # Phase 5B exit shadow-test baseline (consumed only by
  # backend.services.shadow_runner when QUANTMIND_SHADOW_ENABLED=1).
  # Identical to fund_manager EXCEPT the routing block is intentionally
  # absent — this leg always calls Kimi directly so we can compare the
  # tiered-routing decision against the original kimi-only baseline.
  fund_manager_shadow_baseline:
    name: "基金经理 (Shadow Baseline / Kimi-only)"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: deepseek, model: deepseek-v4-pro }
    thinking:
      type: enabled
      max_tokens: 8000
      keep: last_round
    frequency: "shadow_only"
    task: "Phase 5B 出口 7-day shadow window 的 baseline leg；仅在 shadow_runner 启用时被调用，永远不进入实盘决策路径"

exec
/bin/bash -lc 'git diff --stat && git diff -- backend/data/analysis_scheduler.py backend/services/shadow_runner.py config/agent_models.yaml tests/test_llm_router_thinking.py tests/test_shadow_runner.py' in /home/ps/papers/QuantMind
 succeeded in 0ms:
exec
/bin/bash -lc 'grep -R "AnalysisServices(" -n . | head -100 && grep -R "AnalysisScheduler(" -n backend tests | head -100' in /home/ps/papers/QuantMind
 succeeded in 227ms:
./docs/session-a-prompt.md:294:   analysis_services = AnalysisServices(
./tests/test_agents_graph.py:93:    return AnalysisServices(
./tests/test_analysis_scheduler_fast_slow.py:78:    return AnalysisServices(
./tests/test_mirofish_integration.py:137:    return AnalysisServices(
./tests/test_agents_nodes.py:109:    return AnalysisServices(
./backend/api/analysis.py:109:        services = AnalysisServices(
./backend/api/analysis.py:416:    return AnalysisServices(
./backend/agents/models.py:63:class AnalysisServices(BaseModel):
./backend/main.py:168:    services = AnalysisServices(
backend/main.py:192:    analysis_scheduler = AnalysisScheduler(
tests/test_analysis_scheduler.py:88:    return AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:115:    return AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:200:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:221:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:295:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:432:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:460:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:495:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_fast_slow.py:599:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:60:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:77:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:93:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:115:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:139:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:160:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_catchup.py:181:        scheduler = AnalysisScheduler(
tests/test_analysis_scheduler_budget.py:82:    return AnalysisScheduler(
tests/test_analysis_scheduler_budget.py:189:        scheduler = AnalysisScheduler(

exec
/bin/bash -lc "sed -n '130,220p' backend/main.py && sed -n '80,130p' backend/api/analysis.py && sed -n '390,440p' backend/api/analysis.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
        circuit_breaker.is_halted if circuit_breaker is not None else None
    )
    application.state.approval_queue = ApprovalQueue(
        registry, halt_check=halt_check
    )

    log.info("trading_layer_initialized")


async def _init_analysis_scheduler(application: FastAPI) -> None:
    """Initialize the daily analysis orchestrator.

    Phase 5B-T02: when ``config/watchlist_policy.yaml`` is present the
    scheduler runs in Fast/Slow mode (two cron jobs). When the file is
    missing or fails to parse we log a warning and fall back to the
    legacy single-cron mode so a typo in the YAML can't bring the
    scheduler down.
    """
    from backend.agents.models import AnalysisServices, PipelineConfig
    from backend.data.analysis_scheduler import AnalysisScheduler
    from backend.services.watchlist_policy import (
        WatchlistPolicyError,
        load_policy,
    )

    required = [
        "llm_router",
        "market_data",
        "history_data",
        "news_crawler",
        "mongodb",
        "watchlist",
    ]
    for attr in required:
        if not hasattr(application.state, attr):
            log.warning("analysis_scheduler_skip", missing=attr)
            return

    services = AnalysisServices(
        llm_router=application.state.llm_router,
        market_data=application.state.market_data,
        history_data=application.state.history_data,
        news_crawler=application.state.news_crawler,
        mongodb=application.state.mongodb,
        pipeline_config=PipelineConfig(),
    )

    policy_path = os.environ.get(
        "QUANTMIND_WATCHLIST_POLICY_PATH", "config/watchlist_policy.yaml"
    )
    policy = None
    if os.path.exists(policy_path):
        try:
            policy = load_policy(policy_path)
        except (WatchlistPolicyError, OSError) as exc:
            log.warning(
                "watchlist_policy_load_failed",
                path=policy_path,
                error=str(exc),
            )
    else:
        log.info("watchlist_policy_missing", path=policy_path)
    analysis_scheduler = AnalysisScheduler(
        watchlist=application.state.watchlist,
        services=services,
        mongodb=application.state.mongodb,
        redis_client=getattr(application.state, "redis", None),
        policy=policy,
    )
    await analysis_scheduler.start()
    # Re-read the scheduler's policy AFTER start(): a malformed cron in
    # the YAML triggers a runtime fallback that clears the policy, and
    # app.state must reflect that so the API doesn't keep accepting
    # /category mutations against a policy whose cron jobs were never
    # registered (Codex R6 HIGH #2).
    application.state.watchlist_policy = analysis_scheduler.policy
    application.state.analysis_scheduler = analysis_scheduler
    log.info(
        "analysis_scheduler_initialized",
        fast_slow_mode=analysis_scheduler.policy is not None,
    )


async def _shutdown_data_layer(application: FastAPI) -> None:
    """Shut down the data layer services."""
    if hasattr(application.state, "analysis_scheduler"):
        await application.state.analysis_scheduler.stop()
    if hasattr(application.state, "scheduler"):
        await application.state.scheduler.stop()
    if hasattr(application.state, "mongo_client"):
        application.state.mongo_client.close()

    stock_code: str
    max_debate_rounds: int = Field(default=2, ge=1, le=5)


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


@router.post("/api/analysis/stock")
async def analyze_stock(request: Request, body: AnalysisRequest) -> dict[str, Any]:
    """Run the full multi-agent analysis pipeline for a stock.

    Triggers 9 LLM agents: 5 analysts, 2 debaters, risk officer, fund manager.
    Returns a TradingSignal with action/target_price/confidence/risk_score.
    """
    if not _CODE_RE.match(body.stock_code):
        _err(f"Invalid stock code '{body.stock_code}': must be 6 digits", 422)

    _llm_preflight_or_503(request)

    try:
        services = AnalysisServices(
            llm_router=request.app.state.llm_router,
            market_data=request.app.state.market_data,
            history_data=request.app.state.history_data,
            news_crawler=request.app.state.news_crawler,
            mongodb=getattr(request.app.state, "mongodb", None),
            pipeline_config=PipelineConfig(
                max_debate_rounds=body.max_debate_rounds
            ),
        )
    except AttributeError as exc:
        log.error("services_not_initialized", error=str(exc))
        _err("Analysis services not initialized", 503)
        return _ok(None)  # unreachable

    timeout = services.pipeline_config.analysis_timeout_seconds
    mongodb = getattr(request.app.state, "mongodb", None)
    try:
        outcome = await asyncio.wait_for(
            run_analysis(body.stock_code, services),
            timeout=timeout,
        )

# -- Live analysis jobs & SSE streaming --
#
# POST /api/analysis/jobs      — creates a background run, returns job_id
# GET  /api/analysis/stream/{id} — text/event-stream subscription
#
# The two-step design is required because browsers' native EventSource can
# only send GET, so the POST-body parameters (stock_code, debate rounds)
# must be bound to a pre-allocated job before the stream opens.


class AnalysisJobRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    stock_code: str
    max_debate_rounds: int = Field(default=2, ge=1, le=5)


def _get_hub(request: Request) -> AnalysisStreamHub:
    hub = getattr(request.app.state, "analysis_stream_hub", None)
    if hub is None:
        _err("Analysis stream hub not initialized", 503)
    return hub  # type: ignore[return-value]


def _build_services(request: Request, rounds: int) -> AnalysisServices:
    return AnalysisServices(
        llm_router=request.app.state.llm_router,
        market_data=request.app.state.market_data,
        history_data=request.app.state.history_data,
        news_crawler=request.app.state.news_crawler,
        mongodb=getattr(request.app.state, "mongodb", None),
        pipeline_config=PipelineConfig(max_debate_rounds=rounds),
    )


async def _run_job(
    *,
    job_id: str,
    stock_code: str,
    services: AnalysisServices,
    hub: AnalysisStreamHub,
    mongodb: Any,
) -> None:
    """Run the pipeline for a job, pushing events to the hub."""

    async def emitter(event: dict[str, Any]) -> None:
        event.setdefault("run_id", job_id)
        # The pipeline_completed event is re-emitted below with signal_id
        # patched in, so skip the one from the collector.
        if event.get("event_type") == "pipeline_completed":

exec
/bin/bash -lc "grep -R \"all_ten\\|ten agents\\|len(.*agents\\|agents.keys\\|fund_manager_shadow_baseline\" -n tests backend | head -200" in /home/ps/papers/QuantMind
 succeeded in 0ms:
grep: tests/__pycache__/test_llm_router_thinking.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: backend/services/__pycache__/shadow_runner.cpython-312.pyc: binary file matches
tests/test_llm_router_thinking.py:334:    "fund_manager_shadow_baseline": ("enabled", 8_000, "last_round"),
tests/test_llm_router_thinking.py:350:        assert set(production_router_config.agents.keys()) == set(
backend/llm/router.py:176:                f"Available: {sorted(config.agents.keys())}"
backend/llm/router.py:541:            agents=sorted(new_config.agents.keys()),
backend/services/shadow_runner.py:5:(``fund_manager_shadow_baseline``), and writes the resulting
backend/services/shadow_runner.py:60:SHADOW_BASELINE_AGENT = "fund_manager_shadow_baseline"

exec
/bin/bash -lc "ls scripts | grep shadow && sed -n '1,320p' scripts/shadow_compare.py || true && grep -R \"shadow_decisions\\|ShadowDecision\" -n . --exclude-dir=.git --exclude='*.pyc'" in /home/ps/papers/QuantMind
 succeeded in 337ms:
./.pytest_cache/v/cache/nodeids:360:  "tests/test_database.py::TestInitialize::test_creates_shadow_decisions_indexes",
./.pytest_cache/v/cache/nodeids:1049:  "tests/test_shadow_recorder.py::TestQueryShadowDecisions::test_drops_object_id",
./.pytest_cache/v/cache/nodeids:1050:  "tests/test_shadow_recorder.py::TestQueryShadowDecisions::test_invalid_days_rejected",
./.pytest_cache/v/cache/nodeids:1051:  "tests/test_shadow_recorder.py::TestQueryShadowDecisions::test_mongo_error_returns_empty",
./.pytest_cache/v/cache/nodeids:1052:  "tests/test_shadow_recorder.py::TestQueryShadowDecisions::test_naive_now_normalised_to_utc",
./.pytest_cache/v/cache/nodeids:1053:  "tests/test_shadow_recorder.py::TestQueryShadowDecisions::test_uses_utc_cutoff",
./.pytest_cache/v/cache/nodeids:1054:  "tests/test_shadow_recorder.py::TestRecordShadowDecision::test_collection_name",
./.pytest_cache/v/cache/nodeids:1055:  "tests/test_shadow_recorder.py::TestRecordShadowDecision::test_happy_path_upserts_by_run_id",
./.pytest_cache/v/cache/nodeids:1056:  "tests/test_shadow_recorder.py::TestRecordShadowDecision::test_idempotent_second_call",
./.pytest_cache/v/cache/nodeids:1057:  "tests/test_shadow_recorder.py::TestRecordShadowDecision::test_mongo_error_returns_false",
./.pytest_cache/v/cache/nodeids:1058:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_empty_run_id_rejected",
./.pytest_cache/v/cache/nodeids:1059:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_empty_stock_code_rejected",
./.pytest_cache/v/cache/nodeids:1060:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_empty_trade_date_rejected",
./.pytest_cache/v/cache/nodeids:1061:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_happy_path",
./.pytest_cache/v/cache/nodeids:1062:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_naive_created_at_rejected",
./.pytest_cache/v/cache/nodeids:1063:  "tests/test_shadow_recorder.py::TestShadowDecisionEntry::test_to_document_round_trip",
./.pytest_cache/v/cache/nodeids:1064:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_bool_confidence_rejected",
./.pytest_cache/v/cache/nodeids:1065:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_happy_path",
./.pytest_cache/v/cache/nodeids:1066:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_inf_latency_rejected",
./.pytest_cache/v/cache/nodeids:1067:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_action_rejected[\\u4e70]",
./.pytest_cache/v/cache/nodeids:1068:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_action_rejected[\\u6301\\u4ed3]",
./.pytest_cache/v/cache/nodeids:1069:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_action_rejected[]",
./.pytest_cache/v/cache/nodeids:1070:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_action_rejected[buy]",
./.pytest_cache/v/cache/nodeids:1071:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_confidence_rejected[-0.1]",
./.pytest_cache/v/cache/nodeids:1072:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_confidence_rejected[-inf]",
./.pytest_cache/v/cache/nodeids:1073:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_confidence_rejected[1.1]",
./.pytest_cache/v/cache/nodeids:1074:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_confidence_rejected[inf]",
./.pytest_cache/v/cache/nodeids:1075:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_invalid_confidence_rejected[nan]",
./.pytest_cache/v/cache/nodeids:1076:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_negative_latency_rejected",
./.pytest_cache/v/cache/nodeids:1077:  "tests/test_shadow_recorder.py::TestShadowDecisionLeg::test_zero_latency_accepted",
./scripts/phase5b_exit_check.py:9:* shadow consistency (from MongoDB shadow_decisions)
./scripts/phase5b_exit_check.py:41:_MAX_DAYS = 30  # cap matches shadow_decisions TTL retention
./scripts/phase5b_exit_check.py:120:    from backend.services.shadow_recorder import query_shadow_decisions
./scripts/phase5b_exit_check.py:164:        shadow_docs = await query_shadow_decisions(service, days=args.days)
./scripts/shadow_compare.py:4:Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
./scripts/shadow_compare.py:14:    # File replay (operator-collected JSONL of shadow_decisions docs)
./scripts/shadow_compare.py:69:        help="JSONL file of shadow_decisions documents. Mutually "
./scripts/shadow_compare.py:125:    """Connect to MongoDB and pull shadow_decisions for the given window.
./scripts/shadow_compare.py:133:    from backend.services.shadow_recorder import query_shadow_decisions
./scripts/shadow_compare.py:139:        docs = await query_shadow_decisions(service, days=args.days)
./docs/phase5-eval-and-phase6-prep-master-plan.md:978:  - `backend/services/shadow_recorder.py` (100% cov) — 纯数据层 `ShadowDecisionEntry/Leg` schema + `record_shadow_decision` + `query_shadow_decisions`(UTC cutoff,fail-soft)
./docs/phase5-eval-and-phase6-prep-master-plan.md:983:  - `backend/data/database.py` — `shadow_decisions` 集合 unique(run_id) 索引 + TTL(created_at, 30 天)索引
./docs/phase5-eval-and-phase6-prep-master-plan.md:1798:| 2026-05-02 | claude-opus-4-7-1m | 12bac5b | Phase 5B 出口 marker ⏳→🔧:harness 全部就位(`backend/services/{shadow_recorder,shadow_compare,phase5b_exit_check}.py` + `scripts/{shadow_compare,phase5b_exit_check}.py` + `shadow_decisions` Mongo 集合 unique(run_id) + TTL(created_at,30d) 索引);117 个新测试覆盖 schema validation / pure compute / CLI mocked-IO / 索引契约;1077 pytest passed(11 skipped,+109 net 自 P5B-T03 968),backend 覆盖率 83% / risk 98%;7 轮 codex review(R1 architecture / R2 followup / R3 perf / R4 testing / R5 security / R6+R7 final verify)→ 15 个发现全部 RESOLVED,无回归;7 项出口指标 4 项工具就位(cost x3 + latency x2 + shadow consistency x2),真值待 deployment 部署窗口 + operator 启用 shadow_recorder cron 后 7 天采集 + `phase5b_exit_check.py --strict` 输出最终判定;summary 报告 `docs/reviews/phase5b-summary-2026-05-15.md` 生成;11 项 cross-cutting backlog 列入 summary §6;**STOP 等部署窗口 + Phase 5C 授权,不自动跨阶段** |
./docs/reviews/p5b-exit-r1-architecture.md:422:+:class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
./docs/reviews/p5b-exit-r1-architecture.md:517:+    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.
./docs/reviews/p5b-exit-r1-architecture.md:734:+the immutable ``ShadowDecisionEntry`` schema and the read/write API
./docs/reviews/p5b-exit-r1-architecture.md:735:+against the ``shadow_decisions`` MongoDB collection. The companion CLI
./docs/reviews/p5b-exit-r1-architecture.md:773:+SHADOW_COLLECTION = "shadow_decisions"
./docs/reviews/p5b-exit-r1-architecture.md:779:+class ShadowDecisionLeg:
./docs/reviews/p5b-exit-r1-architecture.md:825:+class ShadowDecisionEntry:
./docs/reviews/p5b-exit-r1-architecture.md:836:+    baseline: ShadowDecisionLeg
./docs/reviews/p5b-exit-r1-architecture.md:837:+    routed: ShadowDecisionLeg
./docs/reviews/p5b-exit-r1-architecture.md:871:+    entry: ShadowDecisionEntry,
./docs/reviews/p5b-exit-r1-architecture.md:873:+    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.
./docs/reviews/p5b-exit-r1-architecture.md:899:+async def query_shadow_decisions(
./docs/reviews/p5b-exit-r1-architecture.md:905:+    """Return shadow_decisions documents for the last ``days`` days.
./docs/reviews/p5b-exit-r1-architecture.md:943:+    "ShadowDecisionEntry",
./docs/reviews/p5b-exit-r1-architecture.md:944:+    "ShadowDecisionLeg",
./docs/reviews/p5b-exit-r1-architecture.md:945:+    "query_shadow_decisions",
./docs/reviews/p5b-exit-r1-architecture.md:962:+* shadow consistency (from MongoDB shadow_decisions)
./docs/reviews/p5b-exit-r1-architecture.md:1049:+    from backend.services.shadow_recorder import query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:1062:+        shadow_docs = await query_shadow_decisions(service, days=args.days)
./docs/reviews/p5b-exit-r1-architecture.md:1115:+Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
./docs/reviews/p5b-exit-r1-architecture.md:1125:+    # File replay (operator-collected JSONL of shadow_decisions docs)
./docs/reviews/p5b-exit-r1-architecture.md:1162:+        help="JSONL file of shadow_decisions documents. Mutually "
./docs/reviews/p5b-exit-r1-architecture.md:1212:+    """Connect to MongoDB and pull shadow_decisions for the given window.
./docs/reviews/p5b-exit-r1-architecture.md:1220:+    from backend.services.shadow_recorder import query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:1226:+        docs = await query_shadow_decisions(service, days=args.days)
./docs/reviews/p5b-exit-r1-architecture.md:2072:+* ShadowDecisionLeg / ShadowDecisionEntry validation (action, confidence,
./docs/reviews/p5b-exit-r1-architecture.md:2075:+* query_shadow_decisions UTC cutoff, _id stripping, error fail-soft
./docs/reviews/p5b-exit-r1-architecture.md:2088:+    ShadowDecisionEntry,
./docs/reviews/p5b-exit-r1-architecture.md:2089:+    ShadowDecisionLeg,
./docs/reviews/p5b-exit-r1-architecture.md:2090:+    query_shadow_decisions,
./docs/reviews/p5b-exit-r1-architecture.md:2107:+) -> ShadowDecisionLeg:
./docs/reviews/p5b-exit-r1-architecture.md:2108:+    return ShadowDecisionLeg(
./docs/reviews/p5b-exit-r1-architecture.md:2123:+    baseline: ShadowDecisionLeg | None = None,
./docs/reviews/p5b-exit-r1-architecture.md:2124:+    routed: ShadowDecisionLeg | None = None,
./docs/reviews/p5b-exit-r1-architecture.md:2125:+) -> ShadowDecisionEntry:
./docs/reviews/p5b-exit-r1-architecture.md:2126:+    return ShadowDecisionEntry(
./docs/reviews/p5b-exit-r1-architecture.md:2159:+class TestShadowDecisionLeg:
./docs/reviews/p5b-exit-r1-architecture.md:2198:+class TestShadowDecisionEntry:
./docs/reviews/p5b-exit-r1-architecture.md:2235:+class TestRecordShadowDecision:
./docs/reviews/p5b-exit-r1-architecture.md:2272:+# Group 3: query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:2292:+class TestQueryShadowDecisions:
./docs/reviews/p5b-exit-r1-architecture.md:2296:+            await query_shadow_decisions(service, days=0)
./docs/reviews/p5b-exit-r1-architecture.md:2312:+        docs = await query_shadow_decisions(service, days=7)
./docs/reviews/p5b-exit-r1-architecture.md:2321:+        await query_shadow_decisions(service, days=7, now=now)
./docs/reviews/p5b-exit-r1-architecture.md:2337:+        await query_shadow_decisions(service, days=7, now=now)
./docs/reviews/p5b-exit-r1-architecture.md:2347:+        docs = await query_shadow_decisions(service, days=1)
./docs/reviews/p5b-exit-r1-architecture.md:2360::class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
./docs/reviews/p5b-exit-r1-architecture.md:2455:    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.
./docs/reviews/p5b-exit-r1-architecture.md:2674:the immutable ``ShadowDecisionEntry`` schema and the read/write API
./docs/reviews/p5b-exit-r1-architecture.md:2675:against the ``shadow_decisions`` MongoDB collection. The companion CLI
./docs/reviews/p5b-exit-r1-architecture.md:2713:SHADOW_COLLECTION = "shadow_decisions"
./docs/reviews/p5b-exit-r1-architecture.md:2719:class ShadowDecisionLeg:
./docs/reviews/p5b-exit-r1-architecture.md:2765:class ShadowDecisionEntry:
./docs/reviews/p5b-exit-r1-architecture.md:2776:    baseline: ShadowDecisionLeg
./docs/reviews/p5b-exit-r1-architecture.md:2777:    routed: ShadowDecisionLeg
./docs/reviews/p5b-exit-r1-architecture.md:2811:    entry: ShadowDecisionEntry,
./docs/reviews/p5b-exit-r1-architecture.md:2813:    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.
./docs/reviews/p5b-exit-r1-architecture.md:2839:async def query_shadow_decisions(
./docs/reviews/p5b-exit-r1-architecture.md:2845:    """Return shadow_decisions documents for the last ``days`` days.
./docs/reviews/p5b-exit-r1-architecture.md:2883:    "ShadowDecisionEntry",
./docs/reviews/p5b-exit-r1-architecture.md:2884:    "ShadowDecisionLeg",
./docs/reviews/p5b-exit-r1-architecture.md:2885:    "query_shadow_decisions",
./docs/reviews/p5b-exit-r1-architecture.md:2904:* shadow consistency (from MongoDB shadow_decisions)
./docs/reviews/p5b-exit-r1-architecture.md:2991:    from backend.services.shadow_recorder import query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:3004:        shadow_docs = await query_shadow_decisions(service, days=args.days)
./docs/reviews/p5b-exit-r1-architecture.md:3053:Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
./docs/reviews/p5b-exit-r1-architecture.md:3063:    # File replay (operator-collected JSONL of shadow_decisions docs)
./docs/reviews/p5b-exit-r1-architecture.md:3100:        help="JSONL file of shadow_decisions documents. Mutually "
./docs/reviews/p5b-exit-r1-architecture.md:3150:    """Connect to MongoDB and pull shadow_decisions for the given window.
./docs/reviews/p5b-exit-r1-architecture.md:3158:    from backend.services.shadow_recorder import query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:3164:        docs = await query_shadow_decisions(service, days=args.days)
./docs/reviews/p5b-exit-r1-architecture.md:5809:    96	    from backend.services.shadow_recorder import query_shadow_decisions
./docs/reviews/p5b-exit-r1-architecture.md:5822:   109	        shadow_docs = await query_shadow_decisions(service, days=args.days)
./docs/reviews/p5b-exit-r4-testing.md:5:| 2 | [P3-R3] `shadow_decisions` missing indexes | Fixed in code, test gap | Indexes are present in [backend/data/database.py](/home/ps/papers/QuantMind/backend/data/database.py:155), but current DB tests only assert generic index creation. |
./docs/reviews/p5b-exit-r4-testing.md:13:Fix: Add a mocked `_gather_inputs()`/fake cursor test asserting `find(filter, projection)`, `.sort("created_at", -1)`, `aggregate_costs(..., days=args.days)`, `query_shadow_decisions(..., days=args.days)`, client cleanup, and strict exit codes.
./docs/reviews/p5b-exit-r4-testing.md:21:[LOW] `shadow_decisions` index regression is not locked  
./docs/reviews/p5b-exit-r4-testing.md:25:Fix: Add a collection-specific mock for `shadow_decisions` and assert both exact `create_index` calls.
./docs/reviews/p5b-exit-r3-perf.md:16:[P3] `shadow_decisions` reads/writes are unindexed  
./docs/reviews/p5b-exit-r3-perf.md:19:Issue: `record_shadow_decision()` upserts by `run_id`, and `query_shadow_decisions()` filters by `created_at`, but no `shadow_decisions` indexes are added in `MongoDBService.initialize()`. At the stated 7-30 day volumes this is not painful, but if shadow data is retained for months, every upsert/query trends toward collection scans.  
./docs/reviews/p5b-exit-r3-perf.md:20:Fix: create indexes for `shadow_decisions`: unique `run_id`, plus `created_at` for the lookback query. If retention should be bounded, make `created_at` a TTL index.
./docs/reviews/p5b-exit-r3-perf.md:28:| `query_shadow_decisions` iterator | Iterator vs `to_list()` is not the issue; missing indexes are. |
./docs/reviews/p5b-exit-codex-summary.md:3:**Scope**: backend/services/{shadow_recorder,shadow_compare,phase5b_exit_check}.py + scripts/{shadow_compare,phase5b_exit_check}.py + tests/{test_shadow_recorder,test_shadow_compare,test_phase5b_exit_check,test_scripts_*}.py + backend/data/database.py shadow_decisions index spec.
./docs/reviews/p5b-exit-codex-summary.md:33:| 9 | R3 | P3 | shadow_decisions no indexes | database.py |
./docs/reviews/p5b-exit-codex-summary.md:37:| 13 | R5 | MED | shadow_decisions no TTL | database.py |
./docs/reviews/phase5b-summary-2026-05-15.md:54:**单元/集成增量**: 116 个新测试覆盖 shadow_recorder + shadow_compare + phase5b_exit_check + 两个 CLI + 数据库索引契约。`test_creates_shadow_decisions_indexes` 锁定 unique(run_id) + TTL(created_at, 30d) 两条索引;`TestExitCheckCLILiveInputs` 用 mock motor + Redis 验证 ``$or`` 窗口过滤 + 5-字段投影 + 资源关闭。
./docs/reviews/phase5b-summary-2026-05-15.md:63:- **抉择**: A) 把 baseline + routed 双调用塞进 `fund_manager_node`;B) 提供一个独立的 `shadow_decisions` 集合 + 公共录制 API,operator 用单独后台任务消费 baseline 路径,实时分析仍走 routing 路径。
./docs/reviews/phase5b-summary-2026-05-15.md:65:- **代价**: 7-day shadow 真值需要 deployment 后由 operator 单独写 `shadow_decisions` 入 Mongo。已在 backlog 列出"Phase 5C 部署任务: shadow recorder cron wiring"。
./docs/reviews/phase5b-summary-2026-05-15.md:97:| `shadow_decisions` TTL 30d 下短期回滚后无法回看历史 | LOW | 部署初期可改 `_TTL_DAYS_DEFAULT` 临时延长;commit 已用单一常量 |
./docs/reviews/phase5b-summary-2026-05-15.md:99:| Mongo 上线前未跑 `MongoDBService.initialize()` → 索引缺失 | MEDIUM | main lifespan 已在 P5A-T03 阶段强制调用 initialize;新加的 shadow_decisions 索引接入相同生命周期 |
./docs/reviews/phase5b-summary-2026-05-15.md:117:1. **shadow_recorder cron wiring**: backend/services/shadow_recorder.py 已是纯数据层,需要部署后接 cron 触发 baseline 调用 + 写 `shadow_decisions`。建议作为 P5C-T0X 单独 task(包含 sampling rate 配置 + 失败重试 + cost-guard 双重保护)。
./docs/reviews/phase5b-summary-2026-05-15.md:144:    ShadowDecisionEntry, ShadowDecisionLeg, record_shadow_decision,
./docs/reviews/phase5b-summary-2026-05-15.md:149:# 3. 组装 ShadowDecisionEntry 并 record_shadow_decision(mongodb, entry)
./docs/reviews/phase5b-summary-2026-05-15.md:175:CLI 自动从 `MONGODB_URI` / `MONGODB_DB` / `REDIS_URL` 取连接字符串(env 优先,argv fallback)。`--days` 被 clamp 到 [1, 30],配合 shadow_decisions TTL 30 天构成完整的 retention 边界。
./docs/reviews/phase5b-summary-2026-05-15.md:197:- `backend/data/database.py`(`shadow_decisions` 索引 + TTL)
./docs/reviews/phase5b-summary-2026-05-15.md:209:- `tests/test_database.py`(+1 case for shadow_decisions indexes)
./docs/reviews/p5b-exit-r5-security.md:7:| 3 | R4 `shadow_decisions` indexes | VERIFIED | `backend/data/database.py:155` creates unique `run_id` and `created_at` indexes; TTL still missing, see below. |
./docs/reviews/p5b-exit-r5-security.md:13:| 9 | Data retention | NEEDS_FIX | `shadow_decisions` has no TTL despite storing sensitive decision telemetry. |
./docs/reviews/p5b-exit-r5-security.md:18:[MEDIUM] `shadow_decisions` retains sensitive decision telemetry indefinitely  
./docs/reviews/p5b-exit-r5-security.md:21:Issue: `shadow_decisions` stores stock codes, actions, confidences, model names, and timestamps, but only creates `run_id` and `created_at` indexes. `_TTL_DAYS_DEFAULT = 30` exists in `backend/services/shadow_recorder.py:44` but is unused, so data accumulates forever.  
./docs/reviews/p5b-exit-r5-security.md:27:Issue: `--days` has no upper bound, `analysis_records` and `shadow_decisions` cursors are fully materialized, Redis is scanned for every requested day, and `scripts/shadow_compare.py:85` reads the whole JSONL input into memory. A bad or compromised invocation can overload Mongo/Redis or OOM the runner.  
./docs/reviews/p5b-exit-r6-final-verify.md:12:| 9 | `shadow_decisions` had no indexes | RESOLVED | `run_id` unique index and `created_at` index are created. |
./docs/reviews/p5b-exit-r6-final-verify.md:15:| 12 | `shadow_decisions` index regression not locked | RESOLVED | Test asserts both required indexes, including TTL. |
./docs/reviews/p5b-exit-r6-final-verify.md:16:| 13 | `shadow_decisions` retained indefinitely | RESOLVED | `created_at` index has `expireAfterSeconds=30*86400`. |
./tests/test_shadow_runner.py:31:    ShadowDecisionEntry,
./tests/test_shadow_runner.py:435:        recorded: list[ShadowDecisionEntry] = []
./tests/test_shadow_runner.py:437:        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
./tests/test_shadow_runner.py:492:        recorded: list[ShadowDecisionEntry] = []
./tests/test_shadow_runner.py:494:        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
./tests/test_database.py:121:    async def test_creates_shadow_decisions_indexes(
./tests/test_database.py:126:        # silently degrading shadow_decisions reads/writes to scans.
./tests/test_database.py:128:        coll = mock_db["shadow_decisions"]
./tests/test_database.py:134:        ), "shadow_decisions.run_id unique index missing"
./tests/test_database.py:144:        assert ttl_calls, "shadow_decisions.created_at TTL index missing"
./tests/test_shadow_recorder.py:4:* ShadowDecisionLeg / ShadowDecisionEntry validation (action, confidence,
./tests/test_shadow_recorder.py:7:* query_shadow_decisions UTC cutoff, _id stripping, error fail-soft
./tests/test_shadow_recorder.py:20:    ShadowDecisionEntry,
./tests/test_shadow_recorder.py:21:    ShadowDecisionLeg,
./tests/test_shadow_recorder.py:22:    query_shadow_decisions,
./tests/test_shadow_recorder.py:39:) -> ShadowDecisionLeg:
./tests/test_shadow_recorder.py:40:    return ShadowDecisionLeg(
./tests/test_shadow_recorder.py:55:    baseline: ShadowDecisionLeg | None = None,
./tests/test_shadow_recorder.py:56:    routed: ShadowDecisionLeg | None = None,
./tests/test_shadow_recorder.py:57:) -> ShadowDecisionEntry:
./tests/test_shadow_recorder.py:58:    return ShadowDecisionEntry(
./tests/test_shadow_recorder.py:91:class TestShadowDecisionLeg:
./tests/test_shadow_recorder.py:130:class TestShadowDecisionEntry:
./tests/test_shadow_recorder.py:167:class TestRecordShadowDecision:
./tests/test_shadow_recorder.py:204:# Group 3: query_shadow_decisions
./tests/test_shadow_recorder.py:224:class TestQueryShadowDecisions:
./tests/test_shadow_recorder.py:228:            await query_shadow_decisions(service, days=0)
./tests/test_shadow_recorder.py:244:        docs = await query_shadow_decisions(service, days=7)
./tests/test_shadow_recorder.py:253:        await query_shadow_decisions(service, days=7, now=now)
./tests/test_shadow_recorder.py:269:        await query_shadow_decisions(service, days=7, now=now)
./tests/test_shadow_recorder.py:279:        docs = await query_shadow_decisions(service, days=1)
./tests/test_scripts_phase5b_exit_check.py:121:        # aggregate_costs and query_shadow_decisions are imported inside
./tests/test_scripts_phase5b_exit_check.py:153:                "backend.services.shadow_recorder.query_shadow_decisions",
./backend/data/database.py:155:        shadow_decisions = self._db["shadow_decisions"]
./backend/data/database.py:156:        await shadow_decisions.create_index(
./backend/data/database.py:161:        # TTL on created_at — shadow_decisions hold per-stock action /
./backend/data/database.py:168:        await shadow_decisions.create_index(
./backend/services/shadow_runner.py:6:``(baseline, routed)`` pair to the ``shadow_decisions`` collection so
./backend/services/shadow_runner.py:47:    ShadowDecisionEntry,
./backend/services/shadow_runner.py:48:    ShadowDecisionLeg,
./backend/services/shadow_runner.py:199:def _routed_leg_from_record(record: AnalysisRecord) -> ShadowDecisionLeg | None:
./backend/services/shadow_runner.py:213:        return ShadowDecisionLeg(
./backend/services/shadow_runner.py:322:        baseline_leg = ShadowDecisionLeg(
./backend/services/shadow_runner.py:339:        entry = ShadowDecisionEntry(
./backend/services/shadow_compare.py:8::class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
./backend/services/shadow_compare.py:149:    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.
./backend/services/shadow_recorder.py:4:the immutable ``ShadowDecisionEntry`` schema and the read/write API
./backend/services/shadow_recorder.py:5:against the ``shadow_decisions`` MongoDB collection. The companion CLI
./backend/services/shadow_recorder.py:43:SHADOW_COLLECTION = "shadow_decisions"
./backend/services/shadow_recorder.py:49:class ShadowDecisionLeg:
./backend/services/shadow_recorder.py:95:class ShadowDecisionEntry:
./backend/services/shadow_recorder.py:106:    baseline: ShadowDecisionLeg
./backend/services/shadow_recorder.py:107:    routed: ShadowDecisionLeg
./backend/services/shadow_recorder.py:141:    entry: ShadowDecisionEntry,
./backend/services/shadow_recorder.py:143:    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.
./backend/services/shadow_recorder.py:169:async def query_shadow_decisions(
./backend/services/shadow_recorder.py:175:    """Return shadow_decisions documents for the last ``days`` days.
./backend/services/shadow_recorder.py:213:    "ShadowDecisionEntry",
./backend/services/shadow_recorder.py:214:    "ShadowDecisionLeg",
./backend/services/shadow_recorder.py:215:    "query_shadow_decisions",

exec
/bin/bash -lc "sed -n '1,280p' backend/services/shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Pure analysis layer for the Phase 5B shadow-test harness.

``scripts/shadow_compare.py`` is a thin CLI on top of this module — all
the math + threshold gating lives here so it is unit-testable without a
running Mongo and without ``argparse`` ceremony.

Inputs are plain dicts shaped like the documents produced by
:class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
malformed (missing keys, wrong types) is dropped and counted in a
``skipped`` bucket so the consumer can see whether the harness actually
saw clean data.

The thresholds match SSoT §6 P5B-T03 pass criteria:

* action consistency ≥ 0.85
* mean absolute confidence delta < 0.15
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

ACTION_MATCH_THRESHOLD = 0.85
CONFIDENCE_DELTA_THRESHOLD = 0.15

# Strict ``YYYY-MM-DD`` shape — anything else (pipes, newlines, control
# chars from a malicious shadow doc) gets the row dropped before it
# reaches the markdown renderer (codex P5B-exit R5 LOW: report
# injection). We don't validate the calendar (Feb 30 etc.); the gate
# math doesn't care, and a bogus-but-shaped date just clusters by its
# own bucket.
_TRADE_DATE_RE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Mirrors backend.services.shadow_recorder._VALID_ACTIONS — duplicated
# rather than imported because the analyser must cope with documents
# the recorder may not have produced (offline JSONL replay, future
# schema migrations).
_VALID_LEG_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})


@dataclass(frozen=True)
class LegMetrics:
    """Per-leg counters lifted from the ``baseline`` / ``routed`` arms."""

    parse_ok_rate: float
    escalation_rate: float
    avg_latency_ms: float


@dataclass(frozen=True)
class ShadowReport:
    """Output of :func:`compute_shadow_report`. All fields are immutable.

    ``passes`` is the gate result against SSoT §6 P5B-T03 thresholds.
    Tooling that wants to print but not gate (e.g. mid-window trend
    inspection) can ignore the field; the exit-check CLI uses it.
    """

    total_pairs: int
    skipped: int
    action_match_rate: float
    confidence_delta_p50: float
    confidence_delta_p95: float
    confidence_delta_mean_abs: float
    baseline: LegMetrics
    routed: LegMetrics
    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    passes: dict[str, bool] = field(default_factory=dict)


def _coerce_leg(raw: Any) -> dict[str, Any] | None:
    """Return a leg dict only if every required field is present and typed.

    Mongo deserialises documents loosely (an int may arrive where a float
    was written), so we coerce numeric fields. Action and the two boolean
    flags are validated strictly: a free-form ``str(action)`` would let a
    rogue / legacy doc with action ``"buy"`` be counted as valid, and a
    truthy-string ``"false"`` for ``parse_ok`` would silently flip to
    ``True`` (codex P5B-exit R4 MED). Anything off-contract drops the
    leg, which propagates to the ``skipped`` counter upstream.
    """
    if not isinstance(raw, Mapping):
        return None
    required = ("action", "confidence", "model", "parse_ok", "escalated")
    if not all(k in raw for k in required):
        return None

    action = raw["action"]
    if not isinstance(action, str) or action not in _VALID_LEG_ACTIONS:
        return None

    parse_ok = raw["parse_ok"]
    escalated = raw["escalated"]
    if not isinstance(parse_ok, bool) or not isinstance(escalated, bool):
        return None

    confidence = raw["confidence"]
    # Pre-empt the ``bool`` ⊂ ``int`` Python quirk: True/False would
    # otherwise pass the float coercion check unchanged.
    if isinstance(confidence, bool) or not isinstance(
        confidence, (int, float)
    ):
        return None

    model = raw["model"]
    if not isinstance(model, str) or not model:
        return None

    latency_raw = raw.get("latency_ms", 0.0)
    if isinstance(latency_raw, bool) or not isinstance(
        latency_raw, (int, float)
    ):
        return None

    try:
        return {
            "action": action,
            "confidence": float(confidence),
            "model": model,
            "latency_ms": float(latency_raw),
            "parse_ok": parse_ok,
            "escalated": escalated,
        }
    except (TypeError, ValueError):
        return None


def _is_clean_pair(pair: dict[str, Any]) -> bool:
    base = pair["baseline"]
    routed = pair["routed"]
    for leg in (base, routed):
        if not math.isfinite(leg["confidence"]):
            return False
        if leg["confidence"] < 0.0 or leg["confidence"] > 1.0:
            return False
        if not math.isfinite(leg["latency_ms"]) or leg["latency_ms"] < 0.0:
            return False
    return True


def compute_shadow_report(
    docs: Iterable[Mapping[str, Any]],
) -> ShadowReport:
    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.

    Empty / dirty input does not raise: the report shows ``total_pairs=0``
    and ``passes`` populated with ``False`` so downstream automation
    (e.g. CI) can treat "no data" as a hard fail rather than a silent
    pass.
    """
    pairs: list[dict[str, Any]] = []
    skipped = 0
    by_day_counts: dict[str, dict[str, int]] = {}

    for raw in docs:
        if not isinstance(raw, Mapping):
            skipped += 1
            continue
        baseline = _coerce_leg(raw.get("baseline"))
        routed = _coerce_leg(raw.get("routed"))
        if baseline is None or routed is None:
            skipped += 1
            continue

        pair = {"baseline": baseline, "routed": routed}
        if not _is_clean_pair(pair):
            skipped += 1
            continue

        trade_date = raw.get("trade_date")
        if not isinstance(trade_date, str) or not _TRADE_DATE_RE.match(
            trade_date
        ):
            # Non-conformant trade_date is dropped to avoid markdown
            # injection in the per-day breakdown (codex P5B-exit R5 LOW).
            skipped += 1
            continue

        pair["trade_date"] = trade_date
        pairs.append(pair)

        slot = by_day_counts.setdefault(
            trade_date, {"matched": 0, "total": 0}
        )
        slot["total"] += 1
        if baseline["action"] == routed["action"]:
            slot["matched"] += 1

    if not pairs:
        empty_leg = LegMetrics(
            parse_ok_rate=0.0,
            escalation_rate=0.0,
            avg_latency_ms=0.0,
        )
        return ShadowReport(
            total_pairs=0,
            skipped=skipped,
            action_match_rate=0.0,
            confidence_delta_p50=0.0,
            confidence_delta_p95=0.0,
            confidence_delta_mean_abs=0.0,
            baseline=empty_leg,
            routed=empty_leg,
            by_day={},
            passes={
                "action_match": False,
                "confidence_delta": False,
                "has_data": False,
            },
        )

    matched = sum(
        1
        for p in pairs
        if p["baseline"]["action"] == p["routed"]["action"]
    )
    total = len(pairs)
    deltas = [
        p["routed"]["confidence"] - p["baseline"]["confidence"]
        for p in pairs
    ]
    abs_deltas = [abs(d) for d in deltas]

    by_day = {
        day: {
            "match_rate": round(slot["matched"] / slot["total"], 4),
            "samples": slot["total"],
        }
        for day, slot in sorted(by_day_counts.items())
    }

    baseline_metrics = _leg_metrics([p["baseline"] for p in pairs])
    routed_metrics = _leg_metrics([p["routed"] for p in pairs])

    action_match_rate = matched / total
    confidence_delta_mean_abs = sum(abs_deltas) / total
    p50 = statistics.median(deltas)
    p95 = _percentile(deltas, 95)

    passes = {
        "has_data": True,
        "action_match": action_match_rate >= ACTION_MATCH_THRESHOLD,
        "confidence_delta": (
            confidence_delta_mean_abs < CONFIDENCE_DELTA_THRESHOLD
        ),
    }

    return ShadowReport(
        total_pairs=total,
        skipped=skipped,
        action_match_rate=round(action_match_rate, 4),
        confidence_delta_p50=round(p50, 4),
        confidence_delta_p95=round(p95, 4),
        confidence_delta_mean_abs=round(confidence_delta_mean_abs, 4),
        baseline=baseline_metrics,
        routed=routed_metrics,
        by_day=by_day,
        passes=passes,
    )


def _leg_metrics(legs: list[dict[str, Any]]) -> LegMetrics:
    n = len(legs)
    if n == 0:
        return LegMetrics(
            parse_ok_rate=0.0, escalation_rate=0.0, avg_latency_ms=0.0
        )
    parse_ok = sum(1 for leg in legs if leg["parse_ok"])
    escalated = sum(1 for leg in legs if leg["escalated"])
    latency = sum(leg["latency_ms"] for leg in legs) / n
    return LegMetrics(
        parse_ok_rate=round(parse_ok / n, 4),
        escalation_rate=round(escalated / n, 4),
        avg_latency_ms=round(latency, 2),
    )

exec
/bin/bash -lc 'grep -R "parse_ok" -n tests/test_shadow_compare.py tests/test_shadow_recorder.py tests | head -100' in /home/ps/papers/QuantMind
 succeeded in 0ms:
grep: tests/__pycache__/test_shadow_compare.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_phase5b_exit_check.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_scripts_shadow_compare.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_shadow_recorder.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_shadow_runner.cpython-312-pytest-8.4.1.pyc: binary file matches
tests/test_shadow_compare.py:31:    base_parse_ok: bool = True,
tests/test_shadow_compare.py:32:    routed_parse_ok: bool = True,
tests/test_shadow_compare.py:46:            "parse_ok": base_parse_ok,
tests/test_shadow_compare.py:54:            "parse_ok": routed_parse_ok,
tests/test_shadow_compare.py:111:    def test_skips_non_bool_parse_ok(self, bad_value) -> None:  # noqa: ANN001
tests/test_shadow_compare.py:113:        doc["baseline"]["parse_ok"] = bad_value
tests/test_shadow_compare.py:266:    def test_parse_ok_aggregation(self) -> None:
tests/test_shadow_compare.py:267:        docs = [_doc(routed_parse_ok=False) for _ in range(2)]
tests/test_shadow_compare.py:270:        assert report.routed.parse_ok_rate == 0.8
tests/test_shadow_compare.py:271:        assert report.baseline.parse_ok_rate == 1.0
tests/test_shadow_recorder.py:38:    parse_ok: bool = True,
tests/test_shadow_recorder.py:46:        parse_ok=parse_ok,
tests/test_shadow_compare.py:31:    base_parse_ok: bool = True,
tests/test_shadow_compare.py:32:    routed_parse_ok: bool = True,
tests/test_shadow_compare.py:46:            "parse_ok": base_parse_ok,
tests/test_shadow_compare.py:54:            "parse_ok": routed_parse_ok,
tests/test_shadow_compare.py:111:    def test_skips_non_bool_parse_ok(self, bad_value) -> None:  # noqa: ANN001
tests/test_shadow_compare.py:113:        doc["baseline"]["parse_ok"] = bad_value
tests/test_shadow_compare.py:266:    def test_parse_ok_aggregation(self) -> None:
tests/test_shadow_compare.py:267:        docs = [_doc(routed_parse_ok=False) for _ in range(2)]
tests/test_shadow_compare.py:270:        assert report.routed.parse_ok_rate == 0.8
tests/test_shadow_compare.py:271:        assert report.baseline.parse_ok_rate == 1.0
tests/test_shadow_runner.py:459:        assert entry.baseline.parse_ok is True
tests/test_shadow_runner.py:485:        # must land so shadow_compare can surface parse_ok rate.
tests/test_shadow_runner.py:509:        assert recorded[0].baseline.parse_ok is False
tests/test_shadow_recorder.py:38:    parse_ok: bool = True,
tests/test_shadow_recorder.py:46:        parse_ok=parse_ok,
tests/test_phase5b_exit_check.py:323:                    "parse_ok": True,
tests/test_phase5b_exit_check.py:331:                    "parse_ok": True,
tests/test_scripts_shadow_compare.py:46:            "parse_ok": True,
tests/test_scripts_shadow_compare.py:54:            "parse_ok": True,

exec
/bin/bash -lc "sed -n '240,285p' tests/test_shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
    def test_p50_p95(self) -> None:
        deltas = [0.0, 0.1, 0.2, 0.3, 0.4]
        docs = [
            _doc(base_conf=0.5, routed_conf=0.5 + d)
            for d in deltas
        ]
        report = compute_shadow_report(docs)
        # median = 0.2 ; p95 ≈ 0.38.
        assert report.confidence_delta_p50 == 0.2
        assert 0.37 < report.confidence_delta_p95 <= 0.4


# ----------------------------------------------------------------------
# Group 4: leg metrics
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestLegMetrics:
    def test_baseline_escalation_rate_zero(self) -> None:
        report = compute_shadow_report(
            [_doc(routed_escalated=True) for _ in range(2)]
        )
        assert report.baseline.escalation_rate == 0.0
        assert report.routed.escalation_rate == 1.0

    def test_parse_ok_aggregation(self) -> None:
        docs = [_doc(routed_parse_ok=False) for _ in range(2)]
        docs += [_doc() for _ in range(8)]
        report = compute_shadow_report(docs)
        assert report.routed.parse_ok_rate == 0.8
        assert report.baseline.parse_ok_rate == 1.0

    def test_avg_latency(self) -> None:
        docs = [
            _doc(base_latency=1000.0, routed_latency=2000.0),
            _doc(base_latency=2000.0, routed_latency=4000.0),
        ]
        report = compute_shadow_report(docs)
        assert report.baseline.avg_latency_ms == 1500.0
        assert report.routed.avg_latency_ms == 3000.0


# ----------------------------------------------------------------------
# Group 5: markdown rendering
# ----------------------------------------------------------------------

exec
/bin/bash -lc "sed -n '300,370p' tests/test_llm_router_thinking.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
        assert "thinking" not in kw
        assert "extra_body" not in kw

    def test_thinking_dropped_for_non_k2_kimi_model(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="moonshot-v1-8k",
            base_kwargs={"temperature": 0.3},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        assert "thinking" not in kw
        assert "extra_body" not in kw


# ============================================================
# Group 4: Real config round-trip (integration)
# ============================================================


_PROD_THINKING_TABLE: dict[str, tuple[str, int, str]] = {
    "news_crawler": ("disabled", 0, "none"),
    "sentiment_analyst": ("disabled", 0, "none"),
    "data_cleaner": ("disabled", 0, "none"),
    "fundamental_analyst": ("disabled", 0, "none"),
    "technical_analyst": ("disabled", 0, "none"),
    "intelligence_officer": ("enabled", 10_000, "last_round"),
    "bull_researcher": ("enabled", 8_000, "all"),
    "bear_researcher": ("enabled", 8_000, "all"),
    "risk_officer": ("enabled", 6_000, "last_round"),
    "fund_manager": ("enabled", 8_000, "last_round"),
    # Phase 5B exit shadow-test baseline — kimi-only clone of
    # fund_manager (no routing block) consumed only by
    # backend.services.shadow_runner. Same thinking config so the
    # baseline reasoning footprint matches the routed-tier kimi call.
    "fund_manager_shadow_baseline": ("enabled", 8_000, "last_round"),
}


@pytest.fixture(scope="module")
def production_router_config() -> RouterConfig:
    return load_router_config(
        Path(__file__).resolve().parents[1] / "config" / "agent_models.yaml"
    )


@pytest.mark.integration
class TestProductionConfigRoundTrip:
    def test_all_agents_present(
        self, production_router_config: RouterConfig
    ) -> None:
        assert set(production_router_config.agents.keys()) == set(
            _PROD_THINKING_TABLE.keys()
        )

    @pytest.mark.parametrize(
        ("agent_name", "expected"),
        list(_PROD_THINKING_TABLE.items()),
        ids=list(_PROD_THINKING_TABLE.keys()),
    )
    def test_each_agent_thinking_matches_ssot_table(
        self,
        production_router_config: RouterConfig,
        agent_name: str,
        expected: tuple[str, int, str],
    ) -> None:
        """Locks down SSoT §704-727 — silent default-fallback regressions fail here."""
        cfg = production_router_config.agents[agent_name].thinking
        assert (cfg.type, cfg.max_tokens, cfg.keep) == expected

    def test_legacy_agent_config_without_thinking_still_loads(
        self, tmp_path: Path

exec
/bin/bash -lc "sed -n '1,220p' backend/agents/bull_researcher.py && sed -n '1,220p' backend/agents/bear_researcher.py && sed -n '1,180p' backend/agents/risk_officer.py && sed -n '1,180p' backend/agents/intelligence_officer.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Bull researcher agent: builds bullish investment thesis."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState, DebateState
from backend.agents.prompts import BULL_RESEARCHER_PROMPT

log = structlog.get_logger(component="agent.bull_researcher")


def _build_reports_context(state: AnalysisState) -> str:
    """Compile all analysis reports into a single context block."""
    return (
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}"
    )


async def bull_researcher_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Build a bullish argument based on all reports and debate history.

    Updates debate_state with new bull argument and incremented count.

    Returns:
        Dict with 'debate_state' key for state update.
    """
    debate = state["debate_state"]
    reports = _build_reports_context(state)

    debate_context = ""
    if debate["bear_history"]:
        debate_context = (
            f"\n\n=== 看空研究员论点（你需要反驳）===\n"
            f"{debate['bear_history']}"
        )

    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"{reports}{debate_context}"
    )
    argument = await call_agent(
        services.llm_router,
        "bull_researcher",
        BULL_RESEARCHER_PROMPT,
        user_content,
    )

    new_debate: DebateState = {
        "history": debate["history"] + f"\n\n【看多研究员】\n{argument}",
        "bull_history": debate["bull_history"] + f"\n{argument}",
        "bear_history": debate["bear_history"],
        "current_response": f"Bull: {argument}",
        "count": debate["count"] + 1,
    }
    return {"debate_state": new_debate}
"""Bear researcher agent: builds bearish investment thesis."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState, DebateState
from backend.agents.prompts import BEAR_RESEARCHER_PROMPT

log = structlog.get_logger(component="agent.bear_researcher")


async def bear_researcher_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Build a bearish argument based on all reports and debate history.

    Updates debate_state with new bear argument and incremented count.

    Returns:
        Dict with 'debate_state' key for state update.
    """
    debate = state["debate_state"]

    reports_context = (
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}"
    )

    debate_context = ""
    if debate["bull_history"]:
        debate_context = (
            f"\n\n=== 看多研究员论点（你需要反驳）===\n"
            f"{debate['bull_history']}"
        )

    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"{reports_context}{debate_context}"
    )
    argument = await call_agent(
        services.llm_router,
        "bear_researcher",
        BEAR_RESEARCHER_PROMPT,
        user_content,
    )

    new_debate: DebateState = {
        "history": debate["history"] + f"\n\n【看空研究员】\n{argument}",
        "bull_history": debate["bull_history"],
        "bear_history": debate["bear_history"] + f"\n{argument}",
        "current_response": f"Bear: {argument}",
        "count": debate["count"] + 1,
    }
    return {"debate_state": new_debate}
"""Risk officer agent: evaluates portfolio risk and recommends position sizing."""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import RISK_OFFICER_PROMPT

log = structlog.get_logger(component="agent.risk_officer")


async def risk_officer_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Evaluate risk based on all reports and debate transcript.

    Returns:
        Dict with 'risk_assessment' key for state update.
    """
    debate = state["debate_state"]
    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"=== 新闻分析 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析 ===\n{state['technical_report']}\n\n"
        f"=== 情报研判 ===\n{state['intelligence_report']}\n\n"
        f"=== 多空辩论记录 ===\n{debate['history']}"
    )
    assessment = await call_agent(
        services.llm_router,
        "risk_officer",
        RISK_OFFICER_PROMPT,
        user_content,
    )
    return {"risk_assessment": assessment}
"""Intelligence officer agent: fuses all analysis reports and market data.

When a MiroFish simulator is available, extracts high-importance events
from the news report and runs group-intelligence simulations. Results
are formatted and injected into the LLM prompt as additional context
for the Bull/Bear debate (Blueprint V3 Section 3.2).
"""

from __future__ import annotations

from typing import Any

import structlog

from backend.agents.base import call_agent
from backend.agents.models import AnalysisServices, AnalysisState
from backend.agents.prompts import INTELLIGENCE_OFFICER_PROMPT

log = structlog.get_logger(component="agent.intelligence_officer")


async def intelligence_officer_node(
    state: AnalysisState, services: AnalysisServices
) -> dict[str, Any]:
    """Fuse all Stage 1 reports with market overview and MiroFish simulation.

    Steps:
    1. Fetch market context (indices, capital flow)
    2. If MiroFish simulator available: extract key events from news,
       run simulation for high-importance events, format results
    3. Call LLM with enriched context (reports + market + simulation)

    Returns:
        Dict with 'intelligence_report' key for state update.
    """
    # -- Step 1: Market context (existing) --
    market_context_parts: list[str] = []
    try:
        indices = await services.market_data.get_index_realtime()
        idx_text = "\n".join(
            f"  {i.name}: {i.price} ({i.change_pct:+.2f}%)"
            for i in indices
        )
        market_context_parts.append(f"大盘指数:\n{idx_text}")
    except Exception as exc:
        log.warning("index_fetch_failed", error=str(exc))

    try:
        flow = await services.market_data.get_capital_flow()
        market_context_parts.append(
            f"北向资金净流入: {flow.north_net_inflow / 1e8:.2f}亿"
        )
    except Exception as exc:
        log.warning("capital_flow_failed", error=str(exc))

    market_context = "\n".join(market_context_parts) or "市场概览数据不可用"

    # -- Step 2: MiroFish simulation (new) --
    # Lazy imports to avoid circular dependency (mirofish -> agents -> graph -> here)
    simulation_context = ""
    if services.mirofish_simulator is not None:
        from backend.mirofish.event_filter import extract_key_events
        from backend.mirofish.formatter import format_simulation_context

        try:
            events = await extract_key_events(
                services.llm_router,
                state["news_report"],
                state["stock_code"],
                state["stock_name"],
            )
            if events:
                from backend.mirofish.schemas import SimulationResult

                results: list[SimulationResult] = []
                for event in events:
                    try:
                        result = await services.mirofish_simulator.run_simulation(
                            event
                        )
                        results.append(result)
                    except Exception as exc:
                        log.warning(
                            "mirofish_simulation_failed",
                            event=event.title,
                            error=str(exc),
                        )
                if results:
                    simulation_context = format_simulation_context(
                        tuple(results)
                    )
                    log.info(
                        "mirofish_simulations_complete",
                        count=len(results),
                    )
                    # Persist simulation results to MongoDB for browsing
                    if services.mongodb is not None:
                        from datetime import datetime, timezone

                        coll = services.mongodb._db["simulations"]
                        for r, ev in zip(results, events):
                            doc = {
                                **r.model_dump(mode="json"),
                                "event": ev.model_dump(mode="json"),
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            }
                            try:
                                await coll.insert_one(doc)
                            except Exception as store_exc:
                                log.warning(
                                    "simulation_persist_failed",
                                    error=str(store_exc),
                                )
        except Exception as exc:
            log.warning("mirofish_pipeline_failed", error=str(exc))

    # -- Step 3: Call LLM with enriched context --
    user_content = (
        f"目标股票: {state['stock_code']} {state['stock_name']}\n"
        f"分析日期: {state['trade_date']}\n\n"
        f"=== 新闻分析报告 ===\n{state['news_report']}\n\n"
        f"=== 情绪分析报告 ===\n{state['sentiment_report']}\n\n"
        f"=== 基本面分析报告 ===\n{state['fundamental_report']}\n\n"
        f"=== 技术分析报告 ===\n{state['technical_report']}\n\n"
        f"=== 市场概览 ===\n{market_context}"
    )

    if simulation_context:
        user_content += (
            f"\n\n=== MiroFish群体智能仿真 ===\n{simulation_context}"
        )

    report = await call_agent(
        services.llm_router,
        "intelligence_officer",
        INTELLIGENCE_OFFICER_PROMPT,
        user_content,
    )
    return {"intelligence_report": report}

exec
/bin/bash -lc "nl -ba backend/services/shadow_runner.py | sed -n '130,220p'" in /home/ps/papers/QuantMind
 succeeded in 0ms:
   130	    debate_history = _join_debates(record)
   131	
   132	    return (
   133	        f"目标股票: {record.stock_code} {record.stock_name}\n"
   134	        f"分析日期: {record.trade_date}\n\n"
   135	        f"=== 新闻分析 ===\n{by_agent['news_crawler']}\n\n"
   136	        f"=== 情绪分析 ===\n{by_agent['sentiment_analyst']}\n\n"
   137	        f"=== 基本面分析 ===\n{by_agent['fundamental_analyst']}\n\n"
   138	        f"=== 技术分析 ===\n{by_agent['technical_analyst']}\n\n"
   139	        f"=== 情报研判 ===\n{by_agent['intelligence_officer']}\n\n"
   140	        f"=== 多空辩论记录 ===\n{debate_history}\n\n"
   141	        f"=== 风控评估 ===\n{by_agent['risk_officer']}"
   142	    )
   143	
   144	
   145	def _join_debates(record: AnalysisRecord) -> str:
   146	    """Serialise the debate rounds back into the ``Bull:/Bear:`` log form.
   147	
   148	    The live node consumes ``state["debate_state"]["history"]`` which is
   149	    ``"Bull: ...\\nBear: ..."`` per turn. The collector saves bull/bear
   150	    contents per-round; we recombine in round order so the baseline
   151	    call sees the same conversation transcript.
   152	    """
   153	    lines: list[str] = []
   154	    for round_record in record.debates:
   155	        if round_record.bull is not None:
   156	            lines.append(f"Bull: {round_record.bull.content}")
   157	        if round_record.bear is not None:
   158	            lines.append(f"Bear: {round_record.bear.content}")
   159	    return "\n".join(lines)
   160	
   161	
   162	def _parse_baseline_response(raw: str) -> tuple[str, float, bool]:
   163	    """Best-effort parse of the baseline JSON envelope.
   164	
   165	    Returns ``(action, confidence, parse_ok)``. The ``parse_ok`` flag
   166	    is the single source of truth for whether the leg ought to count
   167	    as a "clean" sample upstream. Out-of-contract responses still get
   168	    an entry written (parse_ok=False) so shadow_compare can surface
   169	    parse-failure rate as a quality metric.
   170	    """
   171	    if not isinstance(raw, str) or not raw:
   172	        return "持有", 0.5, False
   173	
   174	    match = _JSON_BLOCK_RE.search(raw)
   175	    if match is None:
   176	        return "持有", 0.5, False
   177	    try:
   178	        data = json.loads(match.group(0))
   179	    except (json.JSONDecodeError, ValueError):
   180	        return "持有", 0.5, False
   181	    if not isinstance(data, dict):
   182	        return "持有", 0.5, False
   183	
   184	    action = data.get("action")
   185	    confidence_raw = data.get("confidence")
   186	
   187	    if action not in _VALID_ACTIONS:
   188	        return "持有", 0.5, False
   189	    if isinstance(confidence_raw, bool) or not isinstance(
   190	        confidence_raw, (int, float)
   191	    ):
   192	        return "持有", 0.5, False
   193	    confidence = float(confidence_raw)
   194	    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
   195	        return "持有", 0.5, False
   196	    return action, confidence, True
   197	
   198	
   199	def _routed_leg_from_record(record: AnalysisRecord) -> ShadowDecisionLeg | None:
   200	    """Build the routed leg from the production AnalysisRecord."""
   201	    decision = record.decision
   202	    if decision is None:
   203	        return None
   204	    step = decision.step
   205	    started = step.started_at
   206	    completed = step.completed_at
   207	    if started is None or completed is None or completed < started:
   208	        latency_ms = 0.0
   209	    else:
   210	        latency_ms = (completed - started).total_seconds() * 1000.0
   211	
   212	    try:
   213	        return ShadowDecisionLeg(
   214	            action=decision.action,
   215	            confidence=float(decision.confidence),
   216	            # The router's per-call route taken (triage vs escalation)
   217	            # is observable only through ``llm:escalations`` Redis
   218	            # counters; it is an aggregate signal, not per-run. Using
   219	            # a stable label here keeps the schema honest while the
   220	            # aggregate stays accessible via /api/monitoring/llm/escalations.

exec
/bin/bash -lc "sed -n '1,300p' backend/services/cost_guard.py && sed -n '1,280p' backend/llm/fallback.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Daily LLM cost ceiling enforcement.

Single source of truth for whether the next pipeline run is allowed
under the configured daily budget. The guard never mutates spend data —
it reads the live aggregate from Redis (via cost_tracker) and answers
ok / soft_breach / hard_breach so the scheduler can short-circuit
before paying for another LLM call.

Environment knobs:
- ``QUANTMIND_DAILY_BUDGET`` — absolute hard ceiling in CNY (default ¥20)
- ``QUANTMIND_SOFT_CEIL_PCT`` — fraction of the hard ceiling that
  triggers a soft warning (default 0.7 → ¥14 with the default budget)

Distinct from ``ALERT_COST_DAILY_CNY`` consumed by the dashboard
``/api/monitoring/dashboard`` endpoint: that knob is a cosmetic alert
threshold; this module enforces a hard skip before LLM calls happen.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.llm.cost_tracker import aggregate_costs

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="cost_guard")

# Defaults applied when the env vars are unset; deliberately permissive
# so production stays the source of truth for tightening these.
_DEFAULT_DAILY_BUDGET_RMB = 20.0
_DEFAULT_SOFT_CEIL_PCT = 0.7


@dataclass(frozen=True)
class BudgetState:
    """Snapshot of today's LLM spend vs the configured ceilings."""

    daily_budget: float
    spent_today: float
    soft_ceiling: float
    hard_ceiling: float
    remaining: float
    status: str  # "ok" | "soft_breach" | "hard_breach"


class DailyBudgetExceededError(RuntimeError):
    """Raised when the next call would exceed the daily hard ceiling."""


def _read_env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Parse a float from the environment, falling back to ``default``.

    Tolerates malformed values (logs + uses default) so a typo in one
    knob does not crash the scheduler boot. Rejects NaN and infinity:
    those would silently soft-disable the cap (e.g. ``budget=inf`` makes
    every spend look ok), which is the worst possible failure mode for
    a guard rail. Non-finite values fall back to ``default``.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("cost_guard_env_parse_failed", name=name, raw=raw)
        return default
    if not math.isfinite(value):
        log.warning(
            "cost_guard_env_non_finite",
            name=name,
            raw=raw,
            fallback=default,
        )
        return default
    if value < minimum:
        log.warning(
            "cost_guard_env_clamped",
            name=name,
            raw=value,
            clamped_to=minimum,
        )
        return minimum
    return value


def _classify(spent: float, soft: float, hard: float) -> str:
    if spent >= hard:
        return "hard_breach"
    if spent >= soft:
        return "soft_breach"
    return "ok"


async def get_budget_state(
    redis_client: redis.asyncio.Redis,
) -> BudgetState:
    """Build the current ``BudgetState`` from Redis aggregations.

    Reads only — no mutation of cost or budget data. Today's total comes
    from ``aggregate_costs(days=1)`` which scans
    ``llm:usage:{date}:*`` keys for the current Asia/Shanghai date.
    """
    daily_budget = _read_env_float(
        "QUANTMIND_DAILY_BUDGET",
        _DEFAULT_DAILY_BUDGET_RMB,
        minimum=0.0,
    )
    soft_pct = _read_env_float(
        "QUANTMIND_SOFT_CEIL_PCT",
        _DEFAULT_SOFT_CEIL_PCT,
        minimum=0.0,
    )
    # A misconfigured >1.0 soft pct would defeat the warning, so cap it.
    if soft_pct > 1.0:
        log.warning(
            "cost_guard_soft_pct_clamped",
            raw=soft_pct,
            clamped_to=1.0,
        )
        soft_pct = 1.0

    summary = await aggregate_costs(redis_client, days=1)
    raw_spent = (
        next(iter(summary.daily_totals.values()), 0.0)
        if summary.daily_totals
        else 0.0
    )

    soft_ceiling = round(daily_budget * soft_pct, 4)
    hard_ceiling = daily_budget

    # Fail-closed on corrupt aggregate data: a Redis HSET that wrote
    # ``cost_rmb=nan`` or ``-inf`` would otherwise propagate here and
    # make ``_classify()`` see "ok" forever. Treat invalid spend as a
    # hard breach so the scheduler short-circuits until operators fix
    # the data, instead of silently disabling the cap.
    if not math.isfinite(raw_spent) or raw_spent < 0:
        log.error(
            "cost_guard_invalid_spent",
            raw_spent=raw_spent,
            action="fail_closed_as_hard_breach",
        )
        sentinel_spent = round(max(daily_budget, 0.0) + 1.0, 4)
        return BudgetState(
            daily_budget=daily_budget,
            spent_today=sentinel_spent,
            soft_ceiling=soft_ceiling,
            hard_ceiling=hard_ceiling,
            remaining=0.0,
            status="hard_breach",
        )

    spent_today = raw_spent
    status = _classify(spent_today, soft_ceiling, hard_ceiling)
    return BudgetState(
        daily_budget=daily_budget,
        spent_today=round(spent_today, 4),
        soft_ceiling=soft_ceiling,
        hard_ceiling=hard_ceiling,
        remaining=round(max(0.0, daily_budget - spent_today), 4),
        status=status,
    )


async def assert_budget_allows(
    redis_client: redis.asyncio.Redis,
    *,
    agent_name: str,
) -> BudgetState:
    """Return the live ``BudgetState`` or raise on ``hard_breach``.

    Callers should treat the returned ``status == "soft_breach"`` as a
    cue to degrade (for example serialize catch-up runs and force
    Kimi thinking off, see Phase 5B).
    """
    state = await get_budget_state(redis_client)
    if state.status == "hard_breach":
        log.error(
            "daily_budget_breached",
            agent=agent_name,
            spent=state.spent_today,
            budget=state.daily_budget,
        )
        raise DailyBudgetExceededError(
            f"Daily budget {state.daily_budget:.2f} CNY exceeded "
            f"(spent {state.spent_today:.2f}); skipping {agent_name}"
        )
    if state.status == "soft_breach":
        log.warning(
            "cost_soft_breach_active",
            agent=agent_name,
            spent=state.spent_today,
            soft_ceiling=state.soft_ceiling,
        )
    return state
"""LLM fallback logic and token usage / cost tracking."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

import openai
import structlog

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="llm_fallback")


def _utc_date_str() -> str:
    """Single source of truth for `llm:*:{date}` Redis key date basis.

    Track-time and read-time keys must agree, otherwise the monitoring
    endpoint silently shows zero data while Redis fills under a
    different bucket. Pinning to UTC removes timezone drift on hosts
    deployed in Asia/Shanghai (the default for this project).
    """
    return datetime.datetime.now(tz=datetime.UTC).date().isoformat()

# -- Retryable exceptions that trigger fallback --

RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIConnectionError,
)

# -- Cost rates per million tokens (from blueprint section 2.1) --


@dataclass(frozen=True)
class CostRate:
    """Cost rate per million tokens for a provider (in RMB)."""

    input_rmb_per_million: float
    output_rmb_per_million: float


COST_RATES: dict[str, CostRate] = {
    "deepseek": CostRate(input_rmb_per_million=0.2, output_rmb_per_million=0.2),
    "qwen": CostRate(input_rmb_per_million=1.0, output_rmb_per_million=1.0),
    "kimi": CostRate(input_rmb_per_million=2.1, output_rmb_per_million=8.4),
}

_TTL_DAYS = 90


# -- Token usage tracking --


async def track_usage(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Track token usage and cost in Redis.

    Key pattern: llm:usage:{date}:{agent_name}:{provider}
    Fields: prompt_tokens, completion_tokens, requests, cost_rmb

    Silently logs and returns on Redis errors (degrade, not crash).
    """
    if redis_client is None:
        return

    date_str = _utc_date_str()
    key = f"llm:usage:{date_str}:{agent_name}:{provider}"

    rate = COST_RATES.get(provider, CostRate(0.0, 0.0))
    cost = (
        prompt_tokens * rate.input_rmb_per_million / 1_000_000
        + completion_tokens * rate.output_rmb_per_million / 1_000_000
    )

    try:
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "prompt_tokens", prompt_tokens)
        pipe.hincrby(key, "completion_tokens", completion_tokens)
        pipe.hincrby(key, "requests", 1)
        pipe.hincrbyfloat(key, "cost_rmb", round(cost, 8))
        pipe.expire(key, _TTL_DAYS * 86400)
        await pipe.execute()
    except Exception as exc:
        log.warning(
            "redis_usage_tracking_failed",
            agent_name=agent_name,
            provider=provider,
            error=str(exc),
        )


async def track_fallback(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    primary_provider: str,
    fallback_provider: str,
) -> None:
    """Increment fallback counter in Redis.

    Key: llm:fallbacks:{date}
    Field: {agent_name}:{primary_provider}->{fallback_provider}
    """
    if redis_client is None:
        return

    date_str = _utc_date_str()
    key = f"llm:fallbacks:{date_str}"
    field = f"{agent_name}:{primary_provider}->{fallback_provider}"

    try:
        await redis_client.hincrby(key, field, 1)
        await redis_client.expire(key, _TTL_DAYS * 86400)
    except Exception as exc:
        log.warning(
            "redis_fallback_tracking_failed",
            agent_name=agent_name,
            error=str(exc),
        )


_ESCALATION_REASONS: frozenset[str] = frozenset(
    {"low_confidence", "parse_failed"}
)


async def track_escalation(
    redis_client: redis.asyncio.Redis | None,
    agent_name: str,
    triage_provider: str,
    escalation_provider: str,
    reason: str,
) -> None:
    """Increment per-agent escalation counters in Redis.

    Key pattern: ``llm:escalations:{date}:{agent_name}``
    Fields:
      - ``count``                  total escalations today
      - ``reason_<reason>``        per-reason breakdown
      - ``route_<src>-><dst>``     per-route breakdown for cost analysis

    The reason field is whitelisted (``_ESCALATION_REASONS``) so a
    rogue caller cannot inflate the hash with arbitrary keys; unknown
    reasons fall through to ``reason_other`` and emit a warning. Redis
    failures degrade silently — escalation tracking is observability,
    never a hard dependency for the request path.
    """
    if redis_client is None:
        return

    bucket = reason if reason in _ESCALATION_REASONS else "other"
    if bucket == "other":
        log.warning(
            "escalation_unknown_reason",
            agent_name=agent_name,
            reason=reason,
        )

    date_str = _utc_date_str()
    key = f"llm:escalations:{date_str}:{agent_name}"
    route_field = f"route_{triage_provider}->{escalation_provider}"

    try:
        pipe = redis_client.pipeline()
        pipe.hincrby(key, "count", 1)
        pipe.hincrby(key, f"reason_{bucket}", 1)
        pipe.hincrby(key, route_field, 1)
        pipe.expire(key, _TTL_DAYS * 86400)
        await pipe.execute()
    except Exception as exc:
        log.warning(
            "redis_escalation_tracking_failed",
            agent_name=agent_name,
            reason=reason,
            error=str(exc),
        )

exec
/bin/bash -lc "grep -R \"asyncio_mode\\|pytest_plugins\\|pytestmark\" -n pyproject.toml pytest.ini tests/conftest.py setup.cfg" in /home/ps/papers/QuantMind
 exited 2 in 0ms:
grep: pytest.ini: No such file or directory
pyproject.toml:66:asyncio_mode = "auto"
grep: setup.cfg: No such file or directory

exec
/bin/bash -lc 'pytest -q tests/test_shadow_runner.py tests/test_llm_router_thinking.py' in /home/ps/papers/QuantMind
 succeeded in 978ms:
........................................................................ [ 49%]
........................................................................ [ 99%]
.                                                                        [100%]
145 passed in 0.54s

exec
/bin/bash -lc "sed -n '1,240p' backend/llm/cost_tracker.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""LLM cost aggregation from Redis usage data."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.llm.fallback import COST_RATES

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.database import MongoDBService

log = structlog.get_logger(component="cost_tracker")

# Per-model pricing in RMB per 1K tokens (more granular than COST_RATES)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"input": 0.0002, "output": 0.0002},
    "qwen3.6-plus": {"input": 0.001, "output": 0.001},
    "kimi-k2.6": {"input": 0.0021, "output": 0.0084},
}


@dataclass(frozen=True)
class DailyCostEntry:
    """A single usage record for one agent-provider pair on one day."""

    date: str
    agent_name: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    requests: int
    cost_rmb: float


@dataclass(frozen=True)
class CostSummary:
    """Aggregated cost statistics over a period."""

    period: str
    days: int
    entries: tuple[DailyCostEntry, ...]
    total_cost_rmb: float
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    by_agent: dict[str, float]
    by_provider: dict[str, float]
    daily_totals: dict[str, float]


def calculate_cost(
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate cost in RMB for a given token count.

    Uses COST_RATES from fallback.py (per million tokens).
    """
    rate = COST_RATES.get(provider)
    if rate is None:
        return 0.0
    cost = (
        prompt_tokens * rate.input_rmb_per_million / 1_000_000
        + completion_tokens * rate.output_rmb_per_million / 1_000_000
    )
    return round(cost, 8)


async def aggregate_costs(
    redis_client: redis.asyncio.Redis,
    days: int = 30,
    period: str = "daily",
) -> CostSummary:
    """Scan Redis for usage data and aggregate cost statistics.

    Scans keys matching the pattern llm:usage:{date}:{agent}:{provider}
    for the requested number of days.

    Args:
        redis_client: Async Redis client.
        days: Number of days to look back.
        period: Aggregation period ('daily' or 'weekly').

    Returns:
        CostSummary with all aggregated data.
    """
    # Pin to UTC date — must match the writer in
    # backend.llm.fallback._utc_date_str(). Using local time here was a
    # silent timezone-drift bug: in Asia/Shanghai the cost_guard hard
    # ceiling could read zero spend during 00:00-08:00 UTC+8 even
    # though Redis already had today's UTC entries (codex P5B-T03 R6).
    today = datetime.datetime.now(tz=datetime.UTC).date()
    entries: list[DailyCostEntry] = []

    for day_offset in range(days):
        date = today - datetime.timedelta(days=day_offset)
        date_str = date.isoformat()
        pattern = f"llm:usage:{date_str}:*"

        try:
            keys = await _scan_keys(redis_client, pattern)
        except Exception as exc:
            log.warning("cost_scan_failed", date=date_str, error=str(exc))
            continue

        for key in keys:
            entry = await _parse_usage_key(redis_client, key, date_str)
            if entry is not None:
                entries.append(entry)

    return _build_summary(entries, period, days)


async def _scan_keys(
    redis_client: redis.asyncio.Redis, pattern: str
) -> list[str]:
    """Scan Redis for keys matching a pattern."""
    keys: list[str] = []
    cursor: int | bytes = 0
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=pattern, count=100
        )
        keys.extend(
            k if isinstance(k, str) else k.decode() for k in batch
        )
        if cursor == 0:
            break
    return keys


async def _parse_usage_key(
    redis_client: redis.asyncio.Redis,
    key: str,
    date_str: str,
) -> DailyCostEntry | None:
    """Parse a single Redis usage key into a DailyCostEntry."""
    try:
        data = await redis_client.hgetall(key)
        if not data:
            return None

        # Key format: llm:usage:{date}:{agent_name}:{provider}
        parts = key.split(":")
        if len(parts) < 5:
            return None

        agent_name = parts[3]
        provider = parts[4]

        prompt_tokens = int(data.get("prompt_tokens", 0))
        completion_tokens = int(data.get("completion_tokens", 0))
        requests = int(data.get("requests", 0))
        cost_rmb = float(data.get("cost_rmb", 0.0))

        # Drop entries with corrupt cost values: a negative or non-finite
        # cost_rmb would otherwise offset legitimate spend in the daily
        # aggregate and silently undercut the cost_guard hard cap. This
        # is the data-layer defense; cost_guard.get_budget_state has a
        # second fail-closed check on the aggregate.
        if not math.isfinite(cost_rmb) or cost_rmb < 0:
            log.warning(
                "cost_entry_invalid",
                key=key,
                cost_rmb=cost_rmb,
                action="dropped",
            )
            return None

        return DailyCostEntry(
            date=date_str,
            agent_name=agent_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            requests=requests,
            cost_rmb=cost_rmb,
        )
    except Exception as exc:
        log.warning("cost_parse_failed", key=key, error=str(exc))
        return None


def _build_summary(
    entries: list[DailyCostEntry], period: str, days: int
) -> CostSummary:
    """Build a CostSummary from a list of DailyCostEntry records."""
    total_cost = 0.0
    total_requests = 0
    total_prompt = 0
    total_completion = 0
    by_agent: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for entry in entries:
        total_cost += entry.cost_rmb
        total_requests += entry.requests
        total_prompt += entry.prompt_tokens
        total_completion += entry.completion_tokens

        by_agent[entry.agent_name] = (
            by_agent.get(entry.agent_name, 0.0) + entry.cost_rmb
        )
        by_provider[entry.provider] = (
            by_provider.get(entry.provider, 0.0) + entry.cost_rmb
        )
        daily_totals[entry.date] = (
            daily_totals.get(entry.date, 0.0) + entry.cost_rmb
        )

    return CostSummary(
        period=period,
        days=days,
        entries=tuple(entries),
        total_cost_rmb=round(total_cost, 4),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        by_agent={k: round(v, 4) for k, v in by_agent.items()},
        by_provider={k: round(v, 4) for k, v in by_provider.items()},
        daily_totals={k: round(v, 4) for k, v in daily_totals.items()},
    )


async def flush_to_mongodb(
    redis_client: redis.asyncio.Redis,
    mongodb: MongoDBService,
    days: int = 1,
) -> int:
    """Persist cost entries from Redis to MongoDB for durable storage.


exec
/bin/bash -lc "sed -n '60,120p' tests/test_analysis_scheduler.py && sed -n '60,130p' tests/test_analysis_scheduler_fast_slow.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
    return wl


@pytest.fixture()
def mock_services() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_mongodb() -> AsyncMock:
    mongodb = AsyncMock()
    mongodb.save_signal = AsyncMock(return_value="signal_id")
    mongodb.save_analysis_record = AsyncMock(return_value="record_id")
    return mongodb


@pytest.fixture()
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def scheduler(
    mock_watchlist: AsyncMock,
    mock_services: MagicMock,
    mock_mongodb: AsyncMock,
    mock_redis: AsyncMock,
) -> AnalysisScheduler:
    return AnalysisScheduler(
        watchlist=mock_watchlist,
        services=mock_services,
        mongodb=mock_mongodb,
        redis_client=mock_redis,
    )


class TestRunDailyAnalysis:
    """Tests for run_daily_analysis method."""

    @pytest.mark.asyncio
    async def test_calls_run_analysis_per_stock(
        self, scheduler: AnalysisScheduler
    ) -> None:
        """run_analysis is called once for each watchlist stock."""
        with patch(
            "backend.data.analysis_scheduler.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_result(),
        ) as mock_run, patch(
            "backend.data.analysis_scheduler.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await scheduler.run_daily_analysis()

        assert mock_run.call_count == 3

    @pytest.mark.asyncio
    async def test_persists_each_signal(
        self, scheduler: AnalysisScheduler, mock_mongodb: AsyncMock
    ) -> None:
        """Each successful signal is saved via mongodb.save_signal."""
        trade_date=signal.trade_date,
        status="completed",
        max_rounds=2,
        current_round=2,
        created_at=now,
        completed_at=now,
    )
    return AnalysisRunResult(signal=signal, record=record)


@pytest.fixture()
def policy(tmp_path: Path) -> WatchlistPolicy:
    p = tmp_path / "policy.yaml"
    p.write_text(YAML_TEMPLATE, encoding="utf-8")
    return load_policy(p)


def _make_services() -> AnalysisServices:
    return AnalysisServices(
        llm_router=MagicMock(),
        market_data=MagicMock(),
        history_data=MagicMock(),
        news_crawler=MagicMock(),
        mongodb=MagicMock(),
        pipeline_config=PipelineConfig(),
    )


@pytest.fixture()
def watchlist_with_codes() -> AsyncMock:
    wl = AsyncMock()
    wl.list_stocks = AsyncMock(
        return_value=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
            {"stock_code": "000858", "stock_name": "五粮液", "active": True},
            {"stock_code": "601318", "stock_name": "中国平安", "active": True},
        ]
    )
    return wl


@pytest.fixture()
def mongodb() -> AsyncMock:
    m = AsyncMock()
    m.save_signal = AsyncMock(return_value="sig_id")
    m.save_analysis_record = AsyncMock(return_value="rec_id")
    return m


@pytest.fixture()
def scheduler_with_policy(
    watchlist_with_codes: AsyncMock,
    mongodb: AsyncMock,
    policy: WatchlistPolicy,
) -> AnalysisScheduler:
    return AnalysisScheduler(
        watchlist=watchlist_with_codes,
        services=_make_services(),
        mongodb=mongodb,
        redis_client=None,
        policy=policy,
    )


class TestPolicyAccessors:
    @pytest.mark.unit
    def test_policy_property_returns_loaded_policy(
        self,
        scheduler_with_policy: AnalysisScheduler,
        policy: WatchlistPolicy,
    ) -> None:

exec
/bin/bash -lc "nl -ba backend/services/shadow_runner.py | sed -n '300,355p' && nl -ba backend/data/analysis_scheduler.py | sed -n '535,565p'" in /home/ps/papers/QuantMind
 succeeded in 0ms:
   300	            messages=[
   301	                {"role": "system", "content": FUND_MANAGER_PROMPT},
   302	                {"role": "user", "content": user_content},
   303	            ],
   304	        )
   305	    except Exception as exc:
   306	        log.warning(
   307	            "shadow_baseline_call_failed",
   308	            run_id=record.run_id,
   309	            error=str(exc),
   310	        )
   311	        return False
   312	    latency_ms = (time.perf_counter() - started) * 1000.0
   313	
   314	    raw = ""
   315	    try:
   316	        raw = response.choices[0].message.content or ""
   317	    except (AttributeError, IndexError, TypeError):
   318	        raw = ""
   319	
   320	    action, confidence, parse_ok = _parse_baseline_response(raw)
   321	    try:
   322	        baseline_leg = ShadowDecisionLeg(
   323	            action=action,
   324	            confidence=confidence,
   325	            model="kimi-k2.6-baseline",
   326	            latency_ms=latency_ms,
   327	            escalated=False,
   328	            parse_ok=parse_ok,
   329	        )
   330	    except ValueError as exc:
   331	        log.warning(
   332	            "shadow_baseline_leg_invalid",
   333	            run_id=record.run_id,
   334	            error=str(exc),
   335	        )
   336	        return False
   337	
   338	    try:
   339	        entry = ShadowDecisionEntry(
   340	            run_id=record.run_id,
   341	            stock_code=record.stock_code,
   342	            trade_date=record.trade_date,
   343	            created_at=datetime.now(tz=UTC),
   344	            baseline=baseline_leg,
   345	            routed=routed_leg,
   346	        )
   347	    except ValueError as exc:
   348	        log.warning(
   349	            "shadow_entry_build_failed",
   350	            run_id=record.run_id,
   351	            error=str(exc),
   352	        )
   353	        return False
   354	
   355	    mongodb = services.mongodb
   535	                "save_analysis_record_unavailable", code=stock_code
   536	            )
   537	        except Exception as exc:
   538	            log.warning(
   539	                "save_analysis_record_failed",
   540	                code=stock_code,
   541	                error=str(exc),
   542	            )
   543	
   544	        # Phase 5B exit shadow-test: opt-in via QUANTMIND_SHADOW_ENABLED.
   545	        # Schedule the baseline replay as fire-and-forget so a slow Kimi
   546	        # call cannot stall the next stock in the cron tick.
   547	        try:
   548	            schedule_shadow_run(services, record_with_signal, self._redis)
   549	        except Exception as exc:
   550	            log.warning(
   551	                "shadow_schedule_failed",
   552	                code=stock_code,
   553	                error=str(exc),
   554	            )
   555	
   556	        await self._publish_signal(signal_dict)
   557	        return signal
   558	
   559	    def _resolve_services_and_timeout(
   560	        self,
   561	        category: Category | None,
   562	        policy: WatchlistPolicy | None = None,
   563	    ) -> tuple[AnalysisServices, int | None]:
   564	        """Build per-category services + timeout, or fall back to base.
   565	

exec
/bin/bash -lc "nl -ba backend/services/shadow_runner.py | sed -n '360,402p'" in /home/ps/papers/QuantMind
 succeeded in 0ms:
   360	    return await record_shadow_decision(mongodb, entry)
   361	
   362	
   363	def schedule_shadow_run(
   364	    services: AnalysisServices,
   365	    record: AnalysisRecord,
   366	    redis_client: redis.asyncio.Redis | None,
   367	) -> asyncio.Task[bool] | None:
   368	    """Schedule :func:`run_shadow` as a fire-and-forget background task.
   369	
   370	    Returns the scheduled :class:`asyncio.Task` (so tests can await it)
   371	    or ``None`` when shadow is disabled / the record is unfit.
   372	
   373	    The scheduler intentionally does NOT await — a shadow run can take
   374	    seconds to minutes (a fresh Kimi call), and blocking the analysis
   375	    scheduler on it would defeat the whole "fire and forget" property.
   376	    Errors inside the task are absorbed by ``run_shadow`` itself; we
   377	    additionally attach a done-callback so an unexpected exception is
   378	    logged rather than warned about by the asyncio runtime.
   379	    """
   380	    if not is_enabled():
   381	        return None
   382	
   383	    coro = run_shadow(services, record, redis_client)
   384	    task = asyncio.create_task(coro, name=f"shadow_run:{record.run_id}")
   385	
   386	    def _on_done(t: asyncio.Task[bool]) -> None:
   387	        if t.cancelled():
   388	            return
   389	        exc = t.exception()
   390	        if exc is not None:
   391	            log.warning(
   392	                "shadow_run_task_exception",
   393	                run_id=record.run_id,
   394	                error=repr(exc),
   395	            )
   396	
   397	    task.add_done_callback(_on_done)
   398	    return task
   399	
   400	
   401	__all__ = [
   402	    "SHADOW_BASELINE_AGENT",

codex
The shadow runner can collect non-comparable prompts, can overrun the intended budget guard under concurrent background calls, and can feed fabricated parse-failure defaults into the exit metrics. These issues make the new shadow-test data unreliable even though the normal tests pass.

Full review comments:

- [P1] Rebuild the debate transcript in the live format — /home/ps/papers/QuantMind/backend/services/shadow_runner.py:155-158
  For normal runs with any debate rounds, the live `fund_manager_node` receives `debate["history"]` as produced by the bull/bear nodes with `【看多研究员】` / `【看空研究员】` section headers, not `Bull:` / `Bear:` lines. This reconstructs a different prompt for the baseline, so the shadow window measures prompt-format drift rather than routed-vs-Kimi differences; rebuild the same history format or persist/replay the exact prompt.

- [P2] Serialize shadow budget checks before Kimi calls — /home/ps/papers/QuantMind/backend/services/shadow_runner.py:383-384
  When shadow is enabled for a watchlist with multiple stocks and a baseline call takes longer than the inter-stock delay, this fire-and-forget task lets several `run_shadow` coroutines pass `_budget_allows()` before any of their Kimi usage is tracked. On a near-ceiling day that can launch multiple extra Kimi calls after only one should have been allowed, undermining the hard budget guard; use a serialized shadow queue/semaphore or a reservation before calling Kimi.

- [P2] Exclude parse failures from gate metrics — /home/ps/papers/QuantMind/backend/services/shadow_runner.py:322-328
  When the baseline response is malformed, `_parse_baseline_response` returns the synthetic `持有` / `0.5` with `parse_ok=False`, but this still writes a structurally valid leg. `compute_shadow_report` includes such legs in action-match and confidence-delta gates, so parse failures can be counted as real hold decisions and skew the exit result; either skip these pairs for gate math or change the consumer to exclude `parse_ok=False`.
