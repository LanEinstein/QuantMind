# P0-7 Amendment (2026-06-23) — Risk-control live-data wiring (complete deferred "U-D3")

> **Status**: proposed → implementing (production-hardening audit pass, 2026-06-23).
> **Amends**: P0-7 (RiskEngine 14-check / circuit breaker) §2.4; references P0-6 §2.8
> (daily counters), P1-2.B (EquityPoint MTM). Safety-base redlines (single
> construction point, `backend/risk` purity, append-only, 127.0.0.1, LLM-never-on-
> decision-path, fail-closed) are **unchanged**.
> **Driver**: the 2026-06-23 production-readiness audit found that several documented
> §2.4 circuit-breaker controls are implemented + unit-tested but **never wired to
> live data**, so they do not bind the autopilot at runtime.

## 1. Finding (audit, verified in code)

The §2.4 redline is "熔断 ≤5 单/日 + 日亏 -5% + 连亏 3 笔 + 60min 冷却". Three of these
are **inert in production**:

- **R1 — CircuitBreaker is never fed / reset.** `CircuitBreaker.record_trade_result()`
  and `.reset()` have **zero callers** (`grep` repo-wide → only the definitions + one
  comment at `engine.py:937`). `is_halted()` therefore never returns True; Builder
  early-return #3 and check 13's cooldown branch are permanently dead.
- **R2 — checks 13/14 receive hardcoded zeros on the live BUY path.**
  `line1_context_provider.py:537-538` builds `DailyTradingState(today_portfolio_pnl_pct=0.0,
  last_3_trade_pnls=())` with a "wired in U-D3" stub; the cron calls
  `build_line1_run_state`/`build_line2_run_state` without the real values. So check 13
  (`0.0 <= -0.05` → always False) and check 14 (`len(()) < 3` → always PASS) never bind a BUY.
- **R3 — the daily order-count cap (check 10) does not survive a restart / second run.**
  `today_instruction_count` defaults to 0 in the cron, so the cap only counts BUYs within
  the current in-process basket; a mid-day restart or a second cron invocation resets it.

`assemble_daily_state` + `_count_dispatched_today` (in `backend/services/daily_state_assembler.py`)
already implement the real computation, but have **no production caller**.

### 1.1 Architectural discovery (why this is a feature, not a one-liner)

- **`MockBroker.get_account()` is cost-based, not mark-to-market** (`mock_broker.py:673`
  `mv = pos.cost_price * pos.volume`, `unrealized_pnl=0.0`). The real MTM lives in the
  separate **EquityPoint** system (`backend/broker/equity.py` → `equity_points` Mongo
  collection, written every 30s by the `intraday_mtm` cron; `MongoEquityPointRepository`
  is wired at `main.py:2288`). The daily-loss brake must therefore read NAV from the
  **equity-point repository**, not the account.
- **Realized per-trade PnL is not tracked.** `MockBroker._apply_sell` discards the cost
  basis; no trade/event carries realized PnL. The consecutive-loss streak (check 14) and
  the breaker's per-trade accumulation cannot be fed without first building that infra.

## 2. Decision

Wire the **dominant** unattended brakes now (they have clean, restart-safe data
sources); **defer** the consecutive-loss streak to a dedicated task that first builds
realized-PnL-on-close (it adds new code to the append-only fill path and must be
TDD'd carefully — it is the secondary control because the daily-loss −5% brake already
caps daily damage regardless of streak).

### 2.1 In scope (this amendment)

1. **R3 — order-count cap binds across runs/restarts.** Production crons compute
   `today_instruction_count` via `_count_dispatched_today(event_store, now)` (counts today's
   `ORDER_PLACED` + `EXECUTION_REPORT_APPLIED`, restart-safe because it reads the persisted
   append-only `broker_events`) and pass it into `build_line1_run_state` /
   `build_line2_run_state` at all four call sites (`main.py:1425,1467,1612,1670`).
2. **R2/R1 — daily-loss −5% brake on the live MTM NAV.**
   - `today_portfolio_pnl_pct = (current_nav − day_open_nav) / day_open_nav`, where
     `current_nav` = `equity_point_repository.get_latest().total_equity` **only when that
     point is dated today** (today-tick guard, below), and `day_open_nav` = the prior
     trading day's closing MTM equity via a new **read-only** repository method
     `get_latest_before_trade_date(today)` (unbounded lookback so a long A-share holiday gap
     never drops the reference; falls back to `initial_capital` on a genuine first session).
     Both read the persisted `equity_points` collection (restart-safe).
   - **Today-tick guard (codex review 2026-06-23):** `get_latest()` returns the newest
     point regardless of date, so before today's first 30s MTM tick (pre-open, or a lagging
     MTM cron) it is *yesterday's* close — using it would mis-state today's drawdown as
     yesterday's full-day P&L and could spuriously halt the 09:35 BUY scan. If the latest
     point is not dated today → `0.0` (fail-safe inactive, identical to the pre-amendment
     behaviour at that instant). A validated near-zero `current_nav` (real wipeout) yields a
     large negative ratio → halt (fail-safe), rather than the earlier `total_equity<=0 → 0.0`
     fail-open.
   - The computed `today_portfolio_pnl_pct` flows into `DailyTradingState` (replacing the
     `0.0` stub) via a new `Line1RunState.today_portfolio_pnl_pct` field, so **check 13**
     (daily-loss, BUY-only — SELL exits always allowed) binds against the real drawdown.
   - **CircuitBreaker gains one method** `observe_daily_drawdown(daily_pnl_pct, now)` that
     *sets* (does not accumulate) the NAV-based daily figure and trips the 60-min cooldown
     latch when `daily_pnl_pct <= -daily_loss_limit_pct`. Called from `build_line1/2_run_state`
     before `is_halted()` so `is_in_halt_cooldown` / `halt_until` reflect the live drawdown.
     `_daily_pnl_pct` is now authoritative-from-NAV (overwrite), not a per-trade sum.
