# 长线价值投资战略设计(锚定『十五五』规划)— 2026-06-22

> **类型**:战略设计文档(研究/规划交付物)。**执行范围**:owner 2026-06-22 拍板 = **纯文档**(本文 + amendment 草案 + plan.html Phase AF 任务清单);**本 session 不动代码**,owner 审批后再进 TDD。
> **关联**:总纲 R0 + `P0-7`(风险/预算/≤5 槽)+ `P0-8`(主题研究 peer-sourcing)+ Phase AC(风格分型 + 价值线)+ Phase W(持仓 thesis)+ `quant-gate-rebar-amendment-2026-06-21`(主旋律择场 / 客观底部确认 §2.3)。
> **触发**:owner『短线/超短线 = 用较低本金快速复利提升资产体量;最稳妥的还是价值投资;可用资金达 ¥5 万触发价值投资,锚定党中央十五五规划、避开热门炒作、深挖产业链、提前埋伏布局』+ 4 决策(§8)。

---

## 1. 任务与双引擎定位

owner 的资产成长论:**短线/超短线**(本金小 → 快速复利做大体量)+ **长线价值投资**(体量到位后求稳妥)。两者不是替代,是**资金分层的两台引擎**:

| 引擎 | 角色 | 触发 | 时间尺度 | session 归属 |
|------|------|------|----------|--------------|
| **短线/超短线**(量化第一闸门 + ≤5 槽轮动) | 用较低本金快速复利、把资产体量做大 | 始终 on(< ¥5 万时唯一引擎) | 5–10 td(QGR 主腿)+ T+1 快腿 | **另一 session(QGR)** |
| **长线价值投资**(本 session) | 体量到位后求稳妥、锚定国家战略提前埋伏 | **总权益 ≥ ¥5 万** | 数周–数年(穿越政策周期) | **本 session** |

**关键发现:价值投资机器 ~95% 已建好、~0% 接线**(Phase AC/W 建好从未激活)。所以本任务**不是从零造系统**,而是 **接线 + 灌内容(十五五映射 + 质量基本面)+ 加资金触发 + 给价值仓做轮动豁免**。这是**最贴合系统**的路径。

---

## 2. 现状盘点(file:line 级)

### 2.1 短线/超短线如何处理(另一 session 的 QGR 域)
`backend/screening/screener.py`(全市场纯量化初筛,0 LLM)→ `backend/budget_policy/policy.py`(Micro/Small/Normal 分层)→ `backend/candidate_selector/selector.py`(top-N + 可选 advisory 重排)→ 4-agent 辩论(`backend/orchestration/line1_runner.py`)→ RiskEngine 14-check → `backend/slot_portfolio/`(**≤5 槽 T+1 轮动**:在位『独立够弱』AND 挑战『绝对 margin 胜出』双条件)。默认风格 = `SHORT_TERM`。QGR 评测竞技场(`scripts/factor_research/`,5–10td horizon,真 CPCV)正在另一 session 推进。

### 2.2 长线价值如何处理(已建未接,本 session 的料)
| 已冻结的机器 | 文件 | 状态 |
|---|---|---|
| **三层价值线** 大势主线/资金认可+容量/共振+弹性 | `backend/screening/value_score.py`(`compute_value_score`,12 component)| ✅ 函数已建,**生产从未调用** |
| 价值因子(事件 CAR/Amihud/beta/共振/PIT 基本面) | `backend/screening/value_factors.py` | ✅ 已建(`fundamentals_score` 生产侧为 None) |
| **价值槽配额** 价值≤2/量化≥3 | `backend/candidate_selector/selector.py:341-395`(`_select_with_value`)| ✅ 已建,但 `value_scores=None` → bit-identical 旧路径 |
| `value_slot_quota` 可进化参数 | `backend/strategy_evolution/evolvable_params.py:172-176` | ✅ 注册,端到端接线完成,**无 value_scores 喂** |
| **风格分型** VALUE vs SHORT_TERM(需 `value_score≥0.60`+thesis) | `backend/style/classifier.py:47-86` | ✅ 已建,无 value_score → 永不触发 VALUE |
| **持仓 thesis** 持久化 + 确定性失效门(3 白名单模板) | `backend/models/position_thesis.py` + `backend/monitoring/thesis_break.py` | ✅ **已接线**(每 BUY 落库 + Line-2 日内/日终评估) |
| 风格软层(VALUE 1.5× 止盈带 + thesis intact 跳过止盈) | `backend/monitoring/style_soft.py:50-70` | ✅ 已接线(硬风控风格无关) |
| 产业链知识图谱(choke-point 中心性) | `backend/knowledge_graph/`(schema + centrality) | ✅ 已建,scheduler 待接 |
| 主题研究 5 步倒推 SOP + LLM peer-sourcing + 人工 pin | `backend/theme_research/` | ✅ 已建,scheduler/飞书待接 |
| 政策→主题 `effective_from` 防 hindsight 框架 | `quant-gate-rebar-amendment-2026-06-21.md` §2.3 | ✅ **框架**在;**十五五 映射未建**(owner-gated,QGR-3) |

