# Codex 跨模型代码审查报告 — C-002 watchlist_policy P0-9 重写

**项目**: QuantMind
**审查时间**: 2026-05-12
**审查轮次**: 3 / 3
**最终判定**: ⚠️ 部分通过 — Codex 上游连续两次超时,Cycle 1 的 3 个 finding 已全部 RESOLVED 并配测试,本地 1426 pytest + ruff + redline + frontend 全绿

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 8(含新建 tests/test_seed_watchlist_from_policy.py)|
| 变更行数 | +1330 / -327 |
| 发现问题总数 | 3(1×P1 + 2×P2) |
| 已修复 | 3 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 各轮次详情

### 第 1 轮 (codex review --uncommitted)

**Codex 判定**: NEEDS_FIXES

#### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 置信度 | 处理结果 |
|---|--------|------|----------|--------|----------|
| 1 | P1 | backend/main.py:154 | _seed_watchlist_from_policy 仅 upsert policy 中的 codes,从未 deactivate Mongo 中已被 policy 移除的旧 codes — 轮换后 AnalysisScheduler.list_stocks() 仍返回这些 stale 行,assign_category 把它们默认 fallback 到 slow,导致 policy 之外的 codes 被持续分析 | HIGH | FIXED |
| 2 | P2 | backend/services/watchlist_policy.py:361 | exclusion_rules 只校验"正数",任意写错(如 ipo=31 / amount=1e8)都会通过 — 与 P0-9 §2.1 "runtime-immutable" 矛盾 | HIGH | FIXED |
| 3 | P2 | backend/services/watchlist_policy.py:428 | reserved_cap_release_time 只 regex 校验 HH:MM,任意 well-formed 时间(09:00 / 15:00)都通过 — 与 P0-9 §3.1 "14:30 滑动规则锁定"矛盾 | HIGH | FIXED |

#### 修复详情

**Issue #1 [P1] → FIXED**: `backend/main.py::_seed_watchlist_from_policy` 重写。
- 计算 `canonical = policy.all_watchlist_codes()`(union of fast.default_codes ∪ slow.default_codes ∪ overrides.keys()).
- 调用 `await watchlist_service.list_stocks()` 拿到当前 active 集合,计算 `stale = active_codes - canonical`.
- 对每个 stale code 调用 `watchlist_service.remove_stock(code)` 软删(active=False;保留历史,沿用 P1-2.A 8 项 audit append-only 红线精神).
- 新建 `tests/test_seed_watchlist_from_policy.py` 5 个测试:add-missing / required_etf 名称源 / stale 软删 / 异常行容错(stock_code 缺失/非 str)/ idempotent no-op.

**Issue #2 [P2] → FIXED**: 模块级 `LOCKED_EXCLUSION_RULES` 常量(ipo=30 / sub_new=180 / amount=2e8 / max_unit_price=500.0)。
- `_coerce_exclusion_rules` 每字段对 locked 值 strict equality;报错消息显式援引 P0-9 §2.1.
- max_unit_price 用 float 比较,YAML 整数 500 与浮点 500.0 都接受(int/float 归一化),其他偏移(499.99 / 501)拒绝.
- 测试升级:`test_ipo_threshold_must_equal_30` / `test_sub_new_threshold_must_equal_180` / `test_amount_threshold_must_equal_2e8` / `test_max_unit_price_must_equal_500` / `test_max_unit_price_accepts_int_form` / 旧 `test_ipo_threshold_negative_rejected` 保留作为 fail-closed 路径.

**Issue #3 [P2] → FIXED**: 模块级 `LOCKED_RESERVED_CAP_RELEASE_TIME = "14:30"`。
- `_coerce_cap_allocation` 先走原有 HH:MM regex,再 strict equality 校验.错误消息显式援引 P0-9 §3.1.
- 新增测试 `test_release_time_must_equal_14_30` 覆盖 well-formed 但非 14:30 的拒绝路径.

