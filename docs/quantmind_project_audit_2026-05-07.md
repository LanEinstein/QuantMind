# QuantMind 项目调查报告

生成日期: 2026-05-07
重构日期: 2026-05-08
调查范围: 当前仓库代码、配置、前端、测试与项目文档；补充联网核验飞书双向机器人、飞书消息发送/接收和 AKShare A 股实时行情接口资料。未连接真实外部行情、飞书、LLM、券商、MongoDB 或 Redis 做运行态验证。
重要说明: 仓库中存在晚于当前日期 2026-05-07 的阶段总结文档，例如 `docs/reviews/phase5a-summary-2026-05-08.md`、`docs/reviews/phase5b-summary-2026-05-15.md`。本报告把这些文件视为仓库内计划或生成材料，不把其中的未来日期试运行结论当成已发生事实。

## 1. 最新总体结论

项目方向已在 2026-05-08 重构:

> QuantMind 不再以真实券商自动化或半自动化操作为目标。新的目标是“真实行情/资讯驱动的模拟实盘能力验证 + 飞书交互人工执行闭环”。

新系统必须支持两种模式:

| 模式 | 名称 | 目标 | 真实券商 API | 真实下单 |
|---|---|---|---|---|
| 模式 A | `simulation_auto` 完全自动化模拟全流程 | 让系统自己完成从分析、指令、风控、模拟成交到复盘的闭环，用于检验平台实战能力 | 不接 | 不下 |
| 模式 B | `feishu_interactive` 飞书交互人工执行 | 系统给飞书群发送具体买/卖指导；用户在券商 APP 手动执行；用户飞书回报；系统更新状态镜像并继续监控 | 不接 | 用户手动 |

当前代码已经具备一个较完整的“个人 A 股多 Agent 分析与模拟交易原型”骨架，包括:

- FastAPI 后端。
- Vue/Element Plus/ECharts 前端。
- MongoDB/Redis 数据层。
- `adata` / `akshare` / `baostock` 行情与新闻采集。
- LLM Router。
- LangGraph 多 Agent 分析。
- MockBroker。
- 风控模块。
- SSE/WS 实时展示。
- 成本守门、授权红线、影子对比等评估基础设施。

但它距离新定位仍有关键缺口:

- 只有粗粒度 `TradingSignal`，没有可执行的 `InstructionPlan`。
- 分析信号没有接到模拟订单执行。
- MockBroker 状态只在内存，不适合检验长期实战能力。
- RiskEngine 存在但没有贯穿信号到模拟执行链路。
- 没有飞书发送、接收、解析、去重、回执处理模块。
- 没有用户回报驱动的真实账户状态镜像。
- 没有把“系统模拟状态”和“用户回报状态”分成两个账户体系。
- 前端仍保留旧的 suggest/confirm/auto 交易语义，容易误导。
- 数据源和资讯源能支撑原型，但缺少“发出飞书买卖指令”所需的数据质量保护。

## 2. 新目标架构

推荐目标架构:

```text
真实行情/资讯
  -> 数据质量检查
  -> Watchlist / 候选池
  -> 多 Agent 分析
  -> TradingSignal
  -> InstructionPlan 结构化操作计划
  -> RiskEngine + 仓位计算 + 指令完整性校验
  -> ModeRouter

Mode A: simulation_auto
  -> SimulationExecutor
  -> PersistentMockBroker
  -> simulated_portfolio_snapshots
  -> PnL / 回撤 / 复盘

Mode B: feishu_interactive
  -> FeishuMessenger
  -> 飞书群操作指令
  -> 用户券商 APP 手工执行
  -> 用户飞书回报
  -> FeishuEventReceiver
  -> ExecutionReportParser
  -> UserReportedPortfolio
  -> 继续监控 / 调整 / 复盘
```

核心原则:

- 两种模式共用同一套 `InstructionPlan`、风控、仓位和证据链。
- `simulation_auto` 自动执行只允许进入模拟账户。
- `feishu_interactive` 不调用券商接口，只根据用户回报更新状态镜像。
- 飞书回报解析不清时必须追问，不能猜测并写入持仓。
- 所有指令、飞书消息、模拟成交、用户回报和状态变更都必须进入统一账本。

## 3. 当前系统模块连接方式

### 3.1 后端启动链路

入口是 `backend/main.py`。FastAPI lifespan 启动时依次初始化:

