# P0-10 (Line-2) 修订 — 2026-06-03 长期持股(intact thesis)豁免强制止盈/超配减仓

> **修订基准**: [P0-10-amendment-line2-2026-05-30](./P0-10-amendment-line2-2026-05-30-take-profit-and-weight-trim.md)（TAKE_PROFIT +1R 分批 / WEIGHT_TRIM 超配减仓,P-005）+ [P0-10-amendment-line2-2026-06-01](./P0-10-amendment-line2-2026-06-01-position-thesis-advisory.md)（PositionThesis;W）
> **设计依据**: [`docs/research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md`](../research/line2-adaptive-stops-and-thesis-gated-takeprofit-design-2026-06-03.md) D2
> **修订日期**: 2026-06-03
> **决策人**: owner（2026-06-03 指令「除有必要长期持股的股票外,要及时止盈落袋为安,利用复利赚钱」+ AskUserQuestion 选 A「红线内自适应 + 接长期持股豁免」+ 豁免范围「豁免止盈+软超配,但保 15% 硬顶」）
> **性质**: 决策边界 + 功能（amendment-first;代码随后 TDD + codex-review）

## 0. 触发与意图
Line-2 盘中确定性 runner(P-005/006/007)对**所有**持仓一视同仁地强制止盈(+1R 分批)+ 超配软减仓(16.5%→13%)。owner 要求:**有长期持有 conviction 的仓位应让利润奔跑、不被强制止盈/软再平衡**;非长期仓照常及时止盈落袋(复利)。"长期 conviction"= 该仓有 **intact(未失效)PositionThesis**(W),确定性可判,零 LLM。

## 1. 决策

### 1.1 长期持股 = thesis 在**全部盘中可观测失效条件**上**确认 intact** 的持仓(确定性派生)
`long_term_hold_codes = {有 PositionThesis 且其【全部盘中可观测失效模板】均"可评估且未触发"的持仓码}`,经新增纯函数 `monitoring.thesis_break.intraday_intact_codes` 在每 tick 新鲜价上判定。
- **关键(codex P2 cycle-3)**:**不能**用「不在 break map 里」当 intact —— `evaluate_thesis_breaks` 对**缺输入**的条件是**跳过**(不计 broken),"不在 break"可能是"没评估"而非"确认完好"。豁免=移除卖压,fail-safe 方向是**「不确认 intact 就不豁免」**(`evaluate_thesis_breaks`「缺数据不造 SELL」的镜像)。
- **盘中可观测条件 = ANCHOR_DRAWDOWN(新鲜价)+ TIME_STOP(持仓交易日)**:两者都必须**有输入(可评估)且未触发**,缺一(如无 holding_days → TIME_STOP 不可评估)→ **不豁免**。
- **SCORE_DECAY(line1_score)结构上盘中无输入**(无盘中重打分,按设计),**显式不作为盘中豁免判据**(由日线路径负责),否则全部 thesis 因 SCORE_DECAY 永不可全评估 → 功能恒失效。
- 无新鲜报价 / 任一盘中条件不可评估或已破 → 不豁免(保守 = 照常止盈落袋)。

