# P0-7 修订 — 2026-05-27 集合竞价时段判定(非破坏式)+ MockBroker 撮合诚实边界

> **修订基准**: [P0-7 风险红线 — 仓位 / 熔断 / universe / LLM 不可改](./P0-7-risk-redlines-position-circuit-universe-llm-immutability.md)
> **关联**: P1-2.C(MockBroker 撮合增强) / R0 双线重构总纲
> **修订日期**: 2026-05-27
> **触发**: Owner 审 002747 BUY 信号(2026-05-26)提出 5 真实可交易性缺口。缺口 1:系统**未建模集合竞价**
> (`is_trading_hours` 仅连续竞价 09:30–11:30 / 13:00–15:00),且 MockBroker 不模拟集合竞价撮合 / 五档盘口。
> 研究 dossier:[`docs/research/a-share-trading-rules-2026-05-27.md`](../research/a-share-trading-rules-2026-05-27.md)。
> 经 plan mode + 3 轮 codex + owner 拍板。

## 1. 修订前(P0-7 / P1-2.C 原锁定)

- `backend/utils/trading_hours.py` `is_trading_hours` 仅连续竞价时段;无集合竞价判定。stdlib-only,被 `backend/risk/engine.py` import(check#07 trading_time)。
- Line-1 cron = 09:35 mon-fri(连续竞价内),消费 T-1 EOD 帧。
- P1-2.C:MockBroker `_fill_order` ALL_OR_NONE + at-fill 涨跌停 recheck + 分板滑点 + 深市过户费(simulation_auto 自动撮合路径)。

## 2. 修订后(本 amendment 锁定)

### 2.1 Line-1 cron 维持 09:35 连续竞价(不改 09:25 开盘集合竞价撮合)
缺口 4 的实时盘口限价(见 P0-3/P0-8 同日 amendment)在 09:35 连续竞价内取价并受价格笼子约束,
已能产生可执行买点;**不引入**开盘集合竞价(09:25)撮合机制(避免建模单一价撮合/开盘价生成的复杂度)。
"竞价抢筹"等玩法仅作信号输入参考,不构成下单时机改动。

### 2.2 `trading_hours.py` 新增**非破坏式**集合竞价判定 predicates(stdlib-only)
新增、不改 `is_trading_hours`(故 RiskEngine check#07 trading_time **语义完全不变**):
- `is_opening_call_auction(now)` → 09:15–09:25(交易日)
- `is_closing_call_auction(now)` → 14:57–15:00(交易日)
- `is_call_auction(now)` → 开盘 ∪ 收盘集合竞价
- `market_phase(now)` → 枚举 `MarketPhase`{CLOSED, PRE_OPEN_AUCTION, CONTINUOUS_AM, LUNCH_BREAK, CONTINUOUS_PM, CLOSING_AUCTION, POST_CLOSE}

**约束保持**:模块仍 stdlib + `holiday_loader` only,**不得** import `backend.{llm,agents,mirofish,data}`
(`backend/risk/engine.py` 可继续 import `is_trading_hours`,隔离红线不破)。新增 predicates 供审计/未来用,
**当前不接入** RiskEngine 任何 check(check#07 仍只调 `is_trading_hours`)。

### 2.3 MockBroker 撮合诚实边界(P1-2.C scope 声明,不改现有撮合)
**明确声明不建模**(MVP 不做,诚实边界,写入研究 dossier §6):
- 集合竞价撮合(09:15–09:25 / 14:57–15:00):不模拟单一价集合竞价撮合 / 开盘价·收盘价生成机制。
- 五档盘口排队 / 按档部分成交深度。
- 价格笼子的集合竞价版本(仅建模连续竞价版本,见 P0-3 同日 amendment;Line-1 09:35 适用连续竞价规则)。

理由:feishu_interactive 人工执行路径由真人/真券商撮合,系统只镜像回报(缺口 5);simulation_auto 自动撮合
维持 P1-2.C 现状(连续竞价语义)。集合竞价撮合对本 MVP 非必需。

## 3. 不变量(本 amendment 不触碰)
- `is_trading_hours` 语义、RiskEngine 14-check(含 check#07/#12)、纯函数无 IO、分板涨跌停、滑点/过户费、ALL_OR_NONE — 全不变。
- `backend/risk/` 与 `trading_hours.py` 的 stdlib-only 隔离红线不变。
- 永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策字段 / 单一构造点 / 人工 gate — 全留。

## 4. 影响的代码
- `backend/utils/trading_hours.py`(新增 predicates + `MarketPhase` 枚举,非破坏式)。
- `docs/research/a-share-trading-rules-2026-05-27.md`(研究 dossier,撮合诚实边界)。
- 测试:集合竞价边界单测 + `is_trading_hours` 不回归 + stdlib-only 隔离断言。
