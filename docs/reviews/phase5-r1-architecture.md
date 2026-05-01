[CRITICAL] Signal linkage model loses rerun records
File: backend/data/database.py:115
Confidence: HIGH
Issue: `trading_signals` is upserted by `(stock_code, trade_date)`, so reruns for the same stock/day return the same `signal_id`, while `analysis_records` is designed to accumulate distinct `run_id`s. The unique sparse index on `analysis_records.signal_id` makes the second rerun fail record persistence, or otherwise links old records to a mutable signal row.
Fix: Choose one model. Prefer per-run signals with `run_id` on `trading_signals`, or make `analysis_records.signal_id` non-unique and treat it as a latest-signal pointer. Also drop/migrate the existing unique index.

[CRITICAL] Agent failures still finalize as completed runs
File: backend/agents/graph.py:359
Confidence: HIGH
Issue: `call_agent()` converts LLM failures into `[agent error: ...]`, and the collector marks that step failed, but `run_analysis()` never checks failed steps. It always finalizes `status="completed"` and creates a trading signal, so a single failed agent is silently promoted to a completed analysis.
Fix: After `ainvoke`, inspect `collector.steps`; if any step failed, finalize `status="failed"` and raise/return a failed result instead of persisting a normal signal. If partial success is intentional, add an explicit `completed_with_errors` status to both backend and frontend contracts.

[CRITICAL] Failed AnalysisRecord is discarded by job runner
File: backend/api/analysis.py:346
Confidence: HIGH
Issue: `run_analysis()` raises an `_AnalysisRunError(record)` on graph exceptions, but `_run_job()` catches generic `Exception`, emits an SSE error, and returns without persisting `exc.record`. The same pattern exists in the sync `/stock` path and scheduler callers, so failed runs do not appear in `/history`.
Fix: Make the failure exception public and catch it before generic exceptions. Persist `exc.record.model_dump(mode="json")`, then emit an error event that includes `record_id` when available.

[CRITICAL] Late SSE subscribers can miss the terminal event
File: backend/api/analysis.py:446
Confidence: HIGH
Issue: The stream registers a queue, replays `list(job.events)`, then checks mutable `job.events[-1]`. If `pipeline_completed` or `error` arrives after the replay snapshot but before the check, the terminal event is only in the queue, yet the generator returns without yielding it. Early returns also happen before `finally`, leaking subscriber queues for already-completed jobs.
Fix: Snapshot replay once, put all terminal checks inside a `try/finally`, and only return early if the replay snapshot itself ended terminal; otherwise drain the queue until `None`.

[WARNING] Node exceptions are recorded as successful empty steps
File: backend/agents/graph.py:102
Confidence: HIGH
Issue: On an actual node exception, the wrapper calls `on_agent_completed(..., {})`. The collector extracts empty content and emits `agent_completed` with `status="completed"` before the error event. The failed record then has no failed step/error for the crashing agent.
Fix: Add `RunCollector.on_agent_failed(...)` or a status override and emit an `AgentStepRecord(status="failed", error=str(exc))` before emitting the terminal error.

[WARNING] Slow SSE consumers are dropped without being closed
File: backend/services/analysis_stream.py:123
Confidence: HIGH
Issue: On `QueueFull`, the hub removes the subscriber but does not enqueue a terminal event or `None`. If the dropped event is terminal, that stream can drain stale items and then heartbeat forever, never seeing `error`/`pipeline_completed`.
Fix: When dropping a subscriber, actively close its queue: make room, enqueue a drop/error or terminal event, then enqueue `None`; or keep the subscriber and drop non-terminal events only.

[WARNING] Shutdown cancels jobs but does not await them
File: backend/services/analysis_stream.py:197
Confidence: HIGH
Issue: `shutdown()` calls `job.task.cancel()` and immediately finalizes subscribers without awaiting the tasks. Jobs may continue running against Mongo/LLM services while lifespan proceeds to close them, and cancellation exceptions are unobserved.
Fix: Collect pending tasks, cancel them, then `await asyncio.gather(*tasks, return_exceptions=True)` before closing shared services.

[WARNING] Detail endpoint returns persistence shape, not frontend AnalysisDetail
File: backend/api/analysis.py:231
Confidence: HIGH
Issue: `_detail_from_record()` only converts `_id`; it returns stored `DebateRoundRecord`, `RiskAssessmentRecord`, and `FundManagerRecord` shapes. The frontend expects `DebateArgument.role/model/timestamp`, `RiskAssessment.model/position_limit/raw_text`, and `FundManagerDecision.score/score_label/stop_loss/position_pct`.
Fix: Either update frontend types/components to consume the persisted record schema, or add a backend DTO mapper that transforms stored records into the declared `AnalysisDetail` contract.

## Summary Table
| Severity | Count |
|----------|-------|
| CRITICAL | 4 |
| WARNING | 4 |
| INFO | 0 |

## Verdict
MAJOR_CONCERNS

Core failure and rerun paths can lose records or report failed analyses as completed, so the Phase 5 pipeline is not architecturally safe to ship yet.