# P0-8 修订 — 2026-06-01 LLM 联网调查·产业链倒推 主题研究 peer-sourcing 层(方向①)

> **修订基准**: [P0-8 数据情报](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md) + [P0-8-amendment-2026-05-24-mirofish-advisory-core](./P0-8-amendment-2026-05-24-mirofish-advisory-core.md) + [P0-8-amendment-2026-05-24-tushare-data-source](./P0-8-amendment-2026-05-24-tushare-data-source.md)
> **关联(跨 base 触点)**: R0 §3(PIT 可复现)/ R0 §4(单一构造点)/ P0-9(LLM 仅在 top-N 后介入 → 本 amendment 开「调查作 source discovery 可前置」窄口)/ P0-10(LLM 字段权限)/ P1-7(¥100/日 hard + max_debates)/ P2-2(LiveArtifactRegistry pin + 人工 gate)/ Phase Q(产业链 KG)
> **修订日期**: 2026-06-01(规划 session #60)
> **决策人**: owner(AskUserQuestion 2026-06-01:方向① 答「依靠大模型的联网调查能力做第一层筛选,然后再进入量化筛选等后续操作」)
> **性质**: 决策边界锁定。**本 session 不写代码**;代码在 plan.html Phase Y(主题研究层)实施,实施前本 amendment 是前置门。
> **方法**: 3 轮 codex 对抗(round-3 专攻本条红线兼容性,verdict 固化于 §2/§3)。

## 0. 触发与意图

Owner 锁定 Line-1 引入「第一性原理·产业链倒推」:判未来大方向(宏观/政策/技术拐点)→ 受益板块 → 倒推必需产业链 → 卡脖子环节(断供巨大负面 × 还没炒热)→ 代表性标的。Owner 选定**用大模型的联网调查能力做第一层筛选,再进入量化**。

调研(`docs/research/industry-chain-reverse-deduction-2026-06-01.md`)诚实判断:**两端可纯量化复现(受益板块 / 未炒热 / 标的),中间两步是稀缺瓶颈**——产业链上下游图无干净开放 API(冷启动种子 `liuhuanyong/ChainKnowledgeGraph` 重建,记 provenance);卡脖子/国产化/进口依赖**基本无 API**,在研报/政策文 → 只能 LLM 抽取 + 人工 gate。

## 1. 核心张力(为何不能照字面实现)

「LLM 第一层筛选 → 然后量化」若**照字面 = LLM 先把全市场 universe 剪窄、量化只在剪窄集内筛**,则**同时撞 4 条不可动红线**(owner 未授权移除任何一条):
1. **R0 §3 PIT 可复现**:实时 LLM 联网取数无法 bit-exact replay。
2. **R0 §4 / P0-10**:LLM 永不写决策字段;不得决定资格。
3. **R0 §6 + P0-8-tushare-amendment**:**严禁「LLM 推理时取数」进运行时数据路径**(撞 PIT + LLM-数据隔离 + screening 0-LLM L-002 + 成本)。
4. **P0-8-amendment-mirofish-advisory**:资格纯量化;LLM/MiroFish **永不否决板块 / 静默剪枝 / 改合格集**;top-N 后保 ≥3 量化名额。

## 2. 决策:唯一红线安全的实现 = 「定时·留痕·人工 pin」的 peer-SOURCING 层(codex round-3 verdict)

> **LLM 联网调查 = 定时的、留痕的(provenance-captured)、人工 pin 的 peer-sourcing 层。它只能【追加】主题候选,永不作全局 universe 过滤器。量化仍是资格权威,纯量化路径始终完整可跑。照字面的「LLM 先剪枝」被否决。**

新模块 `backend/theme_research/`,定位与 MiroFish「evidence-only by-construction」同构,但增「LLM+web 调查 → 人工 pin 的机读候选 artifact」能力:

1. **定时·非实时**:调查作**定时有界 research job**(盘前 / 周频,非 per-signal runtime fetch)。**严禁**进 Line-1 实时信号路径取数。
2. **全留痕(R0 §3 扩展)**:调查的 web 输入 + LLM 输出在调查时**全部捕获**进 append-only、content-addressed 快照(仿 `MarketDataSnapshot` 存**原始字节 + checksum**):SERP 字节/snippet、抓取页字节或渲染文本字节、URL、headers/status/redirects、fetch_time、prompt、model/version/params、tool transcript、LLM response 字节、解析出的 pending artifact、parser/config 哈希。**replay 永不联网 / 永不调 LLM**——只校验哈希 + 消费 pin 的 artifact。隐藏 SERP/page 字节的 run = **non-promotable**(不可 pin)。引用入 `SignalInputManifest`。
3. **evidence ↔ 机读候选 分离**:LLM 原始产出**只能**写 `evidence_collection.content`(展示/审计文本,P0-10 允许的 4 字段之一);**严禁** live code 正则解析 evidence 文本成候选。机读候选集 = **独立的、人工批准的、content-addressed pin artifact**(stock codes + 主题/板块映射 + 分数),与 evidence 文本物理分离。
4. **新 evidence 前缀 `THEME-`(本 amendment 解锁第 6 前缀)**:P0-8 原锁 5 前缀(`NEWS-/MIROFISH-/MARKET-/RISK-/DEBATE-`);本 amendment 新增 `THEME-` 供主题研究输出审计区分。`validate_evidence_id` 扩 6 前缀;by-construction 仍无 `RiskCheckSummary` 管线。
5. **人工 gate + LiveArtifactRegistry pin(P2-2 沿用)**:候选 artifact 经飞书/人工**逐条批准**+ 按批准哈希 pin 后才能影响 live 选股;**无 runtime 自动晋升路径**。
6. **量化仍是资格权威(P0-9 窄口)**:P0-9「LLM 仅在纯量化 top-N 后介入」**窄化为**:定时主题研究**可前置**,但**仅作 source discovery**;**全市场纯量化扫描始终强制且不受影响**。pin 的主题候选**作为 peer-sourced 候选进确定性管线**,**仍须过同样的硬门**:排除四件套 + 可负担性(BudgetTierPolicy)+ RiskEngine 14-check + builder 单一构造点 + 飞书人工执行 gate。**LLM 浮现候选,量化定资格,人工执行**;两条路互不否决。
7. **主题配额有界 + 保纯量化(对称 ≥3 量化名额)**:主题源候选占 shortlist 的**保留配额 ≤ `total_daily_cap − 3`(当前 5 单/日 → 最多 2)**;**≥3 纯量化名额始终保留**;纯量化候选**永不被主题否决**;**无新鲜 pin artifact 时主题配额 = 空、纯量化照常跑**(人工 gate 永不 stall Line-1)。
8. **prompt-injection 容器化**(web 文本是敌意数据):源 allowlist;调查 agent **无** secrets / 交易状态 / RiskConfig 访问;严格输出 schema;确定性校验;飞书走固定转义 renderer;exact-hash 人工批准;批准后才过量化 + RiskEngine 门。
9. **成本有界(P1-7 沿用)**:调查 job 受 cadence / max_runs_per_day / max_web_fetches_per_run / max_llm_calls_per_run / max_tokens / timeout / max_candidates_per_artifact 多重 bound;写**同一** `llm:usage:{utc_date}` 真·预留计数器;¥100/日 hard 不变,**严禁**跨阈调用。
10. **产业链 KG(Phase Q)作支撑 pin 快照**:choke-point ≈ pin 的产业链子图上 NetworkX betweenness/PageRank(确定性,无需 Neo4j);criticality/卡脖子/国产化 = **周/不定期人工 pin 快照**,**无快照则该特征权重 = 0**(不做 stale 推断)。日频可跑特征 = 估值分位(`daily_basic` PE/PB 滚动分位)+ 拥挤/关注分位(资金流/换手/龙虎榜)+ 板块动量。「临界但未持有」打分 `criticality × (1−crowding_pct) × (1−valuation_pct)` 须叠基本面过滤防价值陷阱。

11. **结构化调查/分析 prompt = 一等设计物(owner 2026-06-01 强调:必须设计好、明确如何调查、怎样分析)**。LLM 联网调查**绝非自由发挥**——必须由一份**显式结构化 prompt SOP** 驱动,把「第一性原理·产业链倒推」编码为**明确的分步方法论 + 分析框架 + 输出 schema**:
    - **调查方法(how to investigate)**:① 判未来大方向(宏观/政策/技术拐点;限源 allowlist:官方政策站 / 券商研报 / 白名单财经源)→ ② 圈受益板块(映射申万行业 / 概念板块 taxonomy)→ ③ 倒推必需产业链(上游材料→中游→下游;读 pin 的产业链 KG)→ ④ 识别卡脖子环节(断供巨大负面 × 替代难度 × 供应集中度 × **「还没炒热」= 低关注/低估值分位**,逆向/早期偏好)→ ⑤ 挖代表性标的(链环节→上市公司,概念/行业成分映射)。
    - **分析框架(how to analyze)**:第一性原理(非跟风共识)+ 每步必带**引用证据 + 不确定性/置信度** + 显式记录 null result(查无则透明标注,不臆造);敌意 web 文本经源 allowlist + 严格 schema 校验(prompt-injection 容器化,§2.8)。
    - **输出 schema**:结构化链路 `趋势 → 板块 → 产业链环节 → 卡脖子理由 → 候选 codes + 理由 + 置信度`;机读候选与 evidence 文本物理分离(§2.3)。
    - 调研背书:`Expert Investment Teams`(2602.23330)细粒度 SOP-编码 prompt 显著优于通用角色 prompt(p<0.0001);`nexus` repo 的 5-agent 辩论 + RedTeam 挑战 + null-result 透明范式可借。

12. **保留升级进化的可能(owner 2026-06-01 强调;沿用 P2-2 自进化范式)**。调查/分析 prompt **不可硬编码进代码**,必须走**文件式版本化 prompt registry**(`config/prompts/theme_research/{version}.yaml` + `prompts.lock.json`,git 版本化 + restart-gated + 改动走 amendment),以便后续**经 P2-2 保守 3 路径离线进化**(DSPy/GEPA 离线 prompt 演化 ≤¥5/次 + FinMem 风格 exemplars ≤3/prompt + RAG provenance-gated),**全程人工 gate + 45 日 shadow + 重启生效**(P2-2-amendment-2026-05-24 不变;7 禁不变,含 LLM 自动决策权)。**进化的是「措辞/exemplars/参数」,不是「第一性原理倒推 SOP 骨架」**(骨架是 frozen 方法论,类比 T-001 交易员人格卡不可变 / T-004 exemplars 可进化的分离)。prompt version hash 入 `LiveArtifactRegistry` pin 集;实时只认批准的 prompt 版本哈希。

## 3. 锁定的边界条文(amendment 必锁;codex round-3)

- `backend/theme_research/` **只能在定时 research job 内**调 LLM+web;**signal / runtime / replay 路径不联网、不调 LLM**。
- 所有 research 输入/输出 append-only + content-addressed + checksum 校验 + 被 `SignalInputManifest` 引用。
- LLM 只写 `evidence_collection.content`(新 `THEME-` 前缀);**无 live selector 解析 LLM 原始文本**。
- live 选股**只读人工 pin 的主题候选 artifact(按批准哈希)**。
- 主题候选 = **sourcing-only**:必过 排除四件套 + 可负担性 + 纯量化硬门 + RiskEngine 14-check + builder 单一构造点 + 飞书人工执行 gate。
- 纯量化候选**永不被否决**;**≥3 纯量化名额保留**;主题配额 ≤ `total_daily_cap − 3`(当前最多 2)。
- `backend/screening/` / `backend/marketdata_snapshot/` / `backend/risk/` / cost-guard core **保持 0-LLM + import 隔离**(R0 §6 + P0-8-tushare-amendment 运行时数据路径 ban 不变)。
- 调查/分析 prompt = 显式结构化 SOP(5 步倒推方法论 + 分析框架 + 输出 schema,§2.11),**严禁硬编码进代码**;走文件式版本化 registry(`config/prompts/theme_research/`,git + restart-gated + amendment-gated),prompt version hash 入 `LiveArtifactRegistry`;进化只动措辞/exemplars/参数(经 P2-2 离线 + 人工 gate + 45 日 shadow + 重启),**SOP 骨架 frozen**(§2.12)。

## 4. 不变量(本 amendment 不触碰)

- 安全地基全留:永禁真实下单 / 飞书人工执行 / 127.0.0.1 / RiskEngine 14-check 纯函数 / 单一构造点 / fail-closed。
- MiroFish advisory-core(P0-8-amendment-2026-05-24)语义不变:MiroFish 仍 evidence-only + 有界重排 ≤1 分位 + 永不否决板块。**主题研究层与 MiroFish 并存**:两者都写 evidence、都经 CandidateSelector;主题层走 peer-sourcing(过硬门 + 人工 pin),MiroFish 走合格集内 ≤1 分位重排。
- P0-9 全市场纯量化 universe 规则 + 排除四件套 + long-only 不变;主题研究**不剪 universe**。
- Tushare SDK-only 运行时数据路径(P0-8-amendment-tushare)不变;主题研究的 web 取数**不是**运行时行情数据路径(它是留痕 + 人工 pin 的 sourcing 层,实时/replay 不联网)。

## 5. 落点(plan.html Phase Y;实施前本 amendment 是门)

`backend/theme_research/`(**结构化倒推 prompt SOP**[§2.11]+ LLM+web 调查 job + provenance 捕获 + pending artifact)+ `config/prompts/theme_research/{version}.yaml`(文件式版本化 registry,§2.12)+ `backend/knowledge_graph/`(Phase Q 产业链 KG 子能力)+ `CandidateSelector` peer-sourcing 接线 + 人工 pin/审批通道 + 对抗测试先写(种入恶意 web 文本 → 断言不污染候选 / 不写决策字段 / 纯量化候选不被否决 / 无 pin 则配额空)。**ship 序**:codex 判主题层最复杂、依赖下游最少 → **放最后**(方向③ 轮动先行)。**进化接口**:prompt registry + LiveArtifactRegistry pin 接 Phase R / P2-2 离线进化(GEPA/exemplars,人工 gate)。
