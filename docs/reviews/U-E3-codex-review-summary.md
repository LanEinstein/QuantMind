# U-E3 代码审查总结(claude /code-review high — codex 额度回退)

> **任务**: U-E3 缺口5 反转 — owner 飞书只报「成交价(每股,不含费)+ 股数」,系统替 owner 计算含费成本价。
> **审查方式**: codex CLI 撞使用额度(确认 reset 至 2026-05-31),按 owner 既定回退规则改跑 **`claude /code-review high`**(`feedback_codex_rate_limit_fallback`)。
> **角度**: 3 correctness(line-by-line / removed-behavior / cross-file)+ 3 cleanup(reuse/simplify/efficiency)+ altitude,verify 召回偏置。
> **日期**: 2026-05-27 · **结论**: 已修完所有确认的 correctness/consistency findings + 2 个 LOW 边界 finding;门禁全绿后提交。

## 发现与处置

### 1. [已修 · 核心] dual-gross / 双真相源 — `apply_external_fill` v2
4 个角度独立命中。v2 路径的 `commission/transfer` 来自 `calculate_cost`(内部把 `order_price` 先 `_round2` 到 0.01),但 `gross`/`net`/`Trade.amount`/成本价此前用**原始 `fill_price`** 重算。FILLED 正则 `\d+(?:\.\d+)?` 允许 >2 位小数(如 `成交价 1800.555`),两条 gross 不一致 → 佣金按一个 gross 算、现金扣另一个 gross,审计/成本价口径不自洽。
**修法(altitude)**: v2 完全委托 `OrderCostBreakdown` 作单一真相源(`gross_amount`/`net_amount`/`fill_price`/各费项),经济量**只算一次**(取锁前),锁内只做现金/持仓 mutation。v1 保留 owner-fee 手算路径。同步修 `Trade.price`/`avg_fill_price`/positions_delta 用 `trade_price`(=结算 2dp 价,与 simulation_auto + cost_calculator「0.01 结算」约定一致)。消除了重复的费用公式(DRY)+ 散落的 `buy_cost_basis` 状态。2dp 常规价无行为变化(全部既有 v2 测试不变)。

### 2. [已修 · LOW] 模型 v1 仅 FILLED 合法 — `models/execution.py`
`report_schema_version` 此前未与 `kind` 交叉校验:v1 PARTIAL 能过模型(PARTIAL 一律禁 fee),却会在 `apply_external_fill` v1 分支深处 `requires fee` 崩(opaque)。新增校验:v1 仅对 FILLED 合法,边界 fail-fast。加测 `test_v1_only_valid_for_filled`。

### 3. [已修 · LOW] recovery v2 守门校验它真正回放的字段 — `persistence/recovery.py`
原 v2 fail-closed 守门只查 `net`/`commission`(回放并不消费它们),却不校验它**真正回放**的 `positions_delta.cost_price`;v2 BUY leg 缺 cost_price 会静默重建 cost_price=0.0 的持仓。强化:v2 对每个正 volume_delta leg 要求 `cost_price` 非空,否则 RecoveryError(fail-closed)。加测 `test_recover_v2_buy_missing_cost_price_fails_closed`。顺手合并两条 `import ... as _` 为一条(grep 可见性)。

## 未修(已论证)
- **micro-SELL 摩擦>gross → applier 吞异常无 ticket**:`ExecutionReportOrchestrator` 既有 `except Exception → success=False` 行为,**非 U-E3 引入**;且对真实成交不可达(A股最小 1 手=100 股 → gross ≥ 100×价 ≫ 5 元摩擦)。v2 下 `calculate_cost` 在 mutation 前 raise(net<0)。属编排层加固,另起任务。
- **v1 SELL `Trade.stamp_tax=0` 归因**:amendment 明确的 legacy 语义(owner fee 折叠全部),intended。
- **幂等键含 `report.fee`**:v2 fee 恒 None(`_num`→"")故自然排除,v1 保留;确认非回归。

## 门禁(全绿)
- backend: **3967 passed / 13 skipped**,cov **90.59%**(总)/ **98.81%**(risk,≥95)
- ruff clean · `scripts/redline-check.sh` ALL PASS
- frontend: type-check ✅ · vitest **139 passed** ✅ · build ✅
