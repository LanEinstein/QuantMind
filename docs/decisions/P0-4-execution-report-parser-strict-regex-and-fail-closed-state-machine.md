# P0-4 — 飞书回报语法、ExecutionReportParser 严格正则与 fail-closed 状态机

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-4 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §3.2 / §5.2 / §7 / §8.3 / §9 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-4 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.3.1 #4 / §1.5 路由规则)+ `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(尤其 §1.1 长连接 worker 仅 ack+入队 / §3.1 ExecutionReportParser 归属)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.1.1 状态机 / §1.4 超时语义 / §1.6 模板回报示例 / §1.6.3 P0-3 留给 P0-4 的扩展点)|
| 替代       | — |

## 决策摘要

QuantMind 飞书回报路径采用**严格正则 only + fail-closed 状态机**:

1. **解析策略**:用户在飞书的回报必须严格匹配 P0-3 §1.6 锁定的模板正则;不通过即触发 `InstructionStatus.AMBIGUOUS`,系统**自动**回一条预先写死的澄清飞书。**LLM 完全不参与回报解析,也不参与澄清文案生成**(对齐 `backend/agents/fund_manager.py:_parse_signal` 的 `parse_ok=False` 强制降级范式)。

2. **回报形态**:支持五种规范化形式,正则在本决策 §1.2 完整锁定:
   - `已执行` — 全部成交
   - `部分执行` — 部分成交 + 剩余未成交股数
   - `未执行` — 主动放弃,仅"原因:"后允许 ≤200 字短文本
   - `更正 <已执行/部分执行/未执行 ...>` — 覆盖之前回报
   - `盘后补录 <已执行/部分执行/未执行 ...>` — 14:55-16:00 时窗对从未回报或已 EXPIRED 指令补录

3. **追问节奏**:`DISPATCHED → 30 分钟无回报 → 系统发 1 次追问 → valid_until 仍无回报 → EXPIRED`;短追问宽限 — 若 `valid_until - dispatched_at < 30 min`,跳过追问。AMBIGUOUS 期间不计追问。

4. **盘后补录与更正时窗**:都允许到**当日 16:00 Asia/Shanghai**。16:00 后 InstructionPlan 当日变更**冻结**,任何回报触发 AMBIGUOUS 并飞书提示"次日发新指令"。

5. **状态机扩展**(在 P0-3 §1.1.1 基础上,P0-4 落地新增允许迁移):
   - `DISPATCHED → AMBIGUOUS`(正则失败 / 字段冲突)
   - `AMBIGUOUS → DISPATCHED`(用户重发后等待解析)
   - `AMBIGUOUS → FILLED / REJECTED / EXPIRED`(澄清后正确回报到位 / 超时降级)
   - 终态 → 终态(`FILLED ↔ REJECTED` / `EXPIRED → FILLED / REJECTED`):仅经 amendment / post-close 补录路径,**16:00 内**允许;每条迁移由 `instruction_plan_state_machine.py` 守门 + 留 `state_transitions` 痕迹

6. **MockBroker 衔接(高层方向)**:每条解析成功的 `ExecutionReport` 通过 `ExecutionReportApplier` 应用到 MockBroker;更正路径**先反向操作再重应用**。MockBroker 持久化、反向操作的具体撮合细节属 P1-2 范围,P0-4 仅锁定"允许 + 必须 16:00 内 + 必须经状态机守门"。

7. **绝不允许根据上下文猜测 instruction_id**:回报文本中的 `instruction_id` 必须严格通过 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$` 匹配(注意 P0-3 §1.6 / §1.3.1 锁定 HOLD 不发飞书,所以回报路径只针对 BUY/SELL)。系统**绝不**通过"用户最近的飞书指令"等上下文反推 instruction_id。

## 1. 决策具体内容

### 1.1 ExecutionReportParser 解析策略(锁定 — 严格正则 only)

#### 1.1.1 角色边界

```
飞书长连接 worker (P0-2 §1.1)
    │ 收到 im.message.receive_v1
    │ 3 秒内 ack,入异步队列(P0-2 §2 红线 5)
    │
    ▼
ExecutionReportParser  (P0-4 主体,backend/services/execution_report.py)
    │ 1. 用 §1.2 锁定的正则集逐一尝试匹配
    │ 2. 命中即解析为 ExecutionReport,持久化到 execution_reports collection
    │ 3. 全部不命中即触发 AMBIGUOUS:
    │    - 写入 execution_reports.parse_ok=False(留痕原文)
    │    - InstructionPlan.status: 当前状态 → AMBIGUOUS(经状态机守门)
    │    - 调度 ClarificationSender 发预写死澄清飞书(§1.5)
    │
    ▼
ExecutionReportApplier (backend/services/execution_report_applier.py)
    │ 把成功解析的 ExecutionReport 应用到 MockBroker
    │ - 已执行: BUY 扣现金+加持仓 / SELL 减持仓+加现金
    │ - 部分执行: 按 filled_volume 比例应用
    │ - 未执行: 不变更 MockBroker,记入 ledger
    │ - 更正: 先用 reverse 反向原 report,再 apply 新 report
    │ - 盘后补录: 与对应类型应用一致
    │
    ▼
InstructionPlan.status 状态迁移(经状态机守门 §1.3)
    │
    ▼
