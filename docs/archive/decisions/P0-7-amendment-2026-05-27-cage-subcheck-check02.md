# P0-7 修订 — 2026-05-27 RiskEngine check#02 价格笼子子校验 + `cage_tolerance_pct` 配置(非破坏式)

> **修订基准**: [P0-7 风险红线 — 仓位 / 熔断 / universe / LLM 不可改](./P0-7-risk-redlines-position-circuit-universe-llm-immutability.md)
> **关联**: 同日 P0-3-amendment(实时 cage 限价)/ P0-8-amendment(Line-1 接实时主备)
> **修订日期**: 2026-05-27(U-E2 / 缺口 4 落地)
> **触发**: 缺口 4 的价格笼子限价**必须有 RiskEngine 权威再校验**(codex 3 轮强调:limit 派生在 provider,
> 但 RiskEngine 是唯一权威拦截层,不能只信 provider 算对)。经 plan mode + 3 轮 codex + owner 拍板。

## 1. 修订前(P0-7 原锁定)

- RiskEngine 14-check,check#02 = `price_reasonability`(板内涨跌停带 ±10%/±20% vs prev_close)。
- `RiskConfig` runtime 不可改 + hot-reload 全禁(P0-7 §2 红线 1)。
- 价格笼子(比卖一)全栈未建模。

## 2. 修订后(本 amendment 锁定)

### 2.1 check#02 新增**命名价格笼子子校验**(折叠进 check#02,**仍 14-check,不加第 15 check**)
`RiskEngine.validate_order` 新增可选参数 `live_quote: CageQuote | None = None`(`CageQuote` = frozen `{best_ask, source}`,
定义在 `backend/risk/price_cage.py`,纯 / 零 IO / 零 LLM)。`_check_price_reasonability` 仅当
**order 是 BUY 限价单 且 live_quote 非空**时,在板内涨跌停带之前先跑笼子子校验:
`price_cage.is_within_cage(limit_price=order.price, best_ask=live_quote.best_ask, board=stock_meta.board)`。
- 笼子子校验**先于** prev_close 带早返跑 → prev_close 缺失也不会绕过笼子。
- 失败复用 check#02 的 `rule_name="price_reasonability"`(**14-check 计数不变**),message 带
  `price_cage_violation` 子原因 token + 来源 provenance(audit 可区分笼子拒单 vs 带拒单)。
- **fail-closed**:缺 board / 缺 best_ask / 非正 best_ask → `is_within_cage` 返 False → 拒单(不猜价、不放行)。
- `live_quote=None`(legacy 7-check / SELL / 监控 / 既有 caller)→ 笼子子校验跳过,语义完全向后兼容。

RiskEngine 仍是**纯函数零 IO**(P0-7 §2 红线 9):best_ask 由 provider(可 import data)取数后以 frozen `CageQuote` 传入;
engine 只读不取。笼子是 BUY 侧 **废单守门**,独立且附加于板内涨跌停带 —— 合规 BUY 必须两者都过。

### 2.2 `cage_tolerance_pct` 入 `RiskConfig.universe`(runtime 不可改)
`UniverseConfig` 新增 `cage_tolerance_pct: float = 0.02`(ge=0),`config/risk.yaml` `universe` 段落同步。
= 限价上限相对实时 last 的最大上浮(`min(last×(1+pct), 笼子上限)`),默认 0.02 = 与 ±2% 笼子带对齐,
防一个过宽/过时的卖一把限价拉到远高于现价。runtime 不可改 + hot-reload 禁用(P0-7 §2 红线 1 不破):
改它 = git diff + amendment + 重启。

### 2.3 已知范围边界(review 采纳,后续任务)
- **Line-2 补仓(ADD)BUY 暂未接 cage**:`_cage_subcheck` 对任何 BUY 都生效,但 Line-2 监控 ADD 路径的 `validate_order`(`instruction_plan_builder.py` 监控装配点)**不传 `live_quote`** → cage 子校验跳过。U-E2 范围 = Line-1 缺口 4;Line-2 ADD 接 cage 需 Line-2 取盘口,属独立后续任务(记此处避免误判 Line-2 ADB 已受保护)。SELL **不**受 cage(BUY 侧守门)正确。
- **实时 spot staleness 诚实限制**:`StockQuote.timestamp` = 抓取时刻(供应商解析模型不暴露逐笔时戳),故 staleness ≤5s 实际校验"抓取新鲜"(配 tz fail-closed)而非交易所逐笔时延;**divergence(双源 last 一致 ≤0.3%)是真正的双源守门**(见 P0-8-amendment §2.2)。

## 3. 不变量(本 amendment 不触碰)

- 仓位三连(单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万)/ 熔断(≤5 单/日 / 日亏 -5% / 连亏 3 / 60min)。
- universe 沪深主板+创业板+ETF / 禁 ST·科创·北交·可转债 / 禁涨停 BUY(check#12)/ 禁跌停 SELL / long-only。
- `RiskConfig` 全锁 + LLM 永不持写引用 + 提议改 YAML 必经人工 review + amendment + 重启。
- RiskEngine 严禁 `import backend.{llm,agents,mirofish,data}`;`price_cage` 同款纯模块(仅 import `backend.risk.stock_meta.Board`)。
- 14-check 计数、各 check 的 rule_name 命名空间不变(笼子是 check#02 子校验)。

## 4. 落地

- 代码:`backend/risk/price_cage.py`(`CageQuote` + 既有 `is_within_cage` / `cage_bounded_buy_limit`)/ `backend/risk/engine.py`(`live_quote` 参数 + `_cage_subcheck` + check#02 特例化)/ `backend/broker/models.py`(`UniverseConfig.cage_tolerance_pct`)/ `config/risk.yaml` / `backend/services/instruction_plan_builder.py`(`AssemblyContext.live_quote` → validate_order)。
- 测试:`tests/test_risk_engine_cage.py`(10 用例:within/at-ceiling/over-cage/缺 best_ask/缺 board/prev_close=None 不绕过/无 live_quote 跳过/SELL 不校验/MARKET 跳过/ETF tick)/ `tests/test_broker_models.py`。
- 覆盖:`backend/risk` 99%(engine 99% / price_cage 100%)。
- 任务:plan.html U-E2。
