#!/usr/bin/env python
"""On-demand reconciliation initiation — ops tool (P0-5-amendment-2026-06-03).

WHY THIS EXISTS
    The reconciliation INITIATE / ticket-creation path was never wired into
    production: ``initiate_reconciliation`` had no caller, ``run_eod_pipeline``
    never creates a ticket, and nothing constructs a ``ReconciliationTicket``.
    So ``_handle_mismatch`` (which requires a pre-existing OPEN ticket) can
    never fire — the owner has no way to reconcile / restore the mirror. This
    is that path: owner-gated, on-demand.

WHAT IT DOES (creates an OPEN ticket + sends the prompt — NEVER mutates the
mirror)
    1. recover_state() reads the CURRENT broker mirror (read-only).
    2. Builds + persists a ``BrokerSnapshot`` of that mirror (append-only via
       BrokerSnapshotStore) — its snapshot_id is resolvable by
       MongoSnapshotLookup, so ``_handle_mismatch`` can recompute the deviation.
    3. Creates an OPEN ``ReconciliationTicket`` (expected_snapshot_id = that
       snapshot) and persists it. NOTE: an OPEN ticket FREEZES BUY/SELL routing
       on the running backend (§2.7) until resolved — a deliberate safety gate.
    4. Sends the reconciliation prompt to the DECISION chat (never the alert
       chat) via the production renderer + FeishuClient.

    The mirror rewrite happens LATER, owner-gated, through the ALREADY-WIRED
    running backend: the owner replies in the decision chat:
        对账差异 <ticket_id> 现金 <真实可用现金> 持仓 <code> <vol>股 成本 <cost>; ...
        对账采纳：用户回报 <ticket_id>
    which flow through handle_reply → decide_ticket → reset_to_snapshot.

USAGE
    # ZERO writes / sends — print the ticket plan + the exact wire texts.
    python scripts/reconcile_now.py --preview

    # Real: persist snapshot + OPEN ticket + send the decision-chat prompt.
    python scripts/reconcile_now.py --send --confirm

Red lines: requires QUANTMIND_PROD_RUN + a valid owner authorization env;
--send also requires FEISHU_INTERACTIVE_ENABLED=true + --confirm +
FEISHU_DECISION_CHAT_ID. The tool NEVER calls the applier and NEVER mutates the
broker mirror; it only creates an OPEN ticket (single construction via
build_open_reconciliation_ticket) + sends the prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# The owner's real fill prices for the 5 held positions (2026-06-03), shown in
# the next-step reply template so the owner doesn't have to re-derive them.
_HELD_TEMPLATE = (
    "605111 200股 成本 63.035; 300433 100股 成本 41.313; "
    "600909 800股 成本 7.152; 600011 600股 成本 9.213; "
    "605020 200股 成本 34.341"
)


def _preflight_or_exit(now: datetime) -> None:
    """Owner-gate: QUANTMIND_PROD_RUN truthy + a valid owner authorization env."""
    from backend.services.owner_authorization import (
        is_production_run,
        validate_owner_authorization,
    )

    # is_production_run enforces the canonical truthy set {true,1,yes,on} —
    # a bare os.environ.get truthy check would let "0"/"false" through (codex P2).
    if not is_production_run(env=os.environ):
        print(
            "ERROR: reconcile_now requires QUANTMIND_PROD_RUN=true (run from "
            "the go-live shell).",
            file=sys.stderr,
        )
        sys.exit(2)
    validate_owner_authorization(env=os.environ, today=now.date())  # raises if invalid


async def _next_ticket_id(db: object, trade_date_compact: str) -> str:
    """Return RECON-<yyyymmdd>-NNN with NNN one past today's max (fail-safe 001)."""
    prefix = f"RECON-{trade_date_compact}-"
    cursor = db["reconciliation_tickets"].find(  # type: ignore[index]
        {"ticket_id": {"$regex": f"^{prefix}"}},
        projection={"ticket_id": 1},
    )
    max_seq = 0
    async for doc in cursor:
        tid = str(doc.get("ticket_id", ""))
        try:
            max_seq = max(max_seq, int(tid.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return f"{prefix}{max_seq + 1:03d}"


async def _amain(args: argparse.Namespace) -> int:
    now_utc = datetime.now(tz=UTC)
    now_sh = now_utc.astimezone(SHANGHAI)
    _preflight_or_exit(now_sh)

    import motor.motor_asyncio as motor

    from backend.broker.persistence import recover_state
    from backend.broker.persistence.checksum import compute_snapshot_checksum
    from backend.broker.persistence.snapshots import BrokerSnapshot
    from backend.broker.persistence.store import (
        BrokerEventStore,
        BrokerSnapshotStore,
    )
    from backend.integrations.feishu.renderer import MessageRenderer
    from backend.services.mongo_repositories import MongoTicketRepository
    from backend.services.reconciliation_initiate import (
        build_open_reconciliation_ticket,
    )

    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    client = motor.AsyncIOMotorClient(mongo_uri, uuidRepresentation="standard")
    db = client["quantmind"]
    event_store = BrokerEventStore(client, db[BrokerEventStore.COLLECTION_NAME])
    snapshot_store = BrokerSnapshotStore(
        client, db[BrokerSnapshotStore.COLLECTION_NAME]
    )

    recovered = await recover_state(
        event_store=event_store,
        snapshot_store=snapshot_store,
        initial_capital=100000.0,
    )
    snap_positions = recovered.to_snapshot_positions()
    trade_date = now_sh.strftime("%Y-%m-%d")
    ticket_id = await _next_ticket_id(db, now_sh.strftime("%Y%m%d"))

    checksum = compute_snapshot_checksum(
        recovered.cash,
        recovered.frozen_cash,
        recovered.initial_capital,
        snap_positions,
    )
    broker_snapshot = BrokerSnapshot(
        created_at=now_utc,
        trade_date=trade_date,
        last_event_sequence=recovered.last_sequence,
        cash=recovered.cash,
        frozen_cash=recovered.frozen_cash,
        initial_capital=recovered.initial_capital,
        positions=snap_positions,
        checksum=checksum,
        metadata={"source": "reconcile_now"},
    )
    expected_positions = {p.code: p.volume for p in snap_positions}
    expected_total_equity = recovered.cash + sum(
        p.cost_price * p.volume for p in snap_positions
    )
    body = MessageRenderer().render_reconciliation_request(
        ticket_id=ticket_id,
        trade_date=trade_date,
        expected_cash_cny=recovered.cash,
        expected_positions=expected_positions,
        expected_total_equity_cny=expected_total_equity,
    )

    print("=== reconcile_now ===")
    print(
        f"current mirror (expected): cash={recovered.cash} "
        f"positions={len(snap_positions)} last_seq={recovered.last_sequence}"
    )
    print(f"ticket_id: {ticket_id}")
    print("--- Feishu initiation prompt (decision chat) ---")
    print(body)
    print("--- owner next-step replies (paste into the DECISION chat) ---")
    print(
        f"对账差异 {ticket_id} 现金 <你的真实可用现金> 持仓 {_HELD_TEMPLATE}"
    )
    print(f"对账采纳：用户回报 {ticket_id}")

    if not args.send:
        print(
            "\n(--preview: ZERO Mongo writes / Feishu sends. Re-run with "
            "--send --confirm to create the OPEN ticket + send the prompt.)"
        )
        return 0

    if not args.confirm:
        print(
            "ERROR: --send requires --confirm (deliberate second guard).",
            file=sys.stderr,
        )
        return 2
    if os.environ.get("FEISHU_INTERACTIVE_ENABLED", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        print(
            "ERROR: --send requires FEISHU_INTERACTIVE_ENABLED=true.",
            file=sys.stderr,
        )
        return 2
    chat = os.environ.get("FEISHU_DECISION_CHAT_ID", "").strip()
    if not chat:
        print("ERROR: FEISHU_DECISION_CHAT_ID unset.", file=sys.stderr)
        return 2

    # Guard against overwriting an existing ticket: MongoTicketRepository.save
    # upserts by ticket_id, so a colliding id (e.g. a concurrent --send that
    # picked the same seq) would silently clobber an open ticket. Re-check
    # right before writing and abort rather than overwrite (codex P2). The
    # tool is manually run one-at-a-time, so an explicit abort is sufficient.
    ticket_repo = MongoTicketRepository(db)
    if await ticket_repo.get(ticket_id) is not None:
        print(
            f"ERROR: ticket {ticket_id} already exists — refusing to "
            "overwrite. Re-run (a fresh seq will be allocated).",
            file=sys.stderr,
        )
        return 2
    # 1. Persist the expected-state BrokerSnapshot (append-only).
    persisted = await snapshot_store.append(broker_snapshot)
    snapshot_id = str(persisted.snapshot_id)
    # 2. Create + persist the OPEN ticket (single construction point).
    ticket = build_open_reconciliation_ticket(
        ticket_id=ticket_id,
        trade_date=trade_date,
        created_at=now_utc,
        expected_snapshot_id=snapshot_id,
    )
    await ticket_repo.save(ticket)
    # 3. Send the decision-chat prompt.
    from backend.integrations.feishu.client import FeishuClient

    feishu = FeishuClient.from_env()
    if feishu is None:
        print("ERROR: FeishuClient.from_env() returned None (creds?).", file=sys.stderr)
        return 2
    result = await feishu.send_message(chat, body, uuid=f"recon-init-{ticket_id}")
    ok = bool(getattr(result, "ok", False))
    print(f"\nSENT: {'ok' if ok else f'FAILED {result}'}")
    print(f"snapshot_id={snapshot_id}")
    print(
        "OPEN ticket created — BUY/SELL routing is now FROZEN on the running "
        "backend until you resolve it. Reply the two messages above in the "
        "decision chat to restore the mirror + clear the freeze."
    )
    return 0 if ok else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reconcile_now",
        description="On-demand reconciliation initiation (P0-5-amendment-2026-06-03).",
    )
    p.add_argument(
        "--preview", action="store_true", help="print the plan; ZERO writes/sends"
    )
    p.add_argument(
        "--send",
        action="store_true",
        help="persist snapshot + OPEN ticket + send prompt",
    )
    p.add_argument(
        "--confirm", action="store_true", help="deliberate second guard for --send"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.preview and not args.send:
        print("Specify --preview or --send --confirm.", file=sys.stderr)
        return 2
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
