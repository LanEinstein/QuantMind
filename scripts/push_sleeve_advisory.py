#!/usr/bin/env python
"""SLV-1 trial ops — push the defensive-sleeve forward advisory to Feishu.

Reads the forward runner's status JSON (``defensive_sleeve_forward_status.json``,
written by ``scripts.factor_research.defensive_sleeve_forward``) and sends the
CURRENT target book as a **display-only** plain-text digest through the
self-built app OpenAPI (never the banned custom-bot webhook).

Red lines honored by construction:

* the text is composed by :meth:`MessageRenderer.render_sleeve_advisory`
  (CLAUDE.md §2.6 — every Feishu message goes through the renderer); it carries
  NO ``QM-`` instruction_id and NO execution verb, so it can never be parsed
  as, or mistaken for, an order — the owner acts manually through the existing
  human gate;
* zero LLM anywhere in this path (deterministic research output only);
* credentials come from the environment (never persisted / logged); a missing
  credential is a fail-closed exit, never a partial send;
* idempotent per (advisory, as-of date): a LOCAL sent-marker file skips any
  as-of date already pushed (``--force`` overrides), because Feishu's ``uuid``
  dedupe only spans a 1-hour server-side window — on a holiday the runner's
  as-of date does not advance and a marker-less rerun would re-send yesterday's
  book as if fresh (codex finding).

Usage::

    python scripts/push_sleeve_advisory.py --dry-run     # print text, no network
    python scripts/push_sleeve_advisory.py               # send to decision chat
    python scripts/push_sleeve_advisory.py --chat-env FEISHU_ALERT_CHAT_ID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_STATUS = "data/factor_research/defensive_sleeve_forward_status.json"
DEFAULT_SENT_MARKER = "data/factor_research/sleeve_advisory_sent.json"
_REQUIRED_CREDS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")


def already_sent(marker_path: Path, asof: str) -> bool:
    """True when the local marker records a successful push for ``asof``."""
    if not marker_path.exists():
        return False
    try:
        sent = json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False  # unreadable marker → treat as never-sent (safe: dedupe only)
    return asof in sent


def mark_sent(marker_path: Path, asof: str, *, sent_at: str) -> None:
    """Record a successful push for ``asof`` (merge-write, keeps history)."""
    sent: dict[str, str] = {}
    if marker_path.exists():
        try:
            sent = json.loads(marker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            sent = {}
    sent[asof] = sent_at
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(sent, indent=2), encoding="utf-8")


def load_status(path: Path) -> dict[str, Any]:
    """Load + validate the forward status JSON (fail-closed on missing pieces)."""
    if not path.exists():
        raise FileNotFoundError(f"forward status not found: {path} — run the runner")
    raw = json.loads(path.read_text(encoding="utf-8"))
    advisory = raw.get("advisory") or {}
    holdings = advisory.get("holdings") or []
    if not holdings:
        raise ValueError("status has no advisory holdings — nothing to push")
    for key in ("status", "spec_hash", "kill_switch", "forward"):
        if key not in raw:
            raise ValueError(f"status JSON missing {key!r} — fail-closed")
    return raw


def render_text(status: dict[str, Any], *, pilot: bool = False) -> str:
    """Compose the digest via the renderer (§2.6 — the only legal composer)."""
    from backend.integrations.feishu.renderer import MessageRenderer

    advisory = status["advisory"]
    kill = status["kill_switch"]
    return MessageRenderer().render_sleeve_advisory(
        status=str(status["status"]),
        spec_hash_prefix=str(status["spec_hash"])[:8],
        asof_trade_date=str(advisory["asof_trade_date"]),
        universe_size=int(advisory["universe_size"]),
        holdings=list(advisory["holdings"]),
        cash_weight_pct=float(advisory["cash_weight_pct"]),
        complete_periods=int(status["forward"]["complete_periods"]),
        min_forward_periods=int(kill["min_forward_periods"]),
        mdd_kill=float(kill["mdd_kill"]),
        bear_cum_kill=float(kill["bear_cum_kill"]),
        baseline_underperf_periods=int(kill["baseline_underperf_periods"]),
        pilot=pilot,
    )


async def send(text: str, *, chat_id: str, dedupe_key: str) -> bool:
    """Send through the self-built app OpenAPI; returns the API acceptance."""
    from backend.integrations.feishu.client import FeishuClient

    client = FeishuClient(
        os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"]
    )
    result = await client.send_message(chat_id, text, uuid=dedupe_key)
    return bool(result.ok)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", default=DEFAULT_STATUS)
    parser.add_argument(
        "--chat-env",
        default="FEISHU_DECISION_CHAT_ID",
        help="env var holding the target open_chat_id (display-only digest)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the text; send nothing"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="send even if this as-of date was already pushed",
    )
    parser.add_argument("--sent-marker", default=DEFAULT_SENT_MARKER)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()

    status = load_status(Path(args.status))
    text = render_text(status, pilot=args.pilot)
    if args.dry_run:
        print(text)
        return 0

    asof = str(status["advisory"]["asof_trade_date"])
    marker = Path(args.sent_marker)
    if not args.force and already_sent(marker, asof):
        print(f"advisory for asof {asof} already pushed — skipping (--force to resend)")
        return 0

    missing = [c for c in (*_REQUIRED_CREDS, args.chat_env) if not os.environ.get(c)]
    if missing:
        print(f"missing credentials: {', '.join(missing)} — nothing sent")
        return 2
    chat_id = os.environ[args.chat_env].strip()
    ok = asyncio.run(send(text, chat_id=chat_id, dedupe_key=f"sleeve-advisory-{asof}"))
    if ok:
        from datetime import UTC, datetime

        mark_sent(marker, asof, sent_at=datetime.now(UTC).isoformat())
        print(f"sleeve advisory sent (asof {asof})")
        return 0
    print("Feishu API rejected the message — see backend logs")
    return 1


if __name__ == "__main__":
    sys.exit(main())
