# P0-10 Amendment 2026-05-10 — ¥20 hard ceiling 扩展为 LLM 总 daily ¥20 + Kimi 单独 daily ¥4 + 月 soft ¥440

## 元数据

| 字段       | 值 |
|-----------|----|
| amendment 编号 | P0-10-amendment-2026-05-10-cost-budget-extension |
| 主决策     | `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md` |
| 触发决策   | `docs/decisions/P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md` |
| 日期       | 2026-05-10 |
| 状态       | ✅ 已锁定 |
| 性质       | **非破坏式扩展**(¥20 daily hard 锁定不变;仅扩 monthly 维度 + Kimi 子维度)|

## 触发原因

P1-7 决策对齐需要在 P0-10 §1.4 单一 ¥20 daily hard ceiling 基础上扩展月度维度 + Kimi 单独 cap 以应对:

1. **月度成本观察盲区**:仅日 hard 缺月度健康度仪表盘进度数据点;月预算超 50%/80%/100% 节点无任何观察手段
2. **Kimi escalation 单笔失控风险**:Kimi `¥0.0084/k output` 是 deepseek 42 倍;单笔 escalation(8k thinking + 2k output)≈ ¥0.084,失控连发可单卡 ¥8.4 ≈ 全天预算 42%
3. **三 provider 失 failover 弹性风险**:全 provider 单独 cap 时若某 cap 灭可能开不出指令(deepseek 灭后 qwen + kimi 总额 ¥10 不够 4 必经 Agent 全跑)

## 变更内容

### P0-10 §1.4 第 X 段(¥20 hard ceiling 节)扩展为

> **LLM 单调用 30s 硬超时 + 0 重试**;失败即 Agent 返 None 不重试。LLM 全停 ≥1h 触发系统级中断(P0-6)。**LLM 总成本守门**:
>
> - **日 hard ceiling = ¥20**(原锁定不变;唯一全 LLM 暂停触发器;simulation_auto 进入 paused-budget-breach 状态;raise `DailyBudgetExceededError` + 飞书 critical + audit `daily_cost_ceiling_20cny_breached`)
> - **日 soft ceiling = ¥14 = 70% × ¥20**(原锁定不变;触发 `SoftDegradeManager.activate_kimi_escalation_block()` 关闭 Kimi escalation 路径;不暂停 LLM)
> - **月 soft ceiling = ¥440 = 22 工作日 × ¥20**(P1-7 新增;静态固定按自然月;50%/80%/100% 三阶节点 audit + 飞书 info/warning/critical;**不暂停 LLM**;详见 P1-7 §1.6)
> - **Kimi 子 cap = ¥4/日 = 20% × daily ceiling**(P1-7 新增;Kimi 单独熔断仅暂停 Kimi 调用不暂停 deepseek + qwen;raise `KimiDailyCapExceededError` + 飞书 warning + audit `kimi_daily_cap_4cny_breached`)
>
> 4 阈值(`_DEFAULT_DAILY_BUDGET_RMB=20.0` / `_DEFAULT_SOFT_CEIL_PCT=0.7` / `_DEFAULT_MONTHLY_BUDGET_RMB=440.0` / `_DEFAULT_KIMI_DAILY_CAP_RMB=4.0`)修改 = `git diff config/*.yaml backend/services/cost_guard.py` + `docs/decisions/P0-10-amendment-{date}-{原因}.md` + 进程重启三步;严禁 hot-reload(继承 P0-7 §2 红线 14)。

### 角色矩阵(从 1 维 → 3 维)

| 维度 | 阈值 | 角色 | 触发动作 | LLM 是否暂停 |
|------|------|------|----------|--------------|
| 日 | ¥14 (70%) | 软警告 | log warning + audit + `SoftDegradeManager.activate_kimi_escalation_block()` | ❌ 不暂停 |
| 日 | ¥20 (100%) | **硬熔断**(原 P0-10 §1.4)| raise `DailyBudgetExceededError` + 飞书 critical + audit `daily_cost_ceiling_20cny_breached` | ✅ **暂停** |
| 月 | ¥220 (50%) | 仪表盘节点 | audit `monthly_budget_50pct_reached` only | ❌ 不暂停 |
| 月 | ¥352 (80%) | 软警告 | audit + 飞书 warning | ❌ 不暂停 |
| 月 | ¥440 (100%) | 软警告(非硬熔断) | audit + 飞书 critical + cost_breakdown 附件 | ❌ 不暂停 |
| Kimi 日 | ¥4 (20% of daily) | **硬阻 Kimi only** | raise `KimiDailyCapExceededError`(仅暂停 Kimi 不暂停 deepseek + qwen)+ audit + 飞书 warning | 仅 Kimi 暂停 |

