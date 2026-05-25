# backend/monitoring/ — 子任务上下文(Phase N MVP / Phase T 全栈)

> 状态:**done(N-001..N-005 MVP)**(z-score/EWMA/布林异动 + 异动→飞书 SELL + 补仓 ADD + suspension 干净降级 + 触发式 LLM 门 + ★MVP gate 双线端到端;全异动栈 IsolationForest/HMM/ruptures/OFI + autoencoder 仍 Phase T)。治理:R0 §1 Line 2 + §8 + [`P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction`](../../docs/decisions/P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md) + `P1-7-amendment-2026-05-24`(触发式 LLM)。任务:plan.html N-001..N-005 / T-003。

## 职责
**Line 2 持仓监控**(确定性、零 LLM 决策路径):监控持仓盘面/量能/异动 → 飞书 SELL(含价/量);补仓机会 → 飞书 ADD。SELL/ADD 方向由确定性检测器派生,**不经 fund_manager / 多 agent 辩论**;经 builder 新增 `assemble_monitoring_plan` 单一构造点 + RiskEngine 14-check + 飞书人工 gate。

## 模块结构(已实现)
| 文件 | 内容 |
|------|------|
| `anomaly.py` | `AnomalyDetector.scan(snapshot, held_codes, signal_id) -> AnomalyScanResult`:纯量化 z-score/量能 z-score/EWMA 控制图/布林突破(各自 self-gate 历史不足返 None);读 K `MarketDataSnapshot` CSV market-frame(与 screener 同源),只消费持仓行 + 写 `SignalInputManifest` 血缘;保守 ≥3σ 默认(精度 > 召回防告警疲劳)。 |
| `sell_signal.py` | `evaluate_sell_intents`(异动 DOWN 价格/EWMA/布林 → SELL 意图;量能单独不触发;读 `available_volume` T+1 已结算)+ `make_sell_context`(构造 SELL `MonitoringAssemblyContext`,`LINE2-MON-` signal_id + `MARKET-` 证据)。 |
| `add_position.py` | `evaluate_add_intents`(四条件:超卖 RSI + 量能企稳 + 无结构性破位 + 仓位余量;**禁马丁格尔**=Van Tharp 固定分数 + 深度水下拒;**熊市禁补**=`classify_regime`==BEAR 阻断)+ `vanthorp_size`(ATR 止损 sizing,close-based ATR 代理)+ `parse_held_series` + `make_add_context`(BUY)。 |
| `degrade.py` | `partition_by_suspension`(`backend.data.suspension.is_suspended` 把停牌持仓移出活跃扫描集 → `PositionDegrade` 干净降级,非失败订单;缺 spot 不误判)+ `anomaly_trigger_key`(`{code}:{kind}` 每日去重键)。 |
| `alerter.py` / `alert_dispatcher.py` | P1-7 评估期告警(非 Line-2 异动;独立)。 |

> Line-2 触发式 LLM:门在 `backend/services/cost_guard.py::reserve_anomaly_llm_slot`(去重 + `max_anomaly_llm_per_day` + 写**同一** `llm:usage:{utc_date}` 计数器);实际 LLM 调用由编排层在本模块**之外**发起(保持 monitoring import-clean)。

## 本模块红线
1. **纯量化轮询(零 LLM 决策)**;LLM 仅在**触发式异动**(去重 + `max_anomaly_llm_per_day`),写**同一** `llm:usage:{utc_date}` 计数器(防绕过 ¥20 cap)。
2. MVP 仅 z-score/EWMA/布林 + 量能 z-score;全栈(IsolationForest/HMM/ruptures/OFI)Phase T,**按需加**(精度 > 模型多样性,防告警疲劳);autoencoder deferred。
3. SELL 读 **`available_volume`(T+1 已结算)**,非总持仓。
4. 补仓 = 固定分数(Van Tharp)+ ATR 移动止损;要求 oversold + 量能企稳 + 无破位 + 仓位余量;**禁马丁格尔;熊市(regime)禁补**;经 RiskEngine 14-check + 飞书人工。
5. 停牌持仓 → SELL/ADD **干净降级**(非失败订单);suspension 作快照字段;接 `backend.data.suspension`。
6. 所有信号经 `renderer.py`(防注入,`render_monitoring_sell` / `render_add_position`,均 `LINE2-MON-` + canonical id 再校验);走**决策群**(非告警群);`instruction_id` 合规。
7. **InstructionPlan 单一构造点**(R0 §4):本模块**不构造** InstructionPlan;SELL/ADD 经 `instruction_plan_builder.assemble_monitoring_plan`(side/volume/limit_price 确定性派生);`debate_round_count=1` = 确定性监控评估轮。SELL 跳过 watchlist 早返(退出不被入场规则困住),ADD(BUY)跑全 5 早返。

## import 隔离
严禁 `import backend.{llm,agents,agents_team,mirofish}`(异动纯量化;`agents_team` 是 Line-1 LLM 辩论路径 `run_shortlist`/`fund_manager`,引入即把多 agent LLM 路径漏回零 LLM 的 Line-2 决策路径 —— codex N-005;redline-check `[N-005]` grep(覆盖 dotted/name-level/relative 三种 import 形式)+ `tests/monitoring/test_module_contract.py` AST 双重守门 + ruff TID251 per-line noqa)。可用:`backend.{marketdata_snapshot,broker,data,risk,services,integrations,models}`(后者经 per-line `# noqa: TID251`)+ ruptures/hmmlearn(Phase T)。LLM 触发式经 `cost_guard` 预留(编排层在本模块外发起实际调用)。

## 测试
`tests/monitoring/`:anomaly(26)+ sell_signal(15)+ add_position(21)+ degrade(8)+ 模块契约/隔离(8)+ ★MVP gate 双线端到端 e2e(3,含 J-005 N 日预演)= 77。新模块覆盖率 94-100%;包覆盖率 ≥80%。`tests/test_cost_guard_anomaly.py` 6(触发式 LLM 门)。