> **缺口 = 纯工程接线**:① `value_score` 算出来喂 selector(现 None);② `fundamentals_score` 灌质量因子(现 None);③ 十五五 政策→主题映射(QGR-3 共享);④ ¥5 万资金触发 + 价值子账户;⑤ 价值仓轮动豁免 + 做T 降成本 overlay。无需新风控、无需破红线。

---

## 3. 『十五五』规划锚定(政策研究)

『十五五』规划(2026–2030):**建议**于二十届四中全会 **2025-10-20~23** 审议通过、**2025-10-28/29** 公布;**纲要**经全国人大 **2026-03-16** 公布。这两个日期 = **`effective_from` 防 hindsight 锚**(政策存在前不得回溯 tilt)。priorities 干净地分成『价值投资该拥抱 vs 该避开』:

### 3.1 纳入的 4 主题层(owner 2026-06-22 全选)
| 层 | tier(复用 `tier_weights.py`)| 十五五 产业(投资可估值) |
|---|---|---|
| **① 战略性新兴产业** | policy 0.75 | 新一代信息技术、新能源(光伏/风电/储能)、新材料、智能网联新能源汽车、机器人、生物医药、高端装备、航空航天/商业航天、低空经济 |
| **② 卡脖子自主可控**(深挖产业链主战场) | national_event 1.0 | 集成电路、工业母机、高端仪器、基础软件/工业软件、先进材料、生物制造 |
| **③ 人工智能+ 全链**(十五五新设战略行动) | national_event 1.0 | 算力/AIDC、AI 芯片(国产)、AI 应用(赋能千行百业)、AI 产业链必要原材料 |
| **④ 传统产业升级·高股息**(压舱石) | tech 0.5 / stock 0.25 | 矿业、冶金、化工、轻工、纺织、机械、船舶、建筑(『巩固提升全球产业分工地位』)→ 高股息/低估值稳健仓 |

### 3.2 排除(『避开热门炒作题材』= 未来产业 + 高位名)
- **未来产业 = 纯炒作**(无盈利可估值,价值投资不碰):**量子科技、氢能和核聚变能、脑机接口、6G、纯具身智能概念**。前瞻布局 ≠ 可投资标的;一级/主题情绪驱动,价值无锚。
- **同主题内高位名**(『不追涨』):已大涨到高位 / 52 周高分位 / 筹码高获利盘 / 高换手·MAX·IVOL 延展 → 剔除(对应 `quant-gate-rebar` §2.3.D)。

### 3.3 三句对齐 owner mandate
- **避开热门炒作** = 排除未来产业(§3.2)+ 不追涨高位名。
- **深挖产业链** = ② 卡脖子层经知识图谱 choke-point 中心性识别断供环节(`backend/knowledge_graph/centrality.py`)。
- **提前埋伏布局** = 『主题内尚在低位的优质股 + 客观底部确认』(对应 `quant-gate-rebar` §2.3.B/C),买在主线确立、个股仍低位、缩量筑底确认时。

---

## 4. 战略设计:长线价值分仓(Value Sleeve)

### 4.1 双引擎分仓架构(owner 决策 = 独立资金分仓)

总权益 < ¥5 万:**纯短线**(≤5 槽快速复利,价值仓休眠)。总权益 ≥ ¥5 万:切出**价值子账户**,系统成两个独立子组合:

```
┌─ 短线子账户(SHORT sleeve)──┐   ┌─ 价值子账户(VALUE sleeve)──┐
│  ≤5 槽 · 快速复利(不变)     │   │  ≤3 槽 · 长线埋伏           │
│  QGR 量化闸门 + T+1 轮动     │   │  十五五 锚定 + 底部确认埋伏    │
└──────────────────────────┘   └──────────────────────────┘
   各自独立 capital pool + 仓位 cap + 会计;经同一 RiskEngine/单一构造点/MockBroker
```

