# Codex 跨模型代码审查报告 — M-002 LangGraph 编排骨架

**项目**: QuantMind
**审查时间**: 2026-05-24
**审查轮次**: 1 / 1 + 最终只读复核
**最终判定**: ✅ 通过 (经最终复核)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件 | backend/agents_team/{state,nodes,graph,__init__}.py + 3 测试 + pyproject.toml |
| 发现问题总数 | 1 |
| 已修复 | 1 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 第 1 轮(`codex review --uncommitted`)

**Codex 判定**: NEEDS_FIXES(1 × P1)

| # | 严重度 | 文件:行 | 问题 | 处理 |
|---|--------|---------|------|------|
| 1 | P1 | state.py:97-98 / risk_gate_node | `TeamContext` 默认 `daily_state=None` + `stock_meta=None` 时,`RiskEngine.validate_order` 退化为 **legacy 7-check 模式**(跳过 check 8-14:单笔金额上限 / universe 白名单 / 涨跌停 / 熔断)。agents_team 决策路径契约要求 **完整 14-check**,否则超 ¥5 万单笔 / 禁板块订单可能 fail-open 拿到 BUILD_OK。 | ✅ FIXED |

### 修复详情

**#1 fail-open 退化 7-check** — `risk_gate_node` 在 routable(BUY/SELL)方向下、调用引擎**之前**增加 fail-closed 守门:`daily_state is None or stock_meta is None` → 直接返回 `risk_passed=False, risk_rule="risk_context_incomplete"`,绝不退化 7-check。守门先于引擎执行,所以超 cap 订单不可能借不完整上下文绕过 check 9/11 等扩展检查。新增 4 个测试:缺 daily_state / 缺 stock_meta / 超 cap 订单(20 万股 × 4.5 = ¥90 万)被守门拦下 / 端到端 `test_graph_rejects_when_risk_context_incomplete`(decision=REJECTED reason=`risk:risk_context_incomplete`)。

## 最终验证(read-only 复核)

**复核状态**: EXECUTED — **复核判定**: PASS

| # | 原问题 | 当前状态 |
|---|--------|----------|
| 1 | risk_gate 退化 7-check fail-open | RESOLVED(complete-context 守门;over-cap 不可绕过)|

新增严重(P1)回归:**无(NONE)**。复核 trace 确认全图端到端跑通:3 analysts(并行)→ debate(round≥1)→ fund_manager(BUY 提议)→ risk_gate(14-check passed)→ builder。

## 红线/不变量覆盖确认

- **RiskEngine/Builder 作纯节点 + LLM 无边可写**:`risk_gate_node`/`builder_node` 同步纯函数;拓扑测试断言 analysts/debate 无任何指向 tool 节点的边,只有 fund_manager→risk_gate→builder;tool 节点输出仅依赖数值 state + direction 提议(determinism 测试:改 agent 文本 → tool 输出不变)。
- **agent stub 永不写 tool/decision/数值键**(`test_agent_stubs_never_write_tool_output_keys`)。
- **fund_manager 唯一方向提议方**;4 必经缺失 → HOLD;`debate_round_count≥1`;HOLD 永不路由。
- **本地 SQLite checkpointer**(`AsyncSqliteSaver`,`:memory:` 或文件;断言写出 `SQLite format 3` 文件头,无 hosted SaaS)。
- **单一构造点**:agents_team **不构造 InstructionPlan**(builder_node 仅出终局 decision;真正构造 M-003/M-004 委派 `instruction_plan_builder`)。
- 依赖 `langgraph-checkpoint-sqlite==3.0.3` 安全(requires `langgraph-checkpoint>=3,<5`,与 langgraph 0.6 锁的 checkpoint 3.0.1 兼容;`>=3,<3.1` pin 防 3.1+ 强升 checkpoint≥4.1 破坏 langgraph 0.6)。

**本地门禁**:32 passed,模块覆盖率 100%,ruff(touched)全绿,redline-check 全绿,既有 `backend/agents` 管线(test_agents_graph)回归 12 passed。

> 本报告由 Claude Code(修复)+ Codex CLI(审查)协同生成。
