### Verification Table
| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | Malformed analysis records can crash cost aggregation | Fixed | `_cost_for()` uses `.get("run_id")`; `split_runs_by_category()` drops missing `run_id`. |
| 2 | Partial cost telemetry could let gate pass on covered subset | Fixed | Completeness now requires `cost_metric.fast_runs == fast_total` / slow equivalent. |
| 3 | Markdown row broken by literal pipes around `\|Δ\|` | Fixed | Escaped as `\\|Δ\\|`; row terminator stays intact. |

### New Issues Found (perf only)

[P2] Exit-check CLI loads full analysis records for five scalar fields  
File: `scripts/phase5b_exit_check.py:115`  
Confidence: HIGH  
Issue: The query materializes complete `analysis_records` documents, including agent step content, debates, evidence, and decision payloads. `compute_exit_report()` only needs `run_id`, `stock_code`, `trade_date`, `created_at`, and `completed_at`. At ~990 records, this is fine if docs are tiny, but at realistic `AnalysisRecord` sizes of 50-200KB it becomes ~50-200MB transferred and held before any aggregation. A 30-day fast window is ~3,600 records, which can push this into hundreds of MB.  
Fix: add a Mongo projection: `_id: 0, run_id: 1, stock_code: 1, trade_date: 1, created_at: 1, completed_at: 1`.

[P3] `shadow_decisions` reads/writes are unindexed  
File: `backend/services/shadow_recorder.py:153`  
Confidence: MEDIUM  
Issue: `record_shadow_decision()` upserts by `run_id`, and `query_shadow_decisions()` filters by `created_at`, but no `shadow_decisions` indexes are added in `MongoDBService.initialize()`. At the stated 7-30 day volumes this is not painful, but if shadow data is retained for months, every upsert/query trends toward collection scans.  
Fix: create indexes for `shadow_decisions`: unique `run_id`, plus `created_at` for the lookback query. If retention should be bounded, make `created_at` a TTL index.

### Summary Table
| Area | Perf assessment |
|------|-----------------|
| Pure-Python percentiles | Fine at ~1k records; only worth revisiting around tens of thousands of rows. |
| `aggregate_per_run_costs` / bucket split | Linear in records/cost entries; not `records × days` in current code. |
| `compute_exit_report` comprehensions | Extra passes are minor; DB payload size dominates. |
| `query_shadow_decisions` iterator | Iterator vs `to_list()` is not the issue; missing indexes are. |
| CLI loading | Count is acceptable, but full-document projection is the real risk. |

### Verdict
NEEDS_FIXES