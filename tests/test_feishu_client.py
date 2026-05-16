"""Tests for the F-001 FeishuClient wrapper.

Covers the construction matrix (factory + direct ctor), env gating,
happy path / API error / transport error code paths, plus the audit
red lines (chat_id is logged as SHA256[:8] fingerprint only;
tenant_access_token is never touched by our code).
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from backend.integrations.feishu.client import (
    FeishuApiError,
    FeishuClient,
    SendMessageResult,
    _extract_message_id,
)

# Test fixtures matching the H-001 shape validators.
VALID_APP_ID = "cli_" + "a" * 16
VALID_APP_SECRET = "x" * 32
VALID_ALERT_CHAT = "oc_" + "f" * 32


def _baseline_env() -> dict[str, str]:
    """Process env mirror with Feishu credentials but flag off."""
    return {
        "FEISHU_APP_ID": VALID_APP_ID,
        "FEISHU_APP_SECRET": VALID_APP_SECRET,
        "FEISHU_VERIFY_TOKEN": "v" * 32,
        "FEISHU_ENCRYPT_KEY": "e" * 32,
        "FEISHU_ALERT_CHAT_ID": VALID_ALERT_CHAT,
    }


def _ok_response(message_id: str = "om_test_abc", log_id: str = "log_abc") -> Any:  # type: ignore[name-defined]
    """Mimic the lark-oapi CreateMessageResponse minimal surface."""
    return SimpleNamespace(
        code=0,
        msg="success",
        data=SimpleNamespace(message_id=message_id),
        get_log_id=lambda: log_id,
    )


def _api_error_response(code: int, msg: str) -> Any:  # type: ignore[name-defined]
    return SimpleNamespace(
        code=code,
        msg=msg,
        data=None,
        get_log_id=lambda: f"log_{code}",
    )


Any = type("Any", (), {})  # avoid noqa shenanigans in helper sigs


# -----------------------------------------------------------------------------
# Construction matrix
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_direct_constructor_accepts_valid_creds(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        assert client.app_id_fingerprint  # 8-hex SHA prefix
        assert len(client.app_id_fingerprint) == 8

    def test_direct_constructor_rejects_blank_app_id(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        with pytest.raises(ValueError, match="app_id"):
            FeishuClient(app_id="", app_secret=VALID_APP_SECRET, acreate=_stub)

    def test_direct_constructor_rejects_blank_secret(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        with pytest.raises(ValueError, match="app_id"):
            FeishuClient(app_id=VALID_APP_ID, app_secret="", acreate=_stub)


# -----------------------------------------------------------------------------
# from_env: feishu-off path returns None, on path returns ready client
# -----------------------------------------------------------------------------


class TestFromEnv:
    def test_feishu_off_returns_none(self) -> None:
        env = _baseline_env()  # FEISHU_INTERACTIVE_ENABLED unset
        client = FeishuClient.from_env(env=env)
        assert client is None

    @pytest.mark.parametrize("token", ["false", "0", "no", "off", "", "  "])
    def test_feishu_off_aliases(self, token: str) -> None:
        env = _baseline_env()
        env["FEISHU_INTERACTIVE_ENABLED"] = token
        assert FeishuClient.from_env(env=env) is None

    @pytest.mark.parametrize("token", ["true", "1", "yes", "on", "TRUE"])
    def test_feishu_on_aliases_build_client(self, token: str) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        env = _baseline_env()
        env["FEISHU_INTERACTIVE_ENABLED"] = token
        client = FeishuClient.from_env(env=env, acreate=_stub)
        assert isinstance(client, FeishuClient)

    def test_feishu_on_missing_credential_raises(self) -> None:
        """Validator/client race — if validator passed but env lost a
        credential before from_env ran, surface it loudly."""
        async def _stub(_req: object) -> object:
            return _ok_response()

        env = _baseline_env()
        env["FEISHU_INTERACTIVE_ENABLED"] = "true"
        env.pop("FEISHU_APP_SECRET")
        with pytest.raises(FeishuApiError, match="FEISHU_APP_SECRET"):
            FeishuClient.from_env(env=env, acreate=_stub)


# -----------------------------------------------------------------------------
# send_message — happy path
# -----------------------------------------------------------------------------


class TestSendMessageHappyPath:
    @pytest.mark.asyncio
    async def test_returns_ok_result(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response(message_id="om_happy")

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        result = await client.send_message(VALID_ALERT_CHAT, "hello world")
        assert result == SendMessageResult(
            ok=True,
            code=0,
            msg="success",
            message_id="om_happy",
            log_id="log_abc",
        )

    @pytest.mark.asyncio
    async def test_wraps_content_in_json_envelope(self) -> None:
        captured: dict[str, object] = {}

        async def _stub(request: object) -> object:
            body = request.request_body  # type: ignore[attr-defined]
            captured["content"] = body.content
            captured["msg_type"] = body.msg_type
            captured["receive_id"] = body.receive_id
            captured["receive_id_type"] = request.receive_id_type  # type: ignore[attr-defined]
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        await client.send_message(VALID_ALERT_CHAT, "中文消息正文")
        assert captured["receive_id"] == VALID_ALERT_CHAT
        assert captured["receive_id_type"] == "chat_id"
        assert captured["msg_type"] == "text"
        # content is JSON envelope, not raw string.
        assert json.loads(captured["content"]) == {"text": "中文消息正文"}

    @pytest.mark.asyncio
    async def test_uuid_passed_through(self) -> None:
        captured: dict[str, object] = {}

        async def _stub(request: object) -> object:
            captured["uuid"] = getattr(request.request_body, "uuid", None)  # type: ignore[attr-defined]
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        await client.send_message(
            VALID_ALERT_CHAT, "with uuid", uuid="recon-2026-05-16-1"
        )
        assert captured["uuid"] == "recon-2026-05-16-1"


# -----------------------------------------------------------------------------
# send_message — API error (code != 0)
# -----------------------------------------------------------------------------


class TestSendMessageApiError:
    @pytest.mark.asyncio
    async def test_api_error_returns_structured_result(self) -> None:
        async def _stub(_req: object) -> object:
            return _api_error_response(code=99991663, msg="rate limited")

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        result = await client.send_message(VALID_ALERT_CHAT, "x")
        assert result.ok is False
        assert result.code == 99991663
        assert result.msg == "rate limited"
        assert result.message_id is None
        assert result.log_id == "log_99991663"

    @pytest.mark.asyncio
    async def test_api_error_does_not_raise(self) -> None:
        async def _stub(_req: object) -> object:
            return _api_error_response(code=230001, msg="bad chat_id")

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        result = await client.send_message(VALID_ALERT_CHAT, "x")
        # API errors are expected outcomes, surfaced via result.ok=False.
        assert result.ok is False


# -----------------------------------------------------------------------------
# send_message — transport / network error
# -----------------------------------------------------------------------------


class TestSendMessageTransportError:
    @pytest.mark.asyncio
    async def test_transport_error_raises_feishu_api_error(self) -> None:
        async def _stub(_req: object) -> object:
            raise ConnectionError("network down")

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        with pytest.raises(FeishuApiError) as excinfo:
            await client.send_message(VALID_ALERT_CHAT, "x")
        assert "transport error" in str(excinfo.value)
        # The underlying ConnectionError is preserved as __cause__.
        assert isinstance(excinfo.value.__cause__, ConnectionError)

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self) -> None:
        async def _stub(_req: object) -> object:
            raise asyncio.CancelledError()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        with pytest.raises(asyncio.CancelledError):
            await client.send_message(VALID_ALERT_CHAT, "x")


# -----------------------------------------------------------------------------
# Input validation
# -----------------------------------------------------------------------------


class TestSendMessageInputValidation:
    @pytest.mark.asyncio
    async def test_blank_chat_id_rejected(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        with pytest.raises(ValueError, match="chat_id"):
            await client.send_message("", "x")

    @pytest.mark.asyncio
    async def test_blank_content_rejected(self) -> None:
        async def _stub(_req: object) -> object:
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        with pytest.raises(ValueError, match="content"):
            await client.send_message(VALID_ALERT_CHAT, "")

    @pytest.mark.asyncio
    async def test_non_text_msg_type_rejected(self) -> None:
        """P0-2 §2.5 — phase 1 is plain text only."""
        async def _stub(_req: object) -> object:
            return _ok_response()

        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        with pytest.raises(ValueError, match="text"):
            await client.send_message(
                VALID_ALERT_CHAT, "x", msg_type="interactive"
            )


# -----------------------------------------------------------------------------
# Red-line guarantees
# -----------------------------------------------------------------------------


class TestRedLines:
    @pytest.mark.asyncio
    async def test_chat_id_redacted_in_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Plain chat_id never appears in log output — only the fingerprint."""
        async def _stub(_req: object) -> object:
            return _ok_response()

        caplog.set_level(logging.INFO, logger="backend.integrations.feishu.client")
        client = FeishuClient(
            app_id=VALID_APP_ID, app_secret=VALID_APP_SECRET, acreate=_stub
        )
        await client.send_message(VALID_ALERT_CHAT, "x")
        # Plain chat_id never logged.
        for record in caplog.records:
            assert VALID_ALERT_CHAT not in record.getMessage()

    def test_module_imports_are_isolated(self) -> None:
        """LLM red line — Feishu client never pulls in llm/agents/mirofish."""
        import ast
        import pathlib

        path = pathlib.Path("backend/integrations/feishu/client.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden = {"llm", "agents", "mirofish"}
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                parts = mod.split(".")
                if parts[:1] == ["backend"] and len(parts) >= 2:
                    if parts[1] in forbidden:
                        violations.append(f"from {mod} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[:1] == ["backend"] and len(parts) >= 2:
                        if parts[1] in forbidden:
                            violations.append(f"import {alias.name}")
        assert violations == []

    def test_source_never_calls_get_tenant_access_token(self) -> None:
        """The SDK keeps tenant_access_token in memory. Our source must
        never explicitly fetch / log / persist it (P0-2 §2.6 / red line 1).
        """
        import ast
        import pathlib

        path = pathlib.Path("backend/integrations/feishu/client.py")
        text = path.read_text(encoding="utf-8")
        # The SDK helper that returns the raw token is forbidden in our code.
        assert "get_tenant_access_token" not in text

        # tenant_access_token may appear in the module docstring (it's
        # an explicit red-line callout) but never as live code. Walk
        # the AST and confirm every textual occurrence belongs to a
        # docstring or comment, not to a Name / Call / Attribute node.
        tree = ast.parse(text)
        executable_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "tenant_access_token":
                executable_lines.add(node.lineno)
            elif isinstance(node, ast.Attribute) and node.attr == "tenant_access_token":
                executable_lines.add(node.lineno)
        assert executable_lines == set(), (
            f"tenant_access_token referenced as live code on lines "
            f"{sorted(executable_lines)}"
        )


# -----------------------------------------------------------------------------
# _extract_message_id helper
# -----------------------------------------------------------------------------


class TestExtractMessageId:
    def test_none_data_returns_none(self) -> None:
        assert _extract_message_id(None) is None

    def test_attribute_access(self) -> None:
        data = SimpleNamespace(message_id="om_via_attr")
        assert _extract_message_id(data) == "om_via_attr"

    def test_dict_access(self) -> None:
        assert _extract_message_id({"message_id": "om_via_dict"}) == "om_via_dict"

    def test_empty_string_treated_as_none(self) -> None:
        assert _extract_message_id(SimpleNamespace(message_id="")) is None

    def test_missing_field_returns_none(self) -> None:
        assert _extract_message_id(SimpleNamespace(other="x")) is None
