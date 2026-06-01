# 双线投研逻辑 + 编排 + 前端 重构 — 设计草案(2026-06-01 规划 session)

> **性质**:规划/调研 session 的「深度思考」产物(workflow step 2)。**非决策、非 amendment**。
> 喂给 codex 多轮对抗式头脑风暴(step 3),收敛后用 AskUserQuestion 拿 owner 拍板(step 4),才落 `docs/decisions/*` + `docs/plan.html`(step 5)。
> 基于:R0 总纲 + §2 全红线 + 现状代码侦察(4 grounding agents)+ 调研 dossier(industry-chain / thesis-tracking / T+1-rotation / 既有 KG·anomaly·coldstart)。
> **本 session 不写运行代码。**

## 0. 现状关键事实(grounding 已核实)

- **Line-1 = 纯自下而上量化**:screener 5 技术因子(momentum_20d 0.40 / ma_ratio 0.25 / volatility 0.20 inv / amount 0.15;rsi 0 权重)→ 截面分位 → 固定权重合成 → top-N(cap 100)→ CandidateSelector(`final_shortlist_size=5`,`min_quant_slots=3`,`max_percentile_shift=0.01`)→ 4-agent 单轮辩论 → builder 单一构造点 14-check → 飞书。**零自上而下 / 板块 / 产业链推理**;MiroFish `AdvisorySignal` 重排 seam **存在但未接线**(O-003 todo)。
- **买入 thesis 几乎不持久化**:`InstructionPlan` 无 `reasoning` 字段落库;Line-1 路径 `evidence_ids=()` 空;`agent_debate_records` 不落库;`invalidation_summary` 硬编码 "Line-1 daily BUY"。可复现的只有 screener 的 `SignalInputManifest`(因子快照)+ `risk_summary`。
- **Line-2 = 严格零 LLM 确定性**:三层 import 隔离(monitoring 禁 `backend.{llm,agents,agents_team,mirofish}`;runner 禁更广)。触发种类:ATR 止损 > 回撤止损 > 止盈(+1R 减半)> 减仓(>16.5%→13%);去重键 `(code,trigger_kind)`;补仓 Van Tharp + 反马丁格尔 + 熊市禁补。**对持仓「为何买」完全无知**;**无盘后复盘 runner**(仅 09:35 daily 异动 + 30s intraday;17:00 是 MiroFish 写证据,非 Line-2 复盘)。
- **无 ≤5 持仓约束**:仅 check#6 `max_total_positions=10`(已含 ETF + SELL 跳过)+ check#10 `max_daily_new_instructions=5`(≤5 单/日,熔断)。14-check 是锁定 schema 常量(`risk_summary` min=max=14)。`max_sector_pct=0.40` 字段存在但**无 check 执行**(休眠)。
- **T+1 真相**:股票 T+1(`available_volume` 次日释放,三层守门);**项目 universe 无 T+0 标的**;配比政策已锁「不假设当日回笼」。
- **前端**:实建 **13 页**(doc 写 11 — 漂移)+ **14 WS 类**(doc 写 12 — 漂移);仅 2 写端点;Vue3+Pinia+Element Plus+ECharts;3 层 reason 抽屉 + 5 冻结源 StatusBar 已建;**无前端调研 dossier**。

---

## 方向 ① — Line-1 第一性原理·产业链倒推主题层

### 设计目标
自上而下:判大方向(宏观/政策/技术拐点)→ 受益板块 → 倒推必需产业链 → 卡脖子环节(断供巨大负面 × 还没炒热)→ 代表性标的。**与现有自下而上量化 + MiroFish 互补共存,不削弱固有能力。**

