# P0-10 修订(Line-2)— 2026-06-01 持仓 thesis 持久化 + 盘后复盘(advisory 先 / 确定性 quant-break 后)(方向②)

> **修订基准**: [P0-10 LLM 角色边界](./P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md) + [P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction](./P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md)
> **关联**: P0-10-amendment-line2-2026-05-30-take-profit-trim(止盈/减仓范式参照)/ P0-10-amendment-line2-2026-05-31-no-cross-day-take-profit-gate(P-006 脆弱反查教训)/ R0 §3(PIT)/ R0 §4(单一构造点)/ P0-8-amendment-2026-06-01(`THEME-`/evidence)/ P1-2.A(新 EOD cron)/ P1-7(成本)
> **修订日期**: 2026-06-01(规划 session #60)
> **决策人**: owner(AskUserQuestion 2026-06-01:方向② 答「advisory 先,quant-break 后」)
> **性质**: 决策边界锁定;本 session 不写代码,代码在 plan.html Phase W 实施。
> **方法**: 3 轮 codex 对抗(round-1/2 固化边界,round-2 §Q2 给出确定性 incumbent-weak 模板思路)。

## 0. 触发与意图

Owner 要 Line-2 能**盘后复盘**已持仓 + **盘中持续监控**,智能判:**忽略短期波动放长线** vs **短线及时止盈卖出**。判据分层:**长线看逻辑(原始买入 thesis 是否还成立),短线看量化指标(已确定性实现)**。

**核心张力**:「长线看逻辑」= thesis 评估,本质 **LLM/语义** → 与 **Line-2 零 LLM 红线**(P0-10-amendment-line2-2026-05-25)直接冲突。现状:Line-2 严格零 LLM、import 隔离;**对持仓「为何买」完全无知**(无 thesis 持久化);**无盘后复盘 runner**。

## 1. 决策范式:「LLM 当感知/证据,规则当 actuator」(调研背书 + codex)

调研(`docs/research/thesis-tracking-and-t1-rotation-2026-06-01.md`)背书:audit-oriented agentic-trading 推荐范式 = **确定性层做决策(actuator),LLM 做感知/证据(perception)**。owner 选**分阶段**:advisory 先,确定性 quant-break 后。

### 1.1 PositionThesis 持久化(缺失的原语;两段式分层)
买入(Line-1 dispatch)时落结构化 `PositionThesis` 记录:
- **支柱文本(LLM 写,advisory)**:3–5 条买入逻辑支柱(产业链/量化/辩论依据)= fund_manager `reasoning`/`proposal_text`(**P0-10 允许的文本字段**)。
- **确定性量化失效阈值(机检,无 LLM)**:每支柱配 1 个**白名单量化模板**派生的阈值(见 §1.3),买入时由**确定性**模块算出。
- **time-stop / 催化窗** + 原始 `evidence_ids` + 量化因子快照引用 + 辩论结论 + `SignalInputManifest` 引用 → **可 replay**。
- **存储**:新 collection(或扩展现有读模型),**非 `InstructionPlan`**(R0 §4 / M-004 单一构造点不破);由 `instruction_id`/`correlation_id` 关联。
- **关键(避 P-006 教训)**:PositionThesis 是**买入时显式落库的记录**,**不是**事后从 `broker_events` 反查重建(P0-10-amendment-line2-2026-05-31 因 broker_events 缺 evidence_ids、反查过脆而否决 P-006)。显式记录根除脆弱性。
- ⚠️ thesis 文本来自 LLM(合规),但**永不**反向写任何决策/数值字段(side/volume/limit_price/RiskCheckSummary)。

### 1.2 阶段 1(先做)— LLM advisory 盘后复盘
- 新 **EOD Line-2 复盘 cron**(~17:30,MiroFish postclose 之后;派生 P1-2.A BrokerScheduler 新 job)。
- LLM 比对当前新闻/证据 vs 原 thesis 支柱 → 输出「thesis intact / weakening / broken」+ 理由**文本**。
- **仅 evidence/advisory**:写 `evidence_collection.content`(用 P0-8-amendment-2026-06-01 解锁的 `THEME-` 前缀,或本 amendment 同口径申请的复盘前缀;by-construction 无 RiskCheckSummary 管线)+ **display-only 飞书 digest**(仿 `render_basket_digest`,无 `QM-` / 无成交动词 / 对抗 `parse_execution_report` 必 `no_pattern_match`)。
- **LLM 调用在 `backend/monitoring` 之外**(orchestration 层发起,沿用 N-004 触发式 LLM 范式),计 `llm:usage:{utc_date}`、cost-gate;`backend/monitoring` **保持 0-LLM + import 隔离**。
- **owner 据 advisory 人工执行**(走现有飞书人工 gate);**确定性 SELL 路径完全不变**。advisory 永不碰 side/volume/limit_price/RiskCheckSummary。

### 1.3 阶段 2(后做,门控)— 确定性 THESIS_QUANT_BREAK 触发
- 新增 `IntradayTriggerKind.THESIS_QUANT_BREAK`(或 daily 触发),**确定性派生**,经现有单一构造点 `assemble_monitoring_plan` → 14-check → 飞书人工。
- **阈值只来自预批准的白名单确定性量化模板**(NOT LLM 文本选指标/比较符/阈值 —— codex round-1 警告:LLM 选阈值 = 把语义偷渡进零 LLM SELL 路径)。模板示例(实施期细化、人工批准 + pin):相对板块强度跌破 −X%、因子 rank 衰减 > Y 分位、自买入 thesis 锚的回撤、相对超额转负、信号半衰期到期。
- **只能【增】卖压,永不【放松/压制】现有 Line-2 止损/止盈/减仓**(codex:「thesis intact 放宽止盈带」被否决 —— 让 thesis 健康度降低卖压不可接受)。保护性 ATR/回撤止损优先级永不被调制。
- thesis-quant 健康状态**确定性计算**(纯量化 over PIT + 模板阈值,`backend/monitoring` 内,零 LLM)。
- `signal_id` 保 `LINE2-MON-`;`evidence_id = MARKET-{code}-thesis_quant_break`;去重键沿用 `(code, trigger_kind)`;SELL 不熔断(P0-7)。

## 2. 落地(plan.html Phase W;实施前本 amendment 是门)

- `backend/services/`(或新模块)PositionThesis 持久化 + 买入时确定性阈值计算 + replay 引用。
- 新 EOD 复盘 cron(P1-2.A 派生 amendment 候选)+ orchestration 层 LLM advisory 调用(monitoring 外)+ display-only 飞书 renderer + 计预算。
- 阶段 2:`backend/monitoring/` 新 `THESIS_QUANT_BREAK` 确定性触发(白名单模板,add-only)+ `assemble_monitoring_plan` 接入 + 对抗测试(断言 thesis 文本/LLM 永不入数值字段;断言只增卖压不放松现有止损;断言 monitoring 仍 0-LLM + import-clean)。
- **ship 序**:阶段 1(advisory + 持久化)先;阶段 2(quant-break)后且经 shadow 验证。

## 3. 不变量(本 amendment 不触碰)

- `backend/monitoring/` + line2 runners **保持零 LLM + import 隔离**(禁 `backend.{llm,agents,agents_team,mirofish}`);LLM advisory 调用在 monitoring 之外。
- Line-2 SELL/ADD `side/volume/limit_price` 确定性派生(异动/补仓/止盈/减仓 + sizing + settled `available_volume`),**永不来自 LLM**;单一构造点 M-004 不破。
- 现有触发优先级 ATR > 回撤 > 止盈 > 减仓 + `(code,trigger_kind)` 去重 + SELL 不熔断 + T+1 settled 不变;**保护性止损永不被任何 thesis 状态压制**。
- 每条飞书消息必经 renderer + 决策群 + canonical 再校验;display-only digest 不可解析(P0-3/P0-4 范式)。
- PositionThesis 显式落库,**非** broker_events 反查(根除 P-006 脆弱性)。
