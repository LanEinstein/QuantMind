# P0-10 修订 — 2026-05-25 Line-2 持仓监控确定性构造路径(§2.3 划界 Line-1)

> **修订基准**: [P0-10 LLM 角色边界 + 四必经 Agent](./P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md) §2.3 + [P0-10-amendment-2026-05-24-evolvable-agent-team](./P0-10-amendment-2026-05-24-evolvable-agent-team.md) §2.3
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §1(双线)+ §4(单一构造点红线)+ §8(Line-2 纯量化轮询零 LLM)
> **修订日期**: 2026-05-25
> **触发**: Phase N 持仓监控落地时发现硬约束冲突 —— R0 §4 红线锁「`InstructionPlan` 仅 `instruction_plan_builder` 构造」+ InstructionPlan schema 锁 `debate_round_count ≥ 1`,但 R0 §8 + `backend/monitoring/CLAUDE.md` 锁「Line-2 纯量化轮询零 LLM」(无 fund_manager、无辩论)。现有 `builder.assemble_plan` 对**所有** BUY/SELL 强制 4 必经 agent gate + `debate_round_count ≥ 1`,Line-2 无从满足。Owner 2026-05-25 `AskUserQuestion` 确认推荐方案(新增 builder 确定性方法 + 本 amendment 划界;Line-2 专用飞书模板)。

## 1. 修订前(P0-10 §2.3 + 2026-05-24 amendment §2.3 原锁定)

- `fund_manager` **唯一 BUY/SELL/HOLD 倡议者**,且仅倡议方向;`debate_round_count ≥ 1` 必经;4 必经 agent 任一缺失降级 HOLD。
- 文字**未区分 Line-1 / Line-2**;`builder.assemble_plan` 是唯一构造入口,对所有 BUY/SELL 强制 4-agent gate + debate≥1。
- InstructionPlan schema:`debate_round_count: Field(ge=1)`(P0-1 §1.6「zero rounds = LLM bypass」)。

## 2. 修订后(本 amendment 锁定)

### 2.1 §2.3 显式划界:Line-1(LLM 选股)vs Line-2(确定性持仓管理)

- **§2.3「fund_manager 唯一 BUY/SELL/HOLD 倡议者 + 4 必经 agent gate + debate≥1」明确划归 Line-1**(全市场筛 → 候选 → 多 agent 辩论 → BUY/SELL/HOLD 选股决策路径)。**不变**。
- **Line-2 持仓监控**(R0 §1 第二条线)是**确定性、零 LLM** 的风险管理路径:SELL/ADD 方向由**确定性** `AnomalyDetector`(N-001)/ `AddPositionEvaluator`(N-003)派生,**不经** fund_manager、**不经**多 agent 辩论。这是 R0 §8「Line-2 纯量化轮询零 LLM」的必然落地,**比 Line-1 更 provenance-clean**(决策方向无任何 LLM 参与)。

### 2.2 Line-2 单一构造点(R0 §4 红线**保持**,新增确定性方法)

- Line-2 SELL/ADD 的 `InstructionPlan` **仍只能由 `instruction_plan_builder` 构造**(R0 §4 红线 1 不破;`grep "InstructionPlan(" ⊆ {model, builder, tests}` + M-004 AST 守门继续生效)。
- 新增 **`InstructionPlanBuilder.assemble_monitoring_plan(...)`** 确定性方法,与 `assemble_plan`(Line-1)并列:
  - **不要求** 4 必经 agent gate / `MandatoryAgentRecords` / `FundManagerOutput`(Line-2 无 LLM 倡议方)。
  - **仍跑** D-003 五道早返(mode_switch / ticket / circuit_breaker[SELL 永不熔断]/ data_quality / watchlist)+ RiskEngine 14-check(R0 §4 红线 2「决策字段确定性派生」+ 双层守门不破)。
  - `side / volume / limit_price / stock_code` 由**确定性输入**派生(异动检测器/补仓评估器 + sizing 规则 + `available_volume`),**永不来自 LLM JSON**(R0 §4 红线 2/3/4 不破 —— Line-2 根本无 LLM 输入)。
- monitoring 模块(`backend/monitoring/`)**不构造** `InstructionPlan`;它产出确定性的 `MonitoringSignal`(SELL/ADD 意图 + 派生的 volume/limit_price)交给 builder 构造。

### 2.3 `debate_round_count` 语义(Line-2)

