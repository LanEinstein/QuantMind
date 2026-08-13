# M2 接手说明：G1 owner 审查与后续全量分析

> 日期：2026-08-13
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 接手分支：`agent/m2-evidence-reconstruction`
>
> 已推送代码恢复点：`997265d feat(research): complete M2 pilot evidence gate`
>
> 上游接手文档：`KickoffPrompts/M2-phase-B-pilot-continuation-2026-08-13.md`
>
> 当前阶段：M2 阶段 B pilot 已完成；**G1 已由 owner 于 2026-08-13 明确通过**
>
> 后续入口：`KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`

## 0. 接手后直接做什么

不要重做阶段 A 的 30 GB PIT 资产审计，也不要重做已经完成的九条 pilot 和江波龙黄金案例。接手后的第一任务是根据 owner 的明确回复处理 G1：

owner 已给出“预期—反馈偏差”方向、要求重查 8 月 2 日券商事件，并明确回复“G1通过，可以进入全量分析”。这些结果均已落档。本文件不再作为下一上下文的执行入口；阶段 C 从上方“后续入口”接手，不重做 pilot、G1 或既有数据审计。

G1 只决定是否允许从 pilot 进入全量语料研究。全量研究完成、规则基础规范形成并通过后续 G2 以前，不实现 `backend/playbook/yeren/` 的确定性执行器。

## 1. 开始前读取顺序

必须完整读取：

1. `AGENTS.md`；
2. `CLAUDE.md`；
3. 本文件；
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`；
5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`；
6. `docs/research/yeren-system/research-methodology.md`；
7. `docs/research/yeren-system/data-and-source-coverage.md`；
8. `docs/research/yeren-system/casebook.md`；
9. `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
10. 上游接手文档 `KickoffPrompts/M2-phase-B-pilot-continuation-2026-08-13.md`，只用于恢复已完成路径，不以其中的旧进度覆盖本文件。

代码发现继续遵循仓库规则：项目图谱名为 `home-ps-papers-QuantMind`，优先使用 codebase-memory MCP 的 `search_graph`、`trace_path`、`get_code_snippet` 和 `query_graph`；字符串、非代码文件和图谱不足时才使用 `rg`。

## 2. 不可突破的边界

1. 永禁真实券商程序化下单。只允许研究、证据复原、回放、回测和模拟盘。
2. `data/marketdata_pit/`、`data/yeren_corpus/` 和 `data/yeren_research/` 都按 append-only 处理。既有档案不删除、不覆盖、不从零重下。
3. 原始证据、主 agent 解释和规则假设必须分层。`RawEvidence` 的转写原文以 `transcript_span.raw_text` 为准，不能把可读化改写冒充原话。
4. 修订 observation/case 使用新文件名或追加修订记录；hypothesis、event 和 worklog 使用 JSONL 追加，不覆盖历史判断。
5. decision bundle 只放决策截止时点已经可见的材料；未来行情只能进入 outcome bundle。
6. 主体消歧、核心视频语义、规则冲突、消息含义和交易动作必须由当前主 agent 阅读完整上下文后判断。关键词程序只找候选，不得调用批量摘要模型替代逐视频判断，也不得委派给子 agent。
7. 没有分钟、竞价、盘口或成交单时，只做日级或方向性复原；不能从日线高低价倒推盘中成交点。
8. 没有龙虎榜、订单流或原始消息时，量化、游资、主力和传闻主体保持未知；不能用后续涨跌反推资金身份。
9. 只有公告日期、没有精确发布时刻时，保守从下一存档交易日 09:30 起允许进入决策。
10. 反过度防御继续生效：不建评分表替代判断，不预建兼容层/功能开关，不补与真实案例无关的数据。

## 3. Git、远端与本地研究区状态

代码恢复点已于 2026-08-13 推送：

```text
branch: agent/m2-evidence-reconstruction
remote: origin https://github.com/LanEinstein/QuantMind.git
commit: 997265d feat(research): complete M2 pilot evidence gate
tracking: origin/agent/m2-evidence-reconstruction
```

接手时先执行：

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
```

