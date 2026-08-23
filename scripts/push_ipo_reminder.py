#!/usr/bin/env python
"""MZ-1 institutional-rent ops — push the daily IPO/CB subscription reminder.

Protocol: ``docs/research/institutional-rent-protocol-2026-08-23.md``.
Flow (one run per trading morning, 08:30 cron via
``scripts/ipo_reminder_daily.sh``):

1. read today's subscription calendars (``new_share`` / ``cb_issue``,
   official Tushare SDK, BSE excluded);
2. update the rolling break-issue monitor (first-day closes, local cache);
3. apply the protocol §3 kill rule — a killed category suppresses its
   listing forever (one-time stop notice on the transition; recovery is
   an owner decision: clear ``break_kill_state.json`` on instruction);
4. nothing subscribable and no transition → SILENT (no message at all);
5. otherwise compose via :meth:`MessageRenderer.render_ipo_reminder`
   (display-only, no ``QM-`` id, no execution verb) and send through the
   self-built app OpenAPI, deduped per date by a local sent-marker.

Red lines mirror ``push_sleeve_advisory.py``: renderer-only composition,
zero LLM, fail-closed on missing credentials, never an instruction.

Usage::

    python scripts/push_ipo_reminder.py --dry-run     # print text, no network send
    python scripts/push_ipo_reminder.py               # send to decision chat
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.institutional_rent.break_monitor import (
    KILL_THRESHOLD,
    cb_break_stats,
    latch_kill_state,
    load_cache,
    mark_notice_delivered,
    save_cache,
    stock_break_stats,
)
from scripts.institutional_rent.calendars import (
    QueryFn,
    fetch_cb_subscriptions,
    fetch_stock_subscriptions,
)
from scripts.push_sleeve_advisory import already_sent, mark_sent, send

DEFAULT_DATA_DIR = "data/institutional_rent"
_REQUIRED_CREDS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET")


@dataclass(frozen=True)
class ReminderBuild:
    """Composed text plus the stop notices it carries (undelivered so far)."""

    text: str | None
    pending_notices: tuple[str, ...]


def build_reminder(
    query: QueryFn, date: str, data_dir: Path, *, persist: bool = True
) -> ReminderBuild:
    """Compose today's reminder; ``text=None`` = nothing to push (silent day).

    ``persist=False`` (dry-run) leaves the break cache and kill state
    untouched so a rehearsal never advances real monitor state. A stop
    notice stays pending — and is re-included on every subsequent run —
    until the caller confirms delivery via :func:`mark_notice_delivered`
    (codex P1: a failed send must never consume the one-time notice).
    """
    cache_path = data_dir / "break_cache.json"
    state_path = data_dir / "break_kill_state.json"

    stocks = fetch_stock_subscriptions(query, date)
    cbs = fetch_cb_subscriptions(query, date)

    cache = load_cache(cache_path)
    stock_stats, cache = stock_break_stats(query, date, cache)
    cb_stats, cache = cb_break_stats(query, date, cache)
    if persist:
        save_cache(cache_path, cache)

    state = latch_kill_state(
        state_path,
        stock_killed=stock_stats.killed,
        cb_killed=cb_stats.killed,
        persist=persist,
    )
    pending = tuple(
        category
        for category in ("stock", "cb")
        if state[category] and not state[f"{category}_notified"]
    )

    stocks_shown = () if state["stock"] else stocks
    cbs_shown = () if state["cb"] else cbs
    if not stocks_shown and not cbs_shown and not pending:
        return ReminderBuild(text=None, pending_notices=())

    from backend.integrations.feishu.renderer import MessageRenderer

    text = MessageRenderer().render_ipo_reminder(
        date=date,
        stocks=[asdict(s) for s in stocks_shown],
        cbs=[asdict(c) for c in cbs_shown],
        stock_broken=stock_stats.broken,
        stock_evaluated=stock_stats.evaluated,
        cb_broken=cb_stats.broken,
        cb_evaluated=cb_stats.evaluated,
        kill_threshold=KILL_THRESHOLD,
        stock_killed="stock" in pending,
        cb_killed="cb" in pending,
    )
    return ReminderBuild(text=text, pending_notices=pending)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d"),
        help="subscription day, YYYYMMDD (default: today, Asia/Shanghai)",
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--chat-env",
        default="FEISHU_DECISION_CHAT_ID",
        help="env var holding the target open_chat_id (display-only digest)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the text; persist and send nothing",
    )
    parser.add_argument(
        "--force", action="store_true", help="send even if this date was already pushed"
    )
    args = parser.parse_args()

    import tushare as ts

    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        print("missing TUSHARE_TOKEN — nothing done")
        return 2
    query = ts.pro_api(token).query

    data_dir = Path(args.data_dir)
    build = build_reminder(query, args.date, data_dir, persist=not args.dry_run)
    if build.text is None:
        print(f"nothing subscribable on {args.date} — silent")
        return 0
    if args.dry_run:
        print(build.text)
        return 0

    marker = data_dir / "reminder_sent.json"
    if not args.force and already_sent(marker, args.date):
        print(f"reminder for {args.date} already pushed — skipping (--force to resend)")
        return 0

    missing = [c for c in (*_REQUIRED_CREDS, args.chat_env) if not os.environ.get(c)]
    if missing:
        print(f"missing credentials: {', '.join(missing)} — nothing sent")
        return 2
    chat_id = os.environ[args.chat_env].strip()
    ok = asyncio.run(
        send(build.text, chat_id=chat_id, dedupe_key=f"ipo-reminder-{args.date}")
    )
    if ok:
        from datetime import UTC

        mark_sent(marker, args.date, sent_at=datetime.now(UTC).isoformat())
        if build.pending_notices:
            # Only a DELIVERED push consumes the one-time stop notice.
            mark_notice_delivered(
                data_dir / "break_kill_state.json", build.pending_notices
            )
        print(f"ipo reminder sent (date {args.date})")
        return 0
    print("Feishu API rejected the message — see backend logs")
    return 1


if __name__ == "__main__":
    sys.exit(main())
