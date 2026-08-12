# P0-10 修订(Line-2)— 2026-06-04 时间止损(STALE_EXIT)+ 止盈后次日再入场(RE_ENTRY)(卖出重设计第 3 期 E4+E5)

> **修订基准**: [P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction](./P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md) + [P0-10-amendment-line2-2026-06-04-sell-into-strength](./P0-10-amendment-line2-2026-06-04-sell-into-strength.md)(E3,第 2 期)+ [P0-7-amendment-2026-06-04-entry-anchored-chandelier](./P0-7-amendment-2026-06-04-entry-anchored-chandelier.md)(episode store)
> **关联**: `docs/research/sell-timing-deep-dive-and-redesign-2026-06-04.md` §2.4(时间止损 8-15 日 / Turtle fail-safe 再入场)+ §3.1(隔夜负折价 → 次日**开盘**回买最优)+ §5 E4/E5;owner 指令「哪怕第二天还有涨的趋势再买回来也行」
> **修订日期**: 2026-06-04(#71 session,第 3 期)
> **决策人**: owner(2026-06-04 拍板全四期)
> **性质**: 决策边界(新增 1 个确定性日频 SELL 触发 + 1 个确定性盘中 BUY 触发)。env 门控默认 OFF + shadow + 人工 gate;安全地基红线全留。

## 0. 意图与现状(实证缺口 G4)

- **僵尸仓**:动量 swing 的 edge 约 8-15 交易日耗尽(调研 §2.4);现状无时间维度退出——一只不涨不跌的票可以无限占用 ≤5 槽之一(轮动「独立够弱」7 条要求挑战者存在才让位)。
- **卖了买不回**:owner 明示「卖了第二天买回来也行」;现状 ADD 仅限「跌破成本的超卖回补」——止盈/强势卖出后价格仍高于成本,永远无法回补,高抛低吸闭环断裂。微观结构最优解(§3.1):**卖在日内强势、买在次日开盘**(隔夜负折价对买方有利)。

## 1. 决策

### 1.1 E4 `STALE_EXIT` 时间止损(盘中触发,每日至多一评)

```
持有交易日 ≥ stale_days (10)                        # episode store 的 opened_date 起算(E1 同源)
AND 区间收益 < stale_min_return (+3%)                # price/cost − 1
AND 近 stale_high_window (5) 个收盘未创入场后新高      # max(closes[-5:]) < max(entry_closes)
→ STALE_EXIT,全部已结算退出(clamp ¥50k,装不下退 clamp 量)
```

- `IntradayTriggerKind.STALE_EXIT`;优先级在 OVERBOUGHT_BIAS 之后、TAKE_PROFIT 之前(僵尸判定弱于强势信号;真盈利的票会先被 TP/E3 接走)。每日 (code, kind) 去重天然限频。
- **持有日数 = 观测窗代理(披露,review)**:`len(entry_closes)` 受日线帧历史深度(~30 收盘)与 episode 起算日双重截断 → 只会**低估**持有日数(STALE 触发只迟不早,保守向);超长持仓的「入场后新高」检验只看观测窗内;**bootstrap 宽限**:部署/激活后 episode 从首 tick 起算,真僵尸仓也要再等 ~10 交易日才可触发(owner 可按 E1 §1.2 同法 seed 真实入场日消除宽限)。
- 长持(D2 intact thesis)豁免(conviction hold 不被时间维度赶走);封死涨停 tick 抑制(板上不是僵尸)。
- 与 ≤5 槽轮动协同:STALE_EXIT 是轮动「独立够弱」的确定性下界(不要求挑战者存在);腾出的槽位次日由 Line-1/轮动 BUY 复用(T+1 复利闭环既有语义)。

### 1.2 E5 `RE_ENTRY`(BUY)止盈后次日回补

```
资格(前日状态,fold 自 fired-store + episode store,纯确定性):
  code 昨日因 TAKE_PROFIT / LIMIT_BREAK / SURGE_FADE / VOLUME_CLIMAX / OVERBOUGHT_BIAS 有「已送达」SELL
  AND code 今日仍在持仓(分批卖出的余仓;全退不回补——episode 已关,Turtle:趋势证伪后重入须更强信号,归 Line-1)
触发(今日 09:30–10:00 窗,每日一评):
  price ≤ 昨日卖出价 × (1 − reentry_discount 0.02)    # 隔夜折价兑现,买回比卖出便宜 ≥2%
  AND price > MA20(无结构破位)
  AND 无 thesis break(若 W 路径有 thesis)
→ AddIntent(回补昨日卖出量,clamp 预算/15% 单股上限/¥50k),经既有 make_add_context BUY 管道
```

- **保护性止损出场的码永不回补**(ATR/DRAWDOWN/THESIS/STALE 出场 = 趋势证伪,不接飞刀——与 ADD 马丁格尔禁令同精神);只回补**酌情落袋**(TP+E3)的余仓。
- 「昨日卖出价」= fired-store 行回查?fired-store 只存 (code,kind);**新增 `sold_price`/`sold_volume` 字段**(append-only 行扩展,读侧容缺——老行无价则该码不可回补,fail-closed 向不交易)。
- **retention 与长假(review P1 已修)**:回补资格读取先于 prune;prune 截止 = min(今日−7 天, 上一交易日)——春节/国庆长假后上一交易日的卖价行不被先删。
- **「已送达≠已执行」caveat(披露,镜像 D1-d §1.2)**:`sold_price/volume` 记的是**建议**单(派发即记);owner 未执行的派发卖单次日仍获回补资格 → 极端情形把仓位顶回卖前规模,由 15% 单股硬顶(14-check)+ 人工 gate 双重兜底;两条消息 owner 都可见,自行对账。深修需回报回填,留待 P0-4 回报链路演进。
- T+1 感知:回补股次日才可卖(天然合规);禁涨停 BUY / 14-check / 人工 gate 全保留;同日 SELL→ADD 互斥不冲突(资格要求**昨日**卖、今日回补)。
- env `QUANTMIND_LINE2_REENTRY_ENABLED` 与 `QUANTMIND_LINE2_STALE_EXIT_ENABLED` 独立(默认 OFF);`ReentryConfig`/`StaleExitConfig` frozen;None = 上一版 bit-for-bit;triggers v10→v11;config 入 runner hash。

### 1.3 PIT

- STALE_EXIT record:`stale_days`/`stale_min_return`/`stale_high_window` + 实际持有日数 + 区间收益;RE_ENTRY 走 ADD record 既有结构 + `sold_price`/`reentry_discount`。
- fired-store 行新增字段读写兼容(老行无新键 → load 照常,回补资格缺价即不评)。

## 2. 红线影响(全保持)

- 零 LLM / 纯函数:持有日数(episode store)+ 收盘序列 + 昨卖价(fired-store)+ frozen config。
- RE_ENTRY 是 **BUY**:跑全 5 早返(含 watchlist 门)+ 14-check + 预算/集中度上限 + 飞书人工;熔断 ≤5 单/日计数;**不构成自动交易**(全部经人工 gate)。
- 只增卖压原则:STALE_EXIT 仅新增退出;RE_ENTRY 不抑制任何 SELL(同日互斥单向不变:今日有 SELL fire 的码今日禁 ADD/RE_ENTRY)。
- 单一构造点 / T+1 / 自进化 7 禁:不变。

## 3. 测试锚点

- STALE:10 日+涨幅<3%+5 日无新高三条件齐触发;任一不满足不触发;长持豁免;封板抑制;全退 clamp。
- RE_ENTRY:昨日 TP 送达 + 今日折价 ≥2% + 价上 MA20 → BUY intent;保护性出场码不回补;全退码不回补;窗外(10:00 后)不评;折价不足不评;老 fired-store 行(无 sold_price)不评;同日已 SELL 的码被互斥滤掉。
- None config 各自 bit-for-bit;config hash 变化;record 字段。
