# Y-001 产业链 KG 子能力 — codex review summary

> 任务:Phase Y / Y-001(产业链知识图谱节点/边 + liuhuanyong 重建种子 + 确定性中心性)。
> 日期:2026-06-11(session #72)。门禁:本地 pytest + ruff + redline 全绿 + 跨模型审查。
> 审查:codex `review --uncommitted` 5 轮(收敛)→ codex 撞 540s/600s 超时(rate-limit)→ 回退 `/code-review high`(7-angle 多 agent)1 轮。修复全部 P0/P1/P2 + 全部 P3。

## codex 轮次(每轮修完再验)

| 轮 | findings | 处置 |
|---|---|---|
| cycle 1 | P2 孤立环节 PageRank teleport → composite 0.2(无供应证据却像 choke point);P3 `SeedReport.source_docs` 折入链种子后少计(图 6 报告 4) | 全修:孤立环节零分;新增 `total_source_docs` 属性 + 日志报总数 |
| cycle 2 | P2 链种子无条件 `add_node` → 可 clobber ingest 已批准的 canonical 节点(`instrument:002371`/`sector:semiconductor`);P3 `report.edges` 只计结构边漏算 provenance 边 | 全修:`_add_node_if_absent` fill-missing;新增 `total_edges`/`total_nodes` 属性(结构+provenance);测试锁 report==实际图 |
| cycle 3 | P2 结构边仍无条件 `add_edge` → 同 `edge_id`(批准修正/既有链边)被静默 supersede(与节点 fill-missing 不对称) | 全修:`_add_edge_if_absent` 对称 fill-missing;测试锁不 clobber 既有边 |
| cycle 4 | P2 betweenness(normalize 的 N)+ PageRank(teleport)在**全图**算 → 加无关孤立环节会扰动连通环节分数(违反 topology-only 契约;实证 composite 漂移) | 全修:全部中心性改在**连通子图** `gc` 上算;测试锁孤立环节不扰连通分 |
| cycle 5 | P3 冷启动 CLI 摘要 `total_source_docs`(含链 2 docs)与 `chain.total_nodes`(也含那 2 docs)重复计数,additive 摘要多算 2 | 全修:CLI 链桶改用 `chain_nodes`(排除 sourcedocs,与 source-docs 桶不交) |
| cycle 6 | (codex 600s 超时 ×2,rate-limit) | 回退 `/code-review high` |

## /code-review high(回退,7-angle × verify)

7 候选,verify 后处置:

**CONFIRMED 修复(3)**
1. **模块 `backend/knowledge_graph/CLAUDE.md` 仍写「9 节点/12 边 frozen」** —— 与 schema.py 新 12/17 矛盾;§1.5 决策边界记录须一致,否则未来 session 误判红线越界回退。→ 更新为 12/17 + amendment 指针 + 产业链子能力说明。
2. **`REQUIRES` 边过宽** —— 种子对**每个**环节(含 下游 `packaging-test`)发 `半导体设备 REQUIRES`;倒推应指向上游必需环节,非下游消费端。→ 跳过 `layer=="下游"`(packaging-test 仍经 `UPSTREAM_OF` 连通)。
3. **`kg_seed_complete` 日志丢失分层计数**(alpha158/360/wq101/gtja191) —— 可观测性回归。→ 恢复显式分层 kwargs。

**REFUTED(4,by-design / documented)**
- *reach 主导让 静电吸盘 排在 光刻机 前*:**设计如此** —— `chokepoint_score` 纯 topology(网络脆弱性/断供级联深度,Ahern 支撑);`criticality` 是独立研报信号,合成留 Y-004(amendment §2.3)。深上游环节级联更长,高分合理。
- *pre-existing 节点 provenance 缺口*:**设计正确** —— 种子不得对非自己创建的节点伪造 DERIVED_FROM(=no-clobber 规则);ingest 自带 provenance。
- *re-run 时 report 过报*:**已文档化**(「fresh cold-start 的计数」)。
- *`_ANY_SOURCE` 隐式扩张*:**预期且正确**(链节点需 DERIVED_FROM)。

## 终态门禁

- `pytest tests/knowledge_graph/` 62 passed;`centrality.py` / `seed/industry_chain.py` 覆盖率 **100%**。
- 全量 `pytest -q --cov=backend --cov-fail-under=70`:**4864 passed / 13 skipped / 90.84%**。
- `ruff check` 全绿;`scripts/redline-check.sh` 全绿;KG import 隔离保持(无 `backend.{api,broker,risk,llm,agents,mirofish,data}`)。
- 安全地基一条未破:KG 非运行时决策路径、零 LLM、NetworkX-only(无 Neo4j)、append-only/双时态不变、`SUPERSEDES` 端点未松动、种子重建不抄 NOASSERTION 仓库代码。
