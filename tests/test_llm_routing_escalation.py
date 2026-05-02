"""Tests for Phase 5B-T03 tiered triage→escalation routing.

Covers:
  - EscalationCondition schema (extra=forbid + ge/le bounds + at-least-one-rule)
  - LLMRouter._should_escalate: confidence_lt threshold, parse failures,
    no-routing / no-condition shortcuts
  - track_escalation Redis writes
  - /api/monitoring/llm/escalations endpoint integration
  - End-to-end routing flow with track_escalation invocation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.monitoring import router as monitoring_router
from backend.llm.fallback import track_escalation
from backend.llm.providers import (
    EscalationCondition,
    RoutingConfig,
)
from backend.llm.router import LLMRouter
from tests.conftest import make_chat_completion

# ============================================================
# Group 1: EscalationCondition schema (unit)
# ============================================================


@pytest.mark.unit
class TestEscalationConditionSchema:
    def test_confidence_lt_within_bounds(self) -> None:
        cond = EscalationCondition(confidence_lt=0.6)
        assert cond.confidence_lt == 0.6

    def test_confidence_lt_zero_accepted(self) -> None:
        cond = EscalationCondition(confidence_lt=0.0)
        assert cond.confidence_lt == 0.0

    def test_confidence_lt_one_accepted(self) -> None:
        cond = EscalationCondition(confidence_lt=1.0)
        assert cond.confidence_lt == 1.0

    @pytest.mark.parametrize("bad", [-0.01, 1.01, -1.0, 2.0, 100.0])
    def test_confidence_lt_out_of_range_rejected(self, bad: float) -> None:
        with pytest.raises(ValidationError):
            EscalationCondition(confidence_lt=bad)

    def test_empty_condition_rejected(self) -> None:
        """A condition with no rules is a YAML mistake; reject early."""
        with pytest.raises(ValidationError):
            EscalationCondition()

    def test_unknown_field_rejected(self) -> None:
        """extra='forbid' so future-condition typos can't silently disable."""
        with pytest.raises(ValidationError):
            EscalationCondition(  # type: ignore[call-arg]
                confidence_lt=0.5, contradiction_with="bear"
            )

    def test_frozen(self) -> None:
        cond = EscalationCondition(confidence_lt=0.6)
        with pytest.raises(ValidationError):
            cond.confidence_lt = 0.9  # type: ignore[misc]


def _routing(confidence_lt: float = 0.6) -> RoutingConfig:
    return RoutingConfig(
        triage_provider="qwen",
        triage_model="qwen3.6-plus",
        escalation_provider="kimi",
        escalation_model="kimi-k2.6",
        escalation_condition={"confidence_lt": confidence_lt},
    )


# ============================================================
# Group 2: _should_escalate decision matrix (unit)
# ============================================================