decision_ledger 全链记录(P0-1 §3.2)
```

#### 1.1.2 LLM 角色边界(继承 P0-1 §1.6 / P0-3 §1.7.2,P0-4 严格收紧)

LLM **不可以**:
- 解析用户回报文本(P0-4 红线 §2.2)
- 推断/猜测用户意图,即使原文部分匹配模板
- 生成澄清飞书的文案(澄清文案在 §1.5 预写死)
- 决定状态迁移
- 反向操作 MockBroker

LLM **角色**:**完全不参与**回报路径。任何尝试在解析或澄清环节调用 `backend.llm.*` 的代码即红线违规,实施期由 lint rule + 集成测试守门。

> **设计原理**:对照 `backend/agents/fund_manager.py:_parse_signal` 的 `parse_ok=False` 范式 — `_parse_signal` 在 LLM 输出无效时强制降级为 `持有 / 0.5`,后续由代码层(InstructionPlanBuilder)进一步降级为 HOLD。回报路径同样:解析失败 → AMBIGUOUS → 澄清飞书 → 等用户重发,完全不让 LLM "聪明地"猜意图 — 因为飞书消息会触达真实 MockBroker 持仓状态,任何错译都会污染账户镜像。这条原则与 P0-1 §1.6 红线("LLM 不允许直接决定股数 / 价格 / 账户状态")一致。

### 1.2 五种回报形态的精确正则与示例(锁定)

所有正则都在 Python `re.fullmatch` 下匹配(即整段必须 fullmatch,前后不留空白)。**实施期消息预处理**:`message.strip()` 去掉首尾空白,内部多余连续空格归一为单空格 — 这是仅有的容错;其他偏差一律 AMBIGUOUS。

#### 1.2.1 已执行(全部成交)

**正则**:
```python
R_FILLED = re.compile(
    r"^已执行 "
    r"(?P<instruction_id>QM-\d{8}-\d{6}-\d{6}-(?:BUY|SELL)-\d{3}) "
    r"(?P<side_zh>买入|卖出) "
    r"(?P<stock_code>\d{6}) "
    r"(?P<volume>\d+)股 "
    r"成交价 (?P<fill_price>\d+(?:\.\d+)?) "
    r"手续费 (?P<fee>\d+(?:\.\d+)?)$"
)
```

**示例**:
```
已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 成交价 1678.50 手续费 5.00
已执行 QM-20260512-103015-000001-SELL-002 卖出 000001 2000股 成交价 10.20 手续费 2.04
```

**字段约束**:
- `volume` 必须为 100 整数倍(继承 P0-3 §2 红线 4 的 lot size 约束;实施期由 ExecutionReportApplier 二次校验,正则不强约束以便错误信息更清晰)
- `fill_price` > 0
- `fee` ≥ 0(允许免费商佣金 0.00)
- `side_zh` 必须与 `instruction_id` 中的 BUY/SELL 一致(`买入↔BUY` / `卖出↔SELL`),不一致即 AMBIGUOUS(field cross-check fail)
- `stock_code` 必须与 InstructionPlan.stock_code 一致,不一致 AMBIGUOUS

#### 1.2.2 部分执行

**正则**:
```python
R_PARTIAL = re.compile(
    r"^部分执行 "
    r"(?P<instruction_id>QM-\d{8}-\d{6}-\d{6}-(?:BUY|SELL)-\d{3}) "
    r"(?P<side_zh>买入|卖出) "
    r"(?P<stock_code>\d{6}) "
    r"(?P<filled_volume>\d+)股 "
    r"成交价 (?P<fill_price>\d+(?:\.\d+)?) "
    r"剩余未成交 (?P<remain_volume>\d+)股$"
)
```

**示例**:
```
部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 60股 成交价 1678.50 剩余未成交 40股
```

**字段约束**:
- `filled_volume + remain_volume == InstructionPlan.volume`,否则 AMBIGUOUS(field cross-check fail)
- `filled_volume > 0` 且 `filled_volume < InstructionPlan.volume`(全成交用 §1.2.1,完全未成交用 §1.2.3)
- `filled_volume` 必须是 100 整数倍;`remain_volume` 必须是 100 整数倍
- 其他约束同 §1.2.1

> **手续费省略说明**:部分执行模板**不带 `手续费 ...` 字段**,默认 0.00。这是一致与已执行模板的折中 — 用户在部分成交场景往往尚未结算手续费(可能再次部分成交后由券商一次性扣);P0-4 选择"省略 = 0",由 P0-5 / P1-2 决定是否在日终对账时补对。第一阶段的简化代价由 P0-5 衔接。

#### 1.2.3 未执行(主动放弃)

**正则**:
```python
R_UNFILLED = re.compile(
    r"^未执行 "
    r"(?P<instruction_id>QM-\d{8}-\d{6}-\d{6}-(?:BUY|SELL)-\d{3}) "
    r"原因[::]\s?"
    r"(?P<reason>.{1,200})$",
    re.DOTALL,
)
```

**示例**:
```
未执行 QM-20260512-093001-600519-BUY-001 原因: 价格未到
未执行 QM-20260512-093001-600519-BUY-001 原因:账户余额不足
未执行 QM-20260512-103015-000001-SELL-002 原因: 临时改变主意
```

**字段约束**:
- `原因[::]` 接受全角中文冒号 `:` 与英文冒号 `:`(用户输入习惯差异容错;唯一允许的字符级容错点)
- 冒号后允许 0 或 1 个空格(`\s?`),与 `:` 紧贴或留一个空格都合法
- `reason` 长度 1-200 字符;允许任意字符(中英文 + 标点 + 换行,故用 `re.DOTALL`)
- `reason` 不参与状态变更逻辑,**仅入 ExecutionReport.reason 留痕**供前端复盘 / 后续 LLM 复盘(P2-2 自进化输入候选;但 P0-4 不允许 LLM 在解析路径读它)

> **为什么宽松对待 reason**:这是用户表达"为什么我没下"的自然语言出口,不影响 MockBroker 状态(未执行就是不动账户);硬限制为枚举会逼用户多次澄清,无收益。但仍设 200 字上限防异常长串。

#### 1.2.4 更正(amend / overwrite 之前回报)

**正则**:在 §1.2.1 / §1.2.2 / §1.2.3 任一正则前加 `^更正 ` 前缀。等价为:

```python
R_AMEND_FILLED  = re.compile(r"^更正 " + R_FILLED.pattern[1:])
R_AMEND_PARTIAL = re.compile(r"^更正 " + R_PARTIAL.pattern[1:])
R_AMEND_UNFILLED = re.compile(r"^更正 " + R_UNFILLED.pattern[1:])
```

(实施期可改写为 `re.compile(rf"^更正 (?:{R_FILLED.pattern[1:-1]}|{R_PARTIAL.pattern[1:-1]}|{R_UNFILLED.pattern[1:-1]})$", re.DOTALL)` 一条复合,P0-4 不约束实施细节。)

**示例**:
```
更正 已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 成交价 1677.80 手续费 5.00
更正 部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 80股 成交价 1678.20 剩余未成交 20股
更正 未执行 QM-20260512-093001-600519-BUY-001 原因: 实际是涨停未能成交
```

**字段约束**:
- `instruction_id` 必须**已存在**且当前 status ∈ `{FILLED, REJECTED, EXPIRED, AMBIGUOUS}`(终态或可终态);若是 `DRAFT / VALIDATED / DISPATCHED`,AMBIGUOUS(澄清飞书提示"该指令尚未派发或仍在等待回报,无需更正,直接发回报即可")
- 必须在**当日 16:00 Asia/Shanghai 之前**到达解析器;之后到达即 AMBIGUOUS
- 更正可以反复(用户连续发两条更正,第二条覆盖第一条);每条更正都通过 ExecutionReportApplier 反向 + 重应用

#### 1.2.5 盘后补录(post-close report)

**正则**:在 §1.2.1 / §1.2.2 / §1.2.3 任一正则前加 `^盘后补录 ` 前缀(语法同 §1.2.4 更正)。

**示例**:
```
盘后补录 已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 成交价 1678.50 手续费 5.00
盘后补录 未执行 QM-20260512-103015-000001-SELL-002 原因: 临时去开会忘了下单
```

**字段约束**:
- `instruction_id` 必须**已存在**且当前 status ∈ `{EXPIRED, AMBIGUOUS, DISPATCHED}`(从未成功回报过的);若是 `FILLED / REJECTED`,用 `更正` 而非 `盘后补录`,否则 AMBIGUOUS(澄清飞书提示)
- 必须在**当日 16:00 Asia/Shanghai 之前**到达;之后即 AMBIGUOUS
- 单条 instruction_id 的盘后补录**只接受一次**(避免反复"补录"语义混乱);需要修正前一条盘后补录就发"更正 ..."

> **为什么区分"更正"与"盘后补录"两个前缀**:语义不同 — `盘后补录` 是对未回报指令的首次回报(状态机:EXPIRED/AMBIGUOUS/DISPATCHED → FILLED/REJECTED);`更正` 是对已回报指令的覆盖(状态机:终态 → 终态)。从用户视角清晰区分意图,从代码视角清晰区分 MockBroker 应用路径(盘后补录直接 apply,更正先 reverse 再 apply)。

### 1.3 InstructionStatus 状态机扩展(锁定)

P0-3 §1.1.1 已锁定 7 个状态值与 5 条迁移。P0-4 在此基础上**新增**:

#### 1.3.1 P0-3 已锁的迁移(回顾)

```
DRAFT → VALIDATED          (RiskEngine 通过)
DRAFT → REJECTED           (RiskEngine 否决 / parse_ok=False)
VALIDATED → DISPATCHED     (ModeRouter 派发)
DISPATCHED → FILLED        (回报路径锁定前的占位,P0-4 落地为完整路径)
DISPATCHED → EXPIRED       (valid_until 超时未回报)
DISPATCHED → REJECTED      (用户飞书"未执行")
DISPATCHED → AMBIGUOUS     (P0-4 fail-closed 路径占位)
```

#### 1.3.2 P0-4 新增的迁移

```
# 解析路径(回报到达后的细化)
DISPATCHED → AMBIGUOUS            # 正则不通过 / field cross-check 失败
DISPATCHED → FILLED               # 已执行匹配成功,filled_volume == volume
DISPATCHED → REJECTED             # 未执行匹配成功
DISPATCHED → 部分填充态(*)        # 部分执行匹配成功,见 §1.3.3

