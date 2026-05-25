# U-B2 Codex Review Summary — InstructionDispatcher + RouteCoordinator (mode-exclusive single routing + durable outbox)

**Task**: U-B2 — `backend/orchestration/{instruction_dispatcher,route_coordinator}.py` + `run_mode.py`/`mode_router.py` mutual-exclusion semantics fix
**Date**: 2026-05-25
**Cycles**: 1 review (`codex review --uncommitted`) + 2 read-only verifies (`codex exec --sandbox read-only`)

## Cycle 1 — findings (1 × P1, 1 × P2, both fixed)

- **[P1] Outbox did not provide at-most-once — stale-PENDING re-send** (`instruction_dispatcher.py`):
  the original design *resumed* a pre-existing `PENDING` claim by re-sending. After Feishu's
  1-hour `uuid` dedup window a restart/retry could re-send the same BUY/SELL to the owner — a
  real double-buy.
  **Fix**: `try_claim` is now the **at-most-once gate** — only the call that atomically creates the
  claim may send. A pre-existing claim returns `skipped_in_flight` **without** sending. A definitive
  API rejection (`send.ok == False`, nothing delivered) **releases** the claim so a clean retry can
  re-claim + re-send; a transport *exception* (ambiguous delivery) leaves the claim `PENDING` and is
  never auto-resent. Net: missing a signal is the safe failure; the owner is never double-messaged.

- **[P2] SENT short-circuit skipped bookkeeping forever** (`instruction_dispatcher.py`):
  a crash after `mark_sent()` but before the ledger write left the plan permanently without a
  `PLAN_DISPATCHED` ledger row, because the SENT path was an unconditional skip.
  **Fix**: the SENT short-circuit (and any retry) now calls `_finalize_dispatch()`, gated on the
  absence of a `PLAN_DISPATCHED` ledger row, so the marker is repaired exactly once.

## Cycle 2 — verify (1 new P2 surfaced from the cycle-1 fix, fixed)

The cycle-1 fix initially ordered the ledger marker **last** (after the read-model upsert). Codex
flagged that a crash after `plan_repository.upsert(status=DISPATCHED)` but before the marker append
leaves a **DISPATCHED read model with no marker**; a plan re-presented from that read model would
trip the `VALIDATED` guard before the SENT short-circuit could recover it.

**Fix** (two changes):
1. `_finalize_dispatch` now writes the **`PLAN_DISPATCHED` marker FIRST** (the single
   non-idempotent, correctness-critical, idempotency-gated step). The best-effort/fail-open derived
   bookkeeping (audit append-only row, read-model upsert by id, dedup-tolerant WS publish) runs
   **after** it. A persisted DISPATCHED read model can therefore never exist without the marker; a
   crash between the marker and the rest leaves at most a benign audit gap / cosmetically-stale read
   model (consistent with the project "fail-open for infra glitches" policy).
2. `dispatch()` runs only **structural** guards first (HOLD / empty `wire_text`), checks the **SENT
   short-circuit before the VALIDATED guard** (so a re-presented non-VALIDATED id is recovered, not
   rejected), and applies the VALIDATED guard only for a *fresh* send. `_finalize_dispatch` skips the
   state transition for an already-DISPATCHED recovery plan (avoids the illegal DISPATCHED→DISPATCHED).

## Cycle 3 — verify

**Verdict: PASS. No findings.** Confirmed: (a) the read-model-before-marker retry hole is closed;
(b) the original P1 at-most-once guarantees are intact (claim-gated send / no stale-PENDING re-send /
SENT never re-sends / release-on-rejection / transport-exception leaves PENDING); (c) no new
P0/P1/P2. The residual duplicate-`PLAN_DISPATCHED` race requires two *concurrent* finalisations of
the same instruction_id and is governed by the single-instance / single-writer deployment invariant
the EOD pipeline + AuditStore already rely on (tracked as a constraint, not a defect).

## Gate

20 new tests (11 dispatcher + 6 coordinator/resolver + 1 no-stray-`route`-caller AST guard + plan
helpers), module coverage 99% (dispatcher) / 96% (coordinator), `ruff` clean, `redline-check.sh`
green (incl. X-018 Phase-X isolation, M-004 single construction point untouched), full suite
**3624 passed / 11 skipped** (baseline 3604 → +20).
