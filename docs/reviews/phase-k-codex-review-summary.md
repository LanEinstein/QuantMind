# Codex 跨模型代码审查报告 — Phase K(模块 0 marketdata_snapshot)

**项目**: QuantMind
**审查时间**: 2026-05-24
**审查范围**: Phase K 7 个 commit(`0fa4920`..`60b1b0b`,未 push)对 `origin/main` 的全量 delta — `backend/marketdata_snapshot/*`、`backend/data/tushare_client.py`、`backend/data/config.py`、`backend/services/secrets_validator.py`(TUSHARE_TOKEN)、`scripts/replay_signal.py`、`config/data_sources.yaml`
**审查轮次**: 2(cycle 1 `codex review --base origin/main` 全量审查 + cycle 2 `codex review --uncommitted` 修复验证)
**最终判定**: ✅ 通过(经最终复核)

> 本次审查触发自 2026-05-24 owner 协议变更:**但凡有代码编写的任务,commit 之前先跑 codex-review,修复完所有 P0/P1/P2 bug 后再 commit + push**。

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 21(本次审查聚焦 11 个代码文件) |
| 发现问题总数 | 3(1 P1 + 2 P2) |
| 已修复 | 3 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 发现的问题与修复(cycle 1)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | **P1** | `backend/marketdata_snapshot/store.py` `_to_index_row` | `snapshot.model_dump(mode="json")` 会在 `_METADATA_FIELDS` 过滤前先序列化 `raw_payload: bytes`;对非 UTF-8 字节(parquet / gzip / zstd —— 模型经 `encoding`/`compression` 显式允许)抛 `UnicodeDecodeError`,payload 文件已写但 index 行不追加。现有测试仅用 CSV(UTF-8)未暴露。 | **FIXED**:`model_dump(mode="json", exclude={"raw_payload"})`,字节只走 content-addressed payload 文件。新增 `TestBinaryPayload` 用 `b"\x00\x01\x02\xff\xfe..."` 回归。 |
| 2 | P2 | `store.py` `put()` | 仅按 `snapshot_id` 去重;同 `(vendor,endpoint,trade_date)` 用新 id + 默认 `version=1` 的重述会被接受,使 `versions()` 出现两条 v1、`latest()` 取"最后追加"而非"最高版本",版本化 append-only 契约语义模糊。 | **FIXED**:`put()` 新增 `(vendor,endpoint,trade_date,version)` 唯一性校验,重复抛 `SnapshotOverwriteError`(重述须用更大 version)。新增 `TestVersionUniqueness` 回归。 |
| 3 | P2 | `store.py` 读路径 | `get()`/`versions()`/`latest()` 用构造时载入的内存 `_index`,仅 `put()` reload;先构造的长生命周期 reader/Replayer 看不到另一实例/进程后追加的快照,会以 `unknown snapshot_id` 失败。 | **FIXED**:新增 `_reload_index()`(锁内重载,append-only 幂等),`get()`/`versions()` 读前先重载;抽出 `_row_to_snapshot()` 避免 `versions()` 逐行重载。新增 `TestStaleIndex` 回归。 |

## 最终验证(cycle 2,read-only)

`codex review --uncommitted` 复核修复 diff,原文结论:

> "The snapshot store changes address binary payload serialization, stale reader indexes, and duplicate version ambiguity **without introducing a clear regression** in the touched behavior. The relevant marketdata snapshot tests pass locally."

**复核判定**: PASS — 3 个原问题全部 RESOLVED,0 新增严重回归。

## 门禁结果(修复后)

- `tests/marketdata_snapshot/` 85 passed(82 + 3 新回归)。
- 全量套件 3236 passed / 11 skipped(基线 3233 + 3)。
- 模块覆盖率 ≥95%;`ruff` Phase K 触及文件全绿;`scripts/redline-check.sh` 全绿(含 `[K-006]` PIT 子检)。

## 审查维度覆盖

| 维度 | 发现问题 |
|------|----------|
| 正确性与逻辑 | 3(序列化崩溃 / 版本歧义 / 陈旧索引) |
| 安全性 | 0 |
| 错误处理 | 0 |
| 性能 | 0(`versions()` 逐行重载已在修复中顺带消除) |
| 代码质量 | 0 |
| 语言规范 | 0 |

---

> 本报告由 Claude Code(修复)+ Codex CLI 0.133.0(审查)协同生成。审查印证 [[feedback_codex_findings_real]]:全量 pytest + ruff + redline 全绿仍非 commit-safe —— codex cycle 1 在绿测试下抓出 1 个 P1 真实崩溃(二进制 payload),0 dismissed。
