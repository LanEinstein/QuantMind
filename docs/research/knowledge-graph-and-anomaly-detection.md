# 研究报告:本地金融知识图谱 + 持仓异动 / 补仓监控

> Research dossier — Local financial knowledge graph (GraphRAG) + real-time position-monitoring intelligence
>
> 范围:为 QuantMind(全本地 127.0.0.1、永禁 hosted SaaS、A 股模拟实盘 + 飞书人工执行)
> 调研两件事:
> (A) 本地金融知识图谱 — 存量化策略 + 回测溯源、概念、板块/个股/ETF 关系、操盘手"技能"启发式,LLM agent 可查(GraphRAG)、可演化;
> (B) 持仓实时智能 — 价/量/资金流异动 → 卖出告警;识别优质补仓机会。
>
> 编写日期:2026-05-24 · 状态:research-only(无代码,无 SSoT 改动)
> 约束基线:所有候选必须能离线 / 自托管运行,优先复用项目已装包(`networkx 3.4.2` / `scikit-learn 1.6.0` / `akshare 1.18.46` / `langchain-core 0.3.83`),并遵守 P2-2 文件式 RAG provenance 红线(`data/rag/` + `provenance.jsonl`,零 Mongo)。

---

## 0. 执行前提与硬约束(决定取舍)

| 约束 | 来源 | 对选型的影响 |
|------|------|--------------|
| 全层 127.0.0.1 only,远程仅 SSH tunnel,严禁 hosted SaaS | CLAUDE.md §2.9 (P1-6) | 排除 Neo4j AuraDB / Zep Cloud / 任何托管 GraphRAG;只考虑可嵌入或本机自托管 |
| LLM 仅 DeepSeek / Qwen(DashScope)/ MiniMax,严禁新 provider | §2.10 + P2-2 七禁路径 | GraphRAG/KG 构建必须能注入 OpenAI 兼容 endpoint(三家都兼容)且支持本地 embedding |
| LLM 严禁写决策字段 / RiskCheckSummary;positive list 仅 4 类 | §2.2 (P0-10) | KG 可作为 evidence/reasoning 输入;**严禁**让图谱查询结果直接成为 BUY/SELL 决策;异动/补仓信号是 evidence,不是指令 |
| RAG 白名单 5 源 + 文件式 provenance,零 Mongo | §2.12 (P2-2) | 新增 KG **不得**违反"文件式 + git + provenance.jsonl";图谱可作为 RAG 之上的派生索引,但溯源真相源仍是 `data/rag/` |
| 30s 快照节奏,~13 标的(锁定 watchlist),全 long-only | §2.4/§2.5 (P0-7/8/9) | 异动检测必须 30s 级、十几个标的、轻量;**不需要**毫秒级 HFT 微结构引擎 |
| 数据 = akshare/adata/baostock 免费源,无 ceiling;无真 L2 streaming | §2.10 + 调研确认 | OFI/VPIN 可用 akshare 分笔/盘口快照**近似**,但非真实逐笔流;30s 轮询而非事件流 |
| fail-closed for data corruption / fail-open for infra | CLAUDE.md §3 | 异动检测器数据缺失 → 不发假信号(fail-closed 到 HOLD/不告警);基础设施抖动 → 不阻断 |
| 模块隔离:`backend.evolution` 严禁 import `backend.{api,broker,risk,llm,agents,mirofish,data}` | §2.12 红线 17 | KG 构建若放进 evolution,须保持隔离;若服务于 agent 检索,放新 `backend/kg/` 并审慎依赖边界 |

> **核心结论先行**:本场景(十几个标的、单机、演化型小图、强溯源、保守预算)的最优解**不是**重型 GraphRAG。推荐 **KùzuDB 的活跃 fork(LadybugDB / Vela 分支)或 NetworkX+SQLite 作嵌入式图存储 + LightRAG 作 GraphRAG 检索层**,并用文件式 provenance 作为单一真相源。详见 §5 Recommendations。

---

## 1. 知识图谱存储 & GraphRAG

### 1.1 自托管图存储对比