- **触发(默认,owner 审批时可调)**:**总权益 ≥ ¥50,000** 一次性激活(单向 latch + 滞回 re-arm ¥45k 防抖);激活后即使回落 ¥5 万以下也**不强制清价值仓**,仅停止加仓(不破坏长线埋伏)。
- **资金切出 glide path(默认,config 参数 amendment-gated)**:资产越大越偏稳妥价值——
  | 总权益 | 价值仓目标权重 | 短线working floor |
  |---|---|---|
  | < ¥5 万 | 0% | 全部 |
  | ¥5–10 万 | 20% | ≥ ¥4 万 |
  | ¥10–30 万 | 40% | ≥ ¥4 万 |
  | ≥ ¥30 万 | 60%(cap) | 剩余 |
  价值仓**只在底部确认时逐步建仓填向目标**,从不一次性强制部署(埋伏靠等)。
- **红线变更**:`≤5 总仓`(check#6/§2.4)→ **per-sleeve cap**(短线 ≤5 + 价值 ≤3),两子账户独立资金池。**须 amendment**(见 `docs/decisions/value-sleeve-amendment-2026-06-22-*.md`)。

### 4.2 价值评分接线(把 `value_score` 灌活)

复用 `compute_value_score`(三层、确定性、0 LLM、bit-exact replay),把生产侧 None 的 input 灌成真值:

- **大势主线(Bottom)** = `theme_coverage`(十五五 政策主题成分,PIT,§4.3 映射,带 `tier_weights` 权重)+ `sector_momentum_pct` + `regime_score`。
- **资金认可+容量(Mid)** = 事件 CAR + 容量 + (反 Amihud)流动性 + 换手 + 资金流(`capital_flow_pct`)。
- **共振+弹性(Surface)** = `resonance_score`(≥2 独立逻辑)+ **`fundamentals_score`(质量:roe/gpm/EP-TTM/应计,PIT ann_date,现 None → AF-003 接线)** + `elasticity_score`(beta)。
- **客观底部确认门(AF-004)**:`quant-gate-rebar` §2.3.C 的多指标综合——缩量 / 站稳筹码成本带上方(`cyq_perf`)/ 资金流企稳 / 无技术破位 / 无困境(非 ST/无停牌/无退市审计风险)/ 基本面质量地板 → 治 A 股『跌了再跌的洗盘』、不接飞刀。**符号/阈值在 R/AF 阶段从零验,不假设。**
- **不追涨高位剔除(AF-004)**:高换手/MAX/IVOL/52 周高分位 → 主题内剔除。

`value_score ≥ 0.60`(`DEFAULT_VALUE_GATE`)+ thesis 可派生 → 风格分型自动判 `VALUE`(`backend/style/classifier.py` 已建),后续软层/豁免/配额全自动激活。

### 4.3 政策→主题→产业链→标的映射(AF-001,owner-gated,与 QGR-3 共享单一真相源)

git 冻结的映射 registry,**与 QGR-3『政策→主题』映射同一真相源**(避免重复/冲突),价值仓在其上叠加**长 horizon tier 权重 + 传统产业高股息层**:
1. **概念/行业成分 PIT**:`ths_index`(同花顺概念 as-of-date)+ `index_classify`/`index_member_all`(申万行业 PIT)。
2. **每主题挂 `effective_from`** = 宣示它的政策文件发布日(十五五建议 2025-10-28 / 纲要 2026-03-16 / 行业政策各自日期),tilt **只从该日起生效**(严禁 hindsight)。
3. **产业链倒推 + choke-point**:卡脖子层经知识图谱(`backend/knowledge_graph/`)反推断供环节、打 choke-point 中心性。
4. **LLM peer-sourcing(可选,evidence-only)**:`backend/theme_research/` 5 步倒推浮现候选 → 人工 pin → 量化仍资格权威(永不进运行时数据路径/不剪 universe/不否决板块)。
5. **诚实门**:映射须在 baseline 面板证明主题维度 OVER 非主题 baseline 有增量(否则 deflation 揪出『主题 tilt 只是 hindsight』);**owner 确认后再冻**,走专门 codex PIT-soundness 门。

### 4.4 持有哲学 + 做T降成本 overlay(owner 2026-06-22 备注)

owner:『针对短暂性回撤,只要长期价值逻辑没变,对价值投资是可接受的,可以适当做T摊薄成本』。→ **持有穿越政策周期(thesis-gated)+ 容忍短暂回撤 + 做T降成本**:

- **核心底仓(长持)**:买入埋伏后,**只在确定性 thesis-break 或硬风控时退出**——跌破买入锚定回撤 / 政策主线反转(`effective_from` 主题被移除/夕阳化)/ 基本面质量衰减 / RiskEngine 硬门。**容忍短暂回撤**:只要长期价值逻辑(thesis)未破,不因盘中/短期下跌恐慌卖出(复用 `thesis_break.py` intact 豁免 + 1.5× 宽止盈带)。
- **做T降成本 overlay(AF-006,确定性、T+1 合规、env-OFF 默认)**:在核心底仓**之上**叠加有界波段——
  - **底仓地板**:每个价值仓设最小核心底仓(如目标股数 60%),**做T 永不卖破底仓**(只 thesis-break/硬风控能动底仓)。
  - **可做T份额**(如 ≤40%):价格相对参考成本带高 X% 减一档、低 Y% 补回(或反向),**严格 T+1**(只卖昨日已结算股、次日空出资金补回),每周期 round-trip 次数有界。
  - **目标 = 摊薄平均成本**,核心埋伏不动;参考成本带从 PIT(`cyq_perf` 筹码成本带 / 确定性移动参考)派生,**0 LLM**,阈值 = config 参数(amendment-gated、runtime 不可改)。
  - **单一构造点不破**:做T 订单经同一 `instruction_plan_builder` + RiskEngine 14-check;`side/volume/limit_price` 确定性派生。
- **季度 thesis 复检**:每季重算 value_score + thesis 阈值,衰减的换出、仍合格的续埋。

### 4.5 监控与退出(复用已建 Line-2)
价值仓接入既有 `backend/monitoring/thesis_break.py`(已接线):日内仅 ANCHOR_DRAWDOWN+TIME_STOP 可豁免、SCORE_DECAY 日终评。硬风控(止损/熔断/仓位三连/14-check/可卖量 T+1)**风格无关、全留**。SELL 方向经确定性 AnomalyDetector/thesis-break 派生,不经 fund_manager 辩论(Line-2 零 LLM)。

---

## 5. 红线:保留 vs 新增边界

### 5.1 安全地基红线全留(一条不破)
永禁真实下单(只 MockBroker/SimulationExecutor)/ 飞书人工执行 / 127.0.0.1 / LLM 不写决策字段 / RiskEngine 纯函数 / **InstructionPlan 单一构造点** / PIT 可复现(字节+checksum+coverage)/ fail-closed / 排除四件套(ST/科创/北交/可转债永禁)/ 仓位三连(单股 ≤15%/总仓 ≤70%/单次 ≤5 万,跨子账户合并校验)/ 熔断 / LLM 仅主题 peer-sourcing 且 evidence-only。

### 5.2 须 amendment 的新边界(4 条,见 amendment 草案)
1. **≤5 总仓 → per-sleeve cap**(短线 ≤5 + 价值 ≤3,独立资金池;§2.4/P0-7 check#6)。
2. **¥5 万资金触发 + 价值仓资金切出 glide path**(新会计:总权益门 + 子账户切分,config 不可改 + amendment-gated)。
3. **十五五 政策→主题映射激活**(价值仓 scope:全 4 层含传统高股息;`effective_from` 防 hindsight;owner-gated freeze;与 QGR-3 共享)。
4. **做T降成本 overlay**(价值仓内确定性有界 T+1 波段、底仓地板、单一构造点不破;最新颖、最需审查;env-OFF 默认)。

---

## 6. 内化路线图(Phase AF,全 todo,owner-gated)

| 任务 | 内容 | 依赖 |
|---|---|---|
| **AF-001** | 十五五 政策→主题→产业链→标的映射 registry(4 层 + `effective_from` + PIT 成分;与 QGR-3 共享单一真相源)| QGR-3 映射门 + owner 确认 |
| **AF-002** | 价值评分接线:`compute_value_score` 喂 selector(`value_scores` 现 None)| AF-001 |
| **AF-003** | 质量基本面接线:`fundamentals_score`(roe/gpm/EP/应计,PIT ann_date)| 既有 PIT statements |
| **AF-004** | 客观底部确认门 + 不追涨高位剔除(缩量/`cyq_perf`/资金流)| AF-002 |
| **AF-005** | 价值子账户分仓:¥5 万触发 + 资金切出 glide path + per-sleeve cap + 会计 | amendment §1/§2 |
| **AF-006** | 价值仓持有 + 做T降成本 overlay(底仓地板 + T+1 + 单一构造点)| AF-005 |
| **AF-007** | 价值仓监控接线(Line-2 thesis-break)+ 只读前端 panel | AF-005 |

每任务:TDD(非 risk >70%)+ 本地门禁全绿 + **codex 前置门**(有代码任务 commit 前修完 P0/P1/P2)+ 一任务一 feature commit + plan.html 回填。

---

## 7. 与 QGR / 另一 session 的协调(重要)

- **不碰 `scripts/factor_research/`**(QGR 研究域,另一 session)。价值仓实现全在 `backend/`。
- **政策→主题映射 = 共享单一真相源**:QGR-3 正在为短线量化闸门建该映射(owner-gated NEXT)。价值仓 AF-001 **消费/扩展同一冻结映射**(加长 horizon tier 权重 + 传统高股息层),**不重复造**。冻结/激活 owner-gated 共担。
- **评测竞技场复用**:价值腿(慢腿)可入 QGR-2 已冻评测竞技场(`docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md`)做同场公平比 vs 短线快腿 + baseline 面板。
- **sim 暂停期一致**:价值仓与短线同受 sim 暂停约束,直到 B 层前向确认 + go-live gate(owner+LiveArtifactRegistry+45 日真管线 shadow replay+人工 pin+重启)。

---

## 8. owner 决策记录(2026-06-22,AskUserQuestion)

1. **架构 = 独立资金分仓**(>¥5 万切出价值子账户 ≤3 槽,短线子账户 ≤5 槽保满;须 amendment 改 ≤5 总仓红线)。
2. **主题 = 全 4 层**(战略性新兴产业 + 卡脖子自主可控 + 人工智能+ 全链 + 传统产业升级高股息;未来产业排除)。
3. **持有哲学 = (备注)** 容忍短暂回撤(长期价值逻辑未变即可接受)+ **可适当做T摊薄成本**(→ §4.4 持有穿越周期 + 做T overlay)。
4. **执行 = 文档 only**(本文 + amendment 草案 + plan.html 任务;owner 审批后再 TDD)。

---

## 9. 诚实 caveat(强制)

- **盈利不保证(by design)**:价值仓求『稳妥』但无收益承诺;十五五 锚定 = 提高胜率的先验,非保证。
- **主题映射 hindsight 风险**:『哪个主题战略』有前视 + 主观映射风险 → 政策发布日 PIT + 预注册冻结 + baseline 面板证增量,否则 deflation 揪出。
- **做T overlay 张力**:做T 与『纯长线持有』有张力,设计为有界/确定性/env-OFF 默认/底仓地板保护、只降成本不伤核心 thesis;最需 codex 审查。
- **评测同诚实保障**:四诚实保障(冻结-then-read 防火墙 / 累计-N deflation / 显式披露 / 判据不放宽)价值腿同样适用;固定历史给不出无限次干净确认,go-live 须真前向 + shadow replay。

---

## 10. Sources(『十五五』政策)

- 中共中央关于制定『十五五』规划的建议(2025-10-28 公布)— [gov.cn](https://www.gov.cn/zhengce/202510/content_7046050.htm) / [共产党员网](https://www.12371.cn/2025/10/28/ARTI1761640401107119.shtml)
- 『十五五』规划纲要(2026-03-16 全国人大公布)— [中国人大网](http://www.npc.gov.cn/npc/c2/c30834/202603/t20260316_453274.html) / [新华网受权播发](https://www.news.cn/20260313/f2c5c2043ca34805835e1384958782da/c.html)
- 战略擘画综述(战略性新兴产业 + 未来产业)— [新华网](https://www.news.cn/politics/20251107/cf05e60a46ea4178b16b3b067ff7aae1/c.html)
- 加快高水平科技自立自强(集成电路/工业母机/高端仪器/基础软件/先进材料/生物制造)— [gov.cn 政策解读](https://www.gov.cn/zhengce/202510/content_7046306.htm)
- 『十五五』规划里的新质生产力 — [国家发改委](https://www.ndrc.gov.cn/wsdwhfz/202604/t20260413_1404628.html)
