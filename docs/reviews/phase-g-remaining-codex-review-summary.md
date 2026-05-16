# Phase G remaining — Codex Review Summary (Session #16)

**Task scope:** G-005 用户回报录入 + G-006 对账裁定 + G-008 Phase B 收尾 4 页 + G-009 WebSocket 12 类消息升级
**Cycles attempted:** 3
**Cycles completed:** 2 (cycles 1 + 2)
**Final verdict:** ⚠️ **PARTIAL — 6/6 prior issues RESOLVED in cycles 1+2 / cycle 3 + final verification SKIPPED (Codex CLI rate-limit)**
**Reviewer:** Codex CLI 0.130.0 (`codex review --uncommitted` + `codex exec`)
**Author:** Claude Opus 4.7 (1M context)

## Cycle 1 — Initial review (`codex review --uncommitted`)

Diff size: 12 new files / 9 modified / ~3800 lines.

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 1 | P2 | `backend/api/data_quality.py:102` | Real `DataQualityProvider.evaluate(stock_code, now)` signature — endpoint passed only `stock_code`, so every request would `TypeError` then degrade to `unavailable` once C-004 wired | Pass `now=datetime.now(UTC)` explicitly + assert in test |
| 2 | P2 | `backend/api/data_quality.py:66-72` | Serializer used invented field names (`snapshot_outage` / `news_outage` / `mirofish_outage`); real `DataQualityState` exposes `quote_unavailable` / `quote_staleness_breach` / `quote_divergence_breach` / `minimum_freshness_breach` / `news_outage_breach` / `mirofish_unavailable` / `watchlist_snapshot_outage` | Rewrote `_serialize_state` to surface the locked P1-2.B §1.5.1 schema (7 breach bools + 3 counters + 2 derived properties + composed `blocking_breaches` list) |
| 3 | P2 | `frontend/src/utils/executionRegex.ts:107-112` | Preview did not mirror backend `_normalise` (trim + collapse `[ \t]+`); valid pasted reports with leading whitespace failed preview while backend would accept them | Added `normalizeForPreview(raw)` called before regex match |
| 4 | P2 | `frontend/src/views/ReconciliationCenter.vue:266-267` | `submitAmend` closed the dialog regardless of `decide()` success — operator loses JSON on POST failure | `decide()` returns `boolean`; `submitAmend` keeps dialog open + surfaces error in `amendParseError` when POST raised |

Regression tests added: `test_real_field_names_present_in_payload` (backend), `test_happy_path_serializes_state` updated to assert `now` kwarg, vitest mirror updated with `extra_field_appended` invalid fixture + whitespace-tolerant samples.

## Cycle 2 — Verification + new issues

Cycle 1 verification: **all 4 RESOLVED**.

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 5 | P2 | `executionRegex.ts:111` | The first whitespace fix was a 3-stage shape that did not match backend `text.strip()` semantics. Mixed `" \n  已执行 ... \n "` boundary noise was only partially trimmed | Rewrote to `raw.trim().replace(/[ \t]+/g, ' ')` — exact backend mirror; added `test_codex_cycle_2_P2_mixed_newline_indent_boundary` regression |
| 6 | P2 | `ReconciliationCenter.vue:237` | `decide()` wrapped both the POST and `refresh()` in one `try/catch`. A POST that succeeded (broker mirror reset already committed) followed by a failed list reload would return `false`, telling the operator the amendment failed and prompting a dangerous retry | Split into two stages: POST sets `postOk = true`; `refresh()` runs only on success and its failure surfaces as a warning banner (`error.value = "刷新失败 (裁定已应用): ..."`) without flipping the result |

## Cycle 3 — Attempted, hit Codex usage limit

```
ERROR: You've hit your usage limit. Upgrade to Pro (...) or try again at 7:53 PM.
Error: Codex exited with code 1
```

Two consecutive request attempts (one full-scope prompt + one narrow 2-file prompt) returned the same rate-limit error. Per skill protocol §Phase 5, this maps to `EXIT_REASON = codex_unavailable` and Phase 6 final verification was **SKIPPED**.

## Status of cycle 2 fixes (developer attestation, no Codex closure)

Local gates run after cycle 2 fixes:

| Gate | Result |
|------|-------:|
| pytest (full suite) | **2309 passed** / 11 skipped / 88.54% coverage > 70% |
| ruff (Phase G remaining touched files) | clean |
| `scripts/redline-check.sh` (incl. new `[G-009]` sub-check) | all green |
| vitest (full suite) | **120 passed** / 15 files |
| `vue-tsc --noEmit` (frontend type-check) | clean |

