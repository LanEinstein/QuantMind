### Verification Table

| Check | Result | Notes |
|---|---:|---|
| Risk-engine redline | FAIL | `backend/risk` has no direct shadow import, but importing `backend.risk.engine` loads `backend.llm.*` transitively. See issue 1. |
| Authorization mode / suggest path | PASS | `schedule_shadow_run` only calls `run_shadow`, LLM router, and `record_shadow_decision`; it does not touch broker, approval queue, auth mode, or signal publishing. |
| Baseline cannot enter decision path | PASS | Shadow runs after live analysis and persists only comparison telemetry. No path back into trading authorization found. |
| Secret handling | LOW issue | No API keys/prompts are logged, but invalid `QUANTMIND_SHADOW_SAMPLE_RATE` is logged raw. See issue 2. |
| Markdown injection | PASS | `trade_date` is constrained to `YYYY-MM-DD`; rendered report fields are fixed labels or numbers. |
| Prompt injection | PASS | `_rebuild_user_content` replays LLM-produced reports, but the baseline output is observability-only and not used for authorization/trading. |
| JSON parsing | PASS | Baseline action is allow-listed; confidence is finite `[0,1]`; bool confidence is rejected; parse failures become `持有/0.5` with `parse_ok=False`. |
| Resource exhaustion | PASS | In-process shadow backlog is capped at 4; Kimi call is gated and timed out; DB upserts do not trigger shadow scheduling. |
| `shadow_decisions` data/TTL | PASS | Stored docs contain run/stock/date plus action/confidence/model/latency/flags only; no prompts, reasoning, or raw LLM responses. TTL remains 30 days. |

### New Issues Found (security)

1. HIGH: Risk-engine import isolation is violated transitively.

`backend/risk/engine.py` imports `backend.data.trading_hours`, but Python executes [backend/data/__init__.py](/home/ps/papers/QuantMind/backend/data/__init__.py:3) first. That initializer imports `DataScheduler` at [backend/data/__init__.py](/home/ps/papers/QuantMind/backend/data/__init__.py:9), which imports `backend.llm.cost_tracker` at [backend/data/scheduler.py](/home/ps/papers/QuantMind/backend/data/scheduler.py:12). A fresh `import backend.risk.engine` loaded `backend.llm`, `backend.llm.cost_tracker`, `backend.llm.fallback`, `backend.llm.providers`, and `backend.llm.router`.

This appears broader than the shadow patch, but it fails the stated redline. Fix by making `backend/data/__init__.py` lightweight, or moving `trading_hours` into a side-effect-free common/risk module. Add a regression test that imports `backend.risk` in a fresh interpreter and asserts no `backend.llm`, `backend.agents`, `backend.mirofish`, or `backend.services` modules are loaded.

2. LOW: Invalid shadow sample-rate env value is logged raw.

[backend/services/shadow_runner.py](/home/ps/papers/QuantMind/backend/services/shadow_runner.py:129) logs `raw=raw` when `QUANTMIND_SHADOW_SAMPLE_RATE` cannot parse. The variable is not meant to be secret, but the security rule was no env values echoed. Log only `fallback`, value type, or a redacted marker.

### Verdict

NEEDS_FIXES

The shadow execution path itself stays out of authorization/trading, but the risk import redline is currently failing transitively. I did a static/security review plus import probe; I did not run the test suite.