- 日志系统。
- 授权阶段红线: `backend/services/authorization.py`。
- Redis。
- `LLMRouter`: `backend/llm/router.py`，读取 `config/agent_models.yaml`。
- MongoDB 数据服务: `backend/data/database.py`。
- 行情服务: `backend/data/market_data.py`。
- 历史数据服务: `backend/data/history_data.py`。
- 新闻服务: `backend/data/news_crawler.py`。
- 数据调度器: `backend/data/scheduler.py`。
- Watchlist 服务: `backend/data/watchlist.py`。
- BrokerRegistry、MockBroker、ApprovalQueue、CircuitBreaker。
- AnalysisScheduler: `backend/data/analysis_scheduler.py`。
- WebSocket Redis 转发任务: `backend/api/websocket.py`。

没有初始化:

- Feishu client。
- Feishu event receiver。
- Instruction planner。
- Simulation executor。
- User-reported portfolio service。
- Decision ledger。

### 3.2 数据与分析主链路

```text
外部免费数据源
  -> adata / akshare / baostock
  -> MarketDataService / HistoryDataService / NewsCrawlerService
  -> MongoDB 持久化 + Redis Pub/Sub
  -> FastAPI REST / WebSocket
  -> Vue 前端展示

watchlist 或手动输入股票
  -> AnalysisScheduler 或 /api/analysis/*
  -> LangGraph 多 Agent 分析
  -> LLMRouter 按 agent_models.yaml 路由模型
  -> TradingSignal
  -> trading_signals + analysis_records
  -> SSE/REST 前端展示
```

### 3.3 与新目标的错位

当前链路结束在 `TradingSignal`。新目标要求继续生成:

```text
TradingSignal
  -> InstructionPlan
  -> RiskEngine.validate_order
  -> simulation_auto: MockBroker 自动模拟成交
  -> feishu_interactive: 飞书指令 + 用户回报解析
```

这段新主链路当前不存在。

## 4. 行情和资讯现状

### 4.1 当前行情实现

行情服务位于 `backend/data/market_data.py`。

- 指数实时行情:
  - 主源: `adata.stock.market.get_market_index_current`
  - 备用: `akshare.index_zh_a_hist` 取历史日线最后一行近似当前行情
  - API: `GET /api/market/indices`

- 单股实时行情:
  - 主源: `adata.stock.market.list_market_current(code_list=[code])`
  - 备用: `akshare.stock_zh_a_spot_em()` 后按代码过滤
  - API: `GET /api/market/stock/{code}`

- 多股实时行情:
  - `MarketDataService.get_stock_list_realtime(codes)` 已存在，但后台调度未用于 watchlist 定时快照。

- 板块:
  - `akshare.stock_board_industry_name_em`
  - API: `GET /api/market/sectors`

- 北向资金:
  - `akshare.stock_hsgt_hist_em(symbol="北向资金")`
  - API: `GET /api/market/capital-flow`

- 历史 K 线:
  - 主源: `adata.stock.market.get_market`
  - 备用: `baostock.query_history_k_data_plus`
  - API: `GET /api/market/kline/{code}`

`config/data_sources.yaml` 中行情刷新间隔为 30 秒。`backend/data/scheduler.py` 在交易时段每 30 秒只拉取三大指数，写入 `market_realtime` 并推送 WebSocket；没有定时采集全 A 或 watchlist 个股。

### 4.2 联网调研补充

AKShare 官方文档列出 `stock_zh_a_spot_em`，描述为东方财富网沪深京 A 股实时行情数据，单次返回所有沪深京 A 股上市公司实时行情数据，字段包括代码、名称、最新价、涨跌幅、成交量、成交额、换手率、市盈率、市净率等。

这说明免费源可以支持原型级全市场快照，但并不自动解决:

- 延迟确认。
- 断流监控。
- 字段稳定性。
- 授权边界。
- 与备用源对账。
- 停复牌、涨跌停、节假日、异常价格处理。

### 4.3 当前资讯实现

新闻服务位于 `backend/data/news_crawler.py`。

- 最新财经新闻:
  - `akshare.stock_news_em(symbol="")`。
  - 来源标记为 Eastmoney。
  - 后台每 5 分钟执行一次。
  - 保存到 `news_articles`。

- 个股新闻:
  - `akshare.stock_news_em(symbol=code)`。

- 股票代码识别:
  - 正则提取 6 位 A 股代码。

LLM 新闻分析和情绪分析已有 agent，但新闻评分、事件聚类、证据链持久化仍不足。

### 4.4 与新目标的差距

