# P0-3 修订 — 2026-05-27 Line-1 BUY 限价改实时盘口 + 价格笼子(非破坏式)

> **修订基准**: [P0-3 InstructionPlan 严格 schema + 飞书纯文本模板](./P0-3-instructionplan-schema-feishu-text.md)
> **关联**: 同日 P0-7-amendment(check#02 cage 子校验)/ P0-8-amendment(Line-1 接实时主备)/ R0 §4 单一构造点
> **修订日期**: 2026-05-27(U-E2 / 缺口 4 落地)
> **触发**: Owner 审 002747 BUY 信号(2026-05-26)提出缺口 4:`InstructionPlan.limit_price` 取自 **T-1 EOD 收盘价**
> (`line1_context_provider` `round(lead.last_price, 2)`),不是实时盘口,也不受连续竞价价格笼子约束 → 可能给出
> 券商会废单的限价。Owner 决策:缺口 4 = **参考价 + 建议区间 + 限价上限(价格笼子内)**;限价取实时盘口派生。
> 经 plan mode + 3 轮 codex + owner 拍板。

## 1. 修订前(P0-3 原锁定)

- `InstructionPlan.limit_price` 是 Builder 写的确定性数值字段(LLM 永不可写,P0-3 §2 红线 12 / P0-10)。
- Line-1 provider 取 `limit_price = round(lead.last_price, 2)` —— `lead.last_price` = 筛选用 **T-1 EOD 收盘价**。
- 价格合理性仅 RiskEngine check#02 板内涨跌停带(±10%/±20% vs prev_close);**连续竞价价格笼子(比卖一)全栈未建模**。

## 2. 修订后(本 amendment 锁定)

### 2.1 `limit_price` 由**实时盘口 + 价格笼子**确定性派生(单一构造点不破)
Line-1 provider `build_lead_context`(改 async)在产出 BUY plan 前:
1. 取**双源实时 last**(adata + akshare;divergence ≤0.3% / staleness ≤5s,见 P0-8-amendment);
2. 取**主源五档盘口 best_ask(卖一)**(`get_stock_orderbook`);
3. `limit_price = backend.risk.price_cage.cage_bounded_buy_limit(last, best_ask, board, cage_tolerance_pct)`
   = `floor_to_cent( min(last×(1+cage_tolerance_pct), 卖一价格笼子上限) )`,笼子上限 = `max(卖一×1.02, 卖一+10×tick)`(主板/ETF)或 `卖一×1.02`(创业/科创)。
4. `volume` 用 `max_compliant_buy_volume()` 按 **cage 限价**确定性重算(notional 用真实下单价)。

**limit_price 仍是 Builder/provider 写的确定性数值,LLM 永不参与**(P0-3 §2 红线 12 + P0-10 不破)。新增的派生输入(last/best_ask)来自数据层取数,经纯函数 `price_cage`(零 IO / 零 LLM)→ 数值。R0 §4 单一构造点不破:`InstructionPlan(` 构造点仍仅 model + builder。

### 2.2 限价 = **限价上限(cage ceiling)**;飞书呈现保持纯文本模板
`render_buy_signal` 7-段体内 `限价: <money> CNY` 现展示的是**价格笼子内限价上限**(`cage_bounded_buy_limit` 输出)。
模板结构、标签、行序**不变**(P0-3 §2 锁定的 snapshot 模板不破);仅数值来源从 T-1 收盘价改为实时 cage 限价。
(缺口 4 的"参考价 + 建议区间"判据段属 U-E4 显示增强,见同期看板;本 amendment 仅锁限价来源。)

### 2.3 实时盘口不可用 → **DEGRADED 非可执行**一等结果(绝不在 T-1 收盘价上路由真 BUY)
无双源新鲜 last / 单源 / divergence 超限 / stale / 缺 best_ask → provider 返 `Line1QuoteDegrade`,
runner 渲染**结构上不可执行通知**(`render_non_actionable_quote`,header「非交易参考 · 不可下单」,
**无 instruction_id / 无回报模板 / 无 limit_price 字段**,复用 `render_no_compliant_trade` 形),分类
`Line1Outcome.QUOTE_DEGRADED`,跳过辩论,篮子顺延下一只。**永不**用 last / T-1 收盘价兜底发真 BUY。

## 3. 不变量(本 amendment 不触碰)

- `instruction_id` 严格正则 / HOLD 不路由不发飞书 / `parse_ok=False` 强制 HOLD / 状态机 / frozen strict extra=forbid。
- 飞书消息必经 `renderer.py`(防注入);非可执行通知同样经 renderer,纯文本 + `_single_line` 防伪头。
- LLM positive list 4 类不扩;limit_price / volume / side 仍非 LLM 字段。

## 4. 落地

- 代码:`backend/models/market.py`(`StockOrderbook`)/ `backend/data/market_data.py`(`get_stock_orderbook` + `get_stock_realtime_dual`)/ `backend/services/line1_context_provider.py`(async + cage 派生 + DEGRADED)/ `backend/orchestration/line1_runner.py`(`QUOTE_DEGRADED` + `Line1QuoteDegrade`)/ `backend/integrations/feishu/renderer.py`(`render_non_actionable_quote`)。
- 测试:`tests/test_market_data.py` / `tests/services/test_line1_context_provider.py` / `tests/orchestration/test_line1_runner.py` / `tests/test_feishu_buy_signal.py`。
- 任务:plan.html U-E2。
