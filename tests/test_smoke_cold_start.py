"""J-002 — Unit tests for the cold-start smoke check + LLM stub hook.

Covers:

* :func:`is_llm_stub_enabled` env var detection.
* :class:`StubChatCompletion` shape that smoke + simulator consume.
* :meth:`LLMRouter.complete` short-circuits to the stub when env set.
* :data:`ORCHESTRATION_REQUIRED_SLOTS` matches the I-001
  ``must_have`` list (single source of truth).
* :func:`check_app_state` reports missing / None / conditional slots.
* :func:`format_check_result` renders verdict + slot detail.
* ``scripts/smoke_test_cold_start.main`` invoked with a mocked
  lifespan exits 0 on PASS and 1 on FAIL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from backend.llm.router import (
    QUANTMIND_LLM_STUB_ENV,
    LLMRouter,
    StubChatCompletion,
    is_llm_stub_enabled,
)
from backend.services.smoke_check import (
    CONDITIONAL_SLOTS,
    LIFESPAN_BASE_SLOTS,
    ORCHESTRATION_REQUIRED_SLOTS,
    check_app_state,
    format_check_result,
)

# ---------------------------------------------------------------------------
# LLM stub hook
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "Yes"])
def test_is_llm_stub_enabled_truthy(value: str) -> None:
    with mock.patch.dict(
        os.environ, {QUANTMIND_LLM_STUB_ENV: value}, clear=False
    ):
        assert is_llm_stub_enabled()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "anything"])
def test_is_llm_stub_enabled_falsy(value: str) -> None:
    with mock.patch.dict(
        os.environ, {QUANTMIND_LLM_STUB_ENV: value}, clear=False
    ):
        assert not is_llm_stub_enabled()


def test_is_llm_stub_enabled_unset_default() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        assert not is_llm_stub_enabled()


def test_stub_chat_completion_shape() -> None:
    stub = StubChatCompletion()
    assert stub.quantmind_stub is True
    assert stub.model == "quantmind-stub"
    assert len(stub.choices) == 1
    assert stub.choices[0].message.role == "assistant"
    assert stub.usage.total_tokens == 0
    assert stub.usage.prompt_tokens == 0
    assert stub.usage.completion_tokens == 0


def test_stub_chat_completion_supports_overrides() -> None:
    stub = StubChatCompletion(model="custom-stub")
    assert stub.model == "custom-stub"
    assert stub.choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_llm_router_complete_returns_stub_when_env_set() -> None:
    router = LLMRouter(config_path="config/agent_models.yaml")
    with mock.patch.dict(
        os.environ, {QUANTMIND_LLM_STUB_ENV: "1"}, clear=False
    ):
        result = await router.complete(
            agent_name="any_agent", messages=[{"role": "user", "content": "hi"}]
        )
    assert isinstance(result, StubChatCompletion)
    assert result.quantmind_stub is True
    # No real provider call ⇒ no token usage to track.
    assert result.usage.total_tokens == 0


@pytest.mark.asyncio
async def test_llm_router_complete_does_not_call_provider_when_stubbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the stub short-circuit avoids ``_call_provider``."""
    router = LLMRouter(config_path="config/agent_models.yaml")
    call_count = 0

    async def _exploding_call_provider(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        raise AssertionError(
            "_call_provider was invoked despite QUANTMIND_LLM_STUB=1"
        )

    monkeypatch.setattr(router, "_call_provider", _exploding_call_provider)
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    await router.complete(
        agent_name="any_agent", messages=[{"role": "user", "content": "hi"}]
    )
    assert call_count == 0


# ---------------------------------------------------------------------------
# Slot enumeration matches I-001 contract
# ---------------------------------------------------------------------------


def test_orchestration_required_slots_locked_count() -> None:
    """18 I-001 + 4 U-D1 Line-2 + 1 U-D1b Line-1 slot = 23; J-002 reuses it."""
    assert len(ORCHESTRATION_REQUIRED_SLOTS) == 23
    for slot in (
        "instruction_dispatcher",
        "route_coordinator",
        "line2_daily_runner",
        "line2_intraday_runner",
        "line1_runner",
    ):
        assert slot in ORCHESTRATION_REQUIRED_SLOTS


def test_orchestration_required_slots_no_duplicates() -> None:
    assert len(set(ORCHESTRATION_REQUIRED_SLOTS)) == len(
        ORCHESTRATION_REQUIRED_SLOTS
    )


def test_lifespan_base_slots_locked_count() -> None:
    assert len(LIFESPAN_BASE_SLOTS) == 6


def test_conditional_slots_include_reconciliation_orchestrator() -> None:
    names = {slot for slot, _reason in CONDITIONAL_SLOTS}
    assert "reconciliation_orchestrator" in names
    assert "owner_authorization" in names


# ---------------------------------------------------------------------------
# check_app_state
# ---------------------------------------------------------------------------


def _state_with_all(*, llm_router: Any = None) -> SimpleNamespace:
    """Build a SimpleNamespace with every required + base slot set to a
    sentinel value so the smoke check passes."""
    payload: dict[str, Any] = {name: object() for name in LIFESPAN_BASE_SLOTS}
    payload.update({name: object() for name in ORCHESTRATION_REQUIRED_SLOTS})
    # Conditional slots populated only as needed by individual tests.
    if llm_router is not None:
        payload["llm_router"] = llm_router
    return SimpleNamespace(**payload)


def test_check_app_state_all_present_pass() -> None:
    state = _state_with_all()
    result = check_app_state(state)
    assert result.ok
    assert result.missing_required == ()
    assert result.none_required == ()
    assert len(result.present_required) == (
        len(ORCHESTRATION_REQUIRED_SLOTS) + len(LIFESPAN_BASE_SLOTS)
    )


def test_check_app_state_reports_missing_slot() -> None:
    state = _state_with_all()
    del state.broker_event_store  # simulate missing slot
    result = check_app_state(state)
    assert not result.ok
    assert "broker_event_store" in result.missing_required


def test_check_app_state_reports_none_slot() -> None:
    state = _state_with_all()
    state.broker_scheduler = None
    result = check_app_state(state)
    assert not result.ok
    assert "broker_scheduler" in result.none_required


def test_check_app_state_conditional_none_does_not_fail() -> None:
    state = _state_with_all()
    # reconciliation_orchestrator is conditional — None is OK.
    state.reconciliation_orchestrator = None
    state.feishu_client = None
    state.feishu_alerter = None
    state.owner_authorization = None
    result = check_app_state(state)
    assert result.ok
    cond_names = {slot for slot, _reason in result.none_conditional}
    assert "reconciliation_orchestrator" in cond_names
    assert "feishu_client" in cond_names
    assert "feishu_alerter" in cond_names
    assert "owner_authorization" in cond_names


def test_check_app_state_llm_stub_flag_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When llm_router is wired and env set, llm_router_stubbed is True."""
    state = _state_with_all(llm_router=SimpleNamespace())
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    result = check_app_state(state)
    assert result.llm_router_stubbed is True


def test_check_app_state_llm_stub_flag_false_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_with_all(llm_router=SimpleNamespace())
    monkeypatch.delenv(QUANTMIND_LLM_STUB_ENV, raising=False)
    result = check_app_state(state)
    assert result.llm_router_stubbed is False


def test_check_app_state_llm_stub_flag_none_when_router_missing() -> None:
    state = _state_with_all()
    state.llm_router = None
    result = check_app_state(state)
    assert result.llm_router_stubbed is None
    assert "llm_router" in result.none_required


# ---------------------------------------------------------------------------
# format_check_result
# ---------------------------------------------------------------------------


def test_format_check_result_pass_verdict() -> None:
    state = _state_with_all(llm_router=SimpleNamespace())
    with mock.patch.dict(
        os.environ, {QUANTMIND_LLM_STUB_ENV: "1"}, clear=False
    ):
        result = check_app_state(state)
    text = format_check_result(result)
    assert "smoke check verdict: PASS" in text
    assert "llm_router_stubbed" in text
    assert "missing slots" not in text  # nothing missing


def test_format_check_result_fail_verdict_lists_missing() -> None:
    state = _state_with_all()
    del state.broker_event_store
    text = format_check_result(check_app_state(state))
    assert "smoke check verdict: FAIL" in text
    assert "broker_event_store" in text


# ---------------------------------------------------------------------------
# scripts/smoke_test_cold_start.main
# ---------------------------------------------------------------------------


def _load_smoke_script_module() -> Any:
    """Import scripts/smoke_test_cold_start as a module for testing."""
    if "scripts.smoke_test_cold_start" in sys.modules:
        return sys.modules["scripts.smoke_test_cold_start"]
    import importlib

    return importlib.import_module("scripts.smoke_test_cold_start")


def test_smoke_script_pass_when_lifespan_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` exits 0 when the (mocked) lifespan boots cleanly."""
    smoke = _load_smoke_script_module()

    fake_state = _state_with_all(llm_router=SimpleNamespace())

    @asynccontextmanager
    async def _fake_lifespan(_app: Any):
        # Allow check_app_state to inspect a real-ish state object.
        yield

    fake_app = SimpleNamespace(state=fake_state)

    monkeypatch.setattr(
        "backend.main.app", fake_app, raising=False
    )
    monkeypatch.setattr(
        "backend.main.lifespan", _fake_lifespan, raising=False
    )
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    rc = smoke.main(["--json"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    envelope = json.loads(captured.out)
    assert envelope["verdict"] == "PASS"
    assert envelope["llm_router_stubbed"] is True


def test_smoke_script_fail_when_lifespan_raises(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` exits 1 + stderr has a traceback when lifespan blows."""
    smoke = _load_smoke_script_module()

    @asynccontextmanager
    async def _exploding_lifespan(_app: Any):
        raise RuntimeError("simulated Mongo unreachable")
        yield  # pragma: no cover

    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr("backend.main.app", fake_app, raising=False)
    monkeypatch.setattr(
        "backend.main.lifespan", _exploding_lifespan, raising=False
    )
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    rc = smoke.main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "simulated Mongo unreachable" in captured.err


def test_smoke_script_catches_system_exit_lifespan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex cycle 5 P3 regression — lifespan fail-fast gates raise
    SystemExit (secrets validator / J-007 owner auth / acceptance
    gate). The smoke script must catch BaseException so the
    SystemExit surfaces as a structured failure verdict instead of
    silently escaping the script (especially in --json mode)."""
    smoke = _load_smoke_script_module()

    @asynccontextmanager
    async def _exit_lifespan(_app: Any):
        raise SystemExit("simulated owner-auth fail-fast")
        yield  # pragma: no cover

    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr("backend.main.app", fake_app, raising=False)
    monkeypatch.setattr(
        "backend.main.lifespan", _exit_lifespan, raising=False
    )
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    rc = smoke.main(["--json"])
    captured = capsys.readouterr()
    assert rc == 1
    envelope = json.loads(captured.out)
    assert envelope["verdict"] == "FAIL"
    assert "simulated owner-auth fail-fast" in envelope[
        "lifespan_traceback"
    ]


def test_smoke_script_force_sets_stub_env_even_if_pre_set_falsy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex cycle 2 P2 regression — ``_prepare_env`` must FORCE-SET
    QUANTMIND_LLM_STUB=1 (not setdefault) so a pre-existing falsy
    value in the parent shell cannot silently bypass the smoke
    contract. After the fix, even with ``QUANTMIND_LLM_STUB=0``
    pre-set, the smoke script overrides to ``1`` and exits 0."""
    smoke = _load_smoke_script_module()
    fake_state = _state_with_all(llm_router=SimpleNamespace())

    @asynccontextmanager
    async def _fake_lifespan(_app: Any):
        yield

    fake_app = SimpleNamespace(state=fake_state)
    monkeypatch.setattr("backend.main.app", fake_app, raising=False)
    monkeypatch.setattr(
        "backend.main.lifespan", _fake_lifespan, raising=False
    )
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "0")  # pre-set falsy
    rc = smoke.main([])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    # Verify the env was force-overridden.
    assert os.environ[QUANTMIND_LLM_STUB_ENV] == "1"


def test_smoke_script_allow_real_llm_does_not_force_stub_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When ``--allow-real-llm`` is passed, the smoke script must NOT
    override a pre-existing falsy env (operator explicitly opted in)."""
    smoke = _load_smoke_script_module()
    fake_state = _state_with_all(llm_router=SimpleNamespace())

    @asynccontextmanager
    async def _fake_lifespan(_app: Any):
        yield

    fake_app = SimpleNamespace(state=fake_state)
    monkeypatch.setattr("backend.main.app", fake_app, raising=False)
    monkeypatch.setattr(
        "backend.main.lifespan", _fake_lifespan, raising=False
    )
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "0")
    rc = smoke.main(["--allow-real-llm"])
    captured = capsys.readouterr()
    # rc may be 0 or 1 depending on slot state; the contract here is
    # env preservation, not pass/fail.
    assert os.environ[QUANTMIND_LLM_STUB_ENV] == "0"
    _ = (rc, captured)


# ---------------------------------------------------------------------------
# Silence unused-import warnings.
# ---------------------------------------------------------------------------


_ = asyncio
