# P0-3 修订 — 2026-05-27 Line-1 BUY 飞书信号增加判据段(display-only,非破坏式)

> **修订基准**: [P0-3 InstructionPlan 严格 schema + 飞书纯文本模板](./P0-3-instructionplan-schema-feishu-text.md)
> **关联**: 同日 P0-3-amendment(实时 cage 限价)/ P0-10 LLM 字段权限矩阵 / R0 §4 单一构造点 M-004
> **修订日期**: 2026-05-27(U-E4 / 缺口 3 落地)
> **触发**: Owner 审 002747 BUY 信号(2026-05-26)提出缺口 3:飞书买卖信号消息**不够显眼、且缺少可量化 +
> 推理判据**,人工执行时无法据以判断为什么买。Owner 决策:消息须显眼 + 带判据(① 量化:score + 各因子 +
> 为何入选 shortlist;② 推理:fund_manager reasoning + 3 分析师结论)。经 plan mode + codex 计划评审,
> codex 明确判据须 **display-only + 长度截断**(见 `docs/next-session-prompt-tradeable-mvp.md` §5)。

## 1. 修订前(P0-3 原锁定)

- `render_buy_signal` 经共享 `_dispatch_body_lines` 输出 7 段体(指令编号 / 操作 / 股数 / 限价 / 预计金额 /
  有效期 / 仓位预览 + 风险摘要 + 失效说明 + 回报模板),**无判据段**。
- `InstructionPlan` 是 frozen Pydantic v2 strict + `extra="forbid"`,**无 `reasoning` / 判据字段**;
  量化因子(`CandidateRow.factors`)与辩论文本(`TeamState.fund_manager_reasoning` 等)从不进入 plan。
- 飞书消息必经 `renderer.py`,LLM 永不拼接 wire 文本(P0-2 §1.2 / P0-3 §1.3 / CLAUDE.md §2.6)。

## 2. 修订后(本 amendment 锁定)

### 2.1 `render_buy_signal` 增加判据段(纯展示,经 renderer + 净化)
`render_buy_signal` 新增可选渲染参数 `rationale: BuySignalRationale | None`(默认 `None` → 行为与修订前
**字节一致**,既有 snapshot 测试不破)。当提供时,在 banner 之后、7 段体之前插入两段判据:

```
—— 量化判据 ——
综合评分: <score>(全市场量化筛选 top-N 入选)
动量(20日): <pct> / 均线比(5/20): <ratio> / 波动率(20日): <pct> / RSI(14): <num> / 日均成交额(20日): <亿元>
—— 推理判据 ——
基金经理: <fund_manager reasoning · 单行 · 截断>
基本面: <fundamental 结论 · 单行 · 截断>
技术面: <technical 结论 · 单行 · 截断>
风控: <risk_officer 结论 · 单行 · 截断>
```

**判据 = display-only(本 amendment 核心不变量)**:
- 判据**永不**进 `InstructionPlan` 任何数值/文本订单字段(plan strict + extra=forbid,结构上无处可放);
- 判据**永不**被 `ExecutionReportParser` 解析(parser 只看 owner **入站**回复,出站 BUY 信号永不被回放解析);
- 判据**永不**进 `compute_idempotency_key`(幂等键派生自入站 `ExecutionReport`,与出站消息无关);
- 判据**永不**进 `RiskCheckSummary` / RiskEngine 14-check(判据是渲染参数,RiskEngine 纯函数不见它);
- 判据**永不**改变 `side` / `volume` / `limit_price`(单一构造点 M-004 不破;factors/辩论文本仅经
  `line1_runner._route_candidate` 作渲染参数传入,不进 model)。

### 2.2 防注入 + 长度截断(沿用 P0-3/P2-1 既有范式)
- 量化因子值是确定性 float(无注入面);因子标签是代码常量。
- 推理判据 4 行均为 **LLM 可写自由文本**(`fund_manager_reasoning` 等),逐条经 `text_safety.single_line()`
  折叠所有换行/控制符为单空格(防伪造 `【QuantMind …】` 头,P2-1 / CLAUDE.md §2.6)+ `text_safety.truncate()`
  长度截断(reasoning ≤160 / 各分析师结论 ≤120 字符)。
- 净化逻辑单一真相源:`_single_line` / `_truncate` 从 `renderer.py` 抽到 `backend/integrations/feishu/text_safety.py`
  公开为 `single_line` / `truncate`,renderer 与 signal_rationale 共用同一实现(行为不变,snapshot 不破)。

### 2.3 买卖信号顶部显眼排版(纯文本)
`render_buy_signal` 在 header/banner 之后插入"交易要点"显眼块(代码常量分隔线 `━` + `▶` 标记的
**买入方向 / 股数 / 限价**,纯文本、终端与飞书可读)。header 仍是首行(PILOT 时 banner 仍首行),
7 段体内 `股数:`/`限价:` 明细行保留(`in` 断言不破)。显眼块用 `━`(U+2501),不含 `【`,不被误认作头。

**Line-2 SELL/ADD 已显眼 + 已带确定性判据**:`render_monitoring_sell(anomaly_reason)` /
`render_add_position(add_rationale, stop_price)` 各有独立 header + 异动/补仓 banner(确定性 detector
字符串,非 LLM),本 amendment 不改 Line-2 路径(SELL 方向由确定性 AnomalyDetector 派生,无量化因子/辩论)。

## 3. 不变量(本 amendment 不触碰)

- `InstructionPlan` frozen strict extra=forbid;无 reasoning 字段;判据走渲染参数不进 model。
- 单一构造点 M-004:`InstructionPlan(` 仅 model + builder + tests;factors/辩论文本不改 side/volume/limit_price。
- 飞书消息必经 `renderer.py`(防注入);LLM 永不拼接 wire 文本(判据是确定性格式化 + 净化,signal_rationale
  是 renderer 的纯格式化子助手,仅被 renderer 调用)。
- LLM positive list 4 类不扩;HOLD 不路由不发飞书;`parse_ok=False` 强制 HOLD;状态机不变。
- 模板既有标签/行序/数值格式不变(default rationale=None 时字节一致);PILOT banner 单一真相源不变。

## 4. 落地

- 代码:`backend/integrations/feishu/text_safety.py`(新,抽 `single_line`/`truncate`)/
  `backend/integrations/feishu/signal_rationale.py`(新,`BuySignalRationale` + `rationale_lines` 确定性格式化 + 净化)/
  `backend/integrations/feishu/renderer.py`(`render_buy_signal` 加 `rationale` 参数 + 显眼块 + 判据块拼接;
  `_single_line`/`_truncate` 改 import text_safety)/
  `backend/orchestration/line1_runner.py`(`_process_candidate` 从 `CandidateRow` + 辩论 `TeamState` 装配
  `BuySignalRationale`,经 `_route_candidate` 作渲染参数传 `render_buy_signal`,不进 model)。
- 测试:`tests/test_feishu_buy_signal_rationale.py`(新,判据快照 + 因子格式 + 防注入 + 截断 + display-only:
  判据不出现在 idempotency key / plan model_dump / risk_summary)/ `tests/test_feishu_text_safety.py`(新,净化行为)/
  `tests/orchestration/test_line1_runner.py`(BUY 经 runner 带判据 + 判据不改 volume/limit_price)。
- 任务:plan.html U-E4。
