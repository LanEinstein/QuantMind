# P0-7 修订 — 2026-06-03 盘中止损阈值按个股波动分位自适应(D1-a 试点:drawdown_threshold)

> **修订基准**: [P0-7 风险/仓位](./P0-7-risk-and-positions.md) + [P0-10-amendment-line2-2026-05-30](./P0-10-amendment-line2-2026-05-30-take-profit-and-weight-trim.md)(盘中触发系数)
> **设计依据**: [`docs/research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md`](../research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md) **D1**(本 amendment = D1 的第一步试点 D1-a)
> **修订日期**: 2026-06-03
> **决策人**: owner(2026-06-03「止盈/止损不能写死,要据历史波动定」+ AskUserQuestion 选 A;后续「先做 drawdown_threshold 个股波动分位试点」)
> **性质**: 决策边界 + 功能(amendment-first;代码随后 TDD + codex-review)

## 0. 触发与意图
盘中 `DRAWDOWN_STOP`(日内 `(现价−昨收)/昨收 ≤ −drawdown_threshold`)当前用**固定 5%** —— 对高波动股太紧(正常摆动即触发告警疲劳),对低波动股太松(异常下挫才该止损时已大幅回撤)。owner 要求阈值**据个股历史波动确定性派生,不写死**。本 amendment = **试点**:仅把 `drawdown_threshold` 改成个股 |日收益| 分位派生;其余系数(atr_stop_mult / r_multiple / max_add_drawdown_pct 等)留 D1 后续。

## 1. 决策

### 1.1 drawdown_threshold = 个股 |日收益| 分位 × 倍数,clamp 到 [floor, ceiling]
新增纯模块 `backend/monitoring/intraday_calibration.py`:
- `derive_drawdown_threshold(closes, config) -> float | None`:取个股 **日线收盘历史**(持久化日线帧,PIT)算逐日 |收益|,取 `percentile`(默认 0.90)分位作"该股一次异常日内移动"的尺度,× `multiplier`(默认 1.5)→ `clamp(floor=3%, ceiling=12%)`。历史不足(< `min_history`)→ 返 `None`,调用方**回退固定 5%**(不放松、不崩)。
- **META 参数**(window/min_history/percentile/multiplier/floor/ceiling)= frozen `DrawdownCalibrationConfig`,**runtime-immutable**(同 `IntradayTriggerConfig`);只离线重校准(P2-2 shadow + 人工 gate + git + restart)。
- 纯函数、确定性、可 PIT replay(同 closes + 同 config → 同阈值);**零 LLM**;import-clean(N-005,禁 `backend.{llm,agents,agents_team,mirofish}`)。

### 1.2 接线 + 默认 OFF
`evaluate_intraday_sell_intents` 新增 `drawdown_calibration: DrawdownCalibrationConfig | None`:
- `None`(默认)→ 用固定 `cfg.drawdown_threshold`(**与 v4 bit-for-bit**)。
- 提供 → 逐码 `derive_drawdown_threshold(closes)`,得则用个股阈值、不得(历史不足)回退固定值。
- env `QUANTMIND_LINE2_ADAPTIVE_DRAWDOWN_ENABLED`(默认 **OFF**,仿 W-004);main.py 开时传 `DrawdownCalibrationConfig()` 否则 `None`。
- `FEATURE_CODE_VERSION` v4→v5(maths 改;空/None = v4 bit-for-bit)。

### 1.3 PIT 可复现(硬红线,不破)
- runner `_compute_config_hash` **纳入** `drawdown_calibration = {version, config}`(`intraday_calibration.FEATURE_CODE_VERSION` + asdict)→ 写进 `IntradayTriggerManifest`:决策时用的校准参数(含是否启用)+ **派生 maths 版本**被 pin,离线 `replay` 同帧同 hash 重算 bit-exact;参数**或派生逻辑**变即 hash 变 → 陈旧 manifest fail-closed(codex P2:只 bump 模块版本而 dataclass 值不变时,hash 仍须变)。
- **记录实际触发阈值**:`IntradaySellIntent` 新增 `effective_drawdown_threshold`,`DRAWDOWN_STOP` 分支填当 tick 实际用的(自适应或固定)阈值;`_sell_record` 的 `threshold_params['drawdown_threshold']` 用它(非静态 config)→ 自适应收紧到 3% 触发的 −4% 止损被**如实记成 3%**,replay/audit 不会因记成 5% 而误拒真信号(codex P2)。
- 阈值仅派生自**持久化日线 closes**,无外部/LLM 输入。

## 2. 红线(保留 / 变更)

**保留不变**:
- **config runtime 不可改**:META 参数 frozen + pinned,只离线校准 + 重启(P0-7/§2.4)。
- **PIT 可复现**:派生自持久化 PIT closes + config-hash 入 manifest(R0 §3);`FEATURE_CODE_VERSION` v4→v5 fail-closed。
- **零 LLM 决策 / RiskEngine 纯函数 / monitoring import 隔离(N-005)/ 单一构造点不变**:校准纯量化,SELL 仍经 `assemble_monitoring_plan` + 14-check + 飞书人工。
- **自进化 7 禁 + 人工 gate**:重校准走 P2-2 离线 + 45 日 shadow + 人工 gate,**永不**运行时自动调参 / LLM 调参。
- **熔断/止损不放松**:`DRAWDOWN_STOP` 仍是保护性止损;clamp `ceiling=12%` 防校准把止损放得过宽;`floor=3%` 防过紧churn。日内涨跌停/三连等 P0-7 仓位红线不涉及、不变。
- **场外信息严禁进数值阈值**:阈值只由个股历史波动派生;新闻/LLM 对未来的推测只 advisory + 人工 gate(owner 选 A 的核心约束)。

**变更**:
- `DRAWDOWN_STOP` 阈值:固定 5% → 个股 |日收益| 分位派生(env 开时);`evaluate_intraday_sell_intents` 新增 `drawdown_calibration` 参数;新模块 `intraday_calibration.py`;`FEATURE_CODE_VERSION` v4→v5;config_hash 纳入校准参数。

## 3. 范围限定（不在本 amendment）
- **仅** drawdown_threshold;atr_stop_mult / r_multiple / tranche_fraction / max_add_drawdown_pct / breakdown_tolerance 等其余系数留 D1 后续(各自 amendment + shadow)。
- **不**做 regime 条件化(dossier D1 (ii);后续)。
- **不**改 pinned-artifact 文件化(本试点用 frozen dataclass code-pinned,与 IntradayTriggerConfig 一致;YAML 工件化留后续若需)。
- 止盈现金再投复利语义留后续。

## 4. 验证
- TDD:`intraday_calibration` —— 分位+倍数+clamp / 高波动股→更宽阈值、低波动股→floor / 历史不足→None / 非有限·非正收盘过滤 / 确定性(同输入同输出)。`intraday_triggers` —— `drawdown_calibration=None` = v4 bit-for-bit / 提供时高波动股不被固定5%误触发、低波动股按 floor 触发 / 历史不足回退固定值。runner —— config_hash 纳入校准参数(启用与否 hash 不同)。
- 全量 pytest(`FEISHU_INTERACTIVE_ENABLED=false`)+ ruff + `scripts/redline-check.sh`(N-005/M-004)全绿;codex-review 修完 P0/P1/P2 再 commit。
- shadow:启用前 45 日 shadow 对比固定 5% vs 自适应的止损触发频次/收益曲线,owner 飞书 gate 后才开 env。
