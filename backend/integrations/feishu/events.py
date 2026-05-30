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

Connection lifecycle (the tricky part — see the 2026-05-29 fix):

``lark_oapi.ws.Client.start()`` is a *blocking, synchronous* call. It
drives a module-global event loop captured at import time and ends in
``loop.run_until_complete(_select())`` which never returns. Worse, when
``lark_oapi.ws.client`` is first imported inside a running event loop,
that module-global loop *is* the uvicorn loop — so ``start()`` calls
``run_until_complete`` on an already-running loop and dies with
``RuntimeError: this event loop is already running``.

The fix runs ``client.start()`` on a **dedicated daemon thread** that
owns its **own** event loop (and we rebind the SDK's module-global
``loop`` to that thread's loop, since every SDK coroutine references it
by name). The SDK invokes our event handler *synchronously* on that
thread, so the handler is a thin bridge that marshals the real work
back onto the main uvicorn loop via :func:`asyncio.run_coroutine_threadsafe`
— this keeps motor / redis clients on the loop they were created on and
returns immediately so the SDK's 3-second ack budget is honoured.

* :meth:`FeishuEventReceiver.start` captures the main loop, spawns the
  daemon thread, and returns. The SDK auto-reconnects on transient
  failures (``auto_reconnect=True`` upstream default).
