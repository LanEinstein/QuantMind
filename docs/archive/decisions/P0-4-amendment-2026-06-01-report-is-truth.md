# P0-4 修订 — 2026-06-01 回填即真相(执行回报以实报为准 + 报告窗口到 14:55)

> **修订基准**: [P0-4 锁定 — ExecutionReportParser 严格正则 + fail-closed 状态机](./P0-4-execution-report-parser-strict-regex.md)
> **关联**: P0-3(InstructionPlan valid_until = now+5min clamp 14:55)/ P0-5(对账)/ P0-7(仓位限额)/ P0-4-amendment-2026-05-30b(每条回报必有且仅有一条回复)
> **修订日期**: 2026-06-01(周一 MVP 首笔真实 Line-1 BUY 真发后,owner 真实回填暴露)
> **决策人**: owner(2026-06-01 本 session 拍板:回填即真相口径)

## 0. 触发(真实人工执行暴露 P0-4 严格匹配不符现实)

首笔真发 `QM-20260601-133302-605111-BUY-002`(建议 100 股 @ 64.57)。owner 实际按自己判断买了 **200 股 @ 63.035**,回填 `已执行 ... 605111 200股 成交价 63.035`。系统判 **AMBIGUOUS** 发澄清「代码/方向/股数其中之一不匹配」(`parser.py::_cross_check_volume` line 78 `filled_volume != plan.volume`),**拒记真实成交**。

owner 原话:**"我实际操作并不会完全按照飞书指令来"**。这暴露 P0-4 的根本假设错误:它假定 owner 严格按建议数量执行(已执行=建议量 / 部分执行=少于建议量),**没有"按自己判断执行不同数量(含超过建议量)"的路径**。但 QuantMind 模型本就是 **飞书信号=投研建议;owner 按自己判断人工执行;回填=真实成交;MockBroker 镜像=真相**。镜像若不记真实成交 → 与真实券商账户背离 → P0-5 对账失去意义。

### 0.1 第二个耦合缺口:5 分钟报告窗口对人工执行不现实

`parser.py` line 274 报告过期判定用 `plan.valid_until`(= 信号时点 + 5min,P0-3)。但 `valid_until` 是**路由窗口**(信号→飞书派发必须快),而人工执行(去券商下单+回填)必然 >5min。即:即便修了股数,owner 13:45 回填一笔 13:33 的信号仍被判 EXPIRED(除非加「盘后补录」前缀)。飞书消息本身显示「(同日 14:55 截止)」——**报告/执行窗口本应到当日 14:55,而非 5min 路由窗口**。两者是"回填即真相"的一体两面,一并修。

## 1. 决策:回填即真相(owner 2026-05-31... 实为 2026-06-01 拍板)

### 1.1 身份校验保留,数量/价格以实报为准
回报与指令的**强匹配仅保留三项身份**:`instruction_id` + `stock_code` + `side`(买卖方向)。这三项**已由 `ExecutionReport` 模型校验器强制**(报告的 stock_code/side 必须等于 instruction_id 段编码的 code/side)——贴错指令号 / 方向写反 仍是真错误 → 仍 AMBIGUOUS 澄清。

**放开数量强匹配**:`_cross_check_volume` 不再因 `filled_volume != plan.volume`(已执行)或 `filled+remain != plan.volume`(部分)判 mismatch。`filled_volume`(及成交价)**按 owner 实报记录为真实成交**,applier(`appliers.py:252` 已用 `report.filled_volume`)写入镜像 = owner 真实持仓。**保留 100 股整手校验**(非整手 = 真实笔误/A 股不可能)。

### 1.2 报告窗口 = 当日 14:55(非 5min valid_until)
对**已 DISPATCHED** 的指令,回报过期判定改用 **created_at 当日 14:55 Asia/Shanghai cutoff**(复用 `models/instruction.py` 既有 14:55 常量口径),而非 `valid_until`(5min 路由窗口已消耗在派发上)。即:同一交易日 14:55 前的回报 = live 直接 apply;14:55 后 / 跨日 = 仍走「盘后补录」前缀(P0-3 §1.4 不变)。与飞书消息显示的「(同日 14:55 截止)」一致。

### 1.3 偏离建议 = 记录 + 偏离标记,绝不拒
`filled_volume != plan.volume`(owner 偏离建议量,含超额)→ **正常 apply 真实成交** + emit 结构化日志偏离标记(`execution_report_volume_deviation`,记 suggested vs actual volume)供可观测/reason 抽屉。**永不因偏离拒记**——交易已在真实世界发生,镜像必须跟真相。(正式 `AuditEventType` 落地需改 P1-6 锁定的 34 类枚举 → deferred follow-up;当前结构化日志入 `quantmind.jsonl` 已提供观测。)

