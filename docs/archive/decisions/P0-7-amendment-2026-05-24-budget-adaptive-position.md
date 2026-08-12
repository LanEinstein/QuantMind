# P0-7 修订 — 2026-05-24 预算自适应仓位 + ETF concentration_exception

> **修订基准**: [P0-7 风险红线 — 仓位 / 熔断 / universe / LLM 不可改](./P0-7-risk-redlines-position-circuit-universe-llm-immutability.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 2 项
> **修订日期**: 2026-05-24
> **触发**: Owner 锁定可投预算「≤10 万,初期可能仅几百元,后期再加」。Codex 2 轮红队指出:几百元 × A 股 100 股整手 × P0-7 百分比仓位三连**互不相容**(¥500 的 15% = ¥75 < 任意 1 手),且「整手允许超 15%」的笼统放行**危险**(¥500 买 1 手 ¥9 股 = ¥900 > 100% 资金,熔断在集中度最高时变空文)。

## 1. 修订前(P0-7 原锁定)

- 仓位三连:单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万(`PositionLimitsConfig`)。
- RiskEngine **14-check**,`risk_summary` 长度 `min_length=14, max_length=14`(`instruction.py:163`,schema 常量)。
- `RiskConfig` runtime 不可改 + hot-reload 禁用 + LLM 永不持写引用。
- 这套**默认假设资金足够买入符合百分比约束的整手**,在几百元资金下不成立。

## 2. 修订后(本 amendment 锁定)

### 2.1 `BudgetTierPolicy`(新增,纯 Python,LLM / RiskEngine **上游**)

预算分层,**运行期输入**(用户真实可投资金 → tier),在任何 LLM 阶段**之前**裁定可负担 universe + 仓位规则:

| Tier | 资金 | universe | 仓位规则 |
|------|------|----------|----------|
| **Micro** | < ~¥2,000 | **仅 ETF**(白名单廉价宽基,1 手低成本且分散);个股若 1 手买不起 → `NO_COMPLIANT_TRADE` | 单标的 ≤1 手;总仓 ≤70% 仍适用 |
| **Small** | ¥2,000 – ¥10,000 | 「1 手 ≤ tier 上限」的低价股 + ETF | 单标的 ≤1 手;`concentration_exception` 标志可见 + **飞书确认**;总仓 cap 收紧 |
| **Normal** | ¥10,000 – ¥100,000 | 全市场筛后 universe | **P0-7 三连原样不变**:单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万 + 现金缓冲 |

**决策规则**: `max_lot_cost = available_cash × max_single_stock_pct`;若**无任何个股 1 手**满足 → 返回 `NO_COMPLIANT_TRADE`(一等公民 outcome,**非 error**;Micro 档落到 ETF-only 或 paper-only)。

### 2.2 `NO_COMPLIANT_TRADE` = 一等公民

不是异常、不是降级 HOLD 的副作用,而是显式 outcome:当日预算下无合规可成交标的时正常返回,飞书用专属模板告知(见 P0-2 / P0-4 体系 + R0 §8 飞书模板)。

### 2.3 `concentration_exception` 标志 + RiskEngine 独立再校验

- 仅对 **(a) 宽基 ETF + (b) 显式白名单标的**开放;**个股不享有**整手超额例外(个股低于合规 1 手 → `NO_COMPLIANT_TRADE` 或 paper-only)。理由:宽基 ETF 30% 仓位的风险 ≠ 单只小盘股 30%。
- 标志由 `BudgetTierPolicy`(纯 Python 上游)**设置**;**绝对 1 手上限** + **飞书确认**必备。
- **RiskEngine 必须独立再校验** ETF + 白名单 + 1 手上限(纵深防御,镜像 Builder 五道早返 + 14-check 双层守门 §2.3);**严禁**让 exception 标志成为单点绕过。RiskEngine 仍是**纯函数读输入字段**,**不得**新增可被上游 LLM/builder 自由设置的分支。

### 2.4 14-check 数与 schema 常量(实施期决策点,本 amendment 给推荐)

`risk_summary` 长度 `min=max=14` 是 schema 常量。引入 concentration_exception 校验有两个方案:

- **方案 A(推荐)**: **不改 check 数**。在现有 **Check 1(single_stock_pct)内扩展为 budget-aware**:若 `concentration_exception=true` 且标的 ∈ {ETF + 白名单} 且 ≤1 手,则放行并在该 check 的 reason 标注 `concentration_exception_granted`;否则按原 15%/整手逻辑。RiskEngine 在此分支内**独立再校验** ETF+白名单+1 手。**优点**:保 `min=max=14` schema 常量稳定,不破 P0-3 §2 红线 12;审计仍可凭 reason namespace 区分。
- **方案 B**: 新增 **Check 15** `concentration_exception_validation` + schema 常量 14→15(破坏式改 `instruction.py:163` + 全 14-check 测试)。**仅在 owner 要求审计层面完全独立的一条 check 时采用**。

**plan.html Phase L 默认走方案 A**;若 owner 在批准时要方案 B,plan 任务切换。

### 2.5 tier 阈值来源

`~¥2,000` / `~¥10,000` 为**初值**,写入 `config/risk.yaml`(runtime 不可改),理想从「全市场可交易标的的中位 1 手成本」派生而非硬编码——Phase L 任务从 universe 实际 lot 成本分布校准初值。ETF 白名单初值 = `510300` / `510500` / `159949`(可经 amendment 扩)。

## 3. 实施期任务调整

### 3.1 `backend/budget_policy/`(新模块,Phase L)

`BudgetTierPolicy` 纯函数 + `BudgetTier` 枚举 + `affordability` 计算 + `NO_COMPLIANT_TRADE` outcome。严禁 `import backend.{llm,agents,mirofish}`。

### 3.2 `config/risk.yaml` + `backend/risk/`

- `risk.yaml` 新增 `budget_tiers`(2 阈值 + ETF 白名单)+ `concentration_exception`(enabled / 1 手上限 / 飞书确认必备),全 runtime 不可改。
- `RiskEngine` Check 1 扩展为 budget-aware(方案 A)+ 独立再校验 exception;`PositionLimitsConfig` 新增字段(frozen)。
- `redline-check.sh` 加子检:`budget_tiers` / `concentration_exception` 常量存在 + RiskEngine 独立再校验 exception(AST)。

### 3.3 飞书 + 前端

- 飞书模板新增 `NO_COMPLIANT_TRADE` + `ETF_CONCENTRATION_EXCEPTION`(需显式确认),经 `renderer.py`(R0 §8)。
- 前端三层 reason 抽屉展示 tier + exception 标志(P1-5 不变)。

## 4. 红线清单(本 amendment 之后)

1. `BudgetTierPolicy` = 纯 Python 上游门,**先于** LLM + RiskEngine;严禁 `import backend.{llm,agents,mirofish}`。
2. **Normal 档(≥¥10k)P0-7 三连原样不变**(单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万)。
3. `concentration_exception` **仅 ETF + 白名单**,**个股不享有**;绝对 1 手上限 + 飞书确认必备。
4. RiskEngine **独立再校验** exception(ETF + 白名单 + 1 手),**非绕过**;RiskEngine 仍纯函数读输入字段,不新增可被上游自由设置的分支。
5. `NO_COMPLIANT_TRADE` = 一等公民 outcome,非 error,非 HOLD 副作用;飞书专属模板。
6. tier 阈值 + ETF 白名单写 `config/risk.yaml`,runtime 不可改 + hot-reload 禁用(P0-7 §2 红线 14 不变),改走 amendment + 重启。
7. 默认方案 A(Check 1 内 budget-aware,不改 14-check 数,保 `risk_summary` min=max=14 schema 常量);方案 B(Check 15 + schema 14→15)仅 owner 显式要求时启用。
8. 熔断五连(≤5 单/日 + 日亏 -5% + 连亏 3 + 60min 冷却 + SELL 不熔断)**不变**;`RiskConfig` 其余约束不变。

## 5. 修订记录追加

`docs/plan.html` Phase L 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.4 仓位三连表述补充「+ budget-adaptive 分层 + ETF concentration_exception(RiskEngine 独立再校验)」。
