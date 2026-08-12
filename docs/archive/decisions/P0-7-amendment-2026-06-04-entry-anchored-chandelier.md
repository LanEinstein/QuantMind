# P0-7 修订 — 2026-06-04 入场锚定 chandelier 止损(3×ATR)+ 深度分层收盘确认(卖出重设计第 1 期 E1+E2)

> **修订基准**: [P0-7 风险/仓位红线](./P0-7-risk-config-immutable-and-position-caps.md) + [P0-7-amendment-2026-06-03-adaptive-intraday-thresholds](./P0-7-amendment-2026-06-03-adaptive-intraday-thresholds.md)(D1-a 校准框架)+ [P0-7-amendment-2026-06-03-regime-conditioned-drawdown](./P0-7-amendment-2026-06-03-regime-conditioned-drawdown.md)(D1-b)
> **关联**: `docs/research/sell-timing-deep-dive-and-redesign-2026-06-04.md` §2.1-2.3 / §5 E1+E2(调研:LeBeau 原典 + A 股 T+1 微观结构)
> **修订日期**: 2026-06-04(#71 session,第 1 期)
> **决策人**: owner(2026-06-04 拍板:全四期 / 3.0×ATR 正典默认 / **E1+E2 shadow 缩短至 10-15 交易日 + 每日反事实对照**〔偏离 45 日惯例,owner 明示批准——修的是已证实缺陷,旧逻辑多跑一天 = 已知缺陷多跑一天〕)
> **性质**: 决策边界(ATR_TRAILING_STOP 的锚定、乘数与触发确认语义重做)。env 门控默认 OFF + shadow + 人工 gate;安全地基红线全留。

## 0. 意图与现状(实证缺陷,dossier §1)

现状 `ATR_TRAILING_STOP` = `现价 < max(近20日收盘) − 2.0×ATR`,盘中触碰即全仓卖出:

1. **锚定非正典**(G1):20 日窗含**买入前**高点。LeBeau 原文锚 = "highest high **since we entered the trade**";窗口版是 StockCharts/TradingView 简化讹传。实证:605111 止损线在成本 +2.2%、600011 在成本价——新仓位的「利润保护」实为贴身止损,必然「跌一点就让卖」。
2. **乘数过紧**:2.0× 低于正典带(LeBeau 2.5-4,默认 3;Van Tharp 2.7-3.4)→ 震荡市 whipsaw。
3. **盘中触碰即触发**(G2):A 股 T+1 结构性「先抑后扬」(隔夜负折价年化 −11.9%,尾盘 15-30 分钟最强)→ 早盘触发系统性卖在最弱时点。实证:06-03 早盘 600011@8.72 触发(午后 9.19,+5.4%)、600909@7.10 触发(当日收 7.51)。

## 1. 决策

### 1.1 入场锚定双层止损(E1;`ChandelierConfig`,frozen)

```
anchor       = max( max(daily_close since entry), cost )      # 入场以来最高收盘,棘轮性质由 max 保证
initial_stop = cost − initial_atr_mult(2.0) × ATR             # LeBeau 资金管理初始止损
chandelier   = anchor − chandelier_atr_mult(3.0) × ATR        # 正典乘数(owner 拍板 3.0)
stop_level   = max(initial_stop, chandelier)                  # chandelier 棘轮上穿初始止损后接管
```

- 锚用**最高收盘**(非盘中高点):日线帧只有 closes(PIT 同源);收盘锚 + 收盘确认是公认的低 whipsaw 变体;当日盘中急涨急跌的落袋职责归第 2 期 E3(SURGE_FADE 等)。
- 「since entry」由**交易日计数切片**实现:`closes[-min(n, len):]`,n = [entry_date, frame_trade_date] 内交易日数(静态 holidays.yaml;runner 既有 `trading_hours` 依赖)。停牌缺行使 n 略多算 → 窗口偏大 → 可能含少量 pre-entry 收盘,由 initial_stop 下界兜住(方向保守,文档化)。
- **消息语义诚实化**:initial_stop 触发的飞书文案是「止损」、chandelier 触发的是「回撤锁盈」(`stop_governing` 字段),owner 不再把止损误读成止盈失灵。

### 1.2 入场日期 = `PositionEpisodeStore`(append-only,V-003/entry_rank 模式)

- 新纯模块 `backend/orchestration/position_episode_store.py`:`opened(code, trade_date)` / `closed(code, trade_date)` 事件流,读时 fold → `{code: opened_date}`(seed/重复行取**最早** opened,不会被 sync 推后);runner 每 tick `sync(held_codes, trade_date)`(首见即开、离场即关,幂等;镜像 `takeprofit_ledger.sync_episodes`)。**显式落账非反查**(P-006 教训:严禁 broker_events 反向重建)。
- **store 常开**(review altitude:防「激活日才开始积累」断崖):main.py **无条件**构建并注入 episode store,runner 每 tick 同步(决策零影响、append-only、量级=持仓变动),entry 日期自本期部署日起持续积累——与 feature/shadow env **解耦**;env 只控制「是否把 entry 窗口喂给评估器 / 是否打影子日志」。
- **fail-open**:store 损坏/不可读 → 该码无 entry 日期 → **回退现行 v8 窗口止损**(保护永不消失,只是退到旧锚定)+ error 日志。
- **bootstrap caveat(文档化)**:本期部署时已持仓的码,episode 自部署后首 tick 起算 → 锚窗起点晚于真实入场,锚偏低 → chandelier 偏松,由 initial_stop 兜底。owner 可一次性手工 seed 真实入场日(运维手册:服务停止时向 `data/line2_intraday_state/position_episodes.jsonl` 追加 `{"event_type":"opened","code":"605111","trade_date":"2026-06-01"}` 等行——**event_type 必须是 `opened`/`closed`**——后重启;seed 行在文件前部即优先生效)。

### 1.3 深度分层 + 收盘确认窗(E2)

```
深破(立即路由,本 tick):price ≤ stop_level − deep_band_atr(0.5)×ATR
浅破(确认后路由):stop_level − 0.5×ATR < price < stop_level
    → 仅当 tick 时刻 ∈ 确认窗 [14:30, 14:55)(Asia/Shanghai)才产生 intent;窗外不产 intent(非 dedup,下 tick 重评)
DRAWDOWN_STOP(−5% vs 昨收,含 D1-a/b 校准):一字不动,全天即时 —— 灾难止损不受确认延迟影响
```

- 直接利用 A 股日内结构:早盘浅破不卖(大概率 whipsaw),尾盘仍破才卖(当日最强时段出手 + 规避次日开盘隔夜折价)。
- **确认窗止于 14:55(刻意死区)**:连续竞价 14:57 截止、14:57-15:00 收盘集合竞价;建议经飞书人工执行,14:55 截止给 owner ≥2 分钟下单窗口。14:55-15:00 间才首次浅破的极端 case 顺延次日(损失上界=隔夜折价,频率极低;深破/−5% 不受此限)。
- **诚实披露的 trade-off(两条,owner 已知情)**:① 单边阴跌不反弹的日子,浅破出场比现状晚约半日,代价上界 ≈ 0.5×ATR 带宽;② **大涨后的回撤给回更多**——3.0× 锚定线在「20 日窗高=入场后高」情形比旧 2.0× 窗口线低 1.0×ATR(例:锚 6.0、ATR 0.6 → 新 4.2 vs 旧 4.8),这是 owner 拍板 3.0×(让利润奔跑)对 2.5×(更早锁盈)的直接后果,shadow 报告的 would_fire 对照会显性呈现。换得:早盘 whipsaw 整族消除(06-03 两案例全免)+ 新仓位不再被 pre-entry 高点贴身止损。深破/−5% 即时通道保证崩跌日不受影响。

### 1.4 env 门控 + shadow(owner 拍板的激活节奏)

- 主开关 `QUANTMIND_LINE2_ENTRY_ANCHORED_STOP_ENABLED`(默认 OFF;None config = **v8 bit-for-bit**)。
- 影子开关 `QUANTMIND_LINE2_ENTRY_ANCHORED_STOP_SHADOW`(默认 OFF;主开关 OFF 时可独立开):每 tick 对每持仓码并行计算 v9 止损,与现行 v8 止损对照,**每码每日一条** `chandelier_shadow_compare` info 日志(old_stop/new_stop/price/would_fire_old/would_fire_new/governing)——**只记日志,零决策影响**。
- `scripts/line2_chandelier_shadow_report.py`:扫 `logs/quantmind.jsonl*` 聚合输出每日反事实对照表(owner 每日一眼)。
- **激活手册**:shadow 10-15 交易日 → 每日看对照表无恶化 → `QUANTMIND_LINE2_ENTRY_ANCHORED_STOP_ENABLED=1` + 重启(可选先 seed episode 真实入场日,§1.2)。

### 1.5 PIT 三件套(D1 惯例照搬)

- intent 新字段 `stop_anchor` / `stop_governing`("initial"/"chandelier";窗口回退=None)/ `effective_atr_stop_mult`(实际生效乘数,recorder 回退静态 config)——贯穿全部 sell 构造。
- `_sell_record.threshold_params` 写实际生效值(+ `deep_band_atr`、确认窗、anchor)。
- runner `_compute_config_hash` 纳 `chandelier {version, config}`;`FEATURE_CODE_VERSION` triggers v8→v9;calibration 模块版本同步 bump。陈旧 manifest fail-closed。

## 2. 红线影响(全保持)

- **零 LLM**:anchor/乘数/确认窗全部为持久快照 + 静态 config 的纯函数;严禁 LLM/新闻进任何阈值。
- **保护永不消失**:feature ON 的每持仓码恒有 stop_level(双层 max);episode 缺失回退 v8 窗口止损;DRAWDOWN_STOP 不动。**初始止损 cost−2×ATR 在多数实证情形比旧「贴成本线」更明确**;唯一变慢点 = 浅破确认延迟(§1.3 已披露,深破+(-5%) 即时兜底)。
- 单一构造点 / RiskEngine 14-check / 飞书人工 / 熔断 / 仓位三连 / T+1 / N-005 import 隔离:不变。`intraday_calibration` 保持零 `backend.*` import(纯 maths)。
- config runtime 不可改;重校准走 D1/P2-2 离线人工 gate。

## 3. 测试锚点

- `derive_entry_anchored_stop` 纯函数:anchor=max(closes_since_entry, cost);初始止损 governs(新仓)→ chandelier 接管(涨后);entry 当日(零收盘)anchor=cost;ATR≤0 → None。
- 06-03 反事实重建:600909(cost 7.15,旧 stop 7.24 → 新 6.75)7.10 **不**触发;600011(cost 9.21,旧 9.17 → 新 ~8.62)8.72 **不**触发;605020 大涨后 anchor 升至 36.0+,3.0× 下 34.0 不触发。
- E2:深破立即;浅破窗外无 intent;浅破窗内 fire;DRAWDOWN_STOP 全天即时。
- None config = v8 bit-for-bit;config_hash 含/不含 chandelier 不同;intent/record 带实际生效参数。
- episode store:open/close fold、sync 幂等、损坏 fail-open;runner 注入 None = 现行为。
- shadow:每码每日恰一条对照日志;主开关 OFF 时决策路径 bit-for-bit。
