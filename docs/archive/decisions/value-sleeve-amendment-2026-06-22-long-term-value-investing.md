# Amendment 2026-06-22 — 长线价值投资分仓(value sleeve)

> **状态**:**owner 批准 2026-06-22 ✅**(owner『审核通过.开』);Phase AF 实施中(AF-001 = 政策映射 registry infra + 16 主题 DRAFT done,commit `bf3cb44`;映射 `status: draft` 待 owner 确认主题清单→冻结)。
> **类型**:决策边界 amendment(新增长线价值投资引擎 + 改 ≤5 总仓红线为 per-sleeve)。**先于任何 Phase AF 代码**(红线:改决策边界先 amendment)。
> **关联**:总纲 R0 + `P0-7`(风险/预算/≤5 槽)+ `P0-7-amendment-2026-06-01-five-slot-rotation` + `P0-8-amendment-2026-06-01-llm-theme-research-peer-sourcing` + Phase AC(`P0-8-amendment-2026-06-12-style-classifier-and-value-line`)+ Phase W(`P0-10-amendment-line2-2026-06-01-position-thesis-advisory`)+ `quant-gate-rebar-amendment-2026-06-21` §2.3(主旋律/底部确认/不追涨)。**设计** `docs/research/value-investing-strategy-2026-06-22.md`。
> **触发**:owner 2026-06-22『可用资金达 ¥5 万触发价值投资,锚定十五五、避开热门炒作、深挖产业链、提前埋伏』+ 4 决策(设计文档 §8)。

---

## 1. 背景:为何需要本 amendment

系统现以 **≤5 槽单一组合**承载短线/超短线(`P0-7-amendment-2026-06-01` 把 `max_total_positions` 10→5)。owner 要在其上新增**长线价值投资引擎**:体量到位(总权益 ≥ ¥5 万)后切出独立资金做稳妥价值。价值投资机器(三层价值线 / 价值槽配额 / 风格分型 / 持仓 thesis / 软层)Phase AC/W **已建 ~95% 但从未接线**(`value_scores=None` → bit-identical 旧路径)。本 amendment **不造新机器**,而是开放接线所需的**决策边界变更**(主要是 ≤5 总仓 → per-sleeve)。

owner 决策(设计文档 §8):**独立资金分仓** / **十五五 全 4 主题层** / **容忍短暂回撤 + 可做T摊薄成本** / **本 session 文档 only**。

---

## 2. 本 amendment 锁定的变更(4 条)

