"""Feishu OpenAPI client wrapper (P0-2 / F-001).

A thin async wrapper around :mod:`lark_oapi` that exposes one method —
:meth:`FeishuClient.send_message` — used by every outbound channel:

* :mod:`backend.monitoring.alerter` (F-006) — system alerts to
  ``FEISHU_ALERT_CHAT_ID``.
* :mod:`backend.integrations.feishu.renderer` (F-002) — outbound order
  instructions / reconciliation requests / clarification prompts to the
  decision chat (different ``chat_id`` than the alert group).

Red lines (CLAUDE.md §2.6 / P0-2):

1. ``tenant_access_token`` is **never** persisted or logged. ``lark-oapi``
   keeps it in an in-memory cache by default; we do not pass a custom
   ``cache=`` so the token cannot reach disk.
2. ``FEISHU_INTERACTIVE_ENABLED=false`` boot path: :meth:`from_env`
   returns ``None`` so the application does not require Feishu
   credentials in pure simulation_auto mode.
3. LLM isolation: this module imports zero ``backend.llm`` /
   ``backend.agents`` / ``backend.mirofish``. The content argument is
   wire-ready text supplied by :class:`MessageRenderer` (F-002), never
   composed by an LLM (P0-2 §1.2).
4. Plaintext credentials never appear in logs. The ``chat_id`` is
   redacted to a SHA256[:8] fingerprint on every log line; only the
   message ``msg_id`` and the API ``log_id`` (used by Feishu support to
   trace requests) surface in cleartext.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from backend.services.secrets_validator import (
    FEISHU_CREDENTIAL_NAMES,
    compute_fingerprint,
)

log = logging.getLogger("backend.integrations.feishu.client")


# --- Result DTOs -----------------------------------------------------


@dataclass(frozen=True)
class SendMessageResult:
    """Outcome of a single :meth:`FeishuClient.send_message` call.

    Attributes:
        ok: ``True`` iff the API returned ``code == 0``.
        code: Feishu API error code (0 = success).
        msg: Feishu API error message (English summary).
        message_id: Feishu ``message_id`` of the delivered message
            (``None`` when ``ok`` is False).
        log_id: The ``X-Tt-Logid`` response header — given verbatim to
            Feishu support if the operator needs to trace a delivery.
    """

    ok: bool
    code: int
    msg: str
    message_id: str | None
    log_id: str | None


class FeishuApiError(RuntimeError):
    """Raised for transport-level failures (network / JSON parse).

    API-level failures (``code != 0``) flow through the structured
    :class:`SendMessageResult` instead — they are *expected* outcomes
    the caller has to handle, not exceptions.
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


# --- lark-oapi adapter Protocol (testability) -----------------------


class _MessageAcreate(Protocol):
    """Minimal contract satisfied by ``Client.im.v1.message.acreate``.

    Letting the test suite supply an arbitrary async callable keeps the
    unit tests free of ``lark-oapi`` import side-effects (its
    ``pkg_resources`` deprecation warning + protobuf import cost a
    real fraction of a second the first time the package is loaded).
    """

    async def __call__(self, request: Any) -> Any: ...


# --- Client ----------------------------------------------------------