# 澄清后回到正轨
AMBIGUOUS → DISPATCHED            # 用户重发了正确格式(回到等待解析)
                                  # 实施细节:解析成功后直接同步迁移到对应终态,
                                  # 此处保留 DISPATCHED 中转是为状态机守门可见性
AMBIGUOUS → FILLED                # 重发"已执行"
AMBIGUOUS → REJECTED              # 重发"未执行"
AMBIGUOUS → EXPIRED               # 用户始终不重发,valid_until 也已过

# 终态间迁移(更正 / 盘后补录路径,16:00 内允许)
FILLED → REJECTED                 # 更正"未执行"覆盖原"已执行"
REJECTED → FILLED                 # 更正"已执行"覆盖原"未执行"
EXPIRED → FILLED                  # 盘后补录"已执行"
EXPIRED → REJECTED                # 盘后补录"未执行"

# 已 FILLED 的更正(状态不变,仅 MockBroker 反向+重应用)
FILLED → FILLED                   # 更正"已执行"(变更 fill_price / volume)
                                  # 由状态机记 transition,同状态合法
```

#### 1.3.3 部分填充态的处理

P0-4 **不引入新的 `PARTIAL_FILLED` 枚举值**(避免扩张 P0-3 已锁的 7 状态集合)。部分执行的语义在 ExecutionReport 层表达:

- `ExecutionReport.kind = PARTIAL` 表明部分成交,记录 filled_volume / remain_volume
- `InstructionPlan.status` 在部分执行时**进入 `FILLED`**,语义为"该指令已结案,系统不再发追问"(剩余未成交部分由下一轮 simulation_auto 研判决定是否新建 InstructionPlan,**不**自动续派)
- 这与 P0-1 §1.5 "未回报的不更新 / 用户选择不执行的不更新" 一致 — 部分成交的"未执行部分"系统视为用户主动放弃,不再追问

> **设计原理**:引入 PARTIAL_FILLED 状态会让追问/超时/盘后补录每一条迁移都翻倍(FILLED ↔ PARTIAL_FILLED ↔ EXPIRED ↔ ...),状态机复杂度爆炸。部分成交在 A 股手工执行场景中本身比较少见(限价单要么全成要么不成,部分成交主要在大单分批撮合);用 ExecutionReport 字段区分足够,不污染 InstructionPlan.status。

#### 1.3.4 状态机守门函数(伪代码)

```python
# backend/services/instruction_plan_state_machine.py(实施期)

ALLOWED_TRANSITIONS: frozenset[tuple[InstructionStatus, InstructionStatus]] = frozenset({
    # P0-3 锁定
    (DRAFT, VALIDATED),
    (DRAFT, REJECTED),
    (VALIDATED, DISPATCHED),
    (DISPATCHED, FILLED),
    (DISPATCHED, EXPIRED),
    (DISPATCHED, REJECTED),
    (DISPATCHED, AMBIGUOUS),

    # P0-4 新增
    (AMBIGUOUS, DISPATCHED),
    (AMBIGUOUS, FILLED),
    (AMBIGUOUS, REJECTED),
    (AMBIGUOUS, EXPIRED),
    (FILLED, REJECTED),
    (REJECTED, FILLED),
    (EXPIRED, FILLED),
    (EXPIRED, REJECTED),
    (FILLED, FILLED),  # 更正同状态(MockBroker reverse+reapply)
    (REJECTED, REJECTED),  # 更正同状态(变更 reason)
})

def transition(
    plan: InstructionPlan,
    target: InstructionStatus,
    *,
    reason: str,
    triggered_by: str,
) -> InstructionPlan:
    """状态机守门:校验迁移合法 + 记录 state_transitions + 返回 frozen 新对象。"""
    if (plan.status, target) not in ALLOWED_TRANSITIONS:
        raise InvalidTransitionError(
            f"{plan.instruction_id}: {plan.status} → {target} not allowed; "
            f"reason={reason}, triggered_by={triggered_by}"
        )
    # 16:00 Asia/Shanghai cutoff 守门(P0-4 §1.6)
    if _is_post_close(plan, target):
        raise PostCloseFreezeError(
            f"{plan.instruction_id}: {plan.status} → {target} after 16:00; "
            f"reason={reason}"
        )
    # state_transitions collection 留痕
    _record_transition(plan, target, reason, triggered_by)
    return plan.model_copy(update={"status": target})
```

### 1.4 追问机制(锁定)

#### 1.4.1 时序

```
T0  = InstructionPlan.dispatched_at (ModeRouter 发出后)
T_VU = InstructionPlan.valid_until
T_CHASE = T0 + 30 minutes
T_GRACE_THRESHOLD = 10 minutes

if T_VU - T0 < 30 minutes:
    # 短追问宽限:30 分钟内就会过期,不发追问
    skip_chase = True
else:
    skip_chase = False