| 存储 | 类型 | License | 嵌入式? | 成熟度 | Python | 适配本场景 | 关键风险 / 备注 |
|------|------|---------|---------|--------|--------|-----------|----------------|
| **Neo4j Community** | 原生图、磁盘、Java | GPLv3 (Community) | ✗ 需 JVM server | 极高,生态最全(Cypher/APOC/GDS 受限) | `neo4j` driver | 中。重,需常驻 JVM 进程,与"嵌入式单机"哲学不符 | Community 缺热备/角色权限;GDS 高级算法仅企业版;GPLv3 传染性需注意分发 |
| **Memgraph** | 内存原生图、C++ | **BSL 1.1(非 OSI 开源)** | ✗ server | 高,实时性强(比 Neo4j 低延迟 3–40×) | `gqlalchemy` / bolt | 低。BSL 商用受限(~$25k/yr),稳定性有报告问题,内存型不适合长期累积图 | License 红旗 + 内存成本 |
| **ArangoDB** | 多模(文档+图)| **Community License(非 OSI,2023-10 起;100GB 上限 + 商用限制)** | ✗ server | 高 | `python-arango` | 低。多模优势对本场景无用,License 非纯开源 | 2023 改 License,排除 |
| **KùzuDB** | 嵌入式列存图、C++ | **MIT** | ✓ 进程内(类 DuckDB) | 中→**注意:原仓 2025-10 被 Apple 收购并 archive** | `kuzu` pip | **高(但需用 fork)** | 原仓停更;社区 fork:**LadybugDB**(MIT,延续原版)、**Vela-Engineering/kuzu**(加并发写,面向多 agent)。性能 benchmark 曾报 vs Neo4j 大幅领先 |
| **NetworkX + SQLite** | 内存图 + 文件持久化 | NetworkX: BSD;SQLite: 公有领域 | ✓ 纯文件 | 极高(已装 3.4.2) | 原生 | **极高(零新依赖)** | 无 Cypher / 无图查询优化;十万节点内纯 Python 足够;大图遍历慢。可用 SQLite 存边表 + NetworkX 内存重建 |
| **Oxigraph (RDF/SPARQL)** | 嵌入式三元组库、Rust+RocksDB | **Apache-2.0** | ✓ 进程内 / CLI | 中,SPARQL 1.1 合规 | `pyoxigraph` | 中。若要 FIBO 等 RDF 本体 + SPARQL 推理则强;但学习曲线 + 量化关系建模偏重 | 适合"本体驱动"路线;对"轻量演化图"偏重 |

**取舍要点**

- License:纯 OSI 开源只有 **KùzuDB 系(MIT)、NetworkX(BSD)、Oxigraph(Apache-2.0)**。Memgraph(BSL)、ArangoDB(Community License)被自托管纯开源约束排除。
- 嵌入式优先:127.0.0.1 单机 + 不想再多一个常驻 JVM 服务 → 嵌入式(Kùzu / NetworkX / Oxigraph)优于 server 型(Neo4j / Memgraph / Arango)。
- 规模:本场景图很小(~13 标的 + 几十策略 + 几百概念/因子/启发式,年增量有限)。**十万节点级以内,NetworkX 内存图完全胜任**;到百万级或要并发多 agent 写才需要 Kùzu 这类列存引擎。
- KùzuDB archive 事件:原 `kuzudb/kuzu` 已于 2025-10 被 Apple 收购并归档。**不要**依赖原仓;若选 Kùzu 路线,锁定 **LadybugDB**(社区延续,MIT)或 Vela fork,并把版本钉进 P2-2 文件式注册表。

### 1.2 GraphRAG / KG-检索框架对比

| 框架 | License | Stars(约) | 增量更新 | 本地 LLM/embedding | 存储后端 | 索引成本 | 适配本场景 |
|------|---------|-----------|----------|-------------------|----------|----------|-----------|
| **Microsoft GraphRAG** | **MIT** | 高(旗舰) | 弱(批式,改数据近乎全量重算 Leiden 社区);Local/Global/DRIFT 三种检索 | 可配 OpenAI 兼容 endpoint + 本地 embedding | 无存储抽象层(内置 parquet/networkx) | **高**(社区摘要烧大量 token,官方提醒"先小规模") | 低-中。重,索引贵,演化型小图不划算 |
| **LightRAG**(HKUDS,EMNLP 2025)| **MIT** | ~28k | **强**:新文档并入即可,无需重建社区,增量更新约快 50% | Docker 化本地 embedding/rerank/storage;文档完善,作者活跃答疑 | 可插拔(默认轻量;支持外部图/向量库) | 低-中(无社区摘要的全量重算) | **高**。增量友好 + 本地友好 + 检索= 向量取实体/关系 + 图作结构上下文,正好契合"演化型 KB" |
| **nano-graphrag** | **MIT** | ~3.8k | 中(比 MS 轻) | Ollama / transformers / DeepSeek / 自定义;OpenAI 兼容 | **统一存储抽象:NetworkX(默认)或 Neo4j** | 低(~1.1k 行,可读可改,Leiden 社区) | **高(作可 hack 基座)**。代码极简、可审计、易加 provenance 钩子;最近发布 v0.0.8(2024-10),维护温和 |
| **LlamaIndex KG / PropertyGraphIndex** | MIT | 高 | 中 | 全本地可配 | 多(含 Kùzu / Neo4j / SimplePropertyGraphStore) | 中 | 中。生态全但抽象层厚,依赖重;若已用 LlamaIndex 可考虑 |
| **Graphiti(getzep)** | **Apache-2.0** | ~23k | **极强:实时时序图,episode 流式增量,带 provenance + 双时间(t_valid/t_ingest)** | 可配 OpenAI 兼容;支持 Neo4j / FalkorDB / Kùzu | Neo4j / FalkorDB / Kùzu(需 server 或嵌入) | 中 | **高(若需"事实随时间变化 + 溯源")**。时序 + provenance 与本项目 audit/溯源哲学高度一致;但默认依赖图 server |
| **Zep(平台)** | 商用/云 | — | — | hosted | — | — | **排除**(hosted SaaS,违反 §2.9) |

