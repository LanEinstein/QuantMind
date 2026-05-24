# backend/budget_policy/ — 子任务上下文(Phase L)

> 状态:**done**(L-003 policy / L-005 calibration+模块契约)。治理:[P0-7-amendment-2026-05-24](../../docs/decisions/P0-7-amendment-2026-05-24-budget-adaptive-position.md)。任务:plan.html L-003 / L-005。

## 职责
**预算自适应门**(纯 Python,LLM/RiskEngine **上游**):用户真实可投资金 → tier → 可负担 universe + 仓位规则。几百元起步把"买得起"推到极端(A 股 100 股整手),无合规整手 → `NO_COMPLIANT_TRADE`(一等 outcome)。

## 模块结构(已实现)
| 文件 | 内容 |
|------|------|
| `policy.py` | `BudgetTier`(MICRO/SMALL/NORMAL)+ `BudgetTierPolicy.classify_tier` / `assess_candidate` / `assess` + `AffordabilityOutcome` + `NO_COMPLIANT_TRADE` + `load_budget_tier_config`(读 risk.yaml budget_tiers,15%/lot 取自 position_limits 单源)。 |
| `calibration.py` | `calibrate_tiers(per_lot_costs, max_single_stock_pct)` → `TierCalibration`:从 universe 1 手成本分布派生 Micro/Small 阈值(p10/median ÷ 15%);离线助手,不改 config、不入运行路径。 |

## 本模块红线
1. 三档:**Micro <~¥2k 仅白名单 ETF** / Small ¥2k-10k 低价股 1 手+ETF / **Normal ≥¥10k P0-7 三连不变**(单股≤15%/总仓≤70%/单次≤5万)。
2. `max_lot_cost = cash × 15%`;无合规整手 → **`NO_COMPLIANT_TRADE`(一等 outcome,非 error,非 HOLD 副作用)**。
3. `concentration_exception` **仅 ETF + 白名单 + Micro/Small**(个股永不享有;Normal 严格 15% 不给例外);`requires_feishu_confirm`;**RiskEngine 独立再校验**(本模块设标志,非绕过;见 L-004 `_grant_concentration_exception`)。
4. 非有限 / 非正 `lot_cost` 或 `cash` → fail-closed `UNAFFORDABLE`(缺价不可成交)。
5. tier 阈值 + ETF 白名单 → `config/risk.yaml`(budget_tiers),runtime 不可改 + hot-reload 禁用;15%/100-lot **不在 budget_tiers 重复**(单源 position_limits,redline `[L-003]` 守门)。阈值初值从 `calibrate_tiers` 派生(非硬编码)。
6. 纯函数,无 IO(除一次性 config load),无副作用(对齐 RiskConfig 隔离精神)。

## import 隔离
**严禁** `import backend.{llm,agents,mirofish}`(redline-check `[L-002]` + `tests/budget_policy/test_module_contract.py` AST 守门)。可用:标准库 + yaml + `backend.risk` 类型(目前未用,直接 reads risk.yaml)。

## 测试
`tests/budget_policy/`:policy(32:classify/Micro/exception/Normal/aggregate/loader/immutability)+ calibration(11)+ 模块契约/隔离。覆盖率 ≥80%。
