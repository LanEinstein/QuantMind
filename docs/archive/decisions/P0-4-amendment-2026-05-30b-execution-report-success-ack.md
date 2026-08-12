# P0-4 修订(b)— 2026-05-30 入站回报成功确认回执(闭环每条回报必有一条回复)

> **修订基准**: [P0-4 ExecutionReportParser 严格正则 + fail-closed 状态机](./P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md)
> **关联**: P0-2(飞书 WS 出站经 renderer 防注入 / 决策群≠告警群)/ P0-5(MockBroker 单一镜像权威)/ U-D11(@mention 剥离,本 amendment 续作)
> **修订日期**: 2026-05-30(#54 owner 反馈)
> **触发**: U-D10+U-D11 把入站闭环跑通后,owner 指出**成功路径不给任何回复**——只有解析失败才回澄清模板,owner 无法区分『已成功应用』与『消息丢失』。owner 原话:「用户总要清楚地知道自己的回复是不是奏效了,就像 TCP 三次握手」。owner 选定方案 = **只发最终结果回执**(成功→确认回执;失败→沿用现有澄清),不要中途『已收到』二段式(避免群里每条回报产生 2 条机器人消息)。

## 1. 修订前

- 入站 owner 回报:解析失败/AMBIGUOUS/未知 plan/过期/字段交叉校验失败 → 发对应澄清模板回决策群;**解析成功并应用 → 静默,不回任何消息**。
- 结果:owner 发出合法回报、系统已镜像入账,但群里无任何反馈,owner 不知是否奏效。

## 2. 修订后(本 amendment 锁定)

### 2.1 成功路径发**一条**确认回执(ack)
`ExecutionReportApplier.apply` 成功后,`ExecutionReportOrchestrator` 经 `renderer.render_execution_ack` 渲染一条确认消息,发回**同一决策群**(`message.chat_id`,与澄清同路径;**绝不**发告警群、**绝不**走备用 webhook)。内容回显已落账事实:
- `【QuantMind 已记录】` 头 + 指令编号(canonical regex 校验)
- 回报类型(已执行/部分执行/未执行 + 更正/盘后补录 前缀标签)
- FILLED/PARTIAL:`成交: {side_zh} {code} {volume}股 @ {price}`(PARTIAL 附剩余股数)
- UNFILLED:`记录为未成交` + 原因(`single_line`+`truncate` 防伪头)
- `账本现金变动: ±{cash_delta} CNY` + `账本持仓变动: {code} ±{volume} 股`(来自 ApplyResult / report)
- `账本序号: {broker_event_sequence}`(持久化证据)
- 落款 `(以系统模拟账本为准;如有出入请等 16:00 对账)`

**契约**:经此修订,入站 owner 回报(经 InboundGate 放行的决策群+owner 消息)**必有且仅有一条回复**——成功→ack / 失败→澄清模板。owner 永远知道回报是否奏效。

### 2.2 fail-open(回执不得回滚已落账)
ack 渲染/发送**失败必须 fail-open**:已 `apply` 的报告与 broker 镜像是权威事实(P0-5),ack 仅是告知。`_send_ack` 捕获任何异常 → 记 `execution_report_ack_send_failed` warning + 返回 None,**绝不**让 ack 失败把成功 outcome 翻成失败或回滚镜像。

### 2.3 防注入 / LLM 隔离 / 红线不破
- ack 仅插值确定性字段(instruction_id 过 canonical regex;code/volume/price 为 model 校验数值;cash_delta/sequence 来自 applier);唯一自由文本 `reason`(UNFILLED)经 `single_line`+`truncate`,不能夹带换行伪造 `【...】` 头(P0-2 §2.6)。
- 全程零 LLM(确定性字符串拼装)。
- **未新增 `FeishuMessageKind` 第 6 类**:`send_message(chat_id, text)` 不强制 kind;锁定的 5 类枚举不动(避免 P0-2 §2.5 枚举扩张红线)。
- 前端通道(`target_chat_id is None`)不发飞书(前端 UI 自行展示应用结果),与澄清一致。

## 3. 不在范围(留后续)
- owner 模板优化(分隔符 + 引用/回复关联去编号)仍待做(需另一 P0-4 amendment + events.py 补 parent_message_id)。
- 中途『已收到处理中』二段式回执:owner 明确不要(噪音)。

## 4. 影响文件
- `backend/integrations/feishu/renderer.py`(新增 `render_execution_ack` + kind/prefix 标签表 + import ExecutionReport*)。
- `backend/integrations/feishu/parser.py`(成功路径调 `_send_ack`;新增 `_send_ack` fail-open helper)。
- `tests/test_feishu_renderer.py`(`TestExecutionAck` 3 例)+ `tests/test_feishu_parser_orchestrator.py`(成功发一条 ack + ack 发送失败 fail-open)。