**取舍要点**

- **演化型 KB(策略上/下线、启发式增删)→ 增量更新能力是第一指标**。MS GraphRAG 的批式社区重算最不适合;**LightRAG / Graphiti / nano-graphrag** 都增量友好。
- **溯源是项目核心红线**。Graphiti 的双时间 + provenance 模型与 QuantMind 的 `provenance.jsonl` / audit 哲学最契合;但它默认要图 server(Neo4j/FalkorDB)——可用其 Kùzu 后端走嵌入式,或只借鉴其 episode/双时间 schema 思想而自建。
- **可审计 / 可改 / 零黑箱**:nano-graphrag(~1.1k 行)最容易塞进 P2-2 的 sanitiser + provenance 钩子,且默认 NetworkX 后端 = 项目已装包。
- **token 成本**:MS GraphRAG 社区摘要烧钱,与 P1-7 日 ¥20 hard / Kimi ¥4 预算冲突;LightRAG / nano-graphrag 索引便宜得多。

### 1.3 推荐主栈(本节)

> **主栈 = LightRAG(检索层,MIT)+ NetworkX/SQLite 或 LadybugDB-Kùzu(图存储,嵌入式 OSI)+ 文件式 provenance(单一真相源,沿用 P2-2)+ 借鉴 Graphiti 的双时间 schema 做"策略上/下线"时序。**

理由:增量友好(LightRAG)、嵌入式零常驻服务、纯 OSI 许可、复用已装 NetworkX、token 便宜、溯源沿用现有红线。重型 MS GraphRAG 仅在"需要全局主题摘要的大语料"时才回头考虑。

### 1.4 KG schema 草图(node / edge 类型)

> 设计原则:**frozen Pydantic v2 strict + extra="forbid"**(对齐 §3 工程原则)、English 命名、节点/边都带 `provenance_ref`(指向 `data/rag/provenance.jsonl` 的 `doc_id` + `content_sha256`)以满足溯源红线。**任何节点/边的"决策性"字段严禁由 LLM 写**;LLM 仅产出 `summary` / `rationale` 文本(对齐 P0-10 positive list)。

**节点类型(Node labels)**

| 节点类型 | 说明 | 关键属性 |
|----------|------|----------|
| `Strategy` | 一条量化策略 | `strategy_id`, `name`, `family`(trend/mean_reversion/event/factor…), `status`(candidate/shadow/active/retired), `created_at`, `retired_at`, `summary`(LLM), `provenance_ref` |
| `BacktestResult` | 策略的一次回测/影子结果(P0-6 45 日窗口) | `run_id`, `window`(45 trading days), `max_drawdown`, `pnl`, `excess_vs_csi300`, `signal_rate`, `passed`(bool), `as_of_date` |
| `Factor` | 因子/指标(动量、波动、量比、OFI…) | `factor_id`, `name`, `category`(price/volume/flow/fundamental/sentiment), `definition`, `provenance_ref` |
| `Concept` | 金融/量化概念(均值回归、涨跌停、滑点…) | `concept_id`, `name`, `definition`(LLM summary), `provenance_ref` |
| `Sector` | 行业/板块(申万一级等) | `sector_code`, `name` |
| `Instrument` | 个股 / ETF(锁定 watchlist 13 标的) | `code`, `name`, `board`(sh_main/sz_main/chuangye/etf), `is_etf` |
| `Heuristic` | 操盘手/基金经理"技能"启发式("跌破 20 日线减仓") | `heuristic_id`, `text`(LLM), `attributed_to`(book/trader/agent), `confidence`, `provenance_ref` |
| `Event` | 事件(MiroFish 隐性因果、政策、财报)— 仅入 evidence 域 | `event_id`, `type`, `summary`, `evidence_id`(MIROFISH-/NEWS- 前缀), `t_valid` |
| `SourceDoc` | RAG 白名单文档(arxiv/akshare/…) | `doc_id`, `source`, `content_sha256`, `published_at` |

**边类型(Relationship types)**

