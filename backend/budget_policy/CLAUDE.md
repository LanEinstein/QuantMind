# backend/budget_policy/ — 子任务上下文(Phase L)

> 状态:**todo**(Phase L)。治理:[P0-7-amendment-2026-05-24](../../docs/decisions/P0-7-amendment-2026-05-24-budget-adaptive-position.md)。任务:plan.html L-003 / L-005。

## 职责
**预算自适应门**(纯 Python,LLM/RiskEngine **上游**):用户真实可投资金 → tier → 可负担 universe + 仓位规则。几百元起步把"买得起"推到极端(A 股 100 股整手)。

## 本模块红线
1. 三档:**Micro <~¥2k 仅 ETF** / Small ¥2k-10k 低价股 1 手+ETF / **Normal ≥¥10k P0-7 三连不变**(单股≤15%/总仓≤70%/单次≤5万)。
2. `max_lot_cost = cash × 15%`;无合规整手个股 → **`NO_COMPLIANT_TRADE`(一等 outcome,非 error,非 HOLD 副作用)**。
3. `concentration_exception` **仅 ETF + 白名单**(个股不享有);绝对 1 手上限 + 飞书确认;**RiskEngine 独立再校验**(本模块设标志,非绕过)。
4. tier 阈值 + ETF 白名单 → `config/risk.yaml`,runtime 不可改 + hot-reload 禁用;阈值从 universe 实际 1 手成本中位数校准初值。
5. 纯函数,无 IO,无副作用(对齐 RiskConfig 隔离精神)。

## import 隔离
严禁 `import backend.{llm,agents,mirofish}`。可用:`backend.risk` 类型 + 标准库。

## 接口契约(草案)
- `BudgetTierPolicy.classify(cash) -> BudgetTier` + `affordable(tier, candidate) -> bool | NO_COMPLIANT_TRADE`。
- `BudgetTier` 枚举 frozen。
