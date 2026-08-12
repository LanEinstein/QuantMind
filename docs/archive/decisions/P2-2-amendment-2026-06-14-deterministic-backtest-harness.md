# P2-2 修订 — 2026-06-14 确定性回测 harness 新模块 `backend/backtest/`(P1-0/P1b)

> **修订基准**: [P2-2-amendment-2026-05-24 主动发现 + 知识图谱](./P2-2-amendment-2026-05-24-active-discovery-knowledge-graph.md) + [P2-2-amendment-2026-06-12 sim 客观晋升](./P2-2-amendment-2026-06-12-sim-objective-promotion.md)
> **关联**: 自进化 dossier §3.4 + §8.1(OSS 借鉴:nautilus/zipline/qlib/Lean/vectorbt)+ §8.3①②④ + §8.4(codex 对抗审查)+ R0 §3(PIT)/§5(单一构造点)
> **修订日期**: 2026-06-14
> **触发**: 量化参数进化环需「给定参数 + PIT 窗口 → 逐日重放确定性策略 → 产权益曲线 + daily PnL + AcceptanceReport + anti-gaming」的回测引擎。实测确认此引擎**不存在**(只有 `simulate_harsh_fill` 单笔 / `compare_equity_curves` 差分 / `evaluate_promotion` 判官等零件)。`strategy_evolution` import 隔离禁其 import 策略模块 → 须新建外置模块。

## 1. 修订前

- 无确定性策略回测引擎;`ChallengerReplayer`(`shadow_chain.py:361`)仅 Protocol。
- `harsh_fill_model`(撮合零件)/`backtest_oracle`(差分)/`acceptance_report`(指标)/`marketdata_snapshot`(PIT replay)各自独立。
- `strategy_evolution` import 隔离:禁 `backend.{api,broker,risk,llm,agents,mirofish,data}`,可用仅 `knowledge_graph`+`marketdata_snapshot`+rqalpha。

## 2. 修订后(新模块 `backend/backtest/`)

### 2.1 模块归属与 import 隔离(新红线)

- **新建 `backend/backtest/`**(确定性回测 harness),**不放进 strategy_evolution**(后者 import 隔离不可破)。
- **可 import**:`backend.candidate_selector` / `backend.slot_portfolio` / `backend.monitoring` / `backend.marketdata_snapshot`(PIT 取数)/ `backend.strategy_evolution.harsh_fill_model`(撮合零件)/ `backend.services.acceptance_report`。
- **严禁 import**:`backend.{llm,agents,mirofish}`(P1 不回放 LLM)+ `backend.{api,broker}`(harsh_fill 已 broker-free,实盘镜像不碰)。**AST 契约测试钉死**(镜像 strategy_evolution 既有契约)。
- **零 LLM、test-time/offline only、永不入实时路径**(新 redline `[BACKTEST]`)。`strategy_evolution` 只消费 harness 输出(纯数据 `PromotionInputs`),保持 `evaluate_promotion` 纯净;dispatcher(`backend/services`)接线。

### 2.2 事件循环架构(OSS 借鉴,clean-room 重写零 vendor)

- 主循环 = **nautilus 单调 `ts_init` 事件循环**(时钟在数据进 exchange 前推进,look-ahead 物理不可能)+ **zipline「待决单→下一 bar 成交」屏障**(bar T 的单 T+1 成交 = T+1 by construction)。**拒向量化**(路径依赖:T+1、依赖权益的 ≤5 槽轮动、部分成交)。
- 撮合复用 `harsh_fill_model`(harsh-or-equal vs 实盘);摩擦按 Lean 单工厂组织 `cost_calculator`(§2.7);补 **qlib 涨跌停方向门 + 整手向下取整**。
- PIT 强制:**qlib `Ref` lint**(看未来=bug,进 redline-check/AST)+ Lean frontier 时钟纯函数 + `marketdata_snapshot` as-of;harness 内**无 wall-clock、无网络、无 RNG**(或种子化)。
- 可复现:vectorbt `execute_order_nb` 纯不可变 reducer 形态 + 确定性排序(禁 set/dict 迭代序依赖)+ pin 版本 + `OMP_NUM_THREADS=1` + 钱用整数分/Decimal。