| 边 | 方向 | 说明 |
|----|------|------|
| `USES_FACTOR` | Strategy → Factor | 策略使用某因子 |
| `BACKTESTED_AS` | Strategy → BacktestResult | 策略 ↔ 回测溯源(承载 PnL/回撤/超额) |
| `APPLIES_TO` | Strategy → Instrument / Sector | 策略适用标的/板块 |
| `BELONGS_TO` | Instrument → Sector | 个股归属板块 |
| `CORRELATES_WITH` | Instrument ↔ Instrument | 相关性(带 `rho`, `window`) |
| `EXPLAINS` / `DEFINED_BY` | Concept → Factor / Factor → Concept | 概念-因子互链 |
| `RECOMMENDS_ACTION` | Heuristic → Concept/Factor | 启发式援引的概念(action 文本仅 evidence,**不**入决策) |
| `DERIVED_FROM` | (任意节点) → SourceDoc | 溯源边,**强制存在**(无来源的节点视为低可信) |
| `SUPERSEDES` | Strategy → Strategy | 新策略取代旧策略(承载演化时序 `t_valid`) |
| `TRIGGERED_BY` | Event → Instrument | 事件影响标的(MiroFish 加分,cap=1) |
| `AFFECTS` | Event → Sector/Concept | 事件传导(隐性因果链) |

**演化(策略上/下线)建模**:用 Graphiti 风格双时间——节点 `status` + `SUPERSEDES` 边 + `t_valid/t_ingest`,使"某策略在某日因影子未达 P0-6 门槛被 retire"成为可查询、可溯源、可回滚的图事实,而不是覆盖式删除(契合 §3 不可变优先 + P2-2 状态回滚)。

---

## 2. 金融 KG 构建(从文档播种)

### 2.1 抽取流水线(对齐 2024-2025 SOTA)

文档(papers / 策略代码 / 新闻)→ 实体&关系抽取 → 三元组 → 图。SOTA 系统与做法:

| 系统 / 方法 | 年份 | 做法要点 | 对本项目可借鉴点 |
|-------------|------|----------|------------------|
| **FinKario** | 2025 | 事件增强,从研报抽取;305k 实体 / 9.6k 三元组 / 19 种关系 | 关系类型先收敛到 ~20 种(对齐 §1.4 边表),避免关系爆炸 |
| **FinDKG** | 2023-24 | 动态知识图谱 + LLM,面向全球金融,带时间维 | 动态/时序图思路 → 对应策略演化 + 事件时序 |
| **FinCaKG-Onto** | 2025 | **因果**知识图谱 + FIBO 本体引导;因果检测 + 实体链接 + 因果对齐 | 与 MiroFish"隐性因果链"高度契合;因果边 = `AFFECTS`/`TRIGGERED_BY` |
| **FinReflectKG** | 2025 (ACM ICAIF) | **Agentic 构建 + 评估**(自反思校验三元组质量) | LLM 抽完让另一 agent 校验(对齐项目多 agent + fail-closed) |
| **LLM-empowered KG construction: survey** | 2025 (arXiv 2510.20345) | 综述:本体驱动 vs 文档自归纳本体两条路线 | 本项目宜"本体驱动"(锁定 §1.4 schema)而非自由归纳,符合红线可控性 |

### 2.2 本体(ontology)选择

- **FIBO(Financial Industry Business Ontology)**:行业标准 RDF 本体,覆盖工具/实体/合约。**全量 FIBO 过重**;建议只取与权益/量化相关子集(Instrument、Sector、Corporate Action)对齐到 §1.4 schema,不引入完整 FIBO 推理。
- 量化侧 schema 本项目自定义(Strategy / Factor / BacktestResult / Heuristic),因为 FIBO 不覆盖"策略-回测-启发式"。
- 若走 Oxigraph/RDF 路线,FIBO 可直接 import 做 SPARQL;若走属性图(NetworkX/Kùzu),把 FIBO 子集降维成节点/边标签即可。

### 2.3 抽取实现建议(契合现有红线)

1. **来源仅限 P2-2 白名单 5 源**(arxiv / semanticscholar / openreview / github releases / akshare changelog),经现有 `RagIngester`(sanitiser + provenance + 注入计数)后才进抽取——**复用,勿另开抓取通道**。
2. **抽取用 LLM(DeepSeek 主)产出三元组草稿**,但:
   - LLM 只产出 `(subject, predicate, object, evidence_doc_id, confidence)` 候选;
   - **强制每条三元组带 `provenance_ref`**,无来源的丢弃(fail-closed);
   - 注入计数高的文档"标记不信任"(沿用 `injection_markers_flagged`)。
3. **第二 agent 反思校验**(FinReflectKG 风格):谓词是否在 §1.4 白名单内?主/宾是否已存在或可新建?不通过 → 丢弃,不写图。
4. **人工 gate + 飞书通知**(沿用 P2-2):新策略/启发式入图前主动飞书通知,人工 confirm,严禁 LLM 自动落库决策性节点。
5. **图 = 派生索引,provenance.jsonl = 真相源**:图可随时从 `data/rag/` + 三元组 ledger 重建,满足"checksum 失败拒自动恢复 + append-only"哲学。