重要：`data/yeren_research/` 被 Git 忽略，里面的 observation、bundle、case、event、hypothesis 和 worklog **不在远端提交中**。同一工作区接手时这些文件应仍存在；如果换机器或该目录缺失，不能只按文档重新生成并覆盖历史版本，应先请求恢复/转移本地 append-only 研究区。

最小存在性检查：

```bash
test -f data/yeren_research/worklog.jsonl
test -f data/yeren_research/hypotheses.jsonl
test -f data/yeren_research/cases/pilot-jiangbolong-earnings-2026-07-03-to-2026-07-08.json
test -f data/yeren_research/cases/pilot-position-add-boundary-2025-09-to-2026-06.json
find data/yeren_research/observations -maxdepth 1 -type f | wc -l
```

预期 observation 工件数为 12，对应 11 个唯一视频；`7669063381873462208-v1.1.json` 是 8 月 2 日券商案例的当前修订版，旧版按 append-only 保留。owner 已通过 G1；最新门禁记录的 `work_unit` 是 `M2-G1-owner-gate-passed`，状态为 `completed`，`resume_from=M2-C-batch-001`。阶段 C 以新接手文档为准。

## 4. 阶段 A 与阶段 B 已完成范围

### 4.1 阶段 A 基线

阶段 A 已完成语料、PIT、新闻和研究 schema 审计：

- corpus 元数据 1,088 条，转写文件 1,087 份；一条终态不可得；
- PIT 共 23 个 endpoint、24,290 条索引记录；2026-08-13 增量更新后，A 股 `daily`、`adj_factor`、`daily_basic` 及大部分 QGR/事件端点已到 2026-08-13，`fund_daily`、`cyq_perf`、`stk_factor_pro` 因上游当日空帧保持在 2026-08-12；
- 本地新闻 91,317 条，但最早仅到 2026-05-29，且当前抓取器不是严格历史 `as_of` 新闻库；
- 两条空文本视频已做定向音画复核，没有伪造转写；
- observation、hypothesis revision 和 decision/outcome evidence bundle 的冻结 Pydantic schema 已落地。

机器清单继续使用：

- `data/yeren_research/inventory/assets-2026-08-13-v1.3.json`（`v1.1`、`v1.2` 按 append-only 保留）；
- `data/yeren_research/inventory/news-2026-08-13.json`；
- `data/yeren_research/events/benchmark-events-2026-08-13.jsonl`。

### 4.2 九条挑战性 pilot

九条均已有 `analysis_status=analyzed` 的机器可读 observation：

| 视频 ID / 日期 | 核心覆盖 | 当前结论 |
|---|---|---|
| `7526939325557165347` / 2025-07-14 | 混沌、退潮、停止出手、止损、试错、业绩硬逻辑 | 混沌是模式解释力丧失，退潮需核心负反馈；“三天左右”不是固定计时器 |
| `7534671965824175412` / 2025-08-04 | 雅江、模式内确定性、推仓、次日纠错、参与者结构 | 推仓必须与不及预期退出配套；量化/游资身份未被 PIT 证明 |
| `7552015322325699840` / 2025-09-20 | 禁止补仓、原因型离场、一至三成示例 | 下跌摊成本是稳定禁令；个股高位滞涨退出，市场错杀可持有但不加仓 |
| `7562170397225258280` / 2025-10-17 | 交易系统 ontology、本金、开仓、仓位、卖出 | 开仓、仓位、卖出和预期分支须预设；数字胜率/仓位只是教学示例 |
| `7566602982977376372` / 2025-10-29 | 业绩兑现、利空落地、ETF | 财报含义依赖发布前预期与位置；公司实体不明，因此没有补造财务包 |
| `7602233626348496192` / 2026-02-02 | 上午十点清仓、退潮、不信消息、轻仓试错 | 清仓后等待情绪拐点再轻仓试错；“轻仓”是有依据的 ASR 修订，比例未知 |
| `7626700051746734178` / 2026-04-09 | 流动性与消息、PCB/存储业绩落地离场 | 指数承接先看资金筹码；公司/板块层仍可由财报和产业传导改变动作 |
| `7647445389893913039` / 2026-06-04 | 状态输入、主线、龙头、赚钱效应、超预期加仓 | 已形成第一版多输入 ontology，但缺阈值、权重和优先级，不能硬写状态机 |
| `7669063381873462208` / 2026-08-02 | 首周五成上限、消息、竞价、前后排退出 | 五成是阶段规则；券商事件族已高置信复原，博主所指精确子集仍未知 |

