# P0-10 line2 修订 — 2026-05-30 确定性止盈 + 超配减仓

> **修订基准**: [P0-10 LLM 权限 + 必经 Agent + fail-closed 降级](./P0-10-llm-field-permissions-mandatory-agents-failclose.md)
> **叠加于**: [P0-10-amendment-line2-2026-05-25](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md)(Line-2 = 独立确定性零 LLM 监控路径)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2.0(单一构造点)、§4
> **修订日期**: 2026-05-30
> **触发**: Owner 指出 Line-2 只在**不利时卖**(止损/移动止损),**从不主动锁盈**;赢家涨过权重上限也不减。要求补齐**止盈**与**超配减仓**,目标账户**稳定收益**。AskUserQuestion 锁定 **都补**(R 倍数分批止盈 + 阈值带超配减仓)。

## 1. 修订前(Line-2 原锁定)

- Line-2 盘中 SELL 触发仅:`DRAWDOWN_STOP`(回撤 ≤ −5% vs prev_close)+ `ATR_TRAILING_STOP`(`recent_high − atr_stop_mult×ATR`);另有 ADD(补仓,Van Tharp + 反鞅 + 熊市禁补)。**无止盈、无减仓**。
- 去重键 `(code, side)` 每日一次(`backend/orchestration/line2_intraday_runner.py:418`)。
- `IntradayTriggerKind` = `DRAWDOWN_STOP` / `ATR_TRAILING_STOP`(`intraday_triggers.py:99`)。

## 2. 修订后(本 amendment 锁定)

### 2.1 新增两个确定性 SELL 触发(`IntradayTriggerKind`)

- **`TAKE_PROFIT`(R 倍数分批止盈)**:`R = atr_stop_mult × close_atr(closes, window)`(**复用** `backend/monitoring/add_position.py:259` 同款 R);`live_price ≥ cost_price + 1×R` **且净盈利** → SELL `floor((available_volume × tranche_fraction)/100)×100`(默认半仓);**余仓交现有 `ATR_TRAILING_STOP` 护盘**。sub-1-lot 跳过(不发 0)。
- **`WEIGHT_TRIM`(阈值带超配减仓)**:`weight = pos.volume × live / total_assets`;`weight > 单股cap × (1+trim_band)`(默认 `15%×1.1=16.5%`)→ 减回 `trim_target_pct`(默认 13%,**< cap 防即时复发**);从 settled `available_volume` clamp + 整手。

### 2.2 部分 SELL,不破单一构造点 / 零 LLM

- **无 TRIM side**(`InstructionSide` 仍 `BUY/SELL/HOLD`);止盈/减仓 = **更小的整手 SELL**,走现有 `evaluate_intraday_sell_intents → make_intraday_sell_context → assemble_monitoring_plan → RiskEngine 14-check → render_monitoring_sell → 飞书`。
- 复用 `IntradaySellIntent`(不 applicable 字段置 0,如 `DRAWDOWN_STOP`),`make_intraday_sell_context`/provider/`render_monitoring_sell`/manifest **全不改**;`signal_id` 保 `LINE2-MON-`;`evidence_id = MARKET-{code}-{kind.value}`。

### 2.3 去重键 `(code, side)` → `(code, trigger_kind)`

- 原 `(code, side)` 会让一次**部分止盈**压住同日 later 的**保护性 ATR/回撤止损**(risk-bad:锁了半仓盈利却挡掉余仓的保护性退出)。改 `(code, trigger_kind)`:四类 SELL 各自每日至多一次,**保护性止损永不被止盈压住**。
- 四类优先级 **ATR > 回撤 > 止盈 > 减仓**(风险退出优先);每 code 每 tick 仍 **≤1 intent**;SELL 仍压同 code ADD(止盈在 cost **上**、ADD 在 cost **下**,天然互斥,不震荡)。

### 2.4 止盈 tranche 状态 = ledger 派生、连续持仓 episode(保 replay)

