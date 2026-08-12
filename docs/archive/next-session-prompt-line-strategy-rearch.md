# 下一 session prompt — 双线投研逻辑 + 编排 + 前端 的规划/调研 session

> 用途:MVP(Phase K-N)已于 2026-06-01 真启跑通完整人工执行闭环。本 prompt 启动一个**规划/调研 session**(不写运行代码),目标是先调研深思、再与 codex 多轮头脑风暴,最终**优化 SSoT 计划文档 `docs/plan.html`**(精化/新增 Phase O-T 任务 + 必要的 decision docs/amendments),把 owner 提的 4 个方向系统性地落进计划。
>
> 直接把下面「PROMPT 正文」整段粘贴给新 session 的 Claude。

---

## PROMPT 正文

你接手 QuantMind(多 Agent 投研信号 + 模拟实盘 + 飞书人工执行;**永禁真实券商程序化下单**)。**本次是规划/调研 session,不写任何运行代码**;唯一产出 = ① 优化后的 `docs/plan.html`(SSoT 计划文档)② 必要的 `docs/decisions/*` 决策文档/amendment。流程必须是:**先调研 + 深度思考 → 再与 codex 多轮头脑风暴(对抗式/多视角)→ 关键设计决策用 AskUserQuestion 拿 owner 拍板 → 才动 plan.html**。

### 0. 先读(必读,按序)
1. `CLAUDE.md`(§1 进度协议 / §2 全部红线 / §2.0 双线重构 v2 amendment 总览 / §3 工程原则)。
2. `docs/decisions/R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md`(双线重构总纲 + 2 新红线:PIT 可复现 / InstructionPlan 单一构造点)+ §2.0 列的 6 个 amendment。
3. `docs/plan.html`:重点看 **Phase K-T 的 40 任务结构**(K+L+M+N=MVP 已 done;**O=MiroFish 核心 / Q=本地知识图谱 / R=自进化 v2 / T=交易员与全栈**)、`#session-log` 的 **SESSION_LOG #59**(权威「下一步」指针:MVP 真启全闭环已通 + 3 处 follow-up)、`#protocol` 维护协议、`#gates` 红线扫描。
4. 相关锁定文档:P0-7(仓位/熔断/universe)、P0-8(数据情报 + MiroFish + Tushare 数据源 amendment)、P0-9(选股双层 + 排除四件套 + long-only)、P0-10(LLM 字段权限 + 四必经 Agent + Line-2 确定性零 LLM)、P2-2(自进化 3 路径 + 知识图谱)、`docs/research/` 下双线重构调研 dossier。
5. `docs/decisions/P0-4-amendment-2026-06-01-report-is-truth.md`(刚锁的「回填即真相」+ §3.6 follow-up)。

### 1. 不可触碰的安全地基(本次只演进【投研逻辑 + 双线编排 + 前端】,绝不动这些架构红线)
永禁真实下单 / 飞书人工执行 / 全层 127.0.0.1 / **LLM 永不写任何决策字段(只 4 类可写文本)** / RiskEngine 14-check 纯函数无 IO / 人工 gate / **InstructionPlan 单一构造点 M-004** / PIT 数据可复现 / fail-closed。你提出的任何增强都必须能在不破这些红线的前提下设计;凡触及决策边界 → **先写 amendment 后改计划**(本次只改 plan.html + 写 decision docs,不写代码)。

### 2. 四个研究方向(owner 锁定,逐条深挖)