> 候选 SDK:`langchain-experimental` 的 `LLMGraphTransformer` 或 LightRAG 内置抽取——但**优先用项目已装 `langchain-core` + 自写薄抽取器**,避免引入重依赖且便于塞 provenance 钩子。

---

## 3. 异动 / 异动检测(持仓监控,卖出告警)

> 目标:30s 快照、~13 标的、价/量/资金流异动 → evidence(给 risk_officer/fund_manager agent),**不直接下单**。fail-closed:数据缺失/质量不达标(沿用 `DataQualityState`)→ 不产假信号。

### 3.1 方法分层对比

| 层级 | 方法 | Python 库(优先已装) | 30s/十几标的可行性 | 适用异动 | 备注 |
|------|------|---------------------|-------------------|----------|------|
| **统计(首选,轻量)** | z-score(滚动)/ EWMA / Bollinger 带,作用于**成交量**与**收益率** | `numpy`/`pandas`(已具);`scipy` | 极高(O(1) 增量) | 量能突增、价格突破、波动放大 | 解释性强、零训练、可审计 → 与红线"可解释 evidence"最契合 |
| **量比 / 量价** | 量比(当前分钟量 vs 过去 N 日同时段均量)、成交额异常 | akshare 快照 + pandas | 高 | A 股特色"放量异动"、尾盘异动 | akshare 提供分时/盘口快照(3s 级队列、逐笔方向),可近似 |
| **变点检测(CPD)** | `ruptures`:PELT / BinSeg / Window;在线 BOCPD | **`ruptures`**(需装,纯 Python/MIT-style);BOCPD: `bayesian_changepoint_detection` | 中-高(BOCPD 在线增量;ruptures 适合小窗回看) | 趋势/波动结构突变、盘中波动 pattern 断裂 | 2024-25 研究在 CSI300/SP500 日收益上有效;盘中波动结构断点(arXiv 2404.11813) |
| **regime 检测** | 高斯 HMM(bull/bear/neutral 三态),日线/分钟级 | **`hmmlearn`**(需装,BSD)| 中(离线/低频拟合,在线推断 state) | 市场状态切换 → 调整告警阈值 | 标准做法;3-state GaussianHMM,观测=收益+波动。宜慢频(日/30min)训练,盘中只 infer |
| **无监督 ML** | Isolation Forest(多变量:return+vol+量比+OFI) | **`sklearn.ensemble.IsolationForest`**(已装 1.6.0)| 高(轻量,可批训练每日/盘中打分) | 多维联合异常(单维不显著但组合异常) | 已装包,零新依赖,首选 ML 方法 |
| **深度(谨慎)** | Autoencoder 重构误差;LSTM-AE | PyTorch(项目有 pytorch-patterns skill) | 中(训练成本 + 黑箱性差,解释弱) | 复杂时序异常 | **不推荐首期**:黑箱、与"可解释 evidence"红线张力、数据量小易过拟合 |
| **在线/流式** | `river` 在线异常(HalfSpaceTrees / 在线 z-score) | `river`(需装,BSD) | 高(为流式设计,30s 增量天然契合) | 概念漂移下的在线异常 | 若要真正"流式 30s 增量、低内存",river 比反复重算 sklearn 更合身 |

### 3.2 资金流 / 订单流(OFI / VPIN)在 A 股 + 30s 下的可行性

- **OFI(Order Flow Imbalance)**:盘口买卖压力不对称;多档合并显著提升对近期价变的解释力(emergentmind/Markwick;CSI300 期指证据 arXiv 2505.17388)。
- **VPIN**:按等量 bucket 衡量信息化交易概率;**VPIN > 0.7** = 单边量占比过高,大单向移动预警;**< 0.3(spike 后)** = 信息流被吸收,可能反转;**> 0.6 持续** = 趋势态,暂停均值回归。→ 这套阈值可直接做**卖出告警(VPIN spike + 价跌)** 与**补仓提示(VPIN 回落 + 超卖)** 的 evidence。
- **A 股数据现实(关键)**:akshare 提供分时/盘口快照(L2 队列约 3s 更新、逐笔成交方向),**但是免费轮询而非真实逐笔流**。因此:
  - 30s 轮询可计算**近似 OFI/量比/主动买卖盘占比**,够做"异动 evidence",**不够**做毫秒级 HFT;符合本项目"加分 evidence、非核心、非下单"定位。
  - 真实 L2 streaming 需付费数据源 → 与"免费源 + 无 ceiling 但保守"取舍下**暂不引入**;用快照近似 + 标注精度等级(fail-closed 标 degraded)。

### 3.3 异动检测推荐组合(30s × ~13 标的)

