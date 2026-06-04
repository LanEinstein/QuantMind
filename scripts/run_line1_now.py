#!/usr/bin/env python
"""On-demand Line-1 BUY-selection run — ops tool for a missed 09:35 cron.

WHY THIS EXISTS
    The Line-1 BUY basket only routes via the in-process 09:35 BrokerScheduler
    cron (``_line1_daily_callback``). When that cron is missed — e.g. a go-live
    boot that lands past the 5-minute APScheduler misfire window — there is no
    sanctioned on-demand path to run *today's* selection. This is that path.
    Owner-gated; reuses production components only (M-004 single construction
    point is preserved — the production builder assembles every InstructionPlan;
    this tool NEVER constructs one).

HOW IT WORKS
    1. Reuses ``dry_run_double_line.build_real_context`` + ``_run_line1`` for the
       EXACT production selection/render: real Tushare T-1 frame → screener →
       budget tier → CandidateSelector → per-candidate qwen 4-agent debate →
       builder ``assemble_plan`` (14-check single construction point) →
       inverse-volatility allocation clamp → render. ``now`` is the wall clock,
       so ``instruction_id`` + ``valid_until`` are fresh.
    2. Dispatch (``--send``) goes through the production ``InstructionDispatcher``
       (real Feishu send to FEISHU_DECISION_CHAT_ID + Mongo ``instruction_plans``
       persist via ``plan_repository`` so the running backend's WS receiver can
       correlate the owner's reply) — the same dispatcher the cron uses.

valid_until = now + 5 min (clamped 14:55, P0-3 §1.4). A signal MUST be dispatched
within 5 minutes of generation, so this tool selects + dispatches ATOMICALLY in
ONE process (sub-second gap). ``--preview`` and ``--send`` are SEPARATE
invocations; each does its OWN fresh selection so the send's valid_until is
always fresh. The owner approves a ``--preview`` (same codes), then a ``--send``
re-selects + dispatches only the approved codes.

Modes::

    # ZERO Feishu / Mongo writes / broker mutation — print the exact wire texts.
    python scripts/run_line1_now.py --preview

    # Real dispatch — ONLY plans whose stock_code is in --approve-codes (the
    # owner-approved allowlist). Double-gated (--confirm) + allowlist-gated so a
    # freshly-selected name the owner did NOT approve can never be sent.
    python scripts/run_line1_now.py --send --confirm \
        --approve-codes 605111,300433,002138

Red lines: real send requires FEISHU_INTERACTIVE_ENABLED=true + --confirm +
--approve-codes AND passes ``_preflight_or_exit`` (owner J-007 auth + live
backend healthy + clean ¥100k account + zero positions + no active freeze) so a
CLI can never bypass the acceptance/owner gates or send on stale account state;
goes ONLY to FEISHU_DECISION_CHAT_ID (never the alert chat); LLM never reaches
decision fields; no backend.{risk} import bypass.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

# This script lives in scripts/; when run as `python scripts/run_line1_now.py`
# the scripts dir is sys.path[0], so the sibling harness imports directly.
from dry_run_double_line import _run_line1, build_real_context

_LOCAL_API = os.environ.get("QUANTMIND_LOCAL_API", "http://127.0.0.1:8001")


def _now_sh() -> dt.datetime:
    from backend.utils.trading_hours import SHANGHAI

    return dt.datetime.now(SHANGHAI)


async def _preflight_or_exit(now: dt.datetime) -> None:
    """Fail-closed go/no-go BEFORE any real send (codex P1 #1 + #3).

    The on-demand selection sizes the basket against ``build_real_context``'s
    fresh ¥100k MockBroker, and a CLI must NOT bypass the production gates. So
    before dispatching we re-verify, against the LIVE backend's authoritative
    state, the runtime conditions that could have changed since it booted:

    * **Owner production authorization** (J-007 / PILOT cond2) — never bypassed.
    * **Live backend up + healthy** — its presence in feishu_interactive means
      the full 11-condition PILOT acceptance gate already passed at startup
      (the lifespan SystemExits otherwise); we defer to that decision.
    * **Clean go-live account baseline** — cash == ¥100k, zero positions — so
      the fresh-broker sizing matches the real account (else the basket could
      violate actual cash / position state).
    * **No active freeze** — any of the five buy/sell freeze sources active
      (mode-switch / reconciliation / circuit-breaker / data-quality / EOD)
      aborts the send.

    Any failure raises ``SystemExit`` (the send never fires).
    """
    import httpx

    from backend.services.owner_authorization import validate_owner_authorization

    # 1. Owner authorization — the J-007 gate; expiry must block a CLI send.
    validate_owner_authorization(env=os.environ, today=now.date())

    # httpx to the 127.0.0.1 literal: no DNS / no AAAA stall; trust_env=False so
    # a shell proxy never reroutes the loopback probe.
    async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
        health = (await client.get(f"{_LOCAL_API}/api/health/detailed")).json()
        if (health.get("data") or {}).get("status") != "ok":
            raise SystemExit(f"live backend not healthy: {health}")
        # Backend must actually be in feishu_interactive (codex P1): a backend in
        # simulation_auto has no WS receiver, so it would never consume the
        # owner's reply. A running backend reporting feishu_interactive also
        # proves it passed the 11-condition PILOT gate at startup (it would have
        # SystemExit'd otherwise) — the CLI defers to that, never bypasses it.
        risk = (await client.get(f"{_LOCAL_API}/api/risk/status")).json()
        rm = (risk.get("data") or {}).get("run_mode") or {}
        if not rm.get("feishu_interactive"):
            raise SystemExit(
                f"live backend is NOT in feishu_interactive mode (run_mode={rm});"
                " abort — the owner's reply would not be consumed"
            )
        acct = (await client.get(f"{_LOCAL_API}/api/trading/account")).json()
        a = acct.get("data") or {}
        if not (
            abs(float(a.get("total_assets", -1)) - 100000.0) < 1.0
            and abs(float(a.get("available_cash", -1)) - 100000.0) < 1.0
            and abs(float(a.get("frozen_cash", 1))) < 1.0
        ):
            raise SystemExit(
                f"live account is not the clean go-live baseline: {a}"
            )
        pos = (await client.get(f"{_LOCAL_API}/api/trading/positions")).json()
        held = pos.get("data") or []
        if held:
            raise SystemExit(f"live account holds {len(held)} position(s); abort")
        fz = (
            await client.get(f"{_LOCAL_API}/api/system-status/freeze-sources")
        ).json()
        fzd = fz.get("data") or {}
        if fzd.get("any_active"):
            active = [s["name"] for s in fzd.get("sources", []) if s.get("active")]
            raise SystemExit(f"a buy/sell freeze is active: {active}; abort")


async def _select(
    day: dt.date,
) -> tuple[object, list[tuple[object, str]], dt.datetime]:
    """Run today's Line-1 selection render-only; return (ctx, [(plan, wire)], now).

    ``now`` (the logical signal time used for ``instruction_id`` / ``valid_until``
    and threaded back into dispatch — exactly as the production cron does) is
    sampled AFTER the frame assembly so it reflects the real selection start, not
    the slower frame pull. The dispatcher does not hard-gate on ``valid_until``
    (the 14:55 cutoff is the real execution deadline, enforced at the owner-reply
    parser), so the wall-clock the debates consume does not block the send.

    This function NEVER mutates the shared ``llm:debates:{utc_date}`` fan-out
    counter (codex P2): clobbering it on every invocation would let a send wipe
    a preview's slots and let repeated runs bypass the ``max_debates_per_day``
    fail-closed cap. If a missed-cron back-fill genuinely needs the slots reset,
    the operator does it ONCE, deliberately, before the run (logged in the
    SESSION_LOG) — the tool itself stays cap-honest.
    """
    # Render with the active go-live tier's banner: in PILOT the production
    # runners pass pilot=True so the order-bearing wire carries the mandatory
    # "模拟盘 · 人工执行 · 试点" banner (codex P2). --preview and --send both use
    # it, so the previewed wire is byte-identical to what --send transmits.
    pilot = os.environ.get("QUANTMIND_FEISHU_TIER", "").strip().lower() == "pilot"
    ctx = await build_real_context(start_date=day, pilot=pilot)
    now = _now_sh()
    await _run_line1(ctx, now)

    wires = {s.instruction_id: s.wire_text for s in ctx.collector.signals}
    pairs: list[tuple[object, str]] = []
    for result in ctx.line1_results:
        for routed in result.routed_buys:
            plan = routed.plan
            wire = wires.get(plan.instruction_id)
            if wire is None:
                # Fail-closed: never carry a plan we cannot show/verify.
                raise RuntimeError(
                    f"no wire text captured for {plan.instruction_id}"
                )
            pairs.append((plan, wire))
    return ctx, pairs, now


def _print_basket(pairs: list[tuple[object, str]]) -> None:
    print(f"\n=== Line-1 basket: {len(pairs)} BUY signal(s) ===\n")
    for i, (plan, wire) in enumerate(pairs, 1):
        print(f"--- [{i}/{len(pairs)}] {plan.instruction_id} "
              f"({plan.stock_code}) ---")
        print(wire)
        print()


async def _dispatch(
    pairs: list[tuple[object, str]],
    *,
    approve_codes: frozenset[str],
    now: dt.datetime,
) -> int:
    """Dispatch the approved subset via the production InstructionDispatcher."""
    import motor.motor_asyncio as motor

    from backend.audit.store import AuditStore, InMemoryAuditCollection
    from backend.integrations.feishu.client import FeishuClient
    from backend.integrations.feishu.renderer import FeishuMessageKind
    from backend.orchestration.instruction_dispatcher import (
        InstructionDispatcher,
        OutboundSignal,
    )
    from backend.orchestration.mongo_outbox import MongoOutboxRepository
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
    )
    from backend.services.mongo_repositories import MongoInstructionPlanRepository

    if os.environ.get("FEISHU_INTERACTIVE_ENABLED", "").strip().lower() not in (
        "true",
        "1",
        "yes",
        "on",
    ):
        print("ERROR: --send requires FEISHU_INTERACTIVE_ENABLED=true.",
              file=sys.stderr)
        return 2
    chat = os.environ.get("FEISHU_DECISION_CHAT_ID", "").strip()
    if not chat:
        print("ERROR: FEISHU_DECISION_CHAT_ID is unset.", file=sys.stderr)
        return 2
    feishu = FeishuClient.from_env()
    if feishu is None:
        print("ERROR: FeishuClient.from_env() returned None (creds?).",
              file=sys.stderr)
        return 2

    to_send = [(p, w) for (p, w) in pairs if p.stock_code in approve_codes]
    skipped = [p.stock_code for (p, _) in pairs if p.stock_code not in approve_codes]
    missing = sorted(approve_codes - {p.stock_code for (p, _) in pairs})
    if skipped:
        print(f"[guard] NOT sending (not in --approve-codes): {skipped}")
    if missing:
        print(f"[guard] approved codes absent from this selection: {missing}")
    if not to_send:
        print("Nothing to send (no approved code is in the current basket).")
        return 1

    # Fail-closed go/no-go against the LIVE backend (owner auth + clean account
    # + no freeze) before any real send — codex P1 #1/#3. Raises SystemExit.
    await _preflight_or_exit(now)

    mongo_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    client = motor.AsyncIOMotorClient(mongo_uri, uuidRepresentation="standard")
    rc = 0
    try:
        db = client["quantmind"]
        await db.command("ping")
        plan_repo = MongoInstructionPlanRepository(db)
        # Double-send guard (codex P1): refuse a code that already has an active
        # BUY plan today (a prior 09:35 cron or earlier CLI dispatch the owner
        # has not reported yet — fresh QM ids defeat the outbox's id-based dedup,
        # and a clean ¥100k account passes the preflight). EXPIRED / REJECTED are
        # terminal-reusable; every other state is "in flight".
        today_compact = now.strftime("%Y%m%d")
        recent = await plan_repo.list_recent(limit=200, status=None, trade_date=None)
        active_buy_codes = {
            p.stock_code
            for p in recent
            if p.side.value == "BUY"
            and p.instruction_id.startswith(f"QM-{today_compact}-")
            and p.status.value
            in {"VALIDATED", "DISPATCHED", "FILLED", "AMBIGUOUS"}
        }
        dup = sorted(
            {p.stock_code for (p, _w) in to_send if p.stock_code in active_buy_codes}
        )
        if dup:
            print(f"[guard] REFUSING re-send — already an active BUY today: {dup}")
            to_send = [
                (p, w) for (p, w) in to_send if p.stock_code not in active_buy_codes
            ]
        if not to_send:
            print("Nothing to send (all approved codes already active today).")
            return 1
        # Open a ledger entry per plan BEFORE dispatch (codex P1): the dispatcher
        # appends PLAN_DISPATCHED on a successful send, which raises LookupError
        # when no entry exists — that would crash AFTER the Feishu message went
        # out but BEFORE the plan was persisted, leaving the owner reply
        # uncorrelatable. ``open_for_plan`` is idempotent.
        ledger = DecisionLedgerService(InMemoryLedgerRepository())
        for plan, _wire in to_send:
            await ledger.open_for_plan(plan, at=now)
        dispatcher = InstructionDispatcher(
            feishu_client=feishu,
            decision_chat_id=chat,
            outbox=MongoOutboxRepository(
                db[MongoOutboxRepository.COLLECTION_NAME]
            ),
            ledger=ledger,
            audit_store=AuditStore(
                InMemoryAuditCollection(),
                jsonl_path=Path("logs/run_line1_now_audit.jsonl"),
            ),
            # Persist each dispatched plan so the running backend's WS receiver
            # correlates the owner's reply (instruction_plans is the plan_lookup
            # source); the production cron dispatcher now wires this too (bug2).
            plan_repository=plan_repo,
        )
        for plan, wire in to_send:
            signal = OutboundSignal(
                plan=plan,
                wire_text=wire,
                message_kind=FeishuMessageKind.INSTRUCTION_PLAN,
            )
            print(f"SENDING {plan.instruction_id} ({plan.stock_code}) "
                  f"→ chat {chat} ...")
            outcome = await dispatcher.dispatch(signal, now=now)
            print(f"  action={outcome.action} "
                  f"feishu_message_id={outcome.feishu_message_id}")
            if outcome.action != "dispatched":
                print(f"  NOT dispatched (action={outcome.action!r}); see logs.")
                rc = 1
    finally:
        client.close()
    if rc == 0:
        print("\nOwner: review each suggestion in 决策群 and execute the ones you "
              "accept at your broker, then reply per the report template.")
    return rc


async def _amain(args: argparse.Namespace) -> int:
    # Require the go-live prod flag (codex P2): build_real_context resets the
    # shared live debate/reservation gate counters when QUANTMIND_PROD_RUN is
    # UNSET. This ops tool must run from the go-live shell (flag set) so it never
    # clobbers the running backend's fan-out / cost gates.
    if not os.environ.get("QUANTMIND_PROD_RUN"):
        print("ERROR: run_line1_now requires QUANTMIND_PROD_RUN set (go-live "
              "shell) so it never resets the live gate counters.", file=sys.stderr)
        return 2
    # A Line-1 signal needs a valid 5-min window before the 14:55 execution
    # cutoff (P0-3 §1.4): _derive_valid_until clamps to 14:55, which the schema
    # then rejects as <= created_at. Fail FAST with a clear message rather than a
    # confusing 0-BUY / SELECTION-FAILED traceback when a back-fill runs too late
    # (the selection itself takes minutes).
    if _now_sh().time() >= dt.time(14, 50):
        print("ERROR: past 14:50 Asia/Shanghai — a Line-1 signal cannot get a "
              "valid window before the 14:55 cutoff; back-fill is not possible "
              "this late today.", file=sys.stderr)
        return 2
    try:
        _ctx, pairs, now = await _select(_now_sh().date())
    except Exception:  # noqa: BLE001 — frame/selection failure is fatal
        import traceback

        print("run_line1_now: SELECTION FAILED", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1

    _print_basket(pairs)

    if not args.send:
        print("(--preview: ZERO send. Re-run with "
              "--send --confirm --approve-codes <codes> to dispatch.)")
        return 0

    if not args.confirm:
        print("ERROR: --send requires --confirm (deliberate second guard).",
              file=sys.stderr)
        return 2
    approve_codes = frozenset(
        c.strip() for c in (args.approve_codes or "").split(",") if c.strip()
    )
    if not approve_codes:
        print("ERROR: --send requires a non-empty --approve-codes allowlist.",
              file=sys.stderr)
        return 2
    return await _dispatch(pairs, approve_codes=approve_codes, now=now)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_line1_now",
        description="On-demand Line-1 run for a missed 09:35 cron (owner-gated).",
    )
    p.add_argument("--preview", action="store_true",
                   help="select + print the exact wire texts; ZERO send (default)")
    p.add_argument("--send", action="store_true",
                   help="real dispatch (requires --confirm + --approve-codes)")
    p.add_argument("--confirm", action="store_true",
                   help="deliberate second guard for --send")
    p.add_argument("--approve-codes", default=None,
                   help="comma-separated owner-approved stock codes; ONLY these "
                        "are dispatched")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