### 第 2 轮 (codex exec —— 验证修复)

**Codex 判定**: UNKNOWN(`Error: Codex timed out after 1000s` — 上游超时,无 finding 可解析).

### 第 3 轮 (codex exec —— 缩短 prompt 重试)

**Codex 判定**: UNKNOWN(`Error: Codex timed out after 1000s` — 第二次上游超时).

---

## 误报分析

无 — Cycle 1 的 3 个 finding 经评估全部为真实问题:
- P1: P0-9 §1.3 明确 watchlist runtime 不可改 + WatchlistService 已有 `remove_stock` 软删能力,seed 函数原实现少调用一步.
- P2 (×2): P0-9 §2.1 / §3.1 文档明确锁定具体阈值,只校验"格式"而不校验"值"是 lockdown 漏洞.

## 最终验证 (Final Verification)

**复核状态**: SKIPPED
**跳过原因**: codex_unavailable(连续 2 次 UNKNOWN — Codex CLI 上游超时,无有效审查输出)

> 与 SESSION_LOG #5 (Phase B B-001..B-005) 同样的上游超时模式,见 memory `feedback_codex_findings_real`. 本地证据等价于复核:
> - Cycle 1 的 3 个 finding 全部配 unit test,新建 5 测试 + 升级 6 测试覆盖 RESOLVED 路径.
> - pytest 1426 passed (baseline 1381 → +45;含 +20 新 schema 校验测试 + +9 新阈值 lockdown 测试 + +5 seed 路径回归测试 + +11 其他随附测试);coverage 85.93% > 70%.
> - ruff 触动文件全清;`scripts/redline-check.sh` 全绿;frontend type-check + 80 vitest 全过.

## 未解决问题

无.

## 审查维度覆盖(基于 Cycle 1 覆盖)

| 维度 | 检查项数 | 发现问题 |
|------|----------|----------|
| 正确性与逻辑 | - | 1 (P1 rotation drift) |
| 安全性 | - | 0 |
| 错误处理 | - | 2 (P2 lockdown gap × 2) |
| 性能 | - | 0 |
| 代码质量 | - | 0 |
| 语言规范 | - | 0 |

---

## C-002 范围与红线对齐

- ✅ P0-9 §1.1 watchlist 13 codes lock(`LOCKED_TOTAL_CODES + LOCKED_COMPOSITION` 双校验)
- ✅ P0-9 §1.2 mandatory ETF 三件套(510300/510500/159949)必须在 slow.default_codes(boot-time 校验)
- ✅ P0-9 §1.3 runtime-immutable(`update_override` / `save_policy` 彻底删除;新增 `TestRuntimeImmutability` 4 测试守门 `hasattr` 检查 + frozen dataclass 拒绝赋值)
- ✅ P0-9 §2.1 排除规则 4 阈值精确锁定(LOCKED_EXCLUSION_RULES)
- ✅ P0-9 §3.1 cap 分配 4+1=5 锁定 + reserved_cap_release_time = "14:30" 锁定
- ✅ P0-9 §4.1 long-only 锁定(direction_policy.long_only 必须 true / 6 forbidden_sides 必须等于锁定集合 / etf_arbitrage_enabled 必须 false)
- ✅ P0-9 §5 self-check constraints block(若 YAML 含 constraints 节,4 个 self-check 值偏移即拒绝)
- ✅ P1-5 §2 红线 1+2 写端点仅 2 个 — `backend/api/watchlist.py` 全 GET-only,grep `@router\.(post|put|patch|delete)` 仍空
- ✅ P0-10 §2 红线 1 backend/risk 隔离 — 本任务未引入 risk 反向依赖

> 本报告由 Claude Code (Opus 4.7 1M ctx) + Codex CLI 协同生成
> 审查模型: Codex CLI (cycle 1 找 issue) + Claude Code (fix + 测试 + 二三轮验证)
