# C0 EXIT/做T 执行契约(frozen,信号研究前先冻;codex R1-#1/#2/#6/#8)

> **状态**:**FROZEN(C0b 落地随码冻结;contract_id = `ExitExecutionContract` 字段 SHA256)** · 改动 = amendment + debit 非清零账本 · **日期**:2026-06-27 · **作者**:Claude(Opus 4.8)
> **上位**:plan `misty-doodling-pnueli` §A3.1 + `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27`(P-A..P-E)+ `system-roadmap-outline-2026-06-27` §6.1。
> **这是什么**:在写任何 EXIT/做T **信号**之前,先把**订单怎么流**(时序/成交/排队/记账)钉死。信号(避顶部/做T 阈值)是 C1/C3 的事;本契约只定**执行语义**,使后续信号无法靠"换执行假设"刷出 P&L(codex R1-#2:防 backtest 后才发明 live 语义)。

## 1. 时序(codex R1-#6:EOD 不支持当日成交)

- **信号 as-of close T**:所有 EXIT/做T 信号用日频 EOD 特征(OBV/放量滞涨/日频 cyq/成本带 收盘后才齐)→ 在 **close T 决策**。
- **下单 T+1**:决策产出的 `OrderIntent` 进入 `pending`,经冻结引擎 **next-bar barrier 在 T+1 open 成交**(复用 `run_backtest` 的 `_fill_pending`)。**绝无当日成交**。
- **结算 T+1**:卖出只针对**已结算**持仓(日频回测中,前一周期已持有的仓即已结算);做T 低吸的新仓**次日才可卖**(守 T+1)。

## 2. 成交 / 不可成交(codex R1-#8:不可卖不丢弃,排队 + 累计被套)

- **成交**:经冻结 `simulate_harsh_fill`(涨跌停门 + ≤10% ADV 容量 + lot floor)+ `compute_fill_economics`(¥5 min 佣金/印花 0.1%/过户 0.00341%/分板块滑点),与冻结引擎逐字节同源。
- **不可成交(跌停/停牌)= SELL 当日 lapse**:冻结 `_fill_pending` 对 `at_limit_down` / 缺 bar(停牌)的订单**当日不成交**(lapse)。
- **排队(queue)= overlay 次日 re-emit**:EXIT overlay 是**有状态**的——一笔被 signal 但未成交的 SELL,**持仓仍在书上、每日 mark 到收盘 → 被套 MTM 损失自然累计**;overlay **次日重新 emit 同一 SELL**,直到真成交。**绝不**因不可卖而把持仓丢弃(= live 现实:卖不掉就是被套)。
- **signal-hit vs fillable-hit 分账**:overlay 记录每笔 EXIT 的 **signal 日**(何时判定该退)与 **fill 日**(何时真卖掉),两者分开报(诊断分母 fills-aware,codex R1-#8)。

## 3. 强制止损硬触发(P-B:不等确认)

- **P-A 确认门**作用于**入场 / 止盈式 EXIT**(等市场确认方向再动);**P-B 强制止损是硬安全底线,反常下跌硬触发、不等确认**。
- 止损 SELL 与避顶部 SELL 走同一执行契约(close T→T+1→不可卖排队);区别仅在**触发条件**(止损=破止损位即触发;避顶部=确认滚顶才触发)。**只增卖压,永不放松现有止损**(同 rebar §3)。

## 4. 做T inventory 记账(P-C profit-gate;codex R1-#7 worst-case)

- **做T = 持仓内低吸高抛**,**仅当该持仓有正浮盈**(P-C:`market_value > cost_basis` 才允许做T;绝不靠做T 摊低亏损位)。
- **守 T+1**:做T 高抛只卖**已结算**昨仓;做T 低吸的新仓次日才可再卖。**底仓地板**(base floor)永不破。
- **worst-case 日序**(codex R1-#7):日频 OHLC 证不了 low 在 high 前 → 做T 增益用**最不利日内次序** + **仅 inventory-feasible** 交易计;增益是**保守下界**;真做T 精确声明待日内分钟数据(plan §9-4)。
- **C0b 契约只定 do-T 订单流语义**;做T 实际信号/阈值 = C3。

## 5. 再入规则(P-A 对称)

- EXIT 卖出后**不立即再买**回同名;再入须重新过入场确认门(P-A)+ 再入锁定窗(防抖动洗成本)。**C0b 契约定再入锁存在性**;锁窗参数 = C1 标定。

## 6. 与冻结引擎的关系(不变量,codex R1-#1)

- **overlay-disabled ≡ 冻结引擎 byte-exact**:overlay 不产出任何订单时,`run_e2e_backtest` 必与 `backend.backtest.harness.run_backtest` **逐字段相同**(等权 5 槽 sizing 为 plumbing 参照)。这证 EXIT/做T 是**纯叠加**,非偷改基线。
- **复用冻结 plumbing**:`run_e2e_backtest` **import 复用**冻结 `_fill_pending`/`_record_buy_exposures`/`_close_marks_for_holdings`/`_assemble_result`,而非 copy → fill/成本/账本/守恒/exposure 机器零分歧、且随冻结引擎演进自动 track(契约移植非现拟)。
- **不碰引擎字节 / value-sleeve(AF-*) / RiskEngine / 单一构造点**;研究侧 import 隔离(无 `backend.{llm,agents,mirofish,risk}`)。

## 7. 契约冻结 + 账本

- `ExitExecutionContract` frozen dataclass(本契约第 1-5 节的可参数化位:lot_size=100 / queue_unfilled_exits=True / mandatory_stop_bypasses_confirmation=True / do_t_requires_settled_T1=True / do_t_requires_positive_unrealized=True / reentry_lock=True)→ `contract_id` = 字段排序 JSON 的 SHA256(确定性)。
- **契约任何改动 = amendment + debit 非清零账本**(改判据不清零)。C1/C3/C5 信号在**固定契约**上跑,执行假设不再动。
