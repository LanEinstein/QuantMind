# SELL 决策树:何时卖 / 卖多少 / T+1 资金闭环(2026-06-04 落定态)

> **出处**:handoff 任务 B(owner 2026-06-03 锁定:「复利再投」= 把 SELL 触发与定量做对做全,非单独资金机制;T+1 务必查实)。本文记录 **D1-c(`f3471b6`)+ T+1 守卫链(`a3404b5`)+ D1-d(`ede3206`)落地后**的权威 SELL 决策树。
> **治理**:P0-10-amendment-line2-2026-05-30(止盈/减仓引入)→ 2026-05-31(no-cross-day WON'T-DO)→ 2026-06-03(D2 长持豁免)→ 2026-06-03(D1-a/b 自适应+regime 止损)→ 2026-06-04(D1-c regime 止盈 / D1-d 分级 / P0-4 T+1 守卫)。

## 1. 何时 SELL —— 完整触发清单

### 1.1 盘中 30s 确定性 runner(`line2_intraday_runner`,live 非门控;每码每 tick 至多 1 个,固定优先级)

| 优先级 | 触发 | 条件 | 卖多少 | env 门控 |
|---|---|---|---|---|
| 1 | `ATR_TRAILING_STOP` | 现价 < `近20日高 − 2×ATR`(日线 close-ATR,无跨 tick 状态) | **全部已结算** `available_volume`(按手) | 无(live) |
| 2 | `DRAWDOWN_STOP` | 盘中相对昨收回撤 ≤ −阈值;阈值 = 静态 5% **或** D1-a 个股 90 分位 |日收益|×1.5 clamp[3%,12%],D1-b 熊市 ×0.8 再 clamp | 全部已结算 | D1-a `ADAPTIVE_DRAWDOWN` / D1-b `REGIME_DRAWDOWN`(默认 OFF) |
| 3 | `THESIS_QUANT_BREAK` | 持仓 thesis 量化失效(白名单模板,零 LLM);只增卖压永不松既有止损 | 全退(clamp 单次 ¥50k;装不下则 fallthrough 不压低优先触发) | W 路径 `THESIS_QUANT_BREAK`(默认 OFF) |
| 4 | `TAKE_PROFIT` | 现价 ≥ `成本 + tier×eff_r×R`(R=2×ATR)且净盈利。**eff_r**:静态 1.0 或 D1-c regime 三档(牛 1.3/中 1.0/熊 0.6,clamp[0.5,2.0])。**tier**(D1-d):阶梯 (1.0,2.0)×eff_r,由 episode 账本档位数门控;阶梯走完不再止盈 | `tranche_fraction`(0.5)× 当前已结算(分级实际序列 50%→余下 50%→余 25% 骑移动止损) | D1-c `REGIME_TAKEPROFIT` / D1-d `TIERED_TAKEPROFIT`(默认 OFF;OFF=单档跨日 scale-out) |
| 5 | `WEIGHT_TRIM` | 权重 > 15%×1.10=16.5%(软)→ 减回 13%;长持(intact thesis,D2)只受 **15% 硬顶**(ceil 取整 + clamp ¥50k) | 减到目标权重 | D2 豁免 `THESIS_TAKEPROFIT_EXEMPT`(默认 OFF) |

去重:每日 `(code, trigger_kind)` 一次;保护性止损永不被低优先触发掩盖;停牌持仓干净降级(`partition_by_suspension`);stale/缺价 fail-closed 不触发;长持豁免只跳过 TAKE_PROFIT+软 trim(止损/thesis-break/硬顶不豁免)。

### 1.2 日线统计异动(`line2_daily_runner` + `sell_signal`)

DOWN 方向 `PRICE_ZSCORE` / `EWMA_DEVIATION` / `BOLLINGER_BREAKOUT`(量能 z-score 单独不触发)→ 每码取最强一个 → 卖全部已结算。`ROTATION` kind 仅由轮动 runner 构造(非异动输出)。

### 1.3 轮动 SELL(Phase V `rotation_runner`,T+1 跨日)

满 5 槽 + 在位「独立够弱」7 条 AND 挑战者「margin 胜出」→ 卖最弱在位(经单一构造点);`RotationIntent` append-only + expires fallback 防「卖了没买回」;让位保护性止损;每日 ≤1 轮动。

### 1.4 全路径共同闸门

所有 SELL → `assemble_monitoring_plan` 单一构造点 + 5 早返 + RiskEngine 14-check 独立权威 + 飞书人工 gate(`LINE2-MON-` 前缀);熔断 ≤5 单/日(SELL 不熔断计数但受 check 约束)+ 单次 ¥50k + 跌停禁 SELL;HOLD 永不路由。

## 2. 卖多少 —— 定量汇总

- 保护性止损(ATR/drawdown)+ thesis-break + 日线异动:**全部已结算 `available_volume`**(T+1,按手)。
- 止盈:`tranche_fraction=0.5` × 当前已结算;D1-d 分级后实际为 50% → 25% → 余 25% 骑移动止损(owner 拍板,2026-06-04)。
- 减仓:软回 13% / 长持硬顶回 15%(ceil + clamp)。
- 轮动:在位全退(按手、受 ¥50k clamp)。

## 3. T+1 与复利闭环(B.3 查实结论,`a3404b5`)

### 3.1 资金 —— 与 A 股语义一致,无需新机制 ✅

- **MockBroker 卖出成交当日即贷记 `_cash`(可用资金)**:内部 `_apply_fill` 与外部 `apply_external_fill` 两路皆然 → **卖出资金当日可再买入**。A 股惯例 = 卖出资金当日可买证券(T+0 资金周转)、**T+1 才能提现**;系统永禁真实下单、无提现概念 → 现金模型正确。
- **复利闭环**:SELL(止盈/止损/轮动)→ 现金当日入账 → **次日 09:35 Line-1 cron / Phase V 轮动 BUY** 用其建仓(轮动本就 T+1 跨日 + expires fallback);盘中 ADD 亦可用当日回笼资金(真实 A 股同样允许)。**owner 的「复利再投」由此天然成立,无单独机制。**

### 3.2 股票 T+1 ✅ + 回报路径守卫(`a3404b5` 补齐)

- 内部下单:`available_volume`(`volume − today_bought_volume`)预检;16:30 `advance_day` 解锁。
- **外部回报守卫(P0-4-amendment-2026-06-04)**:交易日 = instruction_id 内嵌 `QM-YYYYMMDD`(非 parse 时间);`sellable(D) = volume − Σ(d≥D 买入)`(date-keyed `bought_by_date`,16:30 清不掉、多日不覆盖、恢复可重建);不可能成交的回报(笔误)fail-closed 拒绝 → 人工澄清,绝不静默 desync。BrokerSnapshot v2 持久化该记录(读兼容 v1)。
- 残余(均保守方向,16:00 对账兜底):对账复原后记录为空(退化超持仓检查)/ 倒填早于 prune 窗(5 日期)。

## 4. 待 owner 后续决策 / follow-up

- D1-c/D1-d 激活:45 日 shadow 后 `QUANTMIND_LINE2_REGIME_TAKEPROFIT_ENABLED=1` / `QUANTMIND_LINE2_TIERED_TAKEPROFIT_ENABLED=1` + 重启(D1-d 建议与 D1-c 同启,熊市阶梯整体前移)。
- D1-d caveat(amendment §1.2):owner 未执行的已派发档位也推进阶梯(少卖方向;ledger+audit 可见)。
- D1 余项:`atr_stop_mult` / `max_add_drawdown` / `breakdown_tolerance` 自适应(dossier D1,同法各自 amendment+shadow)。
