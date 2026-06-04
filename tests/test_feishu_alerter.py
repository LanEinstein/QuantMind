"""F-006 — FeishuAlerter tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.integrations.feishu.alerter import (
    ALERT_TYPES,
    DEFAULT_DEDUP_WINDOW,
    AlertResult,
    FeishuAlerter,
)
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer

_ALERT_CHAT = "oc_" + "a" * 32
_DECISION_CHAT = "oc_" + "d" * 32


class _RecordingFeishu:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._fail = fail

    async def send_message(
        self, chat_id: str, content: str, **kwargs: Any
    ) -> SendMessageResult:
        self.calls.append((chat_id, content, kwargs))
        return SendMessageResult(
            ok=not self._fail,
            code=0 if not self._fail else 99991663,
            msg="success" if not self._fail else "rate-limited",
            message_id="om_a" if not self._fail else None,
            log_id="log_a",
        )


def _build_alerter(
    *,
    feishu: _RecordingFeishu | None = None,
    decision_chat_id: str | None = None,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    env: dict[str, str] | None = None,
    clock_now: datetime | None = None,
) -> tuple[FeishuAlerter, _RecordingFeishu | None]:
    feishu_obj = feishu if feishu is not None else _RecordingFeishu()
    clock = (lambda: clock_now) if clock_now is not None else None
    alerter = FeishuAlerter(
        feishu=feishu_obj,  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        alert_chat_id=_ALERT_CHAT,
        decision_chat_id=decision_chat_id,
        dedup_window=dedup_window,
        env=env or {},
        clock=clock,
    )
    return alerter, feishu_obj


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_empty_alert_chat_id_raises(self) -> None:
        with pytest.raises(ValueError, match="alert_chat_id"):
            FeishuAlerter(
                feishu=None,
                renderer=MessageRenderer(),
                alert_chat_id="",
            )

    def test_alert_chat_must_differ_from_decision_chat(self) -> None:
        with pytest.raises(ValueError, match="must NOT equal decision"):
            FeishuAlerter(
                feishu=None,
                renderer=MessageRenderer(),
                alert_chat_id=_ALERT_CHAT,
                decision_chat_id=_ALERT_CHAT,
            )

    def test_dedup_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="dedup_window"):
            FeishuAlerter(
                feishu=None,
                renderer=MessageRenderer(),
                alert_chat_id=_ALERT_CHAT,
                dedup_window=timedelta(seconds=0),
            )

    def test_legacy_custom_bot_env_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(
            logging.WARNING, logger="backend.integrations.feishu.alerter"
        )
        FeishuAlerter(
            feishu=None,
            renderer=MessageRenderer(),
            alert_chat_id=_ALERT_CHAT,
            env={"FEISHU_CUSTOM_BOT_WEBHOOK_URL": "https://example/legacy"},
        )
        assert any(
            "legacy_custom_bot_detected" in r.getMessage()
            for r in caplog.records
        )


# -----------------------------------------------------------------------------
# Alert types
# -----------------------------------------------------------------------------


class TestAlertTypes:
    def test_lock_count(self) -> None:
        """Adding a new type requires an amendment + this test update.

        J-004 added ``acceptance_reset_triggered`` (14); the Line-2 ops
        hardening amendment (2026-06-04) added
        ``line2_protective_sell_rejected`` bringing the total to 15.
        """
        assert len(ALERT_TYPES) == 15

    def test_includes_p1_7_budget_types(self) -> None:
        for kind in (
            "monthly_budget_50pct_reached",
            "monthly_budget_80pct_reached",
            "monthly_budget_100pct_reached",
            "daily_cost_ceiling_20cny_breached",
            "kimi_daily_cap_4cny_breached",
        ):
            assert kind in ALERT_TYPES

    def test_excludes_buysell_recon_clarification(self) -> None:
        """P1-7 §1.7 — alerter NEVER sends instruction / recon / clarification."""
        for forbidden in (
            "instruction_dispatched",
            "reconciliation_requested",
            "clarification_no_pattern",
        ):
            assert forbidden not in ALERT_TYPES


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_fire_dispatches_to_alert_chat(self) -> None:
        alerter, feishu = _build_alerter()
        result = await alerter.fire(
            alert_type="llm_all_providers_failed",
            severity="critical",
            message="3 LLM providers接连超时",
        )
        assert isinstance(result, AlertResult)
        assert result.sent is True
        assert result.suppressed is False
        assert result.reason == "dispatched"
        assert len(feishu.calls) == 1
        chat_id, body, kwargs = feishu.calls[0]
        assert chat_id == _ALERT_CHAT
        assert "【QuantMind 告警 / CRITICAL】" in body
        assert "llm_all_providers_failed" in body
        assert "alert-llm_all_providers_failed-" in kwargs["uuid"]

    @pytest.mark.asyncio
    async def test_default_severity_is_warning(self) -> None:
        alerter, feishu = _build_alerter()
        await alerter.fire(
            alert_type="scheduler_lag",
            message="lag spike",
        )
        assert "WARNING" in feishu.calls[0][1]

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self) -> None:
        alerter, _ = _build_alerter()
        with pytest.raises(ValueError, match="message"):
            await alerter.fire(
                alert_type="scheduler_lag",
                severity="warning",
                message="",
            )


# -----------------------------------------------------------------------------
# Dedup
# -----------------------------------------------------------------------------


class TestDedup:
    @pytest.mark.asyncio
    async def test_dedup_within_window(self) -> None:
        clock = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
        alerter, feishu = _build_alerter(
            dedup_window=timedelta(minutes=15),
            clock_now=clock,
        )
        first = await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="lag",
        )
        second = await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="lag again",
        )
        assert first.sent is True
        assert second.sent is False
        assert second.suppressed is True
        assert second.reason == "dedup_window"
        assert len(feishu.calls) == 1

    @pytest.mark.asyncio
    async def test_dedup_after_window_resends(self) -> None:
        clock = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
        alerter, feishu = _build_alerter(
            dedup_window=timedelta(minutes=15),
            clock_now=clock,
        )
        await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="lag",
        )
        # Simulate clock advance beyond window.
        future = clock + timedelta(minutes=16)
        second = await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="lag again",
            fired_at=future,
        )
        assert second.sent is True
        assert len(feishu.calls) == 2

    @pytest.mark.asyncio
    async def test_dedup_keys_independent(self) -> None:
        alerter, feishu = _build_alerter()
        await alerter.fire(
            alert_type="scheduler_lag",
            message="x",
            dedup_key="db",
        )
        await alerter.fire(
            alert_type="scheduler_lag",
            message="x",
            dedup_key="redis",
        )
        # Same alert type, different dedup_key — both dispatch.
        assert len(feishu.calls) == 2

    @pytest.mark.asyncio
    async def test_distinct_types_independent(self) -> None:
        alerter, feishu = _build_alerter()
        await alerter.fire(
            alert_type="scheduler_lag",
            message="x",
        )
        await alerter.fire(
            alert_type="circuit_breaker_open",
            message="y",
        )
        assert len(feishu.calls) == 2

    @pytest.mark.asyncio
    async def test_reset_clears_cooldown(self) -> None:
        alerter, feishu = _build_alerter()
        await alerter.fire(alert_type="scheduler_lag", message="x")
        alerter.reset()
        await alerter.fire(alert_type="scheduler_lag", message="y")
        assert len(feishu.calls) == 2

    @pytest.mark.asyncio
    async def test_reset_single_type(self) -> None:
        alerter, feishu = _build_alerter()
        await alerter.fire(alert_type="scheduler_lag", message="x")
        await alerter.fire(alert_type="circuit_breaker_open", message="y")
        alerter.reset(alert_type="scheduler_lag")
        # scheduler_lag cooldown cleared; circuit_breaker_open still cooling
        await alerter.fire(alert_type="scheduler_lag", message="x")
        await alerter.fire(alert_type="circuit_breaker_open", message="y")
        # 3 of the 4 fire attempts dispatched (the second circuit_breaker
        # is still in cooldown).
        assert len(feishu.calls) == 3


# -----------------------------------------------------------------------------
# Unknown / suppressed paths
# -----------------------------------------------------------------------------


class TestUnknownAlertType:
    @pytest.mark.asyncio
    async def test_unknown_type_suppressed(self) -> None:
        alerter, feishu = _build_alerter()
        result = await alerter.fire(
            alert_type="not_a_known_type",
            severity="warning",
            message="x",
        )
        assert result.sent is False
        assert result.suppressed is True
        assert result.reason == "unknown_alert_type"
        assert feishu.calls == []


# -----------------------------------------------------------------------------
# No-client path
# -----------------------------------------------------------------------------


class TestNoClient:
    @pytest.mark.asyncio
    async def test_no_client_returns_suppressed_no_client(self) -> None:
        alerter = FeishuAlerter(
            feishu=None,
            renderer=MessageRenderer(),
            alert_chat_id=_ALERT_CHAT,
        )
        result = await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="x",
        )
        assert result.sent is False
        assert result.suppressed is True
        assert result.reason == "no_client"


# -----------------------------------------------------------------------------
# Red lines
# -----------------------------------------------------------------------------


class TestRedLines:
    def test_no_llm_imports(self) -> None:
        import ast
        import pathlib

        path = pathlib.Path("backend/integrations/feishu/alerter.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                parts = (node.module or "").split(".")
                assert not (
                    parts[:1] == ["backend"]
                    and len(parts) >= 2
                    and parts[1] in {"llm", "agents", "mirofish"}
                ), f"forbidden import: {node.module}"

    @pytest.mark.asyncio
    async def test_alerter_never_uses_decision_chat(self) -> None:
        alerter, feishu = _build_alerter(decision_chat_id=_DECISION_CHAT)
        for kind in ("scheduler_lag", "circuit_breaker_open", "health_critical"):
            alerter.reset()  # avoid dedup-suppress between iterations
            await alerter.fire(
                alert_type=kind, severity="warning", message="x"
            )
        for chat_id, _, _ in feishu.calls:
            assert chat_id == _ALERT_CHAT
            assert chat_id != _DECISION_CHAT

    @pytest.mark.asyncio
    async def test_no_buysell_recon_clarification_text(self) -> None:
        """Even though the body is operator-supplied, the alerter rejects
        the alert types that would carry those concepts."""
        alerter, _ = _build_alerter()
        for forbidden_type in (
            "instruction_dispatched",
            "reconciliation_requested",
            "clarification_no_pattern",
        ):
            result = await alerter.fire(
                alert_type=forbidden_type,
                severity="warning",
                message="x",
            )
            assert result.sent is False
            assert result.reason == "unknown_alert_type"


# -----------------------------------------------------------------------------
# Send failure path
# -----------------------------------------------------------------------------


class TestSendFailure:
    @pytest.mark.asyncio
    async def test_api_error_returns_send_failed(self) -> None:
        feishu = _RecordingFeishu(fail=True)
        alerter, _ = _build_alerter(feishu=feishu)
        result = await alerter.fire(
            alert_type="scheduler_lag",
            severity="warning",
            message="x",
        )
        assert result.sent is False
        assert result.suppressed is False
        assert result.reason == "send_failed"
        # Send was attempted, just failed.
        assert len(feishu.calls) == 1
