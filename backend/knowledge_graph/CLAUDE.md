# backend/knowledge_graph/ — 子任务上下文(Phase Q)

> 状态:**done(2026-06-04,session #70:Q-001 `ad85279` / Q-002 `f7574ca` / Q-003 `aba7259` / Q-004 本提交)**。治理:[P2-2-amendment-2026-05-24](../../docs/decisions/P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md)。任务:plan.html Q-001..Q-004。

## 职责
**本地知识图谱**:存策略 + 因子 + 概念 + 板块/标的关系 + 操盘手启发式 + 回测 provenance;供 agent 检索(GraphRAG 式)+ 随策略发现/淘汰进化。

## 本模块红线
1. **本地存储**:SQLite + NetworkX 起步(**LadybugDB 推迟** —— Kùzu 被 Apple 收购归档);存储接口可换,图状态可移植。**严禁** hosted(Neo4j-Aura/etc.)。
2. **append-only + 双时态**(t_valid/t_ingest)+ `SUPERSEDES` 边建模策略晋升/退役;退役节点**留图**保 provenance。
3. 冷启动 seed:qlib Alpha158/360 + GTJA-191/WorldQuant-101(**按公开论文重写**,仓库 NOASSERTION 不抄)+ 操盘启发式。
4. **LightRAG 离线只读**先行;碰实时辩论须快照 retrieval + index 版本 + doc 哈希入 audit;`rag_index_version` 经 `LiveArtifactRegistry`。
5. 构建经实体/关系抽取 + 二次 agent 校验 + **人工/飞书 gate**;provenance 写 `data/rag/provenance.jsonl`。

## import 隔离
严禁 `import backend.{api,broker,risk,llm,agents,mirofish,data}`(防反向调用绕过守门,继承 P2-2)。

## 接口契约(已实现)
- `schema.py`:9 节点(`NodeType`)/ 12 边(`EdgeType`)frozen Pydantic strict + `EDGE_ENDPOINTS` 端点合法性表(写时校验)。
- `store.py`:`SqliteKGStore`(`KnowledgeGraphStore` Protocol 可换引擎)— 双时态版本行(`t_ingest` store 时钟 + `t_valid` 域时间)、`as_of` 回放、`supersede_strategy` append-only 退役、SQLite authorizer **物理拒** UPDATE/DELETE、`to_networkx` 派生只读视图(域属性嵌套 `attrs` 防保留键冲突)。
- `seed/`:冷启动 811 因子(A158 159 + A360 360 程序化;WQ-101/GTJA-191 论文转写 `data/kg_seed/*.json`,**未抄** NOASSERTION 仓库代码)+ 15 启发式;全节点 provenance_ref + DERIVED_FROM;双哈希锚(SourceDoc=原始字节 / factor=canonical 记录);`scripts/seed_kg.py` 离线物化(产物 `data/knowledge_graph/` gitignored)。
- `ingest.py`:`KGIngestPipeline` = 注入式 `TripleExtractor`/`TripleVerifier`(LLM 只在 orchestration 层接线)→ append-only PENDING ledger(`data/kg_ingest/`)→ **具名人工 `decide` 才写图**;`JsonlProvenanceIndex` 只读复用 `data/rag/provenance.jsonl`(latest-wins + rejection 拒锚 + doc_text 必须哈希到锚)。
- `retrieval.py`:`KGRetriever` LightRAG **式**离线只读检索(dense top-k 注入式 `Embedder` + 一跳图扩展);`lightrag-hku` 库未引入(其 insert 流水线含未 gate LLM 抽取,撞人工 gate 红线;实时辩论用途需另加 index 版本 + audit,out of scope)。
- 测试:`tests/knowledge_graph/`(store 12 + seed 8 + ingest/retrieval 13 + module contract 6);模块覆盖率 99%(≥80% 达标);AST import 隔离扫描含自检。