```
第一层(必备,可解释):滚动 z-score / EWMA / Bollinger on {return, volume, 量比}
   → 单维显著即产 MARKET- evidence(命名前缀沿用 P0-8 evidence_id)
第二层(联合,已装包):IsolationForest 多变量打分(每日训练 + 盘中 infer)
   → 多维联合异常补单维盲区
第三层(结构,需装 ruptures + hmmlearn):
   - HMM regime(慢频)调阈值:bear regime 下收紧卖出告警
   - ruptures/BOCPD 盘中波动结构断点
第四层(资金流,akshare 近似):OFI / 主动买卖盘占比 / VPIN 近似
   → spike + 价跌 = 卖出告警 evidence;回落 + 超卖 = 补仓候选 evidence
```

新增依赖建议:`ruptures`(MIT-类)、`hmmlearn`(BSD)、可选 `river`(BSD)。IsolationForest / numpy / pandas 已具。

---

## 4. 补仓 & 卖出信号逻辑(规则框架)

> 原则:这些是**规则化 evidence/proposal**,经 RiskEngine 14-check + 仓位三连(单股 ≤15%/总仓 ≤70%/单次 ≤5 万)+ long-only + 熔断,最终仍 fund_manager 倡议 + 人工飞书执行。**严禁**绕过 RiskConfig。

### 4.1 卖出信号框架(established)

| 框架 | 规则 | 出处 / 教材 | 在本项目落点 |
|------|------|------------|--------------|
| **ATR 跟踪止损** | 价跌破 `peak − k×ATR`(常用 k=3 跟踪、k=2 日内硬止损) | Van Tharp《Definitive Guide to Position Sizing》 | 已有 `backend/risk/stop_loss.py::check_trailing_stop` → 可扩 ATR 版 |
| **百分比跟踪止损** | 从峰值回撤 X% 触发 | 通用 | 已有 trailing_stop_pct |
| **波动率止损** | 止损距离 = f(ATR),据波动自适应 | Van Tharp | regime/HMM 高波动态下放宽,低波动态收紧 |
| **回撤退出** | 组合/个股回撤超阈值减仓(P0-6 最大回撤 ≤8% 已是验收门槛) | 通用风控 | 与 §2.8 acceptance 回撤门槛一致;个股层做软告警 |
| **均值回归失效退出** | 持仓逻辑是均值回归但 regime 转趋势(VPIN>0.6 持续)→ 退出 | VPIN 文献 | regime + VPIN 组合 evidence |

### 4.2 补仓(加仓/摊低成本)框架

| 框架 | 规则 | 取舍 / 风险 |
|------|------|-----------|
| **金字塔加仓(顺势)** | 仅在**盈利**且趋势确认时加,加仓量递减(trend-following add) | 海龟/趋势派正统;**安全**:浮盈加仓 |
| **均值回归补仓(逆势摊低)** | 超卖(RSI 低 / Bollinger 下轨 / z-score 极负)+ **资金流企稳**(OFI 转正 / VPIN 回落)才补 | **危险**:摊低成本=接飞刀;必须配硬止损 + 单股 ≤15% 上限,**严禁**无限制 martingale |
| **ATR 网格补仓** | 每跌 1×ATR 补一档,预设总档数与总上限 | 需严格上限,否则违反仓位三连 |
| **Kelly / 分数 Kelly 定量** | `f* = edge/odds`;实务用 **1/4~1/2 Kelly** 防过度下注 | 需可靠胜率/赔率;系统化策略才适用;本项目保守 → 优先 **固定分数(fixed-fractional)** |
| **固定分数 / 固定风险(Van Tharp)** | 每笔风险固定占资本 R%(常 0.5–2%);仓位 = R×Equity / (entry−stop) | **首选**:简单、稳健、与止损联动、不需精确胜率 | 与单次 ≤5 万 / 单股 ≤15% 天然兼容 |

### 4.3 补仓信号合成(建议规则,作 proposal 非指令)

```
补仓候选 = (价格超卖)            # Bollinger 下轨 / RSI<30 / 收益 z-score < -2
        AND (资金流企稳)        # 近似 OFI 由负转正 OR VPIN 从 spike 回落 < 0.3
        AND (无结构性破位)      # ruptures 未报趋势变点 / HMM 非 bear
        AND (基本面/事件无恶化)  # 无 NEWS-/MIROFISH- 负面事件
        AND (仓位余量充足)      # 加后仍满足单股≤15% & 单次≤5万 & 总仓≤70%
   → 产 risk_parameter / evidence proposal(LLM 仅写 proposal_text)
   → fund_manager 倡议 → RiskEngine 14-check → 飞书人工执行
```

> 关键安全锁:补仓**永远**先过仓位三连与 long-only;**严禁** martingale 式无限加倍;每个补仓档位预绑定 ATR 止损;bear regime 下默认禁补仓(只减不加)。

---

## 5. Recommendations(推荐)

### 5.1 知识图谱栈(A 部分)

