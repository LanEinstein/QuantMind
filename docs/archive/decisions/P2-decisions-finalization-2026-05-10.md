# P2 决策对齐收官 2026-05-10 — P2-1/P2-3 superseded + P2-2 deferred to dedicated session + P2-4 派生 P1-6 二次 amendment

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P2-decisions-finalization-2026-05-10 |
| 决策日期   | 2026-05-10 |
| 状态       | ✅ 已锁定(P2 形式收官)|
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §4 P2 决策点(4 项:P2-1 ~ P2-4)|
| 范围说明   | 本决策为 **P2 决策对齐收官轻量文档**;不为 P2-1 ~ P2-4 各自起独立决策文档(P2-1 + P2-3 已被 P0-8 + P1-6 实质吸收;P2-2 用户明确要单开 dedicated session 调研;P2-4 仅需在 P1-6 amendment 补 1 类 AuditEventType)|
| 派生 amendment | (1) `P1-6-amendment-2026-05-10-audit-eventtype-27.md`(P2-4 派生:`AuditEventType` enum 26 → 27 类;新增 `EXECUTION_REPORT_PARSE_FAILED`)|
| 不锁定的 | P2-2 自进化机制边界 — 用户明确要单开 dedicated session 仔细调研讨论;**实施期 Phase A/B 严禁写任何自进化代码**直到 P2-2 dedicated session 锁定 |

## 决策摘要

P2 决策对齐采用 **P2-1/P2-3 形式标 superseded(已被 P0/P1 实质吸收)+ P2-2 deferred to dedicated session(自进化是必须的但需要单独深度调研)+ P2-4 轻量派生 P1-6 二次 amendment(补 1 类 AuditEventType 解决唯一遗漏事件类型)** 路径,完成 **P2 形式收官**:

1. **P2-1 MiroFish 使用范围 → superseded by P0-8**(2026-05-09 P0-8 决策时已实质吸收):事件驱动(severity≥HIGH;cap=20/日)+ 17:00 盘后复盘双路径;输出仅入 `evidence_collection.content` 不入 `RiskCheckSummary`;硬 cap=1 严禁占用 traditional 4 cap(P0-9 §1 锁定);MiroFish 是"加分项不是核心"(用户 P0-9 重要澄清;`feedback_mirofish_supplementary_not_core.md`)。**不需要独立决策文档**;`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-1 章节顶部加 "✅ superseded by P0-8" 标识 + 链接。

2. **P2-2 自进化机制边界 → deferred to dedicated session**(用户 critical feedback):用户明确不同意"全锁不启用任何自进化"推荐;原话:"自进化功能是必须要有的,可以引入'自我进化后必须经过模拟盘验证'以及状态回滚,但绝对不能完全禁止。大模型如果没有持续学习/持续适应新变化/持续追踪最前沿量化以及金融交易思路的能力,就一定无法长久立于不败之地。具体采用怎样的策略,我们可以单开一个 session 仔细调研和讨论。" 当前状态:P0+P1 已锁的 hot-reload 禁用 + LLM 严禁写决策字段 + RiskConfig/agent_models.yaml/WatchlistPolicy/BrokerConfig/cost_guard 4 常量全 runtime 不可改 = 保持不变(自进化的实现路径在不破现有红线前提下设计 — 例如"代码生成 + 模拟盘验证 + amendment + 重启"是合规路径)。**实施期 Phase A/B/B-finale 严禁写任何自进化代码**直到 P2-2 dedicated session 锁定。`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-2 章节顶部加 "⏳ deferred to dedicated session" 标识 + 链接到 `~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md`。

