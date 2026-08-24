#!/usr/bin/env python
"""SLV-1 trial ops — push the defensive-sleeve forward advisory to Feishu.

Reads the forward runner's status JSON (``defensive_sleeve_forward_status.json``,
written by ``scripts.factor_research.defensive_sleeve_forward``) and sends the
CURRENT target book as a **display-only** plain-text digest through the
self-built app OpenAPI (never the banned custom-bot webhook).

Push semantics (MI-1 redesign — change-triggered, silent by default):

* **status change** (any day): the forward status (ACCRUING / SURVIVING /
  KILLED) differs from the last DELIVERED one → push with an explicit
  transition line. The state file is only updated after a successful send,
  so a failed send can never swallow a one-time KILLED notice (the
  ``mark_notice_delivered`` lesson from MZ-1);
* **rebalance with a diff**: a schedule rebalance date not yet advised has
  passed AND the target book (holdings code+weight / cash / canonical JSON
  hash) differs from the last delivered book → push. Anchoring on the
  schedule-rebalance pointer (not the as-of date) self-heals a missed
  rebalance-day run: the next run still sees the un-advised rebalance;
* **execution reminder**: a delivered rebalance advisory sets an
  ``awaiting_report`` flag; if the owner has not reported by the NEXT
  trading close (the reconciliation loop clears the flag on a booked fill
  or an explicit no-action), the advisory is re-rendered from the latest
  close and re-pushed — the plan's "no report → assume unfilled, recompute
  and re-push". Codex-flagged: without this explicit state the pointer
  advance would silently swallow the retry;
* **anything else**: the pipeline runs, the status JSON is written, and NO
  message is sent. A rebalance whose book is unchanged silently advances
  the pointer (dedupe state only, not a notice — safe to persist unsent).

The advisory book is recomputed by the runner at every as-of close (raw
dv-top5, no buffer), so its hash drifts on non-rebalance days; that drift is
deliberately NOT a push trigger — the owner only acts on rebalance days.

Red lines honored by construction:

* the text is composed by :meth:`MessageRenderer.render_sleeve_advisory`
  (CLAUDE.md §2.6 — every Feishu message goes through the renderer); it carries
  NO ``QM-`` instruction_id and NO execution verb, so it can never be parsed
  as, or mistaken for, an order — the owner acts manually through the existing
  human gate;
* zero LLM anywhere in this path (deterministic research output only);
* credentials come from the environment (never persisted / logged); a missing
  credential is a fail-closed exit, never a partial send.

Usage::

    python scripts/push_sleeve_advisory.py --dry-run     # print decision + text
    python scripts/push_sleeve_advisory.py               # send iff a push event
    python scripts/push_sleeve_advisory.py --force       # send regardless
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.portfolio.sleeve_push_state import (
    AWAITING_KEY,
    load_push_state,
    save_push_state,
)

DEFAULT_STATUS = "data/factor_research/defensive_sleeve_forward_status.json"
DEFAULT_PUSH_STATE = "data/factor_research/sleeve_push_state.json"
DEFAULT_ADVISORY_HISTORY = "data/factor_research/sleeve_advisory_history.jsonl"
# Legacy per-as-of marker (pre-MI-1). Kept only because push_ipo_reminder
# reuses already_sent/mark_sent; the sleeve path no longer writes it.
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
    sent = {**sent, asof: sent_at}
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


def content_hash(status: dict[str, Any]) -> str:
    """Canonical hash of what the owner would ACT on: book + cash.

    Only ``ts_code``/``target_weight_pct``/``cash_weight_pct`` enter the hash —
    daily-drifting display fields (close, dv_ratio, as-of date) must not.
    """
    advisory = status["advisory"]
    canonical = {
        "holdings": sorted(
            [str(h["ts_code"]), float(h["target_weight_pct"])]
            for h in advisory["holdings"]
        ),
        "cash_weight_pct": float(advisory["cash_weight_pct"]),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PushDecision:
    """What this run should do, and the state each outcome persists."""

    event: str | None  # "status_change" | "rebalance" | None (silent)
    status_changed_from: str | None  # rendered transition line, if any
    state_after_send: dict[str, Any]  # persisted ONLY after a delivered send
    state_after_silent: dict[str, Any]  # dedupe pointer advance; safe unsent


def decide(status: dict[str, Any], state: dict[str, Any]) -> PushDecision:
    """Change-triggered push decision (see module docstring for semantics)."""
    current_status = str(status["status"])
    book_hash = content_hash(status)
    asof = str(status["advisory"]["asof_trade_date"])
    schedule = [str(d) for d in status["forward"]["schedule_rebalances"]]
    due = [d for d in schedule if d <= asof]
    latest_due = due[-1] if due else ""
    last_advised = str(state.get("last_advised_rebalance", ""))
    rebalance_pending = latest_due > last_advised
    book_changed = book_hash != state.get("last_sent_hash")
    awaiting = state.get(AWAITING_KEY)
    awaiting_stale = isinstance(awaiting, dict) and asof > str(
        awaiting.get("delivered_asof", "")
    )

    killed = current_status == "KILLED"
    last_status = state.get("last_sent_status")
    if current_status != last_status:
        event = "status_change"
    elif killed:
        # A killed sleeve must never resume action-bearing pushes (codex
        # P1): after the one-time transition notice, no rebalance and no
        # execution reminder — the owner stops manually.
        event = None
    elif rebalance_pending and book_changed:
        event = "rebalance"
    elif awaiting_stale:
        event = "rebalance_reminder"
    else:
        event = None

    state_after_send = {
        "last_sent_status": current_status,
        "last_sent_hash": book_hash,
        "last_advised_rebalance": max(latest_due, last_advised),
        "asof_trade_date": asof,
    }
    # A delivered advisory awaits the owner's execution report; the
    # reconciliation loop clears the flag, tomorrow's cron reminds until
    # then. A status change rides along only when a rebalance is also due.
    # A KILLED delivery clears any pending reminder instead of arming one.
    if not killed and (
        event in ("rebalance", "rebalance_reminder")
        or (event == "status_change" and rebalance_pending and book_changed)
    ):
        state_after_send[AWAITING_KEY] = {
            "hash": book_hash,
            "delivered_asof": asof,
        }
    elif isinstance(awaiting, dict) and not killed:
        state_after_send[AWAITING_KEY] = awaiting

    state_after_silent = dict(state)
    if killed and AWAITING_KEY in state_after_silent:
        # Belt for the delivered-KILLED steady state: kill any leftover
        # reminder flag even on silent runs.
        state_after_silent = {
            k: v for k, v in state_after_silent.items() if k != AWAITING_KEY
        }
    if event is None and rebalance_pending:
        # Book identical at this rebalance → nothing to say, but the pointer
        # must advance or later non-rebalance hash drift would fake a push.
        state_after_silent = {
            **state_after_silent,
            "last_advised_rebalance": latest_due,
        }
    return PushDecision(
        event=event,
        status_changed_from=(
            str(last_status) if event == "status_change" and last_status else None
        ),
        state_after_send=state_after_send,
        state_after_silent=state_after_silent,
    )


def _mirror_context(mirror_path: Path | None = None) -> tuple[Any, float | None]:
    """Current mirror book + declared capital (equity at cost), if any.

    Fail-open: a broken/unreadable mirror must never block the advisory
    push — it degrades to "no sizing info" (an empty, undeclared book).
    """
    from backend.portfolio.mirror_ledger import (
        DEFAULT_LEDGER,
        MirrorBook,
        load_book,
    )

    try:
        book = load_book(mirror_path or DEFAULT_LEDGER)
    except (ValueError, OSError):
        book = MirrorBook(
            positions=(), cash=0.0, opening_declared=False, fill_count=0
        )
    if not book.opening_declared:
        return book, None
    capital = book.cash + sum(p.volume * p.avg_cost for p in book.positions)
    return book, capital


def augment_holdings(
    holdings: list[dict[str, Any]], book: Any, capital: float | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Owner-facing sizing: suggested shares + concrete action per holding,
    plus the clear-out list (mirrored names no longer in the target book).

    Shares = capital × weight ÷ close, floored to a 100-share lot. Without
    a declared capital only the exits are actionable (they need no base).
    """
    target_codes: set[str] = set()
    augmented: list[dict[str, Any]] = []
    for h in holdings:
        code6 = str(h.get("ts_code", ""))[:6]
        target_codes.add(code6)
        held_pos = book.position_for(code6)
        held = held_pos.volume if held_pos else 0
        entry = dict(h)
        close = h.get("close")
        weight = h.get("target_weight_pct")
        if (
            capital is not None
            and isinstance(close, int | float)
            and float(close) > 0
            and isinstance(weight, int | float)
        ):
            suggest = int(
                capital * float(weight) / 100.0 / float(close) // 100 * 100
            )
            if held == 0:
                action = "新进"
            elif suggest > held:
                action = "加仓"
            elif suggest < held:
                action = "减仓"
            else:
                action = "维持"
            entry.update(
                suggest_shares=suggest, held_shares=held, action=action
            )
        augmented.append(entry)
    exits = [
        {"ts_code": p.code, "name": "", "held_volume": p.volume}
        for p in book.positions
        if p.code not in target_codes
    ]
    return augmented, exits


