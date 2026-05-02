# P5B-T01 R4 性能维度 codex review

**最终判定**: ✅ 通过(初轮 🛑 → follow-up ✅)

## 初轮

- CRITICAL: 1 / HIGH: 3 / WARN: 3 / NIT: 1
- **CRITICAL** Kimi `thinking` 写顶层 kwarg 而非 `extra_body` → 真实调用会 TypeError(OpenAI SDK 不接受) → fix:`_normalize_provider_kwargs` 改写入 `extra_body["thinking"]`,合并 caller 既有 extra_body
- HIGH-1 `thinking.max_tokens` 不会按预期节流(reasoning + completion 共享 request budget) → fix:enabled 时 `max_tokens += thinking.max_tokens`
- HIGH-2 disabled 时仍透传 caller temperature(K2.6 非 thinking 模式要求 0.6) → fix:disabled 强制 `temperature=0.6`
- HIGH-3 `ThinkingConfig.max_tokens` 缺上下界 → fix:`Field(ge=0, le=32_000)`
- WARN-1 fallback.py 缺 reasoning_tokens 字段 → fix:`_extract_reasoning_tokens` best-effort 从 `response.usage.completion_tokens_details`,`llm_call_complete` 日志写入
- WARN-2 _reload_config 在 close 旧 client 时可能影响活跃请求 → defer 至 backlog(单独 atomic-write task)
- WARN-3 双重日志在大 watchlist 下 verbose → defer(可观测性优化)
- NIT 高频日志可降 debug 或采样 → defer

## Follow-up

R4 维度全清。验证:
- `test_grows_max_tokens_by_reasoning_budget` 锁定 4096 + 8000 = 12096 (最大单 agent 预算 = 4k completion + 32k thinking = 36k 上限)
- `test_forces_temperature_one_when_thinking_on` + `test_forces_temperature_06_when_thinking_off` 双向锁定 K2.6 spec
- `test_emits_thinking_in_extra_body` + `test_merges_with_caller_extra_body` 锁定 SDK 兼容契约
- `test_kimi_k27_also_translated` future-proof K2.7+ 模型

## 性能影响估算

| 指标 | 修复前(P5A 遗留) | 修复后 |
|---|---|---|
| Kimi single-shot max_tokens | 16k 硬下限(无视 per-agent 配置) | per-agent 4-8k + reasoning(6-10k) |
| 单股 token cost(decision agents) | unbounded reasoning | 每 agent reasoning ≤ 配置 cap |
| disabled agent token | 仍受 16k 影响(实际无 reasoning 但 ceiling 太高) | 4096 顶部 + reasoning_tokens=0 验证 |

待 24h 真实部署期间通过 `llm_call_complete` 日志的 `reasoning_tokens` / `thinking_type` / `route_stage` 字段验证 SSoT §0 出口指标(单股 token ↓≥20%、决策一致率 ≥90%)。
