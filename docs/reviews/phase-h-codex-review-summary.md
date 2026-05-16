# Phase H — Codex Review Summary (Session #15)

**Task scope:** H-002 audit double-write + H-003 cost_guard P1-7 extension + H-004 alert matrix
**Cycles:** 3 + 1 final verification
**Final verdict:** ✅ **PASS** — 7/7 prior issues RESOLVED, 0 new critical regressions
**Reviewer:** Codex CLI 0.130.0 (`codex review --uncommitted` + `codex exec`)
**Author:** Claude Opus 4.7 (1M context)

## Cycle 1 — Initial review (`codex review --uncommitted`)

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 1 | P2 | `backend/api/audit.py:173` | Motor BSON Date naive UTC → API shifts rows on Asia/Shanghai hosts | `_hydrate()` pins tz to UTC when `ts.tzinfo is None` |
| 2 | P2 | `scripts/query_audit.py:185` | Same naive UTC issue in CLI | CLI `_hydrate()` applies same tz pin |
| 3 | P2 | `backend/services/cost_guard.py:273-277` | `QUANTMIND_MONTHLY_BUDGET=0` + spend produces `float('inf')` → FastAPI serialization 500 | Zero-budget+spend caps `fraction=1.0` finite sentinel; `status=threshold_100` preserved |

Regression tests added:
- `tests/test_api_audit.py::test_naive_mongo_timestamp_normalised_to_utc`
- `tests/test_query_audit_cli.py::test_hydrate_normalises_naive_mongo_timestamp`
- `tests/test_cost_guard_p1_7.py::test_zero_budget_with_spend_yields_finite_fraction`
- `tests/test_cost_guard_p1_7.py::test_zero_budget_zero_spend_is_ok`

## Cycle 2 — Verification + new issues

Prior 3: all **RESOLVED**.

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 4 | P2 | `backend/services/soft_degrade_manager.py:120,156` | `activate_kimi_escalation_block` + `maybe_fire_monthly_milestone` test-only — soft breach does not actually block Kimi; monthly milestones don't fire | Wired `AnalysisScheduler._run_and_persist_locked` → on `soft_breach` calls `activate_kimi_escalation_block(reason="daily_soft_breach")`; per-tick `_maybe_emit_monthly_milestone_safely()` via injected `alert_dispatcher`; `LLMRouter._is_kimi_escalation_blocked()` veto before Kimi escalation |
| 5 | P2 | `backend/services/cost_probe.py:207` | `_parse_usage_key` `data.get("cost_rmb", 0.0)` doesn't decode bytes keys — silent fail-open on default Redis client | Added `_normalize_hash()` that decodes both bytes keys and values; called before `data.get(...)` |

Also: `SoftDegradeManager.is_kimi_escalation_blocked` tightened to require `isinstance(value, str|bytes)` — defends against `AsyncMock`-returning unit-test redis doubles that would otherwise spuriously veto Kimi.

## Cycle 3 — Verification + new issues

Prior 5: all **RESOLVED**.

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 6 | P2 | `backend/llm/router.py` | `assert_kimi_budget_allows` / `KimiDailyCapExceededError` exist but no production caller — Kimi ¥4 hard cap not enforced | Added `_kimi_daily_cap_breached()` probe; veto Kimi escalation before `track_escalation` when `KimiBudgetState.status=='hard_breach'`; fail-open on Redis error |
| 7 | P3 | `backend/data/analysis_scheduler.py` | `_maybe_emit_monthly_milestone_safely` only ran on the successful `assert_budget_allows` branch — daily-hard-breach tick silently swallowed monthly milestones | Hoisted milestone evaluation out of the `try/else`; runs regardless of daily-breach outcome with deferred `return None` on hard breach |