def render_text(
    status: dict[str, Any],
    *,
    status_changed_from: str | None = None,
    reminder: bool = False,
    mirror_path: Path | None = None,
    pilot: bool = False,
) -> str:
    """Compose the digest via the renderer (§2.6 — the only legal composer)."""
    from backend.integrations.feishu.renderer import MessageRenderer

    advisory = status["advisory"]
    kill = status["kill_switch"]
    book, capital = _mirror_context(mirror_path)
    holdings, exits = augment_holdings(
        list(advisory["holdings"]), book, capital
    )
    return MessageRenderer().render_sleeve_advisory(
        status=str(status["status"]),
        spec_hash_prefix=str(status["spec_hash"])[:8],
        asof_trade_date=str(advisory["asof_trade_date"]),
        universe_size=int(advisory["universe_size"]),
        holdings=holdings,
        cash_weight_pct=float(advisory["cash_weight_pct"]),
        complete_periods=int(status["forward"]["complete_periods"]),
        min_forward_periods=int(kill["min_forward_periods"]),
        mdd_kill=float(kill["mdd_kill"]),
        bear_cum_kill=float(kill["bear_cum_kill"]),
        baseline_underperf_periods=int(kill["baseline_underperf_periods"]),
        status_changed_from=status_changed_from,
        reminder=reminder,
        exits=exits,
        capital_declared=capital is not None,
        pilot=pilot,
    )