### 2.1 ≤5 总仓 → per-sleeve cap(改 §2.4 / P0-7 check#6)
- **旧**:`config/risk.yaml` `position_limits.max_total_positions = 5`(单一组合,RiskEngine check#6 拒第 6 仓)。
- **新**:**两个独立子账户**——`SHORT` sleeve `max_positions = 5`(不变)+ `VALUE` sleeve `max_positions = 3`,**各自独立资金池 + 仓位 cap + 会计**。RiskEngine check#6 按子账户分别校验(不再一刀切 5)。
- **不变**:**仓位三连(单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万)按两子账户合并的总权益校验**(总杠杆/集中度红线不放松);熔断 ≤5 单/日 + 日亏 -5% + 连亏 3 笔 **跨子账户合并计**;排除四件套 / 涨停不 BUY 跌停不 SELL / long-only / 单一构造点 / 14-check 全留。

### 2.2 ¥5 万资金触发 + 价值仓资金切出(新会计)
- **触发**:**总账户权益 ≥ ¥50,000** 一次性激活价值仓(单向 latch + 滞回 re-arm ¥45,000 防抖);激活后回落 ¥5 万以下**不强制清价值仓**,仅停止加仓。
- **切出 glide path(默认值,config 参数 amendment-gated runtime 不可改)**:< ¥5 万 = 0% / ¥5–10 万 = 20% / ¥10–30 万 = 40% / ≥ ¥30 万 = 60%(cap);短线 working floor ≥ ¥4 万。价值仓**只在底部确认时逐步填向目标**,从不强制一次性部署。
- **owner 审批时可调**:¥50,000 触发线 / glide path 各档 / 短线 floor / 60% cap 均为 owner 设的 config 默认(amendment-gated)。

### 2.3 十五五 政策→主题映射激活(价值仓 scope;与 QGR-3 共享单一真相源)
- **scope**:价值仓纳入全 4 层——① 战略性新兴产业 ② 卡脖子自主可控 ③ 人工智能+ 全链 ④ 传统产业升级·高股息;**排除未来产业**(量子/核聚变/脑机/6G/纯具身智能 = 无盈利可估值的炒作)。
- **防 hindsight(沿用 `quant-gate-rebar` §2.3)**:每主题挂 `effective_from` = 政策文件发布日(十五五建议 **2025-10-28** / 纲要 **2026-03-16** / 行业政策各自日期),tilt 只从该日起生效;概念/行业成分 PIT(`ths_index` as-of + `index_classify`/`index_member_all`);映射 git 冻结作披露假设;**baseline 面板须证主题维度有增量**,否则 deflation 揪出。
- **共享**:与 QGR-3『政策→主题』映射**同一真相源**(价值仓叠加长 horizon tier 权重 + 传统高股息层,不重复造);**冻结/激活 owner-gated**,走专门 codex PIT-soundness 门。
- **LLM 边界不变**:LLM 仅 `theme_research` peer-sourcing(evidence-only,人工 pin),**永不进运行时数据路径 / 不剪 universe / 不否决板块**;纯量化路径始终可跑。

### 2.4 做T降成本 overlay(价值仓内,确定性、env-OFF 默认)
- **底仓地板**:每价值仓设最小核心底仓(默认目标股数 60%),**做T overlay 永不卖破底仓**(只 thesis-break / 硬风控能动底仓)。
- **有界波段**:可做T份额(默认 ≤40%)按确定性参考成本带(`cyq_perf` / 移动参考,**0 LLM**)高减低补,**严格 T+1**(只卖昨日已结算股、次日空出资金补回),每周期 round-trip 次数有界。
- **单一构造点不破**:做T 订单经同一 `instruction_plan_builder` + RiskEngine 14-check;`side/volume/limit_price` 确定性派生,**永不来自 LLM**。阈值 = config 参数(amendment-gated runtime 不可改)。
- **默认 OFF**:overlay env-OFF 落地;owner 重启/显式开启后才活,**关闭时价值仓 = 纯长持 bit-identical**。

---

## 3. 保留不变(安全地基红线全留,一条不破)
永禁真实下单(只 MockBroker/SimulationExecutor)/ feishu_interactive 人工执行 / 127.0.0.1 全层 / LLM 不写决策字段(仅 4 类文本 + 主题 evidence)/ RiskEngine 纯函数无 IO 无 `import backend.{llm,agents,mirofish,data}` / **InstructionPlan 单一构造点**(`grep "InstructionPlan(" ⊆ {model,builder,tests}`)/ PIT 可复现(字节+checksum+coverage)/ fail-closed(数据损坏)/ 排除四件套(ST/科创/北交/可转债永禁)/ 涨停不 BUY 跌停不 SELL / long-only / governance `EconomicMechanism` enum 不动(新机制 fail-closed until amendment)/ 飞书仅 WebSocket 入站 + 2 写端点 / config runtime 不可改 + hot-reload 全禁 / sim 暂停直到 B 层前向确认 + go-live gate。

---

## 4. 范围 / 门 / 待办
- **生效范围**:Phase AF(`backend/` 价值仓接线);**不碰 `scripts/factor_research/`(QGR 域)**;sim 暂停期 live 行为零改动(env-OFF + owner-gated)。
- **前置门链**:本 amendment(owner 批准)→ AF-001(十五五映射,与 QGR-3 共享,owner 确认 + 专门 codex PIT 门)→ AF-002(value_score 接线)→ AF-003(质量基本面)→ AF-004(底部确认 + 不追涨)→ AF-005(分仓 + ¥5万触发)→ AF-006(做T overlay)→ AF-007(监控 + 前端)→ go-live(单独 owner gate + 45 日真管线 shadow replay)。
- **诚实 caveat(强制)**:盈利不保证;主题映射 hindsight 风险(政策发布日 PIT + 预注册 + baseline 增量证明);做T overlay 与纯长持张力(有界/确定性/env-OFF/底仓地板,最需 codex 审查);固定历史无限次干净确认不可得,go-live 须真前向 + shadow replay。
- **codex**:本 amendment 为 docs(codex-exempt);Phase AF 各编码任务 commit 前各自走 codex 代码门(P0/P1/P2 修完;撞额度→/code-review high);AF-001 映射 PIT-soundness 走专门 codex 门。

---

## 5. owner 决策(2026-06-22,据此)
① 架构 = 独立资金分仓(>¥5 万切出价值子账户 ≤3 槽) ② 主题 = 全 4 层(未来产业排除) ③ 持有 = 容忍短暂回撤 + 可做T摊薄成本 ④ 执行 = 文档 only(本 amendment 草案 + 设计文档 + plan.html Phase AF)。**待 owner 批准本草案后,Phase AF 方可开建。**
