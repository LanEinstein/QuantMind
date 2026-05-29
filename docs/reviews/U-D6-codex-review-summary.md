# U-D6 review summary — PILOT gate live-probe wiring (cond9 / cond10a / cond10b)

> **Task**: U-D6 (P0-6-amendment-2026-05-29). Wire the three never-implemented PILOT live-probes so the gate's verdict reflects live system health.
> **Reviewer**: claude `/code-review high` (3 correctness + 4 cleanup/altitude angles → verify). codex CLI on rate-limit fallback through ~2026-05-31 (owner-directed; memory `feedback_codex_rate_limit_fallback`).
> **Date**: 2026-05-29
> **Scope (diff)**: `backend/llm/fallback.py`, `backend/llm/router.py`, `backend/main.py`, new `backend/services/pilot_data_probe.py`, new `tests/test_pilot_live_probes.py`.

## Verdict
1 review cycle, 2 parallel finder agents (correctness + cleanup/altitude). All actionable P1/P2 (HIGH/MEDIUM) findings fixed; P3 acknowledged-by-design. Local gates green: ruff ✅ / redline-check ✅ / full suite **4074 passed, 13 skipped** (clean env).

## Findings & dispositions

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | P2 | cond10a counts per provider **attempt** (primary+fallback+escalation), and only `APITimeoutError` counts as a timeout → attempt-level rate, blind to non-timeout failures (rate-limit/connection). | **By design — kept.** cond10a is timeout-specific (mirrors acceptance `llm_timeout_rate`); numerator + denominator are both per-attempt = internally consistent. Bias is marginal and the dominant boot case is cold-start (0/0). Docstrings sharpened to state attempt-level + timeout-specific scope explicitly. |
| 2 | P2 | `track_llm_call`/`track_llm_timeout` did `incr` then a **separate** `expire` (2 round-trips, non-atomic TTL; an `incr`-then-`expire`-fail leaves a key without TTL). | **Fixed.** Extracted `_incr_daily_counter` using the module's existing pipelined `incr+expire` idiom (same as `track_usage`/`track_escalation`) — single round-trip, TTL set atomically. |
| 3 | P2 | `MANDATORY_ETF_CANARIES` hard-duplicates the P0-9 ETF triple that also defaults `ConcentrationExceptionConfig.etf_whitelist` → silent drift risk on a future amendment. | **Fixed (guard).** Added `test_canaries_match_riskconfig_etf_whitelist` asserting the two sites stay equal, so any future divergence fails CI and is reconciled by a human. (Kept as a separate constant: canary-reachability and concentration-exception are distinct concepts that happen to share the locked triple.) |
| 4 | P2 | `read_llm_timeout_rate` did two sequential `GET`s. | **Fixed.** Single `mget` round-trip. |
| 5 | P3 | Inconsistent unwired-dependency handling: cond9/cond10b return `False`, cond10a relied on raise+`_safe_await`. | **Fixed.** `_llm_timeout_ok` now returns `False` explicitly on `redis is None` (matches cost_guard convention); `read_llm_timeout_rate` keeps raising defensively as a backstop. |
| 6 | P3 | `_incr_daily_counter` duplicated the incr+expire idiom. | **Fixed** as part of #2 (shared helper). |
| — | n/a | cond9 passes against a "responding but stale" feed (staleness not gated at boot). | **By design** (owner chose infra-reachability semantics 2026-05-29; per-code freshness is the builder/RiskEngine's job at trade time). Documented in amendment §1.1. |
| — | n/a | Full production `DataQualityProvider` (4 concrete probes) still absent → trade-time per-code DQ gate is a clean-default no-op. | **Pre-existing gap, documented** in amendment §4 as a follow-up; out of U-D6 scope. cond9 infra-reachability does not depend on it. |

## cond10b (the boot blocker)
`state.daily.status` → `state.status`. `get_daily_budget_state()` returns a `DailyBudgetState` whose status attribute is `.status` (no `.daily` nesting). The old code raised `AttributeError` on every boot → `_safe_await` fail-closed → cond10b permanently unmet. Now correct; pinned by `test_cond10b_*`.

## Tests added (`tests/test_pilot_live_probes.py`, 26)
counters (incr/mget/cold-start/None/failure-fail-open), router instrumentation (call counted always, timeout counted+re-raised, success not double-counted), cond9 reachability (all/single-leg/both-none/None-market-data/empty/probe-exception/locked-triple/divergence-guard), and `_build_pilot_probe` integration through the real `PilotReadinessProbe` for cond9/cond10a/cond10b (this integration test would have caught the original cond10b `AttributeError` and the cond9/cond10a return-False stubs).
