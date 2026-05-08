# QuantMind 项目调查报告

生成日期: 2026-05-07  
调查范围: 当前仓库代码、配置、前端、测试与项目文档。未连接真实外部行情、券商、LLM 或数据库做运行态验证。  
重要说明: 仓库中存在晚于当前日期 2026-05-07 的阶段总结文档，例如 `docs/reviews/phase5a-summary-2026-05-08.md`、`docs/reviews/phase5b-summary-2026-05-15.md`。本报告把这些文件视为仓库内计划或生成材料，不把其中的未来日期试运行结论当成已发生事实。

## 1. 总体结论

当前项目已经具备一个较完整的“个人 A 股多 Agent 分析与模拟交易原型”骨架，包括 FastAPI 后端、Vue/Element Plus/ECharts 前端、MongoDB/Redis 数据层、行情/新闻采集、LLM Router、LangGraph 多 Agent 分析、MockBroker、风控模块、SSE/WS 实时展示和若干监控接口。

但它还不是最初设想中的“基于大模型隐性信息汇总推演机制的全自动与自进化量化交易平台”。当前真实能力更接近:

- 对人工输入或 watchlist 中的股票做多 Agent 分析。
- 输出 `买入/持有/卖出` 建议信号。
- 记录分析过程和信号。
- 提供 MockBroker 级别的内存模拟账户展示。
- 提供部分成本、健康、风控、影子对比工具。

核心缺口是:

- 没有真正的全市场自动选股引擎。
- 没有信号到订单的执行闭环。
- 没有实盘券商接入实现。
- 没有严谨回测与复盘体系。
- 风控和止损存在模块，但没有完整接入交易执行链路。
- MiroFish 隐性变量仿真适配器存在，但运行时未实例化接入主分析服务。
- 前端界面有较完整外观和页面，但多个关键按钮、统计和工作流仍是展示层或 mock/半接线状态。
- “自进化”目前基本没有实现，只有信号命中率、影子对比、成本监控等评估基础设施雏形。

## 2. 当前系统模块连接方式

### 2.1 后端启动链路

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

### 2.2 数据与分析主链路

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

### 2.3 交易链路现状

```text
TradingSignal
  -> 当前仅保存为信号和展示
  -> 未自动生成 Order
  -> 未进入 RiskEngine.validate_order
  -> 未进入 ApprovalQueue.submit
  -> 未进入 MockBroker.place_order
```

交易侧现有链路是另一套独立能力:

```text
前端 Portfolio 页面
  -> /api/trading/accounts|positions|orders|trades|approve|reject
  -> BrokerRegistry
  -> MockBroker 内存撮合
```

关键问题是分析信号与交易执行没有真正接起来。

## 3. 平台如何获取 A 股实时数据

### 3.1 当前实现

行情服务位于 `backend/data/market_data.py`。

- 指数实时行情:
  - 主源: `adata.stock.market.get_market_index_current`
  - 备用: `akshare.index_zh_a_hist` 取历史日线最后一行近似当前行情
  - API: `GET /api/market/indices`

- 单股实时行情:
  - 主源: `adata.stock.market.list_market_current(code_list=[code])`
  - 备用: `akshare.stock_zh_a_spot_em()` 后按代码过滤
  - API: `GET /api/market/stock/{code}`

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

- 财务指标:
  - 主源: `adata.stock.finance.get_core_index`
  - 备用: `akshare.stock_zh_a_spot_em`
  - API: `GET /api/market/financial/{code}`

配置在 `config/data_sources.yaml`:

- 行情刷新间隔: 30 秒。
- 新闻刷新间隔: 300 秒。
- 行情主源: adata。
- 行情备用源: akshare。
- 历史数据备用源: baostock。

`backend/data/scheduler.py` 会在交易时段每 30 秒拉取三大指数，写入 MongoDB `market_realtime`，并通过 Redis 发布给 WebSocket。注意这里不是全市场实时快照，也不是 watchlist 每只股票的定时快照。

### 3.2 主要问题

