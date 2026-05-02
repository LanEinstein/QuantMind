### Verification Table
| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | Routed parse failures always marked gateable | VERIFIED | `parse_ok` now flows from `fund_manager_node` through `RunCollector.finalize` into routed shadow legs. |
| 2 | Missing Mongo still burned baseline LLM call | VERIFIED | `services.mongodb is None` short-circuits before prompt rebuild, budget probe, and baseline call. |
| 3 | Baseline parser stricter than live extractor | VERIFIED | Baseline parser now uses `extract_json_from_response` and accepts numeric string confidence/default confidence. |
| 4 | No-Mongo test did not actually test `mongodb=None` | VERIFIED | Test helper uses sentinel and asserts `services.mongodb is None`. |

### New Issues Found (perf)

[P2] Unbounded shadow task backlog can retain full records/prompts behind one slow baseline call  
File: [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:427), [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:321), [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:339)  
Confidence: HIGH  
Issue: `schedule_shadow_run` creates one background task per completed analysis with no queue depth bound. Each task rebuilds `user_content` before acquiring the global shadow gate, then waits behind a lock held across the baseline LLM call. A slow/stuck baseline call can accumulate pending tasks that retain the full `AnalysisRecord` plus duplicated prompt/debate strings.  
Fix: Replace naked `create_task` with a bounded shadow worker queue, drop/log when full, and apply an explicit timeout around the baseline call. Admit/drop before rebuilding prompt, or pass a slim payload instead of retaining the full record.

[P3] Shadow report does avoidable extra materialization and sorting  
File: [backend/services/shadow_compare.py](/home/ps/papers/QuantMind/backend/services/shadow_compare.py:259), [backend/services/shadow_compare.py](/home/ps/papers/QuantMind/backend/services/shadow_compare.py:289), [backend/services/shadow_compare.py](/home/ps/papers/QuantMind/backend/services/shadow_compare.py:297)  
Confidence: MEDIUM  
Issue: `compute_shadow_report` materializes `pairs`, `gateable`, two leg lists, `deltas`, and `abs_deltas`; then `statistics.median(deltas)` sorts once and `_percentile(deltas, 95)` sorts again. With current CLI caps this is not catastrophic, but it is avoidable peak memory and CPU on large JSONL/Mongo replays.  
Fix: Accumulate leg metrics and by-day counters in one pass, keep one `deltas` list, sort it once, and compute p50/p95 from that sorted list.

### Verdict
NEEDS_FIXES