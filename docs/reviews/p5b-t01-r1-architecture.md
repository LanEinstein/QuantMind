# P5B-T01 R1 架构维度 codex review

**最终判定**: ✅ 通过(初轮 + follow-up 后)

## 初轮(代码改动前一版)

- CRITICAL: 0 / HIGH: 0 / WARN: 3 / NIT: 2
- WARN-1 RoutingConfig 允许只填半边 escalation pair → fix:`_check_escalation_pair` model_validator
- WARN-2 `_should_escalate` 签名 bool 不便 T03 扩展 → defer 至 T03(签名变更随真实 escalation 逻辑同步)
- WARN-3 fallback 不覆盖 escalation 失败 → 测试用例 `test_escalation_error_not_caught_by_primary_fallback` 锁定契约,T03 视情况调整
- NIT-1 `ThinkingConfig.max_tokens` 缺范围 → fix:`Field(ge=0, le=32_000)`
- NIT-2 SSoT "9 agent" → fix:改为 10

## Follow-up 复审(本次提交前)

- WARN R1 `AgentConfig` 仍默认 extra ignore → fix:`extra="forbid"` + 同步 `FallbackConfig` / `ProviderConfig`
- WARN R1 `triage_model` 允许空字符串 → fix:`Field(min_length=1)`

## 模块边界

- `providers.py` 仅持 schema + 客户端工厂;`router.py` 持调度 + provider kwarg 翻译。两者职责清晰。
- `_normalize_provider_kwargs` 是 static 方法,无状态依赖,便于复用。
- thinking 传播路径:`complete()` → `_call_provider(thinking=...)` → `_normalize_provider_kwargs`,无隐式 globals。

## 红线检查

| 红线 | 验证 |
|---|---|
| backend/risk/ 不 import backend.llm/agents/mirofish | grep 仅命中 engine.py docstring 反向声明 |
| 不跨阶段自动推进 | 仅 P5B-T01 marker ⏳→🔧→✅,Phase 5B 出口仍 ⏳ |
| AgentConfig frozen | ✅ + 现在 extra="forbid" |

## Deferred(已记录 SSoT §7.4)

- `RoutingConfig.escalation_condition` 仍 `dict[str, Any]` — T03 owner 实装时收敛为类型化模型
- `_reload_config` TOCTOU/atomic write 加固 — 单独 backlog,与本 task 解耦
