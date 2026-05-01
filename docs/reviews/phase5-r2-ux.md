[CRITICAL] Live SSE Debate Events Are Dropped
File: frontend/src/stores/agent.ts:158
Confidence: HIGH
Issue: `startAnalysis()` resets `currentAnalysis` before connecting SSE, then `applySSEEvent()` ignores `agent_completed` events when `currentAnalysis` is null. During a live run the main panel stays on the empty prompt instead of showing streamed debate updates.
Fix: Create a provisional `AnalysisDetail` when the job starts, or store live debate rounds separately and render them while the final detail fetch is pending.

[CRITICAL] Failed Detail Loads Render As Empty State
File: frontend/src/views/AgentDebate.vue:148
Confidence: HIGH
Issue: When `fetchDetail()` fails with mocks disabled, the store sets `analysisStatus = 'failed'` and `currentAnalysis = null`, but the view shows “选择标的或从历史记录加载分析结果” instead of the actual failure or a retry path.
Fix: Add an explicit failed state using `store.lastError`, with retry for the selected record/job.

[CRITICAL] Terminal SSE Contract Is Only Partially Handled
File: frontend/src/views/AgentDebate.vue:251
Confidence: HIGH
Issue: The view only reacts when `pipelineRecordId` is truthy. Backend `pipeline_completed.record_id` is nullable, so a completed run with failed persistence can leave the UI stuck as “分析中...”. Backend `error` events can include `record_id`, but the frontend ignores it and never loads the failed record or refreshes history.
Fix: Dispatch terminal SSE events into the store, handle nullable `record_id`, fetch failed records when `error.record_id` exists, and set a persistent completed/failed UI state.

[WARNING] History Loading And Error States Are Missing
File: frontend/src/views/AgentDebate.vue:98
Confidence: HIGH
Issue: `fetchHistory()` has no loading flag and failures collapse to `[]`, so users see “暂无历史记录” both while history is loading and when the backend failed.
Fix: Add `historyLoading` and `historyError` state, render a skeleton/spinner while pending, and show a retryable Chinese error state on failure.

[WARNING] Start Button Allows Duplicate Live Jobs
File: frontend/src/views/AgentDebate.vue:42
Confidence: HIGH
Issue: The button loading state is bound to `store.loading`, but `startAnalysis()` never sets it for `createJob()` or the SSE run. Users can click “开始分析” repeatedly while a job is starting/running.
Fix: Track `isStarting`/`isStreaming` and disable or load the button during job creation and active SSE.

[WARNING] Debate Argument Model Is Hard-Coded
File: frontend/src/stores/agent.ts:169
Confidence: HIGH
Issue: SSE `agent_completed` includes `model_label`/`model_id`, but live `DebateArgument.model` is always set to `'Kimi'`, mislabeling output from Qwen, DeepSeek, fallback providers, or future models.
Fix: Derive from `event.model_label` with a safe display fallback, and widen/normalize the frontend model type as needed.

[WARNING] History Search Contract Breaks Name And Fuzzy Search
File: frontend/src/stores/agent.ts:119
Confidence: HIGH
Issue: The UI/local filter supports code/name substring search, but every input is sent to backend as exact `stock_code`. Typing “茅台” or a partial code can fetch an empty server result before local filtering can work.
Fix: Only send `stock_code` for exact 6-digit codes, keep fuzzy/name filtering client-side, or add a backend `q` search contract.

[WARNING] History And Live Updates Are Not Keyboard/Screen-Reader Friendly
File: frontend/src/views/AgentDebate.vue:74
Confidence: HIGH
Issue: History rows are clickable `<div>` elements without `role`, `tabindex`, keyboard handlers, or `aria-current`; live status/SSE updates also lack `aria-live`/`role="status"`.
Fix: Use buttons or listbox semantics for history rows, support Enter/Space, mark the active row, and add polite live regions for status/debate updates.

[WARNING] User-Facing Errors Are English Or Raw Backend Messages
File: frontend/src/composables/useSSE.ts:73
Confidence: HIGH
Issue: Messages like `SSE connection lost`, `Failed to connect SSE`, and raw `err.message`/backend `Analysis failed: ...` are surfaced directly through `ElMessage`, which violates the Chinese UI convention and can expose internal details.
Fix: Normalize API/SSE errors into concise Chinese user messages, parse backend envelope details safely, and keep raw diagnostics in logs only.

[INFO] E2E Does Not Assert Live SSE Rendering
File: frontend/e2e/agent-debate.spec.ts:217
Confidence: HIGH
Issue: The “consumes SSE stream” test sends `看多论点（SSE）` but only asserts final fetched detail text, so it would not catch the current dropped-live-event regression.
Fix: Delay or gate the detail response and assert the streamed debate text appears before final detail loads.

## Summary Table
| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| WARNING | 6 |
| INFO | 1 |

## Verdict
MAJOR_CONCERNS

The REST DTOs are mostly aligned, but the live SSE and failure UX paths are not shippable yet.