# P0-10 修订(Line-2)— 2026-06-04 强势卖出触发族(炸板/冲高回落/放量滞涨/乖离超买)+ 封死涨停豁免(卖出重设计第 2 期 E3)

> **修订基准**: [P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction](./P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md) + [P0-10-amendment-line2-2026-05-30](./P0-10-amendment-line2-2026-05-30-takeprofit-weighttrim.md)(止盈/减仓先例)+ [P0-10-amendment-line2-2026-06-04-tiered-takeprofit](./P0-10-amendment-line2-2026-06-04-tiered-takeprofit.md)
> **关联**: `docs/research/sell-timing-deep-dive-and-redesign-2026-06-04.md` §3(A 股实证:Wan 2015 封板次日延续 79.4% / 炸板次日负溢价 / 华安 2026 缩量首板 +7.44% vs 放量首板 −0.71% / 民生 2025 盘中过强 20 日反转 / BIAS 经典阈值)+ §5 E3
> **修订日期**: 2026-06-04(#71 session,第 2 期)
> **决策人**: owner(2026-06-04 拍板「四个全做 + 封死豁免」;指令原文「股票上涨时应该准确判断何时该卖掉变现」「量化指标不会骗人」)
> **性质**: 决策边界(新增 4 个确定性盘中 SELL 触发 + 1 个反向豁免)。env 门控默认 OFF + shadow + 人工 gate;安全地基红线全留。

## 0. 意图与现状(实证缺口 G3)

现状盘中主动落袋只有 TAKE_PROFIT(`成本+1R`)一条线:06-03 大涨日 300433 冲至 +5.6% 全程零建议(未过 +1R)。冲高回落、炸板、放量滞涨、乖离超买这些 A 股实证有效的「该落袋」强势信号,系统一个都没有——owner 的「涨时不知何时变现」批评成立。

## 1. 决策

### 1.1 新增 4 个 `IntradayTriggerKind`(全部 spot 行 + 日线帧的纯函数,零 LLM)

优先级:`ATR_TRAILING_STOP > DRAWDOWN_STOP > THESIS_QUANT_BREAK >`**`LIMIT_BREAK > SURGE_FADE > VOLUME_CLIMAX > OVERBOUGHT_BIAS`**`> TAKE_PROFIT > WEIGHT_TRIM`(保护性退出与 thesis 全退恒先;族内按证据强度排序;每 tick 每码至多一个 intent,不同 kind 各自每日去重)。

| kind | 判据(`StrengthSellConfig` frozen 默认) | 卖量 | 盈利前置 |
|---|---|---|---|
| `LIMIT_BREAK` 炸板 | `day_high ≥ 涨停价−0.005` AND `price ≤ 涨停价×(1−break_pullback 0.02)` | 1/3 已结算 | **无**(炸板次日负溢价与盈亏无关,de-risk 信号) |
| `SURGE_FADE` 冲高回落 | `(day_high/昨收−1) ≥ surge_min 0.06` AND `(price/day_high−1) ≤ −fade_min 0.03` | 1/3 | **有**(price > cost;亏损票的反弹回落不收割,防与 ADD 互踩 churn) |
| `VOLUME_CLIMAX` 放量滞涨 | 当日累计成交额 ≥ `climax_mult 3.0`× 近5日均额 AND 日内涨幅 < `stall_max 0.03` AND `price ≥ MA20×(1+extension_min 0.10)` | 1/3 | 有 |
| `OVERBOUGHT_BIAS` 乖离超买 | `(price−MA20)/MA20 ≥ bias_threshold 0.15`(静态;个股分位校准留给 D1 框架后续) | 1/3 | 有 |

- 卖量 = 1/3 当前已结算(按手取整、min 1 手、clamp ¥50k 单次上限——镜像 thesis-break clamp);不足 1 手跳过;**单手即超 ¥50k 的极高价票整族跳过**(镜像 thesis 先例;universe 内极罕见,披露在案)。
- **VOLUME_CLIMAX 时段语义(披露)**:判据用当日**累计**成交额对 5 日全日均额——早盘天然难触发,越临近收盘越敏感;3× 全日均额即使在尾盘也是真实的天量(1× 才是常态),叠加滞涨+高位双条件;默认 OFF + 盈利前置 + 人工 gate 三重缓冲。「每信号减 1/3」与调研共识(≥2 顶部信号高概率离场,每信号 1/3)一致:同日多信号通过各自 dedup 键自然叠加,全部经飞书人工 gate。
- **TAKE_PROFIT 并行语义(披露)**:强势信号与 +1R 止盈同日可分别触发(不同 kind)→ 当日累计卖出可达 1/3 + 50%;两者都是人工 gate 建议,owner 可择一执行。族内优先级使同 tick 至多一个。
- **长持豁免一致性**:D2 intact-thesis 长持码跳过全部 E3(与跳过 TAKE_PROFIT/软 trim 同理——conviction hold 不被酌情落袋打断);15% 硬顶 WEIGHT_TRIM 不豁免。

### 1.2 `SEALED_LIMIT_HOLD` 封死涨停豁免(反向,确定性「让利润奔跑」)

- `price ≥ 涨停价−0.005`(封死)→ 本 tick **抑制 TAKE_PROFIT + 全部 E3**(Wan et al. 2015:封死涨停次日开盘延续 79.4%;卖在封板 = 双输——既错过延续又把单交在次日隔夜折价)。
- 保护性止损(ATR/drawdown;封板时数学上不可触发)/ THESIS_QUANT_BREAK / **WEIGHT_TRIM(15% 集中度红线优先于让利奔跑)** 不受抑制。
- 次日开板回落即 `LIMIT_BREAK` 接手——豁免与炸板触发构成完整的「封住=持有,开板=落袋」确定性对。

### 1.3 涨停价派生(确定性近似,披露)

`limit_up_price(code, name, prev_close)` = `round(prev_close × (1+ratio), 2)`;ratio 按前缀:`30*`→20%,`688*`→20%(universe 永禁,防御性),名称含 `ST`→5%,其余(主板/ETF)→10%。**已知近似残余**:个别跨境/创业板 ETF 为 20%——误差方向 = 把 10% 当涨停误判 sealed → 多持有(保守);LIMIT_BREAK 误触发不可能(`day_high ≥ 假涨停价` 意味着真涨停更高,touch 未发生即不满足)。RiskEngine `limit_up_down_block` 仍是订单合法性唯一权威(本派生只控触发,不控下单)。

### 1.4 数据与 env

- 新评估器入参 `amounts_by_code`(`parse_held_series` 本就解析出 amounts,runner 原地丢弃 → 现在透传;PIT 同源,manifest 的 daily_frame pin 不变)。
- env `QUANTMIND_LINE2_SELL_INTO_STRENGTH_ENABLED`(默认 OFF);`StrengthSellConfig` frozen runtime-immutable;None = **v9 bit-for-bit**。triggers v9→v10;config 入 runner config_hash `{version, config}`。
- record:strength 触发的 `threshold_params` 写全部生效阈值 + `day_high` + `limit_up_price`(replay 复算判据);`drawdown_pct`/`atr` 等既有字段照写。
- 激活:shadow(同 E1 报告机制不适用——E3 无新旧对照,直接观察 dedup 后触发频次)→ owner env + 重启;建议与 E1/E2 同窗观察。

## 2. 红线影响(全保持)

- **零 LLM / 纯函数**:四判据 + 封死豁免全部由 spot 行(price/high/amount/name)+ 日线帧(closes/amounts)+ frozen config 决定;新闻/LLM 不进任何阈值。
- **只增卖压、永不放松保护**:E3 仅在无保护性止损/thesis 触发时评估;SEALED_LIMIT_HOLD 只抑制**酌情**落袋(TP+E3),不触碰任何保护性退出与硬顶;None config 即旧行为。
- 单一构造点 / 14-check / 飞书人工 / 熔断 ≤5 单/日 / 单次 ¥50k / T+1 / 禁跌停 SELL / N-005 import 隔离:不变。
- 自进化 7 禁不触碰;阈值重校准走 D1/P2-2 离线人工 gate。

## 3. 测试锚点

- 四触发各自:命中/未命中边界、盈利前置(LIMIT_BREAK 无)、1/3 取整 + ¥50k clamp + 不足 1 手跳过。
- SEALED:封死价位抑制 TP+E3;WEIGHT_TRIM 不受抑制;开板回落转 LIMIT_BREAK。
- 优先级:保护性止损/thesis 在场时 E3 不评估;族内 LIMIT_BREAK 胜 SURGE_FADE。
- 长持(long_term_hold_codes)跳过 E3。
- limit ratio:30* 20% / ST 5% / 默认 10%。
- None config = v9 bit-for-bit;config_hash 含/不含 strength 不同;record 写 day_high/limit_up/阈值。
