# P0-3 — 操作指令结构(InstructionPlan)字段集与第一阶段飞书纯文本模板

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-3 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §3.2 / §5.2 / §5.3 / §8 / §9 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-3 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.5 / §1.6 / §2 红线 5) + `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(尤其 §1.3 第一阶段纯文本路径 + §1.6 P0-3 归属边界) |
| 替代       | 旧 `TradingSignal` 不再作为执行信号(继续保留作为多 Agent 辩论的中间产物) |

## 决策摘要

QuantMind 新增 `InstructionPlan` 作为**唯一可执行操作计划对象**,完全替代旧 `TradingSignal` 在执行链路中的角色(`TradingSignal` 仅作为多 Agent 辩论中 fund_manager 节点的中间结构化输出,不直接驱动撮合或飞书发送)。

四个核心边界已锁定:

1. **instruction_id 命名格式**:`QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq}`,长度 33-34 字符(BUY=33、SELL/HOLD=34),人读 + 飞书回报匹配 + 时间天然排序
2. **side 取值**:极简集合 `{BUY, SELL, HOLD}` + 单字段 `limit_price`(BUY=不高于上限 / SELL=不低于下限);**HOLD 不进 ModeRouter**(只入 ledger 复盘);加仓/减仓/区间/区间偏移留 amendment
3. **有效期**:仅当日盘中 + 分钟级,`valid_until ≤ created_at 当日 14:55 Asia/Shanghai`;feishu_off 超时跳过撮合、feishu_on 追问一次后 expired(追问超时具体时长 P0-4 决定)
4. **数据/证据/风控耦合**:折中绑定 — `data_snapshot_at` / `quote_source` / `news_source` / `position_summary` / 7-check 摘要 by-value 内嵌;完整 `evidence_ids[]` / `risk_validation_id` / `signal_id` / `analysis_record_id` by-reference

第一阶段飞书消息形态由 P0-2 §1.3 锁定为纯文本;本决策给出**唯一被允许的纯文本指令模板**(占位符 + 实例),由 `backend/integrations/feishu/renderer.py` 渲染,**禁止 LLM 自由拼接**。

InstructionPlan 是 `frozen=True` 的 Pydantic v2 模型(继承自 `backend/agents/models.py::TradingSignal` 的 immutability 规范);新增 `instruction_plans` MongoDB collection 持久化。`InstructionPlan → Order(broker.models)` 的派生函数让 `RiskEngine.validate_order` 能直接复用,**不修改 RiskEngine 入参签名**(P0-1 §2 红线 8 / 风控隔离不变)。

## 1. 决策具体内容

### 1.1 数据模型(Pydantic 严格 schema)

新增 `backend/services/instruction_plan.py`(命名待 P1,P0-3 仅锁字段集与语义)。所有模型 `model_config = ConfigDict(frozen=True)`,与 `backend/agents/models.py::TradingSignal`、`backend/broker/models.py::Order` 保持一致风格。

#### 1.1.1 枚举

```python
from enum import StrEnum

