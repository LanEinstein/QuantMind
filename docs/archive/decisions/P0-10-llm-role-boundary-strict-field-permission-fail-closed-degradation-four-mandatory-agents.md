# P0-10 — LLM 角色边界(极严字段权限矩阵 + 严格 fail-closed 降级矩阵 + 四必经 Agent + fund_manager 终局守门 + agent_models.yaml baseline 锁定)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-10 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §5(多 Agent 与 LLM 决策现状)+ §10(LLM Router 与 Agent 编排)+ §13(推荐路线图)|
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-10(LLM 角色边界)+ §P1-8(Kimi thinking 使用策略 — 由本决策前置部分锁定 thinking 启用矩阵)|
| 依赖决策   | 累积 P0-1 ~ P0-9 全部 LLM negative list:`docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.6 多 Agent 辩论 + §2 红线 8 RiskEngine 不允许 import LLM/agents/mirofish)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.5 evidence_ids 与 risk_summary by-value/by-reference 折中 + §2 红线 8 飞书指令文本必须 renderer.py 函数生成)+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(§2 红线 2 LLM 严禁参与回报路径)+ `docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md`(§2 红线 6 LLM 严禁参与对账路径)+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(§2 红线 3 LLM 严禁参与验收路径 + §1.7 P0 系统级中断 5 类定义之"LLM 全停 ≥ 1h")+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(§1.4 RiskConfig 全锁 + LLM 永不持有写引用 + §1.5 RiskEngine 14-check 完整性 + §2 红线 11 LLM 严禁产出 RiskCheckSummary 结果)+ `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md`(§2 红线 7 LLM 严禁参与数据质量判定 + §1.4 MiroFish 输出仅入 evidence_collection 不入 RiskCheckSummary)+ `docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md`(§3.5 MiroFish 是加分项不是核心 + event 路径硬 cap=1 严禁占用主路径 + §1.3 watchlist runtime 不可改 + §2 红线 7 watchlist 排除规则不进 RiskEngine)|
| 派生 amendment | `docs/decisions/P0-3-amendment-2026-05-09-instruction-plan-llm-writable-fields.md`(实施期产出;在 P0-3 InstructionPlan schema 上明确 `reasoning: str | None`(LLM 可写人读思路)+ `agent_debate_record_id: str`(by-reference 关联 `agent_debate_records` collection;LLM 可写 collection 内部 reasoning_text 不可写顶层 ID)等字段标记;`risk_summary` 字段类型同 P0-3 amendment 已锁固化由 RiskEngine 产出 不可被 LLM 覆写) |
| 替代       | `config/agent_models.yaml` 当前 196 行配置作为 P0-10 baseline 整体锁定(runtime 不可改;调整必走 `P0-10-amendment-{date}-routing-change.md`)|

## 决策摘要

QuantMind 第一阶段 LLM 角色边界采用 **极严字段权限矩阵 + 严格 fail-closed 降级矩阵 + 四必经 Agent + fund_manager 终局守门 + agent_models.yaml baseline 锁定 + 调整必走 amendment** 架构:

1. **极严字段权限矩阵**(positive list 仅 4 类,negative list 累积 P0-1~P0-9 + P0-10 新锁):
   - **LLM 可写**:`InstructionPlan.reasoning`(人读思路 / 自由文本)+ `evidence_collection.content`(每条 evidence 的访问体 raw text)+ `agent_debate_records.{reasoning_text, conclusion}`(辩论记录字段)+ `risk_parameter_proposals.proposal_text`(只读 ledger;P0-7 §1.4 已锁,LLM 写入但人工 review 才生效)
   - **LLM 严禁写** (累积 8 类):`InstructionPlan.{volume, limit_price, valid_until, status, risk_summary, evidence_ids[ID 名称], debate_round_count, instruction_id, code, side}` / `RiskCheckSummary` 任一字段 / `evidence_id` 命名 / `cap_allocator.{path, traditional_consumed, event_consumed}` / MockBroker 任一字段 / WatchlistPolicy 任一字段 / `DataQualityState` 任一字段 / `ReconciliationTicket` 任一字段 / `AcceptanceReport` 任一字段
   - **双层守门**:Pydantic schema 强校(`model_config = ConfigDict(frozen=True)` + LLM 输出经 schema validate)+ lint rule 实施期阻止 `backend/agents/` / `backend/llm/` 子树对 forbidden 字段的赋值表达式

2. **严格 fail-closed 降级矩阵**(单调用 / 单 Agent / 全停 / schema / 成本 五个维度):
   - **单调用超时**:`llm_single_call_timeout_seconds=30`(主调用 30s)+ fallback 30s + 不重试;两轮均超 → Agent 返回 None
   - **单必经 Agent 失败**(包括 None / schema 校验失败):InstructionPlanBuilder 在装配早返,降级 HOLD,**不产生 BUY/SELL InstructionPlan**;ledger 留痕 `agent_failure: {agent_name, reason}`
   - **LLM 路由全停 ≥ 1h**(P0-6 §1.7 P0 系统级中断 5 类之一):`acceptance.window_reset()` 重置 45 交易日窗口 + simulation_auto 进入 `paused-no-llm` 状态(行情/资讯收集仍运行,**不发** BUY/SELL 指令,涨跌停/重大事件快照仅写 ledger)
   - **schema 校验失败**:LLM 输出经 Pydantic strict mode 校验失败 → 降级 HOLD,失败原因写 `agent_debate_records.parse_error`,不重试
   - **成本超日预算**:`QUANTMIND_DAILY_BUDGET=20`(默认 ¥20,沿用 cost_guard)硬阈值触发 → 暂停所有 LLM 调用,simulation_auto 进入 `paused-budget-breach` 状态;次日 00:00 自动解除;soft 阈值(70% = ¥14)仅告警不暂停

3. **多 Agent 辩论 — 四必经 + fund_manager 终局守门**:
   - **必经 4 个 Agent**(任一缺席即降级 HOLD):
     - `fundamental_analyst`(qwen 3.6 plus)— 财报 / 估值 / 行业景气
     - `technical_analyst`(qwen 3.6 plus)— K 线 / MACD / RSI / 趋势
     - `risk_officer`(kimi k2.6 + thinking)— 投组风险 / 仓位建议 / 否决权(LLM 视角的风险评估,与 RiskEngine 14-check 不替代)
     - `fund_manager`(kimi k2.6 + thinking + tiered routing)— **唯一 BUY/SELL/HOLD 倡议者**;输出 JSON 包含 `confidence ∈ [0,1]` + `signal ∈ {buy, sell, hold}` + `reasoning`
   - **可跳过 5 个 Agent**(无则降级运行,不产生 InstructionPlan 字段缺失):
     - `news_crawler`(deepseek)/ `sentiment_analyst`(deepseek)/ `data_cleaner`(deepseek)— 高频低成本数据准备
     - `intelligence_officer`(kimi + MiroFish)— P0-9 §3.5 已锁 MiroFish 是加分项 / event 路径独立 cap;intelligence_officer 缺席不阻断主路径
     - `bull_researcher`(kimi)+ `bear_researcher`(kimi)— 看多/看空辩论,缺一可继续
   - **fund_manager 终局**:输出 BUY/SELL/HOLD 倡议 + reasoning;**输出后必须经 InstructionPlanBuilder 五道早返(数据质量 / 切换冻结 / ticket 冻结 / 熔断冻结 / watchlist 排除)+ RiskEngine 14-check 双层守门**;LLM 不直接产 InstructionPlan,只产倡议
   - **debate_round_count ≥ 1**(P0-3 §2 红线 7 已锁;此处复述边界);`max_debate_rounds`(slow=2 / fast=1)P0-9 §3 已锁

4. **agent_models.yaml baseline 锁定 + 调整必走 amendment**:
   - 锁 9-Agent 现有路由(包含 fund_manager_shadow_baseline 共 10 entry)+ provider 三家(deepseek / qwen / kimi)+ per-Agent thinking 配置 + fund_manager tiered routing(triage qwen + escalation kimi at confidence_lt 0.6)
   - `config/agent_models.yaml` runtime 不可改(继承 P0-7 §1.4 RiskConfig 全锁精神)
   - `backend/api/llm*.py` / `backend/api/agents*.py` 仅允许 `GET` 端点(返回路由配置只读视图);严禁 `POST/PUT/PATCH/DELETE` 端点
   - 调整任一字段必须先走 `P0-10-amendment-{date}-routing-change.md` + `git diff config/agent_models.yaml` + 进程重启
   - 新增 provider(claude / gpt-4o / 其他)必须先走 amendment;config 已注释保留 `claude` / `openai` 两个候选 provider 槽位

## 决策具体内容

### 1. LLM 字段权限矩阵(极严)

#### 1.1 LLM positive list(可写字段 — 仅 4 类)

| 字段路径 | 数据载体 | LLM 写入方式 | 守门机制 |
|----------|----------|-------------|----------|
| `InstructionPlan.reasoning: str \| None` | `instruction_plans` collection | LLM 在多 Agent 辩论结束后由 fund_manager 写入人读思路 | Pydantic strict mode + max length 5000 字符 |
| `evidence_collection.content: str` | `evidence_collection` collection 每条 evidence 的访问体 | LLM 抽取/解释多源资讯产出 evidence body | Pydantic strict mode + max length 10000 字符 |
| `agent_debate_records.{reasoning_text, conclusion}: str` | `agent_debate_records` collection 每个 Agent 单轮发言 | 每个 Agent(含必经 4 + 可跳过 5)在自己的发言字段写入 | Pydantic strict mode + max length 8000 字符;`agent_name` 字段由 graph 自动注入,LLM 不可写 |
| `risk_parameter_proposals.proposal_text: str` | `risk_parameter_proposals` collection(P0-7 §1.4 锁定的只读 ledger) | LLM 自进化提议时写入提议理由(注:`accepted` 状态由人工 review 而非 LLM 决定) | Pydantic strict mode + max length 5000 字符;`accepted: bool` 字段 LLM 严禁写 |

#### 1.2 LLM negative list(严禁写字段 — 累积 8 类)

继承 P0-1~P0-9 已锁 + P0-10 新锁,共形成完整 negative list:

**类别 1 — InstructionPlan 决策字段**(继承 P0-1 §2 红线 7 + P0-3 §2 红线 4-5):

```
InstructionPlan.volume         # 股数,P0-7 RiskEngine + InstructionPlanBuilder 决策
InstructionPlan.limit_price    # 限价,确定性代码决策(技术指标 + 涨跌停)
InstructionPlan.valid_until    # 有效期,固定 14:55 当日
InstructionPlan.status         # 状态机 DRAFT→VALIDATED→DISPATCHED→...
InstructionPlan.risk_summary   # RiskEngine 14-check 产出(P0-7 §2 红线 11)
InstructionPlan.evidence_ids   # 名称由 evidence_collection 主键确定性产出(LLM 可写 content,不可写 ID)
InstructionPlan.debate_round_count  # graph orchestration 自动计数
InstructionPlan.instruction_id # 严格正则 QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq}
InstructionPlan.code           # watchlist 内 code,fund_manager 输出 signal 时 graph 自动绑定
InstructionPlan.side           # BUY/SELL/HOLD,fund_manager 输出 signal 字段后 graph 转译
```

**类别 2 — RiskCheckSummary**(继承 P0-7 §2 红线 11):

```
RiskCheckSummary[i].check_id         # check 1-14 名称,代码硬编码
RiskCheckSummary[i].passed           # check 通过/失败,代码逻辑判断
RiskCheckSummary[i].message          # 人读消息,代码模板生成(P0-10 中等严格选项已被拒;LLM 不可写)
RiskCheckSummary[i].evidence_ids     # 关联 evidence 名称,代码自动绑定
```

**类别 3 — evidence_id 命名**(继承 P0-8 §2 红线 14 五前缀约定):

```
evidence_id 字符串本身  # 形如 NEWS-{date}-{seq}/MIROFISH-{date}-{seq}/MARKET-{date}-{seq}/RISK-{date}-{seq}/DEBATE-{date}-{seq}
                       # LLM 可写 evidence content,不可命名 ID
