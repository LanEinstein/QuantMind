### Verification Table
| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | [P2-R3] CLI loaded full `analysis_records` | Fixed in code, test gap | Projection is present in [scripts/phase5b_exit_check.py](/home/ps/papers/QuantMind/scripts/phase5b_exit_check.py:119), but no test exercises `_gather_inputs()` or asserts the projection. |
| 2 | [P3-R3] `shadow_decisions` missing indexes | Fixed in code, test gap | Indexes are present in [backend/data/database.py](/home/ps/papers/QuantMind/backend/data/database.py:155), but current DB tests only assert generic index creation. |

### New Issues Found (testing focus)

[MEDIUM] Phase 5B CLI live input path is effectively untested  
File: [tests/test_scripts_phase5b_exit_check.py](/home/ps/papers/QuantMind/tests/test_scripts_phase5b_exit_check.py:31)  
Confidence: HIGH  
Issue: The script tests cover only missing policy and parser defaults. They do not mock Motor/Redis to drive `_gather_inputs()`, so the R1 date-window filter and R3 scalar projection can regress without a failing test. `main(... --strict)` success/failure is also untested for this CLI.  
Fix: Add a mocked `_gather_inputs()`/fake cursor test asserting `find(filter, projection)`, `.sort("created_at", -1)`, `aggregate_costs(..., days=args.days)`, `query_shadow_decisions(..., days=args.days)`, client cleanup, and strict exit codes.

[MEDIUM] Malformed nested shadow legs are under-tested and partly accepted  
File: [backend/services/shadow_compare.py](/home/ps/papers/QuantMind/backend/services/shadow_compare.py:61)  
Confidence: HIGH  
Issue: Tests cover missing top-level legs and NaN/out-of-range confidence, but not invalid nested action values, string booleans, missing nested fields, negative/non-finite latency, or both legs malformed. Current `_coerce_leg()` coerces arbitrary actions with `str(...)` and `"false"` to `True`, so bad Mongo docs can count as valid pairs.  
Fix: Tighten `_coerce_leg()` to validate action membership and real bool fields, then add parametrized malformed-doc tests including “both legs malformed”.

[LOW] `shadow_decisions` index regression is not locked  
File: [tests/test_database.py](/home/ps/papers/QuantMind/tests/test_database.py:112)  
Confidence: HIGH  
Issue: The generic initialize test only checks `create_index.call_count >= 1`; removing the unique `run_id` index or descending `created_at` index would still pass.  
Fix: Add a collection-specific mock for `shadow_decisions` and assert both exact `create_index` calls.

[LOW] No realistic-size pure integration test for `compute_exit_report()`  
File: [tests/test_phase5b_exit_check.py](/home/ps/papers/QuantMind/tests/test_phase5b_exit_check.py:201)  
Confidence: HIGH  
Issue: Current coverage uses one or two records for most gates. It does not exercise a 7-day, mixed fast/slow, multi-stock dataset with shadow docs and partial malformed inputs.  
Fix: Add one fast pure test with realistic cardinality, e.g. 7 days x N fast/slow runs, asserting bucket counts, p95s, daily totals, shadow pass/fail, and no-data behavior.

[LOW] Timezone test name hides missing naive-`now` coverage  
File: [tests/test_shadow_recorder.py](/home/ps/papers/QuantMind/tests/test_shadow_recorder.py:263)  
Confidence: MEDIUM  
Issue: `test_naive_now_normalised_to_utc` actually passes an aware `+08:00` datetime. A genuinely naive `now` is not covered, and `astimezone()` would interpret it using host local timezone.  
Fix: Rename the existing test and add an explicit naive-`now` test, either rejecting it or documenting the intended normalization.

### Summary Table
| Area | Assessment |
|---|---|
| Edge cases | Good top-level coverage; nested malformed Mongo docs need more tests. |
| Property/contract tests | Good candidate helpers: `_percentile`, `aggregate_per_run_costs`, `compute_shadow_report`, `split_runs_by_category`. Hypothesis can remain deferred; deterministic contract tests are enough for now. |
| Integration coverage | Missing realistic-size pure `compute_exit_report()` test. |
| R1/R2/R3 regression coverage | R1/R2 mostly covered in service tests; R3 projection and index fixes are not locked. |
| Isolation/I/O | Mostly isolated. CLI JSONL tests intentionally use `tmp_path`; no accidental Mongo/Redis I/O seen. |
| Speed | Service tests are fast: `74 passed in 0.07s` with `pytest -q -s -p no:cacheprovider ...`. |

### Verdict
NEEDS_FIXES