1. **图存储:NetworkX(内存)+ SQLite(边/节点持久化)起步,LadybugDB(Kùzu fork, MIT)作扩容路线。**
   - 理由:NetworkX 已装、零新服务、十万节点内够用、可审计;图随时可从 provenance + 三元组 ledger 重建。规模或多 agent 并发写超出后再迁 LadybugDB(嵌入式、MIT、列存、性能强)。
   - **不选** Neo4j(GPLv3 + 常驻 JVM)、Memgraph/ArangoDB(非 OSI License)、原 KùzuDB 仓(已 archive)。
2. **GraphRAG 检索层:LightRAG(MIT)为主;nano-graphrag(MIT)作可 hack 备选。**
   - 理由:增量更新友好(契合演化型 KB)、本地 LLM/embedding 友好、索引 token 便宜(契合 P1-7 预算)。nano-graphrag ~1.1k 行、默认 NetworkX 后端、最易塞 provenance 钩子。
   - **不选** MS GraphRAG 作主力(批式社区重算贵且演化不友好);仅"需全局主题摘要的大语料"时回看。
3. **溯源:沿用 P2-2 文件式** `data/rag/` + `provenance.jsonl` 作单一真相源,图为派生索引;**借鉴 Graphiti 双时间(t_valid/t_ingest)+ `SUPERSEDES` 边**建模策略上/下线,实现可查询、可回滚的演化历史。
4. **schema:** 用 §1.4 的 9 节点 / 12 边 frozen Pydantic 模型;FIBO 仅取权益子集对齐,不引全量推理。
5. **构建:** 复用现有 `RagIngester`(白名单 + sanitiser + 注入计数)→ LLM 抽三元组(强制 provenance_ref)→ 第二 agent 反思校验(FinReflectKG 风格)→ 人工 gate + 飞书通知 → 写图。决策性节点严禁 LLM 自动落库。

### 5.2 持仓监控栈(B 部分)

1. **异动检测分四层**(§3.3):z-score/EWMA/Bollinger(必备,已装)→ IsolationForest(已装,多维)→ HMM regime + ruptures/BOCPD(结构,新增 `hmmlearn`+`ruptures`)→ akshare 近似 OFI/VPIN(资金流)。
2. **不上 Autoencoder/LSTM 首期**:黑箱、解释弱、与"可解释 evidence"红线张力、小样本易过拟合。
3. **30s × ~13 标的**纯统计 + IsolationForest 完全实时;HMM 慢频训练盘中 infer;OFI 用 akshare 快照近似并标 degraded 精度等级(fail-closed)。
4. **补仓/卖出 = 规则化 proposal**(§4),首选 **固定分数(Van Tharp)定量 + ATR 跟踪止损**;补仓需"超卖 + 资金流企稳 + 无破位 + 仓位余量"四条件齐备;**严禁** martingale;bear regime 禁补;一切先过 RiskEngine 14-check + 仓位三连 + long-only,LLM 仅写文本,人工飞书执行。

### 5.3 新增依赖清单(全 OSI / 自托管)

| 包 | 用途 | License | 状态 |
|----|------|---------|------|
| `ruptures` | 变点检测(PELT/Window) | MIT-类 | 新增 |
| `hmmlearn` | HMM regime 检测 | BSD-3 | 新增 |
| `river`(可选) | 流式在线异常 | BSD-3 | 可选 |
| `lightrag-hku` | GraphRAG 检索层 | MIT | 新增(主) |
| `nano-graphrag`(可选) | 可 hack GraphRAG 基座 | MIT | 备选 |
| `ladybug`/`kuzu`-fork(扩容期) | 嵌入式图存储 | MIT | 延后 |
| `pyoxigraph`(若走 RDF/FIBO) | SPARQL 三元组库 | Apache-2.0 | 可选 |
| `networkx` / `scikit-learn` / `akshare` / `numpy` / `pandas` | 图 + IsolationForest + 数据 + 统计 | BSD/公有领域 | **已装** |

### 5.4 红线兼容性 checklist

- [x] 全本地 / 嵌入式 / 无 hosted SaaS(NetworkX/Kùzu/LightRAG 本地)
- [x] LLM 仅产 reasoning/evidence/proposal 文本,严禁写决策/RiskCheckSummary 节点
- [x] 溯源沿用文件式 provenance.jsonl,零 Mongo
- [x] 异动/补仓信号 = evidence/proposal,经 RiskEngine 14-check,人工飞书执行
- [x] 数据缺失 fail-closed(不产假信号);基础设施抖动 fail-open
- [x] 新增包全 OSI 许可(MIT/BSD/Apache-2.0)
- [ ] 模块落点待定:KG 构建若服务 agent 检索,需新 `backend/kg/`,审慎设计与 `backend.{agents,data}` 的依赖边界(避免重蹈 evolution 隔离红线)— 留待计划文档 session

---

## 附录:来源(Sources)

