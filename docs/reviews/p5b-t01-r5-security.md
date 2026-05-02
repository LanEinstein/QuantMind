# P5B-T01 R5 安全 + ops 维度 codex review

**最终判定**: ✅ 通过(初轮 🔧 → follow-up ✅)

## 初轮 + Follow-up

- CRITICAL: 0 / HIGH: 0 / WARN(实际): 2 + 2 deferred / NIT: 1
- WARN-1 `escalation_condition: dict[str, Any]` 弱类型 → defer 至 P5B-T03(随真实 escalation 决策落地一并改为 typed `EscalationCondition` 模型)
- WARN-2 `escalating_to_expensive_provider` 日志缺 cost / condition / reason → fix:加 `triage_model` + `escalation_condition`,T03 再加 cost 估算
- WARN-3 `_reload_config` 读文件 TOCTOU + 非 atomic write → defer 单独 backlog(临时文件 + `os.replace()`,reload 失败保留旧 config)
- WARN-4 `ThinkingConfig.max_tokens` 无上限 → fix:`Field(le=32_000)`
- NIT-1 frozen 浅冻结(escalation_condition dict 可原地 mutate) → 等 T03 typed 模型一并解决

## 红线检查表(本次 working tree)

| 红线 | 结果 | 验证 |
|---|---|---|
| AUTHORIZATION_MODE=suggest 不越界 | ✅ | `.env` 仍 suggest;`backend/services/authorization.py` startup gate 未触碰 |
| backend/risk/ 不 import backend.llm/agents/mirofish | ✅ | grep 仅命中 engine.py docstring 反向声明 |
| .env 不入 git;LLM key 仅 shell env | ✅ | `agent_models.yaml` 仍 `${ENV}` 占位;`.env` `.gitignore` 未变 |
| MongoDB / Redis 仅 127.0.0.1 | ✅ | docker-compose / `.env` loopback 未触碰 |
| 6C 前不激活实盘 adapter | ✅ | `BROKER_MODE=mock` / `config/broker.yaml active: mock` 未触碰 |
| 不跨阶段自动推进 | ✅ | P5B-T01 marker ⏳→🔧→✅;Phase 5B 出口检查仍 ⏳;T02/T03 仍 ⏳ |

## 关键安全保障

- `extra="forbid"` 在 `ThinkingConfig` / `RoutingConfig` / `ProviderConfig` / `FallbackConfig` / `AgentConfigInput` / `ThinkingConfigInput` / `RoutingConfigInput`:typo 立即拒绝,不静默兜底
- `Field(min_length=1)` 在 provider/model 字符串字段:空字符串拒绝
- `RouterConfig._check_provider_references`:跨字段 validator,任何 typo provider 立即 ValidationError 带 path 上下文
- `ConfigService.write_llm_config` 在 deep-merge 后跑 `RouterConfig.model_validate`:partial update 不能把 YAML 写成不可加载状态
- `_extract_reasoning_tokens`:`getattr` 链全 defensive,never raise(纯观测,故障也不阻塞主路径)
- `reasoning_content` 不进 structlog:验证 `_call_provider` 仅记 `reasoning_tokens`(int),不记 content

## 部署后跟踪(24h 验证窗口)

- `llm_call_complete` 日志 `thinking_type=disabled` 的 agent → `reasoning_tokens=0`(SSoT 出口要求)
- `escalating_to_expensive_provider` 频率 ≤ 0(P5B-T01 _should_escalate 永 False;T03 后才会 > 0)
- 无 `llm_config_invalid_merge` warning(API 上游不会推坏 YAML)