### 2.3 事件节奏奇偶性契约(codex J1,本 amendment 锁定)

- **Line-2 盘中 30s 监控(止损/止盈/轮动让位)= 正式归类「非-alpha 保护性风险监控」,不进自进化环**(owner 卡片 2026-06-14)。理由:日线回测无法忠实重放 30s 事件流 → 日线近似 = 未校验的新决策层。
- Line-2 由**独立 invariant 测试**守护(「只紧不松」单调已锁;protective 行为不变),**其参数不走参数晋升通道**;harness 不为 Line-2 出场做日线近似来喂晋升。
- **进环的参数仅限日线节奏可忠实回测者**(P1 首批 = selector/allocation 权重,见 `P2-2-amendment-2026-06-14-quant-param-evolution-loop`)。

### 2.4 双 lane oracle + 不变量 + 决策定点化(codex J2/J3/J4)

- **Lane-1 订单流对账 oracle**:rqalpha 重放主引擎产出的订单流,离散成交/记账**整数零容差**(`R-002-amendment-2026-06-14`)。
- **Lane-2 golden-vector 策略决策 oracle**:在主引擎、pinned runtime、**定点比较**下校验 features/signals/risk-decisions/order-intents 金标准向量(策略逻辑的独立校验,补 rqalpha 降级后的空缺)。
- **封闭式不变量断言**(破 N=2 共因盲区):现金守恒(Σ买入+Σ卖出+期末=期初,整数零容差)/ 持仓守恒 / 费用=显式公式重算 / 单股≤15%·总仓≤70% 每权益点重验 —— 不依赖框架版本。
- **主引擎决策阈值全定点/整数域**(钱到分/价到分/量到股/比率定精度)+ **新 redline lint**:决策路径裸 `float ==`/`<`/`>` 阈值比较须经 `decision_compare(a,b,ulp_guard)` 包装(杀 NEP 50 跨版本翻转)。

### 2.5 golden replay 地基(P1-0,codex 头号、最先)

- **golden replay 测试**:用真实跑过的实盘日(`BrokerSnapshot` 原始字节 + `audit_events` + `data/line1_frames` + `watchlist_market_snapshots` + equity 点)重放,断言 harness 权益曲线**贴合实盘记录**(「同源」唯一实证 + 回测可信地基)。**不过此关,后续统计门一门不加。**

## 3. 实施与门禁

- 本 amendment = 边界文档(无代码)→ docs 例外。**实施(P1-0 + P1b)** commit 前 codex-review + 全量 pytest + ruff + redline(`[BACKTEST]` import 隔离 + `Ref` lint + `decision_compare` lint)+ AST 契约。TDD 对抗先写:harness import LLM/broker → AST 拒;负 `Ref` → lint 拒;golden replay 不贴合 → fail;不变量违反 → DIVERGENT。
- **依赖 P1-DATA**(无历史数据无法跑全窗口回测;golden replay 用现有实盘日可先行)。

## 4. 红线清单

1. `backend/backtest/` import allowlist(可:candidate_selector/slot_portfolio/monitoring/marketdata_snapshot/harsh_fill_model/acceptance_report;禁:llm/agents/mirofish/api/broker);**零 LLM、test-time/offline、永不实时**(redline `[BACKTEST]` + AST)。
2. **Line-2 盘中 = 非-alpha 风险监控,不进环**;只日线节奏参数进环。
3. 双 lane oracle(订单流对账 + golden-vector 决策)+ 封闭不变量(破 N=2);主引擎决策阈值定点化 + `decision_compare` lint。
4. PIT 强制(`Ref` lint + as-of);可复现(纯 reducer + 确定性排序 + pin + 单线程 + 整数钱)。
5. golden replay 地基先于一切统计门。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG;plan.html P1-0/P1b 任务。
</content>