### 1.2 豁免范围(owner 选「豁免止盈+软超配,但保 15% 硬顶」)
对 `long_term_hold_codes`:
- **豁免 `TAKE_PROFIT`(+1R 分批)** —— 让利润奔跑。
- **豁免软性 `WEIGHT_TRIM`**(原 16.5% 触发→回 13% 再平衡)。
- **但保 15% 硬顶**:长期持股权重一旦 > **单股 15% 硬红线**(P0-7 仓位三连),仍 `WEIGHT_TRIM` 减到 **≤15%**(非 13%)。即长期持股可自由骑到 15%,但绝不突破集中度红线。
- 实现:`_weight_trim_intent` 新增 `hard_cap_only`;长期持股用 `threshold=target=max_single_stock_pct`(15%),非长期用原 `15%×1.10=16.5%` / `13%`。**硬顶腿减仓量向上取整到整手**(floor 会残留越界,如 16%→15.2%;ceil 保证减后 ≤15%,codex P2),受 T+1 已结算量上限约束。**并 clamp 到单次 ¥50k(check #9 对 SELL 同样适用)**:超额>¥50k 的整笔减仓会被 RiskEngine 拒 + 被当日去重记为"已触发"→ 持仓卡在越界态无重试;故 clamp 成有效的部分减仓(本 tick 真减、后续 session 再减直至 ≤15%,仿 thesis-break exit clamp;codex P2 cycle-2)。软腿(13% 目标)有缓冲、保 v3 行为不动。

### 1.3 保护性止损/退出 不豁免(只移除可裁量卖压,绝不放松止损)
`ATR_TRAILING_STOP` / `DRAWDOWN_STOP`(保护性止损)+ `THESIS_QUANT_BREAK`(thesis 破即不再属长期持股 → 全退)对长期持股**照常触发**。因这三者在评估优先级上**先于** TP/trim,豁免只作用于 else 分支,天然碰不到止损(W-004「只增卖压不放松止损」精神延续)。

### 1.4 默认 OFF + shadow + 人工 gate
env `QUANTMIND_LINE2_THESIS_TAKEPROFIT_EXEMPT_ENABLED`(默认 **OFF**,仿 W-004 `QUANTMIND_THESIS_QUANT_BREAK_ENABLED`)。OFF → `long_term_hold_codes=∅` → 与现状(v3)bit-for-bit 相同。启用前 45 日 shadow 看豁免对收益曲线影响,owner 飞书 gate。**与 THESIS_QUANT_BREAK 解耦**:新增独立 provider 字段 `exempt_theses_by_code`(仅本 env 开时填),`theses_by_code`(驱动 THESIS_QUANT_BREAK)wiring 一字不动。

## 2. 红线(保留 / 变更)

**保留不变**:
- **零 LLM 决策**:豁免依据 = intact thesis(确定性量化阈值判定);LLM 支柱文本不进决策。monitoring import 隔离(N-005)不破。
- **绝不放松保护性止损**:ATR/drawdown/thesis-break 对长期持股照常。
- **单股 15% 硬红线不破**:长期持股仍受 15% 硬顶减仓(本 amendment 显式强化,不削弱 P0-7)。
- **InstructionPlan 单一构造点**:SELL 仍经 `assemble_monitoring_plan` + 14-check + 飞书人工 gate;本改只决定"是否产出 TP/trim 意图"。
- **PIT 可复现**:`evaluate_thesis_breaks` 纯函数;`FEATURE_CODE_VERSION` v3→v4 让陈旧 replay fail-closed。
- **纯加性**:env OFF / 空集 = v3 输出 bit-for-bit。

**变更**:
- `evaluate_intraday_sell_intents` 新增 `long_term_hold_codes` 门:对其内的码豁免 TP + 软 trim,trim 改 15% 硬顶语义。
- `_weight_trim_intent` 新增 `hard_cap_only`。
- `Line2IntradayProvider` 新增 `exempt_theses_by_code` 字段;runner 新增 `_intact_thesis_codes`(fail-open → ∅);main.py 新增 env + 加载(BREAK 或 EXEMPT 任一开即加载 theses,各自填对应字段)。
- `FEATURE_CODE_VERSION` v3 → v4。

## 3. 范围限定（不在本 amendment）
- **不**做 D1(止盈止损系数历史/regime 自适应)—— 另起 `P0-7-amendment-2026-06-03-adaptive-intraday-thresholds`(设计 dossier D1)。
- **不**改保护性止损/补仓逻辑。
- **不**引入"长持白名单"(用确定性 thesis,不另设人工白名单)。
- 止盈现金再投复利语义(dossier 决策点 5)留后续。

## 4. 验证
- TDD(`tests/monitoring/test_intraday_triggers.py`):intact-thesis 码不出 TAKE_PROFIT(+1R 价也不出)/ 不出软 WEIGHT_TRIM(16.5%)/ 但 >15% 出硬顶 trim 减回 15%；broken-thesis 码 TAKE_PROFIT 照常；无 thesis 码 TP+软 trim 照常；intact-thesis 码仍出 ATR/drawdown 止损(止损不受豁免)；空 `long_term_hold_codes` = v3 输出 bit-for-bit。
- runner(`tests/orchestration/test_line2_intraday_runner.py`):env OFF → ∅;env ON → intact 集正确派生;thesis 读失败 fail-open → ∅(不崩 tick)。
- 全量 pytest(`FEISHU_INTERACTIVE_ENABLED=false`)+ ruff + `scripts/redline-check.sh`(N-005 monitoring import 隔离 + M-004 单一构造点)全绿;codex-review 修完 P0/P1/P2 再 commit。
