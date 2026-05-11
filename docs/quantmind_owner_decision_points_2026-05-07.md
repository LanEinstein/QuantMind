# QuantMind 需要用户决策与调研的重要事项清单

生成日期: 2026-05-07
重构日期: 2026-05-08
用途: 按最新项目定位重写必须由项目所有者亲自决策、调研或确认的关键事项。
关系: 本文件承接 `docs/quantmind_project_audit_2026-05-07.md`，不是实现方案，而是“决策前置清单”。

## 0. 最新项目定位

2026-05-08 已重新锁定方向:

> QuantMind 不再追求真实券商账户的程序化下单、半自动下单或全自动下单。项目只聚焦“基于真实实时数据与资讯的模拟实盘能力验证”，并提供飞书交互闭环: 系统生成具体买入/卖出指导，发送到你的飞书群；你在券商 APP 手动执行；执行后在飞书群回报实际操作；系统解析回报、更新内部状态镜像并继续监控。

因此后续所有决策围绕两种模式展开:

| 模式 | 名称 | 目标 | 是否触达真实券商接口 | 是否真实下单 |
|---|---|---|---|---|
| 模式 A | `simulation_auto` 完全自动化模拟全流程 | 检验平台在真实行情和资讯输入下的实战决策、风控、执行和复盘能力 | 否 | 否 |
| 模式 B | `feishu_interactive` 飞书交互人工执行 | 给用户发送可直接照做的操作指令，并接收用户手工成交回报来维护状态镜像 | 否 | 用户手动在券商 APP 操作 |

硬红线:

- 不开发真实券商自动下单适配器作为当前路线目标。
- 不把 QMT、Ptrade、vn.py 或任何券商 API 接入列为当前阶段主线。
- `simulation_auto` 的“自动”只允许作用于 MockBroker/模拟账户。
- `feishu_interactive` 中系统发出的只是操作指导；真实交易由你本人在券商 APP 完成。
- 系统内部持仓在飞书模式下只是“用户回报驱动的状态镜像”，不是券商账户事实源。

## 1. 阅读方式

优先级含义:

- P0: 不确定就不要开始重构核心闭环。
- P1: 会影响系统架构、成本和交互质量，应在主要开发前定下来。
- P2: 可稍后细化，但最好提前形成原则。

每个决策点建议形成一个明确输出物，例如一页调研结论、一个表格、一条红线规则或一个书面边界。没有输出物的“想法”不算已决策。

外部资料说明: 本文件列出若干公开调研入口，作为后续核验候选，不构成供应商推荐。飞书 API、权限名称、消息限制、行情源、资讯源、费用和接口稳定性都可能变化，正式选择前必须以平台当前官方文档、管理后台配置和你的实测结果为准。

## 2. P0 决策点

## P0-1. 两种运行模式与系统边界

### 当前已锁定

QuantMind 只保留两种运行模式:

1. `simulation_auto`: 完全自动化模拟全流程。
2. `feishu_interactive`: 飞书交互 + 用户手工券商 APP 执行。

废止旧路线中的:

- `live_confirm`。
- `phase7_live`。
- 真实券商自动下单。
- 以实盘自动化为目标的 QMT/VN.py/Ptrade 接入规划。

### 为什么重要

旧文档把“模拟盘、半自动实盘、全自动实盘”放在同一条升级路径里，导致工程上会自然滑向券商接口、真实订单审批和自动下单。新定位要求把“平台能力验证”和“用户手动执行”分开，避免代码、界面和文档继续暗示系统会触达真实券商账户。

### 你需要产出

一条新的运行模式红线:

```text
QuantMind 只允许 simulation_auto 和 feishu_interactive。
任何真实券商 API 下单、撤单、账户同步都不属于当前项目目标。
```

一张模式矩阵:

| 能力 | simulation_auto | feishu_interactive |
|---|---|---|
| 真实行情/资讯输入 | 是 | 是 |
| 自动生成操作计划 | 是 | 是 |
| 自动执行订单 | 仅 MockBroker | 否 |
| 真实券商 APP 操作 | 否 | 用户手工 |
| 状态来源 | 模拟账户撮合结果 | 用户飞书回报 |
| 复盘依据 | 模拟成交 + 行情 | 指令 + 用户回报 + 行情 |

## P0-2. 飞书接入形态

### 你需要决定

飞书交互到底采用哪种接入方式:

1. 群自定义机器人 Webhook。
2. 企业自建应用机器人 + 消息 API + 事件订阅。
3. 企业自建应用机器人 + 消息卡片 + 卡片回调。
4. 临时先用 Webhook 发消息，手工在前端录入成交回报。

### 联网调研结论

- 飞书/开放平台的 `im/v1/messages` 可向用户或会话发送文本、富文本、可交互消息卡片等消息；给群发消息时机器人需要在群里，并使用类似 `chat_id` 的接收者标识。
- 接收群内用户回报不能只靠“群自定义机器人 Webhook”。要稳定接收用户消息，应使用企业自建应用机器人，开启机器人能力并订阅 `im.message.receive_v1` 事件。
- 飞书事件接收可选“长连接”或“开发者服务器回调”。长连接模式的优点是本地服务只需能访问公网，不必提供公网 IP 或域名；开发者服务器回调则需要可被飞书访问的 HTTPS 地址和安全校验。
- 如果希望用按钮完成“我已执行 / 未执行 / 部分成交 / 需要调整”，需要消息卡片和 `card.action.trigger` 类回调能力；如果第一阶段只要求你发文字回报，可以先不做卡片按钮。

### 建议倾向

第一阶段采用:

> 企业自建应用机器人 + `im.message.receive_v1` 接收 @机器人消息 + `im/v1/messages` 主动发送群消息。优先使用长连接接收事件，避免为了本机开发暴露公网 Webhook。

自定义机器人 Webhook 只适合作为单向告警备用，不适合作为核心交互闭环。

### 你需要产出

飞书接入确认表:

- 是否能创建企业自建应用。
- 是否能获取 App ID / App Secret。
- 是否能启用机器人能力。
- 是否能把机器人加入目标群。
- 是否使用长连接还是 HTTPS 回调。
- 目标群 `chat_id` 获取方式。
- 是否第一阶段使用文字回报，还是直接上交互卡片。
- 是否允许机器人读取所有群消息，还是只接收 @机器人消息。

