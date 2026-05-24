# backend/monitoring/ — 子任务上下文(Phase N MVP / Phase T 全栈)

> 状态:**todo**(Phase N z-score/布林;Phase T 全异动栈)。治理:R0 §1 Line 2 + `P1-7-amendment-2026-05-24`(LLM 触发式)。任务:plan.html N-001..N-005 / T-003。

## 职责
**Line 2 持仓监控**:监控持仓盘面/量能/异动 → 飞书 SELL(含价/量);补仓机会 → 飞书 ADD。

## 本模块红线
1. **纯量化轮询(零 LLM)**;LLM 仅在**触发式异动**(去重 + `max_anomaly_llm_per_day`),写**同一** `llm:usage:{utc_date}` 计数器(防绕过 ¥20 cap)。
2. MVP 仅 z-score/EWMA/布林 + 可选一个无监督检测器;全栈(IsolationForest/HMM/ruptures/OFI)Phase T,**按需加**(精度 > 模型多样性,防告警疲劳);autoencoder deferred。
3. SELL 读 **`available_volume`(T+1 已结算)**,非总持仓。
4. 补仓 = 固定分数(Van Tharp)+ ATR 移动止损;要求 oversold + 量能企稳 + 无破位 + 仓位余量;**禁马丁格尔;熊市(regime)禁补**;经 RiskEngine 14-check + 飞书人工。
5. 停牌持仓 → SELL **干净降级**(非失败订单);suspension 作快照字段;接 `backend.data.suspension`。
6. 所有信号经 `renderer.py`(防注入);走**决策群**(非告警群);`instruction_id` 合规。

## import 隔离
严禁 `import backend.{llm,agents,mirofish}`(异动纯量化)。可用:`backend.{marketdata_snapshot,broker,risk,data}` + ruptures/hmmlearn(Phase T)。LLM 触发式经 `cost_guard` 预留。

## 接口契约(草案)
- `AnomalyDetector.scan(positions, snapshot) -> list[AnomalySignal]`(纯量化)。
- `SellSignal` / `AddPositionSignal` → 经 RiskEngine + renderer。
