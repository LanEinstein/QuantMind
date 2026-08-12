# P0-4 修订 — 2026-05-27 缺口5 反转:owner 只报成交价 + 股数,**系统计算含费成本价**

> **修订基准**: [P0-4 ExecutionReportParser 严格正则 + fail-closed 状态机](./P0-4-execution-report-parser-statemachine.md)
> **关联**: P0-5(对账)/ P1-2.C(成本模型 `cost_calculator`)/ P0-1(账户生命周期隔离)/ R0 单一构造点
> **修订日期**: 2026-05-27(U-E3 / 缺口5 设计反转,owner 拍板)
> **触发**: Owner 2026-05-27 明确推翻 #46 AskUserQuestion 的缺口5 锁定。**#46 旧决策**:owner 回报
> 「成本价(含费,每股)+ 股数」,系统永不重算费率。**新决策**:owner **只汇报成交价(每股,不含费)+ 股数**;
> **系统替 owner 计算含费成本价**——owner 给出确切费率:**佣金万分之1.5(0.00015)、不足 5 元按 5 元、双向**,
> 其余(过户费 / 印花税)为交易所标准费率系统已建模,故系统计算结果可与 owner 真实券商对齐。

## 1. 修订前(#46 AskUserQuestion 锁定 — 本 amendment 推翻)

- 缺口5:owner 飞书回报 BUY = 「成本价(含费,每股)+ 股数」(2 数,per-fill 含费均价);系统**永不重算费率**(因当时未知 owner 真实费率,怕系统模型 ≠ 真实券商 → 对账 mismatch)。
- 计划据此设计 `report_schema_version` v1(fill_price+fee)/ v2(cost_price_incl_fee),idempotency 加 cost_price_incl_fee。

## 2. 修订后(本 amendment 锁定)

### 2.1 回报字段:owner 只报**成交价(每股,不含费)+ 股数**(BUY 与 SELL 同)
飞书 FILLED 回报 owner 填**实际成交价(每股)+ 股数**两个数,**不再填手续费**。系统据此确定性计算含费口径:
- **BUY 含费成本价(每股)** = `(gross + 买入侧费用) / 股数`;
- **SELL 净到手(每股)** = `(gross − 卖出侧费用) / 股数`。

### 2.2 费用模型(锁定 — 复用 `backend/broker/cost_calculator.py`,但**人工成交不套系统滑点**)
`gross = 成交价 × 股数`(owner 上报的真实成交价**即为 fill_price**,系统**绝不再叠加 simulation 滑点** —— 滑点是
simulation_auto 撮合模型,人工真实成交价已含真实市场滑点)。各费项:
- **佣金** = `max(gross × 0.00015, 5.0)`(**万分之1.5 + 不足 5 元按 5 元;双向**——买卖各收一次,各方向各算一次);
- **过户费** = `gross × 0.0000341`(仅 `SZ_MAIN`/`CHUANGYE`/`159*` ETF;沪市 + 沪 ETF 为 0;P1-2.C 既有 `TRANSFER_FEE_RATE_SZ`);
- **印花税** = `gross × stamp_tax_rate`(**仅 SELL**;BUY 免);
- **滑点** = 0(人工成交价是真实 fill,非系统派生)。
- **BUY** 现金流出 = `gross + 佣金 + 过户费`;**成本价(每股)** = 该值 / 股数;持仓成本 = **加权平均 blend**(复用 `_apply_buy`)。
- **SELL** 现金流入 = `gross − 佣金 − 印花税 − 过户费`;已实现盈亏对加权平均成本。

### 2.3 owner 佣金率入 broker 配置(0.0003 → **0.00015**;runtime 不可改)
`BrokerConfig.commission_rate` 由占位 `0.0003`(万分之3)改 **`0.00015`**(owner 真实万分之1.5);`min_commission`
保持 `5.0`(已等于"不足 5 元按 5 元")。`broker.yaml` 同步。runtime 不可改 + hot-reload 禁用(P1-2.C / P0-7 §2 红线 1):
改 = git diff + amendment + 重启。(simulation_auto 与 interactive 经账户生命周期隔离 P0-1;统一用 owner 真实费率更贴近实盘,对 shadow 验收无害。)

### 2.4 schema 版本化简化 + 幂等
回报由「价 + 量」构成(不再含 owner 上报的 fee),故 `report_schema_version` 仍标注:**v1**=旧 sim 路径(系统撮合自带 fill_price+fee)/ **v2**=人工 interactive(owner 报成交价+股数,系统算费)。`compute_idempotency_key` 含 version + 成交价 + 股数(**不再含 owner fee**——fee 已是系统派生量,不入幂等键)。recovery loader 按版本分支重算/回放。

### 2.5 对账仍是权威闸(系统算错 → 16:00 ticket fail-closed)
系统计算的含费成本若与 owner 真实券商有差(券商优惠/返佣/特殊费),**16:00 主动对账**(P0-5:现金 1 元 / 量 0% / 成本 0.01 元阈值)捕获并开 `ReconciliationTicket` 三选一裁定。即系统计算是"最佳确定性估计",对账是兜底真相。

## 3. 不变量(本 amendment 不触碰)

- LLM 完全不参与回报路径(P0-4);正则严格 + 不通过 = AMBIGUOUS 绝不更新 MockBroker;`parse_ok=False` 强制 HOLD。
- 成本计算是**纯函数确定性**(`cost_calculator` 无 IO / 无 LLM;`backend.risk` 不 import 它)——非 LLM 决策。
- MockBroker 单一镜像 + `ExecutionReportApplier` 单一写入口;直接 mutation `_cash`/`_positions`/`_trades` 违规。
- 含费(interactive)vs simulation_auto 口径靠**账户生命周期隔离**(模式切换 archive+reset)永不共存;测试断言。
- 飞书消息必经 `renderer.py`;5 种回报正则范畴不变(仅 FILLED 字段由"价+量+费"→"价+量")。

## 4. 落地(U-E3 实施)

- 代码:`backend/broker/cost_calculator.py`(新增 interactive 无滑点成本入口或 `apply_slippage=False` 参)/ `backend/execution/regex_patterns.py`(FILLED v2「成交价+股数」)/ `ExecutionReportApplier`(feishu_interactive 路径系统算费 + 加权平均 blend)/ `backend/broker/models.py` + `config/broker.yaml`(commission_rate 0.00015)/ `frontend/src/utils/executionRegex.ts`(镜像同步,vitest 一致)。
- 测试:per-fill 系统算费(BUY 含买佣+过户;SELL 含卖佣+印花+过户;沪/深/创/ETF 分板过户费;min ¥5 floor)+ 加权平均 blend + v1/v2 replay + 幂等 + 含费/不含费口径隔离 + 前端镜像一致。非 risk >70%。
- 任务:plan.html U-E3。
