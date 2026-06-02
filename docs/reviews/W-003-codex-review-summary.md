# W-003 display-only 飞书 thesis digest — codex review summary

> 任务:W-003(plan.html Phase W)。审查:`codex review --uncommitted`(cycle 1,后台 `</dev/null`)。
> 模型:codex-cli 0.133.0。本地门禁前置全绿(pytest 4558 / ruff / redline,FMK 仍锁 6)后跑 codex。

## 发现与处置(2 条 P2,全修)

| # | 等级 | 位置 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P2 | `line2_thesis_review_runner.py` | **digest 未以持久化 evidence 为前提** —— evidence 写失败(Mongo outage)或无 writer 时 verdict 仍进 digest,owner 收到无 evidence_collection 痕迹的 advisory,破坏 evidence-only/provenance 保证 | ✅ `_write_evidence` 返回 bool;run() 只把**成功落 evidence** 的 verdict 收进 `persisted`,digest 仅发 persisted;Mongo outage(全失败)→ 整个 digest 跳过 |
| 2 | P2 | `renderer.py` thesis digest | **未 redact LLM reason 的订单类 token** —— LLM reason 若回显 `QM-…` 指令号或 `已执行/部分执行/未执行` 等回报动词,直接渲染进决策群,破坏 display-only/no-instruction-id 保证 | ✅ renderer(单一显示门)新增 `_redact_order_tokens`:`QM-\d…` → `[指令号已隐去]`、回报动词 → `□`,渲染 reason 前过滤;runner + renderer 两级对抗测试 |

## 验证
- 修后:pytest **4561 passed, 13 skipped**(+3 codex-fix 测试)/ ruff All checks passed / redline All passed。
- FeishuMessageKind 仍锁 6(digest 走直接 send_message,**不新增 kind、不破 P-004 锁**)。
- 安全地基红线一条未破:display-only(无 QM- / 无回报动词 / 对抗 `parse_execution_report` 必 `no_pattern_match`)/ 幂等 outbox at-most-once / advisory 永不碰决策字段 / 不构造 InstructionPlan / 确定性 SELL 路径不变。

## 备注
- 两条均为 advisory 输出的 provenance + display-only 边界加固,属真红线张力点(LLM 文本进飞书),已修并对抗测试钉死。