### 数据分层(调研诚实判断 — 决定可量化程度)
| 环节 | 数据 | 可纯量化复现? |
|---|---|---|
| ② 受益板块 | Tushare `index_classify`(申万 L1/L2/L3)/`ths_index`+`ths_member`(概念)/`dc_index`(板块涨幅+换手) | ✅ PIT-safe |
| ③ 产业链上下游图 | **无干净开放公司级供应商-客户有向图**;冷启动种子 = `liuhuanyong/ChainKnowledgeGraph`(4654 公司/56824 上游边,无 license → 重建当种子,记 provenance);Tushare `stock_zygc_em` 主营构成弱信号 | ⚠️ 半静态种子,非每日量化 |
| ④ 卡脖子量化(HHI/国产化率/进口依赖/单源) | **基本无 API** — 在研报/政策文 | ❌ 仅 LLM 抽取→evidence(display)+人工 gate |
| ⑤ 还没炒热 | `daily_basic`(PE/PB 历史分位)/ 换手 / 资金流 / 龙虎榜 | ✅ PIT-safe |
| ⑥ 标的 | 概念/行业成分映射 | ✅ |

### 红线兼容设计
- **资格仍纯量化兜底**:screening 排除四件套 + 可负担性是硬门,主题层**永不**让低质标的绕过(P0-9-amendment / P0-8-amendment「资格纯量化」)。
- **产业链 KG = Phase Q 核心应用**:扩 Q 的节点/边 = `Trend→Sector→ChainLink/Product→Stock`,边 `DRIVES/REQUIRES/UPSTREAM_OF/SUPPLIES/BELONGS_TO`;`criticality`/`chokepoint_score`/`crowding_pct`/`valuation_pct` 作属性;双时态 + SUPERSEDES。choke-point = 子图 betweenness/PageRank(NetworkX 原生,无需 Neo4j)。
- **定性事实经 Phase Q ingest 人工 gate**:trend / 卡脖子 / 国产化 由 LLM 抽取 + 二次 agent 校验 + 飞书/人工 gate + `LiveArtifactRegistry` pin 成 KG 节点属性 + evidence(display-only)。**非 runtime LLM**;实时只读 pin 的快照。
- **新确定性模块 `backend/theme_layer/`**(纯量化,禁 import llm/agents/mirofish):读 pin 的 KG 快照 + PIT 数据 → 每股 `thematic_conviction = f(chokepoint_centrality, criticality, 1−crowding_pct, 1−valuation_pct)`。确定性可 replay。

### 关键设计张力(→ owner 决策 #1)
「还没炒热」= 低动量/低关注,**与 screener momentum_20d 0.40 权重直接冲突** → 在已被动量排序的合格集内做 ≤1 分位 advisory 重排**捞不出**被动量压到底部的早期标的。两条路:
- **(a) advisory-only 有界重排**(≤1 分位,MiroFish 同款,**不破红线**):主题只微调排序,**驱动不了**选股。简单合规但**实现不了 owner「倒推挖标的」的初衷**(早期/逆向标的会被动量排序埋没)。
- **(b) peer 候选 sourcing + 保留主题槽**(**新 amendment**):主题源候选**通过同样硬门**(排除四件套 + 可负担性 + RiskEngine 14-check + 人工 gate)后,可占 ≤5 shortlist 的**保留名额 ≤1–2**(对称现有 ≥3 量化名额保证;`event_path_reserved_cap=1` 同构先例)。资格从「纯量化」扩为「量化 OR 主题(两者都过硬门)」。**能实现 owner 初衷**,代价 = 改「资格纯量化」红线 → 必须 amendment + 对抗测试。
- **倾向 (b)**:owner 语气强(「判大方向→倒推→挖标的」+「不影响固有能力」=互补不是替代);且逆向/早期标的本质需要绕过纯动量排序。保留 ≥3 量化名额 + 硬门 + 人工 gate 守底。

