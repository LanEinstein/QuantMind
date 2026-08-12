# P0-4 修订 — 2026-06-04 外部回报 SELL 守卫收紧到 T+1 已结算 available_volume

> **修订基准**: [P0-4 飞书回报解析与回填](./P0-4-feishu-report-parsing-and-fill-application.md)
> **关联**: P0-4-amendment-2026-06-01(回填即真相 + BUY affordability 守卫先例)/ `docs/handoff-d1c-takeprofit-regime-and-sell-logic-2026-06-03.md` §B.3(owner 明确提醒 T+1 查实)
> **修订日期**: 2026-06-04(#68 session 续;任务 B.3 查实发现)
> **决策人**: owner(本 session AskUserQuestion 拍板「修」)
> **性质**: fail-closed 完整性修复(回报路径行为变化:一类原被静默接受的笔误回报改为拒绝触发澄清)。

## 0. 发现

B.3 T+1 查实结论:**资金语义正确**(SELL 成交当日贷记 `_cash` 当日可再买入 = A 股「卖出资金当日可买证券、T+1 提现」;系统无提现概念无需建模)、**内部下单路径 T+1 正确**(`place_order` SELL 预检 `available_volume`)。唯一 gap:

- `MockBroker.apply_external_fill` SELL 分支只查**总持仓** `pos.volume < volume`,**不查** `available_volume`(T+1 已结算)。
- 后果:一份「卖出今日买入股份」的回报——真实券商 T+1 约束下**不可能成交**,必为笔误——会被镜像静默接受,而非 fail-closed 触发澄清;镜像随即与真实账户 desync(靠 16:00 对账兜底)。
- 与 BUY 侧不对称:P0-4-amendment-2026-06-01 已为 BUY 加 affordability 守卫(「a fill the account cannot afford is not truth」);SELL 侧同理:**a fill the real broker could not have executed is not truth**。

## 1. 决策

- SELL 分支守卫分两层、两种可区分错误信息(ops 可一眼分辨):
  1. `pos is None or volume > pos.volume` → 超持仓(原有守卫,保留)。
  2. **按交易日期键控的 T+1 守卫(新增)**:报告卖出量 > `pos.volume − 当报告交易日买入量` = 真实券商不可能成交的回报 = 笔误 → `ValueError` 拒绝,镜像不动(`apply_external_fill` 锁内验证先于变更,raise = 镜像零变化,applier 释放幂等 claim 允许修正后重试——既有契约)。
- **为什么不用 `available_volume` 计数器(codex cycle-1 P1)**:`today_bought_volume` 被 16:30 `advance_day` cron 清零——一份**迟到的同日回报**(16:30 后才发)会绕过基于计数器的守卫,重新引入本 amendment 要堵的静默 desync。故 `_MutablePosition` 新增 **date-keyed 买入记录** `last_buy_trade_date + last_buy_date_volume`(只被 BUY 变更、自然按日期过期、不被 `advance_day` 清零)。
- **交易日 = instruction_id 内嵌日期,非 parsed_at(codex cycle-2 P1)**:该路径的 `traded_at` 实为 `report.parsed_at`——**次日补录**的回报若按 parse 日期键控,`bought_same_day` 归零 → 不可能的同日卖出被放行。指令人工当日执行、14:55 后须盘后补录前缀(P0-3 §1.4)是既有语义,故真实交易日 = `QM-YYYYMMDD-` 前缀(`trade_dates.instruction_trade_date`,解析失败回退 fill 时间戳上海日期,防御性)。BUY 记录与 SELL 守卫两侧同口径(迟到 BUY 补录的股份也按指令日解锁)。
- **可卖量 = as-of 报告交易日(codex cycle-6 P1)**:`sellable(D) = pos.volume − Σ(d≥D 的买入)`——D 当日买入未结算、D 之后买入当时不存在,都须扣除;否则「BUY 100@D、BUY 100@D+1、补录 SELL 100@D」会用后日仓位放行 D 日不可能的成交。该界**只会低估**(D 之后的 SELL 只缩 `pos.volume`)→ 极端复杂补录序列可能误拒走人工澄清,**永不**误收(fail-closed 方向)。
- **补录 BUY 不锁当日(codex cycle-6 P2,顺带修既有过紧语义)**:指令日 < 今天的 BUY 补录已结算,`_apply_buy(lock_today=False)` 不再计入 `today_bought_volume`——否则把可卖仓位错冻到下一次 `advance_day`,且 EOD 快照会把它误标为快照日买入。
- `_apply_buy` 增可选 `traded_date`(内部 `_apply_fill` 与外部 `apply_external_fill` 两路都传),date-keyed 记录两路一致。
- 上游行为:`ExecutionReportApplier.apply` 原样上抛 → 编排层走澄清路径(AMBIGUOUS,绝不更新 MockBroker)——P0-4 既有 fail-closed 语义,无新通道。

## 2. 影响评估

- **对账**:无影响——被拒回报不落账,镜像保持一致;修正后的回报可重试(幂等 claim 已释放)。
- **误拒风险**:镜像 `today_bought_volume` 状态陈旧(如外部 BUY 回报晚到)时,可能多一次人工澄清——fail-closed 优于静默接受不可能成交的回报(人工 gate 设计本意)。
- **恢复路径(codex cycle-3/4/5 P1 链)**:① 事件 replay 重建 `bought_by_date`(`recovery._apply_event`:外部 fill 按 instruction 日期、内部 ORDER_FILLED 按事件上海日期);② main.py seed 改传 recovery position 对象(`to_snapshot_positions()` 会丢字段);③ 快照路径:EOD 快照写在 `advance_day` **之前**,其 `today_bought_volume` 即「快照日买入量」→ v1 快照恢复时回种 `{snapshot_trade_date: today_bought_volume}`;④ **scheduler 快照构造修复**:公开 `Position` 无 `today_bought_volume` 属性,旧 getattr 恒写 0(既有潜在 bug:16:00 快照重启即丢 T+1 锁)→ 改 `volume − available_volume`。`reset_to_snapshot`(对账复原)持仓记录为空 → 守卫退化为超持仓检查(对账即校准,无新误伤)。
- **BrokerSnapshot schema v1→v2(codex cycle-7 P1,owner 本 session 拍板「现在升级」)**:跨多日买入的日期记录须跨快照游标存续(否则「BUY@D、BUY@D+1、EOD 快照、重启、补录 SELL@D」绕过守卫)。`BrokerSnapshotPosition` 新增 `bought_by_date: dict[str,int]`(ISO 日期键,validator 校验格式/非负);`BROKER_SNAPSHOT_SCHEMA_VERSION` 1→2。**读兼容**:版本校验改「拒绝未来版本」(v1 行解析为默认空 map);**checksum 字节级兼容**:canonical payload 仅在 map 非空时折入该键 → v1 行的存量 checksum 原样可验,新行自洽;写路径恒写 v2。scheduler 经新 `MockBroker.export_bought_by_date()` 读 map(公开 Position 无此字段;getattr 守卫,fake/旧视图退化空 map);recovery 优先持久化 map、空时回退 ③ 的 today_bought 重种。

## 3. 测试锚点

- 同日外部 BUY 后 SELL 今日股份 → `ValueError`(match "T+1"),镜像零变化。
- **迟到同日回报**:BUY → `advance_day()`(同交易日 16:30)→ SELL(`traded_at` 仍同日)→ 仍拒绝(codex P1 回归)。
- 次日 SELL → 正常入账。
- 既有「无持仓 SELL」/「超持仓 SELL」守卫行为不变。