飞书指令比普通投研展示要求更高。每条指令必须知道:

- 使用了哪个行情源。
- 数据时间戳。
- 数据是否新鲜。
- 当前是否交易日/交易时段。
- 是否停牌。
- 是否涨跌停。
- 是否源间偏差过大。
- 哪些资讯支持或否定该指令。

当前系统没有这些保护。因此短期只能低频、watchlist 内、数据质量达标时才发飞书指令。

## 5. 多 Agent 与 LLM 决策现状

### 5.1 当前实现

多 Agent 编排在 `backend/agents/graph.py`:

1. 并行执行:
   - `news_crawler`
   - `sentiment_analyst`
   - `fundamental_analyst`
   - `technical_analyst`

2. `intelligence_officer` 汇总新闻、情绪、基本面、技术面和大盘/北向资金。

3. `bull_researcher` 与 `bear_researcher` 辩论。

4. `risk_officer` 输出风险评估。

5. `fund_manager` 输出 JSON，并解析为:

```json
{
  "action": "买入/持有/卖出",
  "target_price": 0,
  "confidence": 0.0,
  "risk_score": 0.0,
  "reasoning": "..."
}
```

对应模型是 `backend/agents/models.py::TradingSignal`。

### 5.2 关键问题

`TradingSignal` 不是可执行指令。它缺少:

- 买卖股数。
- 目标仓位。
- 限价。
- 有效期。
- 触发条件。
- 不执行条件。
- 止损/止盈/失效规则。
- 数据快照 ID。
- 证据 ID。
- 风控校验结果。
- 指令编号。
- 飞书回报模板。

因此当前系统不能直接发出“买入哪几只、买多少、卖多少”的飞书指导。

### 5.3 推荐改造

新增 `InstructionPlan`，由确定性代码和 LLM 协作生成:

```text
TradingSignal
  -> StrategyPolicy / PositionSizer
  -> InstructionPlanDraft
  -> Pydantic 严格校验
  -> RiskEngine
  -> Final InstructionPlan
```

LLM 可参与解释和草案，但仓位、风险红线、指令完整性必须由代码控制。

## 6. 模拟交易能力现状

### 6.1 已有能力

`backend/broker/mock_broker.py` 实现了 MockBroker:

- 初始资金。
- 买卖订单。
- 交易时段校验。
- A 股 100 股整数倍。
- T+1 可卖限制。
- 手续费、印花税、滑点。
- 持仓、订单、成交查询。

前端 `Portfolio.vue` 能展示账户、持仓、订单、成交和审批队列。

### 6.2 主要问题

- MockBroker 状态只在内存中，后端重启即丢。
- 持仓市值使用成本价近似，没有实时 mark-to-market。
- `advance_day()` 没有接入真实日切调度。
- 交易 API 只能查询和审批已有 pending approval，没有从信号生成 pending approval 的公开路径。
- `AnalysisScheduler` 保存信号后没有调用 MockBroker。
- `RiskEngine.validate_order` 没有贯穿模拟订单路径。

### 6.3 对 `simulation_auto` 的影响

当前 MockBroker 是很好的起点，但还不是“完全自动化模拟全流程”。要验证平台实战能力，至少要补:

- `Signal -> InstructionPlan`。
- `InstructionPlan -> RiskEngine`。
- `RiskEngine -> SimulationExecutor`。
- `SimulationExecutor -> PersistentMockBroker`。
- 模拟成交、持仓、现金、净值入库。
- 实时行情估值。
- 每日盘后复盘。

## 7. 飞书交互能力现状

### 7.1 当前代码事实

仓库没有 Feishu/Lark SDK 依赖，没有专门的飞书模块。

已有的相关能力只有 `backend/monitoring/alerter.py`:

- 读取 `ALERT_WEBHOOK_URL`。
- 向 webhook 发送结构化告警 payload。
- 做 cooldown 限流。
- 失败时避免把 webhook URL 打进日志。

这个 alerter 适合外部告警，不适合飞书交易闭环:

- 只能单向发送。
- 不知道飞书 App ID / App Secret。
- 不获取 `tenant_access_token`。
- 不发送 `im/v1/messages`。
- 不接收 `im.message.receive_v1`。
- 不解析用户回报。
- 不记录 message_id、event_id、chat_id。

### 7.2 联网调研结论

飞书双向交互至少需要:

- 企业自建应用。
- 机器人能力。
- App ID / App Secret。
- 发送消息 API，例如 `im/v1/messages`。
- 目标群 `chat_id`。
- 接收消息事件 `im.message.receive_v1`。
- 事件接收方式: 长连接或开发者服务器回调。
- 若使用按钮/卡片: 消息卡片和卡片回调。

自定义机器人 Webhook 适合单向推送到群，不适合作为接收你“我已买入/卖出”回报的核心路径。

### 7.3 推荐新增模块

```text
backend/integrations/feishu/
  client.py              # token 获取、发送消息、重试、限流
  events.py              # 接收 im.message.receive_v1 / card.action.trigger
  schemas.py             # 飞书事件与消息模型
  renderer.py            # InstructionPlan -> 文本/卡片
  parser.py              # 用户回报 -> ExecutionReport
  dedupe.py              # event_id/message_id 去重
```

需要新增 API 或后台 worker:

- `/api/integrations/feishu/status`
- `/api/integrations/feishu/test-send`
- `/api/integrations/feishu/events`，如果采用 HTTPS 回调。
- 长连接 worker，若采用 SDK 长连接。

## 8. 风控和保险机制现状

### 8.1 已有能力

项目已有:

- `RiskEngine`: 股票代码、价格偏离、股数、资金、仓位、持仓数量、交易时间检查。
- `CircuitBreaker`: 亏损和连续亏损熔断。
- `ApprovalQueue`: 内存审批队列。
- `StopLoss` 函数。
- 授权模式红线: `backend/services/authorization.py`。

### 8.2 与新目标的错位

旧的授权模式是 `suggest` / `confirm` / `auto`，并按 `phase5_eval`、`phase6_prep`、`phase6_dryrun`、`phase7_live` 限制。这套命名仍暗示未来可进真实实盘 `auto`。

新目标应改为:

```text
run_mode:
  - simulation_auto
  - feishu_interactive

execution_target:
  - mock_broker
  - feishu_instruction
```

旧的 `auto` 如果保留，只能映射到 `simulation_auto`，不能再代表真实自动交易。

### 8.3 关键缺口

- RiskEngine 未接入 `TradingSignal -> InstructionPlan -> 执行`。
- CircuitBreaker 未从模拟成交或用户回报 PnL 自动更新。
- StopLoss 没有后台扫描任务。
- ApprovalQueue 的旧语义应降级，不应再围绕真实订单审批。
- 飞书回报歧义没有 fail-closed 机制。

## 9. 记录、复盘与账本现状

### 9.1 已有记录

MongoDBService 创建和使用:

- `trading_signals`
- `analysis_records`
- `news_articles`
- `market_realtime`
- `index_prices`
- `cost_tracking`
- `simulations`
- `shadow_decisions`

MockBroker 内部记录:

- orders。
- trades。
- positions。
- account info。

### 9.2 主要问题

- MockBroker 订单、成交、持仓不入库。
- 没有 `instruction_plans`。
- 没有 `execution_reports`。
- 没有 `feishu_messages`。
- 没有用户回报账户镜像。
- 信号、指令、模拟成交、飞书消息、用户回报、收益之间没有强绑定。
- Performance API 的净值计算仍需修正，不能作为新验收依据。

### 9.3 推荐账本

新增统一 `decision_ledger`:

```text
decision_id
run_id
signal_id
instruction_id
mode
stock_code
trade_date
strategy_id
data_snapshot_id
evidence_ids
instruction_plan
risk_validation
simulation_order_id
feishu_message_id
execution_report_id
portfolio_snapshot_before
portfolio_snapshot_after
realized_or_marked_pnl
review_notes
```

## 10. MiroFish 与隐性信息推演现状

### 10.1 已有代码

MiroFish 相关代码位于 `backend/mirofish/`:

- `simulator.py`
- `event_filter.py`
- `extractors/hidden_variables.py`
- `extractors/inflection_points.py`
- `extractors/extreme_scenarios.py`
- `formatter.py`

配置在 `config/mirofish.yaml`。前端 `Simulation.vue` 能展示仿真历史和结果。

### 10.2 未真正接线

`backend/agents/intelligence_officer.py` 只有当 `services.mirofish_simulator is not None` 时才会运行 MiroFish。

但 `backend/main.py` 和 `backend/api/analysis.py` 构造 `AnalysisServices` 时没有创建并传入 `MiroFishSimulator`。

因此当前默认运行时不会触发 MiroFish 仿真，`/api/simulation/*` 也只是浏览已有 `simulations` 集合，不提供“启动一次仿真”的接口。