- 没有全 A 股票池实时扫描。后台调度只定时采集三大指数，不采集全市场和 watchlist 个股。
- 没有 Level-2、盘口、逐笔、委托簿等实盘交易必需的高频数据。
- 没有完整交易日历。`backend/data/trading_hours.py` 只按周一到周五判断，不处理中国法定节假日、调休、临时休市。
- `akshare`、`adata` 这类免费源适合研究和辅助分析，不适合作为无人值守实盘交易的唯一数据源。
- 前端 Dashboard 的涨跌家数、涨停跌停统计目前没有后端真实接口，开发环境中由 `frontend/src/stores/market.ts` mock。
- 没有行情质量监控，例如延迟、缺字段、异常跳变、源间对账、停牌处理、复权一致性。

### 3.3 优化方向

- 建立 `market_universe` 与 `watchlist_quotes` 两类快照。
- 给 watchlist 个股增加定时行情采集和入库，而不只是在分析时临时拉取。
- 增加 A 股交易日历，至少用本地可更新日历表处理节假日。
- 对行情源做质量评分: 可用率、延迟、字段完整率、价格跳变、与备用源偏差。
- 在实盘阶段引入稳定券商或行情服务接口。免费源可以保留为研究/冗余源，但不应作为自动下单唯一依据。
- 将全市场选股需要的日线、成交额、换手率、涨跌幅、行业分类、停复牌状态形成标准化数据表。

## 4. 平台如何获取新闻或其他资讯，并用于推断选股

### 4.1 当前实现

新闻服务位于 `backend/data/news_crawler.py`。

- 最新财经新闻:
  - 使用 `akshare.stock_news_em(symbol="")`，来源标记为 eastmoney。
  - 后台每 5 分钟执行一次。
  - 保存到 MongoDB `news_articles`。
  - API: `GET /api/news/latest`

- 个股新闻:
  - 使用 `akshare.stock_news_em(symbol=code)`。
  - API: `GET /api/news/stock/{code}`

- 股票代码识别:
  - 用正则从标题和正文里提取 6 位 A 股代码。

LLM 新闻分析位于 `backend/agents/news_crawler.py`:

- 对目标股票相关的新闻调用 `news_crawler` agent。
- 该 agent 在 `config/agent_models.yaml` 中配置为 DeepSeek 主用、Qwen 备用。
- Prompt 要求摘要、分类、重要性评分和利多利空判断。

情绪分析位于 `backend/agents/sentiment_analyst.py`:

- 拉取 30 条综合新闻和 20 条个股新闻。
- 把新闻标题交给 LLM 判断市场情绪。

### 4.2 主要问题

- 当前真实资讯源很窄，基本只有 Eastmoney 口径的 akshare 新闻接口。
- 没有公告、财报原文、交易所问询、研报、宏观数据、资金流细项、龙虎榜、社交媒体、政策文件等多源资讯。
- 数据层的 `NewsArticle.importance_score` 默认是 0。LLM 在分析时会评分，但评分没有回写到新闻集合。因此前端 Dashboard 标注“DeepSeek 重要性评分”并不代表已持久化的真实评分。
- 没有“自动选股”。新闻和情绪只服务于已经给定的股票或 watchlist 股票分析。
- 没有证据链管理。Agent 报告里没有结构化 citation、URL、发布时间、来源可靠性、证据强度。
- 没有资讯去重和事件聚类体系。相同事件的多篇新闻可能被重复当作多条信息。
- MiroFish 事件抽取和隐性变量仿真存在代码，但主运行时没有把 `MiroFishSimulator` 实例注入 `AnalysisServices`，所以默认不会触发真实仿真。

### 4.3 优化方向

- 建立资讯采集层:
  - 公司公告。
  - 交易所公告和监管问询。
  - 宏观政策。
  - 财经新闻。
  - 行业数据。
  - 龙虎榜和资金流。
  - 可选社交情绪源。

- 建立事件层:
  - 新闻去重。
  - 同事件聚类。
  - 事件类型、影响行业、影响标的、时效性、置信度。
  - 事件强度评分和来源可靠性评分。

- 把 LLM 的新闻摘要、重要性评分、情绪标签、利多利空结论持久化，而不是只在一次分析上下文中使用。