* :meth:`stop` signals the thread to exit (stops its loop), joins it,
  then drains in-flight handler tasks on the main loop. ``start()`` is
  idempotent and re-callable after a stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
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
    """Subset of ``lark_oapi.ws.Client`` that the receiver needs.

    ``start`` is a *blocking, synchronous* call (it runs the SDK's WS
    loop until cancelled) — the receiver always invokes it on a
    dedicated daemon thread, never on the main event loop.
    """

    def start(self) -> None: ...


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
        # ``_uses_real_sdk`` gates the lark-specific loop bootstrap: only
        # the production (default) factory builds the real ``ws.Client``
        # whose blocking ``start()`` drives the SDK's module-global loop.
        # Injected test factories supply a self-contained blocking stub.
        self._uses_real_sdk = client_factory is None
        self._client_factory = client_factory or self._default_client_factory
        self._on_handler_error = on_handler_error or self._default_error_logger
        self._client: _WSClient | None = None
        # Main uvicorn loop, captured in start(); handler work is
        # marshalled back onto it from the WS thread.
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # The WS thread + the loop it owns (real SDK path only).
        self._thread: threading.Thread | None = None
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        # Set when stop() is requested so the thread's natural teardown
        # exception is not mis-logged as a crash.
        self._stopping = threading.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._app_id_fingerprint = compute_fingerprint(app_id)

    # -- Lifecycle ----------------------------------------------------

    async def start(self) -> None:
        """Begin the long-connection loop on a dedicated daemon thread.

        Idempotent: re-calling after :meth:`stop` reconnects; calling
        while already running is a no-op. The blocking SDK ``start()``
        runs off the main event loop so it can never collide with the
        running uvicorn loop.
        """
        if self._thread is not None and self._thread.is_alive():
            log.info(
                "feishu_event_receiver_already_running app_id_fingerprint=%s",
                self._app_id_fingerprint,
            )
            return
        self._main_loop = asyncio.get_running_loop()
        dispatcher = self._build_dispatcher()
        self._client = self._client_factory(dispatcher)
        self._stopping.clear()
        # Create the WS loop here (main thread) — NOT inside _serve — so
        # _thread_loop is set before the thread starts. Otherwise a stop()
        # racing an unstarted thread would find _thread_loop is None, skip
        # the stop signal, time out the join, null _thread, and let the
        # next start() open a second (duplicate) WS connection.
        if self._uses_real_sdk:
            loop = asyncio.new_event_loop()
            self._thread_loop = loop
            # Rebind the SDK's module-global loop (see module docstring).
            # The loop is not yet running, so a stop signal queued before
            # the thread starts is simply processed once it does.
            from lark_oapi.ws import client as _lark_ws_client

            _lark_ws_client.loop = loop
        else:
            self._thread_loop = None
        self._thread = threading.Thread(
            target=self._serve,
            name="feishu_event_receiver",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "feishu_event_receiver_started app_id_fingerprint=%s",
            self._app_id_fingerprint,
        )

    async def stop(self, *, handler_grace_seconds: float = 5.0) -> None:
        """Stop the WS thread + drain in-flight handlers.

        Signals the daemon thread to exit (stopping its loop for the
        real SDK, or calling the stub's ``stop`` in tests), joins it off
        the event loop, then drains in-flight handler tasks.

        Cycle 2 P2 fix: handlers may block on broker / repo / network
        work; without a timeout an unresponsive handler hangs shutdown
        indefinitely. Grant a short grace window for handlers to
        complete naturally, then cancel + gather any remainder with
        ``return_exceptions=True`` so a single bad handler does not
        block process exit.
        """
        if self._thread is None:
            return
        self._stopping.set()
        if self._thread.is_alive():
            self._signal_thread_stop()
            # Join off the event loop so shutdown of the running loop is
            # never blocked by the thread's teardown.
            await asyncio.to_thread(self._thread.join, handler_grace_seconds)
            if self._thread.is_alive():
                # Thread wedged past the grace window (e.g. SDK blocked in
                # a synchronous reconnect HTTP call). It is a daemon thread
                # so it dies with the process; surface it rather than leak
                # it silently.
                log.warning(
                    "feishu_event_receiver_thread_join_timeout "
                    "app_id_fingerprint=%s grace_seconds=%s",
                    self._app_id_fingerprint,
                    handler_grace_seconds,
                )
        self._thread = None
        self._thread_loop = None

        if not self._tasks:
            log.info(
                "feishu_event_receiver_stopped app_id_fingerprint=%s",
                self._app_id_fingerprint,
            )
            return

        # Snapshot before drain — handlers may add to / remove from
        # self._tasks via their own done_callback discard.
        in_flight = list(self._tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*in_flight, return_exceptions=True),
                timeout=handler_grace_seconds,
            )
        except TimeoutError:
            log.warning(
                "feishu_event_receiver_drain_timeout "
                "app_id_fingerprint=%s pending=%d",
                self._app_id_fingerprint,
                sum(1 for t in in_flight if not t.done()),
            )
            for task in in_flight:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*in_flight, return_exceptions=True)
        self._tasks.clear()
        log.info(
            "feishu_event_receiver_stopped app_id_fingerprint=%s",
            self._app_id_fingerprint,
        )

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- Internals ----------------------------------------------------

    def _serve(self) -> None:
        """Daemon-thread entrypoint: run the blocking SDK ``start()``.

        The WS loop was created and bound to the SDK's module-global in
        :meth:`start` (so a racing stop can never lose the loop handle);
        here we only adopt it as this thread's current loop, because
        ``ws.Client`` drives it via ``run_until_complete`` and its
        coroutines resolve the running loop. With our own loop the SDK can
        never collide with the main uvicorn loop.
        """
        assert self._client is not None
        loop = self._thread_loop  # set in start() for the real SDK path
        if loop is not None:
            asyncio.set_event_loop(loop)
        try:
            self._client.start()
        except Exception as exc:  # noqa: BLE001 — log unless we asked to stop
            if not self._stopping.is_set():
                log.warning(
                    "feishu_event_receiver_crashed app_id_fingerprint=%s "
                    "error_class=%s",
                    self._app_id_fingerprint,
                    exc.__class__.__name__,
                )
        finally:
            # Use the local handle (not self._thread_loop, which stop()
            # may have already cleared) so teardown is race-free.
            if loop is not None:
                try:
                    loop.close()
                except Exception:  # noqa: BLE001 — best-effort teardown
                    pass

    def _signal_thread_stop(self) -> None:
        """Ask the WS thread to exit, from the main event loop thread.

        Real SDK: stop its loop (``run_until_complete(_select())`` then
        raises and unwinds). Stub clients: call their ``stop``/
        ``request_stop`` hook if present.
        """
        if self._uses_real_sdk:
            loop = self._thread_loop
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
            return
        stop_fn = getattr(self._client, "request_stop", None) or getattr(
            self._client, "stop", None
        )
        if callable(stop_fn):
            stop_fn()

    def _on_sdk_event(self, event: Any) -> None:
        """Synchronous SDK callback — marshals work onto the main loop.

        lark-oapi invokes the registered handler *synchronously* on the
        WS thread and discards its return value, so an ``async`` handler
        would never be awaited. We instead schedule :meth:`_handle_event`
        on the main uvicorn loop (where motor / redis clients live) and
        return immediately, honouring the SDK's 3-second ack budget.
        """
        loop = self._main_loop
        if loop is None or loop.is_closed():
            log.warning(
                "feishu_event_dropped_no_main_loop app_id_fingerprint=%s",
                self._app_id_fingerprint,
            )
            return
        try:
            asyncio.run_coroutine_threadsafe(self._handle_event(event), loop)
        except RuntimeError:
            # The loop closed/stopped between the guard above and here
            # (shutdown race). Log a secret-free drop rather than letting
            # the error escape into the SDK's message loop, where it would
            # be mis-attributed as a generic SDK failure.
            log.warning(
                "feishu_event_dropped_loop_unavailable app_id_fingerprint=%s",
                self._app_id_fingerprint,
            )

    def _build_dispatcher(self) -> Any:
        """Construct the lark-oapi event dispatcher.

        Imports are deferred so unit tests that supply a stub
        ``client_factory`` never trigger the SDK's pkg_resources
        warning on collection. The handler is a *synchronous* bridge
        (see :meth:`_on_sdk_event`) because the SDK calls it inline.
        """
        from lark_oapi import EventDispatcherHandler  # local import

        return (
            EventDispatcherHandler.builder(
                self._encrypt_key, self._verify_token
            )
            .register_p2_im_message_receive_v1(self._on_sdk_event)
            .build()
        )

    def _default_client_factory(self, dispatcher: Any) -> _WSClient:
        """Build the real ``lark_oapi.ws.Client``.

        ``log_level=WARNING`` deliberately: the SDK's INFO ``connected to
        {url}`` line embeds the per-connection ``ticket`` / ``access_key``
        session credentials in plaintext, which CLAUDE.md §2.9 forbids in
        logs. Failures (``connect failed``, ``receive message loop exit``)
        still surface at WARNING/ERROR, so a dying connection stays
        observable without leaking the credential-bearing success URL.
        """
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
        # Cycle 2 P1: dedup both event_id AND message_id. Feishu's
        # standard redelivery reuses event_id, but a forwarded copy
        # gets a new event_id while keeping the same message_id —
        # without the secondary check a forwarded execution report
        # could double-apply. Namespaced keys prevent the two
        # streams from colliding.
        is_new_event = True
        is_new_message = True
        event_key = f"event:{message.event_id}"
        message_key = f"message:{message.message_id}"
        try:
            is_new_event = await self._dedupe.claim(event_key)
        except Exception as exc:  # noqa: BLE001 — fail-open on dedupe outage
            log.warning(
                "feishu_event_dedupe_unavailable error_class=%s "
                "event_id=%s",
                exc.__class__.__name__,
                message.event_id,
            )
        if is_new_event:
            try:
                is_new_message = await self._dedupe.claim(message_key)
            except Exception as exc:  # noqa: BLE001 — fail-open on dedupe outage
                log.warning(
                    "feishu_event_dedupe_unavailable_message error_class=%s "
                    "message_id=%s",
                    exc.__class__.__name__,
                    message.message_id,
                )
        if not (is_new_event and is_new_message):
            log.info(
                "feishu_event_dedupe_skip event_id=%s message_id=%s "
                "is_new_event=%s is_new_message=%s",
                message.event_id,
                message.message_id,
                is_new_event,
                is_new_message,
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
        # P0-4-amendment-2026-05-30: a group message only reaches the bot when
        # it @mentions the bot (the app lacks im:message.group_msg scope), and
        # Lark renders that mention in the text body as an "@_user_N"
        # placeholder carried in `mentions`. Strip those placeholders BEFORE the
        # strict execution-report regex — otherwise every owner reply is
        # AMBIGUOUS. Normalisation only; the regex is never relaxed (a body that
        # still does not match → AMBIGUOUS, fail-closed).
        mentions = self._safe_get(message, "mentions")
        text = _strip_mention_placeholders(text, mentions, self._safe_get)
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


def _strip_mention_placeholders(
    text: str,
    mentions: Any,
    getter: Callable[[Any, str], Any],
) -> str:
    """Strip **leading** Lark ``@_user_N`` @mention placeholder tokens.

    P0-4-amendment-2026-05-30. A group message only reaches the bot when it
    @mentions the bot (the app lacks the ``im:message.group_msg`` scope), and
    Lark renders that mention inside the text body as a placeholder key like
    ``@_user_1`` (followed by a space) whose exact string is carried in the
    event's ``mentions`` array. The strict execution-report regex
    (``re.fullmatch``) would never match a mention-prefixed body, so a leading
    run of these placeholders is removed here BEFORE parsing.

    Fail-closed by construction:

    * **Leading-only.** Only placeholders at the start of the body are removed
      (the realistic shape: owner @mentions the bot, then types the report).
      A placeholder appearing *inside* the free-text ``原因`` reason — or any
      mid-body position — is left untouched, so this can never silently delete
      content from an otherwise-valid report. A mid-body mention simply leaves
      the body non-matching → AMBIGUOUS downstream.
    * **Exact keys only**, taken from ``mentions`` (no fuzzy ``@xxx`` guessing),
      matched only when followed by whitespace or end-of-string so a token that
      merely shares a prefix is never eaten. Keys are tried longest-first so a
      shorter key (``@_user_1``) cannot partially strip a longer one
      (``@_user_10``).
    * **No other rewriting**: internal spacing of the report (including any
      double-spaces the owner typed inside a reason) is preserved; the regex is
      never relaxed; no ``instruction_id`` or numeric field is inferred.

    Returns the input unchanged when there are no mentions.
    """
    if not text or not mentions:
        return text
    keys: list[str] = []
    try:
        for mention in mentions:
            key = getter(mention, "key")
            if isinstance(key, str) and key:
                keys.append(key)
    except TypeError:
        # mentions was not iterable (malformed envelope) — leave text as-is.
        return text
    if not keys:
        return text
    keys.sort(key=len, reverse=True)  # longest-first: avoid prefix collisions
    remaining = text.lstrip()
    changed = True
    while changed:
        changed = False
        for key in keys:
            if not remaining.startswith(key):
                continue
            rest = remaining[len(key):]
            # Only a token boundary (whitespace or end) counts as a mention —
            # never swallow a longer token that merely starts with the key.
            if rest and not rest[0].isspace():
                continue
            remaining = rest.lstrip()
            changed = True
            break
    return remaining


__all__ = [
    "FeishuEventReceiver",
    "MessageHandler",
    "ReceivedMessage",
]