### 10.3 新定位下的角色

MiroFish 不应直接给出买卖股数。它适合:

- 重大事件后的情绪演化推演。
- 隐性变量识别。
- 极端场景辅助。
- 盘后复盘。

它的输出应成为 `InstructionPlan` 的证据之一，而不是最终指令。

## 11. 前端现状

### 11.1 已有页面

前端包括:

- Dashboard: 大盘监控。
- AgentDebate: Agent 辩论和分析历史。
- Simulation: MiroFish 仿真展示。
- Portfolio: 组合管理。
- Performance: 绩效报告。
- RiskCenter: 风控中心。
- Settings: LLM、数据源、MiroFish、成本配置。

### 11.2 与新目标的错位

- `DecisionCard.vue` 仍显示“建议模式 / 确认模式 / 自动模式”。
- `AgentDebate.vue` 的批准/拒绝只是 toast，不调用后端。
- Portfolio 展示 MockBroker，但不是持久模拟账户。
- 没有 `InstructionPlan` 页面。
- 没有飞书发送状态、回报解析、对账页面。
- 没有区分 `simulation_auto` 和 `feishu_interactive` 的模式视图。
- 开发环境仍有 mock fallback，可能掩盖真实链路未接通。

### 11.3 推荐改造

新增或重命名:

- `InstructionCenter`: 操作指令中心。
- `SimulationLedger`: 完全自动模拟账户账本。
- `FeishuConsole`: 飞书指令、发送状态、用户回报、解析结果。
- `PortfolioMirror`: 用户回报账户镜像。
- `DataQuality`: 行情/资讯/LLM/飞书连接状态。

旧按钮:

- “自动模式”改为“自动模拟”。
- “确认模式”改为“飞书人工执行”或直接移除。
- “批准执行”不得暗示真实下单。

## 12. 关键差距清单

| 领域 | 当前状态 | 新目标差距 | 优先级 |
|---|---|---|---|
| 运行模式 | suggest/confirm/auto + phase 红线 | 需要改成 simulation_auto / feishu_interactive | P0 |
| 指令结构 | TradingSignal 粗粒度信号 | 缺可执行 InstructionPlan | P0 |
| 模拟闭环 | MockBroker 独立内存能力 | 缺信号到模拟执行、持久化、估值、复盘 | P0 |
| 飞书发送 | 只有通用 webhook alerter | 缺 Feishu app client、消息模板、重试、message_id 记录 | P0 |
| 飞书接收 | 无 | 缺事件接收、去重、解析、澄清 | P0 |
| 用户回报账户 | 无 | 缺 UserReportedPortfolio 和日终对账 | P0 |
| 风控 | RiskEngine 存在 | 未接入指令与执行链路 | P0 |
| 数据质量 | 免费源 + 指数定时采集 | 缺 watchlist 快照、质量评分、停发规则 | P0 |
| 新闻资讯 | Eastmoney 口径为主 | 源少，事件层和证据链不足 | P1 |
| 前端 | 页面较完整 | 缺新模式工作台，旧按钮误导 | P1 |
| 复盘 | 信号历史、影子对比雏形 | 缺指令-成交-回报-PnL 强绑定 | P1 |
| MiroFish | 代码存在，默认未注入 | 应降级为事件证据/复盘辅助 | P2 |

## 13. 推荐修改路线图

### Phase A: 术语和红线重置

目标: 从代码、配置、前端、文档里移除旧的真实实盘升级暗示。

- 新增 `run_mode = simulation_auto | feishu_interactive`。
- 废止当前阶段的 `phase7_live` 目标。
- 保留 `AUTHORIZATION_MODE` 时也必须限制为历史兼容层，不再作为产品主语义。
- 前端不再显示“真实自动提交”类措辞。

### Phase B: InstructionPlan 与统一账本

目标: 把 LLM 信号变成结构化、可校验、可复盘的操作计划。

- 新增 `InstructionPlan` schema。
- 新增 `instruction_plans` collection。
- 新增 `decision_ledger`。
- 每条指令绑定:
  - signal_id。
  - run_id。
  - data_snapshot_id。
  - evidence_ids。
  - risk_validation。
  - mode。

### Phase C: simulation_auto 全自动模拟闭环

目标: 用真实数据输入检验系统实战能力，但只在模拟账户自动执行。

- 新增 `SimulationExecutor`。
- 接入 `RiskEngine.validate_order`。
- MockBroker 持久化。
- 实时行情 mark-to-market。
- 日切任务。
- PnL、回撤、换手、胜率、盈亏比。
- 盘后复盘报告。