**方向 ①:Line-1 引入「第一性原理 · 产业链倒推」选股思路(前提:不削弱其固有量化/MiroFish 能力)**
- 口径:先判**未来发展大方向**(宏观/政策/技术拐点)→ 圈定**一系列受益板块** → 倒推这些方向落地所**必需的产业链** → 调研哪些**环节容易「卡脖子」**(一旦断供/供应出问题会让整条链产生巨大负面效应)**且当前还没炒很热**(低关注/低估值但临界重要 = 逆向/早期)→ 再挖该环节的**代表性上市公司**。
- 研究问题:如何把「趋势→板块→产业链→卡脖子环节→标的」做成可复现、可审计的**自上而下主题层**?数据源(产业链图谱 / 政策 / 供应集中度 / 国产化率 / 进口依赖度 / 关注度与估值分位)?如何量化「卡脖子」(供应集中度 HHI、单一来源、替代难度、库存周期)与「没炒热」(成交/关注度/估值分位)?
- **设计张力 + 红线**:这是一条**自上而下、偏基本面/主题**的候选生成层,要与现有**自下而上全市场量化筛(`screening`/`CandidateSelector`)+ MiroFish 加分(evidence-only)** 互补共存(owner 强调「不影响固有能力」)。LLM/多 Agent 可做产业链推理,但**输出只能进 `evidence_collection`/候选证据,永不写决策字段、永不否决量化名额**(P0-8-amendment「合格集内有界重排 ≤1 分位、永不否决板块、top-N 后保 ≥3 量化名额」是参照范式)。与 **Phase Q 知识图谱**(产业链=天然图结构,SUPERSEDES 双时态)强相关 → 研究是否把产业链倒推做成 Q 的核心应用,或 O/新 Phase。

**方向 ②:Line-2 增加「盘后复盘 + 盘中长期监控持仓 + 长线/短线智能决策」**
- 口径:Line-2 要能**盘后复盘**已持仓资产、以及**盘中持续监控**,**智能判断**:是**忽略短期波动放长线持有**,还是**做短线及时卖出止盈**。判据分层:**长线看逻辑(原始买入 thesis 是否还成立),短线看量化指标**。
- 研究问题:如何**持久化并追踪每只持仓的原始买入逻辑/thesis**(买入时的产业链/量化/辩论依据)?盘后如何**重评 thesis 是否破坏**(逻辑变了→减/清;逻辑还在→扛短期波动)?长线逻辑评估(可能需要 LLM/证据)与短线量化触发(ATR/回撤/止盈,已确定性实现)如何**分层协作**?
- **设计张力 + 红线**:**当前 Line-2 是确定性零 LLM**(SELL/ADD 方向由 `AnomalyDetector`/评估器派生,不经 fund_manager/辩论;`backend/monitoring` 严禁 import llm/agents;见 P0-10-amendment-line2)。owner 的「长线看逻辑」隐含**对 thesis 的(可能 LLM 的)评估**,与 Line-2 零 LLM 红线**直接冲突** → 这是本方向**最关键的设计点**:必须设计出既能评估逻辑、又不破「Line-2 SELL 决策确定性、镜像/对账/RiskEngine 不被 LLM 污染」的方案(候选:thesis 评估仅作 **advisory/evidence**,最终 SELL/HOLD 仍走确定性派生 + 人工 gate;或为「盘后复盘建议」单开一条**不直接动决策**的复盘通道;或新 amendment 明确边界)。务必与 codex 对抗式论证这条边界。

**方向 ③:双线如何组合工作(每日双线并行)+ ≤5 持仓硬约束 + 卖一买一 + T+0/T+1**(owner 标为关键研究点)
- 口径:**每个交易日必须双线并行**才算完整服务(Line-1 发掘新机会 + Line-2 管好已持仓),**不能一次性买入后就不管**。研究双线**如何相互配合使整体收益最大化**。
- **硬约束(新)**:**同一时间持仓 ≤ 5 只(ETF 也算)**。若发现「更好的股票」必须买入但已满 5 只 → **必须先卖出一只**腾位 → 此时要考虑 **T+0 还是 T+1**(A 股股票 T+1 不可当日卖当日买入的;卖出回款当日可再用于买入;部分 ETF 是 T+0,多数股票/ETF 是 T+1 → 卖出的标的须已持有 ≥1 日才可卖)。
- 研究问题:**5 槽组合管理**(slot portfolio)+ **「换仓」决策**(新候选何时「足够好」到值得置换现有最弱持仓?用什么打分对齐 Line-1 评分 / Line-2 thesis 健康度)+ 卖一买一的**资金/结算时序**(T+1 settlement,owner 此前列为真实可交易性缺口之一)+ 与现有 **P0-7 红线**(≤5 单/日、单股 ≤15%、总仓 ≤70%、单次 ≤5 万)如何叠加(≤5 **持仓** 是**新维度**,区别于 ≤5 **单/日**)。如何把它做成 RiskEngine/Builder 可校验的**确定性约束**而非 LLM 自由裁量。
- **设计张力 + 红线**:置换决策必须确定性、可审计、经 RiskEngine + 人工 gate;**long-only 永锁**;单一构造点不破;PIT 可复现。这层本质是**组合构建/轮动层**,要研究它在双线之上如何编排(与 Phase P 已建的逆波动率配比/篮子如何衔接)。

