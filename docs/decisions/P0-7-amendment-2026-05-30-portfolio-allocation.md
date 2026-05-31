# P0-7 修订 — 2026-05-30 组合层逆波动率配比 + 稳健分批部署封套

> **修订基准**: [P0-7 风险红线 — 仓位 / 熔断 / universe / LLM 不可改](./P0-7-risk-redlines-position-circuit-universe-llm-immutability.md)
> **叠加于**: [P0-7-amendment-2026-05-24 预算自适应仓位](./P0-7-amendment-2026-05-24-budget-adaptive-position.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2.0(单一构造点 + PIT 可复现)
> **修订日期**: 2026-05-30
> **触发**: Owner 指出 Line-1 篮子内**每只 BUY 独立顶格到 15% 单股上限、无组合层配比**(`max_compliant_buy_volume` 取 `min(15%, ¥50k, cash, 70%剩余)`),要求"一次给 ≤5 优质股并**充分考虑持仓配比**",终极目标账户**稳定收益**。AskUserQuestion 锁定 **逆波动率加权(等权兜底)+ 稳健分批部署**。

## 1. 修订前(P0-7 原锁定 + 2026-05-24 budget-adaptive)

- 仓位三连:单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万(`PositionLimitsConfig`)。
- Line-1 每只 BUY 的 `volume = max_compliant_buy_volume(...)`(`backend/services/line1_context_provider.py:197`)= `min(单股15%, 单笔¥50k, 可用现金, 70%总仓剩余)`,本质**按打分排序逐只顶格到 15% 单股上限**,直到撞 70%/现金/5 单。
- **无**组合层资金分配:无按波动率/分散调权、无"今日总共部署多少"的资金封套。RiskEngine 14-check 是唯一组合层守门(**事后硬上限**)。
- 对"稳定收益"而言这偏激进:把每只都顶到最大单股集中度,且无单日择时缓冲。

## 2. 修订后(本 amendment 锁定)

### 2.1 `backend/portfolio_allocation/`(新增,纯 Python,LLM / RiskEngine **上游**,确定性 + PIT)

篮子级资金分配,**确定性纯函数**,在任何 LLM 阶段之前裁定每只目标股数:

- **逆波动率权重**:`w_i = (1/σ_i) / Σ(1/σ_j)`,σ 取自 frame **已有** `closes`(20d,对齐 `backend/screening/factors.py` 的 `volatility_20d`)。低波动股多配、高波动股少配,均衡各只风险贡献。
- **等权兜底**:任一 `σ_i` 为 `None`(历史不足)或 `≤ε`(冻结/一字板)→ 该名退化等权,绝不 `1/σ→inf`。
- **稳健分批部署封套**:`deployable = available_cash × deploy_fraction`(≈0.33),单只目标权重上限 `per_name_target_pct`(≈0.10);目标现金 `= w_i × deployable`,逐只 clamp 到 `min(per_name_target_pct × total_assets, 单股hard_cap × total_assets − 已持有价值, ¥50k)`,残差一遍重分配。
- **incremental**:减去已持有同码价值,只买正差(long-only + T+1,不假设当日回笼)。
- **整手**:目标现金 → 100 股整手(贪心 floor + 余额按权重缺口贪心填,移植 PyPortfolioOpt `greedy_portfolio` 思路,纯 numpy,**不引 cvxpy**);sub-1-lot 跳过。

### 2.2 与单一构造点 + RiskEngine 的关系(关键不变量)

- 配比层**只产出目标股数**,经 provider 喂 builder 的 `proposed_volume`;**绝不构造 `InstructionPlan`**(R0 单一构造点;`grep "InstructionPlan(" ⊆ {model, builder, tests}` 不破)。
- 最终 `volume = min(max_compliant_buy_volume(...), 配比目标手数 × 100)` —— 配比**只压不放**,**永不放宽** 15/70/5万/5单。
- RiskEngine 14-check **仍独立权威**;配比 pre-respect caps 仅为少被拒,**绝不替代** RiskEngine。配比层**不被 `backend/risk/` import**。

### 2.3 计算位置(provider 两层,实施期决策)

- cage 限价**只在 `build_lead_context` 内才拿到**且可能 `Line1QuoteDegrade` → 不能 walk 前定最终手数。
- **walk 前**(`prime_allocation(shortlist_rows)`):纯算每只**目标现金额** map(逆波动率权重 × 部署额,单只封顶);**walk 内**:用 live 限价把现金额换整手,`volume=min(max_compliant, 目标手数×100)`。
- **掉票(REJECT/DEGRADE/HOLD)预算今日不部署** → 保守欠配。稳健分批本就 ≤1/3 现金,可接受;次日重算。**不做 mid-walk 动态再分配**(破确定性/可复现)。

### 2.4 PIT 可复现(R0 §2.0 新红线①)

σ 取自 **PIT-pin** frame 的 `closes`(纯 stdlib `pstdev`),bit-exact;同(candidates + 账户 + frame + policy)→ 同每只目标手数。离线 `replay` 可重建。

### 2.5 配置来源

`config/allocation_policy.yaml`(runtime 不可改 + hot-reload 禁,mirror `backend/budget_policy/policy.py:230-264`):`deploy_fraction` / `per_name_target_pct` / `cash_buffer_pct` / `vol_lookback`。**单股 hard cap 从 `config/risk.yaml` 的 `position_limits.max_single_stock_pct` 读**(单一真相源,**不复制** 0.15)。

## 3. 实施期任务调整(Phase P)

- **P-002** `backend/portfolio_allocation/`(`policy.py`/`volatility.py`/`allocator.py`/`CLAUDE.md`)+ `config/allocation_policy.yaml`。
- **P-003** provider 接线:`prime_allocation` + `build_lead_context` 内 clamp `volume`。
- `redline-check.sh` 加 `[P-002]` 子检:模块 import 隔离 + 配比 ≤ caps。

## 4. 红线清单(本 amendment 之后)

1. 配比层 = 纯 Python 上游,严禁 `import backend.{llm,agents,mirofish}`;**不被 `backend/risk/` import**。
2. 配比**只压不放**:`final volume = min(max_compliant, 配比目标)`;**永不放宽** 15/70/5万/5单。
3. **不构造 `InstructionPlan`**(R0 单一构造点;grep 仍 ⊆ {model, builder, tests})。
4. RiskEngine 14-check 仍**独立权威**,配比非替代。
5. σ 取自 PIT-pin frame,确定性可复现(R0 §2.0 红线①)。
6. 掉票预算今日不部署(保守欠配);**不做 mid-walk 动态再分配**。
7. `allocation_policy.yaml` runtime 不可改 + hot-reload 禁;单股 cap 从 RiskConfig 读(单源,不复制)。
8. P0-7 仓位三连 + 熔断五连 + budget-adaptive 分层(2026-05-24)+ `concentration_exception`(ETF 白名单)**全不变**;本配比层在其之上**更保守**(per-name ~10% < 15%,单日 ~1/3 现金)。

## 5. 修订记录追加

`docs/plan.html` Phase P 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.4 仓位表述补充「+ 组合层逆波动率配比(等权兜底)+ 稳健分批部署封套(per-name ~10% / 单日 ~1/3 现金;配比只压不放,RiskEngine 仍权威)」。
