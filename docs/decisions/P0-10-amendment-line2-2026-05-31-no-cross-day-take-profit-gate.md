# P0-10-line2 修订 — 2026-05-31 止盈跨日 gate 撤销(接受分批 scale-out)

> **修订基准**: [P0-10-amendment-line2-2026-05-30 止盈 + 减仓](./P0-10-amendment-line2-2026-05-30-take-profit-trim.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md)
> **修订日期**: 2026-05-31
> **触发**: P-006(止盈 tranche gate,ledger 派生连续持仓 episode)实施时,owner 经 AskUserQuestion 拍板。

## 1. 修订前(2026-05-30 锁定)

P0-10-amendment-line2-2026-05-30 §2.4 要求:止盈「+1R 减半」**每个连续持仓 episode 仅一次**;后续 tick 由 `take_profit_already_taken: frozenset[code]` 抑制重复止盈,该集合**从 `broker_events` 派生**(correlation→instruction→`side=SELL` + evidence `MARKET-{code}-take_profit`,限连续持仓 episode,volume 未归零),即 P-006。

## 2. 修订后(本 amendment 锁定)

**P-006 跨日 episode gate 撤销;接受跨日「分批 scale-out」语义。**

- **急性 bug 已闭**:P-005 已把 Line-2 盘中去重键从 `(code, side)` 改为 `(code, trigger_kind)`(`backend/orchestration/line2_intraday_runner.py` 的 `_fired`)。`take_profit` 是独立 kind,故**当日**一只标的最多止盈一次(30s tick 不再反复减半)——原本最危险的「当日反复减半抽干持仓」已由 P-005 根除。
- **跨日语义 = scale-out(可接受)**:一只**持续走强**的标的,在随后的交易日若仍 ≥ +1R,可再止盈一次(半仓)。owner 判定这等同温和的「分批了结/scale-out」,**非缺陷**,不值得为它引入脆弱的 ledger episode 复原(broker_events 只带 `correlation_id`+`positions_delta`、不带 `evidence_ids` → 需注入 plan store 按 instruction_id 反查 + 用 positions_delta 重建每只 volume episode 边界 + 每笔 SELL 异步查表,复原过脆且耦合 plan store)。
- **接口保留**:`evaluate_intraday_sell_intents(..., take_profit_already_taken: frozenset[str] = frozenset())` 参数**保留**(default 空),以便将来如需收紧为「每 episode 一次」可在不破签名的前提下接入(届时另开 amendment)。运行路径 `take_profit_already_taken` 恒为空。

## 3. 不变量(本 amendment 之后)

1. Line-2 盘中去重键 = `(code, trigger_kind)`(P-005);`take_profit` 当日仅一次。
2. **不**注入 plan store / event store 到 Line-2 provider 做 evidence 反查(monitoring 仍 import-clean;不新增 LedgerEventKind/持久状态)。
3. 止盈 = 部分 SELL,经 `assemble_monitoring_plan` → RiskEngine 14-check → 飞书人工(不变);`signal_id` 保 `LINE2-MON-`;SELL 不熔断(不变)。
4. 其余 P0-10-amendment-line2-2026-05-30(止盈 R 倍数 / tranche / 减仓阈值带 / 优先级)与安全地基全不变。

## 4. 实施期任务调整(Phase P)

- **P-006** 状态 = **won't-do(superseded by 本 amendment)**;`take_profit_already_taken` 留默认空。
- **P-007** 依赖 P-006 视为已满足(本决策即 P-006 终态)。

## 5. 修订记录追加

`docs/plan.html` P-006 标 won't-do + 修订记录 + SESSION_LOG 同步。
