"""F-003 — FeishuEventReceiver + dedupe tests.

Covers the receiver lifecycle, event normalisation, dedupe behaviour
(both Redis and in-memory implementations), and the red-line
guarantees (no LLM imports, 3s ack budget honoured by fire-and-forget).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.integrations.feishu.dedupe import (
    InMemoryEventDedupe,
    RedisEventDedupe,
)
from backend.integrations.feishu.events import (
    FeishuEventReceiver,
    ReceivedMessage,
    _extract_text_content,
)

VALID_APP_ID = "cli_" + "a" * 16
VALID_APP_SECRET = "x" * 32
VALID_VERIFY = "v" * 32
VALID_ENCRYPT = "e" * 32


def _build_event(
    *,
    event_id: str = "ev_abc",
    message_id: str = "om_xyz",
    chat_id: str = "oc_chat_1",
    sender_open_id: str = "ou_sender_1",
    text: str = (
        "已执行 QM-20260516-103000-510300-BUY-001 买入 510300 "
        "1000股 成交价 3.85"
    ),
    create_time: str = "1747380000000",
    message_type: str = "text",
) -> SimpleNamespace:
    """Construct a fake Lark P2 envelope shape (dict-style)."""
    return SimpleNamespace(
        header={"event_id": event_id},
        event={
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "message_type": message_type,
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "create_time": create_time,
            },
            "sender": {"sender_id": {"open_id": sender_open_id}},
        },
    )


# -----------------------------------------------------------------------------
# InMemoryEventDedupe
# -----------------------------------------------------------------------------


class TestInMemoryDedupe:
    @pytest.mark.asyncio
    async def test_first_claim_returns_true(self) -> None:
        dedupe = InMemoryEventDedupe()
        assert await dedupe.claim("ev_a") is True

    @pytest.mark.asyncio
    async def test_second_claim_returns_false(self) -> None:
        dedupe = InMemoryEventDedupe()
        await dedupe.claim("ev_a")
        assert await dedupe.claim("ev_a") is False

    @pytest.mark.asyncio
    async def test_distinct_ids_independent(self) -> None:
        dedupe = InMemoryEventDedupe()
        assert await dedupe.claim("ev_a") is True
        assert await dedupe.claim("ev_b") is True

    @pytest.mark.asyncio
    async def test_lru_eviction(self) -> None:
        dedupe = InMemoryEventDedupe(max_entries=2)
        await dedupe.claim("ev_a")
        await dedupe.claim("ev_b")
        # Force eviction
        await dedupe.claim("ev_c")
        # ev_a should have been evicted (LRU oldest) and is claimable again.
        assert await dedupe.claim("ev_a") is True

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        dedupe = InMemoryEventDedupe(ttl_seconds=1)
        await dedupe.claim("ev_a")
        # Manually rewind the stored timestamp.
        dedupe._entries["ev_a"] = -100.0  # noqa: SLF001
        assert await dedupe.claim("ev_a") is True

    @pytest.mark.asyncio
    async def test_empty_event_id_raises(self) -> None:
        dedupe = InMemoryEventDedupe()
        with pytest.raises(ValueError, match="event_id"):
            await dedupe.claim("")

    def test_invalid_construction_args(self) -> None:
        with pytest.raises(ValueError, match="max_entries"):
            InMemoryEventDedupe(max_entries=0)
        with pytest.raises(ValueError, match="ttl_seconds"):
            InMemoryEventDedupe(ttl_seconds=0)


# -----------------------------------------------------------------------------
# RedisEventDedupe
# -----------------------------------------------------------------------------


class _FakeRedis:
    """Minimal SET NX EX implementation for the dedupe test."""

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[str, str] = {}
        self.fail = fail
        self.set_calls: list[dict[str, Any]] = []

    async def set(  # noqa: A003
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool | None = None,
    ) -> bool | None:
        self.set_calls.append({"name": name, "value": value, "ex": ex, "nx": nx})
        if self.fail:
            raise RuntimeError("redis down")
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True


class TestRedisEventDedupe:
    @pytest.mark.asyncio
    async def test_first_claim_succeeds(self) -> None:
        redis = _FakeRedis()
        dedupe = RedisEventDedupe(redis, ttl_seconds=60)
        assert await dedupe.claim("ev_a") is True
        assert redis.set_calls[-1]["nx"] is True
        assert redis.set_calls[-1]["ex"] == 60

    @pytest.mark.asyncio
    async def test_duplicate_claim_fails(self) -> None:
        redis = _FakeRedis()
        dedupe = RedisEventDedupe(redis)
        await dedupe.claim("ev_a")
        assert await dedupe.claim("ev_a") is False

    @pytest.mark.asyncio
    async def test_key_uses_prefix(self) -> None:
        redis = _FakeRedis()
        dedupe = RedisEventDedupe(redis, prefix="qm:feishu:")
        await dedupe.claim("ev_a")
        assert redis.set_calls[-1]["name"] == "qm:feishu:ev_a"

    def test_invalid_ttl_rejected(self) -> None:
        with pytest.raises(ValueError):
            RedisEventDedupe(_FakeRedis(), ttl_seconds=0)


# -----------------------------------------------------------------------------
# FeishuEventReceiver — construction
# -----------------------------------------------------------------------------


class TestReceiverConstruction:
    def test_missing_app_id_raises(self) -> None:
        async def _handler(_m: ReceivedMessage) -> None:
            return None

        with pytest.raises(ValueError):
            FeishuEventReceiver(
                app_id="",
                app_secret=VALID_APP_SECRET,
                verify_token=VALID_VERIFY,
                encrypt_key=VALID_ENCRYPT,
                dedupe=InMemoryEventDedupe(),
                handler=_handler,
            )

    def test_all_creds_required(self) -> None:
        async def _handler(_m: ReceivedMessage) -> None:
            return None

        for missing in ("app_secret", "verify_token", "encrypt_key"):
            kwargs = {
                "app_id": VALID_APP_ID,
                "app_secret": VALID_APP_SECRET,
                "verify_token": VALID_VERIFY,
                "encrypt_key": VALID_ENCRYPT,
                "dedupe": InMemoryEventDedupe(),
                "handler": _handler,
            }
            kwargs[missing] = ""
            with pytest.raises(ValueError):
                FeishuEventReceiver(**kwargs)


# -----------------------------------------------------------------------------
# Event normalisation — _extract_message via _handle_event
# -----------------------------------------------------------------------------


class _StubWSClient:
    """Stand-in for lark.ws.Client that yields control until cancelled."""

    def __init__(self) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True
        await asyncio.Event().wait()  # block until cancelled


class _RecordingHandler:
    """Captures every dispatched message."""

    def __init__(self, *, raise_on_call: bool = False) -> None:
        self.calls: list[ReceivedMessage] = []
        self.raise_on_call = raise_on_call

    async def __call__(self, message: ReceivedMessage) -> None:
        self.calls.append(message)
        if self.raise_on_call:
            raise RuntimeError("handler boom")


def _make_receiver(
    handler: _RecordingHandler,
    dedupe: InMemoryEventDedupe | None = None,
) -> FeishuEventReceiver:
    """Build a receiver with a no-op WS client factory."""

    def _factory(_dispatcher: Any) -> _StubWSClient:
        return _StubWSClient()

    return FeishuEventReceiver(
        app_id=VALID_APP_ID,
        app_secret=VALID_APP_SECRET,
        verify_token=VALID_VERIFY,
        encrypt_key=VALID_ENCRYPT,
        dedupe=dedupe or InMemoryEventDedupe(),
        handler=handler,
        client_factory=_factory,
    )


class TestEventDispatch:
    @pytest.mark.asyncio
    async def test_valid_event_dispatched(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(_build_event(event_id="ev_1"))  # noqa: SLF001
        # Fire-and-forget — drain pending tasks.
        await asyncio.gather(
            *receiver._tasks, return_exceptions=True  # noqa: SLF001
        )
        assert len(handler.calls) == 1
        msg = handler.calls[0]
        assert msg.event_id == "ev_1"
        assert msg.message_id == "om_xyz"
        assert msg.chat_id == "oc_chat_1"
        assert msg.sender_id == "ou_sender_1"
        assert msg.text.startswith("已执行 QM-20260516")

    @pytest.mark.asyncio
    async def test_duplicate_event_skipped(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(_build_event(event_id="ev_dup"))  # noqa: SLF001
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        await receiver._handle_event(_build_event(event_id="ev_dup"))  # noqa: SLF001
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert len(handler.calls) == 1

    @pytest.mark.asyncio
    async def test_distinct_events_both_delivered(self) -> None:
        """Two genuinely distinct messages (distinct event_id AND
        distinct message_id) both reach the handler."""
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(  # noqa: SLF001
            _build_event(event_id="ev_a", message_id="om_a")
        )
        await receiver._handle_event(  # noqa: SLF001
            _build_event(event_id="ev_b", message_id="om_b")
        )
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert len(handler.calls) == 2

    @pytest.mark.asyncio
    async def test_dedupe_by_message_id_when_event_id_differs(
        self,
    ) -> None:
        """Cycle 2 P1: a forwarded copy of the same message arrives
        with a NEW event_id but the SAME message_id. Without
        message_id dedup the handler would double-apply the execution
        report. After the fix, the second envelope must be skipped."""
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(  # noqa: SLF001
            _build_event(event_id="ev_first", message_id="om_dup")
        )
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        await receiver._handle_event(  # noqa: SLF001
            _build_event(event_id="ev_second", message_id="om_dup")
        )
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert len(handler.calls) == 1

    @pytest.mark.asyncio
    async def test_non_text_message_skipped(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(  # noqa: SLF001
            _build_event(message_type="image", event_id="ev_image")
        )
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert handler.calls == []

    @pytest.mark.asyncio
    async def test_missing_event_id_skipped(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(_build_event(event_id=""))  # noqa: SLF001
        assert handler.calls == []

    @pytest.mark.asyncio
    async def test_empty_text_skipped(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver._handle_event(_build_event(text=""))  # noqa: SLF001
        assert handler.calls == []

    @pytest.mark.asyncio
    async def test_malformed_envelope_skipped(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        # Header missing entirely.
        await receiver._handle_event(  # noqa: SLF001
            SimpleNamespace(header=None, event=None)
        )
        assert handler.calls == []

    @pytest.mark.asyncio
    async def test_handler_error_isolated(self) -> None:
        handler = _RecordingHandler(raise_on_call=True)
        errors: list[BaseException] = []

        def _capture(exc: BaseException, _msg: ReceivedMessage) -> None:
            errors.append(exc)

        receiver = FeishuEventReceiver(
            app_id=VALID_APP_ID,
            app_secret=VALID_APP_SECRET,
            verify_token=VALID_VERIFY,
            encrypt_key=VALID_ENCRYPT,
            dedupe=InMemoryEventDedupe(),
            handler=handler,
            client_factory=lambda _d: _StubWSClient(),
            on_handler_error=_capture,
        )
        await receiver._handle_event(_build_event(event_id="ev_fail"))  # noqa: SLF001
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)

    @pytest.mark.asyncio
    async def test_dispatch_does_not_block_handle_event(self) -> None:
        """Each handler runs in its own task — 3s ack budget honoured."""
        gate = asyncio.Event()

        async def _slow(_m: ReceivedMessage) -> None:
            await gate.wait()

        receiver = _make_receiver(_slow)  # type: ignore[arg-type]
        await asyncio.wait_for(
            receiver._handle_event(_build_event(event_id="ev_slow")),  # noqa: SLF001
            timeout=0.1,
        )
        # Tear down without waiting for the gate so the handler never returns.
        gate.set()
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver.start()
        assert receiver.running is True
        await receiver.stop()
        assert receiver.running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver.start()
        first_task = receiver._task  # noqa: SLF001
        await receiver.start()
        assert receiver._task is first_task  # noqa: SLF001
        await receiver.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver.stop()
        assert receiver.running is False

    @pytest.mark.asyncio
    async def test_can_restart_after_stop(self) -> None:
        handler = _RecordingHandler()
        receiver = _make_receiver(handler)
        await receiver.start()
        await receiver.stop()
        await receiver.start()
        assert receiver.running is True
        await receiver.stop()

    @pytest.mark.asyncio
    async def test_stop_does_not_hang_on_blocked_handler(self) -> None:
        """Cycle 2 P2: a handler stuck on network/broker work must
        not hang shutdown. stop(handler_grace_seconds=0.1) cancels
        the blocked handler and returns within the grace + small
        cancellation overhead."""
        blocked = asyncio.Event()

        async def _slow(_m: ReceivedMessage) -> None:
            try:
                blocked.set()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

        receiver = _make_receiver(_slow)  # type: ignore[arg-type]
        await receiver._handle_event(_build_event(event_id="ev_slow"))  # noqa: SLF001
        await asyncio.wait_for(blocked.wait(), timeout=1.0)
        # Even with 60s sleep in the handler, stop completes within
        # roughly the grace window (0.1s) plus task-cancel overhead.
        await asyncio.wait_for(
            receiver.stop(handler_grace_seconds=0.1),
            timeout=2.0,
        )


# -----------------------------------------------------------------------------
# _extract_text_content
# -----------------------------------------------------------------------------


class TestExtractTextContent:
    def test_valid_json(self) -> None:
        assert _extract_text_content(json.dumps({"text": "hi"})) == "hi"

    def test_strips_whitespace(self) -> None:
        assert (
            _extract_text_content(json.dumps({"text": "  hi  \n"}))
            == "hi"
        )

    def test_empty_raw_returns_empty(self) -> None:
        assert _extract_text_content("") == ""

    def test_invalid_json_returns_empty(self) -> None:
        assert _extract_text_content("{not json") == ""

    def test_non_dict_returns_empty(self) -> None:
        assert _extract_text_content("[]") == ""

    def test_missing_text_returns_empty(self) -> None:
        assert _extract_text_content(json.dumps({"other": "x"})) == ""

    def test_non_string_text_returns_empty(self) -> None:
        assert _extract_text_content(json.dumps({"text": 42})) == ""


# -----------------------------------------------------------------------------
# Red lines
# -----------------------------------------------------------------------------


class TestRedLines:
    def test_no_llm_imports(self) -> None:
        """LLM red line — receiver never imports llm/agents/mirofish."""
        import ast
        import pathlib

        for path in (
            "backend/integrations/feishu/events.py",
            "backend/integrations/feishu/dedupe.py",
        ):
            tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
            violations: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    parts = mod.split(".")
                    if parts[:1] == ["backend"] and len(parts) >= 2:
                        if parts[1] in {"llm", "agents", "mirofish"}:
                            violations.append(f"{path}: from {mod}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        parts = alias.name.split(".")
                        if parts[:1] == ["backend"] and len(parts) >= 2:
                            if parts[1] in {"llm", "agents", "mirofish"}:
                                violations.append(
                                    f"{path}: import {alias.name}"
                                )
            assert violations == [], (
                f"forbidden imports detected: {violations}"
            )

    def test_no_https_callback_route_added(self) -> None:
        """P0-2 §2.1 — zero public HTTPS callback. The receiver must not
        register a FastAPI route handler."""
        import pathlib

        text = pathlib.Path(
            "backend/integrations/feishu/events.py"
        ).read_text(encoding="utf-8")
        assert "fastapi" not in text.lower()
        assert "@router" not in text
        assert "APIRouter" not in text

    @pytest.mark.asyncio
    async def test_dedupe_failure_fails_open(self) -> None:
        """A dedupe outage must NOT block message delivery — fail-open
        per CLAUDE.md §3 'infra glitches'."""

        class _BoomDedupe:
            async def claim(self, _event_id: str) -> bool:
                raise RuntimeError("redis down")

        handler = _RecordingHandler()
        receiver = FeishuEventReceiver(
            app_id=VALID_APP_ID,
            app_secret=VALID_APP_SECRET,
            verify_token=VALID_VERIFY,
            encrypt_key=VALID_ENCRYPT,
            dedupe=_BoomDedupe(),
            handler=handler,
            client_factory=lambda _d: _StubWSClient(),
        )
        await receiver._handle_event(_build_event(event_id="ev_dedupe_down"))  # noqa: SLF001
        await asyncio.gather(*receiver._tasks, return_exceptions=True)  # noqa: SLF001
        assert len(handler.calls) == 1
