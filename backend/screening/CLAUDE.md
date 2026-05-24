# backend/screening/ — 子任务上下文(Phase L)

> 状态:**todo**(Phase L,依赖 K marketdata_snapshot)。治理:[P0-9-amendment-2026-05-24](../../docs/decisions/P0-9-amendment-2026-05-24-full-market-screening.md)。任务:plan.html L-002 / L-005。

## 职责
**全市场纯量化初筛**:从 5000+ 标的(读 K 快照)→ 排除四件套硬排除 → Alpha158 子集因子 → top-N(~50-100)。**0 LLM**;LLM 仅在本模块出 top-N 小包之后(Phase M)才介入。

## 本模块红线
1. 排除四件套(新股≤30 / 次新≤180 / 流动性<2亿 / 单价>500)**硬排除 + fail-closed**(stock_meta 缺失 / 历史<20日 / 缺价 → 排除,不乐观保留)。Builder 第五道早返保留为最后防御,**同一 `exclusion_rules` 真相源**。
2. 科创 688 / 北交 8 / ST / 可转债 **永禁**(P0-7 §2.4 不变)。
3. universe = board 白名单规则(非 13-code 写死);规则 runtime 不可改。
4. 读 K 快照 + 写 SignalInputManifest(可复现)。
5. top-N 有固定上限;超数由确定性 tie-breaker 收窄,**不调 LLM 补名**。

## import 隔离
严禁 `import backend.{llm,agents,mirofish}`。可用:`backend.{marketdata_snapshot,data,risk}` 类型 + qlib/vectorbt(因子)。

## 接口契约(草案)
- `Screener.screen(snapshot_id, budget_tier) -> list[CandidateRow]`(确定性,可复现)。
- 因子计算纯函数 on 快照。
