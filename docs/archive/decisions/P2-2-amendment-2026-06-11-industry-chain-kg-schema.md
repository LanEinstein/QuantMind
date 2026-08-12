# P2-2 修订 — 2026-06-11 产业链知识图谱 schema 子能力(Phase Y / Y-001)

> **修订基准**: [P2-2-amendment-2026-05-24 主动发现 + 本地知识图谱](./P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md)(锁 KG 9 节点/12 边 frozen 集,源 `docs/research/knowledge-graph-and-anomaly-detection.md §1.4`)
> **关联(跨 base 触点)**: [P0-8-amendment-2026-06-01 LLM 主题研究 peer-sourcing](./P0-8-amendment-2026-06-01-llm-theme-research-peer-sourcing.md) §2.10(产业链 KG 作支撑 pin 快照 + choke-point ≈ NetworkX 中心性)/ R0 §3(PIT 可复现)/ R0 §6(运行时数据路径 ban)/ 调研 `docs/research/industry-chain-reverse-deduction-2026-06-01.md §4`(产业链 schema 草图)
> **修订日期**: 2026-06-11(session #72,Phase Y 起手 Y-001)
> **决策人**: owner(2026-06-01 锁定方向①「第一性原理·产业链倒推」+「按计划继续推进」2026-06-11)
> **性质**: 决策边界锁定 + 代码落地(Y-001)。本 amendment 是 Y-001 schema 扩展的前置门(无 amendment 的 frozen 集变动 = 违规)。

## 0. 触发与意图

Phase Y(产业链倒推选股层)第一步 Y-001:在已建 Phase Q 知识图谱(`backend/knowledge_graph/`)内**新增产业链应用子能力**——把「趋势 → 受益板块 → 产业链环节 → 卡脖子环节 → 代表标的」编码成图,并用 NetworkX 中心性把「卡脖子度(criticality / chokepoint_score)」量化成确定性派生特征。Y-001 是 Phase Y 其余任务(Y-002 主题研究 SOP / Y-004 peer-sourcing 接线)的**纯量化、零 LLM、import 隔离**底座:choke-point ≈ pin 的产业链子图上 NetworkX betweenness/PageRank/out-degree(P0-8-amendment-2026-06-01 §2.10)。

## 1. 核心张力(为何需要本 amendment)

Phase Q schema(`backend/knowledge_graph/schema.py`)是 **frozen 的 9 节点/12 边集**,由 P2-2-amendment-2026-05-24 锁定(源 dossier §1.4)。产业链应用需要 frozen 集里**没有**的节点/边类型(`Trend`/`ChainLink`/`Product` 节点;`DRIVES`/`REQUIRES`/`UPSTREAM_OF`/`SUPPLIES_PRODUCT`/`MEMBER_OF` 边)。扩展 frozen 集是决策边界变动,CLAUDE.md §1.5 要求 amendment-first。**本 amendment 只扩 schema 枚举集与端点合法性表,不动任何既有不变量。**

## 2. 决策:frozen 集 9 节点/12 边 → 12 节点/17 边(只增不改)

### 2.1 新增 3 节点类型(既有 `Sector`/`Instrument` 复用,不新建 `Stock`)

| 新节点 `NodeType` | 含义 | 关键 `attrs`(scalar-only) | 来源 |
|---|---|---|---|
| `TREND`(大方向) | 宏观/政策/技术拐点 | `horizon`, `confidence` | 政策/MiroFish 事件(LLM 抽取 evidence + 人工 gate;**本任务只建结构,不接 LLM**) |
| `CHAIN_LINK`(产业链环节) | 上游材料 / 中游 / 下游 | `layer`, `criticality`, `chokepoint_score`, `substitution_difficulty` | ChainKnowledgeGraph 重建底料 + 研报抽取(人工 gate) |
| `PRODUCT`(产品/材料) | 产品/材料小类 | `category` | ChainKnowledgeGraph product/小类(重建) |

> **复用既有类型(不扩 frozen 集)**:dossier §4 的 `Stock` ≡ 既有 `INSTRUMENT`(产业链特有属性 `crowding_pct`/`valuation_pct`/`board` 落 `attrs` 映射);`Sector` ≡ 既有 `SECTOR`。

### 2.2 新增 5 边类型(既有 `BELONGS_TO` 复用)

| 新边 `EdgeType` | from → to | 含义 |
|---|---|---|
| `DRIVES` | `TREND` → `SECTOR` | 大方向驱动受益板块 |
| `REQUIRES` | `SECTOR` → `CHAIN_LINK` | 受益板块倒推必需产业链环节 |
| `UPSTREAM_OF` | `CHAIN_LINK` → `CHAIN_LINK` | 上游环节供应下游(中心性子图底料) |
| `SUPPLIES_PRODUCT` | `INSTRUMENT` → `PRODUCT` | 上市公司供应产品(主营构成弱信号) |
| `MEMBER_OF` | `PRODUCT` → `CHAIN_LINK` | 产品归属产业链环节 |

> **复用 `BELONGS_TO`**(既有 `INSTRUMENT` → `SECTOR`):标的归属板块。

### 2.3 choke-point 量化(确定性,NetworkX-only,无 Neo4j)

新纯模块 `backend/knowledge_graph/centrality.py`:在 `UPSTREAM_OF` 子图(`to_networkx` 派生视图过滤而来)上算 **betweenness(瓶颈)+ out-degree(被多少下游依赖)+ reverse-graph PageRank(作为源的重要性)**,确定性合成 `chokepoint_score ∈ [0,1]`。确定性保证:子图按 **node_id 排序**构建 + betweenness 精确版(`k=None`,无采样)+ 分数 `round` 到固定精度。纯函数,零 IO,零 LLM,import 隔离。choke-point 高 = 高 betweenness/高 out-degree(断供→整链负面);叠加 `(1−crowding_pct)×(1−valuation_pct)` 的「临界但未炒热」打分留待 Y-004(需基本面过滤防价值陷阱,本任务不做)。

### 2.4 冷启动种子:liuhuanyong/ChainKnowledgeGraph 重建(记 provenance,不抄)

`liuhuanyong/ChainKnowledgeGraph` **无 license(NOASSERTION)**(调研 §5.3)。沿用 Q-002「按公开论文重写、不抄 NOASSERTION 仓库代码」纪律:**不下载、不拷贝其 JSON/代码**;而是从公开领域知识(调研 §1.4 半导体卡脖子全景:光刻机/光刻胶/EDA/静电吸盘等)**重建一个小而真实的确定性产业链种子**,`SourceDoc` 节点记录源 repo + license 状态(NOASSERTION)+ 重建说明 + 公开研报源。每个产业链节点带 `provenance_ref`(`{source_doc}#sha256:{artifact_hash}`)+ `DERIVED_FROM` 边(指向 `SourceDoc`)。种子物化纯离线(`scripts/seed_kg.py` 复用),**零网络、零 LLM**。

## 3. 锁定的边界条文(本 amendment 必锁)

- frozen 集 **只增不改**:9→12 节点 / 12→17 边;既有 9 节点/12 边语义、端点合法性表(`EDGE_ENDPOINTS`)既有条目**一字不动**。
- `SUPERSEDES` 端点集**不变**(仍 `STRATEGY → STRATEGY`):产业链节点的 criticality/chokepoint_score 更新走**同 `node_id` append-only 新版本**(store 原生支持的双时态再版,`as_of` 可回放旧值),**非** successor-replacement,**不**扩 `SUPERSEDES` 端点。
- `backend/knowledge_graph/` **import 隔离不变**:严禁 `import backend.{api,broker,risk,llm,agents,mirofish,data}`;产业链子能力(schema/seed/centrality)纯量化、零 LLM、零网络。
- KG **不是运行时决策路径**:产业链图 + 中心性是**离线 pin 快照**支撑;无 pin 快照则该特征权重 = 0(不做 stale 推断,P0-8-amendment-2026-06-01 §2.10);live 选股接线 + 人工 pin 在 Y-004(本任务不接 CandidateSelector)。
- choke-point **NetworkX-only**(betweenness/PageRank/out-degree);**严禁** Neo4j / 托管图库 / 新第三方图依赖(P2-2 §1 不变)。
- 种子**重建不抄**:不引入 ChainKnowledgeGraph 仓库代码/JSON;provenance 记录源与 NOASSERTION 状态;严禁因便利破 license 红线。
- LLM 字段权限不变:产业链节点的 criticality / 卡脖子叙事若来自研报抽取,LLM 只写 `evidence_collection.content`(display-only),**永不**自动写决策字段/节点决策属性;criticality 数值经人工 gate(本任务只建结构 + 留接口,不接 LLM)。

## 4. 不变量(本 amendment 不触碰)

- 安全地基全留:永禁真实下单 / 飞书人工执行 / 127.0.0.1 / RiskEngine 14-check 纯函数 / 单一构造点 / fail-closed。
- KG append-only(SQLite authorizer 物理拒 UPDATE/DELETE)+ 双时态(`t_valid`/`t_ingest`)+ 退役节点留图 + `to_networkx` 派生只读视图(域属性嵌套 `attrs`)**全不变**。
- P0-9 全市场纯量化 universe + 排除四件套 + long-only 不变;产业链 KG **不剪 universe、不否决板块**。
- Tushare SDK-only 运行时数据路径不变;Y-001 种子是**离线重建底料**,不取实时行情。

## 5. 落点(plan.html Y-001)

`backend/knowledge_graph/schema.py`(+3 节点/+5 边/+5 `EDGE_ENDPOINTS` 条目)+ `backend/knowledge_graph/centrality.py`(新,确定性 chokepoint 评分)+ `backend/knowledge_graph/seed/industry_chain.py`(新,重建种子)+ `seed/loader.py`(接产业链种子)+ `tests/knowledge_graph/`(节点/边往返 + 种子 provenance + 中心性确定性 + 双时态再版 + 端点合法性 + import 隔离)+ 对抗测试在 Y-005 收尾。**后续**:Y-002 主题研究 SOP 读 pin 的产业链子图;Y-004 CandidateSelector peer-sourcing + 「临界但未炒热」打分(需 R-001 LiveArtifactRegistry)。
