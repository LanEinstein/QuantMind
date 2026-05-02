# P5B-T01 R3 测试维度 codex review

**最终判定**: ✅ 通过(初轮 🔧 → follow-up ✅)

## 初轮

- CRITICAL: 0 / HIGH: 3 / WARN: 4 / NIT: 1
- HIGH-1 `TestProductionConfigRoundTrip` 断言松,允许默认值兜底回归 → fix:`_PROD_THINKING_TABLE` parametrize,锁定 10 个 agent 各自 `(type, max_tokens, keep)` 三元组
- HIGH-2 fallback 路径未测 thinking 传播 → fix:`test_fallback_to_kimi_propagates_thinking` + `test_fallback_to_qwen_drops_thinking` 双向覆盖
- HIGH-3 escalation 错误路径未锁契约 → fix:`test_escalation_error_propagates`(无 fallback 版)
- WARN-1 parametrize 不等价 hypothesis shrinking → 注释明确说明,SSoT §2.3 hypothesis 后续单独引入(待 dep manifest 用户授权)
- WARN-2 escalation kwargs 断言只到 provider 顺序 → fix:`test_routing_escalates_with_full_kwargs` 强断言 model + thinking dict + temperature
- WARN-3 async mock 缺 `assert_awaited_once` → fix:全文 `AsyncMock(side_effect=...)` + `assert_awaited_once()` / `assert_not_awaited()`
- WARN-4 `pytest.mark.unit` / `pytest.mark.integration` 不一致 → fix:Group 1-3 全 `unit`,Group 4-7 全 `integration`
- NIT-1 legacy fallback 断言间接 → fix 注释 + 默认值断言

## Follow-up

- HIGH R3 `test_escalation_error_propagates` 不带 fallback,无法证伪"escalation 错误被 fallback 吞" → fix:新增 `test_escalation_error_not_caught_by_primary_fallback`,agent 配 fallback,断言 `deepseek_client.chat.completions.create.assert_not_awaited()`
- WARN R3 新文件 untracked → commit 前 `git add tests/test_llm_router_thinking.py`
- WARN R3 RouterConfig 跨字段 validator 只测一个分支 → fix:parametrize `agent.provider` / `fallback.provider` / `routing.triage_provider` / `routing.escalation_provider` 四个分支
- NIT R3 plain async function 塞 create → fix:全用 `AsyncMock(side_effect=...)`

## 测试金字塔覆盖

| 层 | 测试 | 覆盖 |
|---|---|---|
| Schema unit | TestThinkingConfigSchema(11) + TestRoutingConfigSchema(6) | extra/Field/Literal/invariant/frozen |
| Contract | parametrize 21 enabled combos + 1 disabled canonical + 10 invalid keep + 9 invalid type | 等价 hypothesis Literal shrinking |
| Normalize unit | TestNormalizeKwargsKimiEnabled(5) + Disabled(3) + NonKimi(3) | extra_body / temperature 1·0.6 / max_tokens budget / kimi-k2.7 future-proof |
| Production round-trip integration | 10 agent parametrize + legacy 兜底 + 4 cross-validator 分支 | SSoT §704-727 三元组锁定 |
| complete() integration | thinking enabled / disabled 双场景 + assert_awaited_once | extra_body 顶层 kwarg 不暴露 |
| Fallback integration | qwen→kimi 传播 + kimi→qwen 丢弃 | 双向契约 |
| Escalation integration | triage 优先 + escalation 全 kwargs + error propagate(无 fb)+ error 不被 fb 吞(有 fb) | T03 留 hook |

## Coverage

- `backend/llm/providers.py`:100%
- `backend/llm/router.py`:91%
- backend overall:81.67%(≥70% 阈值)
- backend/risk:97.60%(≥95% 阈值)
- pytest:856 passed / 11 skipped / 0 failed