- 选股不要让 LLM 直接从全市场凭空挑股票。更合理的流程是:

```text
全市场数据
  -> 硬过滤: 停牌、ST、流动性、市值、成交额、涨跌停、财务异常
  -> 因子候选: 动量、反转、估值、盈利质量、资金流、行业强度、事件强度
  -> 生成候选池: 例如 20 到 100 只
  -> LLM 只对候选做信息融合、冲突识别、风险解释和排序
  -> 风控/组合优化器决定是否进入 watchlist 或订单候选
```

## 5. 平台如何基于大模型能力确定量化交易策略

### 5.1 当前实现

多 Agent 编排在 `backend/agents/graph.py`:

1. 并行执行四个分析员:
   - `news_crawler`
   - `sentiment_analyst`
   - `fundamental_analyst`
   - `technical_analyst`

2. `intelligence_officer` 汇总新闻、情绪、基本面、技术面和大盘/北向资金。

3. 初始化多空辩论。

4. `bull_researcher` 和 `bear_researcher` 按轮次交替辩论。

5. `risk_officer` 输出风险评估。

6. `fund_manager` 输出最终 JSON:

```json
{
  "action": "买入/持有/卖出",
  "target_price": 0,
  "confidence": 0.0,
  "risk_score": 0.0,
  "reasoning": "..."
}
```

模型路由在 `config/agent_models.yaml`:

- DeepSeek: 新闻、情绪、数据清洗类低成本任务。
- Qwen: 基本面、技术面。
- Kimi: 情报研判、多空辩论、风控、最终决策。
- `fund_manager` 有分级路由: 先 Qwen triage，如果 JSON 置信度低于 0.6 或解析失败，则升级到 Kimi。

### 5.2 这不等于“量化策略”

当前 LLM 产出的是“主观式交易建议”，不是严格意义上的量化策略。

缺少以下量化策略要素:

- 明确入场规则。
- 明确出场规则。
- 仓位公式。
- 止损止盈公式。
- 交易成本模型。
- 滑点模型。
- 可回测的策略参数。
- 样本内/样本外评估。
- 横截面选股排序。
- 组合约束。
- 基准对照。
- 策略衰减监控。

因此当前系统更像“LLM 投研和交易建议系统”，而不是“可验证、可执行、可复盘的量化策略系统”。

### 5.3 优化方向

引入明确的策略对象，例如:

```text
StrategySpec
  - universe
  - filters
  - features
  - signal_rules
  - entry_rules
  - exit_rules
  - position_sizing
  - risk_limits
  - rebalance_frequency
  - benchmark
  - backtest_result
  - live_shadow_result
```

大模型应主要承担:

- 从资讯中提取事件和隐性变量。
- 对因子信号与新闻事件的冲突做解释。
- 生成可测试的策略假设。
- 解释策略失效原因。
- 对候选股做证据链排序。
- 给人类展示决策理由。

大模型不应直接承担:

- 最终风控硬限制。
- 资金划拨。
- 无约束自动下单。
- 回测结果计算。
- 持仓限额判断。
- 止损触发。

这些必须由确定性代码完成。

## 6. Kimi thinking 模式超长等待如何处理，大模型应该充当什么角色

### 6.1 当前已有处理

项目已经做了部分处理:

- `config/agent_models.yaml` 对非 Kimi agent 关闭 thinking。
- Kimi agent 设置 reasoning token 上限:
  - intelligence: 10000。
  - bull/bear: 8000。
  - risk: 6000。
  - fund_manager: 8000。

- `backend/llm/providers.py` 设置了 HTTP 超时:
  - connect 10 秒。
  - read 120 秒。
  - write 30 秒。
  - pool 10 秒。
  - SDK retry 降到 1。
  - 强制 IPv4 出口，规避 IPv6 死路。

- `AnalysisScheduler` 对 fast/slow bucket 有总超时:
  - fast: 480 秒。
  - slow: 900 秒。

- `/api/analysis/stock` 同步分析接口默认 300 秒超时。

- `fund_manager` 使用 Qwen triage，低置信度才升级 Kimi。

### 6.2 仍存在的问题

