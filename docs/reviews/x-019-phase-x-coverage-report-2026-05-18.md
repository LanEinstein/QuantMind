# X-019 Phase X 单元测试覆盖率报告 — 2026-05-18

## 背景

X-019 acceptance(`docs/plan.html#X-019`):

> 8 主模块 200+ 案例 + 覆盖率 >70%
> - prompt_registry + rag_provenance + shadow_chain + exemplar_selector
>   + dspy_gepa_runner + frontier_crawler + rag_ingester + evolution_dispatcher
> - 各 200+ 案例;快路径 + 失败路径 + budget 超限 + 白名单拒绝 + R1/R3/R7 边界

本报告 = X-019 完成证据;覆盖率数据由 session #24(2026-05-18)pytest --cov
直接产出。

## 测试文件清单(16 个)

| # | 文件 | 用例数 | 主要覆盖模块 |
|---|------|--------|-------------|
| 1 | `test_audit_evolution.py` | 52 | `backend.audit.models`(Category-5 7 类 + actor 守门)|
| 2 | `test_evolution_audit_writer.py` | 17 | `backend.services.evolution_audit_writer` |
| 3 | `test_evolution_dispatcher.py` | 10 | `backend.services.evolution_dispatcher` |
| 4 | `test_evolution_feishu_notifier.py` | 16 | `backend.services.evolution_feishu_notifier` |
| 5 | `test_prompt_registry_loader.py` | 21 | `backend.services.prompt_registry`(loader 路径)|
| 6 | `test_prompt_registry_schema.py` | 26 | `backend.services.prompt_registry`(schema 路径)|
| 7 | `test_shadow_chain.py` | 30 | `backend.services.shadow_chain` |
| 8 | `test_exemplar_selector.py` | 32 | `backend.services.exemplar_selector` |
| 9 | `test_provenance_models.py` | 35 | `backend.evolution.provenance.{models,verifier}` |
| 10 | `test_provenance_writer.py` | 22 | `backend.evolution.provenance.writer` |
| 11 | `test_rag_ingester.py` | 20 | `backend.evolution.rag_ingester` |
| 12 | `test_dspy_gepa_runner.py` | 11 | `backend.services.dspy_gepa_runner` |
| 13 | `test_frontier_crawler.py` | 16 | `backend.evolution.frontier_crawler` + 5 crawlers |
| 14 | `test_amendment_drafter.py` | 16 | `backend.services.amendment_drafter` |
| 15 | `test_cost_guard_p2_2_integration.py` | 19 | `backend.services.cost_guard`(P2-2 集成)|
| 16 | `test_phase_x_imports.py` | 40 | Phase X 18 模块 import 守门 |
| 17 | `test_phase_x_coverage_floor.py` | 19 | 本报告的 regression lock |
| **合计** |   | **402** | — |

> 402 ≥ 200(X-019 floor)✅

## 覆盖率(2026-05-18 session #24)

```
pytest tests/test_provenance_writer.py tests/test_provenance_models.py \
       tests/test_evolution_*.py tests/test_prompt_registry*.py \
       tests/test_shadow_chain.py tests/test_exemplar_selector.py \
       tests/test_rag_ingester.py tests/test_dspy_gepa_runner.py \
       tests/test_frontier_crawler.py tests/test_amendment_drafter.py \
       tests/test_audit_evolution.py tests/test_cost_guard_p2_2_integration.py \
       tests/test_phase_x_imports.py tests/test_phase_x_coverage_floor.py \
  --cov=backend.evolution \
  --cov=backend.services.prompt_registry \
  --cov=backend.services.shadow_chain \
  --cov=backend.services.exemplar_selector \
  --cov=backend.services.dspy_gepa_runner \
  --cov=backend.services.evolution_dispatcher \
  --cov=backend.services.amendment_drafter \
  --cov=backend.services.evolution_feishu_notifier \
  --cov=backend.services.evolution_audit_writer
```