对应 observation 文件名均为 `data/yeren_research/observations/<aweme_id>.json`。

### 4.3 江波龙黄金案例附加 observation

黄金案例另有两条：

- `7658346105893142863`：2026-07-04 公司级财报解释；
- `7660119917865709391`：2026-07-08 高位科技风险、修复离场和禁止补仓。

因此当前仍是 11 个唯一视频；8 月 2 日案例有一份追加修订，机器工件总数是 12。

## 5. 江波龙财报黄金案例

### 5.1 实体与官方事件

公司为江波龙 `301308.SZ`。消歧依据包括视频标题 `#江波龙`、正文谐音、同比下限 62,204.03% 和 Q1 数值，能够唯一对应公司。

巨潮公告：

- 公告编号：`1225409631`；
- 标题：`2026年半年度业绩预告`；
- 发布时间：`2026-07-03T18:47:07+08:00`；
- 原文：`https://static.cninfo.com.cn/finalpage/2026-07-03/1225409631.PDF`；
- 最早 A 股动作时刻：`2026-07-06T09:30:00+08:00`。

公告事实：

- 预计 2026H1 归母净利润 92 亿至 110 亿元；
- 同比增长 62,204.03% 至 74,393.95%；
- 扣非归母净利润 90 亿至 105 亿元；
- 预计营收 220 亿至 250 亿元；
- 数据为初步测算，未经审计。

视频说“53 亿到 71 亿”，与正式公告无法对齐。该口播只保留为冲突，不用于计算或实体确认。

### 5.2 决策时 PIT 财务

截至 2026-07-04 视频时点可见的关键 Q1 数据：

- `fina_indicator_vip`：毛利率 55.5274%，净利润同比 2644.0497%，营收同比 132.7928%，ROE 38.548；公告日 2026-04-28；
- `income_vip`：归母净利润 3,862,236,561.17 元，营业收入 9,908,726,244.41 元，利润总额 4,761,495,574.22 元；
- `cashflow_vip`：经营活动现金流净额 -2,874,539,454.35 元，自由现金流 -3,392,462,286.4926 元；
- 2025 年度毛利率 19.3985%。

这些数据支持检查低基数、阶段加速、毛利率和现金质量，但不能证明利润主要来自“低价囤货、高价出售”。公告列出的原因是需求、存储晶圆供给增长有限、供应协议和端侧 AI 产品，因果解释必须继续与财务事实分开。

### 5.3 当前有效 bundle

只能引用以下 `v1.2`：

- `data/yeren_research/decision_bundles/pilot-jiangbolong-earnings-2026-07-04-v1.2.json`；
- `data/yeren_research/decision_bundles/pilot-jiangbolong-earnings-2026-07-04-v1.2-financial.json`；
- `data/yeren_research/decision_bundles/pilot-jiangbolong-earnings-2026-07-04-v1.2-announcement-and-statements.json`；
- `data/yeren_research/outcome_bundles/pilot-jiangbolong-earnings-2026-07-04-v1.2.json`；
- `data/yeren_research/cases/pilot-jiangbolong-earnings-2026-07-03-to-2026-07-08.json`；
- `data/yeren_research/events/jiangbolong-half-year-forecast-2026-07-03.json`。

无后缀、`v1.1` 及旧 financial 文件因 append-only 原则仍保留。它们可能在 Pydantic 层合法，但存在 endpoint 重复或旧财务期选择问题，语义上不是当前版本；不能因为“校验通过”就重新引用。

### 5.4 动作与结果隔离

7 月 4 日的动作变化是从“看超高同比标题”转为观察并检查低基数、现金质量和主营来源，等待正式半年报；原话还明确区分财务见顶和股价见顶，因此不能伪造成当天卖出信号。

