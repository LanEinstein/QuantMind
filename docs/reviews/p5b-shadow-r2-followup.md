### Verification Table
| # | Issue | Status | Notes |
|---|-------|--------|-------|
| 1 | Debate transcript format mismatch | RESOLVED | `_join_debates` now emits `【看多研究员】` / `【看空研究员】`, matching the live bull/bear appenders. |
| 2 | Concurrent shadow tasks could race past budget guard | RESOLVED | `run_shadow` now locks budget probe + baseline call. This resolves the same-process race; cross-process locking remains explicitly deferred. |
| 3 | Parse-failed legs skewed gate metrics | UNRESOLVED | `compute_shadow_report` now excludes persisted `parse_ok=False` pairs correctly, but live routed legs are still always written as `parse_ok=True`, so routed parse-fallback `持有/0.5` can still enter gate math. |

### New Issues Found
[P2] Routed parse failures are still marked gateable  
File: [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:253)  
Confidence: HIGH  
Issue: `_routed_leg_from_record` hard-codes `parse_ok=True`. If the live `fund_manager` response failed parsing and fell back to synthetic `持有/0.5`, the shadow report cannot exclude it, so the R1 parse-failure fix only works for baseline failures or prebuilt docs.  
Fix: Persist fund-manager parse status into `AnalysisRecord` / `FundManagerRecord`, then pass it through to `ShadowDecisionLeg.parse_ok`.

[P2] Missing Mongo still burns the baseline LLM call  
File: [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:390)  
Confidence: HIGH  
Issue: `services.mongodb` is checked after the budget probe and Kimi call. With Mongo absent, shadow spends money and then discards the result.  
Fix: Check `mongodb is None` before entering the budget/Kimi section.

[P2] Baseline parser does not match live fund-manager parsing  
File: [backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:72)  
Confidence: HIGH  
Issue: The greedy `{.*}` regex and strict numeric confidence check reject responses the live parser accepts, such as first JSON followed by another brace block or `"confidence": "0.75"`. Those become false `parse_ok=False` samples.  
Fix: Reuse/refactor the live fund-manager JSON extraction/coercion path and return parse status from the shared parser.

[P3] No-Mongo test does not exercise `mongodb=None`  
File: [tests/test_shadow_runner.py](/home/ps/papers/QuantMind/tests/test_shadow_runner.py:93)  
Confidence: HIGH  
Issue: `_make_services(..., mongodb=None)` replaces `None` with a `MagicMock`, so `test_no_mongo_short_circuits` misses the real branch and does not assert the router was not called.  
Fix: Use a sentinel default and assert `router.complete.assert_not_called()`.

### Verdict
NEEDS_FIXES

I could not run the pytest subset because this sandbox has no usable temp directory, even with pytest cache and bytecode writes disabled.