### 调研入口

- 飞书发送消息 API: https://open.feishu.cn/document/server-docs/im-v1/message/create
- 飞书接收消息事件: https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
- 飞书长连接事件接收: https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/choose-a-subscription-mode
- 飞书自定义机器人: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

## P0-3. 操作指令结构

### 你需要决定

平台发给你的飞书消息必须精确到什么程度。至少应明确:

- 股票代码和名称。
- 买入、卖出、持有、减仓、加仓、撤销建议。
- 股数或目标仓位。
- 限价、参考价或价格区间。
- 有效期。
- 触发条件。
- 止损线、止盈或失效条件。
- 不执行条件。
- 指令编号。
- 数据时间戳和来源。
- 风控检查结果。
- 执行后你应该如何回报。

### 为什么重要

当前 `TradingSignal` 只有 `action`、`target_price`、`confidence`、`risk_score`、`reasoning`。它不能直接指导你买多少、卖多少、什么时候失效，也无法让飞书回报和系统状态精确对应。

### 建议倾向

新增比 `TradingSignal` 更严格的 `InstructionPlan` / `OrderInstruction`:

```text
instruction_id: QM-20260508-093001-600519-BUY-001
mode: simulation_auto | feishu_interactive
code: 600519
name: 贵州茅台
side: BUY | SELL | HOLD
quantity: 100
limit_price: 1680.00
valid_until: 2026-05-08 14:55:00 Asia/Shanghai
reason_summary: ...
risk_summary: ...
stop_loss: ...
take_profit: ...
data_snapshot_at: ...
reply_format: ...
```

### 你需要产出

一份“飞书操作指令模板”，例如:

```text
【QuantMind 操作指令 QM-...】
标的: 600519 贵州茅台
动作: 买入 100 股
价格: 限价不高于 1680.00 元
有效期: 今日 14:55 前
前置条件: 未涨停、盘口可成交、账户可用现金 >= ...
风控: 单股仓位执行后约 8.5%，低于上限 15%
止损/失效: 跌破 ... 或资讯反转时重新评估
执行后请回复:
已执行 QM-... 买入 600519 100股 成交价 1678.50 手续费 5.00
未执行 QM-... 原因: ...
```

## P0-4. 飞书回报语法与成交状态

### 你需要决定

你在飞书里如何回报真实操作。需要支持:

- 已全部执行。
- 部分执行。
- 未执行。
- 执行价格不同。
- 股数不同。
- 券商拒单。
- 临时改为不操作。
- 盘后补录。
- 手工更正上一条回报。

### 为什么重要

飞书模式下平台不能直接读取券商账户，因此状态更新完全依赖你的回报。如果回报语法不稳定，系统会把错误持仓当作事实，后续指令会继续偏离。

### 建议倾向

先采用严格文字模板，后续再加卡片按钮:

```text
已执行 QM-20260508-093001-600519-BUY-001 买入 600519 100股 成交价 1678.50 手续费 5.00
部分执行 QM-... 买入 600519 60股 成交价 1678.50 剩余未成交40股
未执行 QM-... 原因: 价格未到
更正 QM-... 买入 600519 100股 成交价 1677.80 手续费 5.00
```

歧义处理红线:

> 解析不出 `instruction_id`、股票代码、方向、股数或成交价时，系统不能更新持仓，只能发飞书要求澄清。

### 你需要产出

- 回报模板。
- 允许自然语言的范围。
- 哪些字段缺失必须追问。
- 是否允许盘后批量补录。
- 是否允许系统根据聊天上下文猜测指令编号。

## P0-5. 账户状态来源与对账机制

### 你需要决定

飞书模式下系统如何维护账户镜像:

- 初始现金。
- 初始持仓。
- 佣金、印花税、过户费估算。
- 成交价口径。
- 可卖股数 T+1 口径。
- 分红、配股、送转、停牌等特殊事项。
- 用户回报与系统预期不一致时如何处理。
- 是否需要每日手工对账。

### 为什么重要

系统没有券商只读接口，因此无法自动知道真实账户余额、委托、成交、撤单和持仓。如果不建立对账制度，几次手工偏差后，系统发出的后续买卖建议会失去基础。

### 建议倾向

将飞书模式账户定义为:

> `UserReportedPortfolio`: 由用户回报驱动、由行情实时估值的账户状态镜像。

必须显示“未券商核验”状态。每日收盘后要求你发送一次简短对账:

```text
日终对账 2026-05-08 可用现金 123456.78
持仓 600519 100股 成本 1678.50; 000001 2000股 成本 10.20
```

### 你需要产出

- 初始账户镜像表。
- 日终对账模板。
- 状态冲突处理规则。
- 允许偏差阈值。
- 是否需要每周人工导出券商成交单校验。

## P0-6. 完全自动化模拟闭环验收标准

### 你需要决定

`simulation_auto` 的目标是检验平台实战能力，因此必须定义“模拟跑得好”到底是什么意思。

### 需要定义的指标

- 连续运行天数。
- 信号生成成功率。
- 操作指令完整率。
- 风控拦截率。
- 模拟订单成交率。
- 最大回撤。
- 成本后收益。
- 基准超额收益。
- 胜率和盈亏比。
- 换手率。
- 指令到执行延迟。
- 数据延迟和缺失率。
- LLM 超时率。
- 飞书模式下回报解析准确率。

### 建议倾向

先用 `simulation_auto` 做能力考场:

```text
真实行情/资讯 -> 候选/分析 -> InstructionPlan -> RiskEngine
-> SimulationExecutor -> MockBroker -> PortfolioLedger -> PnL/复盘
```

只有这条链路稳定，飞书模式发给你的指令才值得信任。

### 你需要产出

一份阶段门槛:

```text
允许打开 feishu_interactive 真实指导前，simulation_auto 至少满足:
- 连续 X 个交易日无 P0 故障。
- 指令完整率 >= X%。
- 回报/订单账本 100% 可追溯。
- 数据缺失率低于 X%。
- 最大回撤低于 X%。
- 每日复盘报告可生成。
```