7 月 8 日才出现更明确的风险动作：高位科技调整中的修复用于纠错离场，不是入场或补仓。江波龙财报是风险依据之一，不能声称是唯一原因。

7 月 6—10 日价格路径只在 outcome bundle：

| 日期 | 收盘 | 涨跌幅 | 角色 |
|---|---:|---:|---|
| 2026-07-06 | 681.80 | +10.3201% | outcome |
| 2026-07-07 | 627.90 | -7.9055% | outcome |
| 2026-07-08 | 592.71 | -5.6044% | outcome |
| 2026-07-09 | 620.00 | +4.6043% | outcome |
| 2026-07-10 | 587.60 | -5.2258% | outcome |

这些结果不能倒灌为 7 月 4 日解释，也不能用来选择更赚钱的财报语义。

## 6. “不能浮盈加仓”与“超预期加仓”边界

机器案例：`data/yeren_research/cases/pilot-position-add-boundary-2025-09-to-2026-06.json`。

### 6.1 教学锚点

- 2025-09-20 明确反对下跌补第二、第三笔，也说“不能浮盈加仓”；紧接的“退/推仓位”两套 ASR 仍无法消歧。
- 2026-06-04 把符合预期、不及预期、超预期分别对应持有、止损和加仓/退推仓，但这是教学框架，没有绑定当日真实动作。

### 6.2 2026 年 6 月真实链

- 6 月 3 日计划空仓三至五天；
- 6 月 5 日仍自报空仓；
- 6 月 8 日用小账户重新开三个方向，同时说明大账户中未破逻辑的波段仓一直保留。

该链说明“空仓”具有账户和交易类型层级，也说明 6 月 4 日教学句没有在相邻实盘中无条件触发加仓。它不是浮盈加仓案例。

### 6.3 2025 年 11 月真实链

- 11 月 3 日预先声明短线次日不够强就退出并转波段；
- 11 月 4 日上午自报卖出短线龙头、买入波段后被套；
- 当日晚间自报竞价加仓，冲高回落后卖出前一日可交易仓位，把暴露降回原水平，避免变成重仓。

该链支持“新的预设证据可能触发临时增加暴露，反馈失效后撤回”，但加仓前盈亏、证券、账户和竞价信号仍未知。

### 6.4 当前规则结论

当前有效收窄修订是 `H-POSITION-CONVICTION-001-R2`：

- 浮盈或浮亏本身都不是加仓触发器；
- 新的、动作前已经定义的模式内证据才可能增加暴露；
- 必须显式区分同票加仓、策略子仓和组合推仓；
- 后续反馈使证据失效时撤回新增暴露。

owner 已明确目标系统的处理方向：在动作前基于交易内核和真实可见的场内外信息冻结预期，再按指定窗口的真实反馈区分超预期、符合预期和不及预期。浮盈只是状态，不是触发器；同票新增暴露要由反馈相对预期的偏差讨论，并预设证据失效后的撤回。

该方向见 `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`。它是 owner 对目标系统的设计约束，不是博主原话。语料层仍未解决博主是否允许同一证券已有浮盈后因新超预期证据继续加仓，也不能排除 2025 年 9 月至 2026 年 6 月之间的系统演化。

## 7. 2026-08-02 券商事件

原基准事件及两次追加修订都位于：

`data/yeren_research/events/benchmark-events-2026-08-13.jsonl`

owner 要求根据博主措辞联网重查后，事件族已经高置信复原：

- 证监会于 2026-07-31 17:27—18:18 集中公开六份决定书：国元、国融、甬兴被责令改正，广发、世纪、红塔被出具警示函；
- 湘财股份和国盛证券于 8 月 1 日分别公告湘财证券、国盛证券因账户实名制等问题被立案；
- 事件公开时点在周五收盘后至周末，最早 A 股动作时刻为 8 月 3 日 09:30；
- 完整机器记录为 `data/yeren_research/events/broker-regulatory-actions-2026-07-31.json`，当前 observation 为 `data/yeren_research/observations/7669063381873462208-v1.1.json`。

