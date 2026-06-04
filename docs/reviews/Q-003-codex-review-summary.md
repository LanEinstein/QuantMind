# Q-003 codex review summary(2026-06-04)

任务:KG 构建(抽取 + 二次校验 + 人工 gate)+ LightRAG 式离线检索。
新文件:`backend/knowledge_graph/{ingest,retrieval}.py` + `tests/knowledge_graph/test_ingest_retrieval.py`。

## 关键设计(红线对齐)

- **LLM 永不写图**:extractor/verifier 为注入式 Protocol(真 LLM client 只在 orchestration 层接线,镜像 W-002);产物只进 append-only PENDING ledger(`data/kg_ingest/pending.jsonl`)。
- **人工 gate**:`decide(store, pid, decision, decided_by)` 必须具名人类;approve 才写图;reject 同样留痕;已决不可重决。
- **provenance 复用**:只读 `data/rag/provenance.jsonl`(X-002 账本);未锚定文档拒绝进入抽取。
- **LightRAG 取形不取库(scope 决策,记录给 owner)**:`lightrag-hku` 的 `insert` 流水线自带**未经 gate 的 LLM 实体抽取**,会绕过人工 gate 把 LLM 放进图写路径(撞红线)→ 不引依赖;其检索形态(dense top-k + 局部一跳图扩展)在自有 store 上原生实现,严格离线只读、零 LLM、注入式 embedder(生产接 Qwen3-Embedding-0.6B,同 exemplar_selector 模式)。实时辩论用途需另加 index 版本 + audit(明确 out of scope)。

## Cycle 1 — `codex review --uncommitted`:2 P1 + 1 P2 全修

| # | 级别 | 问题 | 修复 |
|---|------|------|------|
| 1 | P1 | `propose` 不校验 doc_text 是否真哈希到 content_sha256 → 被编辑的文本可挂旧锚进图(伪锚定) | 抽取前 `sha256(doc_text)` 必须等于账本锚,否则拒 |
| 2 | P1 | `is_anchored` 任意历史行匹配即过 → 被 supersede/reject 的旧内容仍可入图 | 改 latest-wins:仅该 doc 最新行 + 无 `rejection_reason`(`is not None` 语义,cycle-2 追加)+ 哈希匹配 |
| 3 | P2 | 先记 approve 再写图 → 写失败(如非法端点)后提案被永久卡死且可能留孤儿节点 | approve 前 EDGE_ENDPOINTS 预校验 fail-clean;决策记录移到图写成功**之后**(reject 仍即时留痕) |

## Cycle 2 — verify

findings (1)(3) 确认修复;(2) 追加 `is not None` 语义 → 已修。3 个加固测试钉死(伪文本拒/超期锚拒/非法端点 clean-fail 可重试)。

## 门禁

模块测试 32/32;全量 **4780 passed / 90.82%** + ruff + redline ALL PASS。