- "本持仓已减半 tranche" = **自上次归零后连续持仓内已有 `TAKE_PROFIT` SELL**。
- 由 **provider**(`backend/services/line2_context_providers.py`,可合法 import broker/ledger)**流式过滤 `broker_events`**(correlation→instruction→`side=SELL` + `evidence_ids` 含 `MARKET-{code}-take_profit`,限连续持仓 episode)算 `take_profit_already_taken: frozenset[code]`,传入**纯**评估器(`backend/monitoring/` 仍 import-clean,**不直连 ledger**)。
- 复用 `backend/services/daily_state_assembler.py:128` 的 stream-and-filter 先例;`decision_ledger`/`broker_events` **闭集不可加事件类型** → 用 `evidence_id` 判别,**不新增 `LedgerEventKind`**。
- **实施风险**:从 events 复原"连续持仓 episode"较脆;若过脆**退化为按交易日去重**(仍 **ledger 派生非内存**,保 replay)—— 触此线**报 owner**。
- **不用内存 `_fired`** 作 tranche 状态(重启即丢,破 replay);`_fired` 仅作单 tick/单日运行期去重。

### 2.5 配置

- take-profit/trim 参数**扩入 `IntradayTriggerConfig`**(已 runtime 不可改 + 已入 replay manifest `line2_intraday_runner.py:275`,新知自动入血缘):`r_multiple=1.0` / `tranche_fraction=0.5` / `trim_band=0.10` / `trim_target_pct=0.13`。
- **单股 cap 从 RiskConfig 读**(`max_single_stock_pct`,单源,不复制)。

## 3. 实施期任务调整(Phase P)

- **P-005**:`IntradayTriggerKind` 加 2 值 + 止盈/减仓并入 `evaluate_intraday_sell_intents`(加 `account` + config + 可选 `take_profit_already_taken` 默认空)+ 优先级 + 去重键改 + runner 接线。
- **P-006**:provider 端 ledger 派生 `take_profit_already_taken` reader + 线程入评估器 + replay 测试。

## 4. 红线清单(本 amendment 之后)

1. 止盈/减仓 = **部分 SELL**,**无 TRIM side**;经现有单一构造点 `assemble_monitoring_plan`;**零 LLM**(确定性派生,数值永不来自 LLM)。
2. `signal_id` 保 `LINE2-MON-`;`evidence_id = MARKET-{code}-{kind}`;新增 `TAKE_PROFIT`/`WEIGHT_TRIM` 供审计/replay 区分。
3. 去重键 `(code, side)→(code, trigger_kind)`;**保护性止损永不被止盈压住**;优先级 ATR>回撤>止盈>减仓;每 code/tick ≤1 intent。
4. 止盈 tranche 状态 **ledger 派生**(连续持仓 episode),**非内存**,保 replay;**不新增 `LedgerEventKind`**(用 evidence 判别)。
5. 单股 cap 从 RiskConfig 读(单源);take-profit/trim 参数入 `IntradayTriggerConfig` runtime 不可改。
6. SELL **仍不熔断**(P0-7);RiskEngine 14-check 仍**独立权威**(部分 SELL 减仓只降暴露,check 5/8 平凡通过)。
7. settled `available_volume`(T+1)计;sub-1-lot 跳过**不发 0**;止盈(cost 上)与 ADD(cost 下)天然互斥。
8. `backend/monitoring/` import 隔离不变(禁 `backend.{llm,agents,agents_team,mirofish}`;ledger 读在 provider 层)。

## 5. 修订记录追加

`docs/plan.html` Phase P 任务 + 修订记录 + SESSION_LOG。CLAUDE.md §2.3 line2 表述补充「+ 确定性止盈(R 倍数分批,+1R 减半,余仓交 ATR 移动止损)+ 阈值带超配减仓(>16.5% 减回 13%);去重键 (code,trigger_kind);止盈 tranche ledger 派生」。