结论：原 benchmark 从“事件未知”修订为“事件族高置信、博主精确指代子集未知”。视频没有名单，不能断言“涉事的几家”指八家全体、六家监管措施对象、两家立案对象还是其中上市券商；也不能推定博主持仓或成交。

## 8. Hypothesis 台账现状

`data/yeren_research/hypotheses.jsonl` 当前 20 条追加记录。分类不是评分，而是研究语义边界。

### 8.1 稳定核心候选

- `H-SYSTEM-PRESET-001`：开仓、仓位、卖出和预期分支在入场前成套定义；
- `H-CAPITAL-FIRST-001`：本金与回撤优先，系统或节奏失效时允许停止交易；
- `H-AVERAGING-DOWN-BAN-001`：不以摊低成本为理由在下跌中补第二、第三笔。

“稳定核心”只表示 pilot 中跨时期证据一致，不等于 Base v1 已冻结。

### 8.2 候选规则

- `H-POSITION-CONVICTION-001`：阶段 A 原始推仓假设；使用时必须同时读取 R2；
- `H-POSITION-CONVICTION-001-R2`：浮盈不是触发器，新证据可临时增加暴露，边界仍未知；
- `H-EXIT-EXPECTATION-001`：短线次日不及预期及时纠错；
- `H-SELECTION-HARD-LOGIC-001`：硬逻辑与业绩是过滤器，不是独立买点；
- `H-MARKET-CHAOS-RETREAT-001`：混沌停止新增风险，核心负反馈后转退潮；
- `H-REENTRY-LIGHT-TRIAL-001`：退潮后等可辨认拐点轻仓试错；
- `H-EXIT-CAUSE-DIAGNOSIS-001`：区分个股失效和市场错杀；
- `H-EARNINGS-EXPECTATION-001`：财报含义依赖原预期和发布前位置；
- `H-EARNINGS-QUALITY-001`：核查低基数、绝对利润、毛利与现金质量；
- `H-NEWS-STATE-WEIGHT-001`：消息权重依赖指数/板块/单票问题层级；
- `H-MARKET-LIQUIDITY-PRIORITY-001`：指数承接先看资金与筹码；
- `H-MARKET-STATE-INPUTS-001`：多输入市场状态 ontology；
- `H-TIERED-EXPECTATION-EXIT-001`：前排/后排和延续预期的分层退出。

### 8.3 阶段规则

- `H-PHASE-EXPOSURE-CAP-001`：2026 年 8 月首周约五成组合上限，只绑定该阶段，不能外推为永久参数。

### 8.4 战法特例

- `H-MICROSTRUCTURE-RELAY-001`：量化退出/游资接力；缺龙虎榜和订单流，尚不可确认；
- `H-ETF-INDEX-EXPRESSION-001`：个股选择或雷区风险高时用 ETF 表达方向；
- `H-NEWS-DIRECT-HARM-EXIT-001`：被原始事件直接点名的持仓优先处理；事件族已解析，但视频所指精确证券子集仍不足以执行化。

全量分析中必须继续追加支持、反证、例外和替代解释；不能只收集支持材料。

## 9. G1 七个功能维度

紧凑审查面：`docs/research/yeren-system/g1-pilot-review-2026-08-13.md`。

| 维度 | 主要案例 | 已覆盖 | 未解决 |
|---|---|---|---|
| 进攻 | 雅江、6 月 4 日 ontology | 模式确认、主线/龙头、超预期候选输入 | 单票/组合推仓和比例 |
| 防守 | 混沌/退潮、2 月清仓、8 月上限 | 停止新增风险、清仓、拐点试错、阶段上限 | 状态阈值与具体比例 |
| 消息 | 4 月 9 日、8 月 2 日 | 指数承接与公司直接伤害分层；事件签发、公开与下一交易时刻分离 | 博主所指券商精确子集 |
| 财报 | 10 月 29 日、江波龙 | 公告/PIT/解释/动作分层，预期兑现和质量审查 | 囤货价差因果、正式半年报 |
| 持仓 | 9 月 20 日、6 月多账户链 | 个股失效退出、市场错杀持有、账户/策略分层 | 真实总账户暴露 |
| 加仓/禁止加仓 | 9 月禁令、11 月临时加仓 | 下跌补仓禁令；owner 要求动作前预期与真实反馈偏差；新证据触发与失效撤回 | 博主是否有同票浮盈加仓的可核验链 |
| 离场 | 雅江、业绩兑现、江波龙、8 月分层退出 | 次日纠错、原因型退出、修复离场、前后排分层 | 竞价/开盘/盘中阈值 |

