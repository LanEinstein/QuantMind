# P5B-T01 R2 UX 维度 codex review

**最终判定**: ✅ 通过(初轮 🔧 → follow-up ✅)

## 初轮

- CRITICAL: 0 / HIGH: 3 / WARN: 3 / NIT: 1
- HIGH-1 `backend/api/settings.py::AgentConfigInput` 漏 `routing` / `thinking` 字段 → fix:加 `ThinkingConfigInput` + `RoutingConfigInput` + 字段;`extra="forbid"`
- HIGH-2 `ThinkingConfig` 缺 `extra="forbid"` + Field 范围 + invariant → fix:provider.py 三层 validator
- HIGH-3 `llm_call_complete` 日志缺 thinking metadata → fix:`route_stage` / `thinking_type` / `thinking_max_tokens` / `reasoning_tokens` 四字段
- WARN-1 SSoT §2.8 `news_crawler` routing 例 `triage_provider` only(违反 RoutingConfig) → fix:改为不分级注释
- WARN-2 旧 blueprint V3 doc 例陈旧 → 已加 SSoT §7.4 deferred 项
- WARN-3 RouterConfig 不跨字段校验 provider 引用 → fix:`_check_provider_references` model_validator,报错带 `agents.{name}.path` 上下文
- NIT-1 SSoT "9 agent" → fix:10

## Follow-up

- HIGH R2 SSoT §2.8 `_normalize_provider_kwargs` 示例仍写顶层 `thinking` → fix:示例改 `extra_body["thinking"]` + 测试代码示例同步改 + 加 `extra_body[custom]` 合并演示
- WARN R2 SSoT `ThinkingConfig` 例缺 extra/Field/validator → fix:示例与实装完全一致
- WARN R2 `write_llm_config` 不校验 merged config 完整性 → fix:在 `ConfigService.write_llm_config` 内 deep-merge 后跑 `RouterConfig.model_validate`,失败抛 ValidationError;`backend/api/settings.py` catch ValidationError → 422

## 错误信息可读性

- `RouterConfig` 跨字段 validator 报错带完整 path(`agents.bull_researcher.routing.triage_provider='qwe' not in providers=[...]`),运维一眼定位
- `ThinkingConfig` invariant 报错明确说"disabled requires max_tokens=0 and keep='none'"
- `KeyError("Unknown agent 'X'. Available: [...]")` 已有(P5A 行为不变)

## 配置可读性

- `agent_models.yaml` thinking 段三字段 `type/max_tokens/keep`,与 SSoT §2.8 表 1:1 对应
- `extra="forbid"` 后 `thinkng:` 等 typo 立即 ValidationError,不再静默兜底