The cycle 2 fixes were unit-tested with fresh regressions (`test_codex_cycle_2_P2_mixed_newline_indent_boundary` and reconciliation refresh isolation), but were **not** independently confirmed by Codex. Operator should treat the resulting code as needing a follow-up Codex pass once the rate-limit clears (~19:53 CST 2026-05-16); the skill protocol's "manual retrigger" path applies.

## Cumulative impact (Phase G remaining)

| Metric | Value |
|--------|------:|
| Cycles run | 2 + 1 attempted |
| Issues found | 6 (all P2) |
| Issues resolved | 6 / 6 |
| False positives dismissed | 0 |
| Regressions introduced | 0 (cycle 2 verification scope) |
| New regression tests | 6 (1 backend + 5 frontend) |
| Pre-codex pytest | 2308 passed |
| Post-codex pytest | **2309 passed** / 11 skipped / 88.54% coverage |

## Phase G remaining deliverables

- **G-005 用户回报录入** — `backend/api/execution_reports.py` (POST /api/execution-reports wraps F-004 orchestrator `handle_frontend`), `frontend/src/utils/executionRegex.ts` (JS SSoT mirror of `PATTERNS_AS_DICT` + `normalizeForPreview`), `frontend/src/views/ExecutionReportEntry.vue` (5 templates + JS preview + fail-closed submit), `tests/fixtures/execution_reports_mirror_samples.json` (shared backend + frontend regex fixture).
- **G-006 对账裁定** — `backend/api/reconciliation.py` (GET list + POST decide; `_AmendedSnapshotDTO` JSON-friendly DTO that projects into the strict `MockBrokerSnapshot`), `frontend/src/views/ReconciliationCenter.vue` (ticket list + 3-button decision + amend dialog with isolated refresh failure).
- **G-008 Phase B 收尾 4 页** — `backend/api/data_quality.py` (GET per-stock state, P1-2.B locked schema), `backend/api/feishu.py` (GET audit-derived inbound/outbound history with Mongo→JSONL fallback), `frontend/src/views/{DataQuality,FeishuMessages,CostBreakdown}.vue` + AgentDebate moved into AppShell menu.
- **G-009 WebSocket 14-kind upgrade** — `backend/data/publisher.py` new `CHANNEL_SYSTEM` + `SYSTEM_EVENT_TYPES` + `FORBIDDEN_WS_TYPES` + `publish_system_event` (raises on forbidden / unknown types), `backend/api/websocket.py` bridge subscribes new channel + drops payloads outside the allowlist (defense-in-depth), `frontend/src/types/market.ts` locked `WS_MESSAGE_TYPES` (14) + `FORBIDDEN_WS_MESSAGE_TYPES`, `frontend/src/composables/useWebSocket.ts` handles all 14 kinds with per-kind readonly refs, `scripts/redline-check.sh` new `[G-009]` sub-check.

## Reinforces

- **[[feedback_codex_findings_real]]** — every Codex finding in cycles 1+2 was genuine; zero false positives dismissed.
- **[[feedback_codex_review_manual_invocation]]** — the user-triggered codex pass surfaced 6 P2 issues that 2308 green pytest + 116 vitest + ruff + redline + type-check pre-codex did not catch (4 schema/contract mismatches against future C-004 wiring + 2 frontend UX dialog gotchas).

## Files touched (final)

**New backend:** `backend/api/execution_reports.py`, `backend/api/reconciliation.py`, `backend/api/data_quality.py`, `backend/api/feishu.py`.

**Modified backend:** `backend/api/websocket.py`, `backend/data/publisher.py`, `backend/main.py`, `scripts/redline-check.sh`.

**New frontend:** `frontend/src/utils/executionRegex.ts`, `frontend/src/views/{ExecutionReportEntry,ReconciliationCenter,CostBreakdown,DataQuality,FeishuMessages}.vue`, `frontend/src/api/{executionReports,reconciliation,cost,dataQuality,feishuMessages}.ts`.

**Modified frontend:** `frontend/src/composables/useWebSocket.ts`, `frontend/src/types/market.ts`, `frontend/src/router/index.ts`, `frontend/src/router/menu.ts`, `frontend/src/router/__tests__/menu.spec.ts`.

**New tests:** `tests/test_api_execution_reports.py`, `tests/test_api_reconciliation.py`, `tests/test_api_data_quality.py`, `tests/test_api_feishu_history.py`, `tests/test_execution_regex_mirror_backend.py`, `tests/test_ws_g009_contract.py`, `tests/fixtures/execution_reports_mirror_samples.json`, `frontend/src/utils/__tests__/executionRegex.spec.ts`, `frontend/src/composables/__tests__/useWebSocket.spec.ts`.