两项具体边界均已得到 owner 反馈并完成处理。现在 owner 只需明确判断：现有证据粒度和七个维度是否足够进入全量逐视频分析，即明确回复“G1 通过/可以进入全量分析”或指出仍需补证的具体缺口。

## 10. 已修复的两个实现问题

### 10.1 Market CLI endpoint 重复

旧 CLI 对 `--endpoint` 使用 `action=append, default=["daily"]`。显式请求 `--endpoint daily --endpoint daily_basic` 时会得到 `daily, daily, daily_basic`，导致 bundle 重复日线记录。

当前行为：

- parser 不预置默认列表；
- 未显式传 endpoint 时，执行层默认 `("daily",)`；
- 显式 endpoint 保持用户请求顺序且不额外插入 `daily`。

回归测试：`tests/yeren_research/test_cli.py`。

### 10.2 财务公告日整值浮点

CSV 解析后的 `ann_date` 可能是 `20260428.0`。旧 `_date_text` 直接字符串化，日期长度不合法，导致最新 Q1 行被静默跳过并退回旧财务期。

当前行为：

- 有限且为整数的 float 先转为整数日期字符串；
- 非有限 float 继续当缺失；
- 不为其他想象输入增加兼容层。

回归测试：`test_financial_records_accept_numeric_announcement_dates`。

## 11. 当前验证基线

Python 一律使用：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
```

已通过：

```bash
$PY/ruff check backend/ scripts/ tests/yeren_research/
$PY/mypy --explicit-package-bases scripts/yeren_research
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research
git diff --check
```

基线结果：

- Ruff：通过；
- Mypy：8 个源文件无问题；
- Pytest：20 passed；
- 12 份 observation 工件（11 个唯一视频）通过 `VideoObservation` 校验；
- 20 条 hypothesis revision 通过 `HypothesisRevision` 校验；
- 14 个现存 decision/outcome bundle 通过 `EvidenceBundle` 结构校验；
- case、event 和 worklog JSON/JSONL 语法通过。

注意：旧 bundle 的结构校验通过不代表语义当前；江波龙仍只能引用 `v1.2`。

本阶段没有前端改动，不需要运行前端 type-check、vitest 或 build。

## 12. 接手时的机器校验命令

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python

$PY - <<'PY'
from pathlib import Path

from scripts.yeren_research.schema import (
    EvidenceBundle,
    HypothesisRevision,
    VideoObservation,
)

root = Path("data/yeren_research")
observations = sorted((root / "observations").glob("*.json"))
for path in observations:
    VideoObservation.model_validate_json(path.read_text())

hypotheses = (root / "hypotheses.jsonl").read_text().splitlines()
for line in hypotheses:
    HypothesisRevision.model_validate_json(line)

bundles = []
for folder in ("decision_bundles", "outcome_bundles"):
    for path in sorted((root / folder).glob("*.json")):
        EvidenceBundle.model_validate_json(path.read_text())
        bundles.append(path)

print(
    f"observations={len(observations)} "
    f"hypotheses={len(hypotheses)} bundles={len(bundles)}"
)
PY
```

预期输出：`observations=12 hypotheses=20 bundles=14`。

## 13. owner 通过 G1 后的下一阶段

只有收到 owner 明确的“G1 通过/可以进入全量分析”后才执行以下工作：

