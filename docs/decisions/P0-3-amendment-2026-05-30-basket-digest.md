# P0-3 修订 — 2026-05-30 飞书篮子汇总(display-only 第 6 个 FeishuMessageKind)

> **修订基准**: [P0-3 InstructionPlan 严格 schema + 第一阶段飞书纯文本模板](./P0-3-instructionplan-schema-feishu-text-template.md)
> **关联**: [P0-2 自建应用 + lark-oapi 长连接](./P0-2-feishu-self-built-app-websocket.md) §2.5(消息类型扩展 = 红线扩展)、P0-2-amendment-2026-05-16(告警通道)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2.0(单一构造点)
> **修订日期**: 2026-05-30
> **触发**: Owner 要求"一次给 ≤5 优质股篮子"并在飞书有**整体视图**;现状每只 VALIDATED BUY 逐条独立发、**无篮子汇总**(收到的永远是单只)。AskUserQuestion 锁定 **汇总概览 + 逐只可执行指令(两者都发)**。

## 1. 修订前(P0-3 / P0-2 原锁定)

- `FeishuMessageKind` 锁 **5 类**:`INSTRUCTION_PLAN` / `CLARIFICATION` / `RECONCILIATION_REQUEST` / `RECONCILIATION_RESULT` / `ALERT`(`backend/integrations/feishu/renderer.py:78`),测试断言**成员数恒 5**,docstring 明示"第六种 = P0-2 §2.5 红线扩展,非随意加"。
- Line-1 篮子**逐只 dispatch**:每 VALIDATED BUY 一条 `INSTRUCTION_PLAN`(经 `InstructionDispatcher`,按 `instruction_id` 幂等),**无合并概览**(`tests/orchestration/test_line1_runner.py`:3 只篮子 = 3 次 `send`)。

## 2. 修订后(本 amendment 锁定)

### 2.1 新增第 6 个 `FeishuMessageKind = BASKET_DIGEST`(display-only 概览)

- **内容**:当日篮子全部 ≤5 只 `code`/`name` + 各自**目标手数 · 权重 · 金额** + **合计部署** + **现金占用**。
- **display-only,严格非可执行**:**不含任何可被 `parse_execution_report` 9 条正则匹配的订单字段** —— 尤其**禁印** `QM-…-BUY-… 已执行/部分执行/未执行` 形 token。经 `renderer.py`(防 prompt injection,确定性渲染,**LLM 不拼接**)。
- 发**决策群**(非告警群;P0-2-amendment-2026-05-16 告警通道不变);banner 明示「本条为今日篮子概览,非交易指令,无需回报」(mirror `render_smoke_ping` 的非可执行文风)。

### 2.2 与逐只指令并存(不替代)

逐只可执行 `INSTRUCTION_PLAN` 消息**完全不变**(每只独立 `instruction_id` 供回报/对账/幂等)。`BASKET_DIGEST` **仅附加**全局视图,**不替代**逐只指令、**不承载**回报闭环。

### 2.3 发送路径(实施期 — 关键约束)

- **不走 `InstructionDispatcher`**:它结构上强制 `OutboundSignal.plan`、按 `instruction_id` 幂等、并迁移 plan 状态 + 写 `PLAN_DISPATCHED` ledger;digest **无 plan**(不破单一构造点)。
- **独立幂等发送**:直接 `FeishuClient.send_message`,幂等键 = **run `signal_id` + "-basket-digest"**,走现有 `OutboxRepository.try_claim/mark_sent`(`backend/orchestration/instruction_dispatcher.py:94/115`,outbox 键为任意串)。`run()` 末(`_aggregate` 后)`routed_buys` 非空时**发一次**。
- `Line1Runner` 保 import 隔离:digest sender 注入(mirror 现有 `publish_update` 注入)。

### 2.4 入站安全(对抗测试锁)

对抗测试:`render_basket_digest(...)` 文本喂 `parse_execution_report(...)` → **必** `ExecutionReportParseError(no_pattern_match)`。`InboundGate.classify` 仅校验 `chat_id` + owner allowlist、**不解析**,故 digest 即便被回复也不会误入回报路径。

## 3. 实施期任务调整(Phase P)

- **P-004**:`render_basket_digest` + `BASKET_DIGEST` 枚举 + run 末独立幂等发送 + 对抗/幂等测试。
- `renderer.py` 成员数测试 5→6;`redline-check.sh` `FeishuMessageKind` 成员数校验 5→6。

## 4. 红线清单(本 amendment 之后)

1. `FeishuMessageKind` **5→6**(新增 `BASKET_DIGEST`);成员数测试 + redline 校验同步 5→6;**其余 5 类语义不变**。
2. `BASKET_DIGEST` **display-only**,无可解析订单字段;**对抗测试锁** `parse_execution_report` 必失败。
3. 经 `renderer.py` 防注入;**LLM 不拼接** digest 文本(P0-3 §2 红线);发**决策群**非告警群。
4. 逐只 `INSTRUCTION_PLAN` 不变;digest **仅附加、不替代、不承载回报**。
5. digest **不走 `InstructionDispatcher`**、**不构造 `InstructionPlan`**(R0 单一构造点);独立幂等键 = run `signal_id`-basket-digest。
6. 飞书永禁公网入站 / custom-bot(P0-2 + P0-2-amendment-2026-05-16)不变;digest 走自建应用长连接同款 OpenAPI 发决策群。

## 5. 修订记录追加

`docs/plan.html` Phase P 任务 + 修订记录 + SESSION_LOG。CLAUDE.md §2.6 飞书表述补充「+ BASKET_DIGEST display-only 篮子概览(决策群,无可解析订单字段,入站永不匹配)」;§2.11 WS 类如需前端镜像可后续扩(MVP 不强制前端)。