- `intelligence_officer`、`bull_researcher`、`bear_researcher`、`risk_officer` 默认仍直接使用 Kimi thinking，长上下文时仍可能严重拖慢。
- `/api/analysis/jobs` 的 SSE 后台任务没有外层 `wait_for` 包裹，可能比配置的 `analysis_timeout_seconds` 跑得更久。
- 成本软阈值触发后，代码只是记录 warning，注释中提到“后续会降级 thinking”，但当前没有实际降级。
- 多个 Kimi prose agent 没有结构化置信度输出，无法做和 `fund_manager` 一样的 triage/escalation。
- Kimi 的长思考不应成为主链路默认依赖，否则 watchlist 稍大就会导致 SLA 和成本不可控。

### 6.3 推荐处理策略

- 默认把 Kimi thinking 从主链路移到“少数关键步骤”:
  - 日常 fast 分析: 不使用 Kimi thinking 或只在最终争议场景使用。
  - slow 深度分析: 允许 Kimi thinking，但有明确超时和预算。
  - 人工复核/盘后复盘: 可以使用更长 thinking。

- 给每个 agent 增加硬超时，而不是只靠 pipeline 总超时。

- 所有 prose agent 输出结构化头部:

```json
{
  "confidence": 0.72,
  "need_escalation": false,
  "summary": "...",
  "risks": ["..."],
  "evidence_ids": ["..."]
}
```

这样才能让便宜模型先判断是否需要 Kimi。

- 引入降级策略:
  - Kimi 超时: 使用 Qwen 结果继续，但记录 `degraded=true`。
  - 预算软触发: 禁用 Kimi thinking，只做简版分析。
  - 预算硬触发: 跳过新分析，只允许读取已有记录。

- 前端明确展示:
  - 当前 agent。
  - 已耗时。
  - 超时降级状态。
  - 使用模型。
  - 是否触发 Kimi escalation。

## 7. 是否能做到真正自动化量化交易

结论: 当前不能。

### 7.1 已有自动化

- 后台可以定时采集指数和新闻。
- 后台可以定时对 watchlist 股票运行分析。
- 可以自动生成和保存 `TradingSignal`。
- 可以通过 Redis/WebSocket/SSE 推送状态。
- 可以在 Phase 5B shadow 开关打开时，对 fund_manager 结果做影子对比。

### 7.2 缺少自动交易闭环

缺少从 `TradingSignal` 到订单的执行链路:

- 没有 `Signal -> OrderIntent`。
- 没有 `OrderIntent -> RiskEngine.validate_order`。
- 没有 `RiskEngine -> ApprovalQueue.submit`。
- 没有 `confirm/auto` 模式下的执行器。
- 没有实盘 broker 实现。
- 没有成交回报和持仓同步。
- 没有订单重试、撤单、部分成交、涨跌停、停牌、废单处理。

`backend/broker/approval_queue.py` 支持审批队列，但没有公开的“提交待审批订单”API。现有 `approve/reject` 只能处理已经在内存队列里的 pending approval。

前端 `DecisionCard.vue` 的批准/拒绝按钮只是 emit 事件，`AgentDebate.vue` 里显示 toast，没有调用后端审批或下单接口。

### 7.3 授权模式限制

`backend/services/authorization.py` 明确限制:

- `phase5_eval` 和 `phase6_prep`: 只能 suggest。
- `phase6_dryrun`: suggest 或 confirm。
- `phase7_live`: suggest、confirm、auto。

这条红线是正确的。当前项目即便把前端切到自动模式，也不应进入真实自动交易。

## 8. 模拟交易能力与实盘操作能力

### 8.1 模拟交易能力

`backend/broker/mock_broker.py` 实现了 MockBroker:

- 初始资金。
- 买卖订单。
- 交易时段校验。
- A 股 100 股整数倍。
- T+1 可卖限制。
- 手续费、印花税、滑点。
- 持仓、订单、成交查询。

但它有明显限制:

- 状态只在内存中，后端重启即丢失。
- 持仓市值使用成本价近似，没有接入实时价格 mark-to-market。
- 没有与分析信号自动联动。
- 没有从历史行情驱动的撮合回放。
- 没有交易日推进任务，`advance_day()` 只有方法，未接真实日切调度。

