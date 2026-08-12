# P0-10 修订 — 2026-05-24 固定 4 必经 Agent → 可进化多 agent 团队 + ≥2 交易员

> **修订基准**: [P0-10 LLM 角色边界 + fail-closed 降级 + 四必经 Agent](./P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 4 项 + §4 单一构造点红线
> **修订日期**: 2026-05-24
> **触发**: Owner 要 ≥2 个「具有专业知识、独立人格、可在实践中自我进化」的交易员 agent 结合预算判断何时买 / 买多少。`AskUserQuestion` 确认推翻 P0-10「固定 4 必经 agent」。

## 1. 修订前(P0-10 原锁定)

- 4 必经 agent:`fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`;任一缺失降级 HOLD。
- `fund_manager` 唯一 BUY/SELL/HOLD 倡议者;`debate_round_count ≥1`;`fund_manager_shadow_baseline` 永不入决策。
- LLM positive list 4 字段;Pydantic strict + extra=forbid + lint 三层守门。
- `agent_models.yaml` runtime 不可改 + hot-reload 禁用 + 仅 GET API。

## 2. 修订后(本 amendment 锁定)

### 2.1 可进化多 agent 团队

- **4 必经 agent 保留**(MVP 单轮辩论就用这 4;P0-10 必经语义 + 缺失降级 HOLD 不变)。
- **新增 ≥2 交易员 agent**(Phase T),各持一张**人格卡**:示例 `trader_momentum`(动量 / 入场时点)+ `trader_mean_reversion`(均值回归 / 仓位 sizing)。
- 编排迁移到 **LangGraph**:`RiskEngine` + `InstructionPlanBuilder` 作**纯 Python 节点**,**LLM 无边可写**它们(确定性工具门)。

### 2.2 人格稳定 vs 行为进化(不打架)

- **人格 = frozen、git 版本化 YAML 人格卡**(身份 / mandate / 输出 schema)= **不可变**。
- **行为改进**经 Reflexion 教训 + FinMem 风格 exemplars(**≤3 / prompt**,仅 RiskEngine 通过的案例)+ DSPy/GEPA prompt 演化,作为**批准 artifact**(P2-2 体系)存储,**与人格卡分离**。人格卡定义「agent 是谁」,exemplars 只示范「该人格下的好输出」——二者不冲突。
- 人格卡不可变 + exemplars 经人工 gate(P2-2 amendment)+ `LiveArtifactRegistry` 只认 startup 批准的 `prompt_version_hash`(R0 §8)。

### 2.3 决策权不变(安全核心,配 R0 §4)

- `fund_manager` **仍是唯一 BUY/SELL/HOLD 倡议者**,且**仅倡议方向**;交易员 agent 提的「何时买 / 买多少」是**建议文本**,经 `InstructionPlanBuilder` **确定性派生** `volume / limit_price / 整手`,**永不**由 agent JSON 直接定(R0 §4 单一构造点红线)。
- `debate_round_count ≥1` + `fund_manager_shadow_baseline` 永不入决策 **不变**。
- LLM positive list 4 字段 + strict + extra=forbid + lint 三层守门 **不变**。

### 2.4 配置不可改不变

`agent_models.yaml` runtime 不可改 + hot-reload 禁用 + 仅 GET API **不变**;新增 agent / 人格卡 / 路由调整走 amendment + git diff + 重启。

## 3. 实施期任务调整

- `backend/agents_team/`(新模块,Phase M MVP 用 4 必经 / Phase T 加 ≥2 交易员):LangGraph 编排 + 人格卡加载 + RiskEngine/Builder 纯节点。严禁给 LLM 边写决策字段。
- `config/agent_models.yaml` + `config/prompts/{agent}/{version}.yaml`:新增交易员人格卡(frozen + git);Phase T 落地。
- **对抗测试**(R0 §4 §5):`proposal_text="BUY 5000 shares..."` → 断言 `InstructionPlan.volume` 由 sizing 派生而非 5000。
- `redline-check.sh` 加子检:`InstructionPlan(` 构造站点 ⊆ {model, builder, tests}(单一构造点红线)。

## 4. 红线清单(本 amendment 之后)

1. `fund_manager` 唯一 BUY/SELL/HOLD 倡议者,**仅倡议方向**;交易员 agent 仅出建议文本。
2. `InstructionPlan` **单一构造点**(仅 builder);`side/volume/limit_price` 确定性派生,永不来自 agent/LLM JSON(R0 §4)。
3. 4 必经 agent 保留 + 缺失降级 HOLD;新增交易员 agent 不削减必经 agent。
4. 人格卡 frozen + git 版本化 = 不可变;exemplars ≤3/prompt + 人工 gate + `LiveArtifactRegistry` 只认批准 `prompt_version_hash`。
5. LangGraph 工具门确定性:RiskEngine / Builder 纯 Python 节点,LLM 无边可写;`backend/agents_team/` 严禁让 LLM 输出流入决策字段。
6. LLM positive list 4 字段 + strict + extra=forbid + lint 三层守门不变;`agent_models.yaml` runtime 不可改 + hot-reload 禁用不变。

## 5. 修订记录追加

`docs/plan.html` Phase M/T 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.3 多 Agent 守门表述补充「4 必经 + 可进化交易员团队(人格卡不可变);fund_manager 仍唯一倡议方向;builder 仍确定性派生」。