@pytest.mark.unit
class TestShouldEscalate:
    def test_low_confidence_escalates(self) -> None:
        resp = make_chat_completion(content='{"confidence": 0.4, "action": "buy"}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "low_confidence")

    def test_at_threshold_does_not_escalate(self) -> None:
        """confidence == threshold: spec says strict-less-than, so safe."""
        resp = make_chat_completion(content='{"confidence": 0.6}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (False, "ok")

    def test_high_confidence_does_not_escalate(self) -> None:
        resp = make_chat_completion(content='{"confidence": 0.85}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (False, "ok")

    def test_parse_failure_escalates(self) -> None:
        """Conservative fail-open: malformed JSON ⇒ promote to expensive model."""
        resp = make_chat_completion(content="not valid json at all")
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_non_dict_json_escalates(self) -> None:
        """A bare list is valid JSON but missing the contract — escalate."""
        resp = make_chat_completion(content="[0.4, 0.5]")
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_missing_confidence_field_escalates(self) -> None:
        resp = make_chat_completion(content='{"action": "buy"}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_non_numeric_confidence_escalates(self) -> None:
        resp = make_chat_completion(content='{"confidence": "high"}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_bool_confidence_escalates(self) -> None:
        """``True`` is technically int-coerced — must NOT bypass the gate."""
        resp = make_chat_completion(content='{"confidence": true}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_false_confidence_escalates(self) -> None:
        """Symmetric: ``False`` must not be treated as 0.0 either."""
        resp = make_chat_completion(content='{"confidence": false}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    @pytest.mark.parametrize(
        "raw",
        [
            "NaN",
            "Infinity",
            "-Infinity",
        ],
        ids=["NaN", "Infinity", "negative_Infinity"],
    )
    def test_non_finite_confidence_escalates(self, raw: str) -> None:
        """Python ``json.loads`` accepts NaN/Infinity — must reject them."""
        resp = make_chat_completion(content=f'{{"confidence": {raw}}}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    @pytest.mark.parametrize(
        "raw",
        [-0.0001, -1.0, -100.0, 1.0001, 2.0, 100.0],
    )
    def test_out_of_range_confidence_escalates(self, raw: float) -> None:
        """Confidence is contractually [0,1]; out-of-range must fail safely."""
        resp = make_chat_completion(content=f'{{"confidence": {raw}}}')
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_no_choices_escalates(self) -> None:
        """A response with empty choices array must not crash."""
        resp = MagicMock()
        resp.choices = []
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_missing_message_attribute_escalates(self) -> None:
        """A choice without a message attribute must not crash."""
        resp = MagicMock()
        bad_choice = MagicMock(spec=[])
        resp.choices = [bad_choice]
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_oversized_content_escalates_without_parsing(self) -> None:
        """Adversarial >64 KB blob must not reach json.loads — DoS guard."""
        from backend.llm.router import _MAX_TRIAGE_JSON_BYTES

        # Build a malformed-but-huge payload; if the cap fires first the
        # JSON parser never sees it (which is the intent).
        bloat = "x" * (_MAX_TRIAGE_JSON_BYTES + 1)
        resp = make_chat_completion(content=bloat)
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_oversized_multibyte_content_escalates(self) -> None:
        """The cap is on UTF-8 byte length, not character count.

        Chinese characters take 3 bytes each — a payload that fits in
        char count must still trip the byte budget.
        """
        from backend.llm.router import _MAX_TRIAGE_JSON_BYTES

        # 3 bytes per char × ~22000 chars > 64 KB while only ~22k chars
        char_count = (_MAX_TRIAGE_JSON_BYTES // 3) + 100
        bloat = "中" * char_count
        # Sanity: char count under cap, byte count over
        assert len(bloat) < _MAX_TRIAGE_JSON_BYTES
        assert len(bloat.encode("utf-8")) > _MAX_TRIAGE_JSON_BYTES
        resp = make_chat_completion(content=bloat)
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_none_routing_returns_no_routing(self) -> None:
        resp = make_chat_completion(content='{"confidence": 0.1}')
        decision = LLMRouter._should_escalate(None, resp)
        assert decision == (False, "no_routing")

    def test_routing_without_condition_returns_no_condition(self) -> None:
        routing = RoutingConfig(
            triage_provider="qwen", triage_model="qwen3.6-plus"
        )
        resp = make_chat_completion(content='{"confidence": 0.1}')
        decision = LLMRouter._should_escalate(routing, resp)
        assert decision == (False, "no_condition")

    def test_empty_content_escalates(self) -> None:
        resp = make_chat_completion(content="")
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    def test_none_content_escalates(self) -> None:
        resp = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        resp.choices = [choice]
        decision = LLMRouter._should_escalate(_routing(0.6), resp)
        assert decision == (True, "parse_failed")

    @pytest.mark.parametrize(
        ("confidence", "threshold", "expected"),
        [
            (0.0, 0.6, True),
            (0.5999, 0.6, True),
            (0.6, 0.6, False),
            (0.6001, 0.6, False),
            (0.999, 0.6, False),
            (0.0, 0.0, False),
            (0.0, 0.5, True),
            (0.49, 0.5, True),
            (0.5, 0.5, False),
        ],
    )
    def test_confidence_threshold_matrix(
        self, confidence: float, threshold: float, expected: bool
    ) -> None:
        resp = make_chat_completion(content=f'{{"confidence": {confidence}}}')
        ok, _ = LLMRouter._should_escalate(_routing(threshold), resp)
        assert ok is expected


# ============================================================
# Group 3: track_escalation Redis writes (unit)
# ============================================================


@pytest.mark.unit
def test_writer_and_reader_share_utc_date_basis() -> None:
    """The writer (track_escalation) and reader (llm_escalations endpoint)
    must agree on the date bucket. Timezone drift here is a silent
    monitoring blind spot — caught by codex review (R1/R2/R3 P1).
    """
    from datetime import UTC, datetime

    from backend.llm.fallback import _utc_date_str

    # Both endpoint and writer pull the date from the same helper; this
    # locks the contract so a future "use local time" tweak fails here.
    assert _utc_date_str() == datetime.now(tz=UTC).strftime("%Y-%m-%d")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_tracker_aggregate_uses_utc_date() -> None:
    """`aggregate_costs` must scan UTC-date keys to match `track_usage`
    (codex R6 follow-up: cost_guard could read zero spend during
    Asia/Shanghai 00:00-08:00 if the reader used local time).
    """
    from datetime import UTC, datetime

    from backend.llm.cost_tracker import aggregate_costs

    expected_today = datetime.now(tz=UTC).date().isoformat()
    seen_patterns: list[str] = []

    redis_mock = AsyncMock()

    async def fake_scan(cursor: int = 0, match: str = "", count: int = 100):
        seen_patterns.append(match)
        return 0, []

    redis_mock.scan = fake_scan
    redis_mock.hgetall = AsyncMock(return_value={})

    await aggregate_costs(redis_mock, days=1)
    # First (and only) day in `days=1` must be today's UTC date.
    assert any(expected_today in p for p in seen_patterns), seen_patterns


@pytest.mark.integration
def test_production_routing_locked_to_fund_manager_only() -> None:
    """Production routing config must enable tiered routing only on
    agents whose prompt produces JSON with a top-level ``confidence``.

    Currently that is fund_manager alone — bull/bear/risk/intelligence
    emit prose. Wiring those agents would force ``parse_failed`` on
    every triage call and double cost instead of saving (codex R1/R4
    P1). This test pins the contract.
    """
    from backend.llm.providers import load_router_config

    cfg = load_router_config(
        Path(__file__).resolve().parents[1] / "config" / "agent_models.yaml"
    )
    routed = {
        name for name, agent in cfg.agents.items() if agent.routing is not None
    }
    assert routed == {"fund_manager"}, (
        "Tiered routing must only target JSON-emitting agents; "
        f"unexpected: {routed - {'fund_manager'}}"
    )

    fm = cfg.agents["fund_manager"]
    assert fm.routing.triage_provider == "qwen"
    assert fm.routing.triage_model == "qwen3.6-plus"
    assert fm.routing.escalation_provider == "kimi"
    assert fm.routing.escalation_model == "kimi-k2.6"
    assert fm.routing.escalation_condition.confidence_lt == 0.6


@pytest.mark.unit
@pytest.mark.asyncio
class TestTrackEscalation:
    async def test_writes_count_reason_and_route(self, mock_redis: AsyncMock) -> None:
        await track_escalation(
            mock_redis, "bull_researcher", "qwen", "kimi", "low_confidence"
        )
        pipe = mock_redis.pipeline.return_value
        keys_fields = [c.args for c in pipe.hincrby.call_args_list]
        assert ("llm:escalations:" in keys_fields[0][0])
        agent_in_key = "bull_researcher" in keys_fields[0][0]
        assert agent_in_key
        # 3 fields incremented: count + reason_low_confidence + route
        assert len(keys_fields) == 3
        fields = {f for _, f, _ in keys_fields}
        assert fields == {
            "count",
            "reason_low_confidence",
            "route_qwen->kimi",
        }
        assert pipe.expire.call_count >= 1

    async def test_unknown_reason_buckets_to_other(
        self, mock_redis: AsyncMock
    ) -> None:
        await track_escalation(
            mock_redis, "fund_manager", "qwen", "kimi", "rogue_reason"
        )
        pipe = mock_redis.pipeline.return_value
        fields = {c.args[1] for c in pipe.hincrby.call_args_list}
        assert "reason_other" in fields
        assert "reason_rogue_reason" not in fields

    async def test_redis_none_does_not_crash(self) -> None:
        await track_escalation(None, "x", "qwen", "kimi", "low_confidence")

    async def test_redis_failure_does_not_crash(self) -> None:
        bad_redis = AsyncMock()
        # ``pipeline()`` is sync on real ``redis.asyncio.Redis``; using a
        # bare AsyncMock leaves a coroutine the test never awaits and
        # produces a RuntimeWarning. Match the real shape: sync method
        # that raises on call.
        bad_redis.pipeline = MagicMock(
            side_effect=ConnectionError("redis down")
        )
        # Must NOT raise — observability degrades silently
        await track_escalation(bad_redis, "x", "qwen", "kimi", "low_confidence")


# ============================================================
# Group 4: monitoring /api/monitoring/llm/escalations (integration)
# ============================================================


def _make_app(redis_state: Any) -> FastAPI:
    app = FastAPI()
    app.state.redis = redis_state
    app.include_router(monitoring_router)
    return app


def _scan_iter_factory(
    keys: list[bytes],
) -> Any:
    async def _aiter(*_args: Any, **_kwargs: Any) -> Any:
        for k in keys:
            yield k

    return _aiter


@pytest.mark.integration
def test_llm_escalations_returns_unavailable_when_no_redis() -> None:
    app = _make_app(redis_state=None)
    with TestClient(app) as client:
        resp = client.get("/api/monitoring/llm/escalations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["status"] == "unavailable"
    assert body["data"]["agents"] == {}
    assert body["data"]["total_escalations"] == 0


@pytest.mark.integration
def test_llm_escalations_aggregates_per_agent() -> None:
    from datetime import UTC, datetime

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    keys = [
        f"llm:escalations:{today}:bull_researcher".encode(),
        f"llm:escalations:{today}:bear_researcher".encode(),
    ]
    fake_data = {
        keys[0]: {
            b"count": b"7",
            b"reason_low_confidence": b"5",
            b"reason_parse_failed": b"2",
            b"route_qwen->kimi": b"7",
        },
        keys[1]: {
            b"count": b"3",
            b"reason_low_confidence": b"3",
            b"route_qwen->kimi": b"3",
        },
    }

    redis_state = MagicMock()
    redis_state.scan_iter = _scan_iter_factory(keys)
    redis_state.hgetall = AsyncMock(side_effect=lambda k: fake_data[k])

    app = _make_app(redis_state=redis_state)
    with TestClient(app) as client:
        resp = client.get("/api/monitoring/llm/escalations")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert data["status"] == "ok"
    assert data["date"] == today
    assert data["total_escalations"] == 10
    assert data["agents"]["bull_researcher"]["count"] == 7
    assert data["agents"]["bull_researcher"]["reason_low_confidence"] == 5
    assert data["agents"]["bear_researcher"]["count"] == 3


@pytest.mark.integration
def test_llm_escalations_skips_keys_with_unparseable_field() -> None:
    """Bad field values must not poison the whole response."""
    from datetime import UTC, datetime

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    keys = [f"llm:escalations:{today}:bull_researcher".encode()]
    fake_data = {
        keys[0]: {
            b"count": b"5",
            b"reason_low_confidence": b"not_a_number",
        },
    }

    redis_state = MagicMock()
    redis_state.scan_iter = _scan_iter_factory(keys)
    redis_state.hgetall = AsyncMock(side_effect=lambda k: fake_data[k])

    app = _make_app(redis_state=redis_state)
    with TestClient(app) as client:
        resp = client.get("/api/monitoring/llm/escalations")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    # count parsed; bad field skipped without crashing
    assert data["agents"]["bull_researcher"]["count"] == 5
    assert "reason_low_confidence" not in data["agents"]["bull_researcher"]
    assert data["total_escalations"] == 5


@pytest.mark.integration
def test_llm_escalations_redis_exception_returns_unavailable() -> None:
    """A scan_iter or hgetall failure must surface as unavailable, not 500."""
    redis_state = MagicMock()
    redis_state.scan_iter = MagicMock(
        side_effect=ConnectionError("redis exploded")
    )

    app = _make_app(redis_state=redis_state)
    with TestClient(app) as client:
        resp = client.get("/api/monitoring/llm/escalations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["status"] == "unavailable"


# ============================================================
# Group 5: End-to-end routing — track_escalation hooked in (integration)
# ============================================================


@pytest.fixture()
def routing_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "agent_models.yaml"
    path.write_text(
        """\
providers:
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    default_model: "qwen3.6-plus"
  kimi:
    base_url: "https://api.moonshot.cn/v1"
    api_key: "${MOONSHOT_API_KEY}"
    default_model: "kimi-k2.6"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  tiered:
    name: "Tiered"
    provider: kimi
    model: kimi-k2.6
    routing:
      triage_provider: qwen
      triage_model: qwen3.6-plus
      escalation_provider: kimi
      escalation_model: kimi-k2.6
      escalation_condition:
        confidence_lt: 0.6
    thinking:
      type: enabled
      max_tokens: 4000
      keep: last_round
    frequency: "daily"
    task: "tiered example"
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_low_confidence_triggers_escalation_and_track(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """Low-confidence triage must escalate AND record the event in Redis."""
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    triage_resp = make_chat_completion(
        content='{"confidence": 0.3, "action": "wait"}'
    )
    esc_resp = make_chat_completion(content="kimi escalated answer")

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=triage_resp)
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=esc_resp)

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch(
        "backend.llm.router.track_escalation", new_callable=AsyncMock
    ) as mock_track:
        with patch.object(router, "_get_client", side_effect=get_client):
            result = await router.complete(
                "tiered", [{"role": "user", "content": "hi"}]
            )

    qwen_client.chat.completions.create.assert_awaited_once()
    kimi_client.chat.completions.create.assert_awaited_once()
    assert result is esc_resp

    mock_track.assert_awaited_once()
    args = mock_track.await_args.args
    assert args[1] == "tiered"
    assert args[2] == "qwen"
    assert args[3] == "kimi"
    assert args[4] == "low_confidence"

    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_high_confidence_does_not_track(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    triage_resp = make_chat_completion(content='{"confidence": 0.95}')

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=triage_resp)
    kimi_client = AsyncMock()

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch(
        "backend.llm.router.track_escalation", new_callable=AsyncMock
    ) as mock_track:
        with patch.object(router, "_get_client", side_effect=get_client):
            await router.complete(
                "tiered", [{"role": "user", "content": "hi"}]
            )

    qwen_client.chat.completions.create.assert_awaited_once()
    kimi_client.chat.completions.create.assert_not_awaited()
    mock_track.assert_not_awaited()
    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parse_failure_escalates_with_parse_failed_reason(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    junk_resp = make_chat_completion(content="this is plain text, not json")
    esc_resp = make_chat_completion(content="kimi rescued it")

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=junk_resp)
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=esc_resp)

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch(
        "backend.llm.router.track_escalation", new_callable=AsyncMock
    ) as mock_track:
        with patch.object(router, "_get_client", side_effect=get_client):
            await router.complete(
                "tiered", [{"role": "user", "content": "hi"}]
            )

    mock_track.assert_awaited_once()
    assert mock_track.await_args.args[4] == "parse_failed"
    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_track_usage_uses_triage_and_escalation_suffixes(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """Cost tracking must split triage vs escalation per spec for trace."""
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    triage_resp = make_chat_completion(content='{"confidence": 0.3}')
    esc_resp = make_chat_completion(content="kimi answer")

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=triage_resp)
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=esc_resp)

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch(
        "backend.llm.router.track_usage", new_callable=AsyncMock
    ) as mock_track_usage:
        with patch.object(router, "_get_client", side_effect=get_client):
            await router.complete(
                "tiered", [{"role": "user", "content": "hi"}]
            )

    # Two track_usage calls, one per stage; agent_name suffixed to split
    # llm:usage Redis keys per stage.
    call_names = [c.args[1] for c in mock_track_usage.await_args_list]
    assert "tiered/triage" in call_names
    assert "tiered/escalation" in call_names

    await router.close()