1. 追加一条 worklog，记录 owner 判断、时间和允许的下一阶段；不要覆盖 `ready_for_owner_review` 记录。
2. 在跟踪文档中把状态更新为“G1 由 owner 通过”，保留判定范围只限证据粒度与功能覆盖。
3. 按 `metadata.jsonl` 的 `published_at` 从早到晚建立全量队列，以 ledger 最后状态判断转写可用性。
4. 当前主 agent 逐条阅读完整转写，生成 observation；只有语义歧义会改变交易动作时才定向查看音频/画面。
5. 对每条视频先建立时间区间、主体、语句类型和解释；只有实际需要时再查询行情、公告、财报或官方事件。
6. 每个跨日真实持仓/操作链都保持 decision/outcome 隔离，不能用后续收益选择解释。
7. 持续追加 hypothesis 支持、反证、例外和系统演化记录，特别关注不同账户、短线/波段/中长线层级。
8. 定期更新 casebook 和 coverage，但不在全量研究完成前冻结参数。
9. 对真实案例确实需要、当前又缺失的分钟/竞价、龙虎榜或公告来源形成最小授权请求，再交 owner 决定。

全量分析不是关键词批处理任务。关键词工具只能排序候选；核心语义仍由主 agent 亲自完成。

## 14. owner 未通过 G1 时的处理

如果 owner 指出缺口：

1. 把缺口翻译成一个能改变研究判断的具体故障场景；
2. 只增加能回答该场景的 observation、证据包或案例；
3. 若需要新数据，先证明现有官方/PIT 来源无法回答，再报告最小数据范围和成本；
4. 完成后更新 G1 审查面，再次请 owner 判断；
5. 不用评分表、更多视频数量或重复验证替代 owner 指出的语义问题。

## 15. 已知开放项

这些未知不是当前任务失败，也不应被下一 agent 擅自填平：

- 博主是否允许同一证券已有浮盈后因新超预期证据加仓；目标系统语义已由 owner 定义；
- `755201` 与 `764744` 中“退/推仓位”的原音方向；
- 2025-11-04 竞价加仓的证券、账户、前置盈亏和盘面信号；
- 2026-08-02 事件族和下一交易日已复原；博主所指的精确券商子集仍未知；
- 混沌、退潮、赚钱效应、主线和情绪拐点的阈值与优先级；
- “推仓位”作用于单票、策略子仓还是组合总暴露；
- 雅江案例中的量化/游资身份；
- 江波龙库存价差对利润的真实贡献；
- 竞价、分钟、订单流、龙虎榜和完整历史概念/ST 成员缺口；
- 两条空文本视频不提供可靠交易语音；六条末句偏移异常只在引用对应末句时定向复核。

现阶段没有新增付费数据授权请求。不要提前建设通用历史新闻湖或从零重下 PIT。

## 16. 恢复与汇报格式

每个工作单元完成后向 `data/yeren_research/worklog.jsonl` 追加：

- `work_unit` 与状态；
- 实际读过的视频/案例范围；
- 新增 observation、bundle、case、event、hypothesis；
- 当前恢复点；
- 会改变交易判断的 open items；
- 验证结果。

向 owner 汇报时至少说明：

- 新分析了哪些视频和真实操作链；
- 哪些官方公告、PIT 行情或财务资料在正确历史时点可用；
- 哪些规则被支持、修订、反驳或仍冲突；
- 哪些问题因实体、ASR、时间或数据缺失保持未知；
- decision/outcome 是否持续隔离；
- 测试、commit 和 push 恢复点；
- 当前门禁究竟是“待审查”“owner 已通过”还是“需补证”，不能由 agent 自行升级。

## 17. 当前交付物索引

Tracked 文档与代码：

- `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
- `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
- `docs/research/yeren-system/casebook.md`；
- `docs/research/yeren-system/data-and-source-coverage.md`；
- `docs/research/yeren-system/research-methodology.md`；
- `scripts/yeren_research/`；
- `tests/yeren_research/`。

本地 append-only 研究工件：

- `data/yeren_research/observations/`；
- `data/yeren_research/decision_bundles/`；
- `data/yeren_research/outcome_bundles/`；
- `data/yeren_research/cases/`；
- `data/yeren_research/events/`；
- `data/yeren_research/hypotheses.jsonl`；
- `data/yeren_research/worklog.jsonl`。

G1 已通过。本文件至此完成历史交接职责；阶段 C 使用 `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`，机器恢复入口使用 worklog 的 `M2-G1-owner-gate-passed` 记录。