## P0-7. 风险红线与指导强度

### 你需要决定

即使系统不自动下单，它仍会影响你的真实资金操作。你需要明确:

- 总资金上限。
- 单次指令金额上限。
- 单只股票最大仓位。
- 总仓位上限。
- 每日最大买入次数。
- 每日最大亏损暂停线。
- 连续亏损暂停线。
- 是否允许追涨。
- 是否允许涨停买入、跌停卖出。
- 是否允许科创板、创业板、北交所、ST、可转债、ETF。

### 建议倾向

在 `simulation_auto` 未稳定前:

- 飞书指令可先只发送“模拟建议”，不建议真实执行。
- 真实资金指导强度从小额、低频、watchlist 内标的开始。
- 所有仓位计算必须由确定性代码完成，不能由 LLM 自由决定。

### 你需要产出

一份风险参数确认表，并明确哪些参数绝不允许由 LLM 修改。

## P0-8. 数据与资讯可信度

### 你需要决定

系统发出飞书指令时，到底信任哪些数据源和资讯源。

当前代码事实:

- 行情主源 `adata`，备用 `akshare`。
- 历史 K 线备用 `baostock`。
- 新闻主要来自 `akshare.stock_news_em` 的 Eastmoney 口径。
- 后台每 30 秒只采集三大指数，不采集全 watchlist 个股快照。
- 没有完整 A 股交易日历，只按工作日判断。

联网调研补充:

- AKShare 提供沪深京 A 股实时行情接口，单次可返回所有沪深京 A 股上市公司实时行情字段，但它是开源数据接口，仍需自行处理稳定性、延迟、字段变化和授权边界。
- 飞书指令中必须展示行情数据时间戳和来源，不能只写“实时数据支持”。

### 建议倾向

第一阶段:

- 仍可使用免费源，但必须加数据质量层。
- 飞书指令必须带 `data_snapshot_at`、`quote_source`、`news_source`。
- 当行情源失败、延迟过大、源间偏差过大时，只允许发“观察/暂停”消息，不发买卖指令。

### 你需要产出

数据源可信度表:

- 实时行情源。
- 历史行情源。
- 新闻/公告/政策源。
- 允许本地缓存与否。
- 延迟阈值。
- 断流处理。
- 是否可用于飞书真实操作指导。

## P0-9. 第一阶段标的范围与频率

✅ **已锁定 2026-05-09** — 决策文档:[`docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md`](decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md)

锁定要点:

- **watchlist 规模与组成**:13 codes(沪主 4 + 深主 3 + 创业板 3 + 宽基 ETF 3:`510300` 沪深300 + `510500` 中证500 + `159949` 创业板50);第一阶段不动态选股,用户实施期手工填 13 codes
- **排除规则四件套**:新股 ≤30 个交易日 + 次新 ≤180 个交易日 + 流动性 < 2 亿元(过去 20 交易日日均成交额) + 单价 > 500 元(高单价排除);在 InstructionPlanBuilder 第五道早返,**不进** RiskEngine 14-check
- **调仓频率**:沿用 fast/slow 双频(slow_pipeline 每交易日 09:00 + fast_pipeline 09/11/13/15 四次盘中);5 单 cap 分配 = traditional 4 + event_reserved 1;14:30 后 event 未用滑动给 traditional
- **MiroFish 是加分项不是核心**:平台底层是传统 AI 量化交易,MiroFish 是补充层;event 路径硬 cap=1 严禁占用 traditional 主路径 cap
- **方向限制**:严格 long-only(InstructionSide 永锁 BUY/SELL/HOLD);永禁 SHORT/COVER/MARGIN_BUY/REVERSE_REPO/ETF_SUBSCRIBE/ETF_REDEEM;ETF 仅二级市场买卖;ETF 套利预留 P1 但永锁 disabled
- **watchlist runtime 不可改**:`backend/api/watchlist*.py` 仅 GET;旧 add_stock/remove_stock/clear 标 deprecated;改 watchlist 必须先走 amendment + 进程重启
- **派生 amendment**:`docs/decisions/P0-7-amendment-2026-05-09-watchlist-exclusion-rules.md`(实施期产出)说明排除规则在 InstructionPlanBuilder 早返而非 RiskEngine 边界

红线 17 条详见决策文档 §2;实施期改动清单见 §5。

## P0-10. LLM 角色边界

✅ **已锁定 2026-05-09** — 决策文档:[`docs/decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md`](decisions/P0-10-llm-role-boundary-strict-field-permission-fail-closed-degradation-four-mandatory-agents.md)

锁定要点:

- **极严字段权限矩阵**:LLM positive list 仅 4 类(`InstructionPlan.reasoning` + `evidence_collection.content` + `agent_debate_records.{reasoning_text, conclusion}` + `risk_parameter_proposals.proposal_text`);LLM negative list 8 类整合 P0-1~P0-9 累积红线;Pydantic strict + extra="forbid" + lint rule 三层守门
- **严格 fail-closed 降级矩阵**:单调用超时 30s + 0 重试 + 必经 Agent 失败降级 HOLD + LLM 全停 1h 触发 P0-6 系统级中断 + 成本 ¥20 hard 暂停 simulation_auto
- **四必经 Agent + fund_manager 终局**:必经 4 个 = `fundamental_analyst`(qwen)+ `technical_analyst`(qwen)+ `risk_officer`(kimi)+ `fund_manager`(kimi+thinking+tiered routing);可跳过 5 个 = news_crawler/sentiment_analyst/data_cleaner/intelligence_officer/bull_researcher/bear_researcher;fund_manager 是唯一 BUY/SELL/HOLD 倡议者 + 双层防护 InstructionPlanBuilder 五道早返 + RiskEngine 14-check
- **agent_models.yaml baseline 锁定**:runtime 不可改 + hot-reload 禁用(继承 P0-7 RiskConfig 全锁精神);`backend/api/llm*.py` / `backend/api/agents*.py` 仅 GET;调整必走 `P0-10-amendment-{date}-routing-change.md` + git diff + 进程重启
- **派生 amendment**:`docs/decisions/P0-3-amendment-2026-05-09-instruction-plan-llm-writable-fields.md`(实施期产出)说明 InstructionPlan.reasoning 等 LLM 可写字段边界