每分钟轮询 status=DISPATCHED 的 plan:
    if now ≥ T_VU:
        transition(plan, EXPIRED, reason="valid_until reached", triggered_by="ChasePoller")
    elif (not skip_chase) and (now ≥ T_CHASE) and (not plan.chased_at):
        send_chase_message(plan)
        plan.chased_at = now  # 字段在 InstructionPlan 之外的 plan_runtime collection,不污染 frozen schema
```

#### 1.4.2 追问飞书消息模板(预写死)

```python
CHASE_TEMPLATE = """\
【QuantMind 提醒】
30 分钟前发出的指令 {instruction_id} 尚未收到回报。
请回复(任选其一,严格语法):

已执行 {instruction_id} {side_zh} {stock_code} {volume}股 成交价 {limit_price} 手续费 0.00
部分执行 {instruction_id} {side_zh} {stock_code} 60股 成交价 {limit_price} 剩余未成交 40股
未执行 {instruction_id} 原因: 价格未到

若到 {valid_until_hhmm} 仍无回报,系统将自动标记为已过期。"""
```

#### 1.4.3 追问与 AMBIGUOUS 的关系

- AMBIGUOUS 期间**不发追问**(系统已发了澄清飞书,不再叠加噪声)
- AMBIGUOUS → DISPATCHED 后追问计时**不重置**(以原 dispatched_at 为基准),避免用户反复发错触发系统反复追问

#### 1.4.4 已知边界条件

- 用户在 dispatched_at + 1 分钟时就回报了"已执行"(短回路):状态正常 DISPATCHED → FILLED,不会触发追问
- 用户在 dispatched_at + 31 分钟时回报"已执行"(刚好越过 30 分钟):追问已经发出但用户回报已到,状态 DISPATCHED → FILLED;追问飞书成为冗余信息但不影响状态(可接受)
- valid_until - dispatched_at < 30 分钟(开盘 14:30 后下达 valid_until=14:55 的指令):短追问宽限触发,直接等过期

### 1.5 澄清飞书消息模板(预写死,LLM 不参与)

#### 1.5.1 普通解析失败模板(MOST COMMON)

```python
CLARIFICATION_TEMPLATE_GENERIC = """\
【QuantMind 解析失败】
您 {received_at_hhmmss} 的回报无法解析。请严格按以下任一格式重发(可复制粘贴):

已执行 QM-... 买入/卖出 600519 100股 成交价 1678.50 手续费 5.00
部分执行 QM-... 买入/卖出 600519 60股 成交价 1678.50 剩余未成交 40股
未执行 QM-... 原因: 价格未到

需要更正之前的回报: 在以上格式前加"更正 "
盘后补录(14:55 之后到 16:00): 在以上格式前加"盘后补录 "

注意:
- instruction_id 必须严格匹配 QM-YYYYMMDD-HHMMSS-代码-BUY/SELL-序号 格式
- 字段间用单个空格分隔
- 股数必须是 100 整数倍
- 16:00 之后 系统不再接受当日回报,请次日发新指令"""
```

#### 1.5.2 字段交叉校验失败的细化模板

正则匹配通过但字段冲突时,系统能给更精准的反馈:

```python
CLARIFICATION_TEMPLATE_CROSSCHECK = """\
【QuantMind 解析失败】
您的回报格式正确,但 {field} 字段与原指令不符:
  - 原指令 {field}: {expected}
  - 您的回报 {field}: {actual}

请复制以下模板修正后重发:
{suggested_template}"""
```

`{field}` 取值范围(枚举):
- `stock_code` — 股票代码不一致
- `side` — 方向不一致(回报"买入"但 instruction_id 是 SELL)
- `volume` — filled_volume + remain_volume ≠ InstructionPlan.volume
- `volume_lot` — 股数不是 100 整数倍

#### 1.5.3 状态不允许该回报类型的细化模板

```python
CLARIFICATION_TEMPLATE_WRONG_STATE = """\
【QuantMind 解析失败】
{instruction_id} 当前状态为 {current_status},不能接受 "{report_kind}" 类型回报。
{guidance}"""
```

`{guidance}` 由代码层根据当前状态 + 回报类型生成(枚举有限):
- `current_status=FILLED, report_kind=盘后补录` → "该指令已成交,如需修改请用'更正 已执行 ...'"
- `current_status=DISPATCHED, report_kind=更正` → "该指令尚未回报,无需更正,直接发回报即可"
- `current_status=DRAFT/VALIDATED, report_kind=*` → "该指令尚未派发,系统未发出过此 instruction_id"(罕见,用户复制粘贴错误时出现)

#### 1.5.4 16:00 之后的细化模板

```python
CLARIFICATION_TEMPLATE_POST_CLOSE = """\
【QuantMind 解析失败】
当前时间已超过 16:00,本日 InstructionPlan 状态已冻结,无法接受任何回报。
您的原文已留痕(execution_reports collection)供次日人工对账。
如有需要,请次日盘前发新指令(系统自动按交易日发新 instruction_id)。"""
```

#### 1.5.5 澄清飞书路由

- **必须走主通道**(P0-2 自建应用)— 备用 webhook 严禁发澄清(P0-2 §2 红线 2)
- 主通道失活时:**不发澄清**,但仍把 ExecutionReport.parse_ok=False 入库;系统通过备用 webhook 发"长连接异常,无法回复用户"告警;用户通过其他渠道(电话/前端)感知

### 1.6 16:00 cutoff 与冻结语义(锁定)

#### 1.6.1 cutoff 定义

`cutoff = received_at.astimezone(ZoneInfo("Asia/Shanghai")).replace(hour=16, minute=0, second=0, microsecond=0)`

- `received_at` 是 ExecutionReport 进入 ExecutionReportParser 的时刻(由长连接 worker 入队时刻派生)
- A 股交易日 / 非交易日的处理:cutoff 始终用今日 16:00;非交易日(节假日)系统不应发指令,InstructionPlan 数量为 0,自然不存在过期回报问题(P0-1 §1.6 + simulation_auto 数据采集层守门)

#### 1.6.2 cutoff 后的行为

| 触发场景 | 16:00 之前 | 16:00 之后 |
|---------|-----------|-----------|
| 任何回报到达解析器 | 按 §1.2 正则解析 + 状态机迁移 | 一律 AMBIGUOUS + §1.5.4 模板回复 + 不更新 MockBroker |
| ChasePoller 仍在跑 | 检查 valid_until,自动 EXPIRED | valid_until 必然已过(14:55 cutoff < 16:00),所有 DISPATCHED 已 EXPIRED;ChasePoller 在 16:00 后空跑 |
| ExecutionReportApplier | 正常应用 | **不应用**(状态机抛 PostCloseFreezeError) |
| 前端 InstructionCenter | 实时刷新 | 切换为"日终复盘"视图,显示当日所有 InstructionPlan 终态 |

#### 1.6.3 与 P0-5 日终对账的衔接

- 16:00 cutoff 是"硬冻结",但日终对账(P0-5 范围)是 16:00 之后才发的
- 用户在日终对账中发现 MockBroker 镜像与真实账户偏差时,**不能再用回报路径修复**;P0-5 决定是否引入"日终对账偏差更正"专用语法,与本决策的"更正"路径解耦

### 1.7 与 MockBroker 的衔接(高层方向)

P0-4 仅锁定"允许 + 必须经状态机守门 + 16:00 cutoff",MockBroker 持久化与撮合细节属 P1-2 范围。但本节给出 ExecutionReportApplier 的高层伪代码以确保 P0-4 与 P1-2 接口契合:

```python
# backend/services/execution_report_applier.py(实施期)

