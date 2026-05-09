# QuantMind 项目协作上下文

> 本文件是 Claude 跨 session 接手 QuantMind 时的"第一读"。
> 当前为**过渡版**(2026-05-09 重写)。2026-05-08 audit 与决策清单整体重写,把项目方向从"半自动实盘升级路径"调整为"模拟实盘能力验证 + 飞书人工执行闭环"两条平行能力。所有决策点全部锁定后会基于决策结果重写本文件并生成新的执行计划。

---

## 1. 项目概述

### 1.1 项目定位(2026-05-08 重新锁定)

QuantMind **不再以真实券商账户的程序化下单、半自动下单或全自动下单为目标**。新目标是同时支持两种相互独立的运行模式:

| 模式 | 名称 | 目标 | 真实券商 API | 真实下单 |
|------|------|------|--------------|----------|
| 模式 A | `simulation_auto` 完全自动化模拟全流程 | 让系统在真实行情/资讯输入下,自主完成"分析 → 指令 → 风控 → 模拟成交 → 复盘"全闭环,作为平台实战能力的考场 | 不接 | 不下 |
| 模式 B | `feishu_interactive` 飞书交互人工执行 | 系统把结构化操作指令通过飞书发到群里;用户在券商 APP 手动执行;用户飞书回报实际成交;系统解析回报并维护"用户回报账户镜像" | 不接 | 用户手动 |

两种模式共用同一套 `InstructionPlan`、风控、仓位计算、证据链与统一账本(`decision_ledger`)。

### 1.2 旧愿景 vs 当前形态(基于 audit 2026-05-07/08 重构)

最初愿景是"基于大模型隐性信息汇总推演机制的全自动与自进化 A 股量化交易平台",但 audit 揭示当前真实形态更接近"多 Agent 投研信号系统 + 内存 MockBroker 模拟交易展示":

- 已具备:FastAPI 后端 + Vue 前端 + MongoDB/Redis + adata/akshare/baostock 行情 + 9-Agent LangGraph 分析 + LLM Router(DeepSeek/Qwen/Kimi) + MockBroker + RiskEngine + 影子对比/成本守门/阶段授权红线雏形
- 关键缺口(以 audit 为准):
  - 没有 `InstructionPlan`,只有粗粒度 `TradingSignal`(缺股数/限价/有效期/失效条件/数据快照 ID/证据 ID/指令编号/回报模板)
  - `TradingSignal → 模拟订单执行` 链路完全断裂(`AnalysisScheduler` 保存信号后不调用 MockBroker)
  - `RiskEngine.validate_order` 在 `backend/api/`、`backend/broker/`、`backend/agents/` 中无调用 — 风控未贯穿信号到执行
  - MockBroker 状态只在内存,无持久化、无 mark-to-market、无日切
  - 没有飞书发送/接收/解析/去重/回执模块(只有 `backend/monitoring/alerter.py` 的单向 webhook 告警)
  - 没有 `UserReportedPortfolio` 用户回报账户镜像,也没有 `decision_ledger` 统一账本
  - MiroFish 适配器存在,但 `AnalysisServices` 构造时未注入 `MiroFishSimulator` — 默认运行时不触发仿真
  - 前端仍是 `suggest/confirm/auto` 旧语义,关键按钮(批准/拒绝/注入辩论/导出)为 mock 或半接线
- 历史教训:1139 测试全绿但 RiskEngine 实际未接入订单链路 — **测试通过 ≠ 闭环可用**

### 1.3 当前阶段:决策对齐期(2026-05-08 重启)

旧的 `docs/phase5-eval-and-phase6-prep-master-plan.md` 与 Phase 5B 节奏**暂停推进**。当前只做一件事:针对新方向下的 owner 决策点(P0/P1/P2)逐个完成调研讨论,每个决策点产出 `docs/decisions/{决策编号}-{决策结果简述}.md`(命名约定见 `docs/decisions/README.md`)。

**全部 P0 锁定 → 重写本 CLAUDE.md + 生成新执行计划 → 才开始下一阶段实现工作。**