3. **P2-3 移动端 / 远程访问 → superseded by P1-6 §1.5**(2026-05-10 P1-6 决策时已隐式锁):Backend uvicorn 显式 `--host 127.0.0.1` + Frontend Vite `host: '127.0.0.1'`(F-001 修历史违规)+ MongoDB/Redis docker-compose 127.0.0.1 + Nginx `listen 127.0.0.1:80/443` 显式补充 + 远程访问严格走 SSH tunnel + 严禁 LAN 段开放 + 严禁公网入站(继承 P0-2 永禁 HTTPS 入站红线)= **等价"移动端 Web UI 不开放"**;移动端依赖飞书交互(继承 P0-1 §1.3 模式切换 = 账户生命周期事件 + 用户原决策建议倾向)。**不需要独立决策文档**;`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-3 章节顶部加 "✅ superseded by P1-6 §1.5" 标识 + 链接。

4. **P2-4 告警渠道 → P1-6 二次 amendment(26 → 27 类 AuditEventType;补 EXECUTION_REPORT_PARSE_FAILED)**:核查 P1-6 §1.8 锁定 22 类 + P1-7 amendment 后 26 类 AuditEventType,对照用户原始决策清单 8 类必须告警事件,**7 类已被 P1-6 + P1-7 累积覆盖**:行情源断流 → `DATA_QUALITY_BREACH`;资讯源失败 → `DATA_QUALITY_BREACH`;LLM 全部不可用 → `LLM_CALL_TIMEOUT_30S` + `DAILY_COST_CEILING_20CNY_BREACHED`;指令生成失败 → `BUILDER_EARLY_RETURN`;风控拦截 → `RISK_ENGINE_CHECK_REJECTED`;模拟账户异常 → `MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL`;日终对账缺失 → `EOD_PIPELINE_FAILED` + `RECONCILIATION_TICKET_OPEN_OR_EXPIRED`。**唯一遗漏**:飞书回报解析失败。本决策派生 `P1-6-amendment-2026-05-10-audit-eventtype-27.md` 新增 1 类 `EXECUTION_REPORT_PARSE_FAILED`(归类 4 异常 + 拦截事件;reason_namespace='execution_report_ambiguous';继承 P0-4 §1 严格正则失败即 AMBIGUOUS 节奏;告警通道走 P0-4 五模板预写死的澄清飞书走 P0-2 §1.2 主路径长连接,**严禁走 P0-2 §2.5 备用 webhook**因继承"备用 webhook 仅可发系统告警绝不发买卖指令/对账请求/澄清消息"红线)。**告警通道(继承 P1-7 §1.7)整体维持**:飞书 + audit + Phase B 成本拆解面板;**严禁 SMTP/Slack/Discord 第二告警通道**(继承 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态)。

## 1. P2-1 MiroFish 使用范围 — superseded by P0-8

### 1.1 P0-8 已锁(2026-05-09)

- 事件驱动(severity≥HIGH;cap=20/日)+ 17:00 盘后复盘双路径
- 输出仅入 `evidence_collection.content` 不入 `RiskCheckSummary`(防 LLM 主导决策;P0-10 §2 红线 1 LLM 字段权限矩阵)
- 严禁日常每只股票都跑(资源消耗 + LLM 成本失控;与 P1-7 ¥20 daily hard 冲突)
- 严禁仅做研究展示(已实际接入 evidence 链,有实质决策影响)

### 1.2 P0-9 重要澄清(用户 2026-05-09 修正)

- MiroFish 是"加分项不是核心"(`feedback_mirofish_supplementary_not_core.md`)
- 硬 cap=1 严禁占用 traditional 4 cap(traditional 4 + event_reserved 1 = 5 cap)
- 14:30 后 event 未用可滑动给 traditional;反向不可

### 1.3 处理方式

`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-1 顶部加标识:

```markdown
## P2-1. MiroFish 使用范围 ✅ superseded by P0-8 (2026-05-09)

> 实质决策已在 P0-8 决策时吸收;详见 docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md §1
> 关键澄清:MiroFish 是加分项不是核心(P0-9 用户明确;~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_mirofish_supplementary_not_core.md)
> 不需要独立 P2-1 决策文档
```

## 2. P2-2 自进化机制边界 — deferred to dedicated session

### 2.1 用户 critical feedback(2026-05-10 P2-2 决策时)

> 自进化功能是必须要有的,可以引入'自我进化后必须经过模拟盘验证'以及状态回滚,但绝对不能完全禁止。大模型如果没有持续学习/持续适应新变化/持续追踪最前沿量化以及金融交易思路的能力,就一定无法长久立于不败之地。具体采用怎样的策略,我们可以单开一个 session 仔细调研和讨论。

### 2.2 用户给出的关键边界