def apply_report(report: ExecutionReport, plan: InstructionPlan) -> InstructionPlan:
    """把成功解析的 ExecutionReport 应用到 MockBroker,返回新 status。

    更正路径:先 reverse 原 ExecutionReport(若有),再 apply 新的。
    盘后补录路径:直接 apply(因为原状态是 EXPIRED/AMBIGUOUS,无原成交)。
    """
    if report.is_amendment:
        # 找到该 instruction_id 之前最近一条已应用的 ExecutionReport
        prev = find_last_applied_report(plan.instruction_id)
        if prev is not None:
            mock_broker.reverse(prev)  # P1-2 范围

    if report.kind == FILLED:
        mock_broker.apply_fill(plan, fill_price=report.fill_price, volume=report.volume, fee=report.fee)
        new_status = FILLED
    elif report.kind == PARTIAL:
        mock_broker.apply_fill(plan, fill_price=report.fill_price, volume=report.filled_volume, fee=0.0)
        new_status = FILLED  # §1.3.3 部分填充也归 FILLED
    elif report.kind == UNFILLED:
        # 不变更 MockBroker,只入 ledger
        new_status = REJECTED
    else:
        raise ValueError(f"Unknown report kind: {report.kind}")

    return state_machine.transition(
        plan, new_status,
        reason=f"{report.kind} report received",
        triggered_by=f"ExecutionReportApplier:{report._id}",
    )
```

**反向操作的边界条件**(P1-2 范围,本决策仅记录):
- BUY 已执行的反向 = 减持仓 + 加现金(扣除手续费)
- SELL 已执行的反向 = 加持仓 + 减现金(加回手续费)
- 部分执行的反向 = 按比例反向已成交部分
- 反向后 MockBroker 状态必须等价于"该 ExecutionReport 从未应用过"
- 反向操作必须**幂等**(P1-2 实施期保证)

### 1.8 与 P0-3 §1.6 模板的契合(回报示例已槽位对齐)

P0-3 §1.6 飞书指令模板末尾的"执行后请回报"示例**直接成为本决策 §1.2 的源头**,确保用户在飞书指令中看到的回报模板与实际解析器接受的格式一一对应。本节明确两份模板的对齐点:

| P0-3 §1.6 模板示例(用户看到) | P0-4 §1.2 解析器(系统接受) |
|------------------------------|----------------------------|
| `已执行 QM-... 买入 600519 100股 成交价 1678.50 手续费 5.00` | §1.2.1 R_FILLED 正则 |
| `部分执行 QM-... 买入 600519 60股 成交价 1678.50 剩余未成交 40股` | §1.2.2 R_PARTIAL 正则 |
| `未执行 QM-... 原因: 价格未到` | §1.2.3 R_UNFILLED 正则 |
| (P0-3 §1.6.3 留给 P0-4 的扩展点) | §1.2.4 更正 + §1.2.5 盘后补录 |

renderer.py 在第一阶段渲染指令时**仅展示**已执行 / 部分执行 / 未执行 三条示例(避免初学者被太多选项困扰)。更正与盘后补录在解析失败的澄清飞书(§1.5.1)中向用户介绍,这是符合"渐进披露"的 UX 原则。

## 2. 红线 / 边界(立即生效)

P0-4 落地后这些立即成为代码硬约束:

1. **严格正则不通过 = AMBIGUOUS**:任何不满足 §1.2 正则 + 字段交叉校验的回报,InstructionPlan.status 必须迁移到 AMBIGUOUS;**绝不更新 MockBroker**;违规即红线违规
2. **LLM 严禁参与回报路径**:`backend/services/execution_report.py` / `execution_report_applier.py` 严禁 `import backend.llm.*`;澄清飞书文案严禁通过 LLM 生成(全部预写死,§1.5);实施期由 lint rule + 集成测试守门(类似 `backend/risk/` 的 LLM 隔离原则)
3. **绝不猜测 instruction_id**:回报文本中的 `instruction_id` 必须严格通过 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$` 匹配;系统**不**通过"用户最近回报的指令"等上下文反推;格式不符即 AMBIGUOUS
4. **side_zh ↔ instruction_id BUY/SELL 必须一致**:不一致即 AMBIGUOUS(field cross-check fail),澄清飞书走 §1.5.2
5. **stock_code 必须与 InstructionPlan.stock_code 一致**:不一致即 AMBIGUOUS
6. **股数 lot size 必须 = 100 整数倍**:违反即 AMBIGUOUS(继承 P0-3 §2 红线 4)
7. **filled_volume + remain_volume == InstructionPlan.volume**(部分执行):违反即 AMBIGUOUS
8. **16:00 Asia/Shanghai cutoff 后任何回报触发 AMBIGUOUS**:状态机抛 `PostCloseFreezeError`;违规即红线违规
9. **更正路径仅允许从终态出发**:`{FILLED, REJECTED, EXPIRED, AMBIGUOUS}`;`DRAFT/VALIDATED/DISPATCHED` 阶段收到"更正 ..."即 AMBIGUOUS(澄清飞书提示"该指令尚未回报")
10. **盘后补录路径仅允许对未回报指令**:`{EXPIRED, AMBIGUOUS, DISPATCHED}`;`FILLED/REJECTED` 收到"盘后补录"即 AMBIGUOUS(澄清飞书提示用"更正"代替)
11. **盘后补录每个 instruction_id 仅允许一次**:第二次"盘后补录"即 AMBIGUOUS(澄清提示用"更正"代替)
12. **追问最多发 1 次**:per InstructionPlan;追问已发后再次到 30 分钟阈值不重发;短追问宽限(`valid_until - dispatched_at < 30 min`)直接跳过追问
13. **澄清飞书严禁走备用 webhook**(继承 P0-2 §2 红线 2:备用 webhook 仅发系统告警);主通道失活时不发澄清,只发告警
14. **状态机迁移必须经守门函数**:任何 `model_copy(update={"status": ...})` 直接绕过 `transition()` 即红线违规;实施期由 lint rule 阻止
15. **`feishu` / `lark` / `larksuite` 关键字在 `backend/risk/` 子树严禁出现**(继承 P0-1 §2 红线 8 / P0-2 §2 红线 10)
16. **ExecutionReport 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 immutability 原则)

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-4 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 新增项(代码级)

