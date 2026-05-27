#!/usr/bin/env python
"""U-E5 — read-only list of the Feishu chats the bot belongs to.

Owner-driven verification helper. Uses the app credentials to fetch a
``tenant_access_token`` (kept in-memory by ``lark-oapi``) and calls the
**read-only** ``GET /open-apis/im/v1/chats`` to list every group the bot
is a member of. The owner runs this to confirm that
``FEISHU_DECISION_CHAT_ID`` is actually a chat the bot can post to — and
crucially that it is NOT the alert chat (告警群≠决策群,
P0-2-amendment-2026-05-16).

This script sends **NOTHING** — no message, no broker mutation, no state
change of any kind. It is purely a GET. Without the app credentials it
prints what is missing and exits 0 (the path the test-suite exercises;
zero network).

Usage::

    python scripts/list_feishu_chats.py          # table
    python scripts/list_feishu_chats.py --json    # JSON envelope

Red lines: read-only OpenAPI GET via the self-built app (no custom-bot,
P0-2-amendment-2026-05-16); ``tenant_access_token`` never persisted /
logged (SDK in-memory cache); no LLM in this path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass

# Credentials the read-only chat-list call needs. The decision chat id is
# required because the whole point is to verify it against the bot's
# membership; the alert chat id is optional context (flagged if it
# accidentally equals the decision chat).
_REQUIRED_CREDS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DECISION_CHAT_ID")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested with fakes — zero network)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatRow:
    """One chat the bot belongs to (subset of the ListChat item)."""

    chat_id: str
    name: str
    chat_status: str


@dataclass(frozen=True)
class ChatMembershipVerdict:
    """Whether the configured decision/alert chats are in the bot's list."""

    decision_chat_id: str
    alert_chat_id: str | None
    decision_present: bool
    alert_present: bool
    decision_is_alert: bool

    @property
    def ok(self) -> bool:
        """True iff the decision chat is reachable and distinct from alert."""
        return self.decision_present and not self.decision_is_alert


def evaluate_chat_membership(
    chat_ids: Iterable[str],
    *,
    decision_chat_id: str,
    alert_chat_id: str | None,
) -> ChatMembershipVerdict:
    """Grade the bot's chat membership against the configured chats.

    Fail-closed framing: a decision chat the bot is NOT in (``decision_
    present=False``) means outbound sends would fail, and a decision chat
    that equals the alert chat (``decision_is_alert=True``) violates
    P0-2-amendment-2026-05-16 — both make ``ok`` False so the owner sees
    the problem before flipping any go-live gate.
    """
    ids = set(chat_ids)
    return ChatMembershipVerdict(
        decision_chat_id=decision_chat_id,
        alert_chat_id=alert_chat_id,
        decision_present=decision_chat_id in ids,
        alert_present=bool(alert_chat_id) and alert_chat_id in ids,
        decision_is_alert=bool(alert_chat_id) and alert_chat_id == decision_chat_id,
    )


def missing_credentials() -> tuple[str, ...]:
    """Return required env vars that are absent/empty."""
    return tuple(name for name in _REQUIRED_CREDS if not os.environ.get(name))


# ---------------------------------------------------------------------------
# Read-only network call (owner-run; skip-gated by credential presence)
# ---------------------------------------------------------------------------


async def _list_chats_real() -> list[ChatRow]:
    """Paginate ``GET im/v1/chats`` and return every chat the bot is in.

    Builds its own ``lark-oapi`` client (the locked :class:`FeishuClient`
    is intentionally narrow to ``send_message`` only). Read-only — never
    sends a message.
    """
    import lark_oapi as lark  # local import — keep test import cost low
    from lark_oapi.api.im.v1 import ListChatRequest

    client = (
        lark.Client.builder()
        .app_id(os.environ["FEISHU_APP_ID"].strip())
        .app_secret(os.environ["FEISHU_APP_SECRET"].strip())
        .log_level(lark.LogLevel.WARNING)  # never INFO — leaks tokens
        .build()
    )

    rows: list[ChatRow] = []
    page_token: str | None = None
    while True:
        builder = ListChatRequest.builder().page_size(100)
        if page_token:
            builder = builder.page_token(page_token)
        resp = await client.im.v1.chat.alist(builder.build())
        if not resp.success():
            raise RuntimeError(
                f"im/v1/chats list failed: code={resp.code} msg={resp.msg} "
                f"log_id={resp.get_log_id()}"
            )
        data = resp.data
        for item in getattr(data, "items", None) or []:
            rows.append(
                ChatRow(
                    chat_id=getattr(item, "chat_id", "") or "",
                    name=getattr(item, "name", "") or "",
                    chat_status=getattr(item, "chat_status", "") or "",
                )
            )
        if not getattr(data, "has_more", False):
            break
        page_token = getattr(data, "page_token", None)
        if not page_token:
            break
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="list_feishu_chats",
        description=(
            "Read-only list of the Feishu chats the bot belongs to. Sends "
            "nothing. Without app credentials it lists what is missing."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON envelope to stdout."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    missing = missing_credentials()
    if missing:
        payload = {
            "verdict": "SKIPPED",
            "reason": "missing credentials — read-only chat list not run",
            "missing_credentials": list(missing),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print("list_feishu_chats: missing " + ", ".join(missing))
        return 0

    decision = os.environ["FEISHU_DECISION_CHAT_ID"].strip()
    alert = (os.environ.get("FEISHU_ALERT_CHAT_ID") or "").strip() or None

    try:
        rows = asyncio.run(_list_chats_real())
    except Exception as exc:  # noqa: BLE001 — surface the API error to owner
        print(f"list_feishu_chats: ERROR {exc}", file=sys.stderr)
        return 1

    verdict = evaluate_chat_membership(
        (r.chat_id for r in rows),
        decision_chat_id=decision,
        alert_chat_id=alert,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "verdict": "OK" if verdict.ok else "CHECK",
                    "ok": verdict.ok,
                    "chats": [
                        {
                            "chat_id": r.chat_id,
                            "name": r.name,
                            "chat_status": r.chat_status,
                        }
                        for r in rows
                    ],
                    "decision_chat_id": verdict.decision_chat_id,
                    "decision_present": verdict.decision_present,
                    "alert_chat_id": verdict.alert_chat_id,
                    "alert_present": verdict.alert_present,
                    "decision_is_alert": verdict.decision_is_alert,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"Bot belongs to {len(rows)} chat(s):")
        for r in rows:
            marker = ""
            if r.chat_id == decision:
                marker = "  ← DECISION"
            elif alert and r.chat_id == alert:
                marker = "  ← ALERT"
            print(f"  {r.chat_id}  {r.name!r}  [{r.chat_status}]{marker}")
        print()
        print(
            f"decision_present={verdict.decision_present} "
            f"alert_present={verdict.alert_present} "
            f"decision_is_alert={verdict.decision_is_alert} → "
            f"{'OK' if verdict.ok else 'CHECK'}"
        )
        if not verdict.decision_present:
            print(
                "  ! FEISHU_DECISION_CHAT_ID is NOT in the bot's chats — the "
                "bot cannot post there. Add the bot to the decision group.",
                file=sys.stderr,
            )
        if verdict.decision_is_alert:
            print(
                "  ! FEISHU_DECISION_CHAT_ID equals FEISHU_ALERT_CHAT_ID — "
                "decision and alert chats must be strictly separated "
                "(P0-2-amendment-2026-05-16).",
                file=sys.stderr,
            )

    return 0 if verdict.ok else 1


if __name__ == "__main__":  # pragma: no cover — exercised via tests
    raise SystemExit(main())