红线 18 条详见决策文档 §2;实施期改动清单见 §5。

> 注:本决策同时**部分锁定** §P1-8(Kimi thinking 使用策略),fund_manager tiered routing(triage qwen + escalation kimi at confidence_lt 0.6)+ per-Agent thinking 矩阵已锁;P1-8 后续仅讨论"细节调优"。

### 建议倾向

LLM 可以做:

- 信息抽取。
- 事件归纳。
- 隐性变量推演。
- 多视角辩论。
- 生成操作建议草案。
- 解释为什么要买/卖/不动。

LLM 不应该直接做:

- 最终仓位硬计算。
- 风控硬限制。
- 是否突破风险红线。
- 账户状态更新。
- 飞书回报歧义猜测。
- 真实交易责任判断。

### 你需要产出

LLM 权限边界:

```text
LLM 可以产生建议和解释，但 InstructionPlan 必须通过结构化校验、确定性仓位计算和 RiskEngine；飞书回报必须通过确定性解析和必要的人工澄清。
```

## 3. P1 决策点

## P1-1. 新核心数据模型

### 你需要决定

是否接受新增以下核心集合/模型:

- `instruction_plans`: 系统生成的操作计划。
- `simulation_orders`: 模拟执行订单。
- `execution_reports`: 用户飞书回报。
- `portfolio_snapshots`: 模拟账户和用户回报账户快照。
- `feishu_messages`: 飞书发送与接收记录。
- `decision_ledger`: 统一账本。

### 建议倾向

必须新增。旧的 `trading_signals` 不能承载新闭环。

### 你需要产出

字段级数据模型草案。

## P1-2. MockBroker 持久化与真实行情估值

✅ **三子阶段全锁 2026-05-10** — A + B + C 全部已锁定

| 子阶段 | 状态 | 决策文档 | 范围 |
|--------|------|---------|------|
| **P1-2.A 持久化与日切调度** | ✅ 已锁定 2026-05-09 | [`docs/decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md`](decisions/P1-2.A-persistence-hybrid-snapshot-and-broker-scheduler.md) | 混合 broker_events delta + broker_snapshots EOD 全量 / Mongo multi-doc tx (RS 强约束) / ReconciliationApplier+ExecutionReportApplier 双类 / 条件自动恢复 ≤3h+checksum / 新建 BrokerScheduler / EOD chain 16:00→16:00:35 sequential + 17:00 MiroFish 独立 / 失败 1-retry + freeze 次日(第五种买卖类路由冻结源 `eod_pipeline_freeze`,派生 P0-7 amendment 候选) |
| **P1-2.B 估值与数据协议** | ✅ 已锁定 2026-05-10 | [`docs/decisions/P1-2.B-mtm-30s-equity-points-data-quality-on-demand.md`](decisions/P1-2.B-mtm-30s-equity-points-data-quality-on-demand.md) | intraday_mtm BrokerScheduler 第四 cron(30s 同 watchlist_snapshot 节奏)+ 三级回退价格(Redis≤60s→Mongo≤300s→last_known_cached degraded;禁 cost_price fallback)+ EquityPoint per-position 明细 + last_broker_event_id 反向索引 / eod_pipeline 插 verify_equity_point(chase_poller↔acceptance 之间;无点补 EOD_FALLBACK)+ DataQualityProvider per-stock evaluate 按需聚合 7 breach + 3 计数 + is_acceptable_for_buy_sell 仅 4 breach 阻断(news_outage/mirofish/snapshot_outage 不入)+ daily_state 不引入 collection 从 broker_events+watchlist_snapshots 按需装配 DailyTradingState |
| **P1-2.C 撮合增强与成本细化** | ✅ 已锁定 2026-05-10 | [`docs/decisions/P1-2.C-matching-allornone-defensive-limitcheck-tiered-slippage-transfer-fee.md`](decisions/P1-2.C-matching-allornone-defensive-limitcheck-tiered-slippage-transfer-fee.md) | ALL_OR_NONE 撮合(filled=volume 或 0;PARTIALLY_FILLED 仅用户回报路径)+ MockBroker 注入 MarketMetaProvider(get_prev_close + get_current_price 二级回退 Redis≤60s+Mongo≤300s 不退 last_known_cached)防御性涨跌停 re-check + REJECTED reason='price_limit_violation_at_fill'(三层守门:builder 五道早返 + RiskEngine 14-check check 12 pre-route + MockBroker at-fill;两层 reason 不同)/ 滑点 board 分级 sh_main:1.5+sz_main:1.5+chuangye:3.5+etf:1.5(BrokerConfig.slippage_bps_by_board dict 4 board 必齐;移除 slippage_bps:int=2 单标量;runtime 不可改+hot-reload 禁用)/ 深市过户费 0.00341% 双边(=0.0000341;现行实际费率非用户初拟 0.01%;Trade.transfer_fee 非破坏式扩展)/ cost_calculator.py 抽出 OrderCostBreakdown frozen + calculate_cost 纯函数(无self/无IO/无副作用)/ BrokerConfig+Trade 升级 strict+extra="forbid";mock_broker.py:35 孤儿 get_price_limits 助手 + 688x dead branch 实施期 D-007 删除统一走 stock_metadata.get_price_limit_pct(board)单一真相源 |

### 你需要决定(整体)

`simulation_auto` 是否必须把 MockBroker 的账户、订单、成交、持仓持久化到 MongoDB,并用实时行情 mark-to-market。

### 建议倾向

必须做。当前 MockBroker 内存状态重启即丢,且持仓市值近似使用成本价,不足以检验实战能力。

### 你需要产出

- ~~模拟账户持久化口径~~ ✅ P1-2.A
- ~~日切规则~~ ✅ P1-2.A
- ~~成交撮合规则~~ ✅ P1-2.C
- ~~估值数据源~~ ✅ P1-2.B
- ~~交易成本模型~~ ✅ P1-2.C

## P1-3. 飞书消息形态