```

**类别 4 — cap_allocator**(继承 P0-9 §2 红线 10):

```
cap_allocator.path              # Literal["traditional", "event"] 由 graph 在调用 InstructionPlanBuilder 时显式标记
cap_allocator.traditional_consumed  # 计数器,代码原子 inc
cap_allocator.event_consumed        # 计数器,代码原子 inc
cap_allocator.total_daily_cap       # P0-7 锁定 = 5
```

**类别 5 — MockBroker 持仓与现金**(继承 P0-1 §2 红线 3 / P0-5 §2 红线 12):

```
MockBroker._cash                # 现金余额
MockBroker._positions           # 持仓字典
MockBroker._trades              # 成交流水
MockBroker.short_position       # 永远空字典(P0-9 §2 红线 15)
```

**类别 6 — WatchlistPolicy / 衍生 schema**(继承 P0-9 §2 红线 17):

```
WatchlistPolicy 任一字段
ExclusionRules 任一字段
CapAllocation 任一字段
DirectionPolicy 任一字段
```

**类别 7 — DataQualityState / ReconciliationTicket / AcceptanceReport**(继承 P0-4/5/6/8):

```
DataQualityState 任一字段
ReconciliationTicket 任一字段
AcceptanceReport 任一字段
```

**类别 8 — RiskConfig / 衍生 schema**(继承 P0-7 §2 红线 1):

```
RiskConfig 任一字段
PositionLimitsConfig 任一字段
CircuitBreakerConfig 任一字段
UniverseConfig 任一字段
```

#### 1.3 双层守门机制

**第一层 — Pydantic schema 强校**:

```python
# backend/data/instruction_plan.py(P0-3 已锁 schema 在实施期产出)
class InstructionPlan(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    instruction_id: str = Field(pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$")
    code: str = Field(pattern=r"^[0-9]{6}$")
    side: InstructionSide
    volume: int | None = Field(ge=100, multiple_of=100)
    limit_price: Decimal | None = Field(gt=0)
    valid_until: datetime
    status: InstructionStatus
    risk_summary: tuple[RiskCheckSummary, ...] = Field(min_length=14, max_length=14)
    evidence_ids: tuple[str, ...]
    debate_round_count: int = Field(ge=1)
    reasoning: str | None = Field(default=None, max_length=5000)  # ← LLM-writable
    # ... 其他字段
```

LLM 只能通过返回 JSON envelope 让代码层 build InstructionPlan;**严禁** 直接 mutation;违规即 ValidationError。

**第二层 — lint rule(实施期产出)**:

```python
# scripts/lint_p0_10_llm_field_permission.py
FORBIDDEN_FIELDS = {
    "InstructionPlan.volume", "InstructionPlan.limit_price", "InstructionPlan.valid_until",
    "InstructionPlan.status", "InstructionPlan.risk_summary", "InstructionPlan.evidence_ids",
    "InstructionPlan.debate_round_count", "InstructionPlan.instruction_id",
    "RiskCheckSummary", "MockBroker", "WatchlistPolicy", "RiskConfig",
    "DataQualityState", "ReconciliationTicket", "AcceptanceReport",
    "cap_allocator.path", "cap_allocator.traditional_consumed", "cap_allocator.event_consumed",
}

def lint_llm_subtree(file_paths: list[Path]) -> list[Violation]:
    """Scan backend/agents/ + backend/llm/ for forbidden field assignments.

    Rejects: 1) any 'X.field = ...' where X.field in FORBIDDEN_FIELDS
             2) any prompt template containing 'output volume' / 'output limit_price' etc
             3) any LLM JSON envelope schema declaring forbidden fields as required
    """
    ...
```

CI 必须执行此 lint;违规即 hard fail。

### 2. 超时 / 失败 / 不可用降级矩阵(严格 fail-closed)

#### 2.1 单调用层(LLMRouter)

`backend/llm/router.py::LLMRouter.complete` 调用语义在 P0-10 实施期严格化:

| 维度 | 阈值 | 失败动作 | 实施位置 |
|------|------|----------|----------|
| 单 provider 调用 | `llm_single_call_timeout_seconds=30` | 抛 `LLMTimeoutError` | `asyncio.wait_for` 包 `client.chat.completions.create` |
| fallback 调用 | 30s(同上) | 抛 `LLMFallbackTimeoutError` | router 已有 fallback 逻辑 + 加 `wait_for` |
| 重试次数 | **0** | 不重试 | LLM 失败即降级,不浪费成本 |
| 总调用时长上限 | 60s(主 30 + fallback 30) | 必返(成功 / 失败) | `wait_for` 累计 |

实施期改动:`router._call_provider` 包 `asyncio.wait_for(client.chat.completions.create(...), timeout=30)`。

#### 2.2 单 Agent 层(必经 vs 可跳过)

| Agent 类型 | 失败时动作 | 守门点 |
|------------|-----------|--------|
| 必经 Agent(`fundamental_analyst` / `technical_analyst` / `risk_officer` / `fund_manager`)失败 | InstructionPlanBuilder 早返,降级 HOLD,**不产生 BUY/SELL InstructionPlan** | `backend/agents/graph.py::run_analysis` 在装配 fund_manager input 时检查必经 Agent 输出 |
| 可跳过 Agent(`news_crawler` / `sentiment_analyst` / `data_cleaner` / `intelligence_officer` / `bull_researcher` / `bear_researcher`)失败 | 该 Agent 输出留空,继续运行;`agent_debate_records` 内 `parse_error` 字段记录;不阻断 | 同上 |
| schema 校验失败(LLM 返回但 JSON envelope 不通过 Pydantic strict) | 视为 Agent 失败;按必经/可跳过分别处理 | `backend/agents/base.py::extract_json_from_response` 升级到 strict validate |
| 必经 Agent 部分轮次失败(slow_pipeline 2 轮中第 2 轮失败) | 取最后一轮成功结果继续;若全部轮次均失败,Agent 视为整体失败 | `should_continue_debate` 逻辑 |

ledger 留痕(P0-1 §1.5 decision_ledger):`agent_failure: {agent_name, reason, retry_count: 0, fallback_attempted: bool, timestamp}`。

#### 2.3 全停层(P0 系统级中断)

继承 P0-6 §1.7 P0 系统级中断 5 类之一(LLM 全停 ≥ 1h):

**触发条件**:DeepSeek + Qwen + Kimi 三家 provider 在最近 1h 滚动窗口内 health check 失败率 100%(每 5 min 自动 health probe;`backend/llm/connection_tester.py` 已存在)。

**自动动作**:
1. `acceptance_service.reset_window_on_p0_interrupt(reason="llm_all_down_1h")`(P0-6 §1.7 已锁)
2. simulation_auto 进入 `paused-no-llm` 状态:
   - 行情 / 资讯 / MockBroker 持仓 mark-to-market 持续运行
   - **不调用** LLM Router(即使有事件触发)
   - **不发** 飞书指令(包括澄清/对账卡)
   - 涨跌停 / 重大事件 / MiroFish severity≥CRITICAL 仅写 `evidence_collection` 留痕,不路由 InstructionPlan
3. 任一 provider 恢复(health probe 1 轮成功)→ 自动解除 `paused-no-llm`;但 45 交易日窗口已重置
4. ledger 写 `p0_interrupt_records: {category: "llm_all_down_1h", started_at, recovered_at, duration_minutes}`

#### 2.4 schema 校验失败层

LLM 输出经 Pydantic strict mode 校验失败 → 视为 Agent 失败(同 §2.2);**不重试**;`agent_debate_records.parse_error: str` 写入失败原因 + 原始 LLM 输出前 500 字符;按必经/可跳过 Agent 分别处理。

#### 2.5 成本超预算层

| 阈值 | 状态 | 自动动作 |
|------|------|----------|
| `cost_guard.soft_ceiling=¥14`(70% × 默认 ¥20)| WARNING | 仅告警(主通道告警 + ledger 留痕);不暂停 |
| `cost_guard.hard_ceiling=¥20`(`QUANTMIND_DAILY_BUDGET` 默认值)| HARD STOP | 暂停所有 LLM 调用;simulation_auto 进入 `paused-budget-breach` 状态(行情 / 资讯仍运行;不调用 LLM;不发指令);次日 00:00 自动解除 |
| 用户调高 `QUANTMIND_DAILY_BUDGET` env var | 立即生效 | cost_guard 读 env;但若已 paused,需手动 `POST /api/cost-guard/resume`(由前端按钮触发)|

ledger 留痕:`budget_breach_records: {breach_at, daily_total, daily_budget, status: "auto-paused"|"manually-resumed"}`。

### 3. 多 Agent 辩论必经约束(四必经 + fund_manager 终局)

#### 3.1 9-Agent 角色分工与必经/可跳过分类

| Agent | provider | thinking | 必经/可跳过 | 失败时降级动作 |
|-------|----------|----------|-------------|----------------|
| `news_crawler` | deepseek-v4-pro | disabled | **可跳过** | news 输入留空,辩论继续 |
| `sentiment_analyst` | deepseek-v4-pro | disabled | **可跳过** | sentiment 输入留空,辩论继续 |
| `data_cleaner` | deepseek-v4-pro | disabled | **可跳过** | 直接用未清洗数据,辩论继续 |
| `fundamental_analyst` | qwen3.6-plus | disabled | ✅ **必经** | InstructionPlanBuilder 早返,降级 HOLD |
| `technical_analyst` | qwen3.6-plus | disabled | ✅ **必经** | 同上 |
| `intelligence_officer` | kimi-k2.6 | enabled(10000 tokens) | **可跳过** | MiroFish event 路径独立(P0-9 §3.5);intelligence_officer 缺席不阻断 traditional 主路径 |
| `bull_researcher` | kimi-k2.6 | enabled(8000 tokens) | **可跳过** | 单边视角缺一可继续(bear 仍在) |
| `bear_researcher` | kimi-k2.6 | enabled(8000 tokens) | **可跳过** | 同上(bull 仍在) |
| `risk_officer` | kimi-k2.6 | enabled(6000 tokens) | ✅ **必经** | LLM 视角风险评估缺席 → InstructionPlanBuilder 降级 HOLD(注意:这与 RiskEngine 14-check 双层防护不重复 — risk_officer 是 LLM 投组视角,RiskEngine 是确定性硬限制)|
| `fund_manager` | kimi-k2.6 + tiered routing | enabled(8000 tokens) | ✅ **必经** | **唯一 BUY/SELL/HOLD 倡议者**;失败 → 降级 HOLD |
| `fund_manager_shadow_baseline` | kimi-k2.6 | enabled | shadow only | Phase 5B 出口 shadow 测试用;**不参与决策**;P0-10 实施期保留作为路由变更回归基线 |

#### 3.2 fund_manager 终局守门 + 双层防护

**fund_manager 输出契约**(JSON envelope,严格 Pydantic 校验):

```python
class FundManagerOutput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    signal: Literal["buy", "sell", "hold"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=5000)
    recommended_volume_hint: int | None = Field(default=None, ge=100, multiple_of=100)
    # ↑ 注意:hint 是建议,不是决策;InstructionPlanBuilder 自行计算 volume,可忽略 hint
```

**守门顺序**(fund_manager 输出后):

1. **InstructionPlanBuilder 五道早返**(继承 P0-1 / P0-5 / P0-7 / P0-8 / P0-9):
   - 数据质量早返(P0-8 §1.5)
   - 切换冻结早返(P0-1 §1.4)
   - ticket 冻结早返(P0-5 §1.6)
   - 熔断冻结早返(P0-7 §1.7)
   - watchlist 排除早返(P0-9 §2.2)
2. **RiskEngine 14-check**(继承 P0-7 §1.5):
   - check 1-14 全部通过 → InstructionPlan.status=VALIDATED → DISPATCHED → 路由
   - 任一失败 → InstructionPlan.status=REJECTED → 不路由

**关键定位**:fund_manager **倡议** BUY/SELL,**不决定** 最终是否发出指令。即使 fund_manager 输出 buy + confidence=0.99 + reasoning 极强,只要 RiskEngine 任一 check 失败,InstructionPlan 必 REJECTED。

#### 3.3 risk_officer 与 RiskEngine 双层防护(LLM 视角 vs 确定性硬限制)

**risk_officer Agent**(LLM 视角):
- 投组级风险评估(集中度 / 波动率 / 相关性)
- 仓位建议(LLM 提议但不决定)
- 可对单个 fund_manager 倡议提"否决建议"(写入 `agent_debate_records`,不直接拦截 InstructionPlan)

**RiskEngine 14-check**(确定性硬限制):
- check 1-14 由 P0-7 §1.5 锁定的代码逻辑判定
- LLM **不可** 覆盖 / 绕过 / 替代任一 check
- 即使 risk_officer 说"通过",RiskEngine check 失败 → REJECTED

**两者独立并行**:risk_officer 失败 → 必经 Agent 缺失 → 降级 HOLD(不到 RiskEngine);risk_officer 通过 + RiskEngine check 失败 → REJECTED(到 RiskEngine 但被拒)。

#### 3.4 debate_round_count 与 max_debate_rounds

继承 P0-1 §1.6 + P0-3 §2 红线 7 + P0-9 §3.1:

- `debate_round_count ≥ 1`(任何 InstructionPlan 至少 1 轮辩论)
- `slow_pipeline.max_debate_rounds=2`(slow 09:00 跑 2 轮 — 充分辩论)
- `fast_pipeline.max_debate_rounds=1`(fast 盘中跑 1 轮 — 快速验证)
- 第 1 轮:bull_researcher + bear_researcher(可跳过任一)各自陈述 + fund_manager 初步倡议
- 第 2 轮(slow only):基于第 1 轮 fund_manager 倡议 + risk_officer 否决建议,fund_manager 重新倡议

#### 3.5 fund_manager tiered routing 维持锁定

继承 `config/agent_models.yaml::fund_manager.routing`(已存在):

```yaml
fund_manager:
  provider: kimi
  model: kimi-k2.6
  routing:
    triage_provider: qwen
    triage_model: qwen3.6-plus
    escalation_provider: kimi
    escalation_model: kimi-k2.6
    escalation_condition:
      confidence_lt: 0.6
```

- triage 阶段:qwen3.6-plus 跑一次,输出 confidence
- 若 `confidence ≥ 0.6`:接受 triage 输出,不升级到 kimi(节省成本)
- 若 `confidence < 0.6`:升级到 kimi-k2.6 + thinking,重跑一次
- triage 失败 → 直接走 kimi(fallback);kimi 也失败 → fund_manager 失败 → 降级 HOLD

P0-10 锁定此 tiered routing 配置;调整 `confidence_lt` 阈值或 triage_provider 必须先走 amendment。

### 4. agent_models.yaml baseline 锁定

#### 4.1 锁定范围(整个 196 行配置)

P0-10 锁定 `config/agent_models.yaml` 当前结构:

| 章节 | 锁定项 | runtime 可改? |
|------|--------|---------------|
| `providers.{deepseek,qwen,kimi}` | base_url + api_key + default_model | ❌ 不可改 |
| `defaults.{temperature,max_tokens}` | temperature=0.3 / max_tokens=4096 | ❌ 不可改 |
| `agents.<each>.provider` | 各 Agent 路由的 provider | ❌ 不可改 |
| `agents.<each>.model` | 模型名称 | ❌ 不可改 |
| `agents.<each>.fallback` | fallback provider + model | ❌ 不可改 |
| `agents.<each>.thinking` | thinking 启用矩阵 + max_tokens + keep | ❌ 不可改 |
| `agents.<each>.frequency` | 调用频次描述(annotation 不影响行为) | ❌ 不可改(语义层面) |
| `agents.fund_manager.routing` | tiered routing(triage_provider / escalation_condition) | ❌ 不可改 |

**变更流程**(任一字段调整):

1. 写 `docs/decisions/P0-10-amendment-{date}-{原因}.md` 说明:
   - 改了什么字段(精确到 YAML key path)
   - 为何改(如 deepseek-v4-pro 弃用、qwen3.6-plus 涨价、新模型评测优势)
   - 影响哪些 Agent / 哪些场景
   - 回滚条件 / 测试计划
2. `git diff config/agent_models.yaml` 对照 amendment 文档
3. 进程重启(LLMRouter 已支持 hot-reload 但 P0-10 锁定为禁用 hot-reload — 加 `enable_hot_reload: false` 字段)
4. 同步 P0-10 决策文档顶部加 `> 已被 amendment-XXX 修订` 提示

#### 4.2 hot-reload 禁用

`backend/llm/router.py::LLMRouter._maybe_reload_config` 在 P0-10 实施期改为根据 `enable_hot_reload` 字段决定:

```python
# 实施期改动
async def _maybe_reload_config(self) -> None:
    if not self._config.enable_hot_reload:
        return  # P0-10 锁定:禁用 hot-reload,只能进程重启生效
    # ... 原有 hot-reload 逻辑
```

`config/agent_models.yaml` 顶部加:

```yaml
# P0-10 锁定:hot-reload 禁用,任何路由变更必须 amendment + 重启
enable_hot_reload: false
```

#### 4.3 backend/api 层只允许 GET

继承 P0-7 §1.4 / P0-9 §1.3 RiskConfig / WatchlistPolicy 全锁精神:

```python
# backend/api/llm.py(实施期,如不存在则新建)
@router.get("/api/llm/router-config")
async def get_router_config() -> RouterConfigSafeView:
    """Return the current LLMRouter config (read-only view, secrets redacted)."""
    ...

# 严禁 POST/PUT/PATCH/DELETE
# 严禁 backend/api/agents*.py 下的 mutation 端点
```

实施期 lint rule:

```bash
grep -rn "@router.post\|@router.put\|@router.patch\|@router.delete" backend/api/llm*.py backend/api/agents*.py
# 必须返回 0 行
```

### 5. 完整失败动作矩阵(P0-10 §2 与 P0 系统衔接的全景表)

| 失败维度 | 触发条件 | 即时动作 | 累积动作 |
|----------|----------|----------|----------|
| 单调用超时 30s | `asyncio.wait_for` 抛 TimeoutError | 抛 `LLMTimeoutError`,fallback 30s | 累计 5 次/h → 路由全停判定接近 |
| fallback 也超时 | fallback 调用 30s 后超时 | 抛 `LLMFallbackTimeoutError`,Agent 返回 None | 同上 |
| schema 校验失败 | Pydantic strict 抛 ValidationError | Agent 返回 None,记 parse_error | 累积 ≥ 50% / 1h → 评估 prompt 漂移 |
| 必经 Agent 失败 | 4 必经任一 Agent 返回 None | InstructionPlanBuilder 降级 HOLD;ledger 留痕 | 影响 P0-6 §1.2.1 指令完整率 95% 硬门槛 |
| 可跳过 Agent 失败 | 5 可跳过任一 Agent 返回 None | 留空字段继续辩论 | 不影响指令完整率 |
| LLM 全停 ≥ 1h | 三家 provider 1h 内 health check 100% 失败 | simulation_auto → paused-no-llm + 重置 45 交易日窗口(P0-6) | P0 系统级中断之一 |
| 成本超 soft 70%(¥14)| `cost_guard.soft_ceiling` 命中 | 主通道告警 + ledger 留痕 | 不暂停 |
| 成本超 hard 100%(¥20)| `cost_guard.hard_ceiling` 命中 | simulation_auto → paused-budget-breach | 次日 00:00 自动解除 |
| 用户手动 resume budget | `POST /api/cost-guard/resume` | 解除 paused-budget-breach | 不影响 45 交易日窗口 |
| LLM 输出包含 forbidden 字段 | lint rule + Pydantic extra="forbid" 双层捕捉 | 抛 ValidationError + ledger 写 attack_attempt | 累积 ≥ 5 次/天 → 主通道告警(可能 prompt injection)|

### 6. 与 P0-1 ~ P0-9 累积红线的整合

P0-10 不重复定义 P0-1 ~ P0-9 已锁的"LLM 严禁 X"红线,而是把它们整合到 §1.2 negative list 类别 1-8 中。整合表:

| P0 决策 | 红线 | P0-10 整合到 |
|---------|------|--------------|
| P0-1 §2 红线 8 | RiskEngine 不允许 import LLM/agents/mirofish | §1.2 类别 8(RiskConfig 写引用)|
| P0-3 §2 红线 4-5 | volume / limit_price / valid_until 必须确定性代码 | §1.2 类别 1(InstructionPlan 决策字段)|
| P0-3 §2 红线 8 | 飞书指令文本必须 renderer.py | §1.1(LLM 可写 reasoning;不可拼飞书文本)|
| P0-4 §2 红线 2 | LLM 严禁参与回报路径 | §1.2 类别 7(ReconciliationTicket / ExecutionReport 不可写)|
| P0-5 §2 红线 6 | LLM 严禁参与对账路径 | §1.2 类别 7(ReconciliationTicket 不可写)|
| P0-6 §2 红线 3 | LLM 严禁参与验收路径 | §1.2 类别 7(AcceptanceReport 不可写)|
| P0-7 §2 红线 11 | LLM 严禁产出 RiskCheckSummary 结果 | §1.2 类别 2(RiskCheckSummary 任一字段)|
| P0-7 §1.4 | RiskConfig 全锁 + LLM 永不持有写引用 | §1.2 类别 8(RiskConfig 任一字段)|
| P0-8 §2 红线 7 | LLM 严禁参与数据质量判定 | §1.2 类别 7(DataQualityState 任一字段)|
| P0-8 §1.4 | MiroFish 输出仅入 evidence_collection 不入 RiskCheckSummary | §1.1(LLM 可写 evidence content)+ §1.2 类别 2(RiskCheckSummary)|
| P0-9 §2 红线 7 | watchlist 排除规则不进 RiskEngine | §1.1(LLM 可写 reasoning 解释为何降级)+ §1.2 类别 6(WatchlistPolicy 不可写)|
| P0-9 §2 红线 10 | event 路径不夺 traditional cap | §1.2 类别 4(cap_allocator 字段不可写)|

## 红线 / 边界(立即生效硬约束)

P0-10 在 P0-1 ~ P0-9 既有红线基础上叠加,共 **18 条** P0-10 专属红线:

1. **LLM positive list 仅 4 类字段可写**:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text, conclusion}` / `risk_parameter_proposals.proposal_text`;新增 LLM 可写字段必须先走 `P0-10-amendment-{date}-llm-positive-list-expand.md`

2. **LLM negative list 8 类整合锁**:类别 1 InstructionPlan 决策字段 / 类别 2 RiskCheckSummary / 类别 3 evidence_id 命名 / 类别 4 cap_allocator / 类别 5 MockBroker / 类别 6 WatchlistPolicy / 类别 7 DataQualityState/ReconciliationTicket/AcceptanceReport / 类别 8 RiskConfig — 严禁 LLM 写;实施期 lint rule + Pydantic strict mode + extra="forbid" 三层守门

3. **InstructionPlan 字段类型必须 frozen Pydantic v2 strict 模式**:`model_config = ConfigDict(frozen=True, strict=True, extra="forbid")`;LLM 输出经 schema validate 失败即降级;严禁 `extra="allow"` / `extra="ignore"`(继承 P0-3 §2 红线 12 immutability + extra="forbid" 抗 prompt injection)

4. **LLM 单调用超时 = 30 秒硬阈值**:`llm_single_call_timeout_seconds=30`;调整必须先走 amendment;实施期由 `asyncio.wait_for` 强制 enforce;严禁通过 monkey-patch / try-except 绕过

5. **重试次数 = 0**:LLM 单调用失败 + fallback 失败后,Agent 返回 None;**不重试**(避免重复成本 + 加剧 latency);P0-10 实施期 grep 校验 `for retry in range` / `for _ in range(retry_count)` 不出现在 LLM 调用路径

6. **必经 Agent 4 个固定锁**:`fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`;调整必经名单(增加或减少)必须先走 `P0-10-amendment-{date}-mandatory-agent-list.md`

7. **fund_manager 是唯一 BUY/SELL/HOLD 倡议者**:其他 Agent 输出仅作为 evidence/debate/context 输入;严禁其他 Agent 直接产生 InstructionSide 输出 + 严禁 InstructionPlanBuilder 接受非 fund_manager 的 signal 字段;实施期 lint rule grep `signal.*=.*['"]buy['"]` 不出现在非 fund_manager Agent 文件

8. **fund_manager 输出经 InstructionPlanBuilder 五道早返 + RiskEngine 14-check 双层守门**:任何"fund_manager 直出 InstructionPlan 不经守门"的代码即红线违规;严禁 `if fund_manager_output.signal == "buy": broker.buy(...)` 类绕过模式

9. **risk_officer 与 RiskEngine 14-check 双层防护**:risk_officer 失败 → 必经 Agent 缺失 → 降级 HOLD(不到 RiskEngine);risk_officer 通过 ≠ RiskEngine 必通过;两者独立并行,互不替代

10. **`debate_round_count ≥ 1`**(继承 P0-3 §2 红线 7;此处复述);`max_debate_rounds`(slow=2 / fast=1;P0-9 §3.1 锁)

11. **LLM 全停 ≥ 1h 触发 P0 系统级中断**(继承 P0-6 §1.7 之一):重置 45 交易日窗口 + simulation_auto 进入 `paused-no-llm`;重大事件仅写 evidence 不发指令;严禁绕过此触发条件继续发 InstructionPlan

12. **成本超 hard 阈值 = ¥20 即暂停所有 LLM 调用**:`QUANTMIND_DAILY_BUDGET=20`(env var 默认);`cost_guard.hard_ceiling` 命中即 simulation_auto 进入 `paused-budget-breach`;次日 00:00 自动解除;严禁通过修改 cost_guard 代码或绕过 ceiling 继续调用

13. **agent_models.yaml runtime 不可改 + hot-reload 禁用**(继承 P0-7 §1.4 RiskConfig 全锁精神):`enable_hot_reload: false` 锁定;调整任一字段必须先走 `P0-10-amendment-{date}-routing-change.md` + `git diff config/agent_models.yaml` + 进程重启

14. **`backend/api/llm*.py` / `backend/api/agents*.py` 仅允许 GET 端点**:严禁 POST/PUT/PATCH/DELETE(继承 P0-7 §1.4 / P0-9 §1.3);实施期 lint rule grep 必须返回 0 行

15. **新增 provider 必须先走 amendment**:DeepSeek/Qwen/Kimi 三家 provider 锁定;`config/agent_models.yaml` 注释保留 `claude` / `openai` 槽位但启用必须先走 amendment;严禁实施期未走 amendment 即添加 provider key

16. **fund_manager tiered routing 锁定**:`triage=qwen` / `escalation=kimi` / `confidence_lt=0.6`;调整必须先走 amendment;严禁通过 monkey-patch / 配置直改绕过

17. **`fund_manager_shadow_baseline` 永远不参与决策**(继承 audit §10.4 Phase 5B 出口 shadow 测试约束):`frequency: shadow_only` 锁定;实施期 lint rule grep `fund_manager_shadow_baseline` 调用必须仅在 `backend/services/shadow_runner.py`,严禁出现在 `backend/agents/graph.py::run_analysis` 或 InstructionPlanBuilder 调用路径

18. **`FundManagerOutput` / `AgentDebateRecord` / `RiskParameterProposal` / `EvidenceItem` 是 frozen Pydantic v2 strict 模型**:`model_config = ConfigDict(frozen=True, strict=True, extra="forbid")`;就地 mutation 红线违规(继承 P0-3 / P0-4 / P0-5 / P0-6 / P0-7 / P0-8 / P0-9 immutability 原则)

## 影响范围(实施期改动清单)

### 1. 配置层(YAML)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `config/agent_models.yaml` | 顶部加 `enable_hot_reload: false`;现有 9-Agent 路由配置不动 | P0 |

### 2. 数据模型与服务层(Python)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `backend/llm/providers.py` | `RouterConfig` 加 `enable_hot_reload: bool = False` 字段;Pydantic 模型加 `model_config = ConfigDict(frozen=True, strict=True, extra="forbid")` | P0 |
| `backend/llm/router.py` | `_call_provider` 包 `asyncio.wait_for(timeout=30)`;`_maybe_reload_config` 加 `if not enable_hot_reload: return`;**重试次数 = 0**(已是) | P0 |
| `backend/agents/base.py` | `extract_json_from_response` 升级到 strict Pydantic validate;失败抛 `LLMSchemaValidationError` | P0 |
| `backend/agents/graph.py` | `run_analysis` 检查必经 4 Agent 输出非 None;失败 → 降级 HOLD;`fund_manager_shadow_baseline` 仅在 shadow_runner 调用 | P0 |
| `backend/agents/fund_manager.py`(新建/重写)| 输出 `FundManagerOutput` strict schema(signal/confidence/reasoning/recommended_volume_hint)| P0 |
| `backend/agents/models.py` | 新增 `AgentDebateRecord` / `EvidenceItem` / `FundManagerOutput` / `RiskParameterProposal` 四个 frozen + strict + extra="forbid" Pydantic 模型 | P0 |
| `backend/services/instruction_plan_builder.py` | 在 fund_manager 输出后接入五道早返 + RiskEngine 14-check;**严禁** 接受非 fund_manager 的 signal | P0 |
| `backend/services/cost_guard.py` | `paused-budget-breach` 状态字段 + 次日 00:00 auto-resume cron + `POST /api/cost-guard/resume` 端点 | P0 |
| `backend/services/p0_interrupt_detector.py`(新建/扩展)| LLM 三家 provider 1h 滚动窗口 health check + 全停 ≥ 1h 触发 P0 系统级中断 | P0 |

### 3. API 层(FastAPI)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `backend/api/llm.py`(新建)| `GET /api/llm/router-config` 只读视图(secrets redacted)| P0 |
| `backend/api/llm*.py` / `backend/api/agents*.py` | 删除/标 410 任何 POST/PUT/PATCH/DELETE 端点 | P0 |
| `backend/api/cost_guard.py` | `POST /api/cost-guard/resume` 端点(用户手动解除 paused-budget-breach)| P0 |

### 4. 前端(Vue)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `frontend/src/views/LLMRouterView.vue`(新建/扩展)| 显示 9-Agent 路由(只读)+ 当日 cost summary + paused 状态指示 | P1 |
| `frontend/src/views/CostGuardView.vue` | 显示 soft/hard 阈值 + 当日累计 + 手动 resume 按钮 | P1 |
| `frontend/src/views/AgentDebateView.vue` | 显示每 InstructionPlan 关联 agent_debate_records 时间线(reasoning_text 可读)| P1 |

### 5. 测试

| 路径 | 新增 / 改动 | 优先级 |
|------|--------------|--------|
| `tests/llm/test_router_timeout.py`(新建)| 单调用 30s 超时 + fallback 30s 超时 + 重试 = 0 + Agent 返回 None | P0 |
| `tests/llm/test_router_hot_reload_disabled.py`(新建)| `enable_hot_reload=false` 时 config mtime 变化不触发 reload | P0 |
| `tests/agents/test_mandatory_agents.py`(新建)| 4 必经任一缺失 → 降级 HOLD;5 可跳过任一缺失 → 继续辩论 | P0 |
| `tests/agents/test_fund_manager_terminal.py`(新建)| fund_manager 是唯一 BUY/SELL/HOLD 倡议者;其他 Agent 不可输出 signal | P0 |
| `tests/agents/test_fund_manager_double_gate.py`(新建)| fund_manager 输出 buy + RiskEngine 14-check 失败 → REJECTED | P0 |
| `tests/agents/test_schema_strict.py`(新建)| LLM 输出包含 forbidden 字段 → ValidationError;Pydantic extra="forbid" 命中 | P0 |
| `tests/services/test_p0_interrupt_llm_all_down.py`(新建)| 三家 provider 1h 100% 失败 → 重置 45 交易日窗口 + paused-no-llm | P0 |
| `tests/services/test_cost_guard_paused.py`(新建)| ¥20 hard 命中 → paused-budget-breach;次日 00:00 auto-resume | P0 |
| `tests/agents/test_field_permission_lint.py`(新建)| `backend/agents/` + `backend/llm/` grep forbidden 字段赋值表达式必须 0 行 | P0 |
| `tests/integration/test_llm_full_flow.py`(新建)| 9-Agent 全跑 + 4 必经 + fund_manager 终局 + 五道早返 + 14-check + InstructionPlan REJECTED 全链路 | P0 |

### 6. 文档

| 路径 | 改动 | 优先级 |
|------|------|--------|
| `CLAUDE.md` | §1.3 P0-10 进度行 + §2.1 P0-10 ✅ + §3.1 加第 20 块红线(P0-10 18 条) | P0 |
| `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md` | 本文件 | ✅ |
| `docs/decisions/P0-3-amendment-2026-05-09-instruction-plan-llm-writable-fields.md`(实施期产出) | 在 P0-3 InstructionPlan schema 上明确 `reasoning: str | None` 等 LLM 可写字段 + `risk_summary` 不可被 LLM 覆写边界 | P0 |
| `docs/quantmind_owner_decision_points_2026-05-07.md` | §P0-10 表格状态 ⏳ → ✅ + 链接本文件;§P1-8 标记由 P0-10 §3.5 部分锁定 | P0 |
| `MEMORY.md` | 加 P0-10 索引项 | P0 |
| `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_10.md`(新建)| P0-10 锁定要点 | P0 |

## 决策依据

### 1. audit 引用

- `docs/quantmind_project_audit_2026-05-07.md` §5(多 Agent 与 LLM 决策现状):9-Agent LangGraph pipeline + 已存在但未与 InstructionPlan 闭环
- §10(LLM Router 与 Agent 编排):`backend/llm/router.py` 已支持 fallback / hot-reload / cost tracking;P0-10 锁定为禁用 hot-reload
- §13 推荐路线图:LLM 解释 / 草案 / 辩论;**LLM 不直接决定股数与风控红线**

### 2. 用户决策记录(2026-05-09 P0-10 四题)

- **Q1 字段权限矩阵**:用户选 "极严" — LLM 可写 4 类(reasoning / evidence content / agent_debate / proposal_text);严禁 8 类 negative list
- **Q2 降级矩阵**:用户选 "严格 fail-closed" — 30s 超时 + 0 重试 + LLM 全停 1h 触发 P0 中断 + ¥20 hard 暂停
- **Q3 必经 Agent**:用户选 "四必经 + fund_manager 终局" — fundamental + technical + risk_officer + fund_manager 必经;5 个可跳过
- **Q4 路由锁定**:用户选 "锁 baseline + 调整必走 amendment" — agent_models.yaml runtime 不可改 + hot-reload 禁用 + 新增 provider 走 amendment

### 3. 与既有决策的衔接

- **P0-1**(simulation_auto 底座):四必经 Agent 缺失即降级 HOLD,与 always-on 兼容(simulation_auto 不暂停,只是单条 InstructionPlan 降级)
- **P0-3**(InstructionPlan 严格 schema):派生 amendment `P0-3-amendment-2026-05-09-instruction-plan-llm-writable-fields.md` 明确 `reasoning` 字段
- **P0-4 / P0-5 / P0-6 / P0-7 / P0-8 / P0-9**:LLM negative list 8 类整合
- **P0-6 P0 系统级中断**:LLM 全停 ≥ 1h 是 5 类之一(已存在;此处复述边界)
- **P0-7 RiskConfig 全锁**:agent_models.yaml runtime 不可改与 RiskConfig 全锁同精神
- **P0-9 MiroFish 加分项**:`intelligence_officer`(MiroFish 路径)是可跳过 Agent,与 §3.5 一致;event 路径独立 cap=1 与 §3.1 4 必经互不冲突
- **P1-8(Kimi thinking 使用策略)**:由 P0-10 §3.5 部分锁定 — fund_manager tiered routing + per-Agent thinking 矩阵已锁;P1-8 后续仅讨论"细节调优"(如各 Agent 的 max_tokens 微调)

### 4. 代码事实抽检

- `backend/llm/router.py::LLMRouter`(513 行):已支持 fallback + hot-reload + cost tracking + tiered routing;P0-10 实施期需禁用 hot-reload + 加 30s 超时
- `config/agent_models.yaml`(196 行):9-Agent + fund_manager_shadow_baseline + 三家 provider + per-Agent thinking;P0-10 锁定整体作 baseline
- `backend/agents/graph.py`(>400 行):LangGraph orchestration;P0-10 实施期需加必经 Agent 检查 + 失败降级
- `backend/services/cost_guard.py`(已存在):`QUANTMIND_DAILY_BUDGET=20` + soft 70% + hard 100%;P0-10 锁定行为 + 加 paused-budget-breach 状态

### 5. 红线动机

| 红线编号 | 动机 |
|----------|------|
| 1 / 2 | LLM 写入边界是抗 prompt injection / 抗误决策的根本约束 |
| 3 | strict mode + extra="forbid" 让 LLM 输出额外字段直接 validate 失败,防 prompt injection 添加未授权字段 |
| 4 / 5 | 30s 超时 + 0 重试 防止 LLM 失败时无限等待 + 重复成本 |
| 6 / 7 / 8 | 必经 4 Agent + fund_manager 终局 + 双层守门是 P0-1 §1.6 多角色辩论原则的具体化 |
| 9 | risk_officer (LLM 视角) 与 RiskEngine (确定性硬限制) 双层防护是 P0-7 14-check + LLM 不写 RiskCheckSummary 的逻辑展开 |
| 10 | 复述 debate_round_count ≥ 1 边界,防 LLM 无辩论直出 |
| 11 / 12 | 全停与成本暂停是 P0-6 P0 系统级中断与 cost_guard 既有机制的实施层 |
| 13 / 14 / 15 | agent_models.yaml 锁定与 RiskConfig 全锁同精神 — 关键配置不可 hot-reload + 不可走 mutation API |
| 16 | tiered routing 锁定保 fund_manager 决策路径稳定;调 confidence_lt 阈值影响成本与质量 |
| 17 | shadow_baseline 隔离防 Phase 5B 测试代码污染生产决策路径 |
| 18 | frozen Pydantic v2 strict immutability 原则;继承 P0-3/4/5/6/7/8/9 |

## 后续动作 / Checklist

### 决策落定 — 当前 commit 范围

- [x] 写 `docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`(本文件)
- [ ] 同步更新 `CLAUDE.md` §1.3 进度行 + §2.1 表格 + §3.1 加第 20 块"LLM 角色边界红线"(18 条)+ §3.4 操作速查 + 4 条 P0-10 lint 命令
- [ ] 同步更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-10 表格状态 + §P1-8 部分锁定标记
- [ ] 同步更新 `MEMORY.md` 加 P0-10 索引项
- [ ] 创建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_10.md` 自记忆文件
- [ ] 等用户授权后 commit;不自动 push
- [ ] commit 后:**P0 决策清单全部 ✅** → 写"P0 全锁 final summary handoff prompt",启动决策对齐期收尾

### 实施期(决策对齐期完成后启动)

实施期不在 P0-10 commit 范围,但本节列出 checklist 作为 implementation 阶段输入:

#### 配置(P0)

- [ ] `config/agent_models.yaml` 顶部加 `enable_hot_reload: false`
- [ ] 实施期产出 `docs/decisions/P0-3-amendment-2026-05-09-instruction-plan-llm-writable-fields.md`

#### LLM 模块(P0)

- [ ] `backend/llm/providers.py` 加 `enable_hot_reload` 字段 + frozen Pydantic strict
- [ ] `backend/llm/router.py` `_call_provider` 包 `asyncio.wait_for(timeout=30)`
- [ ] `backend/llm/router.py` `_maybe_reload_config` 加 `enable_hot_reload` 守门

#### Agent 模块(P0)

- [ ] `backend/agents/base.py` `extract_json_from_response` 升级 strict
- [ ] `backend/agents/graph.py` `run_analysis` 加必经 4 Agent 检查 + 降级 HOLD
- [ ] `backend/agents/fund_manager.py` 输出 `FundManagerOutput` strict schema
- [ ] `backend/agents/models.py` 新增 4 个 frozen + strict + extra="forbid" 模型

#### Service 模块(P0)

- [ ] `backend/services/instruction_plan_builder.py` 接入 fund_manager 输出 + 五道早返 + 14-check
- [ ] `backend/services/cost_guard.py` 加 paused-budget-breach 状态 + auto-resume cron
- [ ] `backend/services/p0_interrupt_detector.py` 加 LLM 1h 全停检测

#### API 层(P0)

- [ ] `backend/api/llm.py` 新建 GET /api/llm/router-config
- [ ] `backend/api/llm*.py` / `backend/api/agents*.py` 删除/标 410 mutation 端点
- [ ] `backend/api/cost_guard.py` 加 POST /api/cost-guard/resume

#### 前端(P1)

- [ ] `frontend/src/views/LLMRouterView.vue`(只读路由 + cost summary + paused 状态)
- [ ] `frontend/src/views/CostGuardView.vue`(soft/hard 阈值 + 手动 resume)
- [ ] `frontend/src/views/AgentDebateView.vue`(辩论时间线)

#### 测试(P0)

- [ ] `tests/llm/test_router_timeout.py`
- [ ] `tests/llm/test_router_hot_reload_disabled.py`
- [ ] `tests/agents/test_mandatory_agents.py`
- [ ] `tests/agents/test_fund_manager_terminal.py`
- [ ] `tests/agents/test_fund_manager_double_gate.py`
- [ ] `tests/agents/test_schema_strict.py`
- [ ] `tests/services/test_p0_interrupt_llm_all_down.py`
- [ ] `tests/services/test_cost_guard_paused.py`
- [ ] `tests/agents/test_field_permission_lint.py`
- [ ] `tests/integration/test_llm_full_flow.py`

#### 红线静态检查(实施期 lint rule)

```bash
# P0-10 红线 1 / 2:LLM 不可写 forbidden 字段
grep -rnE "(InstructionPlan\.(volume|limit_price|valid_until|status|risk_summary|evidence_ids|debate_round_count|instruction_id)\s*=)" backend/agents/ backend/llm/

# P0-10 红线 3:Pydantic strict + extra="forbid"
grep -rn "extra.*=.*['\"]allow['\"]\|extra.*=.*['\"]ignore['\"]" backend/agents/models.py backend/llm/providers.py

# P0-10 红线 4:超时 30s 必须 enforce
grep -rnE "(asyncio\.wait_for.*timeout=30|llm_single_call_timeout_seconds.*=.*30)" backend/llm/router.py

# P0-10 红线 5:重试次数 = 0
grep -rnE "(for retry in range|for _ in range\(retry_count|retry.*=.*[1-9])" backend/llm/router.py

# P0-10 红线 7:fund_manager 是唯一 BUY/SELL 倡议者
grep -rnE "signal.*=.*['\"]buy['\"]|signal.*=.*['\"]sell['\"]" backend/agents/ | grep -v "fund_manager"

# P0-10 红线 13 / 14:agent_models.yaml runtime 不可改
grep -rnE "@router\.(post|put|patch|delete)" backend/api/llm*.py backend/api/agents*.py

# P0-10 红线 13:hot-reload 禁用
grep -rn "enable_hot_reload" config/agent_models.yaml | grep -v "false"

# P0-10 红线 17:shadow_baseline 隔离
grep -rn "fund_manager_shadow_baseline" backend/agents/graph.py backend/services/instruction_plan_builder.py
```

---

**P0-10 决策锁定时间**:2026-05-09
**P0-10 决策实施期**:全部 P0 决策(P0-1 ~ P0-10)已锁定 → 决策对齐期收尾启动
**下一站**:P0 全锁 final summary handoff(基于 10 个 P0 决策结果重写 CLAUDE.md + 生成新执行计划 → P1 决策对齐 OR 实施期启动)