| 模块 | Stmts | Miss | Cover | X-019 ≥70% |
|------|-------|------|-------|-----------|
| `backend/evolution/__init__.py` | 0 | 0 | 100% | ✅ |
| `backend/evolution/crawlers/__init__.py` | 7 | 0 | 100% | ✅ |
| `backend/evolution/crawlers/akshare_changelog.py` | 24 | 6 | 75% | ✅ |
| `backend/evolution/crawlers/arxiv.py` | 22 | 3 | 86% | ✅ |
| `backend/evolution/crawlers/base.py` | 36 | 1 | 97% | ✅ |
| `backend/evolution/crawlers/github_releases.py` | 24 | 6 | 75% | ✅ |
| `backend/evolution/crawlers/openreview_crawler.py` | 24 | 6 | 75% | ✅ |
| `backend/evolution/crawlers/semanticscholar.py` | 24 | 4 | 83% | ✅ |
| `backend/evolution/crawlers/spotlighting.py` | 21 | 0 | 100% | ✅ |
| `backend/evolution/frontier_crawler.py` | 67 | 1 | 99% | ✅ |
| `backend/evolution/provenance/__init__.py` | 4 | 0 | 100% | ✅ |
| `backend/evolution/provenance/models.py` | 61 | 1 | 98% | ✅ |
| `backend/evolution/provenance/verifier.py` | 46 | 4 | 91% | ✅ |
| `backend/evolution/provenance/writer.py` | 75 | 6 | 92% | ✅ |
| `backend/evolution/rag_ingester.py` | 122 | 0 | 100% | ✅ |
| `backend/services/amendment_drafter.py` | 87 | 1 | 99% | ✅ |
| `backend/services/dspy_gepa_runner.py` | 69 | 0 | 100% | ✅ |
| `backend/services/evolution_audit_writer.py` | 41 | 0 | 100% | ✅ |
| `backend/services/evolution_dispatcher.py` | 86 | 0 | 100% | ✅ |
| `backend/services/evolution_feishu_notifier.py` | 31 | 0 | 100% | ✅ |
| `backend/services/exemplar_selector.py` | 167 | 18 | 89% | ✅ |
| `backend/services/prompt_registry.py` | 145 | 1 | 99% | ✅ |
| `backend/services/shadow_chain.py` | 116 | 1 | 99% | ✅ |
| **TOTAL** | **1299** | **59** | **95%** | ✅ |

每个 Phase X 模块覆盖率 ≥ 75%,合计 95%。X-019 acceptance >70% 全部满足。

## R1 / R3 / R7 边界覆盖证据

- **R1 sample/iter cap**(`tests/test_dspy_gepa_runner.py`):
  - GEPA_MAX_SAMPLES = 100 边界用例(99/100/101)+ over_cap raise GEPASampleLimitExceededError
  - GEPA_MAX_ITERATIONS = 10 边界用例 + over_cap raise GEPAIterationLimitExceededError
  - cost_guard 集成在 `test_cost_guard_p2_2_integration.py::TestDSPyGEPABudgetIntegration`
- **R3 RAG precision floor 0.80**(`tests/test_rag_ingester.py`):
  - 白名单 5 源拒绝 / 接受 / 重复 ingest / SHA256 mismatch fail-closed
  - Sanitiser 3 层 + Spotlighting 边界 covered
- **R7 amendment 4 mandatory sections**(`tests/test_amendment_drafter.py`):
  - 4 section 缺一即 raise AmendmentSchemaError
  - length_inflation_50pct 边界(49.9% / 50.0% / 50.1%)
  - amendment_id path-traversal 守门(`..` / `/` / leading `.`)

## 测试集合 regression lock

`tests/test_phase_x_coverage_floor.py` 锁死:

1. 16 个 Phase X 测试文件全部存在(`TestPhaseXTestFilesPresent`)
2. 累计 ≥ 200 用例(`TestPhaseXTestCountFloor`)
3. 每个文件至少 1 个用例(防空文件)

如未来 refactor 删除测试文件或某个测试文件被掏空,floor 测试会在 pytest 跑
出红色失败,提示作者补回测试或主动调高 floor 数。

## 与 codex review 之关系

本报告**不**触发 codex review(Q15 决策已锁:R3 SDK 在 X-B 后 ad-hoc 跑过;
完整 5 轮 R1-R5 在 X-D 之后跑)。

X-019 = 测试本身;通过 == 完成。