- `backend/services/execution_report.py`:
  - `ExecutionReportKind` StrEnum(`FILLED` / `PARTIAL` / `UNFILLED`)
  - `ExecutionReport` frozen Pydantic 模型(`_id` / `instruction_id` / `kind` / `is_amendment` / `is_post_close` / `filled_volume` / `remain_volume` / `fill_price` / `fee` / `reason` / `received_at` / `parse_ok` / `raw_text`)
  - `parse_report(raw_text: str, received_at: datetime) -> ExecutionReport | AmbiguousReport`
  - 五条正则常量(§1.2.1-§1.2.5)
- `backend/services/execution_report_applier.py`:
  - `apply_report(report, plan) -> InstructionPlan`(§1.7 伪代码)
  - `find_last_applied_report(instruction_id) -> ExecutionReport | None`
- `backend/services/instruction_plan_state_machine.py`:
  - `ALLOWED_TRANSITIONS` frozenset(§1.3.4)
  - `transition(plan, target, *, reason, triggered_by) -> InstructionPlan` 守门函数
  - `_is_post_close(plan, target) -> bool` 16:00 cutoff 校验
  - `_record_transition(...)` 写入 `state_transitions` collection
  - `InvalidTransitionError` / `PostCloseFreezeError` 异常类
- `backend/services/chase_poller.py`:
  - 异步任务,每分钟扫 `status=DISPATCHED` 的 plan
  - 30 分钟阈值发追问 + valid_until 自动 EXPIRED + 短追问宽限
  - 追问消息模板预写死(§1.4.2)
- `backend/integrations/feishu/parser_dispatcher.py`(P0-2 §3.1 已规划新增):
  - 长连接 worker 收到事件 → ack → 入队 → 异步调用 `execution_report.parse_report`
- `backend/integrations/feishu/clarification.py`:
  - `ClarificationSender` — 路由到主通道发预写死澄清飞书
  - 五个澄清模板常量(§1.5.1-§1.5.4)+ generic / crosscheck / wrong_state / post_close
- 新 MongoDB collection `execution_reports`:
  - 索引 `(instruction_id)` + `(received_at)` + `(parse_ok, received_at)`(供前端 ambiguous 列表)
- 新 MongoDB collection `state_transitions`(P0-1 §3.2 已规划,P0-4 落地写入):
  - 索引 `(instruction_id, transitioned_at)` 复合
- 新 MongoDB collection `clarification_messages`:
  - 索引 `(instruction_id)` + `(sent_at)`,记录系统发出的澄清飞书全文与时间

### 3.2 修改项

- `backend/services/instruction_plan.py`:
  - InstructionPlan 不变(继续 frozen);新增 `plan_runtime` collection 存放可变 runtime 字段(`dispatched_at` / `chased_at` / `last_state_transition_at`),与 frozen InstructionPlan 解耦
  - 文档注释:status 字段的迁移须经过 `state_machine.transition()`,不允许直接 model_copy
- `backend/data/database.py`:
  - 新增 `save_execution_report()` / `query_execution_reports()` / `record_state_transition()` 方法
  - 新增 `update_plan_runtime(instruction_id, **fields)` 方法(写 plan_runtime collection)
- `backend/integrations/feishu/longconn.py`(P0-2 范围):
  - worker 入队接口对接到 `parser_dispatcher.dispatch(event)`
- `backend/integrations/feishu/__init__.py`:
  - 导出 `parser` / `parser_dispatcher` / `clarification`

### 3.3 配置项

- `config/execution_report.yaml`(新):
  - `chase_threshold_minutes: 30`
  - `chase_grace_minutes: 10`(短追问宽限阈值)
  - `chase_max_attempts: 1`
  - `cutoff_local_time: "16:00"`
  - `reason_max_length: 200`
  - `clarification_templates_path: "backend/integrations/feishu/templates/clarification/"`(可选,模板内嵌也可)
- `.env`:无新增(所有飞书凭证仍走 shell env / P0-2 §1.4)

### 3.4 文档同步(本决策落地立即执行,见 §5.1)

