# backend/agents_team/ — 子任务上下文(Phase M MVP / Phase T 扩展)

> 状态:**todo**(Phase M 4 必经单轮;Phase T 加 ≥2 交易员)。治理:[P0-10-amendment-2026-05-24](../../docs/decisions/P0-10-amendment-2026-05-24-evolvable-agent-team.md) + R0 §4。任务:plan.html M-002/M-003/T-001/T-002/T-004。

## 职责
**LangGraph 多 agent 编排**:4 必经 agent(MVP 单轮)+ ≥2 交易员 agent(Phase T);辩论收敛候选;交易员提"何时买/买多少"建议。

## 本模块红线(守住 LLM 不写决策)
1. **InstructionPlan 单一构造点**:本模块**不构造** InstructionPlan;交易员建议是**文本**,经 `instruction_plan_builder` **确定性派生** side/volume/limit_price。对抗测试:`proposal_text="BUY 5000"` → volume≠5000。
2. RiskEngine 14-check + InstructionPlanBuilder 作 **LangGraph 纯 Python 节点**,**LLM 无边可写**它们(确定性工具门)。
3. `fund_manager` 仍**唯一 BUY/SELL/HOLD 倡议者**(仅倡议方向);4 必经缺失降级 HOLD;`debate_round_count≥1`。
4. **人格卡 frozen git 版本化**(身份/mandate/输出 schema 不可变);行为进化经 Reflexion/exemplars(≤3/prompt,仅 RiskEngine 通过案例)作批准 artifact,与人格卡分离。
5. LLM positive list 4 字段;单调用 30s + **per-stage 0 重试**;cost 经 `cost_guard` 真·预留(**一次辩论/每日 shortlist 非 per candidate**)。
6. agent 数/路由/人格卡 runtime 不可改 + hot-reload 禁用;新增走 amendment + 重启;`LiveArtifactRegistry` 认 `prompt_version_hash`。

## import 隔离
可 `import backend.risk`(作纯节点调用),但 **LLM 输出永不流入决策字段**。严禁让 agent JSON 直达 InstructionPlan 数值字段。本地 checkpointer(SQLite),无 hosted SaaS。

## 接口契约(草案)
- LangGraph 图:analysts → debate → trader → risk(纯节点)→ builder(纯节点)。
- 人格卡 `config/prompts/{agent}/{version}.yaml`。