- Line-2 plan 的 `debate_round_count = 1`,语义为「**一次确定性监控评估轮**」(非 LLM 辩论)。schema `Field(ge=1)` **不改动**(避免动核心 frozen schema + 3506 测试基线 + 状态机 + 飞书 + 对账全链)。
- 「zero rounds = LLM bypass」的原始动机针对 **Line-1 LLM 选股**;Line-2 无任何 LLM 参与决策方向,不存在「绕过 LLM 守门」风险,故 `=1` 充分且语义自洽。
- **audit 可区分**:Line-2 plan 的 `signal_id` 用 **`LINE2-MON-` 命名空间前缀**(Line-1 用 `signal_id` 既有格式),`invalidation_summary` 含「Line-2 deterministic monitoring」标记;`risk_validation_id` 同理带 Line-2 标记。下游 audit/对账可凭 signal_id 前缀辨别两条线。

### 2.4 Line-2 飞书模板(经 renderer.py 单源 + 决策群)

- 新增 `MessageRenderer.render_monitoring_sell(plan, *, anomaly_reason)` + `render_add_position(plan, *, add_rationale)`(仿 M-006 BUY 4 模板模式):异动原因 banner(z-score/EWMA/布林触发)/ 补仓四条件 banner + 既有 7-section dispatch body 复用。
- 仍**全经 renderer.py**(防 prompt injection,P0-2 §2.6 / CLAUDE.md §2.6)、走**决策群**(非告警群,P0-2-amendment-2026-05-16)、`instruction_id` 经 canonical 正则再校验(防伪造)。

### 2.5 安全核心不变(配 R0 §5)

- LLM 永不写决策字段;RiskEngine 纯函数 IO-free + 双层守门;config runtime 不可改;永禁真实下单 + 飞书人工执行;Line-2 SELL/ADD 仍经 RiskEngine 14-check + 飞书人工 gate。
- Line-2 触发式 LLM(N-004,仅作异动**说明/富化**,非决策)写**同一** `llm:usage:{utc_date}` 计数器 + 去重 + `max_anomaly_llm_per_day` 上限(P1-7-amendment §2.2);monitoring 模块**严禁** `import backend.{llm,agents,mirofish}`(触发门只经 `cost_guard` 预留 + Redis 计数,实际 LLM 调用由编排层在 monitoring 之外发起,保持 monitoring import-clean)。

## 3. 实施期任务映射(plan.html Phase N)

- **N-001** `backend/monitoring/anomaly.py`:纯量化 z-score/EWMA/布林 `AnomalyDetector`(读 K 快照;零 LLM)。
- **N-002** `backend/monitoring/sell_signal.py` + `instruction_plan_builder.assemble_monitoring_plan`(SELL)+ `renderer.render_monitoring_sell`:SELL 读 `available_volume`(T+1 已结算)。
- **N-003** `backend/monitoring/add_position.py` + builder ADD(BUY 方向)+ `renderer.render_add_position`:Van Tharp 固定分数 + ATR;四条件齐 + 禁马丁格尔 + 熊市禁补。
- **N-004** `backend/monitoring/degrade.py` + suspension 接入 + `cost_guard` 异动 LLM slot:停牌干净降级 + 触发式 LLM 去重+日上限+同一计数器。
- **N-005** `backend/monitoring/CLAUDE.md` + `redline-check.sh [N-005]` + 模块契约/隔离 AST 测试 + ★MVP gate 双线端到端(复用 J-005)。

## 4. 红线清单(本 amendment 之后)

1. §2.3「fund_manager 唯一 BUY/SELL/HOLD 倡议者 + 4 必经 agent + debate≥1」= **Line-1 LLM 选股路径**;Line-2 持仓监控为独立确定性零 LLM 路径(R0 §1/§8 治理)。
2. Line-2 SELL/ADD `InstructionPlan` **仍仅 builder 构造**(R0 §4 红线 1 + M-004 AST 守门不破);新增 `assemble_monitoring_plan` 确定性方法,monitoring 模块不构造 plan。
3. Line-2 `side/volume/limit_price` 确定性派生(异动/补仓 + sizing + available_volume),**永不来自 LLM**(R0 §4 红线 2/3/4;Line-2 无 LLM 决策输入)。
4. Line-2 仍跑 5 道早返 + RiskEngine 14-check + 飞书人工 gate;SELL 读 `available_volume`(T+1 已结算)。
5. Line-2 plan `debate_round_count=1`(确定性监控评估轮语义);schema `Field(ge=1)` 不改;`signal_id` 用 `LINE2-MON-` 前缀供 audit 区分。
6. Line-2 飞书消息全经 `renderer.py` + 决策群 + `instruction_id` canonical 再校验;monitoring 严禁 `import backend.{llm,agents,mirofish}`;触发式 LLM 写同一 `llm:usage` 计数器。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG 同步追加。`CLAUDE.md §2.3` 补充「§2.3 = Line-1 LLM 选股;Line-2 持仓监控为确定性零 LLM 路径,SELL/ADD 经 builder 新增 `assemble_monitoring_plan` 确定性构造(单一构造点不破)+ RiskEngine + 飞书人工」。
