# P5B-T01 Codex Review 综合 — agent_models.yaml schema 扩展 + Per-Agent Thinking Config

**最终判定**: ✅ 全部 5 维度通过(初轮 + follow-up 闭环)

## 5 轮初评

| 轮次 | 初评 | 主要发现 |
|---|---|---|
| R1 架构 | ✅ 0/0/3/2 | RoutingConfig pair validator + hook 签名 + fallback 语义 |
| R2 UX | 🔧 0/3/3/1 | settings.py 漏字段 + extra=forbid + log thinking 元数据 |
| R3 测试 | 🔧 0/3/4/1 | round-trip 断言松 + fallback thinking 传播 + escalation 错误路径 |
| R4 性能 | 🛑 1/3/3/1 | **CRITICAL**:thinking 顶层 kwarg → 必须 extra_body |
| R5 安全 | 🔧 0/0/4/1 | escalation_condition 弱类型 + log enrichment + TOCTOU |

## Follow-up 复审(代码修复后)

| 轮次 | 复评 | 闭环情况 |
|---|---|---|
| R1+R3 | 🔧 → ✅ | 1 R3 HIGH (escalation+fallback contract) + 4 WARN + 1 NIT 全 fix |
| R2+R4+R5 | 🔧 → ✅ | 1 R2 HIGH (SSoT example top-level thinking) + 2 WARN(merged validation + ThinkingConfig 示例)全 fix;2 deferred WARN(T03 / backlog)经签收 |

## 核心修复

### CRITICAL — R4 SDK 兼容
- `_normalize_provider_kwargs`:thinking 写入 `extra_body["thinking"]`,合并 caller 既有 extra_body;不再用顶层 kwarg
- enabled 时 `temperature=1`、`max_tokens += thinking.max_tokens`(reasoning + completion 共享 budget)
- disabled 时 `temperature=0.6`(K2.6 非 thinking 模式常量)

### HIGH — schema 强化
- `ThinkingConfig` / `RoutingConfig` / `FallbackConfig` / `ProviderConfig` / `AgentConfig`:`extra="forbid"`
- `ThinkingConfig.max_tokens`:`Field(ge=0, le=32_000)` + `_check_disabled_invariant` model_validator
- `RoutingConfig`:`_check_escalation_pair` model_validator
- `RouterConfig`:`_check_provider_references` 跨字段 validator
- 所有 provider/model 字符串字段:`Field(min_length=1)`
- `backend/api/settings.py`:`ThinkingConfigInput` + `RoutingConfigInput` + `extra="forbid"` 全部 Input 模型
- `backend/services/config_service.py::write_llm_config`:deep-merge 后跑 `RouterConfig.model_validate`,失败抛 ValidationError;API 层 catch → 422

### HIGH — observability
- `_call_provider` 加 `route_stage` 参数(triage|primary|fallback|escalation)
- `llm_call_complete` 日志加 `thinking_type` / `thinking_max_tokens` / `route_stage` / `reasoning_tokens`
- `escalating_to_expensive_provider` 日志加 `triage_model` + `escalation_condition`
- `_extract_reasoning_tokens`:best-effort 从 `response.usage.completion_tokens_details`

### HIGH — testing
- `_PROD_THINKING_TABLE` parametrize 锁定 10 个 agent 各自 `(type, max_tokens, keep)` 三元组
- `test_fallback_to_kimi_propagates_thinking` + `test_fallback_to_qwen_drops_thinking`:fallback 双向契约
- `test_routing_escalates_with_full_kwargs`:strong assert escalation 带 thinking,triage 不带
- `test_escalation_error_propagates` + `test_escalation_error_not_caught_by_primary_fallback`:无 fb / 有 fb 双场景
- 4 个分支 parametrize cross-validator(agent.provider / fallback / routing.triage / routing.escalation)
- 全文 `AsyncMock(side_effect=...)` + `assert_awaited_once()` / `assert_not_awaited()`
- `@pytest.mark.unit` / `@pytest.mark.integration` 全文统一

## 红线检查(全部 ✅)

- AUTHORIZATION_MODE=suggest 不越界
- `backend/risk/` 无 backend.llm/agents/mirofish import
- `.env` 不入 git;LLM key 仅 shell env
- MongoDB / Redis 仅 127.0.0.1
- 6C 前不激活实盘
- 不跨阶段自动推进

## Deferred(已记录 SSoT §7.4)

| 项 | Owner | 理由 |
|---|---|---|
| `RoutingConfig.escalation_condition: dict[str, Any]` 类型化 | P5B-T03 | 随真实 escalation 决策落地一并改为 typed 模型,避免重复抽象 |
| `_should_escalate` 签名 → tuple[bool, str] | P5B-T03 | T03 引入 escalation reason 时同步 |
| `_reload_config` TOCTOU + atomic write | backlog | 与 T01 解耦,单独 task |
| blueprint V3 旧示例去陈旧化 | docs cleanup backlog | 与代码 task 解耦 |
| backend/agents/base.py 双重日志采样 | 可观测性优化 backlog | 高频路径优化,非红线 |
| `hypothesis` 测试框架引入 | dep manifest 单独 PR | 需要用户书面授权安装 |

## 测试 / coverage 结果

- pytest:**856 passed** / 11 skipped / 0 failed(基线 762,+94 net)
- backend/risk:97.60% ≥ 95% ✅
- backend overall:81.67% ≥ 70% ✅
- backend/llm/providers.py:100%
- backend/llm/router.py:91%
- ruff:全过
- 红线 grep:仅 docstring,真 import 0

## 24h 部署窗口跟踪点(`docs/reviews/p5b-t01-thinking-impact.md` 续作)

- `llm_call_complete` 日志中 `thinking_type=disabled` agent 的 `reasoning_tokens` 必须 = 0
- 单股 token 消耗 vs P5A baseline ↓ ≥ 20%
- 决策一致率(action+confidence 区间)vs baseline ≥ 90%
- 无 `llm_config_invalid_merge` warning
- `escalating_to_expensive_provider` 频率 = 0(T01 永不触发,T03 后转正)