### 你需要决定

第一阶段飞书消息采用:

- 纯文本。
- 富文本。
- 交互卡片。
- 文本 + 卡片混合。

### 建议倾向

先文本 + 严格回报模板，随后加交互卡片:

- 文本适合快速落地和保留完整指令。
- 卡片适合减少回报错误，例如“已执行/未执行/部分执行”按钮。

### 你需要产出

飞书消息模板和卡片交互范围。

## P1-4. 回报解析策略

### 你需要决定

飞书回报解析使用:

- 纯正则/规则。
- LLM 辅助解析。
- 规则优先，LLM 只做候选解释。

### 建议倾向

规则优先。LLM 只能在解析失败时生成“澄清问题”，不能直接写入成交。

### 你需要产出

回报解析状态机:

```text
received -> parsed -> validated -> applied
received -> ambiguous -> clarification_sent
received -> rejected -> ignored_with_reason
```

## P1-5. 前端优先工作流

✅ **已锁定 2026-05-10** — 决策文档:[`docs/decisions/P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md`](decisions/P1-5-frontend-workflow-mvp-7-pages-readonly-first-write-strict-bounded.md)

锁定要点:

- **MVP 7 页 + Phase B 4 页**:MVP = Dashboard / 系统状态 / InstructionPlan 池 / Portfolio / 用户回报录入 / 对账裁定 / 验收报告;Phase B 收尾再补 Agent 辩论 / 数据质量 / 飞书消息历史 / 成本拆解
- **一级菜单按决策闭环 4 分组**:运行状态 / 决策与指令 / 账本与成交 / 复盘与验收;设置(LLMRouter / DataSources / MiroFishConfig / CostDashboard)折叠为只读
- **写入接口立即下线 + Phase A 一次性破坏式删除**:`backend/api/{risk,watchlist,llm,agents,settings,trading,analysis}*.py` 所有 POST/PUT/PATCH/DELETE handler 删除;前端 RiskCenter / Settings / ApprovalQueue 写入按钮 + axios 调用立即下线;**仅允许 2 个前端写入端点** = POST `/api/execution-reports` + POST `/api/reconciliation-tickets/{ticket_id}/decide`
- **WS 单通道 12 类消息扩展**:已有 6 类 + 新增 8 类(`instruction_plan_update` / `broker_event` / `equity_point_update` / `data_quality_breach` / `freeze_source_update` / `ticket_update` / `acceptance_report_ready` / `feishu_message_received`)+ 删除 2 类(`auth_mode_change` / `approval_update`);SSE 仅保留 LLM 流式专用
- **用户回报飞书主 + 前端备用双路径**:飞书主走 lark-oapi 长连接 → ExecutionReportApplier;备用走前端 5 模板表单 + 实时 JS 正则镜像预览 + POST `/api/execution-reports` 同一 ExecutionReportApplier 入口;前端 JS 正则镜像与后端 `backend/execution/regex_patterns.py` 单一真相源保持一致(单元测试断言)
- **5 冻结源全局 StatusBar 顶部常驻 + 独立系统状态页**:5 状态点(`freeze_source_switch` / `freeze_source_ticket_open` / `freeze_source_circuit_breaker_cooldown` / `freeze_source_data_quality` / `freeze_source_eod_pipeline_freeze`)独立并行;永禁聚合为单一 `frozen=true`
- **三层决策拦截 reason 在 InstructionPlan 池详情抽屉三 tab**:Builder 早返(六检查项)/ RiskEngine 14-check(逐项)/ MockBroker at-fill(终态 + cost_breakdown);三层 reason 命名空间不同(`price_limit_violation_at_fill` ≠ `limit_up_block`/`limit_down_block`)便于 audit 区分
- **Performance + 验收报告页分离**:Performance 走 broker_snapshots+equity_points(可视化)/ 验收报告走 acceptance_reports(决策表格 + 5 稳定性 + 3 策略硬门槛 + can_switch_to_feishu_on() 布尔)
- **P1-5 暂不加本机认证**:范围归 P1-6;前端不允许存储任何凭证到 localStorage / sessionStorage / cookie;敏感配置末四位脱敏 + webhook_configured 布尔;Vite 默认 host: '127.0.0.1' 不允许 '0.0.0.0'
- **Simulation.vue 保留为 P1-5 范围外**:用户细化指令 — MiroFish 多 Agent 演化可视化具有融资展示价值,但 P1-5 阶段不重点投入,后续阶段再细致打算
- **派生 amendment**:无(本决策不修改既有 P0/P1-2.A/B/C 决策的硬约束;新增 backend/api 路由表为 GET only 端点扩展属非破坏式落地)

红线 18 条详见决策文档 §2;实施期改动清单 32 条(E-001~E-032)详见决策文档 §3。

## P1-6. 安全、密钥与访问边界 ✅ 2026-05-10 锁定

> 决策定稿:`docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md`

### 锁定结果摘要