知识图谱存储 / GraphRAG:
- [ArcadeDB — Neo4j Alternatives 2026](https://arcadedb.com/blog/neo4j-alternatives-in-2026-a-fair-look-at-the-open-source-options/)
- [Memgraph vs Neo4j (Memgraph)](https://memgraph.com/blog/neo4j-vs-memgraph) · [PuppyGraph](https://www.puppygraph.com/blog/memgraph-vs-neo4j)
- [KùzuDB fork for AI Agents — Vela Partners](https://vela.partners/blog/kuzudb-ai-agent-memory-graph-database)
- [kuzudb-study benchmark](https://github.com/prrao87/kuzudb-study)
- [Microsoft GraphRAG (Research)](https://www.microsoft.com/en-us/research/blog/graphrag-unlocking-llm-discovery-on-narrative-private-data/) · [LICENSE (MIT)](https://github.com/microsoft/graphrag/blob/main/LICENSE)
- [LightRAG (HKUDS, EMNLP 2025)](https://github.com/HKUDS/LightRAG)
- [nano-graphrag](https://github.com/gusye1234/nano-graphrag)
- [Graphiti (getzep, Apache-2.0)](https://github.com/getzep/graphiti)
- [Oxigraph (Apache-2.0)](https://github.com/oxigraph/oxigraph) · [pyoxigraph PyPI](https://pypi.org/project/oxigraph/)
- [Awesome-GraphRAG (DEEP-PolyU)](https://github.com/DEEP-PolyU/Awesome-GraphRAG)
- [Graph RAG: A Survey (ACM TOIS)](https://dl.acm.org/doi/10.1145/3777378)

金融 KG 构建:
- [FinKario (arXiv 2508.00961)](https://arxiv.org/html/2508.00961v1)
- [FinDKG](https://www.researchgate.net/publication/375730347_FinDKG_Dynamic_Knowledge_Graph_with_Large_Language_Models_for_Global_Finance)
- [FinCaKG-Onto (Springer)](https://link.springer.com/article/10.1007/s10489-025-06247-1)
- [FinReflectKG (ACM ICAIF)](https://dl.acm.org/doi/full/10.1145/3768292.3770363)
- [LLM-empowered KG construction: survey (arXiv 2510.20345)](https://arxiv.org/pdf/2510.20345)

异动 / 异动检测:
- [Stock Market Anomaly Detection (Z-Score/IsolationForest/AE)](https://github.com/shubh123a3/Stock-Market-Anomaly-Detection)
- [Isolation Forest + Autoencoder (Medium)](https://medium.com/data-has-better-idea/ai-based-anomaly-detection-integrating-autoencoders-and-isolation-forests-d1cc5314e486)
- [AI-Driven Anomaly Detection in Stock Markets (Springer, 2025)](https://link.springer.com/article/10.1007/s10614-025-11274-8)
- [BOCPD for Financial Time Series (ACM 2025)](https://dl.acm.org/doi/10.1145/3795154.3795291)
- [PELT change-point (ACM 2025)](https://dl.acm.org/doi/10.1145/3773365.3773532)
- [Structural break in intraday volatility (arXiv 2404.11813)](https://arxiv.org/abs/2404.11813)
- [ruptures change-point Python](https://www.insightbig.com/post/using-change-point-detection-to-find-market-shifts-with-python)
- [HMM Regime Detection (QuantInsti)](https://blog.quantinsti.com/market-regime-detection-hidden-markov-model-project-fahim/) · [QuantStart](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)

资金流 / OFI / VPIN:
- [Order Flow Imbalance (Dean Markwick)](https://dm13450.github.io/2022/02/02/Order-Flow-Imbalance.html)
- [OFI topic (EmergentMind)](https://www.emergentmind.com/topics/order-flow-imbalance)
- [OFI on CSI 300 futures (arXiv 2505.17388)](https://arxiv.org/pdf/2505.17388)
- [Forecasting HFT OFI with Hawkes (arXiv 2408.03594)](https://arxiv.org/abs/2408.03594)
- [VPIN (QuestDB)](https://questdb.com/docs/cookbook/sql/finance/vpin/) · [VPIN flow toxicity (Buildix)](https://www.buildix.trade/blog/what-is-vpin-flow-toxicity-crypto-trading)
- [AKShare](https://github.com/akfamily/akshare) · [A-share Level-2 guide](https://medium.com/@wutainfofu/practical-guide-to-a-share-level-2-market-data-api-b41f891c50d8)

仓位 / 止损 / 补仓:
- [Van Tharp Stops & Exits (7 Circles)](https://the7circles.uk/van-tharp-7-stops-and-exits/)
- [Position sizing strategies (Robust Trader)](https://therobusttrader.com/position-size/)
- [Van Tharp percentage sizing (StockWonk)](https://stockwonk.com/the-case-for-van-tharp-style-percentage-based-position-sizing/)
- [Position sizing methods (Zerodha Varsity)](https://zerodha.com/varsity/chapter/position-sizing-active-traders-part-3/)
