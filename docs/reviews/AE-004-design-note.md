# AE-004 设计笔记 — `backend/backtest/` 确定性 harness + 双 lane oracle + 封闭不变量(P1b)

> 边界文档:`P2-2-amendment-2026-06-14-deterministic-backtest-harness` §2.1-2.4(无新 amendment)。
> 复用既有件:`golden_replay`(AE-003 同源地基)/`decision_compare`(定点)/`harsh_fill_model`(撮合)/`backtest_oracle`(Lane-1 rqalpha 订单流对账,AE-002)/`pit_export`(PIT 同源)/`candidate_selector`/`slot_portfolio`/`acceptance_report`。

## 模块(全 `backend/backtest/`,clean-room 零 vendor)

1. **`friction.py`** — Lean 单工厂摩擦,逐字镜像 `backend.broker.cost_calculator`(harness 禁 import broker → 重写公式,纯 leaf 取 primitives)。整数分域。佣金 `round(max(gross¢*rate, min¢))`、印花税所有 SELL `round(gross¢*0.001)`、SZ 过户费 `round(gross¢*0.0000341)`、分板块滑点 `round(price¢*(1±bps/1e4))`。`apply_board_slippage` 开关:同源模式 True(broker parity)/harsh 模式 False(harsh_fill 已含冲击,避免双计)。
2. **`portfolio.py`** — 整数分组合账本(复用 golden_replay 的 ReplayPosition/EquityPoint/Fill 语义);`apply_fill`/`mark`(逐日收盘 MTM)。返回全 frozen。
3. **`event_loop.py`** — nautilus 单调 `BacktestClock`(时钟只进,禁时间旅行)+ `DayBar`(open/high/low/close¢ + adv + prev_close¢ + limit_ratio + board)+ `BarSource` Protocol(as-of 取数,注入式;测试喂合成、生产钉 `SnapshotPitExporter`)。涨跌停门由 prev_close¢×(1±limit_ratio) 整数派生。
4. **`strategy.py`** — 日线节奏确定性策略:`ScoreProvider` Protocol(注入,AE-004 测试喂确定性分;AE-005 接真因子)→ `CandidateSelector.select` → `propose_rotation`+churn gates → `DayDecision`(order intents)。**仅日线;Line-2 30s 盘中=非-alpha 不进环(§2.3,不 import monitoring 盘中)**。
5. **`invariants.py`** — 封闭式不变量(破 N=2 共因盲区):① 现金守恒(initial+Σsell_net−Σbuy_net=final,整数零容差)② 持仓守恒(Σbuy−Σsell=final,永不负)③ 费用=显式公式重算 ④ 单股≤15%·总仓≤70% 每权益点重验。任一违反→`Verdict.DIVERGENT`。
6. **`golden_vector.py`** — Lane-2 golden-vector 决策 oracle:pin 的 (date,code,feature/signal/decision/order-intent) 金标准向量,`decision_compare` 定点比对(补 rqalpha 降级后策略逻辑空缺)。
7. **`harness.py`** — 顶层装配 `run_backtest(spec, bar_source, score_provider, configs) → BacktestResult`(纯数据:equity curve + daily PnL + fill_count + anti-gaming〔avg_exposure/signal_count/monthly_turnover〕+ invariant verdict)。`to_acceptance_report`(注入 now + benchmark,映射 AcceptanceReport)+ `to_order_schedule`(导出订单流喂 Lane-1 rqalpha oracle)。**产 PromotionInputs 内容**(实际 pydantic 映射在 AE-005 dispatcher seam,保 backtest 不 import strategy_evolution〔除 harsh_fill_model〕)。

## 事件循环(look-ahead 物理不可能)
zipline 屏障:day T 收盘决策的单 → day T+1 open 成交(T+1 by construction)。每日:① 上日待决单按今日 open 经 harsh_fill(涨跌停门+ADV cap+整手+延迟)成交+friction ② 今日收盘 MTM equity point ③ 不变量重验 ④ 用 ≤T 数据生成新决策→明日待决单。clock 只进,请求 <当前日 bar→raise。

## 红线遵从
- import allowlist(禁 llm/agents/agents_team/mirofish/api/broker;strategy_evolution 仅 harsh_fill_model)→ 既有 AST 契约 glob `backend/backtest/*.py` 自动覆盖新模块。
- **零裸 float 比较**(AST lint 扫所有 Compare 节点的 float 字面量操作数)→ 决策走 `decision_compare`,金额走整数分,阈值比较用 int/Decimal 字面量。
- 永不实时:新增 AST 契约断言 harness 模块零 `datetime.now()`/`time.time()`(确定性/no-wall-clock)。
- PIT:无 wall-clock/网络/RNG;BarSource as-of 注入;Ref lint 既有。

## 测试断言(plan.html)
harness import LLM/broker→AST 拒 / 不变量违反→DIVERGENT / golden-vector 决策定点比对 / harsh-or-equal 撮合。