### 8.2 实盘操作能力

`config/broker.yaml` 中有:

```yaml
active: mock
qmt:
  enabled: false
vnpy:
  enabled: false
```

代码中只有 `IBroker` 接口和 `MockBroker` 实现。没有 QMT、VNPy 或券商 API 实盘适配器。

因此当前没有实盘操作能力。

## 9. 复盘、回测与绩效能力

### 9.1 当前已有能力

- `analysis_records`: 记录一次多 Agent 分析过程。
- `trading_signals`: 记录最终信号。
- `GET /api/analysis/history`: 查询分析历史。
- `GET /api/analysis/signal-accuracy`: 用未来 N 天价格方向评估信号命中率。
- `GET /api/performance`: 根据 MockBroker trades 构造净值、回撤和核心指标。
- `shadow_decisions`: 用于对比 routed fund_manager 与 Kimi baseline。
- `scripts/shadow_compare.py` 和 `scripts/phase5b_exit_check.py`: Phase 5B 出口检查工具。

### 9.2 主要问题

- 没有真正回测引擎。虽然 `pyproject.toml` 依赖了 `backtrader`，当前仓库代码没有使用 backtrader。
- `SignalEvaluator` 只判断买入后涨、卖出后跌，过于粗糙，没有考虑:
  - 入场价格。
  - 出场规则。
  - 止损止盈。
  - 交易成本。
  - 最大不利波动。
  - 持有期收益分布。
  - benchmark 超额收益。

- `backend/api/performance.py` 的净值计算基于 MockBroker trade 的 `net_amount`，买入成交也会被当成正向金额加到组合净值，这会导致绩效曲线失真。
- MockBroker 不持久化，绩效无法跨进程复盘。
- 没有策略版本、参数版本、模型版本和数据版本绑定，无法知道某次收益来自哪个策略配置。
- 没有“复盘结论回写策略”的闭环。

### 9.3 优化方向

- 建立 `DecisionLedger`:
  - run_id。
  - signal_id。
  - strategy_id。
  - model_route。
  - data_snapshot_id。
  - evidence_ids。
  - intended_order。
  - risk_check_result。
  - actual_order。
  - fill_result。
  - realized_pnl。
  - attribution。

- 建立可回放回测:
  - 使用历史日线或分钟线。
  - 每个交易日按同一策略生成信号。
  - 通过 MockBroker 或 Backtrader 执行。
  - 输出交易清单、净值、回撤、换手、胜率、盈亏比、暴露度。

- 建立盘后复盘:
  - 当天所有信号。
  - 是否执行。
  - 未执行原因。
  - 收益归因。
  - 规则违背。
  - LLM 观点和事实结果对比。

## 10. 前端界面是否精美且实用

### 10.1 当前实现

前端使用 Vue 3、Pinia、Element Plus、ECharts，路由包括:

- Dashboard: 大盘监控。
- AgentDebate: Agent 辩论和分析历史。
- Simulation: MiroFish 仿真展示。
- Portfolio: 组合管理。
- Performance: 绩效报告。
- RiskCenter: 风控中心。
- Settings: LLM、数据源、MiroFish、成本配置。

整体界面已具备较强的可视化雏形:

- 三大指数卡片。
- 板块热力图。
- 北向资金图。
- 新闻列表。
- Agent 辩论面板和 SSE 实时分析。
- MiroFish 情绪曲线、隐性变量矩阵、极端场景图。
- 持仓、订单、审批队列。
- 净值、回撤、模型贡献。
- 风控雷达和配置。

### 10.2 实用性缺口

- Dashboard 的涨跌统计没有真实后端数据。
- 股票搜索是静态列表，不是全市场搜索。
- AgentDebate 的批准/拒绝没有后端执行。
- Simulation 的“注入 Agent 辩论”只是跳转页面，没有把仿真结果作为上下文传入分析任务。
- Performance 的导出按钮只是提示，未调用 `performanceApi.exportReport`。
- Portfolio 展示的是 MockBroker 内存状态，不是持久账户。
- RiskCenter 的部分数据来自内存事件和默认值，不是完整风控审计。
- 许多开发环境 fallback 会显示 mock 数据，容易掩盖后端未接通的问题。