def _pit_closes(asof: str, codes: list[str]) -> dict[str, float | None]:
    """As-of closes for exited codes from the PIT daily snapshot (read-only).

    A lookup failure yields ``None`` (the drift report then counts the sell
    as uncovered — honest fallback, never a stale price).
    """
    if not codes:
        return {}
    try:
        import io

        import pandas as pd

        from backend.marketdata_snapshot.store import SnapshotStore

        snap = SnapshotStore("data/marketdata_pit").latest(
            vendor="tushare", endpoint="daily", trade_date=asof
        )
        frame = pd.read_csv(
            io.BytesIO(snap.raw_payload), usecols=["ts_code", "close"]
        )
        found = dict(
            zip(frame["ts_code"].astype(str), frame["close"].astype(float))
        )
        return {c: found.get(c) for c in codes}
    except Exception:  # noqa: BLE001 — disclosure aid only, never blocks a push
        return {c: None for c in codes}


def append_advisory_history(
    path: Path, status: dict[str, Any], *, event: str, delivered_at: str
) -> None:
    """Snapshot the DELIVERED advisory (research-side reference prices).

    The status JSON is overwritten daily, so the monthly mirror-vs-research
    execution-drift disclosure (plan §5⑤) needs the book AS DELIVERED —
    closes included — persisted at push time. Codes present in the PREVIOUS
    delivery but dropped from this one are recorded under ``exits`` with
    their as-of close (codex P1: an exit sell must be compared against the
    exit rebalance's close, not a weeks-old advisory price).
    """
    advisory = status["advisory"]
    asof = str(advisory["asof_trade_date"])
    current_codes = {str(h.get("ts_code", "")) for h in advisory["holdings"]}
    previous = load_advisory_history(path)
    prev_codes: set[str] = set()
    if previous:
        prev_codes = {
            str(h.get("ts_code", ""))
            for h in previous[-1].get("holdings", ())
        }
    # Recorded ONCE, at the delivery that drops the code: the research-side
    # sell assumption IS the exit rebalance's close, even if the owner
    # executes days later.
    exited = sorted(prev_codes - current_codes - {""})
    exit_closes = _pit_closes(asof, exited)
    row = {
        "asof": asof,
        "delivered_at": delivered_at,
        "event": event,
        "holdings": [
            {
                "ts_code": str(h.get("ts_code", "")),
                "close": h.get("close"),
                "target_weight_pct": h.get("target_weight_pct"),
            }
            for h in advisory["holdings"]
        ],
        "exits": [
            {"ts_code": code, "close": exit_closes.get(code)} for code in exited
        ],
        "cash_weight_pct": float(advisory["cash_weight_pct"]),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_advisory_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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
        "--dry-run",
        action="store_true",
        help="print the decision and text; persist and send nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="send even when no push event fired (rehearsal/manual resend)",
    )
    parser.add_argument("--push-state", default=DEFAULT_PUSH_STATE)
    parser.add_argument("--pilot", action="store_true")
    args = parser.parse_args()

    status = load_status(Path(args.status))
    state_path = Path(args.push_state)
    state = load_push_state(state_path)
    decision = decide(status, state)
    asof = str(status["advisory"]["asof_trade_date"])

    reminder = decision.event == "rebalance_reminder"
    if args.dry_run:
        print(f"decision: event={decision.event or 'silent'} asof={asof}")
        if decision.event or args.force:
            print("--")
            print(
                render_text(
                    status,
                    status_changed_from=decision.status_changed_from,
                    reminder=reminder,
                    pilot=args.pilot,
                )
            )
        return 0

    if decision.event is None and not args.force:
        if decision.state_after_silent != state:
            save_push_state(state_path, decision.state_after_silent)
            print(f"rebalance {asof}: book unchanged — pointer advanced, silent")
        else:
            print(f"no push event (asof {asof}) — silent")
        return 0

    missing = [c for c in (*_REQUIRED_CREDS, args.chat_env) if not os.environ.get(c)]
    if missing:
        print(f"missing credentials: {', '.join(missing)} — nothing sent")
        return 2
    text = render_text(
        status,
        status_changed_from=decision.status_changed_from,
        reminder=reminder,
        pilot=args.pilot,
    )
    chat_id = os.environ[args.chat_env].strip()
    book_prefix = content_hash(status)[:8]
    ok = asyncio.run(
        send(text, chat_id=chat_id, dedupe_key=f"sleeve-{asof}-{book_prefix}")
    )
    if ok:
        from datetime import UTC, datetime

        sent_at = datetime.now(UTC).isoformat()
        save_push_state(
            state_path,
            {**decision.state_after_send, "sent_at": sent_at},
        )
        append_advisory_history(
            Path(DEFAULT_ADVISORY_HISTORY),
            status,
            event=decision.event or "forced",
            delivered_at=sent_at,
        )
        print(f"sleeve advisory sent (event {decision.event or 'forced'}, asof {asof})")
        return 0
    # State untouched → the event (incl. a one-time KILLED notice) retries
    # on the next run instead of being swallowed by a failed send.
    print("Feishu API rejected the message — see backend logs")
    return 1


if __name__ == "__main__":
    sys.exit(main())
