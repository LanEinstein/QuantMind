# backend/knowledge_graph/ — 子任务上下文(Phase Q)

> 状态:**todo**(Phase Q)。治理:[P2-2-amendment-2026-05-24](../../docs/decisions/P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md)。任务:plan.html Q-001..Q-004。

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

## 接口契约(草案)
- `KnowledgeGraph.add_node/add_edge/query`(双时态)+ `retrieval`(LightRAG 离线)。
- 9 节点 / 12 边 schema(见调研 dossier `docs/research/knowledge-graph-and-anomaly-detection.md`)。
