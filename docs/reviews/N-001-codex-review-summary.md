# N-001 Codex 跨模型代码审查报告

**任务**: N-001 — Line-2 持仓监控异动检测(`backend/monitoring/anomaly.py`)
**审查时间**: 2026-05-25
**审查轮次**: 1 cycle + read-only final verification
**最终判定**: ✅ 通过(经最终复核)
**审查范围**: `backend/monitoring/anomaly.py` + `tests/monitoring/test_anomaly.py`(uncommitted）

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 2 |
| 发现问题总数 | 2(P2 / MEDIUM × 2;0 P0/P1) |
| 已修复 | 2 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 第 1 轮 — `codex review --uncommitted`

**Codex 判定**: NEEDS_FIXES(2 × P2,correctness)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | anomaly.py `_parse` | held 码同时出现 valid + malformed 两行时,malformed 行在计数前被 `_parse_line` 丢弃 → valid 副本当唯一被扫描;但离线 replay 的 `CsvRowParser` 按 row_key 解析到 **last**(malformed)行 → manifest hash 与 replay 漂移(应 fail-closed 而非静默扫描)。 | ✅ FIXED |
| 2 | P2 | anomaly.py `AnomalyConfig.min_bars` | `min_bars = ewma_span + 2` 全局门:当 `ewma_span > window`(如 60 vs 20)时,25-bar 的价格 spike 被判 `insufficient_history`,即便价格 z-score / 布林已够历史。 | ✅ FIXED |

### 修复详情

1. **malformed 重复(P2)**:`_parse` 改为**先**统计每个 held 码在所有数据行的**原始出现次数**(在 parse-None 过滤之前),`count > 1` 标记 malformed 并丢弃**所有**副本(fail-closed)。这样 valid+malformed 重复不再扫描 valid 副本,与 replay 的 row_key→last 解析一致,杜绝 manifest hash 漂移。回归测试 `test_valid_plus_malformed_duplicate_marked_malformed`。
2. **EWMA 历史门(P2)**:`min_bars` 改为 `min(window, ewma_span + 2)` —— 取两个最便宜检测器(布林=window / EWMA=span+2)的下限,行不再因 EWMA 历史不足被全局跳过;各检测器自行 self-gate(历史不足返 `None`)。回归测试 `test_ewma_span_larger_than_window_does_not_block_other_detectors`。

## 最终验证(read-only closure check)

**复核状态**: EXECUTED
**复核判定**: **PASS**

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | malformed 重复行 | RESOLVED | `_parse` raw_counts 先计数,count>1 全丢弃 |
| 2 | EWMA 历史门 | RESOLVED | `min_bars = min(window, ewma_span+2)` |

**复核中新增 P1 严重回归**: 无。
**验证运行**: `pytest tests/monitoring/test_anomaly.py` → 26 passed。

## 门禁

- pytest:`tests/monitoring/test_anomaly.py` 26 passed,模块覆盖率 95%(≥80%)。
- ruff:全绿。
- import 隔离:anomaly.py 仅 import `backend.marketdata_snapshot` + 标准库 + structlog;无 `backend.{llm,agents,mirofish}`(Line-2 纯量化红线)。

> 本报告由 Claude Code + Codex CLI 协同生成(Claude 修复 / Codex 审查)。