### 10.3 优化方向

- 将 mock fallback 改为显式“演示模式”开关，生产环境绝不静默 mock。
- 增加 Watchlist 管理页，支持分类、候选池、分析频率、最新信号、执行状态。
- 增加“信号到订单”确认页:
  - 信号。
  - 推荐仓位。
  - 风控检查。
  - 预计成本。
  - 最大亏损。
  - 审批按钮。

- 增加数据质量面板:
  - 行情源延迟。
  - 新闻源状态。
  - LLM provider 状态。
  - 今日预算。
  - 当前降级状态。

- 增加复盘页:
  - 决策记录。
  - 真实收益。
  - 策略版本。
  - 错误归因。

## 11. 是否可以记录决策与收益情况

### 11.1 已有记录

MongoDBService 创建和使用以下集合:

- `trading_signals`: 最终交易信号。
- `analysis_records`: 完整 Agent 分析记录。
- `news_articles`: 新闻。
- `market_realtime`: 市场快照。
- `index_prices`: 指数收盘价。
- `cost_tracking`: LLM 成本。
- `simulations`: MiroFish 仿真结果。
- `shadow_decisions`: 影子对比结果。

MockBroker 内部记录:

- orders。
- trades。
- positions。
- account info。

### 11.2 主要问题

- MockBroker 的订单、成交、持仓不入库，重启丢失。
- `analysis_records` 中 `AgentStepRecord` 的 `model_label`、`model_id`、tokens、cost 多数为空或 0，无法做真实模型级归因。
- 信号和收益之间没有强绑定。当前没有回答“这笔收益来自哪次决策、哪条证据、哪个模型、哪个策略版本”。
- 没有记录每次风控检查的完整结果。
- 没有记录“建议但未执行”的原因。
- 没有记录真实成交回报，因为没有实盘 broker。

### 11.3 优化方向

新增统一决策账本:

```text
decision_ledger
  - decision_id
  - run_id
  - signal_id
  - stock_code
  - trade_date
  - strategy_id
  - model_route
  - evidence_snapshot
  - action
  - target_price
  - confidence
  - risk_score
  - proposed_order
  - risk_validation
  - authorization_mode
  - approval_status
  - broker_order_id
  - fill_status
  - realized_return_1d/5d/20d
  - benchmark_excess_return
  - review_notes
```

这样才能把“决策质量”和“收益结果”真正闭环。

## 12. 是否有保险机制

### 12.1 已有保险机制

项目已有一些重要保护:

- 阶段授权红线: `backend/services/authorization.py`。
- LLM provider preflight: 所有 provider 缺 key 时拒绝分析。
- LLM fallback: provider 失败时尝试 fallback。
- LLM 成本守门: `backend/services/cost_guard.py`。
- Fast/Slow watchlist 超时: `backend/data/analysis_scheduler.py`。
- RiskEngine 硬编码校验: `backend/risk/engine.py`。
- Stop loss 函数: `backend/risk/stop_loss.py`。
- CircuitBreaker: `backend/risk/circuit_breaker.py`。
- ApprovalQueue: `backend/broker/approval_queue.py`。
- Shadow runner: 路由决策与 Kimi baseline 对照。

### 12.2 严重缺口

- RiskEngine 没有接入信号到订单执行链路。
- Stop loss 没有后台扫描任务，也没有自动生成减仓/卖出订单。
- CircuitBreaker 没有从 MockBroker 成交结果自动更新盈亏状态。
- ApprovalQueue 缺少从分析信号生成 pending approval 的公开路径。
- 授权模式目前主要是启动和 API 状态约束，没有贯穿订单执行器。
- 没有全局 kill switch API 和前端显著入口。
- 没有真实资金账户保护，因为没有实盘 broker。
- 没有异常行情保护，例如价格跳变、源间偏差、停牌、涨跌停不可成交。

### 12.3 优化方向

所有订单必须走固定链路:

```text
TradingSignal
  -> OrderIntent
  -> RiskEngine.validate_order
  -> AuthorizationPolicy
  -> suggest: 只记录
  -> confirm: ApprovalQueue.submit
  -> auto: Broker.place_order
  -> Order/Trade persistence
  -> CircuitBreaker.record_trade_result
  -> StopLossMonitor
```

风险模块应成为下单前不可绕过的硬依赖，而不是旁路展示模块。

## 13. MiroFish 与隐性信息推演现状

### 13.1 已有代码

MiroFish 相关代码位于 `backend/mirofish/`:

- `simulator.py`: 用 LLM 模拟 persona 和情绪演化。
- `event_filter.py`: 从新闻报告抽取高重要性事件。
- `extractors/hidden_variables.py`: 隐性变量提取。
- `extractors/inflection_points.py`: 拐点提取。
- `extractors/extreme_scenarios.py`: 极端场景提取。
- `formatter.py`: 把仿真结果格式化注入 Agent 上下文。

配置在 `config/mirofish.yaml`。

前端 `Simulation.vue` 能展示仿真历史和结果。

### 13.2 未真正接线

`backend/agents/intelligence_officer.py` 只有当 `services.mirofish_simulator is not None` 时才会运行 MiroFish。  
但 `backend/main.py` 和 `backend/api/analysis.py` 构造 `AnalysisServices` 时没有创建并传入 `MiroFishSimulator`。

因此当前默认运行时不会触发 MiroFish 仿真，`/api/simulation/*` 也只是浏览已有 `simulations` 集合，不提供“启动一次仿真”的接口。

### 13.3 优化方向

- 在启动时根据 `config/mirofish.yaml` 创建 `MiroFishSimulator`。
- 在 `AnalysisServices` 注入 simulator。
- 为 MiroFish 加独立预算和超时。
- 将事件抽取、仿真触发、结果持久化、注入 Agent 上下文做成可观测链路。
- 前端“注入 Agent 辩论”按钮要传递 simulation_id 或 event_id 到分析任务。

## 14. 当前差距清单

| 领域 | 当前状态 | 与目标差距 | 优先级 |
|---|---|---|---|
| A 股实时数据 | 免费源单点/备用，指数定时采集 | 无全市场实时扫描，无数据质量监控 | 高 |
| 新闻资讯 | Eastmoney 新闻为主 | 源太少，评分不持久化，无事件聚类 | 高 |
| 自动选股 | watchlist/手动输入 | 没有全市场候选池和筛选器 | 高 |
| LLM 策略 | 多 Agent 交易建议 | 不是可回测量化策略 | 高 |
| Kimi thinking | 有 token cap 和部分超时 | prose agent 仍慢，SSE job 外层超时不足 | 高 |
| 模拟交易 | MockBroker 内存模拟 | 不持久，不接信号，不 mark-to-market | 高 |
| 实盘交易 | 配置占位 | 无 QMT/VNPy/broker 实现 | 高 |
| 风控保险 | 模块存在 | 未贯穿订单执行，止损/熔断未闭环 | 高 |
| 复盘回测 | 信号命中率和绩效雏形 | 无策略回测，无收益归因 | 高 |
| 前端 | 页面较完整 | 关键操作半接线，部分 mock | 中高 |
| 决策记录 | analysis_records/signals | 缺决策账本和收益归因 | 高 |
| 自进化 | shadow/accuracy 雏形 | 无策略选择、参数更新、失效检测闭环 | 高 |

## 15. 推荐修改路线图

### Phase A: 先把系统定位拉回可控范围

目标: 从“全自动赚钱机器”降级为“可验证的智能投研和模拟交易系统”。

- 明确阶段:
  - 当前只允许 suggest。
  - confirm 只进入模拟盘审批。
  - auto 在实盘券商、风控、回测、审计全部达标前禁用。

- 移除前端误导:
  - AgentDebate 的自动模式不要显示“已自动提交”，除非后端真的提交了订单。
  - 批准/拒绝必须调用后端审批 API。
  - mock 数据必须明确标注演示模式。

### Phase B: 补齐数据和事件基础设施

目标: 让选股和推断有可靠输入。

