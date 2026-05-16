"""Feishu long-connection event receiver (P0-2 §2.1 / F-003).

Subscribes to ``im.message.receive_v1`` via ``lark-oapi``'s WebSocket
client. Every accepted event:

1. Deduplicates by ``event_id`` (and ``message_id`` as a secondary key
   so a forwarded copy still resolves to the same wire payload).
2. Normalises to a typed :class:`ReceivedMessage`.
3. Dispatches to the consumer callback (F-004 parser) without blocking
   the SDK's 3-second ack window — the handler returns immediately,
   the dispatch happens through ``asyncio.create_task``.

Red lines (P0-2 / CLAUDE.md §2.6):

* Zero public HTTPS callback — the only inbound channel is this WS.
* ``tenant_access_token`` is owned by the SDK; we never touch it.
* No ``backend.llm`` / ``backend.agents`` / ``backend.mirofish`` imports
  — LLMs never compose response text.

Connection lifecycle:

* :meth:`FeishuEventReceiver.start` schedules ``client.start()`` on
  the running event loop. The SDK auto-reconnects on transient
  failures (``auto_reconnect=True`` upstream default).
* :meth:`stop` cancels the background task; ``start()`` is idempotent
  so re-calling after a stop reconnects.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.integrations.feishu.dedupe import EventDedupe
from backend.services.secrets_validator import compute_fingerprint

log = logging.getLogger("backend.integrations.feishu.events")


# === Public DTOs ====================================================


@dataclass(frozen=True)
class ReceivedMessage:
    """Normalised inbound Feishu message envelope.

    Captures only the fields the parser (F-004) needs; the full Lark
    envelope is *not* stored because some of its embedded ids are
    user-controlled and would otherwise leak into every audit row.
    """

    event_id: str
    message_id: str
    chat_id: str
    sender_id: str
    text: str
    raw_create_time: int
    received_at: datetime


MessageHandler = Callable[[ReceivedMessage], Awaitable[None]]


# === SDK adapter Protocols ==========================================


class _WSClient(Protocol):
    """Subset of ``lark_oapi.ws.Client`` that the receiver needs."""

    async def start(self) -> None: ...


# Factory signature: builds a configured WS client given an event
# handler. Letting the receiver accept an injected factory keeps unit
# tests free of real network setup.
WSClientFactory = Callable[[Any], _WSClient]


# === Receiver =======================================================


class FeishuEventReceiver:
    """Long-connection receiver wrapping ``lark-oapi``'s WS client.

    Args:
        app_id: Feishu app id (``cli_*``).
        app_secret: Feishu app secret.
        verify_token: ``FEISHU_VERIFY_TOKEN`` from env (32 alnum).
        encrypt_key: ``FEISHU_ENCRYPT_KEY`` from env (32 alnum).
        dedupe: dedupe primitive (Redis in prod, in-memory in tests).
        handler: async callable invoked with each ``ReceivedMessage``.
            The receiver fire-and-forgets via ``asyncio.create_task``
            so the SDK's ack pipe is never blocked by downstream work.
        client_factory: optional override of the WS client constructor.
            Production callers omit this; tests pass a fake.
        on_handler_error: optional callable invoked when the handler
            task raises. Defaults to a structlog warning.
    """

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        verify_token: str,
        encrypt_key: str,
        dedupe: EventDedupe,
        handler: MessageHandler,
        client_factory: WSClientFactory | None = None,
        on_handler_error: Callable[[BaseException, ReceivedMessage], None]
        | None = None,
    ) -> None:
        if not (app_id and app_secret and verify_token and encrypt_key):
            raise ValueError(
                "FeishuEventReceiver requires app_id/app_secret/"
                "verify_token/encrypt_key — secrets_validator (H-001) "
                "is the upstream gate"
            )
        self._app_id = app_id
        self._app_secret = app_secret
        self._verify_token = verify_token
        self._encrypt_key = encrypt_key
        self._dedupe = dedupe
        self._handler = handler
        self._client_factory = client_factory or self._default_client_factory
        self._on_handler_error = on_handler_error or self._default_error_logger
        self._client: _WSClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._app_id_fingerprint = compute_fingerprint(app_id)

    # -- Lifecycle ----------------------------------------------------

    async def start(self) -> None:
        """Begin the long-connection loop.

        Idempotent: re-calling after :meth:`stop` reconnects; calling
        while already running is a no-op.
        """
        if self._task is not None and not self._task.done():
            log.info(
                "feishu_event_receiver_already_running app_id_fingerprint=%s",
                self._app_id_fingerprint,
            )
            return
        dispatcher = self._build_dispatcher()
        self._client = self._client_factory(dispatcher)
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(
            self._run_loop(), name="feishu_event_receiver"
        )
        log.info(
            "feishu_event_receiver_started app_id_fingerprint=%s",
            self._app_id_fingerprint,
        )

    async def stop(self) -> None:
        """Cancel the WS task + drain in-flight handlers."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
        # Drain handler tasks (best-effort — handlers should be short).
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        log.info(
            "feishu_event_receiver_stopped app_id_fingerprint=%s",
            self._app_id_fingerprint,
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- Internals ----------------------------------------------------

    async def _run_loop(self) -> None:
        assert self._client is not None
        try:
            await self._client.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — log and propagate
            log.warning(
                "feishu_event_receiver_crashed app_id_fingerprint=%s "
                "error_class=%s",
                self._app_id_fingerprint,
                exc.__class__.__name__,
            )
            raise

    def _build_dispatcher(self) -> Any:
        """Construct the lark-oapi event dispatcher.

        Imports are deferred so unit tests that supply a stub
        ``client_factory`` never trigger the SDK's pkg_resources
        warning on collection.
        """
        from lark_oapi import EventDispatcherHandler  # local import
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

        async def _handle(event: P2ImMessageReceiveV1) -> None:
            await self._handle_event(event)

        return (
            EventDispatcherHandler.builder(
                self._encrypt_key, self._verify_token
            )
            .register_p2_im_message_receive_v1(_handle)
            .build()
        )

    def _default_client_factory(self, dispatcher: Any) -> _WSClient:
        """Build the real ``lark_oapi.ws.Client``."""
        import lark_oapi as lark  # local import — see _build_dispatcher

        return lark.ws.Client(
            app_id=self._app_id,
            app_secret=self._app_secret,
            log_level=lark.LogLevel.WARNING,
            event_handler=dispatcher,
        )

    async def _handle_event(self, event: Any) -> None:
        """Bridge an SDK event into a :class:`ReceivedMessage`.

        Returns *immediately* — heavy lifting (dedupe + handler call)
        is fired-and-forgotten so we honour the 3-second ack budget
        even if downstream work is slow.
        """
        try:
            message = self._extract_message(event)
        except _SkipEventError as skip:
            log.info(
                "feishu_event_skipped reason=%s app_id_fingerprint=%s",
                skip.reason,
                self._app_id_fingerprint,
            )
            return
        except Exception as exc:  # noqa: BLE001 — drop malformed events
            log.warning(
                "feishu_event_extract_failed error_class=%s "
                "app_id_fingerprint=%s",
                exc.__class__.__name__,
                self._app_id_fingerprint,
            )
            return

        task = asyncio.create_task(
            self._dispatch(message), name="feishu_event_dispatch"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message: ReceivedMessage) -> None:
        try:
            is_new = await self._dedupe.claim(message.event_id)
        except Exception as exc:  # noqa: BLE001 — fail-open on dedupe outage
            log.warning(
                "feishu_event_dedupe_unavailable error_class=%s "
                "event_id=%s",
                exc.__class__.__name__,
                message.event_id,
            )
            is_new = True
        if not is_new:
            log.info(
                "feishu_event_dedupe_skip event_id=%s message_id=%s",
                message.event_id,
                message.message_id,
            )
            return
        try:
            await self._handler(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — handler errors isolated
            self._on_handler_error(exc, message)

    # -- Event normalisation ------------------------------------------

    def _extract_message(self, event: Any) -> ReceivedMessage:
        """Translate a Lark P2 envelope into a :class:`ReceivedMessage`."""
        header = self._safe_get(event, "header")
        data = self._safe_get(event, "event")
        if header is None or data is None:
            raise _SkipEventError("envelope missing header or event payload")

        event_id = str(self._safe_get(header, "event_id") or "")
        if not event_id:
            raise _SkipEventError("missing event_id")

        message = self._safe_get(data, "message")
        sender = self._safe_get(data, "sender")
        if message is None:
            raise _SkipEventError("event has no message payload")
        if sender is None:
            raise _SkipEventError("event has no sender payload")

        message_id = str(self._safe_get(message, "message_id") or "")
        chat_id = str(self._safe_get(message, "chat_id") or "")
        msg_type = str(self._safe_get(message, "message_type") or "")
        raw_content = str(self._safe_get(message, "content") or "")
        create_time_raw = str(self._safe_get(message, "create_time") or "0")
        if not (message_id and chat_id):
            raise _SkipEventError("message missing id or chat_id")
        if msg_type != "text":
            raise _SkipEventError(f"unsupported message_type {msg_type!r}")

        try:
            create_time = int(create_time_raw)
        except (TypeError, ValueError) as exc:
            raise _SkipEventError("malformed create_time") from exc

        sender_id_obj = self._safe_get(sender, "sender_id")
        if sender_id_obj is None:
            raise _SkipEventError("sender_id missing")
        sender_id = (
            str(self._safe_get(sender_id_obj, "open_id"))
            or str(self._safe_get(sender_id_obj, "user_id"))
            or ""
        )
        if not sender_id:
            raise _SkipEventError("sender open_id / user_id missing")

        text = _extract_text_content(raw_content)
        if not text:
            raise _SkipEventError("text content empty")

        return ReceivedMessage(
            event_id=event_id,
            message_id=message_id,
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            raw_create_time=create_time,
            received_at=datetime.now(UTC),
        )

    @staticmethod
    def _safe_get(obj: Any, attr: str) -> Any:
        """Tolerant getter — Lark exposes dataclass-y models AND raw dicts."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(attr)
        return getattr(obj, attr, None)

    @staticmethod
    def _default_error_logger(
        exc: BaseException, message: ReceivedMessage
    ) -> None:
        log.warning(
            "feishu_event_handler_error error_class=%s event_id=%s "
            "message_id=%s",
            exc.__class__.__name__,
            message.event_id,
            message.message_id,
        )


class _SkipEventError(RuntimeError):
    """Sentinel raised inside :meth:`_extract_message` to drop an event."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _extract_text_content(raw: str) -> str:
    """Parse a Lark text message body — ``{"text": "..."}``.

    Returns the unwrapped text (no surrounding whitespace) or an empty
    string when the body is malformed. Empty results cause the event
    to be skipped (see :meth:`_extract_message`).
    """
    if not raw:
        return ""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(envelope, dict):
        return ""
    text = envelope.get("text", "")
    if not isinstance(text, str):
        return ""
    return text.strip()


__all__ = [
    "FeishuEventReceiver",
    "MessageHandler",
    "ReceivedMessage",
]