### Phase/任务落点
扩 **Phase Q**(产业链 KG = 核心应用,精化 Q-001/Q-002 加产业链节点 + liuhuanyong 种子)+ 新模块 `backend/theme_layer/` 的 scoring + Line-1 集成(advisory 或 peer-sourcing,依决策 #1)。可能需新 Phase(如 **V 主题层**)或并入 O/Q。

---

## 方向 ② — Line-2 盘后复盘 + thesis 监控 + 长短线智能决策

### 设计目标
盘后复盘已持仓 + 盘中持续监控,智能判:**忽略短期波动放长线** vs **短线及时止盈卖出**。**长线看逻辑(thesis 是否破坏),短线看量化指标(已有)。**

### 核心张力(最关键)
「长线看逻辑」= thesis 评估,**本质 LLM/语义** → 与 **Line-2 零 LLM 红线**(P0-10-amendment-line2)直接冲突。

### 调研背书的合规范式:「LLM 当感知/证据,规则当 actuator」
(Agentic-Trading 两层工作记忆;audit-oriented 推荐范式。)三块设计:

**(1) PositionThesis 持久化(缺失的原语)**
- 买入时落结构化 thesis = `{pillars(3–5 支柱,LLM 文本 advisory) + 每支柱的确定性量化失效阈值(machine-checkable) + time-stop/催化窗 + 原始 evidence_ids + 量化因子快照 + 辩论结论}`。
- **分层**:thesis **文本**由 fund_manager reasoning 写(允许的文本字段);**失效阈值确定性派生**(量化,无 LLM 才能机检)。
- 存储:新 collection(或扩展),**非 InstructionPlan**(M-004 不破);由 `instruction_id`/`correlation_id` 关联;引用 `SignalInputManifest` + evidence_ids → 可 replay。
- ⚠️ 注意:写入点在 Line-1 dispatch,thesis 文本来自 LLM(合规),但**不得**让 thesis 反向写任何决策字段。

**(2) 盘后复盘 runner(缺失的 slot)**
新 EOD Line-2 复盘 cron(如 17:30,MiroFish postclose 之后),两通道:
- **确定性 thesis-health(量化)**:重评每个 PositionThesis 的量化阈值(因子漂移 / IC 衰减 / 相对超额转负 / MA 结构破位)。**这是量化 → 可进确定性决策路径**:可派生新触发(`THESIS_QUANT_BREAK` daily 触发种类)→ 经 `assemble_monitoring_plan` → SELL/减仓。零 LLM 合规。
- **advisory thesis-health(定性 LLM)**:LLM 比对当前新闻/证据 vs 原 thesis 支柱 → 输出「thesis intact / weakening / broken」+理由**文本**。**仅 evidence/advisory**:写 evidence_collection(新前缀如 `THESIS-` 或复用 `DEBATE-`)+ display-only 飞书 digest(仿 basket_digest,不可解析)。LLM 调用在 **monitoring 之外**(orchestration 层)发,计 llm:usage,cost-gate。**永不**碰 side/volume/limit_price/RiskCheckSummary。owner 据此**人工**执行,或确定性止损独立触发。

**(3) 长短线分层**
- 短线(intraday 确定性,已有):ATR 止损 / 回撤 / 止盈 / 减仓。
- 长线:确定性 thesis-quant-break → SELL/减仓(合规);定性 LLM thesis-break → advisory(人工 gate)。
- **「扛波动 vs 止盈」**:thesis-quant-health == INTACT → 可**确定性地放宽**短期 noise 触发(止盈/减仓带);**保护性 ATR/回撤止损永不被调制**(优先级红线)。thesis broken → 升级 SELL。**调制规则必须确定性。**

### 红线兼容
- monitoring 保持零 LLM + import 隔离;确定性 thesis-health 评估器在 monitoring 内(纯量化 over PIT + 阈值)。LLM advisory 通道在 monitoring **之外**,只写 evidence。
- **新 amendment**:P0-10-amendment-line2-② 定义 (a) PositionThesis schema+持久化;(b) 确定性 thesis-quant-break 触发种类;(c) LLM advisory thesis-review = evidence-only + display-only 飞书 + 计预算 + 永不碰决策字段边界。
- 盘后复盘 cron = 新 BrokerScheduler job → P1-2.A amendment。

### 关键决策(→ owner 决策 #2)
Line-2 thesis 评估引入哪些:(a) 仅 advisory LLM(决策全留确定性量化,owner 人工执行);(b) advisory LLM + 新确定性 quant-thesis-break SELL 触发;(c) 仅 advisory,不加新触发。**倾向 (b)**:量化阈值破位是确定性的,可安全进决策路径;LLM 只做语义 advisory。

---

## 方向 ③ — 双线组合 + ≤5 持仓 + 卖一买一 + T+1

### 设计目标
每日双线并行(Line-1 发掘 + Line-2 管持仓),整体收益最大化,不一次买完不管。**硬约束:同时持仓 ≤5(ETF 也算)**;满 5 遇更好标的 → 先卖一腾位(T+0/T+1)。

### 调研背书:qlib `TopkDropoutStrategy`(MIT,可移植算法)
`topk=5` 槽位 + `n_drop=1` 排名替换 + `hold_thresh` 最短持有 + rank-buffer hysteresis(挑战者须排名高到把在位者挤进 last-n_drop 才替换 = 「挑战者须以 margin 击败在位者」)→ 天然「卖一买一」,换手率 `≈2·n_drop/topk` 可控。

### ≤5 持仓硬约束
最简 = `max_total_positions` 10→5(config + P0-7 amendment + 重启)。check#6 已含 ETF + SELL 跳过。**无需 15th check**。但 check#6 只**拒第 6 笔买**,不做轮动。

### 轮动决策(确定性,上游)
- 新确定性模块 `backend/slot_portfolio/`(纯量化,import 隔离):当前 5 持仓「在位分」+ 新候选「挑战分」→ 挑战者以 margin 击败最弱在位者(hysteresis 缓冲)→ 提议 ROTATION(卖最弱)。
- **打分对齐(①②③ 集成点)**:在位分 = thesis-health(方向②)+ Line-1 重算分;挑战分 = Line-1 量化 + theme conviction(方向①)。替换分是三方向的**共享货币**。

### T+1 时序(最关键正确性)
调研:T+1 两腿跨日。**首选无状态设计(避开 P-006 脆弱跨日 state 教训)**:
- Line-1 选股**从 holdings-blind 改 holdings-aware**:知当前 5 持仓 + 已结算的空槽。
- 满 5 且挑战者过 margin → **当日发「轮动卖出最弱」建议**(Line-2 SELL / Line-1 rotation 建议),**当日不买**。
- 次日若槽位**实际**被腾出(owner 已卖,从 settled 持仓观察 — 非内存承诺)+ 候选仍合格 → 才买入空槽。
- **「腾出的槽」从真实持仓观察,不靠 in-flight 承诺** → 无脆弱跨日 pending state。轮动 = 今日 SELL 建议 + 次日条件 BUY 进真实空槽。

### 红线兼容
- ≤5 持仓 = check#6 config 改(amendment)。
- 轮动决策确定性、上游、纯量化模块(import 隔离),喂 builder(单一构造点不破)。
- T+1:不当日回笼;轮动 = 跨日(今 SELL + 次日条件 BUY 进真实空槽);**无状态优先**。
- long-only 不破;≤5 单/日 cap 不破(轮动耗 cap:1 卖 + 后 1 买);叠在逆波动率配比之上(只压不放)。
- 反 churn 四闸门(全确定性):rank margin band + 进/出不对称双阈 + `hold_thresh` 最短持有 + 每日至多 1 次轮动建议。
- 可选:激活休眠 `max_sector_pct`(板块集中度)。

### 关键决策(→ owner 决策 #3)
确认 T+1 无状态轮动设计(今卖最弱 + 次日条件买进真实空槽;无脆弱跨日 pending;不当日回笼)+ 替换 margin/hysteresis 口径。

### Phase/任务落点
扩 **Phase P**(组合配比已 done → 加轮动层)或新 Phase。新模块 `backend/slot_portfolio/` + Line-1 holdings-aware 改造 + ≤5 amendment。

---

## 方向 ④ — 前端

### 设计目标
双线系统前端做好:产业链倒推推理可视化(趋势→板块→链→卡脖子→标的可解释链路)、持仓 thesis 追踪 + 「长持 vs 止盈」面板、5 槽组合 + 换仓视图、双线每日并行运行态、三层 reason 抽屉延展。

### 红线张力
- **11 页名额锁已被突破到 13**(RiskCenter 多出;doc 漂移)+ **WS 12 类已到 14**(doc 漂移)。**先 reconcile 漂移**。
- 仅 2 写端点(新 viz 全只读,不加写端点)+ 前端不存凭证 + 127.0.0.1 不变。

### 设计取向(→ owner 决策 #4)
- **(a) 新 viz 作现有页内 PANEL/TAB/抽屉**(不破页锁,reconcile 11→13):产业链 viz 进 InstructionPlans 抽屉/新 tab;thesis 追踪进 Portfolio;5 槽进 Portfolio;双线运行态进 Dashboard/SystemStatus。**倾向**。
- **(b) 新顶级页**(破 11 页锁 → P1-5 amendment)。
- 建议调 `frontend-design` skill 研究现代、可信、高信息密度金融投研前端。
- WS 新增类(thesis-health/rotation 推送?)会扩 12/14 → 需 reconcile + 可能 amendment。

### Phase/任务落点
扩 **Phase G**(前端)新 viz 任务 + 先 reconcile doc 漂移(11→实际)。

---

## 集成故事(四方向如何咬合)

```
方向① theme conviction ─┐
                        ├─→ 挑战分(Line-1 候选打分)─┐
Line-1 量化分 ──────────┘                          │
                                                   ├─→ 方向③ slot_portfolio 轮动决策(替换分)
方向② thesis-health ────→ 在位分(持仓健康度)──────┘
                                                   │
方向② 确定性 thesis-break ─→ Line-2 SELL ──────────┘(腾槽)
方向② LLM advisory ───────→ evidence + 飞书 display(人工 gate)
方向④ ─→ 全部可视化(产业链链路 / thesis 追踪 / 5 槽轮动 / 双线运行态)
```
**替换分 = 三方向共享货币**:对齐 Line-1 挑战分(量化+主题)与 Line-2 在位分(thesis-health)是集成枢纽。

## Phase 结构提案(待 owner 决策 #5)
- 空闲字母:V W Y Z。现有 O(MiroFish)/Q(KG)/R(自进化)/T(交易员全栈)有 todo。
- 提案:**精化 Q**(产业链 KG = ① 核心)+ **新 Phase V**(① 主题层 scoring+集成)+ **新 Phase W**(② thesis + 盘后复盘 + 长短线)+ **扩 Phase P→新任务 或 Phase Y**(③ ≤5 槽 + 轮动)+ **扩 Phase G**(④ 前端)。或更紧凑地并入 O/Q/P/T。**结构本身请 owner 拍板。**

## 给 codex 的对抗问题(step 3)
1. ① peer-sourcing(b)是否真能不削弱量化能力?保留主题槽 ≤2 会不会被低质主题标的占用?硬门 + 人工 gate 够不够?「资格 = 量化 OR 主题」改红线的代价 vs advisory(a)的局限,哪个对?
2. ② 确定性 quant-thesis-break 进决策路径 vs 仅 advisory:量化阈值破位派生 SELL 会不会变相让「thesis 逻辑」污染确定性 SELL?边界在哪?PositionThesis 持久化会不会重蹈 P-006 脆弱跨日 state?
3. ③ T+1 无状态轮动:今卖次日买,中间一夜 owner 没卖 / 候选失格怎么办?会不会两头落空(卖了没买成)?margin/hysteresis 防 churn 够不够?≤5 单/日 cap 会不会被轮动 + 新 BUY + Line-2 止损挤爆?
4. ④ panel vs 新页:reconcile 13→11 还是改 11 锁?WS 新类扩张边界。
5. 整体:四方向同期上会不会过载 MVP 之后的复杂度?优先级/依赖序?哪些必须 amendment-first?