**关键原则**:**日 hard ¥20 是唯一全 LLM 熔断触发器**(原 P0-10 §1.4 不变);月 soft 是仪表盘观察;Kimi cap 是子熔断仅暂停 Kimi。

## 与 P0-10 §2 红线兼容性核查

- **§2 红线 1(LLM 字段权限矩阵)** ✅ 兼容:`backend/services/cost_guard.py` + `backend/services/soft_degrade_manager.py` 严禁 import `backend.{llm,agents,mirofish,data}`(继承)
- **§2 红线 2(4 必经 Agent 缺一即 HOLD)** ✅ 兼容:软触发降级只关 Kimi escalation 路径(fund_manager 仍走 triage qwen);严禁同时关闭 4 必经 Agent 任一
- **§2 红线 3(单调用 30s 硬超时 + 0 重试)** ✅ 不变
- **§2 红线 4(LLM 全停 ≥1h 触发 P0-6 中断)** ✅ 兼容:月预算 100% 严禁触发停摆(仅日 hard 触发);避免 P0-6 中断由月度波动假阳性触发
- **§2 红线 5(LLM 严禁参与回报/对账/验收/数据质量/RiskEngine)** ✅ 不变
- **§2 红线 6+7(Pydantic strict + extra='forbid' 三层守门)** ✅ 兼容:`BudgetState` frozen + strict + extra='forbid' 升级 15 字段(详见 P1-7 §1.1.3)
- **§2 红线 8(agent_models.yaml runtime 不可改 + hot-reload 禁用)** ✅ 兼容:`backend/services/cost_guard.py` 4 常量同款 runtime 不可改 + hot-reload 禁用(P1-7 §2 红线 11)

## 实施期任务

P1-7 实施期 Phase B 任务 G-002 + G-009 + G-010 + G-011 同步落地:

- **G-009**:升级 `backend/services/cost_guard.py::BudgetState` frozen 三维(daily + monthly + kimi 15 字段)
- **G-010**:新增 `KimiDailyCapExceededError` + `assert_kimi_cap_allows()` + `get_kimi_spent_today()`
- **G-011**:新增 `get_monthly_spent()` + `add_monthly_spent()` + `check_monthly_thresholds()`
- **G-013**:升级 `assert_budget_allows()` 集成 soft breach 触发 `SoftDegradeManager.activate_kimi_escalation_block()`

## 与历史 P0-N 决策协同

- **P0-1 §1.3 模式切换 = 账户生命周期事件不是 flag toggle**:LLM 暂停 → simulation_auto 进入 paused-budget-breach 状态(原 P0-10 §1.4 锁定不变)
- **P0-2 §2.5 备用 webhook 仅可发系统告警**:本 amendment 月节点 + Kimi cap + daily hard 飞书告警均走 `FEISHU_CUSTOM_BOT_WEBHOOK_URL`
- **P0-6 §2 5 类系统级中断重置窗口**:LLM 全停 ≥1h 仍由 daily hard 触发(月预算 100% 不触发,避免月度波动假阳性)
- **P0-7 §2 红线 14 RiskConfig runtime 不可改**:同款约束 cost_guard 4 常量 runtime 不可改 + hot-reload 禁用
- **P0-9 §2 5 单 cap 分配(traditional 4 + event 1)**:软触发不降 event_path(已最低)+ 不降 fast 频次(响应延迟不可接受)

## 不在本 amendment 范围内

- ❌ 不修改 §2 红线 1(LLM 字段权限矩阵)— LLM 仍可写 `InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text, conclusion}` / `risk_parameter_proposals.proposal_text` 4 类字段
- ❌ 不修改 §2 红线 2(4 必经 Agent + fund_manager 唯一倡议者)
- ❌ 不修改 §2 红线 3(LLM 30s 超时 + 0 重试)
- ❌ 不修改 agent_models.yaml tiered routing 锁定(fund_manager triage qwen + escalation kimi 配置仍由 agent_models.yaml 锁定;运行时由 SoftDegradeManager 标志位决定是否真触发 escalation)
- ❌ 不引入新 LLM provider(仍 deepseek + qwen + kimi 三 provider)
- ❌ 不修改 LLM key 存储位置(仍 `~/.bashrc` 单源;继承 P1-6 §1.1)

---

**P0-10 amendment 2026-05-10 锁定 ✅**

¥20 daily hard 不变;扩 monthly soft ¥440 + Kimi daily cap ¥4 + soft breach SoftDegradeManager;非破坏式扩展。
