# P0-7 修订 — 2026-06-03 盘中止损阈值 regime 条件化(D1-b:熊市收紧 drawdown 阈值)

> **修订基准**: [P0-7-amendment-2026-06-03-adaptive-intraday-thresholds](./P0-7-amendment-2026-06-03-adaptive-intraday-thresholds.md)（D1-a 个股波动分位自适应 drawdown_threshold）
> **设计依据**: [`docs/research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md`](../research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md) **D1 (ii) regime 条件化**
> **修订日期**: 2026-06-03
> **决策人**: owner（『止盈止损据历史 + 综合场外信息对未来的推测』+ 选 A『红线内自适应』+『继续按计划推进』；regime = 对未来推测的**确定性**红线安全代理）
> **性质**: 决策边界 + 功能（amendment-first;代码随后 TDD + codex-review）

## 0. 触发与意图
D1-a 让 drawdown 止损阈值按**个股**波动自适应。本步加**市场 regime**维度:用已有确定性 `classify_regime(index_closes)`(纯量化、从基准指数历史派生)作"对未来的推测"的**红线安全代理** —— **熊市 regime 收紧 drawdown 止损**(更快止损、保存资本)。这是 dossier D1 (ii) 的第一步;**严禁** LLM/新闻进数值阈值(owner 选 A 核心约束),regime 信号纯由市场数据确定性派生。

## 1. 决策

### 1.1 熊市收紧:`is_bear → drawdown 阈值 × bear_multiplier`
- `DrawdownCalibrationConfig` 加 `bear_multiplier: float = 0.8`(熊市收紧 20%);META 参数 frozen runtime-immutable,只离线重校准(P2-2 shadow + 人工 gate)。
- `derive_drawdown_threshold(closes, config, *, is_bear=False)`:`is_bear` 时 `raw × bear_multiplier`,再 clamp 到 `[floor, ceiling]`(收紧不破 floor 3%,防过紧 churn)。`is_bear=False`(非熊/未启用)→ 与 D1-a bit-for-bit。
- **解耦不 import**:`intraday_calibration` 收 `is_bear: bool`(非 `MarketRegime` 枚举),保持模块零 `backend.*` 子包 import(纯 stdlib);regime→bool 的判定在 `intraday_triggers`(已 import `MarketRegime`)。

### 1.2 接线 + 默认 OFF + 依赖 D1-a
- `evaluate_intraday_sell_intents` 加 `regime: MarketRegime | None`;`is_bear = regime is MarketRegime.BEAR`,传 derive。
- runner 当 `QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED=1` 时 `classify_regime(index_closes)` 算 regime 传入,否则传 None。
- **U-D3 接线(codex P2,前置必需)**:`main.py` 的 `index_closes` 原**硬编码 `()`** → `classify_regime` 恒 NEUTRAL → regime 特性(D1-b **及既有 ADD 熊市禁补**)恒失效。现经纯函数 `_observable_index_closes` 从持久化 `index_prices`(15:30 日线 cron)读基准 000300 日收盘:**(a)** 只取 **finite-positive** 收盘(`get_index_history` 把缺失 coerce 成 0,杂 0 会拉低 MA 误判 BEAR);**(b)** 只取 **≤T-1**(PIT 可观测;盘中今日收盘未出 + 吸收 UTC↔上海日界;丢未来回填行);**(c)** 最新收盘**陈旧**(> 15 日,如 cron 长期失败)→ `()` → NEUTRAL;全程 **fail-open** 不阻断 tick。**(d)** `BENCHMARK_BACKFILL_DAYS` 5→**60**(单次 cron 回填 ≥21 交易日收盘,否则 fresh/reset `index_prices` 要数周才够 classify;codex P2)。**副作用(强调)**:这同时**激活了既有设计的『ADD 熊市禁补』**(此前因数据缺失静默失效)—— 方向严格保守(熊市少买、永不多买),属落地已锁设计(`add_position` 红线『熊市禁补』),非新决策;owner 周知:熊市 regime 下系统不再建议补仓(更安全)。
- **依赖**:regime 条件化是**对个股自适应阈值的精炼** —— 仅当 D1-a(`QUANTMIND_LINE2_ADAPTIVE_DRAWDOWN_ENABLED=1`,提供 calibration)也开时,derive 才被调用、regime 才生效;只开 regime 不开 D1-a → derive 返 None(回退固定 5%)→ regime 无效(文档化)。
- `FEATURE_CODE_VERSION` intraday_triggers v5→v6 + calibration v1→v2(maths 改;`is_bear=False`/未启用 = 前版 bit-for-bit)。

### 1.3 PIT 可复现(硬红线,不破)
- regime 由**持久化基准指数 closes** 确定性派生(PIT 可复现);`bear_multiplier` 在 `DrawdownCalibrationConfig`(已入 config_hash);新增 `regime_drawdown_enabled` flag 入 runner `_compute_config_hash`(决定是否应用 regime)→ 启用与否 hash 不同 → 陈旧 manifest fail-closed。
- 最终(含 regime 调整后)阈值经 D1-a 已建的 `IntradaySellIntent.effective_drawdown_threshold` **如实落 record**(贯穿全部 sell 构造)→ replay/audit 重算 bit-exact。

## 2. 红线（保留 / 变更）
**保留不变**:零 LLM(regime 纯市场数据派生,严禁 LLM/新闻进阈值)/ config runtime 不可改(frozen + pinned + 重启)/ PIT 可复现(regime 自 PIT 指数 + flag 入 hash + 实际阈值入 record + 版本 fail-closed)/ RiskEngine 纯函数 / 单一构造点 / monitoring import 隔离(`intraday_calibration` 仍零 backend.* import)/ 自进化 7 禁 + 人工 gate / 止损不放松到危险(clamp floor 3% 防过紧、ceiling 12% 不变;熊市只会更紧不会更松)。
**变更**:`derive_drawdown_threshold` 加 `is_bear` + `bear_multiplier`;`evaluate_intraday_sell_intents` 加 `regime`;runner 加 `regime_drawdown_enabled` + config_hash;两 `FEATURE_CODE_VERSION` bump。

## 3. 范围限定（不在本 amendment）
- **仅** drawdown 阈值的熊市收紧;其余系数(atr_stop_mult / r_multiple 等)的 regime 条件化 + 更细 regime 分档(牛/震荡/高低波动)留后续(各自 amendment + shadow)。
- 默认方向 = 熊市收紧 20%(`bear_multiplier=0.8`);whipsaw 风险由 45 日 shadow + owner 离线调参 + clamp floor 承接,owner 验证后才启。

## 4. 验证
- TDD:`intraday_calibration` —— `is_bear=True` 收紧(×0.8)vs False / 熊市收紧不破 floor。`intraday_triggers` —— `regime=BEAR` 下一个非熊市自适应阈值不触发的 drawdown 触发(收紧生效)/ `regime=None` = 前版 / 非熊 regime 无收紧。runner —— config_hash 含 `regime_drawdown_enabled`。
- 全量 pytest + ruff + redline(N-005/M-004)全绿;codex-review 修完 P0/P1/P2 再 commit。
- shadow:启用前 45 日 shadow 对比 regime-条件化 vs 不条件化的止损频次/whipsaw/收益曲线,owner 飞书 gate 后才开 env。
