"""Tests for backend.services.shadow_runner.

Covers:
* env-flag enable / sample rate clamping / parsing
* prompt rebuild from AnalysisRecord (includes debate transcript)
* parsing the baseline kimi response (valid / malformed / NaN /
  out-of-range / non-bool / fenced / plain JSON)
* run_shadow gating: disabled, sample-rate=0, no decision, partial
  record, budget breach, mongo unavailable
* run_shadow happy path: builds correct entry + writes via
  record_shadow_decision
* schedule_shadow_run fire-and-forget contract
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.records import (
    AgentStepRecord,
    AnalysisRecord,
    DebateRoundRecord,
    FundManagerRecord,
)
from backend.services import shadow_runner
from backend.services.shadow_recorder import (
    ShadowDecisionEntry,
)

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _step(
    agent: str,
    content: str,
    started: _dt.datetime | None = None,
    completed: _dt.datetime | None = None,
) -> AgentStepRecord:
    started = started or _dt.datetime(2026, 5, 2, 9, 0, tzinfo=_dt.UTC)
    return AgentStepRecord(
        agent=agent,  # type: ignore[arg-type]
        content=content,
        started_at=started,
        completed_at=completed
        or started + _dt.timedelta(seconds=2),
    )


def _make_record(
    *,
    decision: FundManagerRecord | None = None,
    debates: list[DebateRoundRecord] | None = None,
    drop_steps: tuple[str, ...] = (),
) -> AnalysisRecord:
    base_steps = [
        _step("news_crawler", "新闻摘要内容"),
        _step("sentiment_analyst", "情绪分数 0.6"),
        _step("fundamental_analyst", "PE 30 ROE 25"),
        _step("technical_analyst", "MACD 金叉"),
        _step("intelligence_officer", "情报融合"),
        _step("risk_officer", "风控 OK"),
    ]
    steps = [s for s in base_steps if s.agent not in drop_steps]
    if decision is None and "fund_manager" not in drop_steps:
        decision_step = _step("fund_manager", "决策")
        decision = FundManagerRecord(
            action="买入",
            confidence=0.8,
            risk_score=0.3,
            reasoning="多空一致",
            step=decision_step,
        )
        steps = [*steps, decision_step]
    return AnalysisRecord(
        run_id="run-1",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-05-02",
        status="completed",
        steps=steps,
        debates=debates or [],
        decision=decision,
    )


_DEFAULT_MONGO = object()


def _make_services(
    router: AsyncMock | None = None, mongodb: Any = _DEFAULT_MONGO
) -> Any:
    services = MagicMock()
    services.llm_router = router or AsyncMock()
    # Distinguish "default → use a MagicMock" from "explicit None →
    # leave services.mongodb as None" so the no-Mongo branch is
    # actually exercised (codex P5B-shadow R2 P3).
    services.mongodb = MagicMock() if mongodb is _DEFAULT_MONGO else mongodb
    return services


def _make_response(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ----------------------------------------------------------------------
# Group 1: env helpers
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestEnvHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", True),
            ("true", True),
            ("YES", True),
            ("on", True),
            ("0", False),
            ("", False),
            ("disabled", False),
        ],
    )
    def test_is_enabled(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, raw)
        assert shadow_runner.is_enabled() is expected

    def test_is_enabled_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
        assert shadow_runner.is_enabled() is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0.5", 0.5),
            ("1.0", 1.0),
            ("0", 0.0),
            ("2", 1.0),  # clamped
            ("-0.1", 0.0),  # clamped
            ("nan", 1.0),  # default
            ("not-a-float", 1.0),  # default
            ("", 1.0),  # default
        ],
    )
    def test_sample_rate(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
    ) -> None:
        if raw == "":
            monkeypatch.delenv(
                shadow_runner.SHADOW_SAMPLE_RATE_ENV, raising=False
            )
        else:
            monkeypatch.setenv(shadow_runner.SHADOW_SAMPLE_RATE_ENV, raw)
        assert shadow_runner._sample_rate() == expected


# ----------------------------------------------------------------------
# Group 2: prompt rebuild
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRebuildUserContent:
    def test_full_record_rebuilds(self) -> None:
        debates = [
            DebateRoundRecord(
                round=1,
                bull=_step("bull_researcher", "看涨论点"),
                bear=_step("bear_researcher", "看空论点"),
            )
        ]
        record = _make_record(debates=debates)
        out = shadow_runner._rebuild_user_content(record)
        assert out is not None
        assert "目标股票: 600519 贵州茅台" in out
        assert "新闻摘要内容" in out
        assert "情绪分数 0.6" in out
        # codex P5B-shadow R1 P1: live debate format uses 【看多研究员】
        # and 【看空研究员】, NOT the Bull:/Bear: shorthand. Locking the
        # expected format here so the prompt drift bug doesn't regress.
        assert "【看多研究员】\n看涨论点" in out
        assert "【看空研究员】\n看空论点" in out
        assert "风控 OK" in out

    def test_returns_none_when_step_missing(self) -> None:
        record = _make_record(drop_steps=("intelligence_officer",))
        assert shadow_runner._rebuild_user_content(record) is None

    def test_empty_debate_renders_empty_section(self) -> None:
        record = _make_record(debates=[])
        out = shadow_runner._rebuild_user_content(record)
        assert out is not None
        # The prompt template expects the debate block, even when empty.
        assert "=== 多空辩论记录 ===" in out


# ----------------------------------------------------------------------
# Group 3: response parsing
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestParseBaselineResponse:
    def test_plain_json_happy_path(self) -> None:
        action, conf, ok = shadow_runner._parse_baseline_response(
            '{"action":"买入","confidence":0.75}'
        )
        assert (action, conf, ok) == ("买入", 0.75, True)

    def test_fenced_json_extracted(self) -> None:
        raw = '```json\n{"action":"卖出","confidence":0.42}\n```'
        action, conf, ok = shadow_runner._parse_baseline_response(raw)
        assert (action, conf, ok) == ("卖出", 0.42, True)

    def test_numeric_string_confidence_accepted(self) -> None:
        # The live ``_parse_signal`` does ``float(data["confidence"])``
        # so a string-encoded number passes — the shadow parser must
        # match (codex P5B-shadow R2 P2): a stricter regex would
        # over-flag valid kimi responses as parse_ok=False.
        action, conf, ok = shadow_runner._parse_baseline_response(
            '{"action":"持有","confidence":"0.62"}'
        )
        assert ok is True
        assert action == "持有"
        assert conf == 0.62

    def test_first_balanced_object_wins_over_trailing_text(self) -> None:
        # Live extractor uses a balanced-brace regex so trailing
        # garbage after a valid object still parses. Shadow must
        # match.
        raw = '{"action":"买入","confidence":0.8} trailing chatter {bad'
        action, conf, ok = shadow_runner._parse_baseline_response(raw)
        assert (action, conf, ok) == ("买入", 0.8, True)

    def test_missing_confidence_uses_default(self) -> None:
        # Live ``_parse_signal`` does ``float(data.get("confidence",
        # 0.5))`` so a JSON missing ``confidence`` still parses to a
        # TradingSignal — shadow must mirror that, otherwise the
        # baseline parse-ok rate looks worse than the live rate.
        action, conf, ok = shadow_runner._parse_baseline_response(
            '{"action":"持有"}'
        )
        assert ok is True
        assert action == "持有"
        assert conf == 0.5

    def test_missing_action_uses_default(self) -> None:
        # codex P5B-shadow R6 UNRESOLVED: live ``_parse_signal``
        # uses ``data.get("action", "持有")`` so JSON missing the
        # ``action`` field still produces a parse_ok=True hold
        # signal. Baseline must match.
        action, conf, ok = shadow_runner._parse_baseline_response(
            '{"confidence":0.7}'
        )
        assert ok is True
        assert action == "持有"
        assert conf == 0.7

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not json at all",
            "{not even kinda json}",
            '{"action":"buy","confidence":0.5}',  # invalid action
            '{"action":"持有","confidence":1.5}',  # out of range
            '{"action":"持有","confidence":"high"}',  # wrong type
            '{"action":"持有","confidence":NaN}',  # python json accepts but we reject
            '{"action":"持有","confidence":true}',  # bool not int
        ],
    )
    def test_malformed_returns_parse_failed(self, raw: str) -> None:
        action, conf, ok = shadow_runner._parse_baseline_response(raw)
        assert ok is False
        assert action == "持有"
        assert conf == 0.5


# ----------------------------------------------------------------------
# Group 4: routed leg extraction
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRoutedLegFromRecord:
    def test_happy_path(self) -> None:
        record = _make_record()
        leg = shadow_runner._routed_leg_from_record(record)
        assert leg is not None
        assert leg.action == "买入"
        assert leg.confidence == 0.8
        assert leg.latency_ms == 2000.0  # 2s

    def test_no_decision_returns_none(self) -> None:
        record = _make_record(drop_steps=("fund_manager",))
        record = record.model_copy(update={"decision": None})
        assert shadow_runner._routed_leg_from_record(record) is None

    def test_routed_parse_ok_propagated_from_record(self) -> None:
        # codex P5B-shadow R2 P2: synthetic 持有/0.5 fallback in the
        # live fund_manager must surface as routed.parse_ok=False so
        # shadow_compare can drop it from gate math.
        decision = FundManagerRecord(
            action="持有",
            confidence=0.5,
            risk_score=0.5,
            reasoning="LLM response could not be parsed",
            parse_ok=False,
            step=_step("fund_manager", ""),
        )
        record = _make_record(decision=decision)
        leg = shadow_runner._routed_leg_from_record(record)
        assert leg is not None
        assert leg.parse_ok is False

    def test_completed_before_started_yields_zero_latency(self) -> None:
        bad_step = AgentStepRecord(
            agent="fund_manager",
            content="决策",
            started_at=_dt.datetime(2026, 5, 2, 10, 0, tzinfo=_dt.UTC),
            completed_at=_dt.datetime(2026, 5, 2, 9, 0, tzinfo=_dt.UTC),
        )
        decision = FundManagerRecord(
            action="持有",
            confidence=0.5,
            risk_score=0.5,
            step=bad_step,
        )
        record = _make_record(decision=decision)
        leg = shadow_runner._routed_leg_from_record(record)
        assert leg is not None
        assert leg.latency_ms == 0.0


# ----------------------------------------------------------------------
# Group 5: budget gate
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestBudgetAllows:
    async def test_no_redis_returns_false(self) -> None:
        # Without Redis we can't verify spend → fail-closed.
        assert await shadow_runner._budget_allows(None) is False

    async def test_ok_state_returns_true(self) -> None:
        redis = MagicMock()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ):
            assert await shadow_runner._budget_allows(redis) is True

    async def test_soft_breach_returns_false(self) -> None:
        # During the 7-day window we treat soft_breach as "skip shadow"
        # so production decision quality stays the priority.
        redis = MagicMock()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="soft_breach")),
        ):
            assert await shadow_runner._budget_allows(redis) is False

    async def test_hard_breach_returns_false(self) -> None:
        redis = MagicMock()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="hard_breach")),
        ):
            assert await shadow_runner._budget_allows(redis) is False

    async def test_exception_returns_false(self) -> None:
        redis = MagicMock()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ):
            assert await shadow_runner._budget_allows(redis) is False


# ----------------------------------------------------------------------
# Group 6: run_shadow integration
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRunShadow:
    async def test_disabled_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
        services = _make_services()
        ok = await shadow_runner.run_shadow(
            services, _make_record(), MagicMock()
        )
        assert ok is False
        services.llm_router.complete.assert_not_called()

    async def test_zero_sample_rate_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        monkeypatch.setenv(shadow_runner.SHADOW_SAMPLE_RATE_ENV, "0")
        services = _make_services()
        ok = await shadow_runner.run_shadow(
            services, _make_record(), MagicMock()
        )
        assert ok is False
        services.llm_router.complete.assert_not_called()

    async def test_no_decision_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        services = _make_services()
        record = _make_record()
        record = record.model_copy(update={"decision": None})
        ok = await shadow_runner.run_shadow(services, record, MagicMock())
        assert ok is False
        services.llm_router.complete.assert_not_called()

    async def test_budget_breach_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        services = _make_services()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="hard_breach")),
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
        assert ok is False
        services.llm_router.complete.assert_not_called()

    async def test_partial_record_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        record = _make_record(drop_steps=("intelligence_officer",))
        services = _make_services()
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ):
            ok = await shadow_runner.run_shadow(
                services, record, MagicMock()
            )
        assert ok is False

    async def test_no_mongo_short_circuits_before_kimi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # codex P5B-shadow R2 P2 + R2 P3: missing Mongo must skip the
        # Kimi call entirely, otherwise we burn cost only to discard
        # the result. The previous test left mongodb as a MagicMock
        # because of the helper default, hiding this branch.
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_response('{"action":"买入","confidence":0.8}')
        )
        services = _make_services(router=router, mongodb=None)
        assert services.mongodb is None  # sanity: real None reached run_shadow
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
        assert ok is False
        router.complete.assert_not_awaited()

    async def test_happy_path_writes_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_response(
                '{"action":"持有","confidence":0.55}'
            )
        )
        mongodb = MagicMock()
        services = _make_services(router=router, mongodb=mongodb)

        recorded: list[ShadowDecisionEntry] = []

        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
            recorded.append(entry)
            return True

        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ), patch.object(
            shadow_runner, "record_shadow_decision", _capture
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )

        assert ok is True
        assert len(recorded) == 1
        entry = recorded[0]
        assert entry.run_id == "run-1"
        assert entry.routed.action == "买入"
        assert entry.baseline.action == "持有"
        assert entry.baseline.confidence == 0.55
        assert entry.baseline.parse_ok is True
        # Sanity: baseline call hit the dedicated baseline agent.
        called_kwargs = router.complete.call_args.kwargs
        assert called_kwargs["agent_name"] == shadow_runner.SHADOW_BASELINE_AGENT

    async def test_router_failure_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=RuntimeError("kimi down"))
        services = _make_services(router=router)
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
        assert ok is False  # absorbed, not raised

    async def test_baseline_parse_failure_still_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Parse failures are themselves a quality signal; the entry
        # must land so shadow_compare can surface parse_ok rate.
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_response("not-even-json")
        )
        services = _make_services(router=router)
        recorded: list[ShadowDecisionEntry] = []

        async def _capture(_mongo: Any, entry: ShadowDecisionEntry) -> bool:
            recorded.append(entry)
            return True

        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ), patch.object(
            shadow_runner, "record_shadow_decision", _capture
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
        assert ok is True
        assert recorded[0].baseline.parse_ok is False


# ----------------------------------------------------------------------
# Group 7: schedule_shadow_run
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestShadowGateSerialisation:
    """codex P5B-shadow R1 P2: budget probe + Kimi call must serialise.

    Without the lock, two fire-and-forget shadow tasks could both
    observe ``budget=ok`` before either's Kimi usage was tracked.
    Reset the module-level lock between tests so each starts fresh.
    """

    @pytest.fixture(autouse=True)
    def _reset_gate(self) -> None:
        shadow_runner._shadow_gate = None

    async def test_concurrent_runs_observe_serialised_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")

        # Track ordering: each budget probe records its sequence
        # number, each kimi call increments a "spent" counter
        # synchronously. With the lock, probe N must see the spend
        # from kimi call N-1.
        spent = [0.0]
        observations: list[float] = []

        async def fake_budget(_redis: Any) -> Any:
            observations.append(spent[0])
            # First call sees ok; second call sees breach iff first
            # already paid → if serialised, second probe sees the
            # incremented spend.
            status = "ok" if spent[0] < 0.05 else "hard_breach"
            return MagicMock(status=status)

        async def fake_complete(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0)  # yield to scheduler
            spent[0] += 0.1  # one Kimi call costs ¥0.1
            return _make_response('{"action":"持有","confidence":0.5}')

        router = AsyncMock()
        router.complete = fake_complete
        services = _make_services(router=router)

        # Stub record_shadow_decision so we don't need Mongo here.
        async def _noop(_mongo: Any, _entry: Any) -> bool:
            return True

        with patch.object(shadow_runner, "get_budget_state", fake_budget), \
             patch.object(shadow_runner, "record_shadow_decision", _noop):
            results = await asyncio.gather(
                shadow_runner.run_shadow(
                    services, _make_record(), MagicMock()
                ),
                shadow_runner.run_shadow(
                    services, _make_record(), MagicMock()
                ),
            )

        # First probe sees 0 spend → ok → kimi call → spend=0.1
        # Second probe (serialised) sees 0.1 → hard_breach → skipped
        assert results == [True, False]
        assert observations[0] == 0.0
        # Second observation must reflect the post-kimi spend.
        assert observations[1] >= 0.1


@pytest.mark.unit
class TestScheduleShadowRun:
    @pytest.fixture(autouse=True)
    def _reset_module_state(self) -> None:
        # Module-globals leak across tests if we don't reset them
        # (codex P5B-shadow R4 P2). Each test starts with a fresh
        # gate + a 0 backlog counter so cap/decrement assertions
        # see the clean baseline.
        shadow_runner._shadow_gate = None
        shadow_runner._inflight_shadow = 0

    def test_returns_none_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(shadow_runner.SHADOW_ENABLED_ENV, raising=False)
        services = _make_services()
        task = shadow_runner.schedule_shadow_run(
            services, _make_record(), MagicMock()
        )
        assert task is None

    async def test_creates_named_task_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        services = _make_services()
        # Stub run_shadow so the task completes immediately.
        with patch.object(
            shadow_runner,
            "run_shadow",
            AsyncMock(return_value=False),
        ):
            task = shadow_runner.schedule_shadow_run(
                services, _make_record(), MagicMock()
            )
            assert task is not None
            assert task.get_name().startswith("shadow_run:")
            await task
        # codex P5B-shadow R4 P2: successful task must decrement
        # the in-flight counter back to zero so the next run can
        # be admitted. A regression that decrements only cancelled
        # tasks would silently exhaust the cap forever.
        assert shadow_runner._inflight_shadow == 0

    async def test_backlog_full_drops_new_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # codex P5B-shadow R3 P2: a stuck baseline call must not let
        # the backlog grow unbounded. Saturating the in-flight
        # counter should make schedule_shadow_run return None and
        # log a warning.
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        monkeypatch.setattr(shadow_runner, "_inflight_shadow", 0)
        monkeypatch.setattr(shadow_runner, "_MAX_INFLIGHT_SHADOW", 2)
        services = _make_services()

        # Use a never-completing run_shadow stub so tasks stay in flight.
        never_done = asyncio.get_event_loop().create_future()

        async def fake_run(*_args: Any, **_kwargs: Any) -> bool:
            await never_done
            return False

        with patch.object(shadow_runner, "run_shadow", fake_run):
            t1 = shadow_runner.schedule_shadow_run(
                services, _make_record(), MagicMock()
            )
            t2 = shadow_runner.schedule_shadow_run(
                services, _make_record(), MagicMock()
            )
            t3 = shadow_runner.schedule_shadow_run(
                services, _make_record(), MagicMock()
            )

        assert t1 is not None and t2 is not None
        assert t3 is None  # cap = 2 → third one dropped

        # Cleanup: cancel pending tasks and wait so done-callbacks fire.
        for task in (t1, t2):
            task.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        # Inflight counter must drop back to 0 once tasks finalised.
        assert shadow_runner._inflight_shadow == 0

    async def test_baseline_timeout_dropped_then_gate_released(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # codex P5B-shadow R3 P2 + R4 P3: a slow Kimi call must hit
        # the timeout AND release the gate so subsequent shadow runs
        # are not starved. Use a Future under our control instead of
        # wall-clock sleep to keep the test deterministic.
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        monkeypatch.setattr(
            shadow_runner, "_BASELINE_CALL_TIMEOUT_SEC", 0.01
        )

        slow_future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()

        async def slow_complete(**_kwargs: Any) -> Any:
            return await slow_future  # never resolves on its own

        async def fast_complete(**_kwargs: Any) -> Any:
            return _make_response('{"action":"持有","confidence":0.5}')

        router = AsyncMock()
        router.complete = slow_complete
        services = _make_services(router=router)
        with patch.object(
            shadow_runner,
            "get_budget_state",
            AsyncMock(return_value=MagicMock(status="ok")),
        ), patch.object(
            shadow_runner,
            "record_shadow_decision",
            AsyncMock(return_value=True),
        ):
            ok = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
            assert ok is False  # timed out, dropped

            # Now swap in a fast complete and run again. If the gate
            # was held by the cancelled coroutine we'd hang; the
            # ``async with`` block exits even when wait_for raises
            # so this second call must complete promptly.
            router.complete = fast_complete
            ok2 = await shadow_runner.run_shadow(
                services, _make_record(), MagicMock()
            )
            assert ok2 is True

        # Cleanup: cancel the dangling future so it doesn't warn.
        slow_future.cancel()

    async def test_task_exception_logged_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv(shadow_runner.SHADOW_ENABLED_ENV, "1")
        services = _make_services()
        with patch.object(
            shadow_runner,
            "run_shadow",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            task = shadow_runner.schedule_shadow_run(
                services, _make_record(), MagicMock()
            )
            assert task is not None
            # awaiting the task surfaces the exception via .exception(),
            # which our done-callback handles. Awaiting from the test
            # propagates it though, so use gather with return_exceptions.
            results = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(results[0], RuntimeError)
        # codex P5B-shadow R4 P2: even an exception path must
        # decrement so the next admission isn't blocked.
        assert shadow_runner._inflight_shadow == 0
