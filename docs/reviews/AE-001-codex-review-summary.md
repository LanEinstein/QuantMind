# Codex 跨模型代码审查报告 — AE-001 Tushare bulk 历史 PIT 摄取

**项目**: QuantMind
**任务**: AE-001(P1-DATA bulk 历史 PIT 摄取 + 幸存者无偏 universe)
**审查时间**: 2026-06-14
**审查轮次**: 2 / 3(cycle1 `codex review --uncommitted` → 修复 → cycle2 `codex exec` 复核)
**最终判定**: ✅ 通过(cycle2 verdict = PASS,3 个历史问题全 RESOLVED,无 P1/P2 回归)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件 | `backend/data/historical_ingest/`(7 文件)+ `backend/data/tushare_client.py`(`trade_cal`)+ `backend/data/database.py`(`save_daily_frame`/`_iso_date`)+ `scripts/ingest_historical_pit.py` + 测试 |
| 发现问题总数 | 3(1×P1 + 2×P2) |
| 已修复 | 3 |
| 误报排除 | 0 |
| 未解决(P3,范围外) | 2 |

## 第 1 轮发现 + 修复

### [P1] `kline_daily` 行键与既有读者不一致 — `database.py`
`save_daily_frame` 原以 Tushare `ts_code`(`600519.SH`)+ 紧凑日期(`20180102`)为键写
`kline_daily`,但既有读者(`MarketMetaProvider.get_prev_close`、`main.py` 当日归因 `{code, date_iso}`)
按裸 6 位 `code` + ISO `date` 查询 → 写入的行永远查不到,kline 路径形同空表。
**修复**:`ts_code.split(".")[0]` 归一化为裸 `code`(同 `screener.py:291`)+ 新 `_iso_date()`
把紧凑日期转 ISO `YYYY-MM-DD`;原 `ts_code` 保留在文档内;upsert 键改 `{code, date_iso}`。
测试断言键形(`tests/test_database.py`)。→ **RESOLVED**

### [P2] 跳过既有快照时不回填派生存储 — `job.py`
daily 快照已存在则在写派生存储(kline/coverage)前提前 return,导致"先 snapshot-only 跑、
后开 `--with-kline`/`--with-coverage` 重跑"永远不补派生件,违背"可续传"承诺。
**修复**:skip 路径对 daily 调 `_backfill_secondary()` —— 读**已校验**的快照原始字节
(不重新抓取)→ 重入 `_write_secondary()`;coverage 写前加 `get()` 存在性闸,append-only
manifest 不重复。→ **RESOLVED**

### [P2] 空 `adj_factor` 被当成功空快照存储 — `job.py`
原仅 `daily` 空帧 fail-closed;空 `adj_factor`(as-of 复权重建的必需 PIT pin)被存为成功空快照,
重跑又跳过 → 永久缺口。
**修复**:`_REQUIRE_NON_EMPTY = frozenset(DEFAULT_ENDPOINTS)`(全四个全市场端点),交易日空帧
一律 fail-closed 不存。→ **RESOLVED**

## 第 2 轮复核(cycle2)

| 历史问题 | 状态 |
|---|---|
| P1 kline_daily 键形 | RESOLVED |
| P2 跳过不回填 | RESOLVED |
| P2 空 adj_factor | RESOLVED |

**Verdict: PASS** — 无 P1/P2 回归。

## 未解决问题(P3,范围外)

| # | 问题 | 处理 |
|---|------|------|
| 1 | `SnapshotOverwriteError` 竞态路径返回 skipped 不做 daily 二次回填(仅并发进程间) | 文档化:本 job 为单一顺序 runner;后续顺序重跑自动修复 |
| 2 | coverage 去重 `get()`→`put()` 非原子(仅并发重跑会重复 append) | 同上;已在 `job.py` 类文档注明禁止对同一 snapshot root 并发运行 |

两 P3 均为并发硬化项;摄取按设计 = owner-gated **单次顺序**离线批跑(非实时/非并发),已在 `HistoricalIngestJob`
docstring 显式注明,顺序重跑可自愈,故非阻塞,不在 AE-001 范围内修复。

---

> 本报告由 Claude Code(修复)+ Codex CLI(审查)协同生成。
