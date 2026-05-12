# C-003 — Codex 跨模型审查总结

**任务**: C-003 全 watchlist 30s 快照 (P0-8 §1.1 + P1-2.B §1.2)
**审查时间**: 2026-05-12
**审查轮次**: 2 / 3
**最终判定**: ✅ 通过

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 10 (含 1 新增模块 + 1 新增测试) |
| 变更行数 | +728 / -20 |
| 发现问题总数 | 2 |
| 已修复 | 2 (P1×1, P2×1) |
| 误报排除 | 0 |
| 未解决 | 0 |

## 第 1 轮 (`codex review --uncommitted`)

**Codex 判定**: NEEDS_FIXES

### 发现的问题

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P1 (CRITICAL) | backend/data/market_data.py:336-338 | 多 code akshare 兜底将 `,".join(codes)` 传给单 code 过滤的 `_fetch_stock_akshare`,过滤为 `df["代码"] == code` 永不命中 → adata 故障期 watchlist snapshot 永久空帧 + Redis quote 缺失。 | ✅ FIXED |
| 2 | P2 (WARNING) | backend/data/market_data.py:347-348 | 非空 watchlist 时,adata 返回空 DataFrame 也是 primary 失败,但旧分支直接返回 `[]` 不试 akshare → adata 成功返空时 DataQualityProvider 误判 missing-rate 飙高。 | ✅ FIXED |

### 修复详情

**Issue #1 修复**:
- 新增 `_fetch_stock_list_akshare(codes: list[str])` 模块级 helper,单次调用 `akshare.stock_zh_a_spot_em()` 后通过 `df["代码"].isin(codes)` 做多 code 过滤(语义正确 + 一次网络往返)。
- `get_watchlist_snapshot` 走 akshare 兜底时改调用新 helper,丢弃 broken `_fetch_stock_akshare(",".join(...))` 调用。
- 新增回归测试 `test_adata_exception_falls_back_to_akshare_multi_code`(`tests/test_market_data.py`)断言:noisy 全市场 frame(含 600519/000001/300750)经 akshare 兜底后 *仅* 返回请求的两个 code,source='akshare'。

**Issue #2 修复**:
- 重构 `get_watchlist_snapshot` 控制流:`try/except` 捕获 primary 异常 + `if df is None or df.empty` 也进 fallback 分支,两条 primary 失败路径统一汇入 akshare leg。
- 新增 `primary_exc` 变量 + 仅当 *两条 leg 都异常* 时抛 `DataFetchError`;两条 leg 都返空时返 `[]`(不抛)。
- 新增回归测试 `test_empty_adata_frame_falls_back` + `test_both_legs_empty_returns_empty_no_raise` + `test_both_legs_fail_raises`。

## 第 2 轮 (`codex exec` 增量复核)

**Codex 判定**: ✅ PASS

| # | 历史问题 | 当前状态 | Codex 备注 |
|---|----------|----------|------------|
| 1 | Multi-code akshare fallback | RESOLVED | "`get_watchlist_snapshot()` now calls `_fetch_stock_list_akshare(codes)`, and the helper filters with `df["代码"].isin(codes)`. This fixes the comma-joined single-code filter failure." |
| 2 | Empty primary frame fallback | RESOLVED | "Empty or failed adata results now enter the akshare fallback branch before returning `[]`." |

新增问题:无。

### 第 2 轮历史

- 首次 `codex exec` 上游 1000s 超时(同 SESSION_LOG #5/#7 模式)。
- 立即原 prompt 重试一次,成功返回 PASS;符合 skill 协议(第一次 UNKNOWN 触发 1 次 retry 即可)。

## 本地验证(commit 前必经)

| 检查 | 结果 |
|------|------|
| pytest 全量 | 1464 passed / 11 skipped |
| 覆盖率(非 risk) | 85.96% (≥70% 阈值) |
| 覆盖率(backend/risk) | 97.60% (≥95% 阈值) |
| ruff(改动文件) | All checks passed |
| `scripts/redline-check.sh` | All checks passed |
| frontend `npm run type-check` | OK |
| frontend `vitest --run` | 80 passed |
| frontend `npm run build` | OK |

---

> 本报告由 Claude Code (Opus 4.7 1M) + Codex CLI 协同生成
> 审查模型: Claude Code (修复) + Codex CLI (审查)
