# Codex 跨模型代码审查报告

**项目**: QuantMind
**审查目标**: Phase A 破坏式清理(commit `4256baa` 主体 + `8b441ae` SSoT 同步)
**审查时间**: 2026-05-11 Asia/Shanghai
**审查轮次**: 3 / 3(2 轮评审 + 1 轮最终复核)
**最终判定**: ✅ **通过(经最终复核)** — 复核 PARTIAL 后追加 1 个 follow-up 修复关闭 race

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数(原 commit) | 69 |
| 变更行数(原 commit) | +731 / -6039 |
| 发现问题总数 | 4(1 P1 + 2 P2 + 1 新 WARNING) |
| 已修复 | 4 |
| 误报排除 | 0 |
| 未解决 | 0(P0-9 余 10 码 deferred 至 C-002,经 codex cycle 3 接受合理) |
| 修复合入 commit | `61c18db`(fix) + `c14c0a5`(plan.html SSoT) |

---

## 各轮次详情

### 第 1 轮 — 初次评审 `codex review --commit 4256baa`

**Codex 判定**: NEEDS_FIXES — 3 个真实问题。

#### 发现的问题

| # | 严重度 | 文件 | 问题描述 | 置信度 | 处理结果 |
|---|--------|------|----------|--------|----------|
| 1 | **P1** | `backend/api/watchlist.py:50`(语义,代码已 GET-only) | A-002+A-004 删了 POST/DELETE `/api/watchlist` 后没有种子路径,fresh Mongo 卷 `WatchlistService.list_stocks()` 返 `[]`,`AnalysisScheduler` 每轮 Fast/Slow 都空跑,排程分析永不启动。 | HIGH | ✅ 已修复 |
| 2 | P2 | `frontend/src/components/trading/AccountBanner.vue:94-99` | banner 依赖 `riskStore.riskStatus.run_mode` 决定 simulation/Feishu tag,但 `Portfolio.vue` 仅加载 portfolio store。运营人员直接进 Portfolio 不经过 Risk 页面时,banner 回退 `false`,即便后端 `FEISHU_INTERACTIVE_ENABLED=true` 也错显成"模拟实盘"。 | HIGH | ✅ 已修复 |
| 3 | P2 | `backend/api/risk.py:153` | 删除 POST `/api/risk/config` 时漏删孤儿 helper `_apply_config_updates`,其参数注解仍引用已删除的 `RiskConfigUpdate` 模型 — `ruff check` 报 F821,`mypy` 同。 | HIGH | ✅ 已修复 |

#### 修复详情

**Issue #1 (P1)**:
- 新增 `backend/main.py:_MANDATORY_ETF_NAMES` + `_seed_watchlist_from_policy()`(`backend/main.py:135-163`)。
- 钩入 `_init_analysis_scheduler` 在 policy load 后、`scheduler.start()` 前。
- 通过 `WatchlistService.add_stock` 幂等 upsert `policy.{fast,slow}.default_codes ∪ policy.overrides`。
- 同时更新 `config/watchlist_policy.yaml`,在 `slow.default_codes` 写入 P0-9 唯一硬锁的 3 个 mandatory ETF(`510300` / `510500` / `159949`)。**其余 10 个 owner-pick 个股(沪主 4 + 深主 3 + 创业板 3)按 `docs/decisions/P0-9 §3` deferred 至 C-002。** 由于 seeder 是 policy 驱动,C-002 落地时无需额外接线。

**Issue #2 (P2)**:
- `frontend/src/views/Portfolio.vue` 导入 `useRiskStore`,在 `onMounted` 内 `Promise.allSettled([store.fetchAll(), riskStore.fetchStatus()])` 并发拉取。注释说明动机。

**Issue #3 (P2)**:
- 删除 `backend/api/risk.py` 中孤儿 `_apply_config_updates`。`_config_to_response` 保留(仍被 `GET /api/risk/config` 使用)。

---

### 第 2 轮 — 修复后评审 `codex exec`

**Codex 判定**: NEEDS_FIXES — 1 新 WARNING + 强约束。

#### 历史问题复核

| # | 问题 | 状态 | 备注 |
|---|------|------|------|
| 1 | watchlist 种子 | NOT_RESOLVED | Seeder 位置正确但仅播 5 码(3 ETF + 2 overrides 中的 300750/601318);P0-9 名义锁 13 码。 |
| 2 | AccountBanner run_mode | RESOLVED | Portfolio.vue 并发 fetch 已生效。备注:`store.account` 与 `riskStore.riskStatus` 间仍有微秒级 race。 |
| 3 | `RiskConfigUpdate` 引用 | RESOLVED | 完全清理。 |

#### 新发现的问题

| # | 严重度 | 文件 | 问题描述 | 置信度 | 处理结果 |
|---|--------|------|----------|--------|----------|
| 4 | WARNING | `backend/main.py:143` | `_seed_watchlist_from_policy` 参数注解 `Any` 但 `backend/main.py` 未 `from typing import Any` — `ruff check` 报 F821。 | HIGH | ✅ 已修复 |

#### 修复详情

**Issue #4 (WARNING)**:
- 添加 `from typing import TYPE_CHECKING` + `if TYPE_CHECKING: from backend.data.watchlist import WatchlistService; from backend.services.watchlist_policy import WatchlistPolicy`。
- 用具体类型替代 `Any`:`_seed_watchlist_from_policy(watchlist_service: WatchlistService, policy: WatchlistPolicy)`。
- 顺手修复 `backend/api/risk.py` 既有的 3 个 ruff 警告(E501 行长 × 2 + UP017 `datetime.timezone.utc` → `datetime.UTC` alias)。

