# P0-4 修订 — 2026-05-30 飞书入站 @mention 占位符剥离(解析前文本归一化)

> **修订基准**: [P0-4 ExecutionReportParser 严格正则 + fail-closed 状态机](./P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md)
> **关联**: P0-2(飞书 WS 入站 + owner allowlist `P0-2-amendment-2026-05-27`)/ U-D10(websockets pin 修好 WS 传输)/ R0 单一构造点(不破)
> **修订日期**: 2026-05-30(#54 owner-in-loop 真飞书往返实测暴露)
> **触发**: U-D10 把飞书 WS 入站传输修通后,owner 在决策群真回报,后端**确实收到入站事件**(audit `execution_report_parse_failed` / `no_pattern_match`,raw_text_length=75)并回了澄清模板。但 owner 必须 **@机器人** 才能让群消息送达 bot(应用缺 `im:message.group_msg` scope,飞书仅把 @ 机器人的群消息推给应用),而 lark 把这个 @mention 在 text 正文里渲染成占位符 `@_user_1 `(占位 key 在事件 `mentions` 数组里)。P0-4 的执行回报正则用 `re.fullmatch`(`^...$`),`@_user_1 盘后补录 已执行 …` 永远匹配不上 → 恒 AMBIGUOUS → 闭环断。

## 1. 修订前(P0-4 原文)

- 入站消息文本经 `_extract_text_content` 取 `{"text": "..."}` 的 `text` 字段(strip 首尾空白)后,**原样**交给严格正则;不通过 = AMBIGUOUS,绝不更新 MockBroker,严禁猜 `instruction_id`。
- 未考虑群聊 @mention 场景:lark 把 @ 渲染成 `@_user_N` 占位符并前置进 text 正文,导致 owner 在群里 @ 机器人后的合法回报恒被判 no_pattern_match。

## 2. 修订后(本 amendment 锁定)

### 2.1 解析前剥离 @mention 占位符(纯文本归一化,**不放松正则**)
`_extract_message` 在取出 text 后、交正则前,按事件 `message.mentions` 数组里的占位 key(如 `@_user_1`)把这些 token 从正文中移除,并归一化因移除而产生的多余空格(仅折叠 ≥2 个连续空格为 1 个,保留正文内单空格),再 strip 首尾。

**严格边界(均不破 P0-4 红线)**:
- **只剥离 `mentions` 数组里声明的占位 key**(精确字符串匹配),不做任何模糊「@xxx」正则猜测;数组为空或缺失 → 文本原样(行为同修订前)。
- **不放松执行回报正则**:剥离后的文本仍须 100% 通过原 `re.fullmatch`;不通过 = **AMBIGUOUS**,绝不更新 MockBroker(fail-closed 不变)。
- **不推断 `instruction_id`**:剥离只动 @mention 噪声,`instruction_id` 仍只能来自正文里 owner 亲填、由正则捕获;严禁从 `mentions`/`sender`/任何元数据派生编号。
- **不动数值订单字段**:剥离不解析、不改写 `side/volume/limit_price/price` 等任何字段;只移除占位 token + 折叠空白。
- 剥离后若文本为空 → `_SkipEventError`(同原「text content empty」跳过路径)。

### 2.2 LLM 隔离不变
文本归一化是确定性字符串操作(`str.replace` + 一条折叠空白的 `re.sub`),**零 LLM 参与**;继续严禁 LLM 触碰回报解析路径(P0-10 / P0-4 红线)。

### 2.3 与 owner allowlist 顺序
入站仍先经 `InboundGate`(P0-2-amendment-2026-05-27:chat_id + owner open_id allowlist,DROP_NOT_OWNER 不触 parser);@mention 剥离发生在 gate 通过、parser 解析之前。@mention 的存在不改变 allowlist 判定(allowlist 看 `sender.open_id`,不看正文 @)。

## 3. 不在本 amendment 范围(留后续)

- **引用/回复关联去编号**(owner 2026-05-30 模板诉求②:回报不写 `instruction_id`,改用飞书「回复/引用」那条 BUY 消息 → 入站事件 `parent_id`/`root_id` → 经 outbox `feishu_message_id` 反查 instruction_id):需独立 amendment + parser 分支 + events.py 补 `parent_message_id` 字段;本次先把 @mention 这条最小闭环打通。
- **回报模板加分隔符**(owner 诉求①):renderer `_REPORT_TEMPLATE_BLOCK`,无依赖独立任务。

## 4. 影响文件
- `backend/integrations/feishu/events.py`(`_extract_message` 取 `mentions` + 新增 `_strip_mention_placeholders` 模块级纯函数;`import re`)。
- `tests/test_feishu_events.py`(@mention 剥离单测:单/多 mention、mention 在中间、无 mention 不变、剥离后仍 AMBIGUOUS 的 fail-closed 守门)。
- 消息处理逻辑其余(`_dispatch`/dedupe/状态机/appliers)零改;前端入站正则镜像不涉及(@mention 是 lark 入站表示,前端只镜像出站回报正则)。
