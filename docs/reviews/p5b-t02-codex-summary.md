# P5B-T02 Codex Review Summary

**Task**: P5B-T02 — Fast/Slow Watchlist 拆分
**Date**: 2026-05-02
**Session**: main session (Opus 4.7)
**Rounds executed**: 5 (R1 architecture / R2 UX / R3 testing / R4 perf / R5 security) + 1 follow-up

| Round | Topic | Output |
|---|---|---|
| R1 | Architecture | `p5b-t02-r1-architecture.md` |
| R2 | UX / DX | `p5b-t02-r2-ux.md` |
| R3 | Testing | `p5b-t02-r3-testing.md` |
| R4 | Performance | `p5b-t02-r4-perf.md` |
| R5 | Security | `p5b-t02-r5-security.md` |
| R6 | Follow-up verification | `p5b-t02-r6-followup.md` |

## Findings resolved

| Round | Severity | Finding | Resolution |
|---|---|---|---|
| R1 | HIGH | Malformed YAML/cron crashes startup | Wrapped `yaml.YAMLError` in `WatchlistPolicyError`; cron `ValueError` in `start()` triggers fallback to legacy single-cron + `policy=None`. Test: `TestCronRegistration::test_malformed_cron_falls_back_to_legacy`. |
| R1 | MEDIUM | Policy not snapshotted within a tick | Added `policy = self._policy` snapshot at top of `run_category_analysis`; passes `policy` through `_run_codes` → `_run_and_persist` → `_resolve_services_and_timeout`. |
| R1 | MEDIUM | Single-process assumption implicit | Documented in `update_policy` and module docstrings. |
| R1 | LOW | `pipeline` field unused | Documented as RESERVED for P5B-T03 / P5C in `BucketConfig` docstring. |
| R2 | MEDIUM | Invalid category bypassed envelope | Switched `category` to `str \| None` + manual handler validation routed through `_err`. Test: `test_invalid_category_returns_envelope_422`. |
| R2 | MEDIUM | Unknown code silently written to overrides | Validate against active watchlist; 404 with actionable message. Test: `test_unknown_code_returns_404`. |
| R2 | MEDIUM | Cron logs missing triage fields | `category_analysis_started/skipped/complete` now emit `matched_codes`, `total_watchlist`, `policy_version`, `cron`, `timeout_seconds`, `max_debate_rounds`, `failed`. |
| R2 | MEDIUM | `save_policy` strips YAML comments | Documented in `save_policy` docstring + the API endpoint docstring; round-trip-safe write deferred to backlog. |
| R2 | LOW | `null` clears override discoverability | Pydantic `Field(description, examples)`; doc reflects body shape. |
| R3 | HIGH | Cron registration tests time-flaky | `_compute_catch_up_targets` patched in tests. |
| R3 | HIGH | Missing fast/slow + budget breach test | Added `TestBudgetInteractionWithPolicy`. |
| R3 | HIGH | Missing catch-up + policy test | Added `TestCatchUpWithPolicy`. |
| R3 | MEDIUM | Timeout test only checks except branch | Now asserts `captured_timeout == [480]`. |
| R3 | MEDIUM | App.state pollution | Snapshot/restore teardown in `app_state_with_policy` fixture. |
| R3 | MEDIUM | `run_daily_analysis` mixed-bucket untested | Added `TestRunDailyAnalysisWithPolicy`. |
| R3 | MEDIUM | `save_policy` failure path untested | Added `test_save_failure_returns_500_and_keeps_old_policy`. |
| R3 | LOW | Empty watchlist with policy | Added `TestEmptyWatchlistWithPolicy`. |
| R4 | LOW | Sync `save_policy` blocks event loop | Wrapped in `asyncio.to_thread`. |
| R4 | LOW | `assign_category` double-lookup + tuple membership | Switched to `dict.get` + `frozenset` (cached at load). |
| R5 | LOW | Error response leaks file path | Generic 500 message; path stays in server log. Test: `test_save_failure_returns_500_and_keeps_old_policy`. |
| R5 | LOW | `extra="forbid"` not set on body | Added; test `test_extra_field_rejected`. |
| R6 | HIGH | `app.state.watchlist_policy` desyncs on cron fallback | After `start()`, re-read `analysis_scheduler.policy` into `app.state`. |
| R6 | MEDIUM | `category` default=None lets `{}` clear override | Made required (`Field(...)`); test `test_empty_body_rejected`. |
| R6 | MEDIUM | Snapshot incomplete (per-stock config still reads live policy) | Snapshot threaded through `_run_codes` → `_run_and_persist` → `_resolve_services_and_timeout`. |

## Findings deferred (documented as known limitations)

| Round | Severity | Finding | Reason |
|---|---|---|---|
| R4 | HIGH | `_run_lock` lets fast wait on slow | Doc-only fix in module docstring. Per-bucket lock + Redis-backed budget reservation deferred to Phase 5C; current single-process eval scope is acceptable. |
| R4 | HIGH | Per-stock timeout ≠ end-to-end SLA | Documented; operators monitor `category_analysis_complete` log and tighten the bucket timeout if there's head-room. |
| R4 | HIGH | Cost guard doesn't projected-reserve | Pre-existing P5A-T02 design (fail-open on Redis hiccup, single sample). Tightening to projected/reserved is a separate cost_guard task. |
| R4 | MEDIUM | Sequential 10s rate-limit eats fast SLA | Existing rate limit, not introduced by T02. Operators size fast watchlists ≤ 1-2 stocks for now. |
| R5 | HIGH | `/category` endpoint lacks request-level auth | Cross-cutting; ALL `/api/watchlist/*` endpoints lack request-level auth. Address in a separate hardening pass alongside operator/admin auth dependency wiring. |
| R5 | MEDIUM | Lost-update on concurrent POST | Single-process eval scope; `WEB_CONCURRENCY=1` documented. PolicyStore + filelock deferred to Phase 5C if multi-worker becomes a goal. |

All HIGH findings either resolved or explicitly documented as known limitations. CRITICAL findings: none.
