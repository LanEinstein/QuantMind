# P-004 代码审查总结 — 飞书篮子汇总(display-only 第 6 FeishuMessageKind)

**任务**: P-004(`FeishuMessageKind` 5→6 `BASKET_DIGEST` + `render_basket_digest` display-only + runner 末独立幂等发送 + main.py 接线)
**审查日期**: 2026-05-31
**审查工具**: codex review --uncommitted(额度已恢复 10:12 后;首次 300s 超时未出结论 → 重跑 900s 完整出结论)
**最终判定**: ✅ 通过(codex 报 1×P1 + 1×P2,全修)

## 发现与处置

| # | 级别 | 文件 | 问题 | 处置 |
|---|------|------|------|------|
| 1 | P1 | main.py:705-709 | allocation_policy.yaml 缺失/损坏时 try/except → None → `prime_allocation` no-op → 每只 BUY 退回 max_compliant 15%/股 sizing,**恰在配置错误时静默关闭配比层**(本层正是为防此激进行为而建)| **FIXED** 删 try/except,让 `load_allocation_policy` 直接 raise(与紧邻的 `load_budget_tier_config`/`load_selector_config` 一致 fail-closed);shipped 配置恒在,损坏须 abort 启动而非静默降级 |
| 2 | P2 | line1_runner.py:494-495 | `_route_candidate` 把 `send_failed`(ok=False)的派发也归 `Line1Outcome.ROUTED` → 进 `routed_buys` → digest 汇总了**从未送达 owner** 的订单 | **FIXED** `_send_basket_digest` 先过滤 `route_outcome.action ∈ {dispatched, skipped_duplicate}`(已送达)再渲染;全失败则跳过 digest;count 日志改 delivered 数 |

## 实现要点(红线遵守)
- **display-only**:`render_basket_digest` 仅 name/code/手数/金额/占比 + 合计部署 + 非交易免回复声明;**无 instruction_id(QM-)、无成交动词**;经 `renderer.py` 防注入;权重=各只 notional/合计(此处派生,非 LLM)。
- **不走 InstructionDispatcher**:runner `_aggregate` 后独立发送,自有幂等键 `{signal_id}-basket-digest` 走 OutboxRepository try_claim/mark_sent;send 失败 release(可重发),异常吞掉(digest 是 audit 便利非交易门)。
- **单一构造点不破**:digest 不构造 InstructionPlan。
- **5→6 枚举**:成员数测试 + docstring 同步;redline 成员数=6 校验在 P-007 加。

## 加固测试
- renderer:列名/code/手数/合计/占比 + 无 QM-/无成交动词 + **对抗喂 parse_execution_report 必 no_pattern_match** + pilot banner + 空 raise(5 例)。
- runner:digest 发一次 + 幂等(两 run 一发)+ **send_failed 不入 digest(共 2 只)**(3 例)。

## 门禁
- `pytest`(line1_runner + renderer):66 passed;全量 4306 passed(`FEISHU_INTERACTIVE_ENABLED=false`)。
- `ruff`:All checks passed。redline:全绿。