- **Secrets 存储:shell env 单源 + git 钩子防护 + 启动期 fail-fast 三件套**:LLM 3 key + 飞书 6 凭证 + 未来扩展凭证全部走 `~/.bashrc`;`.env` 仅放非密配置 + 启动期 secrets_validator 强制扫描禁止;`.gitignore` 覆盖 `.env`+`*.key` + `.pre-commit-config.yaml` 引入 gitleaks v8.18+ + custom rules 覆盖 sk-* / FEISHU_* / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / MOONSHOT_API_KEY pattern
- **凭证轮换:事件驱动 + 12 月最长保质期 5 类强制触发**:① 凭证泄露/疑似泄露 ② 团队成员变动 ③ provider 通知 key 异常活动 ④ 12 月自然到期(warning 不强制 exit)⑤ 升级 P2 真实账户前;轮换流程 = 编辑 ~/.bashrc + source + systemctl restart + 启动期日志确认 fingerprint;严禁 hot-reload(继承 P0-7+P0-10);凭证 fingerprint = SHA256(value)[:8] 写 audit + 启动日志,严禁 plaintext 写持久化通道
- **飞书 6 凭证落地时机:P1-6 仅锁约束实际配置延迟到 feishu_interactive 启用前**:P1-6 仅锁定存储方式 + 轮换策略 + 应急 + fingerprint + 启动期 fail-fast 行为;实际 ~/.bashrc 配置不强制现在做(因 acceptance 通过日期未定可能 P2 末);启动期 fail-fast 仅在 `FEISHU_INTERACTIVE_ENABLED=true` 时校验飞书 6 凭证(默认 false 不影响 simulation_auto 启动)
- **凭证泄露应急:三步应急 playbook + gitleaks pre-commit + 启动期 secrets_validator 三件套**:① 立即轮换(更新 ~/.bashrc + source + systemctl restart + provider revoke)② git history 排查(git log -p -S 前 8 字符 + git filter-repo 重写 + force push 仅本人确认 + 已 push 到公网视为永久泄露必须 revoke)③ 影响评估(audit_events 反查 + 飞书控制台 + cost_guard 异常飙升)
- **IP 全层严锁 127.0.0.1 only + 远程访问仅 SSH tunnel**:Backend uvicorn 显式 --host 127.0.0.1 + Frontend Vite 修复 host:'127.0.0.1'(P1-5 红线 11 历史违规 F-001 必修)+ MongoDB/Redis docker-compose 127.0.0.1 已 ✅ + Nginx upstream + listen 127.0.0.1 显式补充 + 远程访问仅 SSH tunnel(SSH 本身是身份认证层);严禁 LAN 段开放 + 0.0.0.0 + iptables 控制
- **本机访问不加任何 auth middleware**(P1-5 §2 红线 11 P1-6 处置承诺履行):127.0.0.1 only 含义 = OS 用户隔离已是身份认证;前端 axios 不插任何 Authorization / Bearer / cookie / localStorage / sessionStorage(继承 P1-5);WebSocket + SSE 不要求 token 参数;不引入单 token 文件认证 / OAuth/OIDC / mTLS
- **审计日志:Mongo audit_events 180 天 TTL + JSONL 30 天双写**:新建 audit_events collection append-only insert-only(继承 P1-2.A broker_events 8 项红线);schema 由 backend/audit/models.py AuditEvent frozen Pydantic v2 strict + extra='forbid' 锁定 9 字段(event_id/timestamp/event_type/actor/resource_type/resource_id/payload/outcome/correlation_id/reason_namespace);TTL 180 天 = P0-6 45 交易日 acceptance × 4 倍安全余量;Mongo failure 时 fail-open(JSONL 兜底 + warning 不阻主路径);严禁 LLM 写 audit(继承 P0-10)
- **审计事件类型:4 类全写(22 个 AuditEventType 枚举永锁)**:类 1 — 两唯二写入端点(execution_report_submitted + reconciliation_ticket_decided)/ 类 2 — 模式切换 + 5 冻结源 + 生命周期(feishu_interactive_toggled + mockbroker_reset + freeze_source_*_changed + advance_day + eod_pipeline_*) / 类 3 — 凭证生命周期 + 飞书收发(secrets_validator_* + key_fingerprint_changed + feishu_*_sent + feishu_message_received) / 类 4 — 异常 + 拦截(state_machine_illegal_transition + risk_engine_check_rejected + builder_early_return + mockbroker_price_limit_violation_at_fill + data_quality_breach + reconciliation_ticket_open_or_expired + llm_call_timeout_30s + daily_cost_ceiling_20cny_breached);调试性事件(LLM raw / RiskEngine trace / 行情快照 / cron 心跳 / Redis cache)严禁入 audit 走 quantmind.jsonl
- **Audit 查询入口:CLI + GET API,不加前端页面**:scripts/query_audit.py CLI(--time-range / --event-type / --actor / --correlation-id / --format) + backend/api/audit.py GET /api/audit/events(仅 GET 符合 P1-5 §2 红线 1+2);**前端不占 P1-5 11 页名额**(后期需加查询界面必须先走 P1-5-amendment + P1-6-amendment 双批准)
- **派生 amendment**:无破坏式;新增 backend/audit/* 模块 + scripts/query_audit.py + GET /api/audit/events 端点 + 修复 Vite host '0.0.0.0' → '127.0.0.1' 历史违规;P1-5 §2 红线 11 在本决策完成"P1-6 处置"承诺即"不加本机认证"

红线 20 条详见决策文档 §2;实施期改动清单 34 条(F-001~F-034)详见决策文档 §3。

## P1-7. 成本预算 ✅ 已锁定 2026-05-10

> 决策定稿: `docs/decisions/P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md`
> 派生 amendment: `docs/decisions/P0-10-amendment-2026-05-10-cost-budget-extension.md` + `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-26.md`
> 锁定结果: 仅锁 LLM 预算(数据/运维不设)+ 日 hard ¥20 严格(继承 P0-10 不变)+ 月 soft ¥440 = 22 工作日 × ¥20 静态固定按自然月 + Kimi daily cap ¥4 单独锁(deepseek+qwen 不单独 cap 防失 failover 弹性)+ 软触发(¥14)关 Kimi escalation 路径(SoftDegradeManager;严禁缩减 4 必经 Agent / 降 fast 频次 / 全模型 deepseek-only)+ 月节点 50% audit only / 80% 飞书 warning / 100% 飞书 critical+cost_breakdown(月预算 100% 严禁触发停摆)+ 飞书仅 hard breach + 月节点(严禁 SMTP/Slack/Discord 第二通道)+ Phase B 成本拆解面板挂载 P1-5 §2 红线 1 锁 4 Phase B 页之一(GET only)+ AuditEventType 22→26 类(派生 P1-6 amendment)+ 20 红线 + 38 实施期任务(G-001~G-038)
>
> **P1 决策对齐收官 ✅**(P0-1~P0-10 + P1-2.A/B/C + P1-5 + P1-6 + P1-7 全锁;P1-1/P1-3/P1-4/P1-8 由 P0+P1 累积锁定)→ 启动实施期 Phase A

### 原始决策点(已被上方决策取代)

#### 你需要决定

- 每日 LLM 成本上限。
- Kimi thinking 使用上限。
- 飞书消息频率上限。
- 行情/资讯源预算。
- 服务器预算。

#### 建议倾向

`simulation_auto` 可较高频，`feishu_interactive` 必须低噪声:

- 不把每个信号都发飞书。
- 只发”可执行指令””风险变化””需要对账””错误澄清”。
- 预算软触发时降级模型和减少深度分析。

#### 你需要产出

月预算表与超限动作。

## P1-8. Kimi thinking 使用策略

### 你需要决定

哪些场景允许使用 Kimi thinking。

### 建议倾向

- 快速扫描: 禁用。
- 争议较大或重大事件: 可启用。
- 盘后复盘: 可启用。
- 风控硬判断: 不使用 LLM。
- 飞书临近交易时段指令: 必须有硬超时，超时则不发指令。

### 你需要产出

模型使用矩阵:

| 场景 | 模型 | thinking | 超时 | 失败动作 |
|---|---|---|---|---|
| 新闻摘要 | DeepSeek/Qwen | 关闭 | 待定 | 跳过或备用 |
| 指令生成草案 | Qwen/Kimi | 条件启用 | 待定 | 不发指令 |
| 风控计算 | 代码 | 不适用 | 秒级 | fail closed |
| 盘后复盘 | Kimi | 可开 | 待定 | 降级 |

## 4. P2 决策点

## P2-1. MiroFish 使用范围 ✅ superseded by P0-8 (2026-05-09)

> 实质决策已在 P0-8 决策时吸收;详见 `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md` §1
> 关键澄清:MiroFish 是加分项不是核心(P0-9 用户明确;`~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_mirofish_supplementary_not_core.md`)
> 不需要独立 P2-1 决策文档;P2 收官见 `docs/decisions/P2-decisions-finalization-2026-05-10.md` §1

### 原始决策点(已被 P0-8 取代)

#### 你需要决定

MiroFish 是:

- 日常每只股票都跑。
- 只在重大事件触发。
- 只做盘后复盘。
- 只做研究展示。

#### 建议倾向

只在重大事件和盘后复盘触发。MiroFish 输出作为证据之一，不直接产生买卖股数。

## P2-2. 自进化机制边界 ✅ 已锁定 2026-05-11(dedicated session 完成;保守 3 路径 + 全人工 gate + 飞书主动通知 + 45 日 shadow validate + 文件式 prompt registry)

> 决策定稿: `docs/decisions/P2-2-self-evolution-conservative-three-paths-shadow-validate-feishu-notify-file-registry.md`
> 派生 3 amendment:
>   - `docs/decisions/P0-7-amendment-2026-05-11-risk-proposals-shadow-validation.md`(risk_parameter_proposals 扩字段 + 走 P2-2 shadow validate)
>   - `docs/decisions/P1-2.A-amendment-2026-05-11-evolution-shadow-cron-5th.md`(BrokerScheduler 4→5 cron;新增 evolution_shadow_run 22:00 mon-fri)
>   - `docs/decisions/P1-6-amendment-2026-05-11-audit-eventtype-34.md`(AuditEventType 27→34 类;新增 7 类自进化生命周期)
> Critical feedback memory: `~/.claude/projects/-home-ps-papers-QuantMind/memory/feedback_self_evolution_must_have.md`(deferred → 已锁;补充实际锁定策略)
> 实施期 deferred 等用户在 dedicated 计划文档 session 锁定推进时间表后才允许 Phase X 实施代码启动

### 已锁定决策(P2-2 dedicated session 4 轮 16 议题)

#### 启用 3 路径(保守集;严禁 fine-tune/online learning/RLHF/DPO/继续 SFT)

- **DSPy GEPA 离线 prompt 演化**(batch offline;输出 `config/prompts/{agent}/{candidate}.yaml`;单次 ≤ ¥5 budget;严禁覆盖 production)
- **RAG provenance-gated 知识库自扩展**(白名单源 arxiv/semanticscholar/openreview/github releases/akshare changelog;写文件系统 `data/rag/`;零 Mongo)
- **FinMem 风格 in-context exemplars**(decision_ledger 近 90 日成功 case;严禁 >3 exemplars per prompt)

#### 全人工 gate + 飞书主动通知

- 任何自进化产物(prompt 新版本 / RAG 新文档 / proposal / amendment 草案)必须人工最终批准 + amendment + restart 才生效
- shadow validate 45 日通过 + 5+3 硬门槛达标 + challenger 胜 production → 立即发飞书通知(走 FEISHU_CUSTOM_BOT_WEBHOOK_URL;Alerter dedup_15min)
- LLM 严禁直接 mutate 任何 runtime 状态

#### Shadow validation 沿用 P0-6 45 交易日

- 完全复用 P0-6 `compute_acceptance_window` + `acceptance_reports` collection + 5 稳定性 + 3 策略硬门槛
- shadow_acceptance_reports 独立 collection 不影响 production acceptance_reports
- BrokerScheduler 第五 cron `evolution_shadow_run` 22:00 mon-fri 触发

#### 状态回滚:文件式 prompt registry + git + restart

- 新增 `config/prompts/{agent}/{version}.yaml` + `config/prompts.lock.json` + `data/rag/{source}/{date}/{doc_id}.md` + `data/rag/provenance.jsonl`
- 版本切换 = `git commit` + amendment + restart;严禁 hot-reload
- 严禁引入 MLflow/LangSmith 等外部 hosted prompt registry

#### Risk_proposals 合并 P2-2 体系

- P0-7 §1.4 risk_parameter_proposals 走 P2-2 同款 cron + shadow validate + 飞书通知 + 自动起草 P0-7-amendment
- 派生 P0-7-amendment-2026-05-11

#### Audit 27→34 类(新增 7 类自进化生命周期归类 5)

- `prompt_version_pinned` / `prompt_version_rolled_back` / `rag_document_ingested` / `rag_document_rejected_non_whitelist` / `shadow_evolution_run_completed` / `evolution_amendment_drafted` / `evolution_feishu_notified`
- actor=SYSTEM 或 SCHEDULER;严禁 LLM/FRONTEND_USER/FEISHU_USER 直接写
- 派生 P1-6-amendment-2026-05-11(第 3 次 amendment)

#### 实施期 deferred 等用户计划文档

- 28 实施期任务 H-001 ~ H-028 完整列出但顶部明标"等用户在 dedicated 计划文档 session 锁定推进时间表"
- 本 session 严禁写任何自进化代码
- Codex review 5 轮 R1-R5 在 Phase X 实施期跑;本 session 不跑

## P2-3. 移动端或远程访问 ✅ superseded by P1-6 §1.5 (2026-05-10)

> 实质决策已在 P1-6 §1.5 决策时吸收;详见 `docs/decisions/P1-6-secrets-shell-env-12month-event-driven-rotation-loopback-only-no-local-auth-audit-mongo-jsonl-dual-write.md` §1.5
> 等价含义:移动端 Web UI 不开放(127.0.0.1 only + 严禁 LAN/公网 + 远程仅 SSH tunnel)
> 移动端依赖飞书交互(继承 P0-1 §1.3 + 用户原决策建议倾向)
> 不需要独立 P2-3 决策文档;P2 收官见 `docs/decisions/P2-decisions-finalization-2026-05-10.md` §3

### 原始决策点(已被 P1-6 §1.5 取代)

#### 你需要决定

是否需要在外部网络访问 Web UI，或者完全通过飞书交互。

#### 建议倾向

第一阶段 Web UI 保持本机/内网，移动侧主要依赖飞书。

## P2-4. 告警渠道 ✅ 已锁定 2026-05-10(派生 P1-6 二次 amendment 26 → 27 类 AuditEventType)

> 决策定稿: `docs/decisions/P2-decisions-finalization-2026-05-10.md` §4
> 派生 amendment: `docs/decisions/P1-6-amendment-2026-05-10-audit-eventtype-27.md`(新增 `EXECUTION_REPORT_PARSE_FAILED`)
> 锁定结果: 7/8 类用户列出事件已被 P1-6 + P1-7 累积覆盖(行情/资讯断流→DATA_QUALITY_BREACH;LLM 全停→LLM_CALL_TIMEOUT_30S+DAILY_COST_CEILING_20CNY_BREACHED;指令生成失败→BUILDER_EARLY_RETURN;风控拦截→RISK_ENGINE_CHECK_REJECTED;模拟账户异常→MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL+STATE_MACHINE_ILLEGAL_TRANSITION;日终对账→EOD_PIPELINE_FAILED+RECONCILIATION_TICKET_OPEN_OR_EXPIRED);唯一遗漏"飞书回报解析失败"由本 amendment 补 EXECUTION_REPORT_PARSE_FAILED 解决(归类 4 异常+拦截;reason_namespace='execution_report_ambiguous';澄清飞书走 P0-2 §1.2 主路径长连接 + P0-4 §1 五模板预写死,严禁走 P0-2 §2.5 备用 webhook)。告警通道维持 P1-7 §1.7 锁定:仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP/Slack/Discord 第二通道(继承 P1-6 §1.1 凭证池仅 LLM 3+飞书 6 锁状态)。
>
> **决策对齐期 P0+P1+P2 全完成 ✅**(P2-2 dedicated session 2026-05-11 完成锁定:保守 3 路径 + 全人工 gate + 飞书主动通知 + 45 日 shadow validate + 文件式 prompt registry;派生 3 amendment;实施期 deferred 等用户计划文档)→ 下一站等用户在 dedicated 计划文档 session 锁定推进时间表 → Phase A 实施期(代码迁移合并 P1-5 + P1-6 + P1-7 + P0-1 旧矩阵删除)+ Phase X 实施期(自进化 28 任务 H-001~H-028;Codex review 5 轮 R1-R5)

### 原始决策点(已被本决策取代)

#### 你需要决定

哪些事件必须飞书提醒:

- 行情源断流。
- 资讯源失败。
- LLM 全部不可用。
- 指令生成失败。
- 风控拦截。
- 模拟账户异常。
- 飞书回报解析失败。
- 日终对账缺失。

#### 建议倾向

把“告警”和“交易指令”分开，避免你在飞书里混淆。

## 5. 建议的决策顺序

建议按以下顺序推进:

1. 锁定两种运行模式和禁用真实券商 API 红线。
2. 确认飞书接入方式: 自建应用、长连接/回调、权限、群。
3. 定义 `InstructionPlan` 和飞书指令模板。
4. 定义用户回报语法和对账规则。
5. 定义初始账户镜像和风险红线。
6. 定义 `simulation_auto` 验收标准。
7. 定义第一阶段标的范围和频率。
8. 定义数据/资讯源可信度和异常停发规则。
9. 再开始核心代码重构。

## 6. 建议近期完成的 6 个输出物

### 1. 运行模式红线

目标: 防止项目继续滑向真实自动下单。
建议字段: 模式名、允许动作、禁止动作、状态来源、验收指标。

### 2. 飞书接入确认表

目标: 明确能否做双向飞书机器人。
建议字段: App ID、机器人能力、权限、群 ID、接收方式、回调方式、消息模板。

### 3. 操作指令模板

目标: 让飞书消息可以直接指导手工操作。
建议字段: 指令编号、标的、方向、数量、价格、有效期、风控、数据时间、回报模板。

### 4. 回报与对账模板

目标: 让系统能可靠更新账户镜像。
建议字段: 已执行、部分执行、未执行、更正、日终对账。

### 5. 风险红线确认表

目标: 限制系统对真实资金的影响。
建议字段: 单股仓位、总仓位、单次金额、每日指令数、亏损暂停线。

### 6. simulation_auto 验收标准

目标: 用完全自动模拟闭环检验平台实战能力。
建议字段: 连续天数、收益/回撤、指令完整率、数据质量、复盘完整率。

## 7. 最后提醒

新路线下，QuantMind 最大的工程难点不是“能不能自动下单”，而是:

- 能不能把真实数据和资讯变成结构化、可执行、可复盘的操作指令。
- 能不能用自动化模拟闭环证明这些指令有基本实战能力。
- 能不能通过飞书把“系统建议、用户手工执行、用户回报、系统状态更新”做成低歧义闭环。

只有这三件事可靠，飞书里的具体买卖指令才有工程意义。