### 1.4 超风险限额 = 记录 + 告警,绝不拒(本 amendment 文档化,核心 apply 已天然记录)
若 owner 真实成交导致镜像仓位超 P0-7 限额(单股 >15% / 总仓 >70% / 单笔 >¥5万),applier 仍如实记录(交易已发生,RiskEngine 是**对建议的事前投研建议门**,非对 owner 人工执行的硬控)。超限 → audit + 飞书告警(owner 自行越界知情);**不拒记**(拒记 = 镜像与真相背离)。告警实现为 follow-up 增强;§1.3 的 deviation audit 标记已提供观测底座。

## 2. 落地
- `backend/integrations/feishu/parser.py::_cross_check_volume`:删 `volume_mismatch_filled` / `volume_mismatch_partial_sum` 两返回;保留 `volume_lot_violation`(100 整手)+ HOLD `field_cross_check_failed` fail-closed。
- `parser.py` 报告过期段(line ~273-279):`plan.valid_until` → `created_at` 当日 14:55 cutoff(新 `_report_window_deadline(plan)` 助手)。
- `parser.py` apply 成功后:`report.filled_volume != plan.volume` 时 emit 结构化日志 `execution_report_volume_deviation`(suggested/actual volume)。
- 测试:`_cross_check_volume` 偏离量不再 mismatch(改断言)+ 新增 已执行 200≠100 apply 成功 / 14:55 前 late 报告 apply 成功 / 14:55 后非盘后补录 EXPIRED / 100 整手仍 reject / 方向贴错仍 AMBIGUOUS(模型层)/ deviation audit 标记。前端 JS 正则镜像不受影响(只镜像 `regex_patterns.py` 解析正则,不做 vs-plan 数量交叉校验)。

## 3.5 放开数量校验暴露的下游修复(review 抓出,本 amendment 一并修)

放开「数量必须等于建议」后,一笔**不可能的成交**(typo 超买 / 超卖于持仓)不再被 cross-check 提前拦,会真到达 applier。两处 review HIGH 一并修:

- **超买负现金(HIGH)**:`mock_broker.apply_external_fill` BUY 路径原无现金充足校验,relaxing 后 typo 超买(如多一个 0)会静默把 `_cash` 打负、腐化镜像。新增 `net > self._cash → raise`(对齐既有 SELL 超持仓 raise);**不可负担的成交不是真相 = typo,拒并澄清,不腐化镜像**(与 §1.4「超**限额**记录+告警」区分:超限额=可负担的真实偏离→记录;不可负担=不可能→拒)。
- **applier 抛错静默无回复(HIGH)**:relaxing 后超卖/超买使 applier 抛错可达,orchestrator 原 except 返回 `success=False/ambiguous=False/send_result=None` = **零回复**,违反 2026-05-30b「每条回报必有且仅有一条回复」。改 except 走 `_send_clarification(FIELD_CROSS_CHECK_FAILED)` + audit `apply_failed`,镜像不变(applier raise 在 mutate 前)+ owner 必得一条澄清。

## 3.6 已知 follow-up(本 amendment 文档化,不静默)
- **更正(AMEND)前缀过 14:55 仍被判 EXPIRED**(pre-existing:expiry bypass 仅认 POST_CLOSE,不认 AMEND)。更正本是事后操作应豁免窗口;本次未改(与放开数量正交,且 owner 重测可用 POST_CLOSE)→ 候选下次随「更正语义」一并修。
- **超 P0-7 限额(可负担)的真实偏离 → 飞书告警**(§1.4)实现 deferred;当前 `execution_report_volume_deviation` 结构化日志提供观测底座。
- 边界测试(14:55:00 含 / 14:55:01 排)+ 跨日 parsed_at EXPIRED 回归守卫 + deviation 日志断言:review LOW,候选补测。

## 3. 不变量(本 amendment 不触碰)
- AMBIGUOUS 永不更新 MockBroker(P0-4 §1.1.1);每条回报必有且仅有一条回复(amendment 2026-05-30b)。
- `instruction_id` + code + side 身份强匹配(模型校验器);100 股整手;盘后补录跨日前缀;fail-closed 解析正则。
- LLM 永不参与回报路径;applier 单一入口 `ExecutionReportApplier`;镜像经 applier 不直改 `_cash`/`_positions`。
- RiskEngine 14-check 仍是 Line-1/Line-2 建议侧权威事前门(对建议,非对人工执行)。
