### Verification Table
| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | R3 backlog cap | PARTIAL | `test_backlog_full_drops_new_runs` covers cap/drop and cancellation cleanup, but not normal/exception completion counter cleanup. |
| 2 | R3 baseline timeout | PARTIAL | Timeout drop is covered, but the test does not prove the gate is released for a following run. It also uses real sleeps. |
| 3 | R3 compare single-pass behavior | PASS | Empty input, all parse-failed, parse-failed exclusion, thresholds, p50/p95, leg metrics are covered. |
| 4 | Parse-ok propagation | PARTIAL | Shadow runner covers routed leg parse_ok, but graph/collector/persistence propagation is not locked end-to-end. |
| 5 | Shared module state isolation | PARTIAL | `_shadow_gate` is reset in some tests; `_inflight_shadow` is reset only in one test and not asserted after all scheduled-task outcomes. |

### New Issues Found (testing)

[P2] Missing regression lock for `_inflight_shadow` decrement on successful/failed scheduled tasks  
File: tests/test_shadow_runner.py:660  
Confidence: HIGH  
Issue: `test_creates_named_task_when_enabled` awaits a normal scheduled task but never asserts `_inflight_shadow == 0`; `test_task_exception_logged_not_raised` also does not assert decrement. A regression where the done-callback only decrements cancelled tasks would still pass, yet production shadow collection would stop after the cap is reached.  
Fix: Add an autouse fixture resetting `_inflight_shadow`/`_shadow_gate`, and assert the counter returns to zero after successful, cancelled, and exception task completion.

[P2] Parse-ok propagation is not tested through graph/collector/persistence boundary  
File: tests/test_agents_graph.py:203  
Confidence: HIGH  
Issue: `FundManagerRecord.parse_ok` is only tested indirectly in `shadow_runner`; there is no test proving invalid fund-manager JSON produces `trading_signal["parse_ok"] = False`, `run_analysis()` passes `signal_parse_ok=False`, `RunCollector.finalize()` stores `decision.parse_ok=False`, and `model_dump(mode="json")` preserves it. This is the main R2 regression path.  
Fix: Add a graph/collector test with an invalid fund-manager response and assert `result.record.decision.parse_ok is False`, plus a model dump/load assertion for `FundManagerRecord.parse_ok`.

[P3] Timeout test relies on wall-clock sleep and does not verify gate release  
File: tests/test_shadow_runner.py:718  
Confidence: MEDIUM  
Issue: `test_baseline_timeout_dropped_not_held` uses a real `asyncio.sleep(1.0)` with a 50ms timeout and only asserts the timed-out run returns `False`. It does not prove a subsequent shadow run can acquire `_shadow_gate`.  
Fix: After the timeout, run a fast baseline call and assert it completes, or monkeypatch `asyncio.wait_for`/use a controlled future to avoid wall-clock dependency.

### Verdict
NEEDS_FIXES

I could not execute the focused pytest run because the sandbox has no writable temp directory; pytest failed during capture setup before collecting tests.