**进度(2026-05-09)**:
- P0-1 ✅ 已锁定 — `simulation_auto` always-on 底座 + `feishu_interactive` 可叠加切换;MockBroker 是唯一账户镜像;模式切换 = 账户生命周期事件;旧 `AUTHORIZATION_MODE × QUANTMIND_PHASE` 矩阵一次性破坏式删除;详见 [P0-1 决策文档](docs/decisions/P0-1-simulation-base-feishu-overlay.md)
- P0-2 ✅ 已锁定 — 企业自建应用 + `lark-oapi` 长连接(双向闭环主路径)+ 自定义机器人 webhook(仅系统告警备用);第一阶段纯文本指令 + 严格回报模板;零公网入站、所有飞书凭证仅 shell env;详见 [P0-2 决策文档](docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md)
- P0-3 ✅ 已锁定 — `InstructionPlan` 严格 Pydantic schema(`QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq}` 人读 ID + 极简 BUY/SELL/HOLD + 单 limit_price + 当日盘中 valid_until + 7-check 摘要 by-value / 完整证据 by-reference 折中绑定);第一阶段飞书纯文本模板锁定,`HOLD` 不路由不发飞书;`parse_ok=False` 强制降级 HOLD;详见 [P0-3 决策文档](docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md)
- P0-4 ✅ 已锁定 — ExecutionReportParser 严格正则 only(LLM 完全不参与回报路径);五种回报形态(已执行/部分执行/未执行/更正/盘后补录)精确正则锁定;盘中 30 分钟超时 + 1 次追问,valid_until 自动 EXPIRED;盘后补录与更正允许到当日 16:00;状态机扩展 AMBIGUOUS / 终态间迁移;澄清飞书五模板预写死;详见 [P0-4 决策文档](docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md)
- P0-5 ✅ 已锁定 — MockBroker 单一账户镜像(不引入 UserReportedPortfolio 平行 collection);系统 16:00 主动发起日终对账(`RECON-{YYYYMMDD}-{seq}` 人读 ID);分级偏差阈值(持仓股数 0% + 现金 ≤1 元 + 成本价 ≤0.01 元);超阈值创建 `reconciliation_ticket` fail-closed 等用户飞书显式裁定(三选一:采纳用户回报/采纳系统镜像/对账更正);OPEN/EXPIRED ticket 期间冻结次日买卖类 InstructionPlan 路由;公司行动第一阶段不支持自动处理统一走"对账更正";LLM 完全不参与对账路径;详见 [P0-5 决策文档](docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md)
- P0-6 ✅ 已锁定 — `simulation_auto` 验收 = 45 交易日滚动窗口 + 5 项稳定性硬门槛(指令完整率 ≥95% / 回报解析准确率 ≥99% / 数据缺失率 ≤1% / LLM 超时率 ≤5% / 信号生成成功率 ≥95%)+ 3 项策略硬门槛(最大回撤 ≤8% / 累计 PnL ≥0 / 沪深 300 累计基准超额收益 ≥0)+ 7 项观察指标(不阻断切换);P0 系统级中断重置倒计时;reconciliation 冻结日不计入窗口(暂停而非重置);切换 `feishu_on` 必须前置 `acceptance.can_switch_to_feishu_on()` 校验,严禁 env var 绕过;每日 16:00:30 系统生成 `acceptance_reports` 一条记录;LLM 完全不参与验收路径;详见 [P0-6 决策文档](docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md)
- P0-7 ✅ 已锁定 — 保守仓位三连阈值(单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万)+ 中性日内熔断(每日 ≤5 单 / 日亏 -5% / 连亏 3 笔 / 冷却 60 分钟,SELL 默认不熔断)+ 中性 universe(`sh_main`+`sz_main`+`chuangye`+`etf`,禁 ST/科创/北交所/可转债 + 禁涨停 BUY / 跌停 SELL)+ RiskConfig 全锁(runtime 不可改,LLM 永不持有写引用)+ RiskParameterProposal 自进化提议通道(只读 ledger,人工 weekly review → amendment + 重启);RiskEngine 从 7-check 扩展为 14-check(派生 P0-3 amendment 调整 risk_summary 长度);DailyTradingState + StockMetadata 由 InstructionPlanBuilder 装配传入(RiskEngine 仍纯函数 + 无 IO);熔断与切换冻结 / ticket 冻结独立并行;详见 [P0-7 决策文档](docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md)
- P0-8 ✅ 已锁定 — 行情严格主备(adata + akshare;staleness ≤5s / divergence ≤0.3%)+ 全 watchlist 30s 个股快照(填补 audit §6.2 缺口;`watchlist_market_snapshots` collection)+ 多域 5 源情报(财经 2 + 时政 1 + 全球 2:`stock_news_em`/`stock_info_global_cls`/`news_cctv`/`stock_info_global_em`/`stock_info_global_sina`;军事/社交舆论留 P1)+ MiroFish 双路径接线(事件驱动 severity≥HIGH / 盘后复盘 17:00 watchlist_all;原 P2-1 前置;输出仅入 `evidence_collection` 不入 `RiskCheckSummary`)+ DataQualityState 早返第四种买卖类冻结来源(builder 早返降级 HOLD / 不暂停 simulation_auto / risk_summary 长度恒 14 不变)+ 5 类 evidence_id 前缀约定(`NEWS-`/`MIROFISH-`/`MARKET-`/`RISK-`/`DEBATE-`);LLM 完全不参与数据质量判定;派生 P0-3 amendment(`news_source`→`news_sources_by_domain`);P2-1 标记为 superseded;详见 [P0-8 决策文档](docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
- P0-9 ✅ 已锁定 — 中等双层 watchlist(13 codes = 沪主 4 + 深主 3 + 创业板 3 + 宽基 ETF 3:`510300`/`510500`/`159949`)+ 中等严格排除规则(新股 ≤30 / 次新 ≤180 / 流动性 < 2 亿 / 单价 > 500;由 InstructionPlanBuilder 第五道早返,不进 RiskEngine 14-check)+ 沿用 fast/slow 双频(09:00 slow 全 watchlist 多 Agent 辩论 + 09/11/13/15 fast 盘中验证)+ 5 单 cap 分配(`traditional_path_default_cap=4` + `event_path_reserved_cap=1`,14:30 后 event 未用可释放给 traditional)+ **关键定位:MiroFish 是加分项不是核心,平台底层是传统 AI 量化交易,event 路径硬 cap=1 严禁占用主路径**+ 严格 long-only(BUY/SELL/HOLD 永锁 / 永禁 SHORT/COVER/MARGIN/REPO / ETF 仅二级市场买卖 / ETF 套利预留 P1 但永锁 disabled)+ watchlist 在 runtime 不可改(`backend/api/watchlist*.py` 仅 GET);派生 P0-7 amendment(watchlist 排除规则在 InstructionPlanBuilder 早返而非 RiskEngine);详见 [P0-9 决策文档](docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md)
- 下一站:P0-10 LLM 角色边界(P0 最后一站;抽取/解释/草案 vs 决策/仓位/风控硬限制 / 多 Agent 辩论 vs RiskConfig 全锁的精确分割)

> ⚠️ 注意:本 CLAUDE.md 的早期版本曾把"P0-1 = 半自动实盘 live_confirm"和"P0-2 = 免费数据栈+财联社+巨潮"标注为 ✅ 已锁定,并在表格中链接 `docs/decisions/P0-1-...` / `docs/decisions/P0-2-...`。这些决定属于**旧方向**,在 2026-05-08 audit/决策清单整体重写后已**全部作废**;链接的决策文件也从未真正落地。新方向下的 P0-1 已于 2026-05-09 重新落定为本节进度所述结果。

### 1.4 项目目录速查

```
backend/
  agents/           # 9-Agent LangGraph pipeline + AnalysisRecord(产出 TradingSignal,缺 InstructionPlan)
  api/              # FastAPI routers (analysis/risk/monitoring/trading/...)
  data/             # MongoDB / Redis / scheduler / news_crawler / market_data
  llm/              # router + cost_tracker + fallback + providers
  risk/             # 纯 Python 硬编码,严禁 import LLM/agents/mirofish(未贯穿到订单链路)
  services/         # cost_guard / authorization / signal_evaluator / shadow_runner
  broker/           # MockBroker(内存) + IBroker interface(实盘 stub 不开发)
  mirofish/         # 隐性变量仿真模块(默认未注入运行时)
  monitoring/       # alerter.py 单向 webhook(不是飞书双向闭环)
frontend/           # Vue 3 + Element Plus + ECharts(port 9276,仍含 suggest/confirm/auto 旧语义)
config/             # agent_models.yaml / risk.yaml / broker.yaml / data_sources.yaml / mirofish.yaml
docs/
  quantmind_project_audit_2026-05-07.md             # 当前真实状态全景盘点(2026-05-08 重写)
  quantmind_owner_decision_points_2026-05-07.md     # 待决策点清单(2026-05-08 重写)
  decisions/                                          # 已锁定决策(目前只有 README.md)
  reviews/                                            # codex review 与阶段总结
  phase5-eval-and-phase6-prep-master-plan.md         # 旧 SSoT(暂停)
  QuantMind_Project_Blueprint_V3.md                   # 早期蓝图
tests/              # pytest 全部测试(1139 绿,但闭环未通)
```

---

## 2. 技术栈决策点

> 本节随每个决策点的落地实时更新。状态:`⏳ 待讨论` / `🔧 调研中` / `✅ 已锁定` / `🛑 暂缓` / `❌ 已废弃`。
> 决策编号取自 `docs/quantmind_owner_decision_points_2026-05-07.md`(2026-05-08 重写版)。

### 2.1 P0 决策点(必须先于核心闭环重构完成)

| 编号  | 主题                                                | 状态        | 决策文档 | 备注 |
|------|----------------------------------------------------|------------|---------|------|
| P0-1 | 两种运行模式与系统边界                              | ✅ 已锁定   | [P0-1-simulation-base-feishu-overlay.md](docs/decisions/P0-1-simulation-base-feishu-overlay.md) | `simulation_auto` always-on 底座 + `feishu_interactive` 切换器;MockBroker 单一账户镜像;模式切换 = 账户生命周期事件;旧授权矩阵一次性破坏式删除 |
| P0-2 | 飞书接入形态                                         | ✅ 已锁定   | [P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md](docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md) | 企业自建应用 + `lark-oapi` 长连接做双向闭环主路径;自定义机器人 webhook 仅作系统告警逃生通道;第一阶段纯文本;零公网入站 + 凭证仅 shell env |
| P0-3 | 操作指令结构(`InstructionPlan`)                     | ✅ 已锁定   | [P0-3-instruction-plan-strict-schema-and-text-template.md](docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md) | 严格 Pydantic schema(人读 ID + BUY/SELL/HOLD + 单 limit_price + 当日盘中 valid_until + 7-check 摘要 by-value + 详细证据 by-reference);第一阶段飞书纯文本模板锁定;HOLD 不路由不发飞书 |
| P0-4 | 飞书回报语法与成交状态                              | ✅ 已锁定   | [P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md](docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md) | 严格正则 only(LLM 完全不参与回报路径)+ 五种回报形态精确正则 + 状态机扩展 AMBIGUOUS / 终态间迁移 + 30 分钟追问 + 16:00 盘后补录与更正 cutoff |
| P0-5 | 账户状态来源与对账机制                              | ✅ 已锁定   | [P0-5-daily-reconciliation-fail-closed-tickets.md](docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md) | MockBroker 单一镜像;系统 16:00 主动发对账(RECON-{YYYYMMDD}-{seq} ID);分级阈值(股数 0% + 现金 ≤1 元 + 成本 ≤0.01 元);超阈值创建 reconciliation_ticket fail-closed 等用户三选一裁定(采纳用户/采纳系统/对账更正);OPEN/EXPIRED ticket 冻结次日买卖类指令路由;公司行动第一阶段走对账更正;LLM 不参与对账路径 |
| P0-6 | `simulation_auto` 验收标准                          | ✅ 已锁定   | [P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md](docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md) | 45 交易日滚动窗口 + 5 项稳定性硬门槛(95% / 99% / 1% / 5% / 95%)+ 3 项策略硬门槛(回撤 ≤8% / PnL ≥0 / 沪深 300 超额 ≥0)+ 7 项观察指标;P0 系统级中断重置;reconciliation 冻结暂停不重置;切换 `feishu_on` 必须前置 acceptance 校验;LLM 不参与验收路径 |
| P0-7 | 风险红线与指导强度                                   | ✅ 已锁定   | [P0-7-risk-redlines-position-circuit-universe-llm-immutability.md](docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md) | 保守仓位三连(单股 15% / 总仓 70% / 单次 5 万)+ 中性日内熔断(每日 5 单 / 日亏 -5% / 连亏 3 / 冷却 60 分钟 / SELL 不熔断)+ 中性 universe(主板+创业板+ETF / 禁 ST/科创/北交/可转债 / 禁涨跌停同向交易)+ RiskConfig 全锁 + RiskParameterProposal 自进化提议通道;RiskEngine 7-check → 14-check(派生 P0-3 amendment) |
| P0-8 | 数据与资讯可信度                                     | ✅ 已锁定   | [P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md](docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md) | 行情主备 staleness 5s / divergence 0.3% + 全 watchlist 30s 快照 + 多域 5 源情报(财经 2 / 时政 1 / 全球 2;军事+社交舆论 P1)+ MiroFish 双路径(事件 + 盘后)+ DataQualityState 早返第四种买卖类冻结(不进 RiskEngine,builder 层降级 HOLD,risk_summary 长度恒 14)+ 5 类 evidence_id 前缀约定;派生 P0-3 amendment(多域 news_source)+ P2-1 superseded |
| P0-9 | 第一阶段标的范围与频率                               | ✅ 已锁定   | [P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md](docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md) | 中等双层 watchlist(13 codes = 沪主 4 + 深主 3 + 创业板 3 + 宽基 ETF 3)+ 中等严格排除(新股 30 / 次新 180 / 流动性 2 亿 / 单价 500;在 InstructionPlanBuilder 早返不进 14-check)+ fast/slow 双频(09:00 slow 全 watchlist + 09/11/13/15 fast 盘中)+ 5 单 cap 分配(traditional 4 + event_reserved 1,14:30 后滑动)+ **MiroFish 是加分项不是核心,event 路径硬 cap=1 严禁占用主路径** + 严格 long-only / ETF 套利留 P1(永锁 disabled);watchlist runtime 不可改;派生 P0-7 amendment(watchlist 排除规则在 InstructionPlanBuilder 早返) |
| P0-10| LLM 角色边界                                         | ⏳ 待讨论   | —       | 抽取/解释/草案 vs 决策/仓位/风控硬限制 |

### 2.2 P1 决策点(影响架构,主要闭环开发前定下来)

| 编号  | 主题                              | 状态      | 决策文档 | 备注 |
|------|-----------------------------------|----------|---------|------|
| P1-1 | 新核心数据模型                    | ⏳ 待讨论 | —       | `instruction_plans`/`simulation_orders`/`execution_reports`/`portfolio_snapshots`/`feishu_messages`/`decision_ledger` |
| P1-2 | MockBroker 持久化与实时估值       | ⏳ 待讨论 | —       | 持久化口径/日切/撮合/估值源/交易成本模型 |
| P1-3 | 飞书消息形态                      | ⏳ 待讨论 | —       | 纯文本 / 富文本 / 卡片 / 混合 |
| P1-4 | 回报解析策略                      | ⏳ 待讨论 | —       | 规则优先 vs LLM 辅助 + 解析状态机 |
| P1-5 | 前端优先工作流                    | ⏳ 待讨论 | —       | InstructionCenter / SimulationLedger / FeishuConsole / PortfolioMirror / DataQuality |
| P1-6 | 安全、密钥与访问边界              | ⏳ 待讨论 | —       | App Secret/Verify Token/Encrypt Key 存放 + 公网回调与否 |
| P1-7 | 成本预算                          | ⏳ 待讨论 | —       | LLM/飞书/数据源/服务器月预算 + 软超限动作 |
| P1-8 | Kimi thinking 使用策略            | ⏳ 待讨论 | —       | 场景/超时/失败动作矩阵 |

### 2.3 P2 决策点(可稍后细化)

| 编号  | 主题                       | 状态      | 决策文档 | 备注 |
|------|----------------------------|----------|---------|------|
| P2-1 | MiroFish 真实使用范围      | 🛑 superseded | 由 [P0-8 §1.4](docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md) 取代 | P0-8 锁定双路径接线(事件驱动 + 盘后复盘);实施期产出 `P2-1-superseded-by-P0-8.md` 显式记录 |
| P2-2 | 自进化机制边界              | ⏳ 待讨论 | —       | 候选权重/路由/prompt/策略/风控参数 自动改边界 |
| P2-3 | 移动端或远程访问            | ⏳ 待讨论 | —       | Web UI 内网 + 移动端只飞书 |
| P2-4 | 告警渠道                    | ⏳ 待讨论 | —       | 行情断流/资讯失败/LLM 不可用/指令生成失败/风控拦截/日终对账缺失 |

### 2.4 当前(尚未决策时)的临时栈快照

仅作"系统现在跑成什么样"的事实记录,**不构成最终选择**:

- **后端**: FastAPI + Python 3.11(`/home/ps/anaconda3/envs/zhanglan/bin/python`)
- **数据层**: MongoDB(127.0.0.1) + Redis(127.0.0.1)
- **行情源**: adata 主 / akshare 备 / baostock 历史备(免费源,只适合原型与研究)
- **资讯源**: `akshare.stock_news_em` (Eastmoney 口径) — 单源、缺乏事件聚类与证据链
- **LLM**: DeepSeek V4 Pro + Qwen 3.6 Plus + Kimi K2.6,通过 `backend/llm/router.py` 按 `config/agent_models.yaml` 路由
- **前端**: Vue 3 + Pinia + Element Plus + ECharts,port 9276
- **券商**: 仅 MockBroker(内存模拟),qmt/vnpy 占位但**新方向下不再开发**
- **授权语义**: `backend/services/authorization.py` 仍是旧的 `phase5_eval/phase6_prep/phase6_dryrun/phase7_live × suggest/confirm/auto` 矩阵 — P0-1 已锁定**一次性破坏式删除**,实施期会替换为 `FEISHU_INTERACTIVE_ENABLED` 单开关 + 切换状态机(详见 [P0-1 §3](docs/decisions/P0-1-simulation-base-feishu-overlay.md))

---

## 3. 硬约束和原则

> 本节记录"绝对不能碰的红线 + 已经踩过的坑 + 必须怎么做的原则"。会随项目演进追加。

### 3.1 红线(违反即停止)

**新方向定位红线**(P0-1 锁定 2026-05-09,详见 [decisions/P0-1](docs/decisions/P0-1-simulation-base-feishu-overlay.md) §2):

- **永久禁止真实券商 API 下单/撤单/账户同步**;不开发 QMT / Ptrade / vn.py 适配器,`backend/broker/` 仅留 `IBroker` interface stub 与 `MockBroker`
- **`FEISHU_INTERACTIVE_ENABLED` 是唯一的运行时开关**;`AUTHORIZATION_MODE` / `QUANTMIND_PHASE` 在新代码中**禁止再读取**(P0-1 落地时一次性破坏式删除)
- **`live_confirm` / `phase7_live` / `auto` 三个词在新代码中视为非法标识符**(grep 必须为空,通过 lint rule 持续校验)
- **MockBroker 是唯一账户镜像**:`feishu_off` 下是虚拟资金考场,`feishu_on` 下是用户真实资金的状态镜像(由飞书回报驱动);**不存在两条平行账本**
- **模式切换不是 flag toggle,是账户生命周期事件**:必须经过强制归档 + MockBroker 重置 + 飞书初始化对账 + 解析成功才正式切换;切换期间冻结买卖类 InstructionPlan
- **InstructionPlan 必须由角色鲜明的多 Agent 多轮辩论生成**;LLM 不允许绕过辩论直出股数/价格/有效期
- **`feishu_off` 时只发系统告警,绝不发买卖指令**;`feishu_on` 时未收到回报的 InstructionPlan(超时 + 追问后)标记 expired,不更新 MockBroker
- **飞书回报歧义必须 fail-closed**:解析不出 `instruction_id` / 股票代码 / 方向 / 股数 / 成交价时,系统不能更新持仓,必须发飞书要求澄清

**架构红线**:

- `backend/risk/` 严禁 `import backend.llm` / `backend.agents` / `backend.mirofish`,LLM 输出不得覆盖风控硬限制
- 仓位计算、风控红线判断必须由确定性代码完成,**LLM 不允许直接决定股数或风控边界**
- 飞书指令必须带 `data_snapshot_at`、`quote_source`、`news_source`;数据质量不达标时只允许发"观察/暂停"消息,不发买卖指令

**飞书接入红线**(P0-2 锁定 2026-05-09,详见 [decisions/P0-2](docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md) §2):

- **永久禁止 HTTPS 回调入站端口**;事件订阅只走 `lark-oapi` 长连接(WebSocket),零公网入站;若未来真需要 HTTPS 回调必须先走 `P0-2-amendment-{date}-https-callback.md`
- **自定义机器人 webhook 仅可发系统告警,绝不发买卖指令 / 对账请求 / 澄清消息**;实施期由 lint rule + 集成测试守门
- **长连接 worker 只能单实例运行**;水平扩多实例属红线违规(飞书消息随机推单 client,会丢消息)
- **长连接事件处理函数 3 秒内必须返回 ack**;真实解析/落库走异步队列
- **`tenant_access_token` 不持久化**:不写入 MongoDB / Redis / 文件;只在内存
- **第一阶段不实现交互卡片**(`interactive` msg_type / `card.action.trigger` 回调实现属实施期外的范围,加它必须先走 amendment)
- **长连接断线时**:可继续发系统告警(走备用 webhook),但**不可发买卖指令**;由 ModeRouter 在长连接失活态下 fail-closed 拒绝路由买卖类 InstructionPlan
- **`lark` / `feishu` / `larksuite` 关键字在 `backend/risk/` 子树严禁出现**(继承 P0-1 §8 原则)

**操作指令结构红线**(P0-3 锁定 2026-05-09,详见 [decisions/P0-3](docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md) §2):

- **`instruction_id` 必须严格匹配** `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`;Pydantic schema 强校验,违规即 ValidationError;长度恒在 33-34 字符
- **`InstructionSide` 集合冻结为** `{BUY, SELL, HOLD}`,任何其他取值红线违规;**HOLD 永不路由到 SimulationExecutor / FeishuMessenger**(`is_routable()` 强制返回 False)
- **`valid_until` 三连约束**:`> created_at` + 当日内 + `≤ 当日 14:55 Asia/Shanghai`;跨日支持必须先走 `P0-3-amendment-{date}-cross-day-validity.md`
- **BUY/SELL 必须有 `volume`(100 整数倍)+ `limit_price`(>0)**;HOLD 必须 `volume=None, limit_price=None`,违规 ValidationError
- **`InstructionPlan.status` 流转必须经过状态机**(DRAFT → VALIDATED → DISPATCHED → FILLED / EXPIRED / REJECTED / AMBIGUOUS);跨态(如 DRAFT→FILLED)红线违规
- **`risk_summary` 必须包含恰好 7 条**(对应 `backend/risk/engine.py::_check_*` 7-check);HOLD 也要 7-check
- **`debate_round_count ≥ 1`**(P0-1 §1.6 / 绕过辩论 = 0 即红线违规);Pydantic `Field(ge=1)` 强校
- **`parse_ok=False` 的 TradingSignal 不得产出可执行 InstructionPlan**;必须降级为 HOLD
- **飞书指令文本必须由 `renderer.py` 函数生成**,不允许 LLM 自由拼接(P0-2 §2 红线 / 防 prompt injection 间接绕过模板)
- **InstructionPlan 是 frozen Pydantic 模型**;就地 mutation 红线违规;状态流转必须 `model_copy(update={"status": ...})` 经过状态机守门
- **第一阶段排除**:市价单(`OrderType.MARKET`)/ 价格区间 / 区间偏移 / ADD/REDUCE 百分比 全部不在 P0-3 范围,引入必须先走 amendment

**飞书回报路径红线**(P0-4 锁定 2026-05-09,详见 [decisions/P0-4](docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md) §2):

- **严格正则不通过 = AMBIGUOUS**:任何不满足 P0-4 §1.2 五条正则 + 字段交叉校验的回报,InstructionPlan.status 必须迁移到 AMBIGUOUS,**绝不更新 MockBroker**
- **LLM 严禁参与回报路径**:`backend/services/execution_report*.py` / `backend/integrations/feishu/parser*.py` / `clarification.py` 严禁 `import backend.llm.*`;澄清飞书文案全部预写死,LLM 不生成
- **绝不猜测 instruction_id**:回报文本中 instruction_id 必须严格通过 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$` 匹配;系统不通过上下文反推,格式不符即 AMBIGUOUS
- **side_zh ↔ instruction_id BUY/SELL 必须一致** + **stock_code 必须与 InstructionPlan 一致** + **股数必须 100 整数倍** + **filled_volume + remain_volume == InstructionPlan.volume**;任一失败 AMBIGUOUS
- **16:00 Asia/Shanghai cutoff 后任何回报触发 AMBIGUOUS**:状态机抛 `PostCloseFreezeError`;当日 InstructionPlan 状态变更冻结
- **更正路径仅允许从终态出发**(`{FILLED, REJECTED, EXPIRED, AMBIGUOUS}`);**盘后补录路径仅允许对未回报指令**(`{EXPIRED, AMBIGUOUS, DISPATCHED}`)且每个 instruction_id 仅一次
- **追问机制**:盘中 30 分钟阈值发 1 次,短追问宽限 10 分钟(若 valid_until - dispatched_at < 30 分钟跳过追问);AMBIGUOUS 期间不发追问;valid_until 自动 EXPIRED
- **澄清飞书严禁走备用 webhook**(继承 P0-2 §2 红线 2 / 备用 webhook 仅发系统告警);主通道失活时不发澄清,只发告警
- **状态机迁移必须经守门函数**(`instruction_plan_state_machine.py::transition`);任何 `model_copy(update={"status": ...})` 直接绕过即红线违规
- **ExecutionReport 是 frozen Pydantic v2 模型**;就地 mutation 红线违规(继承 P0-3 §2 红线 12 immutability 原则)

**数据与资讯可信度红线**(P0-8 锁定 2026-05-09,详见 [decisions/P0-8](docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md) §2):

- **多域 5 源情报选型锁定**(`stock_news_em` + `stock_info_global_cls` + `news_cctv` + `stock_info_global_em` + `stock_info_global_sina`):新增/删除任何源必须先走 `P0-8-amendment-{date}-{原因}.md`;微博 / 知乎 / 雪球 / 国防部 RSS / 同花顺全球 / 百度衍生在 P0-8 第一阶段严禁实现(合规风险与稳定性未评估)
- **行情主备硬阈值锁定**:`staleness_threshold_seconds=5` / `divergence_threshold_pct=0.003` / `minimum_freshness_seconds_for_buy_sell=60`;调整任一阈值必须先走 amendment;实施期 lint rule 守门
- **DataQualityState 早返是第四种买卖类路由冻结来源**:与 P0-1 切换冻结 / P0-5 ticket 冻结 / P0-7 熔断冷却独立并行;任一为真即冻结买卖类 InstructionPlan;严禁绕过任一种冻结
- **`pause_simulation_auto_on_quality_breach=false` 锁定**:数据质量违规**不**暂停 simulation_auto;multi-Agent 辩论 + signal 生成 + InstructionPlan 装配持续运行,只是 Builder 早返降级 HOLD;改为 true 必须先走 amendment(违反 P0-1 §1.1 always-on 精神)
- **MiroFish 双路径触发锁定**:事件驱动(severity ≥ HIGH;runs_per_day_cap=20;runs_per_minute_cap=3)+ 盘后复盘(17:00 cron;watchlist_all;runs_per_day_cap=50);新增触发路径必须先走 amendment;实施期必须为 `AnalysisServices` 注入 `MiroFishSimulator`(audit §10.2 缺口必修)
- **MiroFish 输出严禁进 `RiskCheckSummary`**:仅写入 `evidence_collection`,通过 `evidence_ids` by-reference 关联(继承 P0-1 §2 红线 8 / P0-7 §2 红线 11);任何把 MiroFish 输出字段映射到 RiskCheckSummary 的代码即红线违规
- **LLM 严禁参与数据质量判定**:`backend/data/data_quality.py` / `backend/data/divergence.py` / `backend/data/staleness.py` / `backend/data/suspension.py` 严禁 `import backend.llm.*`;MiroFish 内部 LLM 调用走 evidence 不走 DataQualityState / RiskEngine
- **行情主备价差超 0.3% 即降级**:不允许"价差大但仍发指令"的乐观回退(继承 P0-7 §2 红线 13 fail-closed 精神)
- **全 watchlist 30s 快照采集是 P0 实施期必修**:audit §6.2 揭示的"只采三大指数"缺口必须填补,否则 P0-6 数据缺失率 ≤ 1% 硬门槛不可达
- **停牌检测必经 `backend/data/suspension.py` 纯函数**:严禁在 `backend/risk/` / `backend/services/` 内重复实现停牌识别(继承 P0-7 §2 红线 15 单一真相源原则);严禁基于 LLM 推断停牌
- **5 源情报严禁强制要求全部存活**:`global_require_at_least_n_alive=1`(任一存活即继续);**禁止**把"5 源全断"或"任一域单源故障即冻结全市场"作为更严格的阈值
- **`news_cctv` 6h 阈值锁定**:新闻联播一日一期,真实更新仅在 19:30+,但 6h 阈值容忍 cron 抖动 + 跨日盘前数据未刷新场景;严禁强行升级到分钟级阈值(违反时政域信号本质)
- **`evidence_ids` 前缀约定锁定**:`NEWS-` / `MIROFISH-` / `MARKET-` / `RISK-` / `DEBATE-` 五类前缀;新增前缀必须先走 amendment
- **`watchlist_market_snapshots` collection 索引锁定**:`(code, snapshot_at)` 唯一 + `(snapshot_at, -1)` 倒序 + `(code, snapshot_at, -1)`;严禁删除任一索引(影响 InstructionPlanBuilder 查询性能 + P0-6 数据覆盖度计算)
- **多源去重严格按 domain + 标题 + 时间窗 60s**:跨 domain 不去重(同一事件多域评价是 MiroFish 输入价值);严禁实施期为减少存储成本而跨域去重
- **`DataQualityState` / `WatchlistSnapshot` / `NewsArticle` / `StalenessReport` / `DivergenceReport` 是 frozen Pydantic v2 / @dataclass(frozen=True) 模型**:就地 mutation 红线违规(继承 immutability 原则)
- **第一阶段排除项**:付费行情源(tushare pro / iFinD / wind)/ HTTPS 公网爬虫 / 微博 / 知乎 / 雪球 / 国防部 RSS / sina mil / 同花顺全球 / 百度衍生 / 卡片交互式资讯展示 — 全部留 P1 / amendment 范围;实施期任何引入即红线违规

**风险红线与 RiskConfig 不可改红线**(P0-7 锁定 2026-05-09,详见 [decisions/P0-7](docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md) §2):

- **`RiskConfig` 任何字段在 runtime 不可改**:`backend/api/risk/*.py` 只允许 `GET` 端点;`backend/agents/` / `backend/llm/` / `backend/mirofish/` 严禁 `from backend.risk import` 或 `from backend.broker.models import RiskConfig`(继承 P0-1 §2 红线 8);`RiskConfig` / `PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig` 必须 `model_config = ConfigDict(frozen=True)`;违规即红线
- **阈值修改流程 = 不可绕过的三步**:`git diff config/risk.yaml` + 同期产出 `P0-7-amendment-{date}-{原因}.md` + 进程重启;任何 runtime hot-reload / setattr / monkey-patch 绕过即红线违规
- **保守仓位三连阈值锁定**:`max_single_stock_pct=0.15` / `max_total_position_pct=0.70` / `max_single_instruction_amount=50000`;调整必须先走 `P0-7-amendment-{date}-{原因}.md`;实施期 lint rule 阻止三常量被覆写
- **中性日内熔断阈值锁定**:`max_daily_new_instructions=5` / `daily_loss_limit_pct=0.05` / `consecutive_loss_count=3` / `cooldown_minutes=60`;调整必须先走 amendment
- **中性 universe 白名单锁定**:`allowed_boards=("sh_main","sz_main","chuangye","etf")`;**禁止 ST / 科创板(688) / 北交所(8x/92x) / 可转债(11x/12x)**;`forbidden_st=true` / `forbid_buy_at_limit_up=true` / `forbid_sell_at_limit_down=true` 全部锁定
- **熔断期间冻结 BUY 类 InstructionPlan**:`CircuitBreakerState.is_in_halt=true` 期间 InstructionPlanBuilder 在早返阶段直接降级 BUY 候选为 HOLD;SELL 类按 `apply_to_sell_orders=false` 默认放行(避免锁仓陷阱);改为 true 必须先走 amendment
- **熔断与 P0-1 切换冻结 / P0-5 ticket 冻结独立并行**:三种买卖类路由冻结来源(切换中 / OPEN-EXPIRED ticket / 熔断冷却)在 ModeRouter 与 InstructionPlanBuilder 中独立判定,任一为真即冻结
- **RiskEngine 14-check 完整性**:`validate_order` 必须依次执行 14 条 check;`InstructionPlan.risk_summary` 长度恒为 14(passed 字段类型放宽 `bool | None`,通过 P0-3 amendment 同步更新);任何"短路时只填 7 条 RiskCheckSummary"或"实施期跳过 check 8-14"即红线违规
- **`backend/risk/` 严禁 import `backend.data` / `backend.llm` / `backend.agents` / `backend.mirofish`**:check 11/12 需要的 `stock_meta` / check 10/12/13/14 需要的 `daily_state` 必须由 InstructionPlanBuilder 装配后传入;RiskEngine 不发起任何 IO(继承 P0-1 §2 红线 8 RiskEngine 纯函数原则)
- **LLM 严禁产出 RiskConfig 字段值或 RiskCheckSummary 结果**:Agent 复盘时仅可写入 `risk_parameter_proposals` collection(只读 ledger),不写入 RiskConfig
- **`risk_parameter_proposals` collection 是只读 ledger**:`accepted` 后必须由人工同步产出 amendment 文档,decision_doc 字段填路径,YAML 修改 + 重启才正式生效;系统不通过 proposal `accepted` 状态自动改 RiskConfig
- **`stock_meta` 与 `daily_state` 缺失即 fail-closed**:check 11(stock_meta=None)/ check 12(stock_meta=None 或 current_price=None 或 prev_close=None)直接 REJECTED;不允许"缺数据时通过"的乐观回退
- **熔断状态持久化必经 `circuit_breaker_repo`**:严禁直接 mutation `CircuitBreakerState` 或在内存中临时存熔断状态;`circuit_breaker_state` collection 是单文档(`_id="singleton"`),所有读写经 repo 接口
- **板块/ST 识别经 `backend/data/stock_metadata.py` 纯函数**:严禁在 `backend/risk/` 内重复实现板块/ST 识别逻辑;严禁绕过 `classify_board` 直接 hard-code 代码前缀于 RiskEngine 内部
- **`PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig` / `RiskParameterProposal` 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 / P0-5 §2 红线 16 / P0-6 §2 红线 14 immutability 原则)
- **第一阶段排除项目**:`max_sector_pct` 字段保留但 RiskEngine 不实现 sector check(留 P1);科创板 / 北交所 / 可转债 / ST 加入 universe 必须先走 amendment;`apply_to_sell_orders=true`(SELL 也熔断)必须先走 amendment

**对账路径红线**(P0-5 锁定 2026-05-09,详见 [decisions/P0-5](docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md) §2):

- **不引入独立 `user_reported_portfolios` collection**:MockBroker 是 `feishu_on` 时唯一账户镜像(继承 P0-1 §2 红线 3 不允许两条平行账本);任何代码尝试新建该 collection 或同义命名即红线违规
- **日终对账必须由系统主动发起**(每个交易日 16:00 Asia/Shanghai 之后系统通过主通道发 `RECON-{YYYYMMDD}-{seq}` 请求);用户主动发的"日终对账"无 ticket_id 一律 AMBIGUOUS,飞书提示"请等待系统在 16:00 自动发起对账"
- **偏差阈值锁定**:cash 1.00 元 / volume 0%(严格相等)/ cost_price 0.01 元;调整必须先走 `P0-5-amendment-{date}-threshold-adjustment.md`
- **超阈值必须创建 reconciliation_ticket**:严禁系统自动以用户回报覆盖 MockBroker;严禁系统拒绝接受用户回报让用户卡死;只有用户飞书显式三选一裁定(`对账采纳:用户回报 / 对账采纳:系统镜像 / 对账更正`)才允许覆盖
- **OPEN/EXPIRED ticket 期间冻结买卖类 InstructionPlan 路由**:ModeRouter 必须在路由前查询 ticket 状态;冻结只在路由阶段(simulation_auto 与 InstructionPlanBuilder 仍持续运行,前端可见冻结期间系统决策)
- **LLM 严禁参与对账路径**:`backend/services/reconciliation*.py` / `backend/integrations/feishu/reconciliation*.py` 严禁 `import backend.llm.*`;对账请求 / 裁定卡 / 澄清飞书全部预写死,LLM 不生成
- **`ticket_id` 必须严格匹配** `^RECON-\d{8}-\d{3}$`;同一 trade_date seq ≥ 1000 抛 ValueError;长度恒 16 字符
- **5 种用户对账回报形态严格正则 only**(对账无误 / 对账差异 / 对账更正 / 对账采纳:用户回报 / 对账采纳:系统镜像);任何不通过即 AMBIGUOUS,绝不更新 ticket / MockBroker
- **绝不猜测 ticket_id**:回报中 ticket_id 必须严格匹配且当前 status ∈ `{OPEN, EXPIRED}`;系统不通过上下文反推
- **`对账更正` 与 P0-4 `更正` 严格不混淆**:dispatcher 按前缀路由,zero crossover;违规即红线(`对账更正` 关联 ticket_id 覆盖整账户镜像;`更正` 关联 instruction_id 覆盖单条成交)
- **公司行动第一阶段不支持自动处理**(分红 / 配股 / 送转 / 停牌等);任何在 `backend/services/reconciliation*.py` 中引入复权计算 / 自动送股 / 自动加现金 / akshare/adata/baostock 复权字段的代码都属红线违规;必须先走 amendment
- **MockBroker 覆盖必须经过 ReconciliationApplier**(`reset_to_snapshot` 入口);严禁直接 mutation MockBroker 内部 `_cash` / `_positions` / `_trades`;实施期由 lint rule 阻止
- **裁定卡严禁走备用 webhook**(继承 P0-2 §2 红线 2 / P0-4 §2 红线 13);主通道失活时不发裁定卡,只发"主通道异常"告警
- **券商真实账户读取严禁**:绝不引入读取券商 API / 解析券商 PDF / 抓券商账户网页的代码(继承 P0-1 §2 红线 1);用户成交单只能离线人工对照
- **ReconciliationTicket 状态机迁移必须经守门函数**(`reconciliation_state_machine.py::transition_ticket`);任何 `model_copy(update={"status": ...})` 直接绕过即红线违规(继承 P0-4 §2 红线 14)
- **DailyReconciliation / ReconciliationTicket 是 frozen Pydantic v2 模型**;就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 immutability 原则)

**验收路径红线**(P0-6 锁定 2026-05-09,详见 [decisions/P0-6](docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md) §2):

- **验收期固定 45 个交易日滚动窗口**:任何尝试在新代码中读取 `WINDOW_TRADING_DAYS` 之外的常量(30 / 20 / 60 等)即红线违规;调整必须先走 `P0-6-amendment-{date}-window-length.md`
- **5 项稳定性硬门槛 + 3 项策略硬门槛阈值锁定**:95% / 99% / 1% / 5% / 95% / 8% / 0 / 0;调整任一阈值必须先走 amendment;实施期 lint rule 阻止阈值常量被修改
- **LLM 严禁参与验收路径**:`backend/services/acceptance*.py` / `backend/services/equity_curve.py` 严禁 `import backend.llm.*`(继承 P0-4 §2 红线 2 / P0-5 §2 红线 6)
- **A 股节假日表静态 YAML**:第一阶段不引入 akshare/adata/baostock 节假日 API;只读 `config/a_share_holidays_*.yaml` 人工年度更新
- **基准固定沪深 300**(`000300.SH`):切换基准必须先走 `P0-6-amendment-{date}-benchmark-change.md`
- **`equity_curve` 不可绕过 mark-to-market**:每个交易日 15:00 之后必须有一条 `EquityPoint` 写入;`equity_points` collection 缺日红线违规;数据降级时仍写入但标 `mark_quality='degraded'`
- **回报解析准确率 99% 在 feishu_off 模式下也要求**:SimulationExecutor 必须按 P0-4 §1.2 模板渲染 ExecutionReport 文本,经过相同 parser 解析后才能更新 MockBroker;严禁绕过 parser 直接更新
- **P0 系统级中断 5 类定义锁定**:行情连续断流 30min / LLM 全停 1h / MockBroker 损坏 / 状态机非法迁移 / 长连接 4h;新增类别必须先走 amendment
- **reconciliation 冻结只暂停不重置**:任何代码尝试在冻结日触发 `reset_window_on_p0()` 或同语义函数即红线违规
- **切换 API `POST /api/run-mode/transition` 必须前置 `acceptance_report.can_switch_to_feishu_on()` 校验**:返回 False 时立即 HTTP 403 + 预写死 blockers 文案;严禁 env var / CLI 绕过(继承 P0-1 §1.4)
- **`acceptance_reports` collection 每日仅 1 条**:`(report_date)` 唯一索引;同日重新生成必须 upsert(防"试探" + 防数据污染)
- **观察指标不阻断切换 = 不参与 `can_switch_to_feishu_on` 计算**:任何把观察指标写入 `can_switch_to_feishu_on` 判断式的代码即红线违规
- **验收期内禁止"绕过指标修改 MockBroker"**:用户不能通过前端直接编辑 MockBroker 现金 / 持仓(继承 P0-5 §2 红线 12 ReconciliationApplier 入口收口);否则 equity_curve 失真,PnL / 最大回撤指标无意义
- **基准数据缺失保守判定 = 0**:`benchmark_excess_return=0`(等价边界 PASS);严禁基准缺失时跳过校验或假阳性 PASS
- **AcceptanceReport / EquityPoint / P0InterruptRecord 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 / P0-5 §2 红线 16 immutability 原则)

**watchlist 与频率红线**(P0-9 锁定 2026-05-09,详见 [decisions/P0-9](docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md) §2):

- **watchlist 总 codes 数恒定 = 13**(10 个股 + 3 ETF):`config/watchlist_policy.yaml.watchlist.total_codes` 必须为 13;改动必须先走 `P0-9-amendment-{date}-watchlist-size.md`;实施期 lint rule 校验 `len(default_codes) == 13`
- **watchlist 板块组成恒定** = `sh_main:4 / sz_main:3 / chuangye:3 / etf:3`;任一板块数量改动必须先走 amendment;`backend/data/watchlist.py::validate_composition()` 启动时强校验
- **3 只必备 ETF 永锁** = `510300`(沪深300)+ `510500`(中证500)+ `159949`(创业板50);新增/替换 ETF 必须先走 `P0-9-amendment-{date}-etf-list-expand.md`;严禁第一阶段加行业 ETF / 主题 ETF / 跨境 ETF / 商品 ETF / 货币 ETF
- **科创板(688x)/北交所(8x/92x)/可转债(11x/12x) 在 watchlist 中永禁**(继承 P0-7 §1.3.4;此处复述);任何代码尝试加入这三类 codes 即红线违规
- **watchlist 在 runtime 不可改**(继承 P0-7 §1.4 RiskConfig 全锁精神):`config/watchlist_policy.yaml.watchlist.default_codes` 列表只能通过 git diff + amendment + 进程重启变更;`backend/api/watchlist*.py` 只允许 `GET` 端点;旧 `WatchlistService.add_stock` / `remove_stock` / `clear` 方法标 deprecated 不暴露在 FastAPI route
- **watchlist 排除规则四件套阈值锁定**:`ipo_min_trading_days=30` / `sub_new_min_trading_days=180` / `min_avg_amount_20d_yuan=200_000_000` / `max_unit_price_yuan=500.0`;调整任一阈值必须先走 amendment
- **watchlist 排除规则在 InstructionPlanBuilder 早返**:**不进** RiskEngine 14-check;`backend/risk/` 严禁实现 IPO/SUBNEW/LIQUIDITY/HIGHPRICE 排除逻辑;实施期 lint rule 阻止 RiskEngine `_check_*` 方法引用 `ipo_date` / `avg_amount_20d` / `limit_price * 100`
- **watchlist 排除规则数据缺失即 fail-closed 降级 HOLD**:`stock_meta.ipo_date=None` / 流动性数据不足 20 交易日 / `limit_price=None` 即降级 HOLD;不允许"缺数据时通过"的乐观回退(继承 P0-7 §2 红线 13 fail-closed 精神)
- **5 单 cap 分配锁定**:`total_daily_cap=5`(继承 P0-7)/ `traditional_path_default_cap=4` / `event_path_reserved_cap=1`;调整 cap 分配必须先走 amendment;`event_path_reserved_cap > 1` 永禁(MiroFish 不夺主精神)
- **MiroFish 事件路径不得占用 traditional 主路径 cap**:`backend/services/instruction_plan_builder.py` 必须显式标记 `path: Literal["traditional", "event"]`;`cap_allocator` 按 path 字段独立计数;实施期 lint rule 阻止把 MiroFish severity 映射到 path=traditional 的 candidate
- **event_path_reserved_cap=1 用满后即使 severity=CRITICAL 也不再发新 InstructionPlan**:仅写 `evidence_collection`;实施期单元测试强覆盖
- **fast/slow 双频架构锁定**:`fast.cron="0 9,11,13,15 * * mon-fri"` / `slow.cron="0 9 * * mon-fri"`;改 cron 必须先走 amendment;`pipeline_timeout_seconds` 阈值(fast 480 / slow 900)同样锁定
- **InstructionSide 永锁** `{BUY, SELL, HOLD}`(继承 P0-3 §2 红线 2;此处复述);任何尝试加 SHORT/COVER/MARGIN_BUY/REVERSE_REPO/ETF_SUBSCRIBE/ETF_REDEEM 即红线违规;P1 加 ETF 套利必须先走 `P0-9-amendment-{date}-etf-arbitrage-enable.md`(届时还要扩 P0-3 InstructionSide / P0-7 RiskEngine check)
- **`broker.allowed_instruments` 永锁** `{sh_main, sz_main, chuangye, etf}`:`backend/broker/mock_broker.py::ALLOWED_INSTRUMENTS` 用 `frozenset` 不可变;严禁 runtime 修改;新增板块/工具必须先走 amendment
- **SELL 仅可对已持仓 codes**(继承 RiskEngine check 5);MockBroker 永远不维护 short_position 字段(`MockBroker.short_position={}` 永远空字典)
- **ETF 套利 P1 预留接口永锁 disabled**:`config/broker.yaml.etf_arbitrage_enabled=false`;`backend/broker/etf_arbitrage_stub.py` 仅含 `class ETFArbitrageStub: NotImplementedError`;`backend/api/etf_arbitrage*.py` 不创建 router;启用必须走 amendment
- **`WatchlistPolicy` / 衍生 schema 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 / P0-4 / P0-5 / P0-6 / P0-7 / P0-8 immutability 原则)

**安全红线**:

- LLM key / 飞书凭证(`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`)仅走 shell env(`~/.bashrc`),永不入 `.env`、永不入 git
- MongoDB / Redis 端口仅绑定 `127.0.0.1`
- 前端不能直接显示完整密钥(只允许末四位脱敏 + `webhook_configured` 布尔)

**流程红线**:

- 不自动跨阶段推进,Phase 末必须 STOP + summary 报告等用户授权
- 不自动 push,本地 commit 后等用户授权再推远端
- IPv4-only 出口:`httpx` 客户端必须 `local_address="0.0.0.0"`,host 无 IPv6 默认路由

### 3.2 必须遵守的工程原则

**编码**

- 注释 / commit message 用英文;UI 文本与文档用中文
- public function 必须有 type hints + docstring(WHY,不是 WHAT)
- 配置走 YAML;LLM 调用必须 try/except,降级而非崩溃
- 不可变数据结构优先(`@dataclass(frozen=True)` / `NamedTuple`)
- 文件 200-400 行典型,800 行上限;函数 <50 行;嵌套 <4 层

**进度管理**

- TaskCreate/TaskUpdate 全程跟踪,`in_progress` 严格只挂 1 个
- 每个 task 完成后立刻 mark completed,不批量
- 报告"完成"前必须先把状态文档(决策表/计划/SSoT)更新好 + 填真实 commit hash + 报告里明确说改了什么;不要让用户当 reviewer 抓遗漏
- 跨 session 接手第一件事:读本 CLAUDE.md + audit + 决策清单 + `MEMORY.md` 索引,不重新发明状态

**质量门禁**

- **Codex review 是 hard gate**(从 P5A-T02 5 轮发现 6 issue / P5A-T03 3 轮发现 3 P2 沉淀的经验):major 5 轮 R1-R5,minor R1+R3 两轮,输出存 `docs/reviews/{task_id}-r{N}-{topic}.md`,触发前 `git pull` 同步 [LanEinstein/CCodexSkill](https://github.com/LanEinstein/CCodexSkill) 到 `~/.claude/skills/codex-review/`
- 后端测试金字塔 + ruff 全绿才允许 commit;非 risk 模块覆盖率 >70%,risk 模块 >95%
- **测试通过 ≠ 闭环可用**:测试断言要覆盖"被谁调用、贯穿到哪",不能只测"自身行为正确"(audit 已揭示 1139 绿 + RiskEngine 不接订单的反面教材)

### 3.3 已经踩过的坑(沉淀的判断)

- **完整升级路径优先**:不为省工作量妥协系统可用性(用户明确授权)
- **Fail-closed for data corruption / fail-open for infra glitches**:NaN/Inf/负值在 `cost_rmb` / `spent_today` 等数据层与守门层做双层校验;Redis ConnectionError 让 scheduler 兜底通过
- **抽出独立模块换取可测性**:authorization / cost_guard 都从原本散落的逻辑提到 `backend/services/`
- **Codex CRITICAL 是真 bug**:mocks accept any kwargs;codex 抓到 Kimi SDK 不兼容时 125 个绿测试全过 — 信 codex R4 SDK 签名指认胜过绿测试套件
- **Handoff 文档要详尽**:计划/SSoT 文档未来 session 会读,必须有完整代码片段、精确命令、预期输出 — 不能只列大纲
- **测试通过 ≠ 闭环可用**:audit 揭示 1139 测试全绿但 RiskEngine 实际未接入订单链路 — 测试要追问"被谁调用",不仅是"自身行为正确"
- **方向重构要彻底**:CLAUDE.md 早期把旧方向 P0-1/P0-2 标注 ✅ 但链接的决策文件并未落地;audit 重写后这些标注必须及时清理,不能留半新半旧的状态

### 3.4 操作速查(过渡期,P0-1 已锁定但代码迁移待实施期统一进行)

```bash
# 后端启动 — 当前仍是旧 env var(代码尚未迁移,P0-1 落地后改为 FEISHU_INTERACTIVE_ENABLED=false)
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000

# 前端(避开 Open WebUI 占用的 3000)
cd frontend && npm run dev   # listens on :9276

# 测试 / 验证
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70
pytest -q backend/risk --cov=backend/risk --cov-fail-under=95
cd frontend && npm run type-check && npm run test -- --run && npm run build

# 红线静态检查
grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish\|from backend.data" backend/risk/
grep -rn "from backend.risk\|RiskConfig\|PositionLimitsConfig\|CircuitBreakerConfig\|UniverseConfig" backend/llm/ backend/agents/ backend/mirofish/
grep -rn "@router.post\|@router.put\|@router.patch" backend/api/risk/
grep -rn "from backend.llm\|import backend.llm" backend/data/data_quality.py backend/data/divergence.py backend/data/staleness.py backend/data/suspension.py
grep -rn "MiroFish.*RiskCheckSummary\|RiskCheckSummary.*MiroFish" backend/  # P0-8 红线 6
grep -rn "@router.post\|@router.put\|@router.patch\|@router.delete" backend/api/watchlist*.py  # P0-9 红线 5
grep -rn "ipo_date\|avg_amount_20d\|max_unit_price" backend/risk/  # P0-9 红线 7
grep -rn "SHORT\|COVER\|MARGIN_BUY\|REVERSE_REPO\|ETF_SUBSCRIBE\|ETF_REDEEM" backend/data/instruction_plan.py  # P0-9 红线 13
grep -rn "etf_arbitrage_enabled" config/broker.yaml | grep -v "false"  # P0-9 红线 16
```

LLM key 永远走 shell env:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书 key(P0-2 已锁定 2026-05-09,实施期启用):
- 主路径(自建应用 + 长连接):`FEISHU_APP_ID` / `FEISHU_APP_SECRET`;`FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` 预留(若 `lark-oapi` 长连接路径需要)
- 备用通道(自定义机器人 webhook,仅系统告警):`FEISHU_CUSTOM_BOT_WEBHOOK_URL` / `FEISHU_CUSTOM_BOT_SIGN_SECRET`(可选,启用 HmacSHA256 签名时)

`.env` 仅放非密配置(`MONGODB_URI`、`QUANTMIND_DAILY_BUDGET` 等;P0-1 锁定后实施期会替换为 `FEISHU_INTERACTIVE_ENABLED`,旧 `AUTHORIZATION_MODE` / `QUANTMIND_PHASE` 一次性破坏式删除)。

---

## 4. 重要文档

> 决策对齐期最重要的三份文档(按阅读顺序)

| 路径                                                     | 类型     | 用途 |
|----------------------------------------------------------|---------|------|
| `docs/quantmind_project_audit_2026-05-07.md`             | 全景盘点 | 当前系统真实状态、新目标架构、模块连接、关键差距清单、推荐路线图 — **接手第一份必读**,2026-05-08 重写 |
| `docs/quantmind_owner_decision_points_2026-05-07.md`     | 决策清单 | P0/P1/P2 决策前置清单,每个决策点的关键问题、建议倾向与产出物要求 — 2026-05-08 重写 |
| `docs/decisions/`                                         | 决策归档 | 已锁定决策文档(`{决策编号}-{结果简述}.md`),只放定稿。当前只有 `README.md`,未有定稿决策 |

### 4.1 历史/参考文档(只读,与新方向有偏差)

| 路径                                                | 状态        | 说明 |
|----------------------------------------------------|-------------|------|
| `docs/phase5-eval-and-phase6-prep-master-plan.md`  | 🛑 暂停推进 | 旧 SSoT,Phase 5B 出口已就位,但**整体方向待 P0 决策后重写**;旧 phase 命名将被 run_mode 取代 |
| `docs/QuantMind_Project_Blueprint_V3.md`           | 📜 早期蓝图 | 最初愿景文档,与 audit 揭示的实际状态有偏差,作历史参考 |
| `docs/reviews/`                                     | 📜 阶段记录 | codex review 报告 + 阶段 summary + Phase 5B shadow runbook(基于旧方向,新方向下部分结论需要重新评估) |

### 4.2 用户私有规范(全局,跨项目通用)

读取自 `/home/ps/.claude/rules/`:`coding-style.md` / `git-workflow.md` / `development-workflow.md` / `testing.md` / `security.md` / `performance.md` / `agents.md` / `hooks.md` / `patterns.md`,以及 `python/` / `typescript/` 子目录下的特化版本。

### 4.3 自记忆索引

`/home/ps/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md` — 跨 session 持久的用户偏好/反馈/项目状态/外部参考。

> 注意:`project_phase5_status.md` 记录的是 Phase 5B 出口状态,基于旧方向。新方向决策对齐期开始后,该条目会随决策落定逐步更新或退役。

---

_本 CLAUDE.md 是过渡骨架。决策对齐期完成后会基于决策表整体重写,届时本节末尾会移除此说明。_