### Phase D: 飞书出站指令

目标: 系统能把 `InstructionPlan` 渲染为你可直接照做的飞书消息。

- 新增 Feishu client。
- 获取和缓存 `tenant_access_token`。
- 支持 `chat_id` 发送文本/富文本/卡片。
- 记录 `message_id`。
- 失败重试和限流。
- 消息必须包含指令编号、股数、价格、有效期、风险、回报模板。

### Phase E: 飞书入站回报

目标: 系统能接收你的手工操作回报并更新状态镜像。

- 接入 `im.message.receive_v1`。
- 优先用长连接；若用 HTTPS 回调，则加验签和重放防护。
- 按 `event_id` / `message_id` 去重。
- `ExecutionReportParser` 规则优先。
- 解析失败自动发澄清消息。
- 解析通过后写入 `execution_reports`。
- 更新 `UserReportedPortfolio`。

### Phase F: 数据质量和指令停发规则

目标: 防止脏数据驱动飞书真实操作指导。

- Watchlist 个股定时快照。
- 行情源延迟监控。
- 源间价格偏差检查。
- 交易日历。
- 停牌/涨跌停/异常价格保护。
- 新闻源状态和发布时间检查。
- 数据不达标时只发告警，不发买卖指令。

### Phase G: 前端工作台

目标: 让两种模式可观察、可审计。

- InstructionCenter。
- SimulationLedger。
- FeishuConsole。
- PortfolioMirror。
- DataQuality。
- ReviewReport。

### Phase H: 验收与复盘

目标: 判断系统是否真的能用。

- 连续运行报告。
- 指令完整率。
- 模拟账户 PnL。
- 用户回报解析准确率。
- 数据质量统计。
- 飞书发送/接收成功率。
- 指令后 1/5/20 日收益归因。

## 14. 近期最优先的工程任务

1. 新增 `InstructionPlan` schema 和 `instruction_plans` collection。
2. 实现 `TradingSignal -> InstructionPlan` 转换，先只保存，不执行。
3. 将 `RiskEngine.validate_order` 接入 `InstructionPlan`。
4. 持久化 MockBroker 账户、订单、成交、持仓。
5. 实现 `SimulationExecutor`，打通 `simulation_auto`。
6. 建立 `decision_ledger`，把分析、信号、指令、风控、模拟成交绑定。
7. 新增 Feishu client，先实现测试群发送。
8. 新增飞书指令模板，发送 `InstructionPlan`。
9. 接入飞书 `im.message.receive_v1`，先把消息原文入库。
10. 实现严格文字回报解析和澄清机制。
11. 新增 `UserReportedPortfolio` 与日终对账模板。
12. 改前端文案和入口，移除“真实自动/半自动实盘”暗示。

## 15. 当前不建议做的事

在新路线下，以下工作应暂停或删除出主路线:

- QMT、Ptrade、vn.py 等真实券商下单适配器。
- Windows 券商客户端桥接。
- 真实账户只读同步。
- 真实自动撤单/下单/成交回报。
- 以 `phase7_live` 为目标的阶段计划。
- 让前端“批准/拒绝”暗示真实下单。
- 让 LLM 直接决定最终股数和风控红线。

这些不是永久不能做，而是不属于当前最新定位。

## 16. 联网调研入口

飞书:

- 发送消息 API: https://open.feishu.cn/document/server-docs/im-v1/message/create
- 接收消息事件: https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
- 事件订阅与长连接: https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/choose-a-subscription-mode
- 自定义机器人: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot

行情/数据:

- AKShare 股票数据: https://akshare.akfamily.xyz/data/stock/stock.html
- AKShare GitHub: https://github.com/akfamily/akshare
- adata GitHub: https://github.com/1nchaos/adata
- BaoStock: https://baostock.com/

## 17. 最终判断

QuantMind 现在最接近的是“多 Agent 投研信号系统 + 内存模拟交易展示”。要达到新目标，下一步不应继续增加 Agent 或追求实盘接口，而应先把两条闭环做硬:

1. `simulation_auto`: 真实数据输入下，系统能自动形成结构化指令、过风控、在持久模拟账户执行、产出可复盘结果。
2. `feishu_interactive`: 系统能把同样的结构化指令发给你，并可靠接收你的手工执行回报，维护用户回报账户镜像。

这两条闭环完成后，项目才真正符合新的定位。
