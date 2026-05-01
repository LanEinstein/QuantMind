**Findings**
- Critical + [tests/test_analysis_run_failures.py:97](/home/ps/papers/QuantMind/tests/test_analysis_run_failures.py:97) + Confidence: High + Issue: The C2/C3 regression is only mocked at the API boundary. No test drives `run_analysis()` through a real agent failure or graceful `[agent error: ...]`, so a regression that promotes failed steps to `completed` would still pass. + Fix: Add a graph-level failure test that makes one agent return/raise a failure, expects `AnalysisRunError`, and asserts `record.status == "failed"` plus a failed step.

- Critical + [tests/test_analysis_stream.py:144](/home/ps/papers/QuantMind/tests/test_analysis_stream.py:144) + Confidence: High + Issue: There is no backend integration test for `/jobs -> SSE -> /history`. The stream test asserts event types only; it never proves the completed job is persisted and visible through history/detail. + Fix: Run a job with a fake `run_analysis`, assert `save_signal`, `save_analysis_record`, terminal `record_id`, then call `/api/analysis/history` and verify the new run appears.

- High + [tests/test_analysis_stream.py:108](/home/ps/papers/QuantMind/tests/test_analysis_stream.py:108) + Confidence: High + Issue: `test_post_jobs_returns_job_id` starts a background task but exits the patch/context without waiting for task completion or hub shutdown. The task can run after the mocked `run_analysis` is unpatched, leaking work into later tests. Same pattern exists in [tests/test_llm_preflight.py:161](/home/ps/papers/QuantMind/tests/test_llm_preflight.py:161). + Fix: Await the attached job task or poll for a terminal hub event in every `/jobs` test; use a yield fixture that calls `analysis_stream_hub.shutdown()` and restores `app.state`.

- High + [tests/test_api_analysis.py:52](/home/ps/papers/QuantMind/tests/test_api_analysis.py:52) + Confidence: High + Issue: API fixtures use a bare `AsyncMock` as `llm_router`. `_llm_preflight_or_503()` calls `preflight()` synchronously, so this mock shape returns a coroutine rather than the real `dict[str, bool]`; tests pass while exercising a non-real path. Same issue at [tests/test_analysis_stream.py:51](/home/ps/papers/QuantMind/tests/test_analysis_stream.py:51). + Fix: Use `MagicMock` for the router or set `preflight = lambda: {"deepseek": True}` in every API fixture.

- High + [tests/test_analysis_stream_hub.py:28](/home/ps/papers/QuantMind/tests/test_analysis_stream_hub.py:28) + Confidence: Medium + Issue: The atomic subscribe coverage checks tuple shape and frozen snapshots, but it does not reproduce the terminal-event race that motivated the fix. Reverting the API to separate replay/subscribe calls could pass these hub tests. + Fix: Add an API-level race test where a terminal event is pushed between snapshot and subscription, then assert the stream receives terminal and exits.

- Medium + [tests/test_analysis_scheduler.py:116](/home/ps/papers/QuantMind/tests/test_analysis_scheduler.py:116) + Confidence: High + Issue: Scheduler tests were updated for `AnalysisRunResult`, but still only assert `save_signal`. They would not catch dropping successful or failed `save_analysis_record()` persistence. + Fix: Assert successful records are saved with `signal_id`, and add an `AnalysisRunError` scheduler test that persists the failed record.

- Medium + [frontend/e2e/agent-debate.spec.ts:120](/home/ps/papers/QuantMind/frontend/e2e/agent-debate.spec.ts:120) + Confidence: High + Issue: The Playwright suite relies heavily on CSS implementation selectors (`.debate-layout`, `.history-item`, `.stock-selector`, Element Plus dropdown classes). The live-before-detail gate is meaningful, but selector brittleness will break on harmless markup/style refactors. + Fix: Prefer roles, labels, visible text, or stable `data-testid` hooks for app-specific controls.

**Summary Table**
| Area | Status |
|---|---|
| Critical failure coverage | Insufficient |
| Persistence/history integration | Missing |
| Atomic SSE race coverage | Too shallow |
| Mock fidelity | Needs fixes |
| Playwright robustness | Gate is good; selectors brittle |
| Scheduler/circuit breaker edge coverage | Circuit breaker OK; scheduler persistence gap |

**Verdict**
Not shippable as Phase 5 coverage yet. The suite has useful happy-path and handler tests, but it still misses the regressions most likely to hurt production: real graph failure propagation, job-to-history persistence, and SSE race/hang behavior.

**Minimum Additional Tests**
1. `test_run_analysis_agent_failure_raises_with_failed_step_record`
2. `test_jobs_sse_completion_persists_record_and_history_returns_new_run`
3. `test_stream_subscribe_terminal_race_replays_terminal_and_exits`
4. `test_stream_slow_consumer_drop_closes_http_stream_without_hanging`
5. `test_detail_from_record_matches_analysis_detail_contract_full_record`