3. **Breaker self-heals across days (no separate reset hook).** `observe_daily_drawdown`
   *overwrites* `_daily_pnl_pct` with the NAV-based daily figure every run (it is not a
   running per-trade sum), and `is_halted()` already auto-expires `_halted_at` after the
   60-min cooldown — so a new session's small drawdown produces no trip, and a mid-day
   restart re-trips immediately from the persisted NAV if the drawdown still stands. An
   explicit `reset()` at the day-roll is therefore redundant and is intentionally omitted to
   avoid coupling the scheduler to the breaker singleton. (`_consecutive_losses` stays 0
   until the deferred realized-PnL task wires it; `reset()`/`record_trade_result` keep their
   existing semantics for that task.)
4. **R5 — default agrees with the redline.** `RiskConfig.max_total_positions` field default
   `10 → 5` (the ≤5 owner hard cap). The live engine already loads `5` from
   `config/risk.yaml`; this removes the foot-gun where a future `RiskConfig()` default
   construction would silently allow 10 concurrent positions.

### 2.2 Deferred (separate task #Batch1b + its own amendment)

- **check 14 (consecutive-3-loss) + CircuitBreaker per-trade streak.** Requires
  realized-per-trade PnL: build realized-pnl-on-close in the broker (avg-cost basis −
  fees, emitted on the SELL trade/`ORDER_FILLED` event), a last-N realized-PnL store, and
  recovery replay, then wire `last_3_trade_pnls` + `record_trade_result`. Until then
  check 14 PASSes (its documented empty-history behaviour) and the streak counter stays 0.

## 3. Redline impact

- **No new write endpoint** (still 3). **No LLM** anywhere near this path. **`backend/risk`
  stays pure** — the NAV/count assembly lives in `backend/services` (the assembler /
  context providers), exactly as `DailyTradingState`'s docstring requires; `engine.py`
  is unchanged. **Single construction point** untouched (no `InstructionPlan` built here).
- New surface area: one CircuitBreaker method (`observe_daily_drawdown`), one read-only
  equity-repo method (`get_session_open_equity`), one `Line1RunState`/`Line2RunState`
  field (`today_portfolio_pnl_pct`), and reset/count wiring in the crons. All additive;
  dormant-equivalent when the equity repo is absent (cold start → `day_open_nav` falls back
  to `initial_capital` → `pnl_pct` ≈ realized-only, brake still fail-closes on a real loss).
- **Direction of change is strictly safer in normal operation**: it activates documented
  protective halts that were previously dead, and a tripped daily-loss halt blocks new BUYs
  while always allowing SELL exits (check 13 `apply_to_sell=False`). Fail-direction on a
  *degraded* input follows CLAUDE.md §3: an equity-store **infra glitch** (repo absent /
  read error / cold start with no points) fails **open** — `today_portfolio_pnl_pct=0.0`,
  brake inactive that run — because the always-on layer (per-position ATR/drawdown stops +
  the ≤5 position/order caps, now restart-safe via R3) still binds; the daily-loss brake is
  one defence-in-depth layer, not the sole guard. A *present* NAV read can only mis-state by
  the equity store's own MTM accuracy.

## 4. TDD plan (RED→GREEN)

- `CircuitBreaker.observe_daily_drawdown`: trips at exactly `-daily_loss_limit_pct`, latches
  cooldown for `cooldown_minutes`, auto-expires, ignores non-finite, overwrites (not sums).
- `build_line1/2_run_state`: with an injected fake equity repo returning a prior-day close,
  `today_portfolio_pnl_pct` is correct; `today_instruction_count` reflects the fed count;
  `is_in_halt_cooldown` True once drawdown ≤ −5%.
- `get_latest_before_trade_date`: returns the prior trading-day close; unbounded across a
  long holiday gap; `None` only on a genuine first session.
- `compute_today_portfolio_pnl_pct`: today-tick guard (a `latest` not dated today → 0.0);
  zero-equity wipeout → large-negative ratio (fail-safe halt); cold-start seed fallback.
- End-to-end: a −6% MTM day makes a fresh BUY REJECT at check 13 while a SELL still passes;
  the order-count cap rejects the 6th BUY of the day and survives a simulated restart
  (re-reading `_count_dispatched_today`).
- Regression: the full existing suite stays green (the assembler / engine tests already
  cover the logic; this wires real inputs). `config/risk.yaml`-loaded engine behaviour is
  byte-identical (it already used 5); only the in-code default changes.

## 5. Rollout

`enabled by construction once merged` — there is no env flag; the brakes simply begin to
bind. Because **sim is paused**, this lands and is validated by tests before the owner
resumes unattended operation. No production data migration. `codex review` gate per §3.
