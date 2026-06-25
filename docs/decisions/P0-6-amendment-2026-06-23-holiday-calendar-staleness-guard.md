# P0-6 Amendment (2026-06-23) — Holiday-calendar fail-closed staleness guard

> **Status**: proposed → implementing (production-hardening audit pass).
> **Amends**: P0-6 §2.8 (static `config/holidays.yaml`, ops-curated annually,
> akshare 节假日 API barred at runtime). Keeps the static-YAML / no-hot-reload
> design; adds a fail-closed guard on top.
> **Driver**: the 2026-06-23 audit found `holidays_2027` is a 1-date placeholder
> and `last_verified` / `schedule_version` are loaded into `HolidayTable` but
> consulted by **no guard**. An unattended run crossing into 2027 would treat
> 2027 holiday weekdays (e.g. the whole Spring-Festival week) as trading days —
> routing BUY baskets, 30s SELL ticks, `advance_day` settlement, and the EOD
> chain against absent/stale data, with no alert. Separately, `_eod_pipeline_job`
> lacks the `is_trading_day` gate its sibling cron jobs all have.

## 1. Decision

Add a fail-closed staleness guard so an un-curated calendar **stops the autopilot**
instead of silently mis-classifying holidays as trading days, and gate the EOD job.

### 1.1 In scope

1. **Staleness primitive** (`backend/utils/holiday_loader.py`, pure stdlib):
   - `CalendarStaleError`.
   - `calendar_staleness_reason(today, *, table=None) -> str | None` (non-raising) —
     stale when the **current operating year's** `holidays_YYYY` block is missing or a
     placeholder (`< _MIN_HOLIDAYS_PER_YEAR = 10` dates; a fully-curated A-share year
     lists ~15-20 holiday weekdays, Spring Festival + National Day alone ~10, so 10 is a
     conservative floor that every placeholder/half-curated year fails). `last_verified` is
     folded into the message but the per-year count is decisive.
   - `calendar_forward_warning(today, *, table=None) -> str | None` — a SEPARATE **soft**
     warning (never raises) for the December-onward case where *next* year's block is not
     yet curated. NOT a stale condition: a hard boot-block there would brick startup weeks
     before the State Council publishes the next-year 放假安排 notice (codex review
     2026-06-23). The current year still trades fine.
   - `assert_calendar_covers(today, *, table=None)` — raising form (current year only).
2. **Boot fail-fast** (`backend/main.py`): call `assert_calendar_covers(today_shanghai)`
   during startup, **before** the broker scheduler starts. A stale **current year** refuses
   to boot — an unattended run can never *start* operating inside a placeholder calendar.
   The December next-year shortfall is logged via `calendar_forward_warning` (never blocks
   boot). Env-guarded: when `QUANTMIND_HOLIDAYS_PATH` is set (test / alternate deploy;
   production leaves it unset) the boot warns instead of raising so a minimal test calendar
   can boot.
3. **EOD holiday-gate** (`backend/broker/scheduler.py:_eod_pipeline_job`): skip on a
   non-trading day (mirrors `_advance_day_job` et al.). The EOD snapshot/chain must not
   run for a session that never happened.
4. **Runtime guard on the position-opening path** (`backend/main.py` Line-1 09:35 BUY
   cron): before routing the BUY basket, if `calendar_staleness_reason(today)` is not
   None → log + fire a `critical` Feishu alert (dedup per day) + skip (fail-closed: open no
   new positions on an un-curated calendar). Because the Line-1 cron runs every trading day,
   it is the **runtime tripwire**: an in-flight cross-year staleness (a long run that crosses
   New Year without a restart, so boot never re-checked) surfaces as a daily critical alert.

### 1.2 Out of scope / residual

- The Line-2 SELL/monitoring crons are not individually gated on staleness this pass
  (exits are the safe direction, and boot fail-fast + the daily Line-1 staleness alert +
  gate cover the dangerous "open positions on a wrong day" path). For a *covered* year
  `is_trading_day` is already correct; the guard only matters for an *un-curated* year,
  which the boot + Line-1 + alert layers catch. A future pass may route every trading cron
  through a shared `trading_day_ok()` helper.
- `is_trading_day`'s pure semantics are unchanged (callers like `prev_trading_day` /
  `next_trading_day` that iterate it must not start raising/looping).

## 2. Redline impact

No new write endpoint, no LLM, `backend/risk` import path unaffected (the guard lives in
`backend/utils/holiday_loader`, which `backend/risk` already imports via `trading_hours`).
Direction of change is **fail-closed for data corruption** (an un-curated calendar is
treated as a corrupt input → stop), exactly per CLAUDE.md §3. Annual ops maintenance and
the static-YAML SSoT are unchanged; the guard only *enforces* that the maintenance happened.

## 3. TDD plan

- `assert_calendar_covers` / `calendar_staleness_reason`: passes for a curated current year;
  raises/returns-reason for a placeholder year; December forward-coverage requires next
  year; boundary at exactly `_MIN_HOLIDAYS_PER_YEAR`.
- `_eod_pipeline_job` skips on a non-trading day (does not call `run_eod_pipeline`).
- Full suite + ruff + redline green; `codex review` gate per §3.
