# L-001 Codex 跨模型代码审查报告

**任务**: L-001 — `universe_policy` 改写 + 重命名(13 标的固定池 → 全市场 universe 规则集)
**审查时间**: 2026-05-24
**审查模型**: Claude Opus 4.7(实现/修复)+ Codex CLI gpt-5.5(独立审查)
**审查轮次**: 1 cycle review + 1 read-only final verification
**最终判定**: ✅ 通过(经最终复核 PASS)

---

## 审查范围

`codex review --uncommitted` 全量审查 19 文件 / +817 −929(含旧 `watchlist_policy.{py,yaml}` + 旧测试删除、`docs/plan.html` 状态翻转)。核心代码:`backend/services/universe_policy.py`(新)、`config/universe_policy.yaml`(新)、消费方(`main.py` / `api/watchlist.py` / `analysis_scheduler.py` / `instruction_plan_builder.py` / `phase5b_exit_check.py` / `config_service.py`)、`scripts/redline-check.sh` 新增 `[L-001]` 子检、测试改写。

## 发现的问题

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P1 | `config/universe_policy.yaml:50-57` + `backend/main.py` | shipped v3 policy `default_codes`/`overrides` 全空 → `all_watchlist_codes()` 为空 → `_seed_watchlist_from_policy` 把 Mongo 里**所有** active 行当 stale **软删**,而 `AnalysisScheduler` 仍从该 collection 取候选(`backend/screening` 尚是 TODO)→ 默认部署 fast/slow cron 全 `no_matched_codes` skip。 | **部分修复 + 部分文档化驳回**(见下) |

### 问题 1 处理详解

Codex 的 P1 含两层,分别处置:

1. **可立即修复(已修)— 破坏性软删**:全市场模式下 `_seed_watchlist_from_policy` 不应在 pin 集为空时把既有行全部软删。新增 guard:`canonical` 为空时记 `watchlist_seed_skipped_full_market` 并 early-return,**既不 seed 固定列表、也不软删既有行**。新增回归测试 `test_seed_skips_when_no_pinned_codes`(断言空 pin 集下 `add_stock`/`remove_stock` 均未被 await);非空对账路径(`test_seed_soft_deletes_stale_codes` 等)不变。

2. **文档化驳回 — fresh 部署调度器空喂**:fresh 部署在 `backend/screening`(L-002)+ screener→scheduler 接线(L-002/M)落地前没有分析 universe,这是 **P0-9-amendment-2026-05-24 有意排定的过渡态**,不是 L-001 的缺陷。Codex 建议的"保留 seedable codes"会**重新引入固定标的列表,直接违反本任务的治理 amendment**,故不采纳。该集成缺口由 L-002/M 闭合,已在 SSoT L-002 notes + 本报告记录为已知后续依赖。

## 最终验证(read-only 复核)

`codex exec -s read-only` 复核 guard 修复:**PASS**。

> "The guard in backend/main.py returns before any add_stock, list_stocks, or remove_stock call when all_watchlist_codes() is empty, so the destructive soft-delete path is blocked for the empty-pin v3 policy case. The non-empty reconciliation path is still intact... No new P1 regression introduced."

| # | 原问题 | 当前状态 | 备注 |
|---|--------|----------|------|
| 1 | 空 pin 集破坏性软删 | RESOLVED | guard early-return,6 测试通过 |
| 1b | fresh 部署调度器空喂 | 文档化驳回 | L-002/M 排定闭合,修复=违反 amendment |

新增严重回归:**无**。

## 门禁

- pytest 全量 **3233 passed / 11 skipped**(+ guard 回归测试,见下方 commit)。
- ruff:本任务触动文件全绿。
- `scripts/redline-check.sh`:全绿,含新 `[L-001]`(universe_policy 规则集存在 + config/ 无 13-code lock)+ 既有 `[K-006]` 等不变。

## 红线确认(本审查重点)

- 科创 688 / 北交 8 / ST / 可转债 **永禁**:`FORBIDDEN_BOARDS` 锁定 + loader 拒绝 `forbidden_boards` 漂移 + `classify_board` 上游 `ForbiddenCodeError` 不变 + redline `[L-001]` 子检。
- `board_whitelist` 不可越过 4 板块(loader `== BOARD_WHITELIST` 强约束)。
- 仅 GET API:`api/watchlist.py` 仍仅 GET,序列化器换 `universe` 块,路由路径不变。
- runtime 不可改:全 frozen dataclass,无 mutation 路径(`save_policy`/`update_override` 仍缺席,测试锁)。
- `board_not_whitelisted` 语义改动:membership-in-13 → board∉whitelist;builder tests 已覆盖(narrowed-whitelist 触发 + 白名单 board 放行)。

---

> 本报告由 Claude Code(Opus 4.7)+ Codex CLI(gpt-5.5)协同生成。