- 自进化输出**必须经过模拟盘验证**(类似 P0-6 acceptance 45 交易日滚动窗口的验证理念)
- 必须有**状态回滚**机制(自进化产生不利结果时可回退;类似 P1-2.A `ReconciliationApplier::reset_to_snapshot` 思路)
- 不能完全禁止任何自进化路径

### 2.3 deferred 原因

- 自进化策略涉及多维度深度调研:LLM 持续学习架构(在线学习 vs 离线 fine-tune vs prompt 演化)、A/B 测试框架设计、模拟盘验证窗口长度、状态回滚粒度(参数级 vs prompt 级 vs 模型级)、风控参数自进化的特殊约束(P0-7 锁定的不可改性 ·vs· 自进化必要性)、最前沿追踪机制(论文订阅 vs 会议跟踪 vs 持续 benchmark)
- 单 session 4 议题轻量决策无法覆盖此复杂度
- 用户主动提出"我们可以单开一个 session 仔细调研和讨论"

### 2.4 当前阶段约束(deferred 期间)

- **P0+P1 已锁的所有 runtime 不可改 + hot-reload 禁用 + LLM 严禁写决策字段 = 保持不变**
- **实施期 Phase A/B/B-finale 严禁写任何自进化代码**(P2-2 锁定前)
- 当前 LLM 仍仅产出:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text, conclusion}` / `risk_parameter_proposals.proposal_text` 4 类只读建议(P0-10 §2 红线 1)
- `RiskParameterProposal` 提议通道(P0-7 §1.4)是唯一允许的"自进化轻量入口" — 但必须人工 review + amendment + 重启,不自动应用

### 2.5 处理方式

`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-2 顶部加标识:

```markdown
## P2-2. 自进化机制边界 ⏳ deferred to dedicated session (2026-05-10)

> 用户明确要单开 dedicated session 仔细调研讨论自进化策略;不允许在本阶段做出最终决策。
> 关键约束(用户给出):自进化输出必须经过模拟盘验证 + 必须有状态回滚机制;不能完全禁止任何自进化路径。
> 当前实施期 Phase A/B/B-finale 严禁写任何自进化代码直到 P2-2 dedicated session 锁定。
> Critical feedback memory: ~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md
```

### 2.6 dedicated session 调研建议方向(参考)

- LLM 持续学习架构选型:在线学习(Online Learning)/ 离线 fine-tune / prompt 演化 / RAG 知识库扩充 / Adaptive Routing 各自适用场景
- A/B 测试框架:14/30/45 日观察窗口 + shadow run + 性能对比 + 自动选优;继承 P0-6 acceptance 框架
- 状态回滚机制:参数级(YAML diff + 重启)/ prompt 级(版本化 + amendment)/ 模型级(checkpoint + amendment)各自实现
- 风控参数自进化特殊约束:P0-7 锁定的不可改性 ·vs· 用户"必须有自进化"立场如何调和(可能路径:RiskParameterProposal 自动 A/B + 模拟盘验证后**人工最终批准**走 amendment)
- 最前沿追踪机制:NeurIPS/ICML/QuantCon 论文订阅 + LLM 总结 + 提议入库
- 自进化路径合规性:不破 P0+P1 已锁红线(尤其 LLM 严禁写决策字段、hot-reload 禁用、Pydantic strict + extra="forbid")
- 调研产出物:专项决策文档 `docs/decisions/P2-2-self-evolution-{summary}.md` + 派生 amendment(可能影响 P0-7 / P0-10 / 各 P1 决策)

## 3. P2-3 移动端 / 远程访问 — superseded by P1-6 §1.5

### 3.1 P1-6 已锁(2026-05-10)

- Backend `uvicorn` 显式 `--host 127.0.0.1`(deploy/quantmind-backend.service)
- Frontend Vite `host: '127.0.0.1'`(F-001 修 P1-5 §2 红线 11 历史违规;原 `'0.0.0.0'`)
- MongoDB / Redis docker-compose 127.0.0.1 绑定(已 ✅)
- Nginx `listen 127.0.0.1:80` / `127.0.0.1:443`(显式补充)
- 远程访问严格走 SSH tunnel(`ssh -L 9276:127.0.0.1:9276 user@host` + `ssh -L 8000:127.0.0.1:8000 user@host`)
- 严禁 LAN 段开放(违反 P1-5 §2 红线 11 严格意旨 + LAN 任意设备可访问未认证 UI)
- 严禁全 0.0.0.0 + iptables 控制(单机过度复杂 + iptables 漏配即全凭证泄露 + 与 P0-2 永禁 HTTPS 入站红线冲突)

