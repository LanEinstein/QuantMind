# Q-002 codex review summary(2026-06-04)

任务:冷启动 seed(Alpha158/360 程序化生成 + WQ-101/GTJA-191 论文转写 + 操盘启发式)。
新文件:`backend/knowledge_graph/seed/{__init__,qlib_factors,heuristics,loader}.py` +
`data/kg_seed/{wq101,gtja191}.json` + `scripts/seed_kg.py` + `tests/knowledge_graph/test_seed.py`。

## 数据来源(license 红线)

- **WQ-101**:agent 抓取 arXiv:1601.00991 原文 PDF 转写全 101 条(括号配平校验);**未抄**任何 GitHub 仓库代码。
- **GTJA-191**:agent 从公开复现(JoinQuant 数据字典 + BigQuant wiki 双源对照)转写全 191 条,保留报告原文已知笔误(SMEAN/DELAT/COVIANCE/BANCHMARK)以忠实于已发表文本;未抄仓库代码。
- **Alpha158/360**:qlib(MIT)config 结构程序化生成(159 + 360,定义为 qlib 表达式 DSL)。
- **启发式**:6 个 playbook 家族 15 条,自行编码 + attributed_to 出处。
- 合计 **811 因子 + 15 启发式 + 4 SourceDoc**,全部带 provenance_ref + DERIVED_FROM 边。

## Cycle 1 — `codex review --uncommitted`:3 P2 全修

| # | 位置 | 问题 | 修复 |
|---|------|------|------|
| 1 | loader.py 默认路径 | 默认 seed 路径相对 cwd,非 repo root 调用 FileNotFoundError | `_REPO_ROOT = Path(__file__).resolve().parents[3]` 锚定;`cd /tmp` 下 CLI 实测通过 |
| 2 | provenance 哈希 | 只哈希公式文本,metadata(name/category/source)变更不移哈希 → 两个不同 seed 图共享一个锚 | `_records_hash`(canonical `_asdict` + sort_keys)统一全 tier;cycle-2 追加:paper tier 双锚 = SourceDoc 钉原始文件字节哈希 + factor provenance 钉 canonical 记录哈希 |
| 3 | test 注解 | fixture 注解 `object` 撞 strict mypy | 改 `SeedReport` |

## Cycle 2/3 — verify

cycle-2 verify 追加 1 项(paper tier 仍 raw-bytes 哈希)→ 双锚修复 → cycle-3 **COMMIT-SAFE**。

## 门禁

pytest 19/19(含真实 artifact 811≥600 验收)→ 全量 4754 passed / 90.77% + ruff + redline ALL PASS。
零 LLM、零网络(运行时);转写仅发生在开发期(agent 抓论文),产物为静态 JSON 入库。
