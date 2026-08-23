#!/usr/bin/env python
"""MI-1 reconciliation listener — owner free text → mirror ledger → ack.

A STANDALONE process (no uvicorn / FastAPI / old M4 runtime): it wires the
existing, tested pieces together and nothing more:

    FeishuEventReceiver (WS long connection, websockets<14)
      → InboundGate (decision chat + owner open_id, fail-closed)
      → backend.portfolio.reconcile.handle_owner_text
          (one LLM extraction via LLMRouter agent ``execution_reconciler``;
           everything else deterministic — ledger append, awaiting-flag
           clear, renderer-composed reply)
      → FeishuClient.send_message (reply into the decision chat)

Red lines: ``real_broker_orders = false`` forever — this process only
records what the owner says they did; the LLM never composes wire copy.

Run (owner-started; credentials live in ``~/.bashrc``)::

    FEISHU_INTERACTIVE_ENABLED=false \
        python scripts/reconcile_listener.py            # Ctrl-C to stop
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.integrations.feishu.client import FeishuClient
from backend.integrations.feishu.dedupe import InMemoryEventDedupe
from backend.integrations.feishu.events import FeishuEventReceiver, ReceivedMessage
from backend.integrations.feishu.inbound_gate import InboundGate, InboundVerdict
from backend.llm.router import LLMRouter
from backend.portfolio.reconcile import handle_owner_text

log = logging.getLogger("scripts.reconcile_listener")

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REQUIRED_ENV = (
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFY_TOKEN",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_DECISION_CHAT_ID",
    "FEISHU_OWNER_OPEN_ID",
)

DEFAULT_STATUS = Path("data/factor_research/defensive_sleeve_forward_status.json")
DEFAULT_PUSH_STATE = Path("data/factor_research/sleeve_push_state.json")
DEFAULT_MIRROR = Path("data/portfolio/mirror_ledger.jsonl")
DEFAULT_Z = Path("data/institutional_rent/z_ledger.jsonl")


def _message_time(message: ReceivedMessage) -> datetime:
    """The Lark create_time (ms epoch) in Shanghai; fallback: receive time."""
    if message.raw_create_time > 0:
        return datetime.fromtimestamp(
            message.raw_create_time / 1000.0, tz=UTC
        ).astimezone(_SHANGHAI)
    return message.received_at.astimezone(_SHANGHAI)


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(message)s"
    )
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        print(f"missing credentials: {', '.join(missing)} — not started")
        return 2

    gate = InboundGate.from_env(os.environ)
    client = FeishuClient(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    )
    router = LLMRouter("config/agent_models.yaml")
    await router.initialize(None)  # no Redis — cost tracking is skipped

    async def complete_fn(prompt: str) -> str:
        completion = await router.complete(
            "execution_reconciler",
            [{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        return str(completion.choices[0].message.content or "")

    async def handler(message: ReceivedMessage) -> None:
        verdict = gate.classify(
            chat_id=message.chat_id, sender_id=message.sender_id
        )
        if verdict is not InboundVerdict.ACCEPT:
            log.info("reconcile_dropped verdict=%s", verdict.value)
            return
        result = await handle_owner_text(
            message.text,
            received_at=_message_time(message),
            complete_fn=complete_fn,
            ledger_path=DEFAULT_MIRROR,
            z_ledger_path=DEFAULT_Z,
            push_state_path=DEFAULT_PUSH_STATE,
            status_path=DEFAULT_STATUS,
        )
        sent = await client.send_message(
            message.chat_id,
            result.reply_text,
            uuid=f"reconcile-{message.message_id}",
        )
        log.info(
            "reconcile_handled booked=%s reply_sent=%s",
            result.booked,
            sent.ok,
        )

    receiver = FeishuEventReceiver(
        app_id=os.environ["FEISHU_APP_ID"],
        app_secret=os.environ["FEISHU_APP_SECRET"],
        verify_token=os.environ["FEISHU_VERIFY_TOKEN"],
        encrypt_key=os.environ["FEISHU_ENCRYPT_KEY"],
        dedupe=InMemoryEventDedupe(),
        handler=handler,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    await receiver.start()
    log.info("reconcile_listener_started (Ctrl-C to stop)")
    await stop.wait()
    log.info("reconcile_listener_stopping")
    await receiver.stop()
    await router.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