**方向 ④:前端构建(owner 强调「也很重要」)**
- 口径:这套双线系统的前端要做好。当前 P1-5 锁定 **MVP 7 页 + Phase B 4 页 + 决策闭环 4 分组**(见 plan.html 前端章 + P1-5 决策文档)。
- 研究问题:双线系统需要哪些**新的可视化/交互**?如:产业链倒推推理的可视化(趋势→板块→链→卡脖子环节→标的的可解释链路)、持仓 thesis 追踪与「长持 vs 止盈」决策面板、**5 槽组合 + 换仓**视图、双线每日并行的运行态、三层 reason 抽屉的延展。在不破 **P1-5 §2 红线**(仅 2 写端点 / 11 页名额锁 / 前端不存凭证 / WS 12 类 / 全 127.0.0.1)前提下如何演进?
- 建议调用 `frontend-design` skill 研究现代、可信、信息密度高的金融投研前端设计;给出页面/组件级建议并落进 plan.html 前端任务。

### 3. 工作流程(严格按序)
1. **调研**(按 `~/.claude/rules/development-workflow.md` §0):GitHub code search(`gh search repos/code`)找产业链图谱/主题量化/组合轮动/thesis-tracking/金融前端的成熟实现;primary docs(Tushare/akshare 能否取产业链/供应链/国产化数据);Exa/web 调研「第一性原理产业链倒推」「卡脖子环节量化」「A 股 T+1 组合轮动」「持仓 thesis 衰减监控」「投研前端设计」等方法论。沉淀到 `docs/research/`。
2. **深度思考**:进 plan mode,对 4 方向各产出结构化设计草案(目标 / 数据 / 算法 / 与红线的兼容路径 / 落哪个 Phase / 任务拆解)。
3. **与 codex 多轮头脑风暴**(owner 明确要求):用 codex plugin(`codex:codex-oracle` 做深推理/构思,或 `codex exec` 喂设计草案)做**多轮、对抗式、多视角**讨论 —— 让 codex 挑战每个方案的红线兼容性、收益最大化的组合编排、T+1 时序正确性、Line-2 零 LLM 边界、产业链评分的可复现性、前端取舍。**codex 若 stall/撞额度**(2026-06-01 当日 stall 2 次)→ 回退 `claude /code-review high` 或多 agent split-role 头脑风暴(参照 `~/.claude/rules/agents.md` 多视角)。每轮记录分歧与收敛。
4. **关键设计决策用 AskUserQuestion 拿 owner 拍板**(尤其:Line-2 thesis 评估是否引入 advisory LLM 及其边界 / 5 槽换仓打分口径 / 产业链层落 Q 还是新 Phase / 前端新增页是否突破 11 页名额)。
5. **改 SSoT**:把收敛结论写成 `docs/decisions/*`(决策/amendment)+ 更新 `docs/plan.html`(精化/新增 Phase O-T 任务,任务粒度可实施、依赖清晰、含红线约束)+ 追加一条规划 session 的 SESSION_LOG + 修订记录。**不写运行代码**。

### 4. 提醒
- 报告中文、推理英文、代码/commit 英文;handoff/计划文档要 exhaustive(完整设计 + 取舍 + 任务拆解),future session 能无缝接手。
- 凡决策边界变更 → 先 amendment 后改计划;凡新增硬约束(如 ≤5 持仓)→ 想清楚它在 RiskEngine/Builder/config 的确定性落点 + 红线扫描(`#gates`)如何加守门。
- 安全地基红线一条都不破;这 4 个方向是**投研能力 + 编排 + 前端**的演进,不是安全架构的改动。
- push origin main 始终 owner-gated;本规划 session 的 docs commit 也等 owner 授权。