- 建全市场股票池表。
- 建 watchlist 行情定时采集。
- 建交易日历。
- 建数据质量检查。
- 扩展资讯源。
- 将 LLM 新闻评分和事件抽取持久化。
- 为每条 Agent 结论绑定 evidence_id。

### Phase C: 建立真正的候选选股引擎

目标: LLM 不直接海选股票，而是对候选做高质量推断。

- 实现硬过滤器:
  - ST、停牌、成交额过低、涨跌停、财务异常、价格异常。

- 实现候选因子:
  - 动量。
  - 成交额/换手。
  - 行业强度。
  - 估值。
  - 盈利质量。
  - 资金流。
  - 事件强度。

- 输出候选池:
  - top N 股票。
  - 每只股票的候选理由和风险标签。

- LLM 对候选池做二次排序和解释。

### Phase D: 把 LLM 建议变成可执行策略

目标: 每个信号都能被回测。

- 引入 `StrategySpec`。
- 每次信号绑定 strategy_id 和参数版本。
- `fund_manager` 不只输出 action，还要输出:
  - entry_price_rule。
  - exit_rule。
  - stop_loss。
  - take_profit。
  - max_holding_days。
  - position_pct。
  - invalidation_conditions。

- 这些字段必须通过 Pydantic 严格校验，解析失败就不能下单。

### Phase E: 接通信号到模拟订单

目标: 形成闭环，但仍只在模拟盘。

- 新增 `OrderIntent`。
- 新增 `OrderExecutor`。
- 新增 `POST /api/orders/intents/{signal_id}` 或 scheduler 自动生成 intent。
- suggest: 保存 intent，不提交。
- confirm: 提交到 ApprovalQueue。
- auto: 仅模拟盘可自动提交到 MockBroker。
- 所有路径必须先过 RiskEngine。
- MockBroker 状态入库。
- Portfolio 页面展示真实持久化模拟账户。

### Phase F: 建立复盘和回测

目标: 不再靠感觉判断模型是否有效。

- 修正绩效净值计算。
- 建立历史回测脚本。
- 对 watchlist 策略做 30/90/180 天回测。
- 每日盘后生成:
  - 信号列表。
  - 执行列表。
  - 收益归因。
  - LLM 观点命中/错误。
  - 数据质量报告。

### Phase G: 降低 Kimi thinking 对主链路的阻塞

目标: 稳定、可控、可降级。

- 给每个 agent 设置独立 timeout。
- Fast pipeline 默认不用 Kimi thinking。
- Slow pipeline 只在关键分歧时用 Kimi thinking。
- 所有 prose agent 输出结构化置信度，支持 cheap triage。
- 预算软阈值触发时自动禁用 Kimi thinking。
- SSE job 加总超时。
- 前端展示降级状态。

### Phase H: 实盘前准备

目标: 在真实资金前完成所有可验证条件。

- 实盘 broker adapter 单独实现，不混入 MockBroker。
- 只读账户同步先行。
- 模拟盘与实盘信号 shadow 至少运行 4 周。
- 所有订单必须可审计、可回滚、可人工 kill。
- 实盘前需要:
  - 数据质量达标。
  - 回测达标。
  - 模拟盘达标。
  - 风控演练达标。
  - 手动确认模式稳定。

## 16. 建议下一步任务

建议下一步不要直接继续“加智能”，而是先补闭环。

优先级最高的 8 个工程任务:

1. 实现 `OrderIntent` 和 `Signal -> OrderIntent` 转换，但先只保存，不下单。
2. 将 `RiskEngine.validate_order` 接入所有订单路径。
3. 让 AgentDebate 的批准/拒绝真正调用后端审批链路。
4. 将 MockBroker 账户、订单、成交、持仓持久化到 MongoDB。
5. 修正 Performance API 的净值计算。
6. 把 MiroFishSimulator 按配置注入 `AnalysisServices`，并增加显式仿真触发接口。
7. 为 watchlist 个股增加定时行情快照和数据质量监控。
8. 增加 `DecisionLedger`，把分析、信号、订单、成交、收益绑定起来。

完成这些后，再推进全市场选股、策略回测和自进化，工程风险会低很多。
