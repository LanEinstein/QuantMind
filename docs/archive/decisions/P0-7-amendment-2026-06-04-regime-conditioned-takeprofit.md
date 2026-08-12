# P0-7 修订 — 2026-06-04 止盈倍数 r_multiple 三档 regime 条件化(D1-c)

> **修订基准**: [P0-7 风险/仓位红线](./P0-7-risk-config-immutable-and-position-caps.md) + [P0-7-amendment-2026-06-03-adaptive-intraday-thresholds](./P0-7-amendment-2026-06-03-adaptive-intraday-thresholds.md)(D1-a 自适应框架)+ [P0-7-amendment-2026-06-03-regime-conditioned-drawdown](./P0-7-amendment-2026-06-03-regime-conditioned-drawdown.md)(D1-b regime 先例)
> **关联**: P0-10-amendment-line2-2026-05-30(TAKE_PROFIT 触发引入,r_multiple=1.0 写死)/ `docs/research/line2-adaptive-stops-and-takeprofit-design-2026-06-03.md` dossier D1 / `docs/handoff-d1c-takeprofit-regime-and-sell-logic-2026-06-03.md`(owner 锁定 prompt)
> **修订日期**: 2026-06-04(#68 session 续;handoff 任务 A)
> **决策人**: owner(2026-06-03 handoff 锁定:「熊市/震荡更早止盈落袋、牛市让利润奔跑」;2026-06-04『继续推进』)
> **性质**: 决策边界(止盈**时机**随市场 regime 自适应)。env 门控默认 OFF + 45 日 shadow + 人工 gate;安全地基红线全留。

## 0. 意图与现状

- **现状**:盘中 TAKE_PROFIT(`_take_profit_intent`)= 现价 ≥ `成本 + r_multiple×R`(R=`atr_stop_mult×ATR`)→ 卖 `tranche_fraction`(0.5)锁盈,余仓骑 ATR 移动止损;`r_multiple=1.0` 写死于 `IntradayTriggerConfig`。
- **意图(owner)**:止盈**时机**随 regime 自适应——**熊市/震荡更早落袋**(降 r_multiple → 更小盈利即触发分批锁盈)、**牛市让利润奔跑**(升 r_multiple → 更晚触发)。镜像 D1-b 的 regime 先例,但作用在**主动止盈**而非保护性止损。

## 1. 决策

### 1.1 三档 r_multiple + clamp(`TakeProfitCalibrationConfig`,frozen)

| regime | r_multiple | 语义 |
|---|---|---|
| BULL | `bull_r_multiple = 1.3` | 让利润奔跑(更晚分批) |
| NEUTRAL | `neutral_r_multiple = 1.0` | = 现静态默认(NEUTRAL 数值 = v6 行为) |
| BEAR | `bear_r_multiple = 0.6` | 更早落袋(残余仓位仍有 ATR 移动止损护住) |

- **clamp**:派生值恒夹 `[floor=0.5, ceiling=2.0]`——下界防熊市止盈过早 = 噪声卖出/churn;上界防牛市目标价跑飞 = 止盈名存实亡。
- META 参数 frozen dataclass、runtime-immutable、只离线 P2-2 重校准(shadow + 人工 gate + git + 重启)——与 D1-a `DrawdownCalibrationConfig` 同治理。

### 1.2 纯派生函数(import-clean)

- `intraday_calibration.effective_r_multiple(config, *, is_bull, is_bear) -> float`:纯函数、零 IO;以**两个 bool** 传 regime(模块保持零 `backend.*` 子包 import,不引 `add_position.MarketRegime`,D1-b 同款手法)。
- 防御性优先级:`is_bear` 胜 `is_bull`(两者同真不可能来自 `classify_regime`,防御取保守=更早落袋);两者皆假 = NEUTRAL。

### 1.3 独立 regime 通道(关键接线决策,门控独立性)

- `evaluate_intraday_sell_intents` 新参 `takeprofit_calibration` + **`takeprofit_regime`**(独立于 D1-b 的 `regime` 参数)。
- **为什么不复用 D1-b 的 `regime`**:runner 只在 `QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED=1` 时 classify 并传 `regime`;若 D1-c 复用同一参数,开止盈 regime(而 drawdown-regime env 关)会让已开的 D1-a 自适应阈值**意外**被熊市收紧——破坏两 feature 的 env 门控独立性。独立通道 = 各 env 只影响各自 maths,任一关闭即该路 bit-for-bit。
- runner 在 `takeprofit_calibration` 非 None 时独立 `classify_regime(index_closes)`(同一确定性基准指数派生,PIT 三重守门 `_observable_index_closes` 复用)。

### 1.4 env 门控 + bit-for-bit

- 新 env `QUANTMIND_LINE2_REGIME_TAKEPROFIT_ENABLED`(默认 OFF);main.py 开时传 `TakeProfitCalibrationConfig()`,否则 None。
- `takeprofit_calibration=None` → 用静态 `cfg.r_multiple`(**v6 bit-for-bit**);单 runner 参数(config 即开关,无独立 bool——regime 条件化就是该 config 的全部内容,不同于 D1-b 在既有 D1-a config 上叠 flag)。

### 1.5 PIT 三件套(D1-a/b codex 教训照搬)

- `IntradaySellIntent.effective_r_multiple: float | None`——**贯穿全部 sell 构造**(非仅 TAKE_PROFIT;D1-a 同理:低优先级触发的 record 也须能复现「为什么 TAKE_PROFIT 没先触发」,否则静态 replay 对 bull=1.3 档会误判 TP 漏触发)。
- `_sell_record.threshold_params` 写 `r_multiple` = intent 实际生效值(回退静态 config)。
- runner `_compute_config_hash` 纳 `takeprofit_calibration {version, config}`(含派生 maths 版本,非只参数值)。
- `FEATURE_CODE_VERSION`:triggers v6→v7;calibration v2→v3。陈旧 manifest fail-closed。

## 2. 红线影响(全保持)

- **零 LLM**:r_multiple 仅由 regime(纯基准指数派生)+ pinned config 决定;严禁 LLM/新闻进数值阈值(owner 选 A)。
- **保护性止损零接触**:条件化只作用 TAKE_PROFIT 触发时机;ATR_TRAILING_STOP / DRAWDOWN_STOP / THESIS_QUANT_BREAK / 硬顶 WEIGHT_TRIM 的 maths 与优先级一字不动——牛市延后止盈**不**移除保护(余仓/全仓仍骑 ATR 移动止损)。
- **D2 豁免正交**:长持(intact thesis)本就跳过 TAKE_PROFIT,与本 feature 无交互。
- config runtime 不可改 / 单一构造点 / RiskEngine 14-check / 飞书人工 gate / 熔断 ≤5 单/日 + 单次 ¥50k + 单股 15% / N-005 import 隔离:不变。
- env 默认 OFF + 45 日 shadow + 人工 gate 后才激活。

## 3. 测试锚点

- `effective_r_multiple`:三态各档 / clamp 上下界 / bear 优先 / 两假=neutral。
- `_take_profit_intent` 经 evaluate:同一价位 BEAR(0.6R)触发而静态(1.0R)不触发;BULL(1.3R)不触发而静态触发;`takeprofit_calibration=None` = v6 bit-for-bit;NEUTRAL 数值等于静态。
- `effective_r_multiple` 入全部 intent;`_sell_record` 写实际值(回退静态)。
- runner config_hash:含/不含 takeprofit calibration 哈希不同。