- `CLAUDE.md` §1.3 进度行(P0-4 ✅,下一站 P0-5)
- `CLAUDE.md` §2.1 P0-4 行(状态 + 决策文档列 + 备注)
- `CLAUDE.md` §3.1 红线节(同步本文 §2 红线中与现有红线不重叠的部分,尤其严格正则 + 状态机扩展 + 16:00 cutoff)
- `MEMORY.md` 索引新增 `project_run_mode_p0_4.md`
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_4.md`

### 3.5 测试覆盖(实施期任务)

- `tests/test_execution_report_parser.py`:
  - 五种回报正则 happy path(每种 ≥3 条样本)
  - 字段交叉校验失败(side mismatch / volume mismatch / stock_code mismatch / volume_lot fail)
  - 边界:`未执行` reason 200 字符 / 201 字符 / 中英文冒号 / 换行
  - 边界:多余空格 / Tab / 全角空格(预处理只 strip + 单空格归一,其他 AMBIGUOUS)
  - 更正与盘后补录前缀的 happy path 与 wrong-state path
- `tests/test_instruction_plan_state_machine.py`:
  - 全 ALLOWED_TRANSITIONS 路径(P0-3 锁的 5 条 + P0-4 新增的 ~15 条)
  - 跨态拒绝路径(DRAFT → FILLED 等)
  - 16:00 cutoff PostCloseFreezeError 路径
- `tests/test_execution_report_applier.py`:
  - 已执行 → MockBroker 减/加现金 / 持仓
  - 部分执行 → 按 filled_volume 应用
  - 未执行 → 不变更 MockBroker
  - 更正 → reverse + reapply 幂等性
  - 盘后补录 → 直接 apply
- `tests/test_chase_poller.py`:
  - 30 分钟阈值发追问
  - 短追问宽限不发追问
  - valid_until 触发 EXPIRED
  - 追问已发不重发
- `tests/test_clarification_sender.py`:
  - 五个模板渲染正确
  - 主通道失活时不发澄清
- 覆盖率:`backend/services/execution_report.py` >90% / `instruction_plan_state_machine.py` >95%(关键路径)

### 3.6 静态检查 / lint rule(实施期任务)

- 阻止 `backend/services/execution_report*.py` / `backend/integrations/feishu/parser*.py` 出现 `import backend.llm`(LLM 隔离)
- 阻止任何文件 `model_copy(update={"status": ...})` 不经过 `state_machine.transition()`(状态机绕过)
- 阻止 `backend/integrations/feishu/clarification.py` 接收非预定义模板字符串(LLM 输出污染澄清)
- 阻止 `backend/integrations/feishu/fallback_webhook.py` 接收 ClarificationMessage 类型(继承 P0-2 §2 红线 2)

## 4. 决策依据

### 4.1 audit 引用

- audit §3.2 当前缺口"飞书回报模块缺失" — 本决策 §1.1 通过 ExecutionReportParser 补齐
- audit §5.2 "TradingSignal 缺回报模板" — P0-3 §1.6 已锁模板,P0-4 §1.2 锁定解析器
- audit §7 飞书集成全无 — P0-2 锁了主备通道,P0-4 锁了解析层
- audit §8.3 "RiskEngine 未接订单链路" — 本决策的状态机守门 + ExecutionReportApplier 把回报路径接入 MockBroker;RiskEngine 在 InstructionPlan 创建时已贯穿(P0-3),回报路径不再二次校验风控
- audit §9 推荐 `decision_ledger` 全链记录 — 本决策的 `state_transitions` / `execution_reports` / `clarification_messages` 三个 collection 都进入 decision_ledger
- audit §15 "当前不建议做的事:让 LLM 直接决定账户状态更新" — 本决策 §1.1.2 严格收紧 LLM 角色边界,完全不参与回报路径

### 4.2 决策清单引用(§P0-4)

- 决策清单 §P0-4 列出 9 种回报形态需求 — 本决策覆盖 8 种(已全部执行 / 部分执行 / 未执行 / 执行价格不同 / 股数不同 / 临时改为不操作 / 盘后补录 / 手工更正);第 9 种"券商拒单"语义上等同"未执行 原因: 券商拒单",不单独建模(§1.2.3 reason 字段允许任意短文本)
- 决策清单 §P0-4 建议倾向"先采用严格文字模板,后续再加卡片按钮" — 本决策完全采纳;卡片按钮留 P1-3 / 后续 amendment
- 决策清单 §P0-4 歧义处理红线"解析不出 instruction_id / 股票代码 / 方向 / 股数 / 成交价时,系统不能更新持仓,只能发飞书要求澄清" — 本决策 §2 红线 1-7 完全继承
- 决策清单 §P0-4 产出物清单 — 本决策已对照覆盖:回报模板(§1.2)+ 允许自然语言范围(§1.2.3 reason 允许 200 字短文本)+ 哪些字段缺失必须追问(§1.5.2 字段交叉校验)+ 是否允许盘后批量补录(§1.2.5 锁定到 16:00)+ 是否允许系统根据上下文猜测指令编号(§2 红线 3 锁定不允许)

### 4.3 代码事实抽检(2026-05-09 复核)

- `backend/agents/fund_manager.py:16-60` `_parse_signal` 的 `parse_ok=False` 强制降级范式 — 本决策 §1.1.2 直接借鉴
- `backend/integrations/feishu/` 在仓库中**不存在** — 本决策与 P0-2 §3.1 一起规划全新子树
- `backend/services/execution_report*.py` / `instruction_plan_state_machine.py` 在仓库中**不存在** — 全新增,实施期写入
- `backend/risk/engine.py:41-82` `validate_order` 签名 — 本决策**不修改**(回报路径不二次走风控,因 InstructionPlan 创建时已通过 RiskEngine;P0-3 §1.7.1 流程已锁)
- `backend/broker/models.py::Order` — 本决策**不修改**;ExecutionReport 是新模型,与 Order 解耦(Order 是"模拟撮合的输入",ExecutionReport 是"用户回报的事实")
- `backend/monitoring/alerter.py` — 本决策**不修改**;澄清飞书走 P0-2 主通道,与 alerter 备用 webhook 解耦

### 4.4 用户选择记录(2026-05-09 决策对话)

| 问题 | 选择 |
|------|------|
| ExecutionReportParser 解析策略? | **A: 严格正则 only,澄清文案预写死** — 与 fund_manager._parse_signal fail-closed 范式对齐;LLM 完全不参与回报路径 |
| 回报追问超时与最大次数? | **盘中 30 分钟 + 1 次追问;到 valid_until 仍无回报 → EXPIRED** — 短追问宽限 10 分钟规避临近 cutoff 的无效追问 |
| 回报模板严格度与自然语言范围? | **完全严格,仅"未执行 ... 原因:"后允许任意 ≤200 字短文本** — 已执行/部分执行模板字面字段必须严格 |
| 盘后补录与回报更正时窗? | **允许盘后补录到当日 16:00 + 更正到当日 16:00** — 16:00 之后 InstructionPlan 当日变更冻结 |

第一题用户初次回答"我不太理解这个问题",给出三方案对比解释后重新选 A;选择 A 与 P0-1 §1.6 红线 + P0-3 §1.7.2 LLM 角色边界 + 本项目"绝不让 LLM 直接决定账户状态"原则完全一致。

### 4.5 与 P0-1 / P0-2 / P0-3 的契合点对照

| 上游决策条款 | P0-4 承载方式 |
|-------------|--------------|
| P0-1 §1.3.1 #4 解析与确认 | 长连接 worker → ExecutionReportParser → §1.2 五种正则;歧义则 §1.5 澄清飞书 |
| P0-1 §1.5 InstructionPlan 路由规则 / 用户选择性不执行 | 状态机 §1.3:DISPATCHED → REJECTED("未执行")/ EXPIRED(超时未回报);REJECTED/EXPIRED 不变更 MockBroker |
| P0-1 §1.6 多 Agent 辩论是 InstructionPlan 唯一生成路径 | 不影响 P0-4(回报路径不参与 InstructionPlan 生成);但 §2 红线 2 LLM 严禁参与回报路径与之精神一致 |
| P0-1 §2 红线 8 风控隔离不变 | RiskEngine 不参与回报路径;ExecutionReportApplier 不调用 RiskEngine(InstructionPlan 创建时已贯穿) |
| P0-2 §1.1 长连接 worker 3 秒 ack | worker 仅 ack + 入队;ExecutionReportParser 异步消费(§1.1.1) |
| P0-2 §1.5 切换衔接 | 切换初始化对账(P0-1 §1.3.1 #4)走同一 ExecutionReportParser,但用专用解析路径(对账模板属 P0-5 范围) |
| P0-2 §3.1 ExecutionReportParser 归属 | 本决策 §3.1 给出 `backend/services/execution_report.py` + `backend/integrations/feishu/parser_dispatcher.py` 双层结构 |
| P0-3 §1.1.1 InstructionStatus 状态机 | §1.3 在 P0-3 5 条迁移基础上扩展 ~15 条;不引入新状态值(§1.3.3) |
| P0-3 §1.4.2 超时语义留给 P0-4 | §1.4 锁定 30 分钟 + 1 次追问 + 短追问宽限 + valid_until 自动 EXPIRED |
| P0-3 §1.6 模板回报示例 | §1.8 槽位对齐表;§1.2 三条主正则源自 P0-3 §1.6 用户视角的回报模板 |
| P0-3 §1.6.3 留给 P0-4 的扩展点 | §1.2.4 更正前缀 + §1.2.5 盘后补录前缀;§1.3.2 状态机扩展;§1.5.1-5 澄清飞书五模板 |

### 4.6 替代方案与拒绝理由

| 候选方案 | 拒绝原因 |
|---------|---------|
| LLM 辅助解析自然语言变体 | 引入 LLM 错译风险污染 MockBroker;违反 P0-1 §1.6 / P0-3 §1.7.2 LLM 角色边界;复杂度增加但收益有限(用户使用模板的边际成本极低) |
| LLM 临场生成澄清飞书文案 | LLM 调用增加成本与超时;预写死模板已能覆盖所有澄清场景(§1.5.1-§1.5.4 五模板);第一阶段无必要引入 LLM |
| 引入 PARTIAL_FILLED 新状态值 | 状态机迁移路径翻倍(P0-3 5 条 + P0-4 15 条 → 30+ 条);A 股手工执行场景中部分成交少见;ExecutionReport 字段已能区分,不污染 InstructionPlan.status |
| 部分执行允许"剩余 N 股"等多种语法变体 | 增加正则与维护成本;P0-3 §1.6 模板已示例"剩余未成交 N股",用户复制即可;模板严格度与"完全严格"用户选择一致 |
| 部分执行允许带手续费字段 | 与已执行模板对齐则更长;部分执行手续费在 A 股手工场景常未结算;P0-5 日终对账可补对(本决策 §1.2.2 已留备注) |
| 允许盘后补录到次日 09:00 | 跨日带来 EXPIRED 复活 + 当日/次日账户镜像差异 + 污染 simulation_auto 当日复盘的复杂度;用户选择"当日 16:00"已能覆盖盘后整理场景 |
| 不区分"更正"与"盘后补录"两个前缀 | 语义混淆:更正是"覆盖已有",补录是"首次回报";状态机入口路径不同(更正必须先 reverse);代码层维护成本反而更高 |
| 16:00 cutoff 后允许任何回报触发补对账 | 与 P0-5 日终对账职责冲突;cutoff 后日终对账模板会汇总当日 MockBroker 镜像与真实账户差异,通过专用语法(P0-5 范围)修正;P0-4 不重复实现 |
| 允许系统根据用户最近指令猜测 instruction_id | 决策清单 §P0-4 明确"是否允许根据聊天上下文猜测指令编号" — 用户原始倾向为否;猜测一旦错误就把成交错关联到无关指令,污染 MockBroker;所有用户飞书指令都已带完整 instruction_id(§P0-3 §1.6 模板),复制粘贴零成本 |
| 追问 2 次 | 飞书噪声翻倍,"未执行"决定本身比"再追问一次"更有效率(用户主动放弃只需要一句"未执行 原因");短追问宽限规避临近 cutoff 噪声 |
| 追问 60 分钟阈值 | 9:30 开盘下达的指令 valid_until 14:55(5 小时 25 分钟),60 分钟阈值意味着 10:30 才追问,接近 14:55 cutoff 还在等用户;30 分钟在用户体验与噪声之间最平衡 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-4 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步(本 PR 内随决策一起提交)

- [x] 写入本决策文档(`docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`)
- [ ] 更新 `CLAUDE.md` §1.3:P0-4 状态从 ⏳ 改为 ✅,链接本文件,下一站改 P0-5
- [ ] 更新 `CLAUDE.md` §2.1:P0-4 行 决策文档列填本文件路径,备注列收紧为决策结果摘要
- [ ] 更新 `CLAUDE.md` §3.1 红线节:同步本文 §2 红线中与现有红线不重叠的部分(尤其严格正则 + AMBIGUOUS fail-closed + LLM 严禁参与回报路径 + 状态机扩展 + 16:00 cutoff)
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_4.md` 条目
- [ ] 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_4.md`
- [ ] commit 本决策文档 + CLAUDE.md/MEMORY.md 同步更新(单 PR);**等用户授权再 commit**;不自动 push

### 5.2 依赖本决策的下游 P0/P1 决策

- **P0-5 账户状态来源与对账机制**:决定 16:00 之后日终对账模板 + 偏差阈值 + 是否引入"对账更正"专用语法(与本决策 §1.6.3 衔接)
- **P0-7 风险红线与指导强度**:决定盘中 30 分钟追问、连续 N 次未执行是否触发"暂停发指令"红线
- **P0-10 LLM 角色边界**:本决策 §1.1.2 已严格收紧 LLM 在回报路径的边界,P0-10 全局对照时本节直接引用
- **P1-1 新核心数据模型**:本决策 §3.1 已规划 `execution_reports` / `state_transitions` / `clarification_messages` 三个新 collection 的索引;P1-1 进一步锁字段级 schema 与查询 API
- **P1-2 MockBroker 持久化与实时估值**:本决策 §1.7 给出 ExecutionReportApplier 的 reverse + reapply 高层路径;P1-2 锁定 MockBroker 反向操作的撮合细节与幂等性保证
- **P1-3 飞书消息形态**:本决策 §1.5 五种澄清模板均纯文本;P1-3 锁第二阶段卡片回报的演进路径(若回报按钮卡片要引入,需要在 ExecutionReportParser 增加 `card.action.trigger` 解析分支,属本决策范围外的扩展)
- **P1-4 回报解析策略**:本决策锁"严格正则 only";P1-4 评估是否值得在严格正则之上引入 LLM 辅助归一化(若评估收益不足以抵消复杂度,P1-4 维持 P0-4 决策)
- **P1-8 Kimi thinking 使用策略**:本决策严格排除 LLM 在回报路径,P1-8 不影响

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.6 编写 implementation 任务列表;`backend/services/execution_report*.py` + `backend/services/instruction_plan_state_machine.py` + `backend/services/chase_poller.py` 与 P0-2 中 `backend/integrations/feishu/longconn.py` / `parser_dispatcher.py` / `clarification.py` 紧密耦合,实施期统一规划 PR 边界
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级:新核心解析层 + 状态机 + 反向 MockBroker)
- [ ] 测试覆盖(§3.5 已列):
  - `tests/test_execution_report_parser.py` 五正则全路径 + 字段交叉校验 + 边界
  - `tests/test_instruction_plan_state_machine.py` 全 ALLOWED_TRANSITIONS + 跨态拒绝 + 16:00 cutoff
  - `tests/test_execution_report_applier.py` 应用 + 反向 + 幂等
  - `tests/test_chase_poller.py` 30 分钟 + 短宽限 + valid_until + 不重发
  - `tests/test_clarification_sender.py` 五模板 + 主通道失活
  - 覆盖率:`backend/services/execution_report.py` >90% / `instruction_plan_state_machine.py` >95%
- [ ] 静态检查 / lint rule(§3.6 已列):LLM 隔离 / 状态机绕过 / 澄清飞书污染 / 备用 webhook 误用
- [ ] 集成测试:从用户飞书发回报 → 长连接 worker ack → 入队 → ExecutionReportParser → ExecutionReportApplier → MockBroker → 状态机迁移 → decision_ledger 全链 e2e

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-4-amendment-{日期}-{原因}.md`。_
