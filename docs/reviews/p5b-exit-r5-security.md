### Verification Table

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | R4 CLI live input test | VERIFIED | `tests/test_scripts_phase5b_exit_check.py` pins `$or` window filter, scalar projection, and Mongo/Redis close paths. |
| 2 | R4 `_coerce_leg` validation | VERIFIED | `backend/services/shadow_compare.py:67` now rejects invalid actions, non-bool flags, empty model, bad confidence/latency. |
| 3 | R4 `shadow_decisions` indexes | VERIFIED | `backend/data/database.py:155` creates unique `run_id` and `created_at` indexes; TTL still missing, see below. |
| 4 | Risk redline imports | PASS | No `backend/risk/` imports of `backend.llm`, `backend.agents`, or `backend.mirofish` found. |
| 5 | Prompt injection via LLM JSON | PASS | LLM-controlled action/confidence fields are constrained and not executed; malformed docs are skipped. |
| 6 | Secret logging | PASS | URI values are not printed/logged in the success path. |
| 7 | Suggest-mode redline | PASS | New Phase 5B harness reads telemetry only; no broker/order/authorization path bypass found. |
| 8 | Resource exhaustion | NEEDS_FIX | Live cursors and JSONL input are materialized without max bounds. |
| 9 | Data retention | NEEDS_FIX | `shadow_decisions` has no TTL despite storing sensitive decision telemetry. |
| 10 | CLI credential handling | WARNING | Secret-bearing URIs can be passed via argv, exposing them to process lists/shell history. |

### New Issues Found (security focus)

[MEDIUM] `shadow_decisions` retains sensitive decision telemetry indefinitely  
File: `backend/data/database.py:155`  
Confidence: HIGH  
Issue: `shadow_decisions` stores stock codes, actions, confidences, model names, and timestamps, but only creates `run_id` and `created_at` indexes. `_TTL_DAYS_DEFAULT = 30` exists in `backend/services/shadow_recorder.py:44` but is unused, so data accumulates forever.  
Fix: Make `created_at` a TTL index with `expireAfterSeconds` using a 30-day default or config/env override. Add a test asserting the TTL option.

[MEDIUM] Unbounded cursor/file materialization can DoS CI/operator hosts and backing stores  
File: `scripts/phase5b_exit_check.py:140`  
Confidence: HIGH  
Issue: `--days` has no upper bound, `analysis_records` and `shadow_decisions` cursors are fully materialized, Redis is scanned for every requested day, and `scripts/shadow_compare.py:85` reads the whole JSONL input into memory. A bad or compromised invocation can overload Mongo/Redis or OOM the runner.  
Fix: Clamp `--days` to a supported maximum such as 30, add max document/file-size limits, and aggregate streaming instead of collecting full lists where practical.

[LOW] Credential-bearing DB URIs are accepted on argv  
File: `scripts/phase5b_exit_check.py:52`  
Confidence: MEDIUM  
Issue: `--mongo-uri` and `--redis-url` may include credentials. Even though the script does not log them, argv secrets are visible to local process listings and often CI command echo/history. `scripts/shadow_compare.py:60` has the same Mongo URI pattern.  
Fix: Prefer env-only or secret-file inputs for credentialed URIs, document that CLI URI flags are for non-secret local use, and keep any future error reporting redacted.

[LOW] Unescaped `trade_date` can inject Markdown into reports  
File: `backend/services/shadow_compare.py:323`  
Confidence: MEDIUM  
Issue: `trade_date` is only checked as `str` at `backend/services/shadow_compare.py:166`, then rendered directly into a Markdown table. A malicious JSONL/DB document could add pipes/newlines/control characters and spoof report structure. This does not affect gate math.  
Fix: Validate `trade_date` as `YYYY-MM-DD` or escape Markdown/control characters before rendering.

### Summary Table

| Area | Result |
|---|---|
| Risk isolation | PASS |
| Prompt-injection resistance | PASS |
| Secret logging | PASS |
| Suggest-mode authorization | PASS |
| Resource bounds | NEEDS_FIX |
| Retention / TTL | NEEDS_FIX |
| CLI credential hygiene | WARNING |
| Report output injection | LOW hardening |

### Verdict

NEEDS_FIXES