class InstructionSide(StrEnum):
    """指令方向。HOLD 不路由到 SimulationExecutor / FeishuMessenger。"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class InstructionStatus(StrEnum):
    """InstructionPlan 状态机。

    流转(只允许下列单向迁移,跨态即红线):
        DRAFT → VALIDATED → DISPATCHED → FILLED
        DRAFT → REJECTED   (RiskEngine 否决 / parse_ok=False)
        DISPATCHED → EXPIRED   (valid_until 超时未回报)
        DISPATCHED → REJECTED  (用户飞书明确回复"未执行")
        DISPATCHED → AMBIGUOUS (P0-4 fail-closed 路径)
    """
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    DISPATCHED = "DISPATCHED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
```

#### 1.1.2 内嵌子模型(by-value)

```python
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class DataSnapshot(BaseModel):
    """决策依赖的数据/资讯快照核心字段(by-value 内嵌)。

    完整原始数据(全字段行情/全文新闻/MiroFish 输出)走 by-reference,
    通过 InstructionPlan.evidence_ids 链回 MongoDB evidence collection。
    """
    model_config = ConfigDict(frozen=True)

    snapshot_at: datetime
    """数据采集时刻(必须 < InstructionPlan.created_at,数据先于决策)。"""

    quote_source: str
    """e.g. "adata" / "akshare" / "baostock"。"""

    quote_latency_ms: int | None = None
    """行情数据采集到本地落库的延迟(P0-8 数据可信度评估字段)。"""

    news_source: str
    """e.g. "akshare.stock_news_em"。"""

    news_window_seconds: int | None = None
    """资讯窗口:本指令纳入考虑的新闻发布时间范围(过去 N 秒)。"""

    prev_close: float | None = None
    """前收盘价(RiskEngine.price_reasonability 需要;市价单 / 缺数据时 None)。"""

    is_trading_day: bool
    """采集时刻是否 A 股交易日。"""

    is_trading_hours: bool
    """采集时刻是否 A 股交易时段。"""


class PositionSummary(BaseModel):
    """执行前后仓位摘要(by-value 内嵌,HOLD 时为 None)。

    数值由确定性代码从 MockBroker 当前快照 + 本指令派生计算,
    LLM 不允许直接产出这些字段(P0-1 §1.6 架构红线)。
    """
    model_config = ConfigDict(frozen=True)

    pre_position_pct: float = Field(ge=0.0, le=1.0)
    """执行前本股票仓位占总资产比例。"""

    post_position_pct: float = Field(ge=0.0, le=1.0)
    """执行后预估比例。"""

    pre_total_position_pct: float = Field(ge=0.0, le=1.0)
    """执行前总仓位占总资产比例。"""

    post_total_position_pct: float = Field(ge=0.0, le=1.0)
    """执行后预估比例。"""

    pre_cash: float = Field(ge=0.0)
    """执行前可用现金。"""

    post_cash: float = Field(ge=0.0)
    """执行后预估可用现金(BUY 减、SELL 加,含手续费/印花税)。"""


class RiskCheckSummary(BaseModel):
    """RiskEngine 7-check 中单条规则的结果摘要(by-value 内嵌)。

    完整 ValidationResult(含 prev_close / now / 计算细节)走 by-reference,
    通过 InstructionPlan.risk_validation_id 链回 MongoDB risk_validations。
    """
    model_config = ConfigDict(frozen=True)

    rule_name: str
    """与 backend/risk/engine.py 中 _check_* 函数 rule_name 一一对应:
    code_validity / price_reasonability / volume_validity /
    fund_sufficiency / position_limit / total_position_limit / trading_time"""

    passed: bool

    threshold: str | None = None
    """规则上限的可读化表达,e.g. "max_single_stock_pct: 20%"。"""

    actual: str | None = None
    """本指令的实际值,e.g. "post_pct: 8.5%"。"""

    message: str = ""
    """与 ValidationResult.message 同步(rejection 时给人读理由)。"""
```

#### 1.1.3 InstructionPlan 主模型

```python
class InstructionPlan(BaseModel):
    """系统发出的可执行操作计划(BUY/SELL)或仅复盘的 HOLD 决议。

    InstructionPlan 是 ModeRouter 路由的唯一对象 — feishu_off 时进
    SimulationExecutor → MockBroker;feishu_on 时进 FeishuMessenger →
    用户手工 → ExecutionReportParser → MockBroker。HOLD 永不路由,
    只入 decision_ledger 复盘。
    """
    model_config = ConfigDict(frozen=True)

    # === 身份与时间 ===
    instruction_id: str = Field(
        pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$"
    )
    """格式: QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq};
    长度恒为 32 字符;违规即红线。"""

    created_at: datetime
    """指令创建时刻(Asia/Shanghai,必须晚于 data_snapshot.snapshot_at)。"""

    valid_until: datetime
    """指令失效时刻;必须 ≤ created_at 当日 14:55 Asia/Shanghai;
    跨日有效性留 amendment(P0-3 第一阶段范围外)。"""

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    """YYYY-MM-DD 格式的交易日(与 created_at 同日)。"""

    # === 标的与方向 ===
    stock_code: str = Field(pattern=r"^\d{6}$")

    stock_name: str
    """显示用,LLM 提取后由代码 sanitize(剔除控制字符 / 限长 32)。"""

    side: InstructionSide

    # === 执行参数(BUY/SELL 必填,HOLD 必为 None)===
    volume: int | None = Field(default=None, ge=100)
    """股数;必须为 100 整数倍(A 股 lot size,与 RiskConfig.volume_lot_size
    保持一致);HOLD 时必须为 None。"""

    limit_price: float | None = Field(default=None, gt=0.0)
    """限价:BUY 时为"不高于"上限,SELL 时为"不低于"下限;
    HOLD 时必须为 None;市价单不在第一阶段范围(留 amendment)。"""

    # === 数据快照(by-value 摘要)===
    data_snapshot: DataSnapshot

    # === 证据(by-reference)===
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    """MongoDB evidence collection 中的证据 ID 列表(新闻 / 行情异动 /
    MiroFish 输出 等)。可为空 tuple,但不应为 None。"""

    # === 仓位摘要(by-value;HOLD 时 None)===
    position_summary: PositionSummary | None = None

    # === 风控(混合)===
    risk_summary: tuple[RiskCheckSummary, ...]
    """RiskEngine 7-check 全部规则结果摘要(by-value);
    长度必须恰为 7,缺一即红线;HOLD 类指令也必须执行 7-check
    (即使 HOLD 不路由到 broker,7-check 仍作为决策依据展示)。"""

    risk_validation_id: str
    """完整 ValidationResult 在 MongoDB risk_validations collection 的
    引用 ID(by-reference)。"""

    # === 多 Agent 辩论溯源(by-reference)===
    signal_id: str
    """关联的 TradingSignal._id(MongoDB trading_signals)。"""

    analysis_record_id: str
    """关联的 AnalysisRecord.run_id(MongoDB analysis_records)。"""

    debate_round_count: int = Field(ge=1)
    """多 Agent 辩论实际进行的轮数;必须 ≥ 1(P0-1 §1.6 红线 —
    绕过辩论 = 0 即红线违规)。"""

    # === 失效条件(简短文本)===
    invalidation_summary: str = Field(max_length=200)
    """人读化失效条件,e.g. "跌破 1650 / 资讯反转 / 盘口流动性骤降";
    完整结构化失效条件留后续决策(P1 范围)。"""

    # === 状态机 ===
    status: InstructionStatus = InstructionStatus.DRAFT

    rejection_reason: str | None = None
    """REJECTED / AMBIGUOUS 时填;其他状态必须为 None。"""
```

#### 1.1.4 派生函数(纯函数,可测)

```python
def derive_order_from_plan(plan: InstructionPlan) -> Order:
    """把 InstructionPlan 转成 backend/broker/models.py::Order,
    供 RiskEngine.validate_order 复用。

    HOLD 类调用此函数会抛 ValueError(HOLD 不应走 RiskEngine path)。

    实现期注意:Order 字段不变(P0-1 §2 红线 8 / 风控隔离不变),
    instruction_id 通过外层 dict 关联,不污染 broker.models。
    """
    if plan.side == InstructionSide.HOLD:
        raise ValueError("HOLD InstructionPlan must not be derived to Order")
    if plan.volume is None or plan.limit_price is None:
        raise ValueError(
            f"BUY/SELL plan {plan.instruction_id} missing volume/limit_price"
        )
    direction = (
        OrderDirection.BUY if plan.side == InstructionSide.BUY
        else OrderDirection.SELL
    )
    return Order(
        order_id=plan.instruction_id,  # 复用作为 order_id
        code=plan.stock_code,
        price=plan.limit_price,
        volume=plan.volume,
        direction=direction,
        order_type=OrderType.LIMIT,
        status=OrderStatus.PENDING,
        created_at=plan.created_at,
        updated_at=plan.created_at,
    )


def is_routable(plan: InstructionPlan) -> bool:
    """ModeRouter 是否应路由本指令到 SimulationExecutor / FeishuMessenger。

    HOLD 永不路由(只入 ledger);非 VALIDATED 状态永不路由。
    """
    return (
        plan.side != InstructionSide.HOLD
        and plan.status == InstructionStatus.VALIDATED
    )
```

#### 1.1.5 instruction_id 生成器

```python
def make_instruction_id(
    created_at: datetime,
    stock_code: str,
    side: InstructionSide,
    seq: int,
) -> str:
    """生成符合 P0-3 §1.2 格式的 instruction_id。

    seq 来源:同一秒内同一 (code, side) 多条指令的递增计数器,
    由 InstructionPlanBuilder 维护(实施期细节);1-999 范围,
    超过 999 抛 ValueError(同秒同方向同股 ≥1000 条 = LLM 失控)。
    """
    if not 1 <= seq <= 999:
        raise ValueError(f"seq {seq} out of range [1, 999]")
    if not re.fullmatch(r"\d{6}", stock_code):
        raise ValueError(f"stock_code {stock_code!r} must be 6 digits")
    return (
        f"QM-{created_at.strftime('%Y%m%d')}-{created_at.strftime('%H%M%S')}"
        f"-{stock_code}-{side.value}-{seq:03d}"
    )
```

### 1.2 instruction_id 命名格式(锁定)

格式:`QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq}`

- **正则**:`^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`
- **长度**:**33-34 字符**(side=BUY 时 33;side=SELL 或 HOLD 时 34)
  - 校核:`QM-`(3) + `20260512`(8) + `-`(1) + `093001`(6) + `-`(1) + `600519`(6) + `-`(1) + side(3 或 4) + `-`(1) + `001`(3) = **33 / 34**
  - 实施期 lint:`assert 33 <= len(instruction_id) <= 34`(配合正则全约束)
- **时区**:`{YYYYMMDD}` 与 `{HHMMSS}` 用 Asia/Shanghai 本地时间(避免与飞书消息中的"今日"混淆)
- **seq**:同一秒内同一 `(stock_code, side)` 的递增计数器,3 位 0 填充;1-999 范围

实例:
```
QM-20260512-093001-600519-BUY-001
QM-20260512-103015-000001-SELL-002
QM-20260512-141500-300750-HOLD-001
```

**飞书回报匹配**:用户在飞书复制粘贴 instruction_id,ExecutionReportParser 用上面的正则严格匹配;格式不符即 ambiguous,触发 P0-4 fail-closed 澄清路径。

### 1.3 side 取值范围 + 限价语义(锁定)

#### 1.3.1 side 取值

`InstructionSide ∈ {BUY, SELL, HOLD}`,任何其他取值红线违规。

| side | 路由到 ModeRouter | 路由到 SimulationExecutor / FeishuMessenger | 入 decision_ledger |
|------|---|---|---|
| BUY  | 是 | 是 | 是 |
| SELL | 是 | 是 | 是 |
| HOLD | **否** | 否(永远) | 是 |

**HOLD 处理规则**:
- HOLD 仍走完整多 Agent 辩论 + RiskEngine 7-check(用于复盘理解"为什么决议持有")
- HOLD InstructionPlan 入库 `instruction_plans` collection,但 `status` 直接 `DRAFT → VALIDATED`,**不进 ModeRouter**(`is_routable()` 返回 False)
- HOLD 不发飞书消息(避免飞书噪声;P0-2 §1.2 / §1.3 第一阶段消息形态保持纯净)
- 前端 InstructionCenter(P1-5)展示所有 HOLD 决议供复盘

#### 1.3.2 limit_price 语义

单字段 `limit_price: float`(BUY/SELL 时必填且 > 0;HOLD 时必为 None)。

| side | limit_price 语义 | 派生 OrderType |
|------|-----------------|----------------|
| BUY  | "不高于"上限,e.g. 1680.00 表示买入价 ≤ 1680.00 | LIMIT(永远) |
| SELL | "不低于"下限,e.g. 1700.00 表示卖出价 ≥ 1700.00 | LIMIT(永远) |
| HOLD | None | — |

**第一阶段排除**:
- 市价单(`OrderType.MARKET`)不在 P0-3 范围(LLM 给出市价单 = 失控,需要锚定价格作为风控基准)
- 价格区间(`limit_price_lower / limit_price_upper`)不在 P0-3 范围
- 区间偏移(`market_offset_pct`)不在 P0-3 范围
- 加仓(ADD)/ 减仓(REDUCE)百分比不在 P0-3 范围

以上若需要,走 amendment(`P0-3-amendment-{date}-{topic}.md`)。

### 1.4 有效期与超时语义(锁定)

#### 1.4.1 valid_until 约束

```python
def validate_valid_until(plan: InstructionPlan) -> None:
    """InstructionPlan.valid_until 必须满足:
    1. > plan.created_at(否则一创建就过期)
    2. ≤ plan.created_at 当日 14:55 Asia/Shanghai(分钟级粒度)
    3. created_at.date() == valid_until.date()(严格当日)
    """
    sh = ZoneInfo("Asia/Shanghai")
    created_local = plan.created_at.astimezone(sh)
    valid_local = plan.valid_until.astimezone(sh)
    if valid_local <= created_local:
        raise ValueError("valid_until must be strictly after created_at")
    if valid_local.date() != created_local.date():
        raise ValueError("valid_until must be same trading day as created_at")
    cutoff = created_local.replace(hour=14, minute=55, second=0, microsecond=0)
    if valid_local > cutoff:
        raise ValueError(
            f"valid_until {valid_local} exceeds 14:55 cutoff {cutoff}"
        )
```

#### 1.4.2 超时语义

| 模式 | 超时(now > valid_until)行为 |
|------|------|
| feishu_off (simulation_auto 单跑) | SimulationExecutor 检测 `now > plan.valid_until` 时:跳过撮合,把 `status` 从 `DISPATCHED` 改为 `EXPIRED`,记入 ledger |
| feishu_on (feishu_interactive)   | ExecutionReportParser 检测 `now > plan.valid_until + 飞书追问宽限期` 时:`DISPATCHED → EXPIRED`(具体追问超时时长由 P0-4 决定);追问期间不更新 MockBroker |

**EXPIRED 不更新 MockBroker**(P0-1 §1.5 路由规则继承);下一轮 simulation_auto 研判会重新评估是否重发(新 instruction_id,旧 EXPIRED 留 ledger)。

### 1.5 数据快照 / 证据 / 风控摘要 折中绑定(锁定)

#### 1.5.1 by-value(内嵌)字段集

以下字段必须**完整内嵌**在 InstructionPlan,确保飞书消息自包含 + 离线复盘可读:

- `data_snapshot.snapshot_at` / `quote_source` / `news_source` / `prev_close` / `is_trading_day` / `is_trading_hours`
- `position_summary.pre/post_position_pct` / `pre/post_total_position_pct` / `pre/post_cash`(HOLD 除外)
- `risk_summary` 7-check 结果摘要(每条规则的 `rule_name` / `passed` / `threshold` / `actual` / `message`)

#### 1.5.2 by-reference(引用)字段集

以下字段只存 ID,完整数据走 MongoDB collections 查询;前端 InstructionCenter 按需展开,飞书消息不展示完整内容:

- `evidence_ids[]` → MongoDB `evidence` collection(全文新闻 / MiroFish 输出 / 行情异动详情)
- `risk_validation_id` → MongoDB `risk_validations` collection(完整 `ValidationResult` 对象 + `prev_close` / `now` / 计算细节)
- `signal_id` → MongoDB `trading_signals` collection
- `analysis_record_id` → MongoDB `analysis_records` collection(含完整 9-Agent 辩论原文 + LLM 路由日志)

#### 1.5.3 设计原理

- **飞书消息长度可控**:by-value 字段总字节数控制在 ~600 字符以内(单条飞书消息阅读体验最佳);完整证据走前端 InstructionCenter 链接(实施期 InstructionCenter 提供 `/instruction-plans/{id}` 详情页)
- **离线复盘可读**:即使 MongoDB 暂时不可达,飞书消息历史仍能让用户判断"当时为什么发这条指令"
- **风控可追溯**:7-check 摘要够人看懂"哪条规则给的余量",完整 prev_close 计算细节去 risk_validations 找

### 1.6 第一阶段飞书纯文本指令模板(唯一允许的模板)

#### 1.6.1 模板字符串(占位符)

由 `backend/integrations/feishu/renderer.py::render_buy_sell_instruction()` 渲染。**禁止 LLM 自由拼接**(P0-2 §2 红线;防 prompt injection 间接绕过模板)。

```text
【QuantMind 操作指令 {instruction_id}】
标的: {stock_code} {stock_name}
动作: {side_zh} {volume} 股
价格: {price_clause}
有效期: 今日 {valid_until_hhmm} 前
数据时点: {snapshot_at_hhmmss} | 行情源: {quote_source} | 资讯源: {news_source}
仓位摘要: 执行后单股 {post_pos_pct}% (限 {single_limit_pct}%) / 总仓 {post_total_pct}% (限 {total_limit_pct}%)
风控: 7 项校验全部通过 (rv={risk_validation_id})
失效条件: {invalidation_summary}
辩论轮数: {debate_round_count}

执行后请回报(任选其一,严格语法):
已执行 {instruction_id} {side_zh} {stock_code} {volume}股 成交价 {fill_price} 手续费 {fee}
部分执行 {instruction_id} {side_zh} {stock_code} {filled_volume}股 成交价 {fill_price} 剩余未成交 {remain_volume}股
未执行 {instruction_id} 原因: {reason}
```

**占位符语义**:
- `{side_zh}`:`BUY → 买入`,`SELL → 卖出`(HOLD 不走此模板)
- `{price_clause}`:BUY 时 `限价不高于 {limit_price} 元`,SELL 时 `限价不低于 {limit_price} 元`
- `{valid_until_hhmm}`:`HH:MM` 格式
- `{snapshot_at_hhmmss}`:`HH:MM:SS` 格式
- `{post_pos_pct}` / `{post_total_pct}`:百分比保留 1 位小数(e.g. `8.5`)
- `{single_limit_pct}` / `{total_limit_pct}`:从 `RiskConfig.position_limits` 读取(单股 20% / 总仓在 P0-7 决定)
- 回报模板中 `{fill_price}` / `{fee}` 等是**示例占位符**,告诉用户回报时填入真实成交价/手续费;不是渲染时由系统填的

**HOLD 不发飞书消息**(§1.3.1),所以本模板只覆盖 BUY/SELL。

#### 1.6.2 实例

```text
【QuantMind 操作指令 QM-20260512-093001-600519-BUY-001】
标的: 600519 贵州茅台
动作: 买入 100 股
价格: 限价不高于 1680.00 元
有效期: 今日 14:55 前
数据时点: 09:30:01 | 行情源: adata | 资讯源: akshare.stock_news_em
仓位摘要: 执行后单股 8.5% (限 20%) / 总仓 42.3% (限 80%)
风控: 7 项校验全部通过 (rv=rv_a3f7b8c2d019)
失效条件: 跌破 1650 / 资讯反转 / 盘口流动性骤降
辩论轮数: 3

执行后请回报(任选其一,严格语法):
已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 成交价 1678.50 手续费 5.00
部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 60股 成交价 1678.50 剩余未成交 40股
未执行 QM-20260512-093001-600519-BUY-001 原因: 价格未到
```

#### 1.6.3 与 P0-4(飞书回报语法)的契合点

回报模板示例已严格匹配决策清单 §P0-4 的"严格文字模板"建议(`已执行 / 部分执行 / 未执行 / 更正` 范式)。P0-4 后续会进一步锁定:

- 部分执行的剩余股数语法(`剩余未成交 N股`)
- "未执行 原因:..." 中允许的自然语言范围
- 用户可飞书发"更正 QM-..."覆盖上一条回报的语法
- 盘后批量补录的允许性

P0-3 不强制 P0-4 实现细节,但已**预留模板槽位**,P0-4 可在不修改 InstructionPlan schema 的前提下扩展 ExecutionReportParser。

### 1.7 与多 Agent 辩论的衔接

#### 1.7.1 数据流

```
9-Agent LangGraph (P0-1 §1.6 多 Agent 辩论 = 唯一通路)
    └─> fund_manager_node 输出 TradingSignal (parse_ok flag)
            │
            ├─ parse_ok=False:
            │    └─> 强制降级为 InstructionPlan(side=HOLD, status=DRAFT)
            │        invalidation_summary="LLM 输出解析失败,自动持有"
            │        debate_round_count = 实际进行的轮数
            │
            └─ parse_ok=True:
                 └─> InstructionPlanBuilder (确定性代码)
                     │  - 仓位计算(由 PositionSizer 完成,LLM 不参与)
                     │  - data_snapshot 注入(行情/资讯当前快照)
                     │  - position_summary 注入(MockBroker 当前快照派生)
                     │  - instruction_id 生成
                     │  - valid_until 计算(默认当日 14:55,可由策略覆盖)
                     │
                     ▼
                 InstructionPlan(status=DRAFT)
                     │
                     ▼
                 RiskEngine.validate_order(derive_order_from_plan(plan), ...)
                     │
                     ├─ ValidationResult.passed=False:
                     │    └─> InstructionPlan.status = REJECTED
                     │        rejection_reason = ValidationResult.message
                     │
                     └─ passed=True:
                          └─> InstructionPlan.status = VALIDATED
                              risk_summary = 7 条 RiskCheckSummary
                              risk_validation_id = 持久化后的 ID
                              │
                              ▼
                          ModeRouter (P0-1 §1.5)
                              │
                              ├─ feishu_off → SimulationExecutor → MockBroker
                              └─ feishu_on  → FeishuMessenger.send_text
```

#### 1.7.2 LLM 角色边界(继承 P0-1 §1.6)

LLM **可以**:
- 抽取/归纳新闻、情绪、基本面、技术面信息
- 多视角辩论(bull / bear / risk_officer)
- 在 fund_manager 节点输出 `action / target_price / confidence / risk_score / reasoning` JSON
- 写 `invalidation_summary`(简短人读化失效条件)

LLM **不可以**:
- 直接计算 `volume`(必须由 PositionSizer 确定性代码)
- 直接计算 `limit_price`(目前依赖 LLM 的 `target_price`,但 P1-2 / P0-7 后续可能引入价格容差代码层)
- 直接写 `position_summary` / `risk_summary`(全部确定性代码派生)
- 决定 `valid_until`(代码默认当日 14:55,允许策略覆盖但 LLM 不参与)
- 决定 `instruction_id`(代码生成)
- 决定 `status` 流转

`debate_round_count` 必须 ≥ 1(P0-1 §1.6 红线 / 绕过辩论 = 0 即红线违规);`AnalysisRecord` 已记录辩论轮数,InstructionPlanBuilder 直接读取。

### 1.8 LLM 输出 → InstructionPlan 校验链路(防降级失控)

```python
def build_instruction_plan(
    signal: TradingSignal,
    parse_ok: bool,
    debate_round_count: int,
    services: AnalysisServices,
    mock_broker_snapshot: tuple[AccountInfo, tuple[Position, ...]],
    risk_engine: RiskEngine,
    risk_config: RiskConfig,
    quote_snapshot: QuoteSnapshot,
    news_snapshot: NewsSnapshot,
    evidence_ids: tuple[str, ...],
    seq_provider: Callable[[str, InstructionSide], int],
) -> InstructionPlan:
    """从 TradingSignal + 上下文构造 InstructionPlan。

    强制降级规则(继承 backend/agents/fund_manager.py:_parse_signal):
        parse_ok=False → side=HOLD, debate_round_count 仍记录
                      → 不调用 PositionSizer、不调用 RiskEngine
                      → status=VALIDATED(直接,因 HOLD 不进 ModeRouter)

    parse_ok=True 路径:
        action=持有 → side=HOLD(同上路径)
        action=买入 → side=BUY,走 PositionSizer + RiskEngine
        action=卖出 → side=SELL,走 PositionSizer + RiskEngine

    PositionSizer / QuoteSnapshot / NewsSnapshot 等具体实现属
    实施期范围(P0-3 不锁);本函数签名锁定,确保依赖注入清晰。
    """
    ...  # 实现期细化
```

**关键约束**:
- `parse_ok=False` **强制 HOLD**,且 `invalidation_summary="LLM 输出解析失败,自动持有"`
- BUY/SELL 路径必须走 RiskEngine;`ValidationResult.passed=False` 时 `status=REJECTED`、`rejection_reason` 填 `ValidationResult.message`
- 所有 InstructionPlan(含 HOLD)入库 `instruction_plans` collection;REJECTED 也入库(供复盘"哪条指令被风控拦截")

## 2. 红线 / 边界(立即生效)

P0-3 落地后这些立即成为代码硬约束:

1. **instruction_id 必须严格匹配正则** `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`;Pydantic schema 强校验,违规即 ValidationError
2. **side ∈ {BUY, SELL, HOLD}**,任何其他值红线违规;**HOLD 永不路由到 SimulationExecutor / FeishuMessenger**(`is_routable()` 强制返回 False)
3. **valid_until 约束三连**:`> created_at` + `当日内` + `≤ 当日 14:55 Asia/Shanghai`,违规 ValidationError;跨日支持必须先走 `P0-3-amendment-{date}-cross-day-validity.md`
4. **BUY/SELL 必须有 `volume`(100 整数倍,与 RiskConfig.volume_lot_size 对齐)+ `limit_price`(>0)**;HOLD 必须 `volume=None, limit_price=None`,违规 ValidationError
5. **InstructionPlan.status 流转必须经过状态机**(§1.1.1),跨态(如 DRAFT→FILLED、REJECTED→DISPATCHED)红线违规;实施期由 `instruction_plan_state_machine.py` 集中守门
6. **data_snapshot 字段全必填**;`snapshot_at` 必须 < `created_at`(数据先于决策),违规 ValidationError
7. **risk_summary 必须包含恰好 7 条**,与 `backend/risk/engine.py::_check_*` 7-check 一一对应,缺一即红线;HOLD 也要 7-check
8. **debate_round_count ≥ 1**(P0-1 §1.6 红线 / 绕过辩论 = 0 即红线违规);Pydantic `Field(ge=1)` 强校
9. **parse_ok=False 的 TradingSignal 不得产出可执行 InstructionPlan**;必须降级为 HOLD(§1.8)
10. **飞书指令文本必须由 `renderer.py` 函数生成**,不允许 LLM 自由拼接(P0-2 §2 红线 / 防 prompt injection)
11. **instruction_id 中的 `{seq}` 不允许 ≥ 1000**;同秒同股同方向超过 999 条 = LLM 失控,实施期抛 ValueError
12. **InstructionPlan 是 frozen Pydantic 模型**(immutability 红线);任何就地 mutation 红线违规;状态流转必须 `model_copy(update={"status": ...})`

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-3 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 新增项(代码级)

- `backend/services/instruction_plan.py`(命名 P1):
  - `InstructionSide` / `InstructionStatus` 枚举
  - `DataSnapshot` / `PositionSummary` / `RiskCheckSummary` / `InstructionPlan` Pydantic 模型
  - `make_instruction_id()` / `derive_order_from_plan()` / `is_routable()` 纯函数
  - `validate_valid_until()` 校验函数
  - `build_instruction_plan()` 构造器(含 parse_ok 降级路径)
  - `instruction_plan_state_machine.py`(可选拆分):状态流转守门
- `backend/services/position_sizer.py`(命名 P1):确定性仓位计算
- `backend/integrations/feishu/renderer.py`:`render_buy_sell_instruction(plan)` 渲染纯文本(P0-2 §3.1 已规划新增,P0-3 锁定模板)
- 新 MongoDB collection `instruction_plans`:
  - 索引 `(stock_code, trade_date)` 复合 + `(instruction_id)` 唯一 + `(status, valid_until)` 复合(供 SimulationExecutor / ExecutionReportParser 扫超时)
- 新 MongoDB collection `risk_validations`:存完整 `ValidationResult` + `prev_close` / `now`,索引 `(_id)` 唯一
- 新 MongoDB collection `evidence`:存证据(实施期决定 schema)
- 新 MongoDB collection `decision_ledger`(P0-1 §3.2 已规划):InstructionPlan + 模拟成交 / 飞书消息 / 用户回报 全绑定

### 3.2 修改项

- `backend/agents/fund_manager.py`:`fund_manager_node` 返回值 dict 增加 `parse_ok` 已存在,无需改动;但下游 `AnalysisScheduler._run_and_persist_locked` 需新增"signal → InstructionPlanBuilder → instruction_plans 持久化"步骤
- `backend/data/database.py`:新增 `save_instruction_plan()` / `query_instruction_plans()` / `update_instruction_plan_status()` 方法
- `backend/risk/engine.py`:**不修改** `validate_order` 签名(P0-1 §2 红线 8 风控隔离不变);`derive_order_from_plan()` 在 `services` 层完成 InstructionPlan→Order 转换
- `backend/agents/models.py::TradingSignal`:**不修改**(继续保留为辩论中间产物);新加文档注释说明它不再是执行信号

### 3.3 配置项

- `config/risk.yaml`:`PositionLimitsConfig.max_total_positions` 与 `position_summary.post_total_position_pct` 上限的对齐(P0-7 决策范围)
- `config/instruction_plan.yaml`(新):
  - `valid_until_default_hhmm: "14:55"`(策略可覆盖但 LLM 不可改)
  - `seq_max: 999`
  - `text_template_max_length: 800`(防异常长 invalidation_summary)

### 3.4 文档同步(本决策落地立即执行,见 §5.1)

- `CLAUDE.md` §1.3 进度行(P0-3 ✅,下一站 P0-4)
- `CLAUDE.md` §2.1 P0-3 行(状态 + 决策文档列)
- `CLAUDE.md` §3.1 红线节(同步本文 §2 红线 1-12 中与现有红线不重叠的部分)
- `MEMORY.md` 索引新增 `project_run_mode_p0_3.md`
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_3.md`

### 3.5 测试覆盖(实施期任务)

- `tests/test_instruction_plan_schema.py`:Pydantic 字段约束、instruction_id 正则、valid_until 三连校验、状态机迁移合法性、HOLD 路径
- `tests/test_instruction_plan_builder.py`:parse_ok=False 强制 HOLD、parse_ok=True BUY/SELL 通路、RiskEngine 拦截路径
- `tests/test_feishu_renderer.py`:模板渲染 BUY 实例 / SELL 实例 / 边界(invalidation_summary 200 字符截断)
- 覆盖率:non-risk 模块 >70%(`backend/services/instruction_plan.py` 应达 >90% 因为是关键路径)

## 4. 决策依据

### 4.1 audit 引用

- audit §3.2 确认当前链路终止在 `TradingSignal`,新目标必须续上 `InstructionPlan` → RiskEngine → 执行
- audit §5.2 列出 `TradingSignal` 缺的 12 个字段,本决策已全部覆盖(股数 / 限价 / 有效期 / 触发条件 / 失效条件 / 数据快照 / 证据 / 风控校验 / 指令编号 / 回报模板 / 风险摘要 / 仓位)
- audit §5.3 推荐 `TradingSignal → StrategyPolicy/PositionSizer → InstructionPlanDraft → Pydantic 严格校验 → RiskEngine → Final InstructionPlan` — 本决策 §1.7 / §1.8 完全采纳
- audit §8.3 关键缺口 "RiskEngine 未接入 TradingSignal → InstructionPlan → 执行" — 本决策 §1.7 通过 `derive_order_from_plan()` 解决,且**不修改 RiskEngine 入参签名**
- audit §9.3 推荐 `decision_ledger` 字段集 — 本决策的 by-reference ID 字段(`signal_id` / `analysis_record_id` / `risk_validation_id` / `evidence_ids[]`)与 ledger 兼容
- audit §15 "当前不建议做的事" 列出 "让 LLM 直接决定最终股数和风控红线" — 本决策 §1.7.2 LLM 角色边界完全继承

### 4.2 决策清单引用(§P0-3)

- 字段集建议(decision-points L156-171)— 本决策已严格采纳,且补充了 `position_summary` / `risk_summary` 7-check / `signal_id` / `analysis_record_id` / `debate_round_count` / 状态机
- 飞书操作指令模板建议(decision-points L177-189)— 本决策 §1.6 模板与建议风格一致,但补充了 `数据时点` / `辩论轮数` / `rv ID`
- 决策清单 §P0-3 强调 "TradingSignal 只有 action/target_price/confidence/risk_score/reasoning,不能直接指导买多少卖多少" — 本决策直接解决

### 4.3 代码事实抽检(2026-05-09 复核)

- `backend/agents/models.py:39-51` `TradingSignal` 当前字段 — 本决策保留它作为辩论中间产物,不删除
- `backend/agents/fund_manager.py:50-60` `parse_ok=False` 降级为 `持有 / 0.5` — 本决策 §1.8 直接继承"parse_ok=False 强制 HOLD"
- `backend/risk/engine.py:41-82` `validate_order(order, account, positions, prev_close, now)` 签名 — 本决策**不修改**,通过 `derive_order_from_plan()` 解耦
- `backend/broker/models.py:56-72` `Order` 字段 — 本决策**不修改**,instruction_id 通过 `Order.order_id` 字段复用
- `backend/broker/interface.py:23-32` `IBroker.place_order(code, price, volume, direction, order_type)` — 本决策**不修改**入参签名;实施期通过 SimulationExecutor 中间层在调用 broker 前记录 instruction_id 关联
- `backend/data/analysis_scheduler.py:520-557` `_run_and_persist_locked` 当前流程在保存 signal 后直接 publish — 实施期需在此插入 InstructionPlanBuilder 调用
- `backend/agents/records.py:114` `AnalysisRecord` 已含完整辩论历史 — 本决策的 `analysis_record_id` 引用即此

### 4.4 用户选择记录(2026-05-09 决策对话)

| 问题 | 选择 |
|------|------|
| instruction_id 命名格式? | 人读结构化 `QM-{YYYYMMDD}-{HHMMSS}-{code}-{side}-{seq}`(人读高 / 飞书回报匹配方便 / 时间天然排序) |
| side 取值 + 限价表达? | 极简 `BUY/SELL/HOLD` + 单 `limit_price`(BUY=不高于,SELL=不低于);第一阶段不引入加仓/减仓/区间/区间偏移 |
| 有效期粒度 + 超时默认动作? | 当日盘中 + 分钟级 valid_until;feishu_off 跳过撮合,feishu_on 追问一次后 expired |
| 数据快照/证据/风控耦合度? | 折中:核心字段 by-value(snapshot_at/quote_source/news_source/position_summary/7-check 摘要)+ 详细证据 by-reference(evidence_ids/risk_validation_id/signal_id/analysis_record_id) |

### 4.5 与 P0-1 / P0-2 的契合点对照

| 上游决策条款 | P0-3 承载方式 |
|-------------|--------------|
| P0-1 §1.5 InstructionPlan 路由唯一通路 | `is_routable()` 函数 + `status` 状态机守门;HOLD `is_routable()=False` 永不路由 |
| P0-1 §1.6 多 Agent 辩论是唯一生成路径 | `debate_round_count ≥ 1` Pydantic 强约束;`parse_ok=False` 降级 HOLD |
| P0-1 §2 红线 5 LLM 不允许绕开辩论直接产出股数/价格/有效期 | §1.7.2 LLM 角色边界 + §1.8 build_instruction_plan 强制 PositionSizer 路径 |
| P0-1 §2 红线 8 风控隔离不变 | RiskEngine.validate_order 签名不修改;`derive_order_from_plan()` 在 services 层 |
| P0-2 §1.3 第一阶段纯文本指令 | §1.6 唯一允许的纯文本模板;HOLD 不发飞书 |
| P0-2 §1.6 P0-3 归属边界 | 本决策严格只锁字段集与模板,不锁飞书发送时序 / 长连接 worker 行为 |
| P0-2 §2 红线 7 第一阶段不实现交互卡片 | §1.3.2 单字段 limit_price + §1.6 纯文本模板,无任何卡片字段 |

### 4.6 替代方案与拒绝理由

| 候选方案 | 拒绝原因 |
|---------|---------|
| 直接扩展 `TradingSignal` 而不新增 InstructionPlan | TradingSignal 已被 9-Agent fund_manager_node 大量持久化(`trading_signals` collection),改它会破坏向前兼容 + 模糊"辩论产物 vs 可执行计划"语义 |
| ULID 作为 instruction_id | 用户明确选了人读结构化;ULID 飞书回报无法用肉眼校验日期/方向,与"严格回报模板 + 用户复制粘贴 ID"的工作流冲突 |
| side 引入 ADD/REDUCE 百分比 | 实施期解析与撮合复杂度大幅上升;P0-1 §1.6 强调 LLM 不参与仓位计算,百分比就成了纯代码层语义,直接 BUY/SELL 即可表达 |
| 区间限价 `[lower, upper]` | LLM 容易给出过宽区间反而稀释风控意义;单 `limit_price` 配合 `RiskConfig.price_deviation_limit` 已足够 |
| 跨日 valid_until | 第一阶段只关心当日闭环;跨日引入"每日盘前重评估"额外状态机分支,违反 P0 阶段"先打通最简通路"原则 |
| 完全 by-value 内嵌全部证据 | 飞书消息会膨胀到 5000+ 字符;违反 P0-2 §1.3 "纯文本可读" 目标 |
| 完全 by-reference 仅存 ID | 用户在飞书上无法判断"这条建议的依据是否仍成立";离线复盘必须始终在线查 MongoDB,运维成本高 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-3 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步(本 PR 内随决策一起提交)

- [x] 写入本决策文档(`docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`)
- [ ] 更新 `CLAUDE.md` §1.3:P0-3 状态从 ⏳ 改为 ✅,链接本文件,下一站改 P0-4
- [ ] 更新 `CLAUDE.md` §2.1:P0-3 行 决策文档列填本文件路径,备注列收紧为决策结果摘要
- [ ] 更新 `CLAUDE.md` §3.1 红线节:同步本文 §2 红线中与现有红线不重叠的部分(尤其 instruction_id 正则 + 状态机 + 7-check 必填)
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_3.md` 条目
- [ ] 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_3.md`
- [ ] commit 本决策文档 + CLAUDE.md/MEMORY.md 同步更新(单 PR);**等用户授权再 commit**;不自动 push

### 5.2 依赖本决策的下游 P0/P1 决策

- **P0-4 飞书回报语法与成交状态**:决定 §1.6 回报模板的精确语法 + 部分执行/更正/盘后批量补录的允许性 + ExecutionReportParser 的 ambiguous fail-closed 状态机 + 追问超时具体时长
- **P0-5 账户状态来源与对账机制**:决定日终对账模板 + 用户回报与 InstructionPlan 状态偏差阈值 + 必要时的人工导出对账周期
- **P0-7 风险红线与指导强度**:决定 `position_summary` 引用的 `single_limit_pct` / `total_limit_pct` 具体值 + 每日最大新指令数 + 单次金额上限 + 亏损暂停线
- **P0-8 数据与资讯可信度**:决定 `data_snapshot.quote_source` / `news_source` 的允许枚举 + 延迟阈值 + 源间偏差停发规则
- **P0-9 第一阶段标的范围**:决定 `stock_code` 允许的 watchlist 范围(只有特定股票 / ETF 在范围内才生成 InstructionPlan)
- **P0-10 LLM 角色边界**:决定 `target_price` 是否仍由 LLM 给出(本决策默认是;P0-10 可能引入价格容差代码层进一步约束)
- **P1-1 新核心数据模型**:决定 `instruction_plans` / `risk_validations` / `evidence` collection 的索引细节与查询 API
- **P1-2 MockBroker 持久化**:决定 SimulationExecutor 接 MockBroker 时如何把 instruction_id 关联到 `Trade.order_id`
- **P1-4 回报解析策略**:决定 ExecutionReportParser 是否引入 LLM 辅助解析("已执行 ... 按市价" 等自然语言变体)
- **P1-5 前端优先工作流**:决定 InstructionCenter 详情页布局 + by-reference 字段(evidence_ids 等)的展开 UI

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.3 编写 implementation 任务列表;`backend/services/instruction_plan.py` 作为新闭环的入口,与 P0-1 中 `mode_router.py` / P0-2 中 `feishu/renderer.py` 同 PR 或紧邻 PR 落地
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级:新核心 schema + 状态机 + 飞书模板)
- [ ] 测试覆盖(§3.5 已列):
  - `tests/test_instruction_plan_schema.py` 全字段约束 + 正则 + 状态机
  - `tests/test_instruction_plan_builder.py` parse_ok 降级 + RiskEngine 拦截 + HOLD 路径
  - `tests/test_feishu_renderer.py` 模板渲染 + 边界
  - 覆盖率:`backend/services/instruction_plan.py` >90%(关键路径)
- [ ] 静态检查:lint rule 阻止
  - 任何文件直接构造 `InstructionPlan(...)` 而不经过 `build_instruction_plan()`(防绕过 parse_ok 降级)
  - 任何文件直接 `model_copy(update={"status": ...})` 而不经过状态机(防跨态)
  - 飞书 `client.send_text(...)` 直接接收非 renderer 输出(防 LLM 自由拼接绕过模板)
- [ ] 集成测试:从 fund_manager 输出 → InstructionPlanBuilder → RiskEngine → ModeRouter 全链路 e2e

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-3-amendment-{日期}-{原因}.md`。_