**关于 Issue #1 仍标 NOT_RESOLVED**:
开发者立场是基础设施已就绪、其余 10 码按设计 deferred:
- `docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md §3` 明确只有 3 个 ETF 是硬锁定的,其余 10 码列为**候选池**(如沪主板 7 选 4),最终选择 "用户实施期手工填"。
- `docs/plan.html` 中 C-002 任务负责把最终 4+3+3 owner picks 写入 YAML。
- Phase A 范围是破坏式清理,不含决策 owner picks。
- Seeder 完全 policy-driven,C-002 落地时只需更新 YAML,无需改代码。

---

### 第 3 轮 — 最终复核(只读)

**Codex 判定**: **PARTIAL** — 1 个边角问题再次浮现,无新 P1 回归。

#### 历史问题最终复核

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | watchlist 种子缺口 | **RESOLVED** | Codex 接受 P0-9 §3 引用,认可 10 码 deferred 至 C-002 是合理结构性决定。当前 staged policy 播 5 码(3 ETF + 2 overrides)。 |
| 2 | AccountBanner run_mode | **UNRESOLVED**(再次) | Portfolio.vue 并发 fetch 已加,但 `<AccountBanner v-if="store.account" ...>` 仅以 portfolio 为门槛。`store.account` 先于 `riskStore.fetchStatus` 完成时,banner 会**短暂**显示 fallback "模拟实盘"。 |
| 3 | `RiskConfigUpdate` 引用 | **RESOLVED** | 彻底清理。 |
| 4 | F821 `Any` 注解 | **RESOLVED** | `ruff check --no-cache backend/main.py backend/api/risk.py` 通过。 |

#### 复核中发现的新增严重问题

| # | 严重度 | 文件 | 描述 |
|---|--------|------|------|
| — | — | — | **无** |

#### Follow-up 修复(超出 Phase 6 只读边界,按用户"不通过及时找根因修复"的常驻指令执行)

针对再次 UNRESOLVED 的 Issue #2:
- `frontend/src/views/Portfolio.vue` 中 `<AccountBanner>` 的 `v-if` 从 `store.account` 收紧为 `store.account && riskStore.riskStatus`,消除瞬时 race。

修复后再跑全套门禁:`vue-tsc` ✅ + vitest 80 passed + ruff ✅ + pytest 1039 passed + `scripts/redline-check.sh` 全绿。

---

## 最终验证总结

**复核状态**: EXECUTED
**复核判定**: PARTIAL → 关闭后 PASS(由 follow-up 修复 + 全门禁回归确认)
**修复 commit**: `61c18db`(代码) + `c14c0a5`(SSoT)

### 历史问题最终状态

| # | 原问题 | 严重度 | 最终状态 |
|---|--------|--------|----------|
| 1 | watchlist 种子缺口 | P1 | RESOLVED |
| 2 | AccountBanner run_mode | P2 | RESOLVED(含 race 修复) |
| 3 | `RiskConfigUpdate` 引用 | P2 | RESOLVED |
| 4 | F821 `Any` 注解 | WARNING | RESOLVED |

### 新增严重问题

无。

---

## 审查维度覆盖

| 维度 | 检查项数 | 发现问题 |
|------|----------|----------|
| 正确性与逻辑 | — | 2(watchlist 种子缺口 P1 + AccountBanner race) |
| 安全性 | — | 0 |
| 错误处理 | — | 0 |
| 性能 | — | 0 |
| 代码质量 | — | 2(F821 + 既有 E501/UP017 顺手清) |
| 语言规范 | — | 0 |

---

## 关键结论

1. **codex 评审捕到的 P1 是真问题**:Phase A 的两条任务 A-002 + A-004 一起删了所有 watchlist 写端点,但没有同时落地种子机制。Codex 的强约束 "len(default_codes)==13" 在严格读 SSoT 时也站得住脚 — 开发者立场是 owner-pick 的 10 码归 C-002,Codex 在 cycle 3 接受该 boundary。
2. **F821 是真 bug**:`_seed_watchlist_from_policy` 用 `Any` 但 `backend/main.py` 未 import,会让 ruff/CI 卡红。这种"绿色测试但红色 lint"的 gap 与项目 memory `feedback_codex_findings_real.md` 中记录的 "mocks accept any kwargs" 教训同源 — 测试套件能跑过不代表静态门禁能过。
3. **codex 主动指出的 race condition** 比测试更敏感:`Promise.allSettled` 并发 fetch 不能保证 banner 渲染时序,只有同时 gate 两个 store 才能消除瞬时错渲染。这种 UI 时序问题在 vitest 单测里通常测不出来,codex 的静态分析视角是关键互补。
4. **修复 commit 61c18db 后**:`pytest -q 1039 passed`、`vitest 80 passed`、`ruff check` clean、`scripts/redline-check.sh` 全绿、`vue-tsc` clean、`npm run build` OK。可作为 Phase B 起点。

---

> 本报告由 Claude Code(分析 + 修复)+ Codex CLI(独立审查)协同生成。
> 审查模型:Claude Opus 4.7 (1M context) + Codex CLI 0.130.0
> 原 Phase A commit:`4256baa` / 修复 commit:`61c18db` / SSoT commit:`c14c0a5`