Regression tests added:
- `tests/test_analysis_scheduler_h003_wiring.py` (6 tests: soft-breach activates Kimi block / milestone dispatches / no-fire skip / **hard-breach still evaluates milestone** / no-Redis no-op / no-dispatcher no-op)
- `tests/test_llm_router_kimi_cap.py` (4 tests: hard-breach skips escalation / OK allows escalation / probe fail-open / no-redis returns False)
- `tests/test_soft_degrade_manager.py::test_is_kimi_blocked_treats_non_string_value_as_absent`

## Final verification

Codex independent re-check (no fixes applied this pass):

```
| 1 | Mongo UTC shift           | RESOLVED |
| 2 | CLI hydrate               | RESOLVED |
| 3 | Infinite monthly fraction | RESOLVED |
| 4 | SoftDegradeManager wired  | RESOLVED |
| 5 | cost_probe bytes hashes   | RESOLVED |
| 6 | Kimi ¥4 cap in production | RESOLVED |
| 7 | Hard-breach + milestone   | RESOLVED |

New Critical Regressions: NONE
Final Verdict: PASS
```

## Cumulative impact

| Metric | Value |
|--------|------:|
| Cycles run | 3 + final |
| Issues found | 7 (5 × P2 + 1 × P2 + 1 × P3) |
| Issues resolved | 7 / 7 |
| False positives dismissed | 0 |
| Regressions introduced | 0 |
| New regression tests | 16 |
| Pre-codex pytest | 2197 passed |
| Post-codex pytest | **2232 passed** / 11 skipped / 88.37% coverage |
| Ruff (touched files) | clean |
| `scripts/redline-check.sh` | all green (incl. new `[H-003] cost_guard isolation` sub-check) |
| Frontend type-check + vitest | 100 vitest passed |

## Phase H deliverables (final)

- **H-002 audit double-write** — `backend/api/audit.py` GET `/api/audit/events` + `/api/audit/event-types`; `scripts/query_audit.py` CLI (Mongo or JSONL); `AuditStore` wired into `app.state.audit_store` with lazy Mongo handle.
- **H-003 cost_guard P1-7** — `backend/services/cost_probe.py` (Redis-only); rewritten `cost_guard.py` (daily ¥20 hard / ¥14 soft / monthly ¥440 50/80/100% / Kimi ¥4); `soft_degrade_manager.py` (Kimi escalation block + monthly SETNX); `backend/api/cost.py` GET `/budget` + `/breakdown` + `/soft-degrade`; wiring in `analysis_scheduler.py` + `llm/router.py`.
- **H-004 alert matrix** — `backend/monitoring/alert_dispatcher.py` (13 locked alert types, soft = audit-only, critical = audit + Feishu); `GET /api/monitoring/alert-matrix` exposes the routing.

## Reinforces

- **[[feedback_codex_review_before_every_commit]]** — 2197 pre-codex pytest + ruff + redline + frontend all-green did NOT mean commit-safe. Codex surfaced 7 material correctness gaps that would each have ridden the green suite into production: 3 timezone / data-shape bugs (cycle 1), 2 dead-code wiring gaps (cycle 2), and 1 missing-enforcement + 1 conditional-skip bug (cycle 3).
- **[[feedback_codex_findings_real]]** — every Codex finding was a genuine issue; zero dismissed as false positive.

## Files touched (final)

**New:** `backend/api/audit.py`, `backend/api/cost.py`, `backend/monitoring/alert_dispatcher.py`, `backend/services/cost_probe.py`, `backend/services/soft_degrade_manager.py`, `scripts/query_audit.py`, plus 9 new test files.

**Modified:** `backend/main.py`, `backend/api/monitoring.py`, `backend/data/analysis_scheduler.py`, `backend/llm/router.py`, `backend/services/cost_guard.py`, `scripts/redline-check.sh`, `tests/test_cost_guard.py`.

**Incidental ruff cleanup (unrelated to Phase H but flagged by lint after `ruff --fix`):** `backend/api/performance.py`, `backend/api/trading.py`, `backend/api/websocket.py`, `backend/broker/registry.py` — only `datetime.UTC` modernization and unused-import removal.