### 3.2 等价含义

P1-6 §1.5 锁定 = "移动端 Web UI 不开放"(127.0.0.1 only + 严禁 LAN + 严禁公网 = 物理上无移动端访问路径);移动端**依赖飞书交互**(继承 P0-1 §1.3 模式切换 = 账户生命周期事件 + 用户原决策建议倾向"第一阶段 Web UI 保持本机/内网,移动侧主要依赖飞书")。

### 3.3 处理方式

`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-3 顶部加标识:

```markdown
## P2-3. 移动端或远程访问 ✅ superseded by P1-6 §1.5 (2026-05-10)

> 实质决策已在 P1-6 §1.5 决策时吸收;详见 docs/decisions/P1-6-secrets-shell-env-...md §1.5
> 等价含义:移动端 Web UI 不开放(127.0.0.1 only + 严禁 LAN/公网 + 远程仅 SSH tunnel)
> 移动端依赖飞书交互(继承 P0-1 §1.3 + 用户原决策建议倾向)
> 不需要独立 P2-3 决策文档
```

## 4. P2-4 告警渠道 — 派生 P1-6 二次 amendment(26 → 27 类 AuditEventType)

### 4.1 P1-6 + P1-7 累积覆盖核查

| 用户原列必须告警事件 | 对应 AuditEventType(P1-6 + P1-7 累积)| 告警通道(继承 P1-7 §1.7)|
|---------------------|----------------------------------------|------------------------|
| 行情源断流 | `DATA_QUALITY_BREACH` | audit + 飞书 warning(P1-7 软触发) |
| 资讯源失败 | `DATA_QUALITY_BREACH`(news_outage 不阻断买卖路由 — P0-8 §2 红线 7;但仍写 audit)| audit + 飞书 info |
| LLM 全部不可用 | `LLM_CALL_TIMEOUT_30S` + `DAILY_COST_CEILING_20CNY_BREACHED` | audit + 飞书 critical(必发)|
| 指令生成失败 | `BUILDER_EARLY_RETURN`(5 道早返任一)| audit + 飞书 warning |
| 风控拦截 | `RISK_ENGINE_CHECK_REJECTED`(14-check 任一)+ `MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL`(at-fill 三层守门)| audit + 飞书 warning |
| 模拟账户异常 | `MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL` + `STATE_MACHINE_ILLEGAL_TRANSITION` + `MOCKBROKER_RESET` | audit + 飞书 critical |
| 飞书回报解析失败 | **❌ 无独立 AuditEventType** | **唯一遗漏 — 本决策派生 P1-6-amendment 补** |
| 日终对账缺失 | `EOD_PIPELINE_FAILED` + `RECONCILIATION_TICKET_OPEN_OR_EXPIRED` | audit + 飞书 critical |

### 4.2 派生 P1-6 二次 amendment

新建 `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md`:

- `AuditEventType` enum 26 → 27 类
- 新增 1 类:`EXECUTION_REPORT_PARSE_FAILED = "execution_report_parse_failed"`
- 归类 4 异常 + 拦截事件
- `actor=AuditActor.FEISHU_USER` / `FRONTEND_USER`(双路径继承 P1-5 §2 红线 5 仅 2 写入端点)
- `resource_type='execution_report'`
- `payload={raw_text: str, regex_attempt_results: dict, parse_error_kind: str}`
- `outcome=AuditOutcome.FAILURE`
- `reason_namespace='execution_report_ambiguous'`(继承 P0-4 §1 严格正则失败即 AMBIGUOUS 节奏)
- 告警通道:audit + 飞书 warning(走 P0-4 §1 五模板预写死的澄清飞书 + P0-2 §1.2 主路径长连接;**严禁走 P0-2 §2.5 备用 webhook**)

### 4.3 处理方式

`docs/quantmind_owner_decision_points_2026-05-07.md` §P2-4 顶部加标识:

```markdown
## P2-4. 告警渠道 ✅ 已锁定 2026-05-10(派生 P1-6 二次 amendment 26 → 27 类 AuditEventType)

