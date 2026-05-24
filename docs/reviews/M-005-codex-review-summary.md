# M-005 Codex Review Summary — cost_guard 真·预留硬上限 + max_debates_per_day + fan-out cap

> Task: M-005 `cost_guard 真·预留硬上限 + max_debates_per_day + P1-7 amendment 落地`.
> Implements [P1-7-amendment-2026-05-24](../decisions/P1-7-amendment-2026-05-24-precall-reservation-fanout-cap.md):
> the ¥20/day hard cap becomes a pre-call reservation (the crossing call never
> happens, vs the old post-hoc trailing-stop), the multiplicative fan-out is
> bounded (`max_debates_per_day`, one debate per shortlist not per candidate),
> and the reservation lives in the unified `llm:usage:{utc_date}` namespace.
> Scope: `backend/services/cost_guard.py`, `backend/services/cost_probe.py`,
> `backend/agents_team/graph.py` (+ `__init__.py`), `scripts/redline-check.sh`,
> tests. The 4 P1-7 ceiling constants (¥20 / 0.7 / ¥440 / ¥4) are unchanged —
> only the execution semantics change.

## What M-005 adds

- `cost_guard.reserve_budget` / `settle_budget` + `BudgetReservation`:
  atomic `incrbyfloat` reservation against `llm:usage:{date}:reserved`; refuse
  (roll back + raise) when `spent + reserved > ¥20`; settle releases the
  reservation (actual spend recorded by the router's track_usage). TTL backstops
  a crashed caller.
- `cost_guard.reserve_debate_slot` + `get_max_debates_per_day` /
  `get_max_anomaly_llm_per_day`: per-UTC-day debate counter
  (`llm:debates:{date}`) capping fan-out; runtime-immutable env-read caps.
- `agents_team.run_shortlist`: ONE budgeted debate per shortlist (reserve → slot
  → single `run_team` on the lead candidate → settle in `finally`); a
  20-candidate shortlist triggers exactly one debate.
- `cost_probe._parse_usage_key`: validates key shape before `hgetall` so the
  string-typed reservation counter is skipped without a WRONGTYPE error.
- `redline-check.sh [M-005]`: asserts reserve/settle/slot + new caps present,
  the 4 ceilings unchanged, reservation in the `llm:usage` namespace, and the
  agents_team debate claims a slot.

## Local gate (pre-codex)

- `pytest tests/test_cost_guard_reservation.py tests/agents_team/test_shortlist_budget.py`
  → 23 passed; `cost_guard` coverage 100%, `agents_team/graph.py` 100%.
- Full suite → 3482 passed / 11 skipped.
- `ruff` clean; `scripts/redline-check.sh` → all green incl. new `[M-005]`.

## Cycle 1 — `codex review --uncommitted`

**1 finding, P1 (no P0).**

- **[P1] Include reservations in legacy budget checks** —
  `backend/services/cost_guard.py`. The pre-call reservation was stored only in
  `llm:usage:{date}:reserved`, but the legacy budget path
  (`get_daily_budget_state` / `assert_budget_allows`) read only actual
  `get_daily_spent` (cost_probe deliberately skips the reserved key). So a
  legacy caller (analysis_scheduler / shadow baseline / GEPA) could pass the
  old guard while a debate reservation was in flight — ¥19 actual + a ¥1
  in-flight reservation reads `< ¥20` on the legacy path and starts another
  paid call, defeating the hard cap.

  **Fix:** added `get_daily_reserved(redis)` and folded it into
  `get_daily_budget_state`: `spent_today = raw_spent + reserved`. Now BOTH
  guards (the new `reserve_budget` and the legacy `assert_budget_allows`) see
  in-flight reservations, so the ¥20 cap holds for every caller.
  `reserve_budget` keeps using `get_daily_spent` + its own counter read, so
  there is no double-count. `get_daily_reserved` only accepts scalar
  str/bytes/int/float (rejecting `bool` and non-scalar test doubles whose
  `__float__` defaults to 1.0) and is fail-open (0.0) on read error / None /
  unparseable / negative / non-finite — the dependable enforcement remains
  `reserve_budget`'s own atomic check. Added 4 regressions (legacy path sees
  in-flight reservation → hard_breach; released after settle; fail-open on
  unreadable; parametric scalar parsing). Re-ran: 116 cost_guard tests pass,
  module coverage 100%; full suite 3495 passed.

## Cycle 2 — `codex exec --sandbox read-only` (verify)

**Verdict: PASS** — `get_daily_budget_state` now adds `get_daily_reserved()` to
actual spend, so `assert_budget_allows` sees ¥19 + ¥1 == ¥20 as `hard_breach`
and blocks legacy callers during an in-flight reservation; `reserve_budget`
still uses `get_daily_spent()` + the post-increment counter (no double-count);
`get_daily_reserved` only accepts scalar Redis values, rejects bool / mock /
object garbage before `float()`, and fail-opens (0.0) on error / None / bad /
negative / non-finite — that fail-open is limited to legacy reporting, primary
enforcement remains `reserve_budget`'s atomic check + rollback. 0 regressions.