class FeishuClient:
    """Async client for ``POST /open-apis/im/v1/messages``.

    Instances are cheap to construct and **stateless** — the underlying
    ``lark-oapi`` ``Client`` keeps the tenant_access_token in memory and
    refreshes it on demand. Concurrency is safe (the SDK's HTTP
    transport is thread-safe and the in-memory cache uses an internal
    lock).

    Args:
        app_id: ``cli_*`` Feishu app id.
        app_secret: 32-char Feishu app secret.
        timeout: per-request timeout in seconds (SDK default 30s).
        acreate: optional override for the underlying
            ``client.im.v1.message.acreate`` callable. Production code
            never passes this; tests use it to stub the wire path.
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        timeout: float = 30.0,
        acreate: _MessageAcreate | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError(
                "FeishuClient requires non-empty app_id and app_secret"
            )
        self._app_id = app_id
        self._app_id_fingerprint = compute_fingerprint(app_id)
        self._timeout = timeout
        self._acreate = acreate or self._build_acreate(app_id, app_secret, timeout)

    @staticmethod
    def _build_acreate(
        app_id: str, app_secret: str, timeout: float
    ) -> _MessageAcreate:
        """Construct the lark-oapi acreate callable lazily.

        ``lark_oapi`` import is deferred so unit tests that pass a stub
        ``acreate`` callable never trigger the SDK's pkg_resources
        warning. Production code paths still load the SDK on first
        construct.
        """
        import lark_oapi as lark

        client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.WARNING)  # never INFO — leaks tokens
            .timeout(timeout)
            .build()
        )
        return client.im.v1.message.acreate  # type: ignore[no-any-return]

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        timeout: float = 30.0,
        acreate: _MessageAcreate | None = None,
    ) -> FeishuClient | None:
        """Construct from process env iff Feishu overlay is configured.

        Returns ``None`` when ``FEISHU_INTERACTIVE_ENABLED`` resolves to
        a falsy token so the lifespan code path in main.py can skip the
        client without branching on ``app_id`` itself. Returns a
        ready-to-use client otherwise.

        Validation of the credential pool happens in the secrets
        validator (H-001) before this factory runs; this method assumes
        env values are already shape-checked.
        """
        source = env if env is not None else os.environ
        if source.get("FEISHU_INTERACTIVE_ENABLED", "").strip().lower() not in {
            "true",
            "1",
            "yes",
            "on",
        }:
            return None
        # H-001 has already verified these are present + shape-correct;
        # any miss here means the operator changed env between the
        # validator and this call — fail loudly so it is fixed.
        missing = [
            name
            for name in FEISHU_CREDENTIAL_NAMES
            if not source.get(name, "").strip()
        ]
        if missing:
            raise FeishuApiError(
                "FeishuClient.from_env: missing credentials "
                f"{missing!r} after secrets_validator passed — race "
                "between validator and client construction"
            )
        return cls(
            app_id=source["FEISHU_APP_ID"].strip(),
            app_secret=source["FEISHU_APP_SECRET"].strip(),
            timeout=timeout,
            acreate=acreate,
        )

    # -- Outbound message API --------------------------------------------

    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        msg_type: str = "text",
        uuid: str | None = None,
    ) -> SendMessageResult:
        """Send a text message to ``chat_id`` via OpenAPI.

        Args:
            chat_id: target chat id; expected shape ``oc_<32 alnum>``.
                Validated by length only because the alert path uses a
                fixed value from env while the decision-group path will
                come from operator configuration.
            content: wire-ready text. The Feishu API requires this to
                be wrapped in a small JSON envelope (``{"text": ...}``
                for text type); we serialise here so callers can pass
                the raw template string from :class:`MessageRenderer`.
            msg_type: ``text`` is the only supported type today (P0-2
                §2.5 — text-only first phase). The argument exists so
                F-005 reconciliation cards can later upgrade to
                ``interactive`` without changing the API shape.
            uuid: optional idempotency key. Feishu uses this server-side
                to dedupe retries within a 1-hour window; pass the same
                value across retries.

        Returns:
            :class:`SendMessageResult` describing whether the API
            accepted the message. Transport-level failures raise
            :class:`FeishuApiError`.
        """
        if not chat_id or len(chat_id) < 4:
            raise ValueError(
                "chat_id must be a non-empty Feishu open_chat_id (oc_...)"
            )
        if not content:
            raise ValueError("content must not be empty")
        if msg_type != "text":
            raise ValueError(
                f"unsupported msg_type {msg_type!r} — P0-2 §2.5 locks "
                "phase 1 to plain text"
            )

        # Build the lark-oapi request. ``content`` for ``text`` type is
        # a JSON string body — Feishu's API contract, not ours.
        from lark_oapi.api.im.v1 import (  # local import: keep test import cost low
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        body_builder = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(json.dumps({"text": content}, ensure_ascii=False))
        )
        if uuid is not None:
            body_builder = body_builder.uuid(uuid)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body_builder.build())
            .build()
        )

        chat_id_fp = compute_fingerprint(chat_id)

        try:
            response = await self._acreate(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — wrap any transport error
            log.warning(
                "feishu_send_message_transport_error chat_id_fingerprint=%s "
                "error_class=%s",
                chat_id_fp,
                exc.__class__.__name__,
            )
            raise FeishuApiError(
                f"feishu transport error: {exc.__class__.__name__}",
                cause=exc,
            ) from exc

        code = getattr(response, "code", None)
        msg = getattr(response, "msg", "") or ""
        log_id_method = getattr(response, "get_log_id", None)
        log_id = log_id_method() if callable(log_id_method) else None
        message_id = _extract_message_id(getattr(response, "data", None))

        ok = code == 0
        log_method = log.info if ok else log.warning
        log_method(
            "feishu_send_message chat_id_fingerprint=%s app_id_fingerprint=%s "
            "ok=%s code=%s log_id=%s message_id=%s",
            chat_id_fp,
            self._app_id_fingerprint,
            ok,
            code,
            log_id,
            message_id,
        )

        return SendMessageResult(
            ok=ok,
            code=code if code is not None else -1,
            msg=msg,
            message_id=message_id,
            log_id=log_id,
        )

    # -- Introspection (used by audit + monitoring) ----------------------

    @property
    def app_id_fingerprint(self) -> str:
        """SHA256[:8] of the configured ``app_id`` — safe to log."""
        return self._app_id_fingerprint


# --- helpers --------------------------------------------------------


def _extract_message_id(data: Any) -> str | None:
    """Pull the message_id out of a Feishu CreateMessageResponseBody."""
    if data is None:
        return None
    # lark-oapi exposes the field both as a Python attribute and via
    # the dict-style body; we accept either so tests can supply a
    # plain SimpleNamespace / dict.
    if hasattr(data, "message_id"):
        candidate = data.message_id
        if isinstance(candidate, str) and candidate:
            return candidate
    if isinstance(data, dict):
        candidate = data.get("message_id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


__all__ = [
    "FeishuApiError",
    "FeishuClient",
    "SendMessageResult",
]
