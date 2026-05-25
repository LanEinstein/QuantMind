# N-002 Codex 跨模型代码审查报告

**任务**: N-002 — Line-2 异动 → 飞书 SELL(`sell_signal.py` + builder `assemble_monitoring_plan` + renderer `render_monitoring_sell`)
**审查时间**: 2026-05-25
**审查轮次**: 1 cycle + read-only final verification
**最终判定**: ✅ 通过(经最终复核)
**审查范围**: `backend/monitoring/sell_signal.py` + `backend/services/instruction_plan_builder.py` + `backend/integrations/feishu/renderer.py` + `tests/monitoring/test_sell_signal.py`

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 4 |
| 发现问题总数 | 2(P2 × 1 / P3 × 1;0 P0/P1) |
| 已修复 | 2(P2 必修 + P3 自愿修,均为真 fail-closed 改进) |
| 误报排除 | 0 |

## 第 1 轮 — `codex review --uncommitted`

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | sell_signal.py `make_sell_context` | 一次异动 scan 产出多个 held 码 SELL intent 时,调用方传**同一** scan `signal_id`;默认 `analysis_record_id`/`risk_validation_id` 仅由 signal_id 派生 → 多个 InstructionPlan 拿到**相同**关联句柄,ledger/risk 按这些字段查找会解析到错误 plan。 | ✅ FIXED |
| 2 | P3 | renderer.py `render_monitoring_sell` | 误传一条 VALIDATED 的 **Line-1** SELL plan 时满足守门,会被打上 Line-2 监控 header(尽管缺 `LINE2-MON-` 判别符)。 | ✅ FIXED |

### 修复详情

1. **per-plan 关联 ID(P2)**:`make_sell_context` 默认改为 `analysis_record_id = "mon:{code}:{seq}:{signal_id}"[:64]`、`risk_validation_id = "rv:{code}:{seq}:{signal_id}"[:64]` —— code+seq 前置(截断后仍保留),`signal_id` 保持共享(PIT 链回 scan manifest 的消费行)。多 intent 现获唯一关联句柄。回归测试 `test_multi_intent_distinct_correlation_ids`。
2. **renderer Line-2 守门(P3)**:`render_monitoring_sell` 新增 `plan.signal_id.startswith("LINE2-MON-")` fail-closed 检查(`_MONITORING_SIGNAL_PREFIX` 本地常量,镜像 builder 的 `MONITORING_SIGNAL_PREFIX`,renderer 不耦合 backend.services)。回归测试 `test_renderer_monitoring_sell_rejects_non_line2_plan`。

## 最终验证(read-only closure check)

**复核状态**: EXECUTED · **复核判定**: **PASS**

| # | 原问题 | 状态 |
|---|--------|------|
| 1 | 关联 ID 碰撞 | RESOLVED |
| 2 | renderer Line-2 守门 | RESOLVED |

**新增 P1 回归**: 无。(codex read-only 沙箱无临时目录无法跑 pytest;本地 45 测试已通过。)

## 门禁

- pytest:`tests/monitoring/test_sell_signal.py` 15 + `test_feishu_renderer.py` 30 = 45 passed;sell_signal 模块覆盖率 98%;Line-1 builder/renderer 无回归(156 相关测试)。
- ruff:全绿(monitoring `backend.{broker,data,risk}` 经 per-line `# noqa: TID251`,保持 `backend.{llm,agents,mirofish}` 禁令生效)。
- redline-check:全绿(M-004 InstructionPlan 单一构造点 AST 仍只认 model+builder —— Line-2 经 builder 新方法构造)。

## 红线遵守

- InstructionPlan 单一构造点(R0 §4):Line-2 SELL 经 `builder.assemble_monitoring_plan` 构造,monitoring 模块不构造 plan。
- §2.3 划界(P0-10-amendment-2026-05-25):SELL 方向由确定性异动检测器派生,无 fund_manager/辩论;`debate_round_count=1` 确定性监控评估轮;`LINE2-MON-` signal_id 供 audit 区分。
- SELL 读 `available_volume`(T+1 已结算);SELL 跳过 watchlist 早返(退出不被入场规则困住);经 RiskEngine 14-check + 飞书人工。

> 本报告由 Claude Code + Codex CLI 协同生成。