> 决策定稿: docs/decisions/P2-decisions-finalization-2026-05-10.md §4
> 派生 amendment: docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md(新增 EXECUTION_REPORT_PARSE_FAILED)
> 7/8 类已被 P1-6 + P1-7 累积覆盖;唯一遗漏"飞书回报解析失败"由本 amendment 补
> 告警通道维持 P1-7 §1.7 锁定:仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP/Slack/Discord 第二通道
```

## 5. 红线(P2 收官)

> 以下条款一律以本 P2 收官决策为准。**违反即视为红线违规**。

1. **P2-1 MiroFish 使用范围 superseded by P0-8 永锁**:不重新开 P2-1 独立决策;不重新评估 MiroFish 使用范围;不引入 MiroFish "日常每只股票都跑" 路径(违反 P0-9 + P1-7 ¥20 daily hard);不引入 MiroFish 仅"研究展示"路径(违反 P0-8 §1 已接入 evidence 链)。

2. **P2-2 自进化机制边界 deferred to dedicated session 永锁**:**实施期 Phase A/B/B-finale 严禁写任何自进化代码**;严禁单方面在其他场景做自进化决策;严禁把"hot-reload 禁用 + LLM 严禁写决策字段"理解为"自进化永远禁止"(用户已明确不同意);P2-2 dedicated session 召开前任何自进化相关代码 PR / 决策必须先指向 `feedback_self_evolution_must_have.md` 用户原话 + 暂停推进 + 等待 dedicated session。

3. **P2-3 移动端/远程访问 superseded by P1-6 §1.5 永锁**:不开发独立移动端 App;PC 浏览器仅本机/SSH tunnel;移动端依赖飞书交互;不引入 LAN 段开放 / 公网 IP / 0.0.0.0 任一变体(继承 P1-6 §2 红线 8+9)。

4. **P2-4 告警渠道仅飞书 + audit + Phase B 成本拆解面板永锁**(继承 P1-7 §2 红线 8):严禁 SMTP/Slack/Discord 第二告警通道(继承 P1-6 §1.1 凭证池仅 LLM 3 + 飞书 6 锁状态);`AuditEventType` enum 27 类(P1-6 + P1-7 amendment + 本决策 amendment 三层累积);任何新增/删除/重命名必走 `P1-6-amendment`;严禁 magic string `event_type` 写入 audit。

5. **澄清飞书走 P0-2 §1.2 主路径长连接永锁**:`EXECUTION_REPORT_PARSE_FAILED` 触发的澄清飞书消息走 P0-4 §1 五模板预写死 + P0-2 §1.2 主路径 lark-oapi 长连接发送;**严禁走 P0-2 §2.5 备用 webhook**(继承"备用 webhook 仅可发系统告警绝不发买卖指令/对账请求/澄清消息"红线)。

6. **`AuditEventType` enum 27 类锁定永锁**(P2-4 amendment 后):26 类 + `EXECUTION_REPORT_PARSE_FAILED` = 27 类;实施期 G-008 一次性升级 backend/audit/models.py;单元测试 enum 完整性断言 27 类。

## 6. 实施期任务

> P2 收官不引入新 G/F/E/D 系列任务;复用 P1-7 实施期 G-008 + G-017(原 Kimi cap 集成,扩展为也含 ExecutionReportParser 失败 raise 分支挂 audit hook)+ G-031 + G-037。

- **G-008 升级**:在 `backend/audit/models.py::AuditEventType` 一次性新增 5 类(P1-7 amendment 4 + P2-4 amendment 1)
- **G-017 升级 / 新增**(原 P1-7 G-017 是 Kimi cap;此处扩展):在 `backend/execution/parser.py::ExecutionReportParser` 失败 raise 分支挂 `audit_store.write(event_type=EXECUTION_REPORT_PARSE_FAILED, ...)`;失败时同时触发澄清飞书 5 模板(继承 P0-4 §1)
- **G-031 升级**:单元测试 enum 完整性断言 22 → 27 类(原 P1-7 G-031 断言 26 类)
- **G-037 升级**:集成测试 5 类新事件(P1-7 amendment 4 + P2-4 amendment 1)端到端写 audit_events;特别核 `EXECUTION_REPORT_PARSE_FAILED` 的 raw_text + regex_attempt_results + reason_namespace='execution_report_ambiguous' 完整性

## 7. 决策依据

### 7.1 用户对齐(2026-05-10 P2 收官 1 轮 4 议题)

- Q1 P2-1 MiroFish 范围 → 直接标 superseded by P0-8 ✅
- Q2 P2-2 自进化机制边界 → **用户 critical feedback 否决"全锁不启用"推荐**;deferred to dedicated session(自进化必须有 + 模拟盘验证 + 状态回滚)⏳
- Q3 P2-3 移动端 / 远程访问 → 直接标 superseded by P1-6 §1.5 ✅
- Q4 P2-4 告警渠道 → 派生 P1-6 二次 amendment 补 EXECUTION_REPORT_PARSE_FAILED(26 → 27 类) ✅

### 7.2 关键判断

- **P2-1/P2-3 形式标 superseded 符合 P2 决策的实际状态**:P0/P1 决策已实质吸收;独立 P2-N 决策文档会重复;轻量形式标识即可
- **P2-2 deferred to dedicated session 符合用户表态**:用户明确要"单开一个 session 仔细调研讨论";Claude 此前的"全锁"推荐被用户否决;尊重用户;不强推决策
- **P2-4 轻量派生 P1-6 二次 amendment 符合"补遗漏"思路**:7/8 类已覆盖唯 1 遗漏;新建独立 P2-4 决策文档过重;派生 amendment 是最小干预
- **27 类 AuditEventType 连续两次 amendment 符合 P1-6 §2 红线 12 锁定边界**:任何新增/删除/重命名必走 amendment;实施期 G-008 一次性升级不需多次部署;无破坏式

### 7.3 排除选项

- **重新开 P2-1 决策评估 MiroFish 使用范围**:P0-8 + P0-9 已实质锁定;重开违反一致性
- **强行锁定 P2-2 自进化全锁不启用**:用户已明确否决;违反 critical feedback
- **重新开 P2-3 决策评估开发独立移动端 App**:P1-6 §1.5 + 用户原决策倾向已实质锁定;违反一致性
- **重新开 P2-4 全决策评估 8 类事件每一类的告警通道**:P1-6 + P1-7 已覆盖 7/8;重开过重
- **引入 SMTP/Slack/Discord 第二告警通道**:违反 P1-6 §1.1 凭证池封闭性
- **EXECUTION_REPORT_PARSE_FAILED 澄清飞书走 P0-2 备用 webhook**:违反 P0-2 §2.5 "备用 webhook 仅可发系统告警绝不发澄清消息" 红线
- **不补 EXECUTION_REPORT_PARSE_FAILED**:audit 复盘需跨 collection join 效率低;违反 P1-6 §1.8 "4 类事件强制写 audit + 调试性事件不入" 统一原则
- **P2-2 dedicated session 不召开,用 P1 锁定状态作为永久状态**:违反用户表态;失去自进化能力 = 长久无法立于不败之地

### 7.4 与 P0/P1 红线协同

- 继承 P0-2 §1.2 主路径长连接 + §2.5 备用 webhook 仅可发系统告警 → §1.4 EXECUTION_REPORT_PARSE_FAILED 澄清飞书走主路径不走备用
- 继承 P0-3 §2.5 LLM 严禁拼接飞书消息文本 → §4.2 raw_text 完整记录无须 LLM;澄清飞书走五模板硬编码
- 继承 P0-4 §1 严格正则 only + LLM 完全不参与回报路径 → §4.2 EXECUTION_REPORT_PARSE_FAILED 在 ExecutionReportParser 失败 raise 节点挂 audit hook 不引入 LLM
- 继承 P0-7 §2 红线 14 RiskConfig runtime 不可改 → §2 P2-2 deferred 期间继续锁定
- 继承 P0-8 §1 MiroFish 双路径 + §2 红线 7 输出仅入 evidence_collection → §1.1 P2-1 superseded
- 继承 P0-9 §2 5 单 cap MiroFish cap=1 + 加分非核心 → §1.2 P2-1 superseded
- 继承 P0-10 §1.4 ¥20 hard ceiling + §2 红线 1 LLM 字段权限矩阵 → §2 P2-2 deferred 期间继续锁定 + §4.2 EXECUTION_REPORT_PARSE_FAILED audit 写不引入 LLM
- 继承 P1-2.A §1.4 broker_events append-only insert-only 8 项红线 → §4.2 audit_events 同款约束
- 继承 P1-5 §2 红线 5 仅 2 写入端点 → §4.2 EXECUTION_REPORT_PARSE_FAILED 由两写入端点失败分支挂 audit hook 不引入新写入端点
- 继承 P1-6 §1.5 全层 127.0.0.1 only + SSH tunnel + §2 红线 8+9 → §3.1 P2-3 superseded
- 继承 P1-6 §1.7 audit_events frozen schema + §1.8 AuditEventType + §2 红线 11+12+13+14+15+16+17+18+19 → §4.2 amendment 完全继承
- 继承 P1-7 §1.7 告警通道仅飞书 + audit + Phase B 面板 + §2 红线 8 → §4 维持

## 8. 后续动作

### 8.1 SSoT 文档同步

- 更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P2-1/P2-2/P2-3/P2-4 各章节顶部加状态标识 + 链接
- 新建 memory `feedback_self_evolution_must_have.md`(已完成 ✅)
- 更新 `MEMORY.md` 索引:加 P2 收官 entry + P2-2 deferred + EXECUTION_REPORT_PARSE_FAILED entry
- 更新 `CLAUDE.md` 顶部 blurb 改"决策对齐期 P0+P1+P2 全完成 ✅";§2 加 §2.14 P2 收官红线节;§5 操作速查不需新增 grep
- 新建 `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md`(P2-4 派生 amendment;已完成 ✅)
- 同步更新 P1-6 主文档 §1.8 + §2 红线 12 链接到本 amendment

### 8.2 派生 amendment

- **P1-6 二次 amendment(P2-4 派生)**:`AuditEventType` enum 26 → 27 类;新增 `EXECUTION_REPORT_PARSE_FAILED`;非破坏式追加

### 8.3 下一站

- **决策对齐期 P0+P1+P2 全完成 ✅**(P2-2 deferred 不阻塞实施期 Phase A;Phase A 内容与自进化无关)
- **启动实施期 Phase A**(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除)
- 实施期 Phase A 任务清单参见 P1-5 §3 E-001~E-013 + P1-6 §3 F-001~F-007 + P1-7 §3 G-001~G-007(其中 P1-7 G-001~G-007 文档同步已在 2026-05-10 P1-7 锁定 session 完成)
- **P2-2 dedicated session** 在合适时机召开(建议 Phase A/B 实施期间或之后;不阻塞当前 Phase A)

### 8.4 实施期 Phase A 启动条件

- ✅ P0+P1+P2 全锁(P2-2 deferred 状态等价"当前 P1 锁定继续生效")
- ✅ 实施期任务清单完备(E + F + G 系列)
- ✅ 红线全锁可静态 grep 检测
- ✅ 派生 amendment 全部完成(P0-10 + P1-6 第 1 次 + P1-6 第 2 次)

### 8.5 本 P2 收官决策不做的事

- 不锁定 P2-2 自进化机制(deferred to dedicated session)
- 不引入新独立 P2-N 决策文档(P2-1/P2-3 形式 superseded;P2-4 仅 amendment)
- 不修改 P0/P1 任何已锁红线
- 不引入 SMTP/Slack/Discord 等告警通道扩展
- 不引入移动端 App 开发
- 不引入 MiroFish 日常跑批路径
- 不引入任何自进化代码(实施期 Phase A/B/B-finale 期间)

---

**P2 决策对齐收官 ✅**

**决策对齐期 P0 + P1 + P2 全完成 ✅**

P2 形式收官完成;P2-1/P2-3 superseded by P0-8 + P1-6 §1.5;P2-2 deferred to dedicated session(自进化必须有但需深度调研);P2-4 派生 P1-6 二次 amendment 补 1 类 AuditEventType 27 类;**6 红线 + 0 新增 G/F/E/D 任务**(全部复用 P1-7 实施期任务)。

**下一站**:实施期 Phase A 启动(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除);具体执行待用户授权。
