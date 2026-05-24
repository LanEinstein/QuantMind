# L-003 Codex 跨模型代码审查报告

**任务**: L-003 — `BudgetTierPolicy` + `NO_COMPLIANT_TRADE`(预算自适应仓位门,P0-7-amendment-2026-05-24)
**审查时间**: 2026-05-24
**审查模型**: Claude Opus 4.7(实现/修复)+ Codex CLI gpt-5.5(独立审查)
**审查轮次**: 1 cycle review + 1 read-only final verification
**最终判定**: ✅ 通过(经最终复核 PASS,2 问题全 RESOLVED,0 新 P1 回归)

---

## 审查范围

`codex review --uncommitted`,6 文件:`backend/budget_policy/policy.py`(纯 Python tier 门)、`__init__.py`、`config/risk.yaml`(新增 budget_tiers)、`tests/budget_policy/test_policy.py`、`scripts/redline-check.sh` 新增 `[L-003]` 子检。

## 发现的问题(2 全修)

| # | 严重度 | 文件:行 | 问题 | 处理 |
|---|--------|---------|------|------|
| 1 | P1 | policy.py `assess_candidate` | Normal tier(cash≥¥10k)白名单 ETF 1-lot 超 15% 但 ≤cash 时被判 `AFFORDABLE_WITH_EXCEPTION`,违反"Normal 档 P0-7 15% 原样不变"——正常账户不应有 concentration_exception。 | **FIXED** |
| 2 | P2 | policy.py `assess_candidate` | lot_cost 为 `NaN`(上游缺价)时 `<=0` 与 `>cash` 均 False,白名单 ETF 落入 exception 分支 → 以未知成本可成交,违反 fail-closed。 | **FIXED** |

### 修复详解

1. **P1 Normal 档不给例外**:exception 分支加 tier 守门 `whitelisted and tier in {MICRO, SMALL}`;Normal 档(≥¥10k)白名单 ETF 超 15% → `EXCLUDED_CONCENTRATION`(P0-7 15% 严格不变)。concentration_exception 仅作小预算(Micro/Small)accommodation,正常账户永不触发。回归 `test_whitelisted_etf_no_exception_in_normal`。
2. **P2 非有限 fail-closed**:`assess_candidate` 在任何 affordability/exception 分支**之前**加 `not math.isfinite(lot_cost) or lot_cost<=0 or lot_cost>cash → UNAFFORDABLE`,并加非有限/非正 `available_cash → UNAFFORDABLE` 守门。回归 `test_nan_lot_cost_fails_closed` / `test_inf_lot_cost_fails_closed` / `test_nonfinite_or_nonpositive_cash_fails_closed`。

## 最终验证(read-only 复核)

`codex exec -s read-only`:**PASS**,2 问题全 **RESOLVED**,NEW P1 regressions **NONE**。
(注:首次后台复核因 codex CLI stdin 死锁挂起,kill 后以 `</dev/null` 前台重跑得 PASS;不影响结论。)

## 门禁

- pytest 全量 **3309 passed / 11 skipped**(budget_policy 32 例:classify/Micro/exception/Normal/aggregate/loader/immutability)。
- ruff:`backend/budget_policy/` + `tests/budget_policy/` 全绿。
- `scripts/redline-check.sh`:全绿,新 `[L-003]`(budget_tiers 段存在 + 15% pct 不在 budget_tiers 内重复 = 单源 position_limits)。

## 红线确认

- **三档分层**:Micro<¥2k 仅白名单 ETF / Small ¥2k-10k 低价股 1 手+ETF(可 exception)/ Normal≥¥10k P0-7 三连不变。
- **NO_COMPLIANT_TRADE 一等公民**:无合规可成交 → `BudgetAssessment.outcome == "NO_COMPLIANT_TRADE"`,非 error、非 HOLD 副作用。
- **concentration_exception 仅 ETF+白名单 + Micro/Small**:个股永不享有;绝对 1 手上限 + `requires_feishu_confirm`;本模块只设标志,RiskEngine 独立再校验(L-004)。
- **15% / 100-lot 单源**:从 `position_limits` 读,budget_tiers 不重复(redline 守门)。
- **runtime 不可改**:`BudgetTierConfig` frozen + 一次性 load;阈值改走 git diff + amendment + 重启。
- **import 隔离**:纯 stdlib + yaml,无 `backend.{llm,agents,mirofish}`(redline `[L-002]` 覆盖 budget_policy)。

---

> 本报告由 Claude Code(Opus 4.7)+ Codex CLI(gpt-5.5)协同生成。
