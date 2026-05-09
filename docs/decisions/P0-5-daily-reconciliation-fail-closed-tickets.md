# P0-5 — UserReportedPortfolio 定位、系统主动发起日终对账与 fail-closed reconciliation_ticket

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-5 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §3.2 / §5.2 / §7 / §9 / §15 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-5 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.1 MockBroker 单一账户镜像 / §1.3.1 #3 切换初始化对账模板 / §1.3.2 退出路径 A/B / §1.4 切换期间冻结买卖类 InstructionPlan)+ `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(尤其 §1.1 长连接 worker 仅 ack+入队 / §2 红线 2 备用 webhook 不发对账 / §3.1 子树规划)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.5.1 position_summary by-value 字段集 / §1.7.1 数据流)+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(尤其 §1.6 16:00 cutoff / §1.6.3 与 P0-5 衔接 / §1.2.2 部分执行手续费默认 0 留待 P0-5 补对 / §1.7 ExecutionReportApplier)|
| 替代       | — |

## 决策摘要

QuantMind 飞书模式下账户镜像走 **MockBroker 单一镜像 + 系统主动发起日终对账 + 分级偏差阈值 + fail-closed reconciliation_ticket** 架构:

1. **不引入独立的 `UserReportedPortfolio` collection**。MockBroker 在 `feishu_on` 时本身就是"用户回报驱动的真实账户镜像"(P0-1 §1.1 已锁)。新增 `daily_reconciliations` collection 仅存放"用户在每个交易日 16:00 之后回报的镜像快照"作为对照源,与 MockBroker 解耦。

2. **日终对账触发**:每个交易日 16:00 Asia/Shanghai(对齐 P0-4 §1.6 cutoff)系统**主动**通过飞书主通道发出 `DailyReconciliationRequest`,载体是 MockBroker 的当前镜像快照(可用现金 + 全部持仓 + ticket_id)。用户严格正则回复"对账无误 RECON-..."、"对账差异 RECON-... 现金 ... 持仓 ..." 或 "对账更正 RECON-... 现金 ... 持仓 ..."。

3. **偏差阈值分级**:`持仓股数 0% + 现金 ≤1 元 + 成本价 ≤0.01 元`。三个字段独立判定,任一字段超阈值即判定 MISMATCHED 并触发 reconciliation_ticket。

4. **超阈值裁定** = 创建 fail-closed `reconciliation_ticket`,系统飞书发三选一裁定卡(纯文本,严格正则):
   - `对账采纳:用户回报 RECON-...` → 系统覆盖 MockBroker 镜像为用户回报值
   - `对账采纳:系统镜像 RECON-...` → 系统坚持当前 MockBroker 镜像(用户认可系统是对的)
   - `对账更正:RECON-... 现金 ... 持仓 ...` → 用户提出第三套数(常用于公司行动 / 漏报 / 多报)
   - **裁定前冻结次日盘中所有买卖类 InstructionPlan**(只放行 simulation_auto 分析与系统告警,继承 P0-1 §1.4 切换期间冻结精神)

5. **公司行动第一阶段不支持**(分红 / 配股 / 送转 / 停牌等):系统不主动调整 MockBroker;若用户持仓涉及,在每日对账时由偏差超阈值触发 ticket,用户用"对账更正"语法手工调整 MockBroker。**不引入复权数据源依赖 / 不改 MockBroker 撮合逻辑**(留 P1-2)。

6. **专用语法严格隔离**:P0-5 引入的 4 种回报形态(`日终对账请求 / 对账无误 / 对账差异 / 对账采纳 / 对账更正`)与 P0-4 的 5 种成交回报形态(`已执行 / 部分执行 / 未执行 / 更正 / 盘后补录`)**走完全独立的解析器**(`reconciliation_parser.py` vs `execution_report_parser.py`)。dispatcher 按前缀路由,严格不交叉。

7. **手续费补对**(P0-4 §1.2.2 伏笔回应):部分执行模板默认 fee=0 与系统估算导致的现金累积误差由"现金 ≤1 元"阈值天然吸收;超出阈值即 ticket → 用户用"对账更正"裁定。

8. **每周人工导出对账**:第一阶段不强制,默认依赖每日闭环;前端 ReconciliationCenter 提供导出本周 ticket 与 daily_reconciliations 的 CSV / JSON,方便用户与券商成交单 PDF 离线手工核对。系统**绝不**自动解析券商 PDF(LLM 不进对账路径)。

## 1. 决策具体内容

### 1.1 UserReportedPortfolio 的定位与 daily_reconciliations collection

#### 1.1.1 不引入独立 UserReportedPortfolio collection

P0-1 §1.1 表格已明确:

> | MockBroker 角色 | 虚拟资金的模型能力考场 | **用户真实资金的状态镜像(由飞书回报驱动)** |

也即 `feishu_on` 时 MockBroker 就是 UserReportedPortfolio 的物理载体。P0-5 不再引入 `user_reported_portfolios` 之类的并行 collection — 否则会回到 P0-1 §2 红线 3 明确禁止的"两条平行账本"违规。

#### 1.1.2 daily_reconciliations collection(对照源)

新增 MongoDB `daily_reconciliations`,仅记录用户每日 16:00 后回报的"该日终镜像快照"作为对照源:

```python
# backend/services/daily_reconciliation.py(实施期)

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ReportedPosition(BaseModel):
    """用户回报的单只持仓(by-value 内嵌)。"""
    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^\d{6}$")
    volume: int = Field(ge=0)
    cost_price: float = Field(ge=0.0)


class DailyReconciliation(BaseModel):
    """用户在 trade_date 16:00 之后回报的日终账户镜像快照。

    这是对照源,不是真相源 — 真相源仍是 MockBroker。
    超阈值时由 reconciliation_ticket 决定如何裁定。
    """
    model_config = ConfigDict(frozen=True)

    ticket_id: str = Field(pattern=r"^RECON-\d{8}-\d{3}$")
    """格式: RECON-{YYYYMMDD}-{seq};与系统主动发起的请求一一对应。"""

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    received_at: datetime
    """用户回报到达时刻;必须在 trade_date 16:00 ~ 次日盘前 09:25 区间。"""

    reported_cash: float = Field(ge=0.0)
    reported_positions: tuple[ReportedPosition, ...]
    """空 tuple 表示用户明确回报无持仓,与 None 语义不同。"""

    raw_text: str
    """原始回报全文留痕(供后续复盘 / 数据质量分析)。"""

    parse_ok: bool = True
    """正则解析是否成功;False 时 reported_* 字段不可信(走 ticket 路径)。"""
```

#### 1.1.3 索引

- `(ticket_id)` 唯一(防同一 ticket 重复回报覆盖)
- `(trade_date)` 复合,供前端 ReconciliationCenter 按日查
- `(received_at)` 单字段,供后续复盘 / TTL(决策清单 §P2-4 告警归档考虑)

### 1.2 日终对账消息流

#### 1.2.1 时序

```
T0  = 16:00:00 Asia/Shanghai  (P0-4 §1.6 cutoff)
T1  = 16:00:30                (cutoff 后 30 秒,等待最后一批 16:00 之前的 ExecutionReport
                               进入 EXPIRED / FILLED / REJECTED 终态;30 秒由 chase_poller 周期性扫描收尾)
T2  = 用户回报到达解析器
T3  = 偏差检测完成 → MATCHED / MISMATCHED
T4  = (MISMATCHED 路径) ticket 创建 + 飞书发裁定卡
T5  = 用户裁定回报到达
T6  = 系统应用裁定 → MockBroker / ticket → RESOLVED_*
T7  = 次日 09:25 (盘前)        若 T2 / T5 任一未到达,ticket 自动 EXPIRED + 持续冻结
```

#### 1.2.2 系统主动发起的请求模板(预写死)

由 `backend/integrations/feishu/renderer.py::render_daily_reconciliation_request()` 渲染。**禁止 LLM 自由拼接**(继承 P0-2 §2 红线 6 / P0-4 §2 红线 2)。

```python
DAILY_RECONCILIATION_REQUEST_TEMPLATE = """\
【QuantMind 日终对账 {ticket_id}】
交易日: {trade_date}
系统当前账户镜像如下,请到券商 APP 核对后回复:

可用现金: {cash:.2f} 元
持仓:
{positions_block}

请回复(任选其一,严格语法):
对账无误 {ticket_id}
对账差异 {ticket_id} 现金 <真实现金> 持仓 <代码1> <数量>股 成本 <价格>; <代码2> <数量>股 成本 <价格>
对账更正 {ticket_id} 现金 <真实现金> 持仓 <代码1> <数量>股 成本 <价格>; ...

(如无持仓,持仓部分填:无)
本日对账截止次日 09:25;未回报将冻结次日所有买卖类指令。"""
```

`positions_block` 渲染规则:

- 无持仓时:`无`
- 有持仓时:每行 `  {code} {volume}股 成本 {cost_price:.2f} (市值 {market_value:.2f})`(双空格缩进 + 市值估算供用户对照)

实例:

```
【QuantMind 日终对账 RECON-20260512-001】
交易日: 2026-05-12
系统当前账户镜像如下,请到券商 APP 核对后回复:

可用现金: 832145.30 元
持仓:
  600519 100股 成本 1678.50 (市值 167850.00)
  000001 2000股 成本 10.20 (市值 20400.00)

请回复(任选其一,严格语法):
对账无误 RECON-20260512-001
对账差异 RECON-20260512-001 现金 <真实现金> 持仓 <代码1> <数量>股 成本 <价格>; <代码2> <数量>股 成本 <价格>
对账更正 RECON-20260512-001 现金 <真实现金> 持仓 <代码1> <数量>股 成本 <价格>; ...

(如无持仓,持仓部分填:无)
本日对账截止次日 09:25;未回报将冻结次日所有买卖类指令。
```

#### 1.2.3 ticket_id 生成

格式:`RECON-{YYYYMMDD}-{seq}`,正则 `^RECON-\d{8}-\d{3}$`,长度恒为 16 字符。

- `{YYYYMMDD}` 用 trade_date(Asia/Shanghai)
- `{seq}` 同一交易日的对账序号,3 位 0 填充;1-999 范围;**正常情况每个交易日只有 1 个**(seq=001),除非系统检测到 16:00 之后又有新 ExecutionReport 应用导致镜像变化(罕见 — P0-4 §1.6 已锁 16:00 后状态冻结),此时 seq=002 起;同一交易日 seq ≥ 1000 抛 ValueError(异常情况 — 系统失控)
- 与 InstructionPlan 的 instruction_id(P0-3 §1.2 `QM-...`)前缀完全不同,飞书消息一眼可分辨

### 1.3 用户回报严格正则与字段交叉校验

#### 1.3.1 五种用户回报形态(锁定)

所有正则在 Python `re.fullmatch` 下匹配,前后 `strip()` 去白空,内部多余连续空白归一为单空格(继承 P0-4 §1.2 仅有的容错)。

##### 1.3.1.1 对账无误

```python
R_RECON_OK = re.compile(
    r"^对账无误 (?P<ticket_id>RECON-\d{8}-\d{3})$"
)
```

实例:`对账无误 RECON-20260512-001`

##### 1.3.1.2 对账差异

```python
R_RECON_MISMATCH = re.compile(
    r"^对账差异 (?P<ticket_id>RECON-\d{8}-\d{3}) "
    r"现金 (?P<cash>\d+(?:\.\d+)?) "
    r"持仓 (?P<positions>.+)$",
    re.DOTALL,
)
```

实例:

```
对账差异 RECON-20260512-001 现金 832146.70 持仓 600519 100股 成本 1678.50; 000001 2000股 成本 10.20
对账差异 RECON-20260512-001 现金 850000.00 持仓 无
```

##### 1.3.1.3 对账更正(等价于"差异"但用户已知是公司行动 / 漏报 / 多报,直接给出第三套数)

```python
R_RECON_AMEND = re.compile(
    r"^对账更正 (?P<ticket_id>RECON-\d{8}-\d{3}) "
    r"现金 (?P<cash>\d+(?:\.\d+)?) "
    r"持仓 (?P<positions>.+)$",
    re.DOTALL,
)
```

实例:

```
对账更正 RECON-20260512-001 现金 832145.30 持仓 600519 100股 成本 1678.50; 000001 2000股 成本 10.20; 600036 500股 成本 38.20
```

> **为什么"差异"和"更正"语法相同但前缀不同**:语义不同 — `对账差异` 是用户对系统当前镜像的"我看到的真实账户是这样"的报告(系统会判定 MISMATCHED 并发裁定卡);`对账更正` 是用户主动绕过 MISMATCHED 检测,直接告诉系统"用我这套覆盖,不要再问"(常见于用户已知昨天送股了,不需要系统再多问一轮)。代码层路径一致(都解析为 ReportedPosition),但状态机分支不同:`对账差异` → 创建 ticket → 等裁定 → 应用;`对账更正` → 自动等价于 ticket + `对账采纳:对账更正` 一键裁定。

##### 1.3.1.4 对账采纳:用户回报(裁定卡回应)

```python
R_RECON_RESOLVE_USER = re.compile(
    r"^对账采纳:用户回报 (?P<ticket_id>RECON-\d{8}-\d{3})$"
)
```

实例:`对账采纳:用户回报 RECON-20260512-001`

##### 1.3.1.5 对账采纳:系统镜像(裁定卡回应)

```python
R_RECON_RESOLVE_SYSTEM = re.compile(
    r"^对账采纳:系统镜像 (?P<ticket_id>RECON-\d{8}-\d{3})$"
)
```

实例:`对账采纳:系统镜像 RECON-20260512-001`

> **为什么用全角中文冒号 `:` 而非半角 `:`**:对账采纳类回报与"未执行 原因:..."(P0-4 §1.2.3)分布在不同正则路径,无歧义;但全角冒号在中文语境更自然且与"对账无误 / 对账差异 / 对账更正"前缀视觉一致。代码层不接受半角冒号(避免容错膨胀;若用户用半角则 AMBIGUOUS 走澄清路径)。

#### 1.3.2 持仓子串解析

`positions` 捕获组的内部语法:

```python
R_POSITION_ITEM = re.compile(
    r"^(?P<code>\d{6}) (?P<volume>\d+)股 成本 (?P<cost_price>\d+(?:\.\d+)?)$"
)
R_POSITIONS_NONE = re.compile(r"^无$")
```

- 多个持仓用 `; `(分号 + 单空格)分隔
- 单只持仓格式严格匹配 `R_POSITION_ITEM`
- 无持仓固定写"无"
- 任一项不匹配 → 整条 AMBIGUOUS(走澄清路径)

#### 1.3.3 字段交叉校验(任一失败即 AMBIGUOUS)

| 校验项 | 规则 |
|--------|------|
| `ticket_id` 存在性 | 必须对应 `reconciliation_tickets` collection 中 status=OPEN 的 ticket;否则 AMBIGUOUS(澄清提示"该 ticket_id 不存在或已结案") |
| `cash` 数值 | `≥ 0` 且 `≤ 1e10`(防异常大数,1e10 = 100 亿,足以覆盖所有真实 A 股零售账户) |
| `positions[*].volume` | 必须 100 整数倍(继承 P0-3 §2 红线 4 lot size);否则 AMBIGUOUS |
| `positions[*].code` | 必须 6 位数字(A 股代码格式);否则 AMBIGUOUS |
| `positions[*].cost_price` | `> 0`;否则 AMBIGUOUS |
| 重复 `code` | `positions` 中同一 code 出现多次 → AMBIGUOUS(用户应合并) |

`对账采纳:*` 类回报字段交叉校验更轻:仅校验 ticket_id 存在性 + 当前 status=OPEN。

### 1.4 偏差阈值分级(锁定)

#### 1.4.1 三字段独立判定

| 字段 | 阈值 | 比较口径 |
|------|------|---------|
| `cash` | `\|expected - actual\| ≤ 1.00 元` | 绝对差(吸收佣金/印花税/部分执行手续费默认 0 累积误差) |
| `positions[code].volume` | `expected == actual`(严格 0%) | A 股以股为单位,本就是整数,不允许任何偏差 |
| `positions[code].cost_price` | `\|expected - actual\| ≤ 0.01 元` | 绝对差(吸收加权平均的尾差) |

#### 1.4.2 集合级判定

- `expected.positions` 与 `actual.positions` 的 `code` 集合必须**完全一致**(否则该 code 单独判 MISMATCHED:expected 缺 = 用户多报或公司行动加仓 / actual 缺 = 用户漏报或卖出未报)
- 集合一致后逐字段比对 volume / cost_price

#### 1.4.3 阈值检查算法(伪代码)

```python
# backend/services/reconciliation_threshold.py(实施期)

from dataclasses import dataclass

@dataclass(frozen=True)
class FieldDeviation:
    field: str          # e.g. "cash" / "positions[600519].volume"
    expected: str
    actual: str
    abs_diff: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class DeviationReport:
    ticket_id: str
    overall_passed: bool
    deviations: tuple[FieldDeviation, ...]


CASH_TOL = 1.00
COST_PRICE_TOL = 0.01


def detect_deviations(
    expected: MockBrokerSnapshot,
    actual: DailyReconciliation,
) -> DeviationReport:
    """逐字段检测 expected (MockBroker 镜像) vs actual (用户回报)。

    overall_passed=False 即触发 reconciliation_ticket OPEN → 飞书裁定卡。
    """
    devs: list[FieldDeviation] = []

    # cash
    cash_diff = abs(expected.cash - actual.reported_cash)
    devs.append(FieldDeviation(
        field="cash",
        expected=f"{expected.cash:.2f}",
        actual=f"{actual.reported_cash:.2f}",
        abs_diff=cash_diff,
        threshold=CASH_TOL,
        passed=cash_diff <= CASH_TOL,
    ))

    # positions: 集合 + 逐字段
    expected_codes = {p.code for p in expected.positions}
    actual_codes = {p.code for p in actual.reported_positions}

    for code in expected_codes - actual_codes:
        ep = next(p for p in expected.positions if p.code == code)
        devs.append(FieldDeviation(
            field=f"positions[{code}].presence",
            expected=f"vol={ep.volume},cost={ep.cost_price:.2f}",
            actual="missing",
            abs_diff=float("inf"),
            threshold=0.0,
            passed=False,
        ))

    for code in actual_codes - expected_codes:
        ap = next(p for p in actual.reported_positions if p.code == code)
        devs.append(FieldDeviation(
            field=f"positions[{code}].presence",
            expected="missing",
            actual=f"vol={ap.volume},cost={ap.cost_price:.2f}",
            abs_diff=float("inf"),
            threshold=0.0,
            passed=False,
        ))

    for code in expected_codes & actual_codes:
        ep = next(p for p in expected.positions if p.code == code)
        ap = next(p for p in actual.reported_positions if p.code == code)
        devs.append(FieldDeviation(
            field=f"positions[{code}].volume",
            expected=str(ep.volume),
            actual=str(ap.volume),
            abs_diff=abs(ep.volume - ap.volume),
            threshold=0.0,
            passed=ep.volume == ap.volume,
        ))
        cost_diff = abs(ep.cost_price - ap.cost_price)
        devs.append(FieldDeviation(
            field=f"positions[{code}].cost_price",
            expected=f"{ep.cost_price:.2f}",
            actual=f"{ap.cost_price:.2f}",
            abs_diff=cost_diff,
            threshold=COST_PRICE_TOL,
            passed=cost_diff <= COST_PRICE_TOL,
        ))

    overall = all(d.passed for d in devs)
    return DeviationReport(
        ticket_id=actual.ticket_id,
        overall_passed=overall,
        deviations=tuple(devs),
    )
```

#### 1.4.4 阈值的设计依据

- **现金 1 元**:MockBroker 当前 `commission_rate=0.0003 / stamp_tax_rate=0.001 / slippage_bps=2 / min_commission=5.0`。一笔 100 股 50 元的买单 = 5000 元名义 + 1 元滑点估算 + 5 元最低佣金。一笔典型零售用户日均 5-10 笔成交 + 部分执行手续费默认 0 累积,1 元已是 99% 场景的安全包络。超 1 元基本意味着漏单 / 多单 / 公司行动 / 系统估算口径偏离券商 — 都值得 ticket。
- **持仓股数 0%**:A 股以股为单位,任何偏差都是事实差异,没有"舍入差"的概念;0% 是唯一合理选择。
- **成本价 0.01 元**:加权平均(`(prev_cost*prev_volume + new_price*new_volume) / total`)的尾差最多到第 3 位小数;round 到 0.01 后偶尔 0.01 元差异是正常的,超过即可能 cost basis 算法分歧或公司行动配股稀释。

### 1.5 reconciliation_ticket 状态机与裁定语法

#### 1.5.1 ReconciliationTicket 数据模型

```python
# backend/services/reconciliation_ticket.py(实施期)

from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class ReconciliationTicketStatus(StrEnum):
    """对账工单状态机。

    流转(只允许下列单向迁移,跨态即红线):
        OPEN → RESOLVED_USER_AS_TRUTH         (用户裁定"采纳用户回报")
        OPEN → RESOLVED_SYSTEM_AS_TRUTH       (用户裁定"采纳系统镜像")
        OPEN → RESOLVED_AMENDED               (用户裁定"对账更正")
        OPEN → EXPIRED                        (次日 09:25 仍未裁定)
        EXPIRED → RESOLVED_*                  (用户在盘前补裁,允许直到次日 16:00)
    """
    OPEN = "OPEN"
    RESOLVED_USER_AS_TRUTH = "RESOLVED_USER_AS_TRUTH"
    RESOLVED_SYSTEM_AS_TRUTH = "RESOLVED_SYSTEM_AS_TRUTH"
    RESOLVED_AMENDED = "RESOLVED_AMENDED"
    EXPIRED = "EXPIRED"


class ReconciliationTicket(BaseModel):
    """对账工单(超阈值偏差时由系统创建,用户飞书显式裁定)。"""
    model_config = ConfigDict(frozen=True)

    ticket_id: str = Field(pattern=r"^RECON-\d{8}-\d{3}$")
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    created_at: datetime
    deviation_report: DeviationReport
    """触发本 ticket 的偏差全集(by-value 内嵌)。"""

    expected_snapshot_id: str
    """触发时刻 MockBroker 镜像的引用(by-reference 到 mockbroker_snapshots
    collection;P1-2 锁细节)。"""

    actual_reconciliation_id: str
    """对应 daily_reconciliations._id(by-reference)。"""

    status: ReconciliationTicketStatus = ReconciliationTicketStatus.OPEN

    resolved_at: datetime | None = None
    resolution_message_id: str | None = None
    """裁定回报对应的飞书消息 _id(by-reference)。"""

    amended_snapshot: MockBrokerSnapshot | None = None
    """status=RESOLVED_AMENDED 时填,记录用户最终给出的第三套镜像。"""
```

#### 1.5.2 系统发裁定卡(预写死)

OPEN 状态创建时立即发飞书。模板:

```python
TICKET_RESOLUTION_REQUEST_TEMPLATE = """\
【QuantMind 对账偏差 {ticket_id}】
{trade_date} 日终对账检测到以下偏差(超阈值字段):
{deviations_block}

请回复(任选其一):
对账采纳:用户回报 {ticket_id}
对账采纳:系统镜像 {ticket_id}
对账更正 {ticket_id} 现金 <真实现金> 持仓 <代码> <数量>股 成本 <价格>; ...

裁定前次日所有买卖类指令冻结。
本 ticket 截止次日 16:00;未裁定将持续冻结。"""
```

`deviations_block` 渲染规则(每行):

```
  cash: 系统 832145.30 / 用户 850000.00 (差 17854.70 > 阈值 1.00)
  positions[600036].presence: 系统 missing / 用户 vol=500,cost=38.20
  positions[600519].volume: 系统 100 / 用户 200 (差 100)
```

#### 1.5.3 状态机守门(继承 P0-4 §1.3.4 设计)

```python
# backend/services/reconciliation_state_machine.py(实施期)

ALLOWED_TICKET_TRANSITIONS: frozenset[
    tuple[ReconciliationTicketStatus, ReconciliationTicketStatus]
] = frozenset({
    (OPEN, RESOLVED_USER_AS_TRUTH),
    (OPEN, RESOLVED_SYSTEM_AS_TRUTH),
    (OPEN, RESOLVED_AMENDED),
    (OPEN, EXPIRED),
    (EXPIRED, RESOLVED_USER_AS_TRUTH),
    (EXPIRED, RESOLVED_SYSTEM_AS_TRUTH),
    (EXPIRED, RESOLVED_AMENDED),
})


def transition_ticket(
    ticket: ReconciliationTicket,
    target: ReconciliationTicketStatus,
    *,
    resolution_message_id: str | None = None,
    amended_snapshot: MockBrokerSnapshot | None = None,
    triggered_by: str,
) -> ReconciliationTicket:
    """状态机守门:校验迁移合法 + 记录 reconciliation_state_transitions + 返回 frozen 新对象。

    EXPIRED → RESOLVED_* 路径必须在次日 16:00 之前(继承 P0-4 §1.6 16:00 cutoff
    精神)。EXPIRED 之后 16:00 cutoff 之后任何裁定回报触发 AMBIGUOUS。
    """
    if (ticket.status, target) not in ALLOWED_TICKET_TRANSITIONS:
        raise InvalidTicketTransitionError(
            f"{ticket.ticket_id}: {ticket.status} → {target} not allowed"
        )
    if target == RESOLVED_AMENDED and amended_snapshot is None:
        raise ValueError("RESOLVED_AMENDED requires amended_snapshot")
    # 记 reconciliation_state_transitions collection 留痕
    return ticket.model_copy(update={
        "status": target,
        "resolved_at": datetime.now(SHANGHAI) if target != EXPIRED else None,
        "resolution_message_id": resolution_message_id,
        "amended_snapshot": amended_snapshot,
    })
```

#### 1.5.4 应用裁定到 MockBroker

```python
# backend/services/reconciliation_applier.py(实施期)

async def apply_ticket_resolution(
    ticket: ReconciliationTicket,
    mock_broker: MockBroker,
) -> None:
    """根据 ticket 状态把裁定结果应用到 MockBroker。

    - RESOLVED_USER_AS_TRUTH: 把 actual_reconciliation 的现金/持仓覆盖 MockBroker
    - RESOLVED_SYSTEM_AS_TRUTH: 不变 MockBroker(用户认可系统是对的)
    - RESOLVED_AMENDED: 把 amended_snapshot 覆盖 MockBroker

    覆盖 MockBroker 的具体写入路径(reset + reapply / 增量 patch)属
    P1-2 范围;本决策仅锁定"必须经过此 applier,不允许直接修改 MockBroker
    内部 dict"。
    """
    if ticket.status == OPEN:
        raise ValueError(f"Cannot apply OPEN ticket {ticket.ticket_id}")
    if ticket.status == RESOLVED_SYSTEM_AS_TRUTH:
        return  # 无需变更
    target_snapshot = (
        await load_actual(ticket.actual_reconciliation_id)
        if ticket.status == RESOLVED_USER_AS_TRUTH
        else ticket.amended_snapshot
    )
    await mock_broker.reset_to_snapshot(target_snapshot)
    # mockbroker_archives 留痕(P0-1 §1.3.1 #1 同款机制,但 archive_id 用
    # reconciliation_archive_{ticket_id}_{ISO timestamp})
```

### 1.6 fail-closed 冻结机制

#### 1.6.1 冻结的范围

`reconciliation_tickets` 中存在 `status=OPEN` 或 `status=EXPIRED` 的 ticket 时:

| 行为 | 是否允许 |
|------|---------|
| `simulation_auto` 数据采集 / 多 Agent 分析 / MiroFish 推断 | ✅ 允许 |
| HOLD 类 InstructionPlan(只入 ledger) | ✅ 允许 |
| BUY/SELL 类 InstructionPlan **生成**(走完整 9-Agent 辩论 + RiskEngine) | ✅ 允许(继续生成入库 status=VALIDATED) |
| BUY/SELL 类 InstructionPlan **路由**(ModeRouter → SimulationExecutor / FeishuMessenger) | ❌ **冻结** |
| 系统告警(行情断流 / LLM 不可用 / 数据质量) | ✅ 允许(走主通道发飞书 + 备用 webhook) |
| 解析新到达的 ExecutionReport(超时回报 / 盘后补录) | ✅ 允许(P0-4 §1.6 16:00 cutoff 已锁,与本冻结独立) |
| 创建新的 reconciliation_ticket | ✅ 允许(允许多 ticket 并存) |

#### 1.6.2 冻结的实现位置

ModeRouter 在路由前查询 `reconciliation_tickets.count_documents({"status": {"$in": ["OPEN", "EXPIRED"]}})`:

- count > 0 → 把 InstructionPlan.status 从 VALIDATED 直接迁移到 `REJECTED`,`rejection_reason="reconciliation_freeze:tickets={ids}"`(经 instruction_plan_state_machine.transition 守门;P0-4 §1.3.4 ALLOWED_TRANSITIONS 已含 VALIDATED → REJECTED)
- 同时通过备用 webhook 发"冻结期间生成了新的买卖建议但被拒绝"告警(单次每天最多发 1 条,防告警风暴)

> **设计原理**:不在 InstructionPlanBuilder 阶段 short-circuit(继续完整生成 + 入库)而在 ModeRouter 阶段拒绝 — 这样 simulation_auto 的"多 Agent 辩论 + RiskEngine 7-check"持续运行,前端 InstructionCenter 仍能展示"冻结期间系统会怎么决策",方便用户快速判断 ticket 影响面。冻结只是"最后一公里"不发不撮合。

#### 1.6.3 EXPIRED 的语义

OPEN ticket 在次日 09:25 Asia/Shanghai 仍未收到裁定回报 → 状态机迁移到 EXPIRED:

- 持续冻结买卖类 InstructionPlan
- 不重发裁定卡(防飞书噪声)
- 用户在 EXPIRED 后任意时刻仍可通过 `对账采纳:*` / `对账更正` 回报触发 RESOLVED_*(状态机已允许 EXPIRED → RESOLVED_*)
- 直到 ticket → RESOLVED_*,冻结才解除

#### 1.6.4 冻结期间的 ChasePoller 行为

继承 P0-4 §1.4 ChasePoller 仍正常扫描 `status=DISPATCHED` 的 InstructionPlan(冻结前已派发的指令仍走原回报路径)。本决策不影响 P0-4 的追问超时逻辑。

### 1.7 公司行动第一阶段处理

#### 1.7.1 不支持的列表

第一阶段(P0-5 范围)系统**不**主动处理以下事件,**不**调整 MockBroker:

- 现金分红(派息)
- 股票分红(送股)
- 公积金转增股本(转股)
- 配股(增发)
- 股票拆分 / 合并
- 停牌 / 复牌(交易日历影响,留 P0-8 数据可信度决策)
- 重大资产重组(代码替换)
- 退市

#### 1.7.2 处理方式

用户持仓涉及上述事件时:

1. 系统在每日 16:00 对账时检测到偏差(stock 多/少 = 送股配股 / cash 多/少 = 分红)
2. 触发 reconciliation_ticket
3. 用户在飞书用 `对账更正 RECON-... 现金 ... 持仓 ...` 一次性给出真实快照
4. 系统 RESOLVED_AMENDED 应用,MockBroker 重置到该快照

> **设计原理**:第一阶段 watchlist 受控规模(P0-9 范围)+ 自上而下选股偏稳健大盘 + 单只股票典型分红配股频次低,公司行动场景估计每月发生 0-2 次。引入复权数据源(akshare 已有但接口稳定性待验)+ 修改 MockBroker 撮合逻辑(自动加股 / 加现金)+ 修改 cost_price 重算(送股摊薄)的实施成本远高于"用户用对账更正手工触发"。**第二阶段(P2-1 / P1-2 amendment)**可单独引入"公司行动事件源"自动处理,届时本决策走 amendment。

#### 1.7.3 停牌的特殊处理(留待 P0-8)

停牌期间系统不应继续生成该股票 BUY/SELL InstructionPlan(否则用户在飞书看到"买入 600519 100股"但券商会拒单,冗余噪声)。停牌信号源、停牌事件检测、watchlist 临时排除规则都属 **P0-8 数据与资讯可信度** 范围。P0-5 仅承诺"停牌期间偏差由对账更正吸收",不承担实时停牌检测责任。

### 1.8 与 P0-4 回报路径的严格边界

P0-4 与 P0-5 的回报形态都通过飞书长连接进入 `parser_dispatcher`。dispatcher 按前缀严格路由:

| 前缀 | 路由到 | 决策来源 |
|------|--------|---------|
| `已执行` / `部分执行` / `未执行` | `execution_report_parser` | P0-4 §1.2 |
| `更正 已执行` / `更正 部分执行` / `更正 未执行` | `execution_report_parser` | P0-4 §1.2.4 |
| `盘后补录 已执行` / `盘后补录 部分执行` / `盘后补录 未执行` | `execution_report_parser` | P0-4 §1.2.5 |
| `对账无误` / `对账差异` / `对账更正` | `reconciliation_parser` | P0-5 §1.3 |
| `对账采纳:` | `reconciliation_parser` | P0-5 §1.3 |

> **关键边界**:`更正` 和 `对账更正` 是不同前缀(2 字 vs 4 字)— 解析器之间零交叉。P0-4 的 `更正` 针对**单条 ExecutionReport** 的覆盖(关联 `instruction_id`);P0-5 的 `对账更正` 针对**整个账户镜像**的覆盖(关联 `ticket_id`)。两者从未在同一指令上歧义,正则前缀已区分。

### 1.9 手续费与印花税估算误差吸收

P0-4 §1.2.2 留下的伏笔回应:

> 部分执行模板**不带 `手续费 ...` 字段**,默认 0.00。这是一致与已执行模板的折中 ... P0-5 选择"省略 = 0",由 P0-5 / P1-2 决定是否在日终对账时补对。

P0-5 的回应:

- **不在 P0-5 范围引入"系统主动重算手续费补对" 机制**;实施成本高且不必要
- 部分执行手续费默认 0 + 系统估算口径(commission_rate / stamp_tax_rate / slippage_bps)与券商真实结算的累积偏差 → 由 1 元现金阈值天然吸收
- 累积超 1 元 → 触发 ticket → 用户用 `对账更正` 一次性给出真实现金;系统不追溯具体哪一笔差了多少
- 这是"对账驱动收敛"模式 — 第一阶段简化方案,放弃精细的逐笔重算,换日终一次性归零

> **为什么不每日重算**:精细重算需要券商真实费率数据(用户的真实佣金折扣 / 是否免印花税新政等),第一阶段难以稳定获取;每日对账的口径误差由用户负责"以现实为准"覆盖,系统的估算只是次佳信号。当用户长期对账偏差稳定在某个方向(比如系统总比券商多算 0.5 元手续费),前端 ReconciliationCenter 可在 1 周后展示"建议在 broker.yaml 调低 commission_rate"提示(实施期 P1-2 / P1-7 衍生功能,不在 P0-5 范围)。

### 1.10 每周人工导出对账(可选路径)

#### 1.10.1 默认依赖每日闭环

P0-5 不强制每周人工导出;每日 16:00 自动对账 + ticket fail-closed 已能保证短期镜像一致性。

#### 1.10.2 前端 ReconciliationCenter 的导出能力(可选)

前端提供按周导出 CSV / JSON:

- 本周每日 `daily_reconciliations`(系统镜像 + 用户回报 + 偏差摘要)
- 本周所有 `reconciliation_tickets`(状态 + 裁定结果 + 应用前后镜像)
- 本周所有 `state_transitions`(InstructionPlan 状态机变化)

用户可拿这份导出与券商真实成交单 PDF 离线手工对照。**系统绝不自动解析 PDF**(LLM 不进对账路径,继承本决策 §2 红线 6)。

#### 1.10.3 第二阶段引入券商成交单结构化导入(amendment 路径)

若用户后续希望系统自动校验券商导出的 CSV(同花顺 / 通达信 / 东方财富导出格式),需走 `P0-5-amendment-{date}-broker-export-import.md`,届时严格正则解析(同 P0-4 风格),不引入 LLM。

## 2. 红线 / 边界(立即生效)

P0-5 落地后这些立即成为代码硬约束:

1. **不引入独立的 UserReportedPortfolio collection**:MockBroker 是 `feishu_on` 时唯一账户镜像(继承 P0-1 §2 红线 3 不允许两条平行账本);任何代码尝试新建 `user_reported_portfolios` collection 即红线违规
2. **日终对账必须由系统主动发起**:用户主动发"日终对账"的旧建议方案(决策清单 §P0-5 草稿示例)**不再适用**;若收到用户自发的对账文本(无 ticket_id),系统按 AMBIGUOUS 处理,飞书提示"请等待系统在 16:00 自动发起对账"
3. **偏差阈值锁定**:cash 1.00 元 / volume 0% / cost_price 0.01 元;调整必须先走 `P0-5-amendment-{date}-threshold-adjustment.md`
4. **超阈值必须创建 reconciliation_ticket**:严禁系统自动以用户回报覆盖 MockBroker(违反 P0-4 fail-closed 精神)
5. **OPEN / EXPIRED ticket 期间冻结买卖类 InstructionPlan 路由**:ModeRouter 必须在路由前查询 ticket 状态;短路在路由阶段而不在生成阶段(simulation_auto 仍持续运行)
6. **LLM 严禁参与对账路径**:`backend/services/reconciliation*.py` / `backend/integrations/feishu/reconciliation*.py` 严禁 `import backend.llm.*`;裁定卡 / 对账请求文案全部预写死(继承 P0-4 §2 红线 2 LLM 隔离原则)
7. **ticket_id 必须严格匹配** `^RECON-\d{8}-\d{3}$`;同一 trade_date seq ≥ 1000 抛 ValueError
8. **5 种用户回报形态严格正则 only**:任何不满足 §1.3.1 五条正则 + 字段交叉校验的回报 → AMBIGUOUS,自动回澄清飞书;**绝不更新 ticket / MockBroker**
9. **绝不猜测 ticket_id**:回报中 ticket_id 必须严格匹配且在 OPEN 或 EXPIRED 状态;**绝不**通过"用户最近一次对账"等上下文反推
10. **`对账更正` 与 P0-4 `更正` 严格不混淆**:dispatcher 按前缀路由,zero crossover;违规即红线
11. **公司行动第一阶段不支持自动处理**:任何在 `backend/services/reconciliation*.py` 中引入复权计算 / 自动送股 / 自动加现金的代码都属红线违规;必须先走 amendment
12. **MockBroker 覆盖必须经过 ReconciliationApplier**:严禁直接 mutation MockBroker 内部 `_cash` / `_positions` / `_trades`(继承 P0-3 §2 红线 12 immutability 原则;MockBroker 内部仍可变,但外部入口收口)
13. **裁定卡严禁走备用 webhook**:继承 P0-2 §2 红线 2(备用 webhook 仅发系统告警)+ P0-4 §2 红线 13(澄清飞书严禁走备用 webhook);主通道失活时不发裁定卡,只发"主通道异常"告警
14. **券商真实账户读取严禁**:绝不引入读取券商 API / 解析券商 PDF / 抓券商账户网页的代码(继承 P0-1 §2 红线 1);用户成交单只能离线人工对照
15. **状态机迁移必须经守门函数**:任何 `model_copy(update={"status": ...})` 直接绕过 `transition_ticket()` 即红线违规;实施期由 lint rule 阻止(继承 P0-4 §2 红线 14)
16. **DailyReconciliation / ReconciliationTicket 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16)

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-5 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 新增项(代码级)

- `backend/services/daily_reconciliation.py`:
  - `ReportedPosition` / `DailyReconciliation` frozen Pydantic 模型
  - `parse_reconciliation_report(raw_text, received_at) -> DailyReconciliation | AmbiguousReport`
  - 五条正则常量(§1.3.1.1-§1.3.1.5)+ 持仓子串正则
- `backend/services/reconciliation_threshold.py`:
  - `FieldDeviation` / `DeviationReport` frozen dataclass
  - `detect_deviations(expected, actual) -> DeviationReport`
  - `CASH_TOL` / `COST_PRICE_TOL` 常量
- `backend/services/reconciliation_ticket.py`:
  - `ReconciliationTicketStatus` StrEnum
  - `ReconciliationTicket` frozen Pydantic 模型
  - `make_ticket_id(trade_date: str, seq: int) -> str`
- `backend/services/reconciliation_state_machine.py`:
  - `ALLOWED_TICKET_TRANSITIONS` frozenset
  - `transition_ticket(ticket, target, ...)` 守门
  - `InvalidTicketTransitionError` / `PostCloseFreezeError`(继承 P0-4)
- `backend/services/reconciliation_applier.py`:
  - `apply_ticket_resolution(ticket, mock_broker)`
  - 通过 `mock_broker.reset_to_snapshot(target_snapshot)` 入口收口
- `backend/services/reconciliation_dispatcher.py`(可选):
  - 16:00 调度器,触发 `DailyReconciliationRequest` 创建 + 飞书发送
  - 与 P0-4 ChasePoller 同款异步任务
- `backend/services/reconciliation_freeze.py`:
  - `is_frozen() -> bool`(查 `reconciliation_tickets.count_documents({"status": {"$in": ["OPEN", "EXPIRED"]}})`)
  - 供 ModeRouter 调用
- `backend/integrations/feishu/reconciliation_renderer.py`(P0-2 §3.1 子树规划下):
  - `render_daily_reconciliation_request(snapshot, ticket_id, trade_date) -> str`
  - `render_ticket_resolution_request(ticket) -> str`
  - 模板预写死(§1.2.2 / §1.5.2)
- `backend/integrations/feishu/parser_dispatcher.py` 修改:
  - 增加 `对账无误 / 对账差异 / 对账更正 / 对账采纳:` 前缀路由分支
- 新 MongoDB collection `daily_reconciliations`:索引 `(ticket_id)` 唯一 + `(trade_date)` + `(received_at)`
- 新 MongoDB collection `reconciliation_tickets`:索引 `(ticket_id)` 唯一 + `(status, created_at)` + `(trade_date)`
- 新 MongoDB collection `reconciliation_messages`:索引 `(ticket_id)` + `(sent_at)`,记系统发的请求 / 裁定卡 + 用户回报全文留痕
- 新 MongoDB collection `mockbroker_snapshots`(P1-2 范围,P0-5 仅引用):每次 reconciliation_ticket 创建时拍下当前镜像快照,by-reference 关联

### 3.2 修改项

- `backend/services/mode_router.py`(P0-1 §3.2 已规划):
  - 路由前调用 `reconciliation_freeze.is_frozen()`;True 时把 InstructionPlan 状态迁移到 REJECTED(`rejection_reason="reconciliation_freeze:..."`),通过状态机守门
- `backend/broker/mock_broker.py`:
  - 新增 `async def reset_to_snapshot(snapshot: MockBrokerSnapshot) -> None`(在锁内 reset _cash / _positions / _trades_archive)
  - 新增 `async def get_snapshot() -> MockBrokerSnapshot`(为日终对账 + ticket 创建提供 frozen 快照)
- `backend/data/database.py`:
  - 新增 `save_daily_reconciliation()` / `query_daily_reconciliations()` / `save_ticket()` / `update_ticket()` / `query_open_tickets()` / `count_freeze_tickets()` / `record_ticket_state_transition()`
- `backend/integrations/feishu/longconn.py`(P0-2 范围):
  - worker 入队接口对接到 `parser_dispatcher.dispatch(event)` 已规划;P0-5 不再修改

### 3.3 配置项

- `config/reconciliation.yaml`(新):
  - `cash_tolerance_yuan: 1.00`
  - `cost_price_tolerance_yuan: 0.01`
  - `volume_tolerance_shares: 0`
  - `daily_reconciliation_send_offset_seconds: 30`(16:00 + 30 秒,等 chase_poller 收尾)
  - `expire_at_local_time: "09:25"`(次日盘前 cutoff)
  - `freeze_alert_max_per_day: 1`(防告警风暴)
- `.env`:无新增(所有飞书凭证仍走 shell env)

### 3.4 文档同步(本决策落地立即执行,见 §5.1)

- `CLAUDE.md` §1.3 进度行(P0-5 ✅,下一站 P0-6)
- `CLAUDE.md` §2.1 P0-5 行(状态 + 决策文档列 + 备注)
- `CLAUDE.md` §3.1 红线节(同步本文 §2 红线中与现有红线不重叠的部分,尤其 16 条新增红线)
- `MEMORY.md` 索引新增 `project_run_mode_p0_5.md`
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_5.md`

### 3.5 测试覆盖(实施期任务)

- `tests/test_reconciliation_parser.py`:
  - 五种用户回报正则 happy path(每种 ≥3 条样本)
  - 字段交叉校验失败(ticket_id 不存在 / cash 负数 / volume 非 100 倍 / cost_price=0 / 重复 code / positions 子串格式错)
  - 边界:多余空格 / Tab / 全角空格(预处理 strip + 单空格归一,其他 AMBIGUOUS)
  - 半角冒号 vs 全角冒号(对账采纳类必须全角)
- `tests/test_reconciliation_threshold.py`:
  - cash 阈值边界(0.99 元 PASS / 1.00 元 PASS / 1.01 元 FAIL)
  - cost_price 阈值边界
  - volume 0% 严格(±1 股 FAIL)
  - 集合差(系统多 / 用户多 / 双方都缺某 code)
  - 全 PASS / 全 FAIL / 混合
- `tests/test_reconciliation_state_machine.py`:
  - 全 ALLOWED_TICKET_TRANSITIONS 路径
  - 跨态拒绝(OPEN → RESOLVED_AMENDED 缺 amended_snapshot 抛 ValueError)
  - EXPIRED → RESOLVED_* 路径合法
- `tests/test_reconciliation_applier.py`:
  - RESOLVED_USER_AS_TRUTH 应用 → MockBroker 重置
  - RESOLVED_SYSTEM_AS_TRUTH 不变 MockBroker
  - RESOLVED_AMENDED 应用 → MockBroker 重置到 amended_snapshot
- `tests/test_reconciliation_freeze.py`:
  - is_frozen=True 时 ModeRouter 拒绝路由
  - is_frozen=False 时正常路由
  - simulation_auto / 告警在冻结期间不受影响
- `tests/test_reconciliation_renderer.py`:
  - 对账请求模板渲染(有持仓 / 无持仓 / 多只持仓)
  - 裁定卡模板渲染(单字段偏差 / 多字段偏差 / presence 偏差)
- 覆盖率:`backend/services/reconciliation_*.py` >90% / `reconciliation_state_machine.py` >95%

### 3.6 静态检查 / lint rule(实施期任务)

- 阻止 `backend/services/reconciliation*.py` / `backend/integrations/feishu/reconciliation*.py` 出现 `import backend.llm`(LLM 隔离)
- 阻止任何文件 `model_copy(update={"status": ...})` 在 ReconciliationTicket 上不经过 `transition_ticket()`(状态机绕过)
- 阻止 `backend/integrations/feishu/reconciliation_renderer.py` 接收非预定义模板字符串(LLM 输出污染)
- 阻止 `backend/integrations/feishu/fallback_webhook.py` 接收 ReconciliationMessage 类型(继承 P0-2 §2 红线 2)
- 阻止任何文件创建 `user_reported_portfolios` collection 或同义命名(防两条平行账本回潮)
- 阻止 `backend/services/reconciliation*.py` 引入 akshare/adata/baostock 复权字段(防止公司行动自动处理偷偷回潮;P2-1 amendment 路径才允许)

## 4. 决策依据

### 4.1 audit 引用

- audit §3.2 当前缺口"飞书回报模块缺失" — P0-4 已补齐成交回报,本决策补齐对账回报
- audit §5.2 / §5.3 未提对账机制 — 旧路线下"实盘账户" 由券商接口托管,无需对账;新路线 (P0-1 锁定) 后必须自建对账,本决策补齐
- audit §7 飞书集成全无 — P0-2 锁主备通道,P0-4 锁解析,P0-5 锁对账;三层完整覆盖
- audit §9 "decision_ledger 全链记录" — 本决策的 `daily_reconciliations` / `reconciliation_tickets` / `reconciliation_messages` / `reconciliation_state_transitions` 四个 collection 全部入 decision_ledger
- audit §15 "当前不建议做的事:让 LLM 直接决定账户状态更新" — 本决策 §1.1.2 严格收紧(LLM 完全不参与对账路径)

### 4.2 决策清单引用(§P0-5)

- 决策清单 §P0-5 列出 9 项需要决定的问题 — 本决策已全部覆盖:
  1. 初始现金 → P0-1 §1.3.1 #3 切换初始化已锁;本决策 §1.1.1 不重复
  2. 初始持仓 → 同上
  3. 佣金 / 印花税 / 过户费 → 本决策 §1.9 由 1 元阈值天然吸收
  4. 成交价口径 → MockBroker 现 slippage_bps=2 已有,P0-4 §1.2.1 fill_price 由用户回报为准;本决策 §1.4.1 cost_price 阈值 0.01 元
  5. 可卖股数 T+1 口径 → MockBroker 已有 today_bought_volume 机制,本决策 §1.4 阈值不涉及 T+1 字段(每日 16:00 之前已 advance_day)
  6. 分红 / 配股 / 送转 / 停牌 → 本决策 §1.7 不支持自动处理 + 走对账更正
  7. 用户回报与系统不一致 → 本决策 §1.4-§1.6 偏差阈值 + ticket + fail-closed 冻结
  8. 是否需要每日手工对账 → 本决策 §1.2 系统 16:00 自动发起 + 用户回报
  9. 偏差阈值 → 本决策 §1.4 三字段分级
- 决策清单 §P0-5 建议倾向"UserReportedPortfolio: 由用户回报驱动、由行情实时估值的账户状态镜像" — 本决策 §1.1.1 改为"MockBroker 在 feishu_on 时即承担此角色" — **避免两条平行账本红线违规**(P0-1 §2 红线 3)
- 决策清单 §P0-5 建议倾向"必须显示'未券商核验'状态" — 本决策通过 ticket OPEN/EXPIRED 状态 + 冻结买卖类 InstructionPlan 实现等价语义(用户未裁定即"未核验",系统不发新指令)
- 决策清单 §P0-5 产出物清单 — 本决策已对照覆盖:初始账户镜像表(P0-1)+ 日终对账模板(本决策 §1.2.2 + §1.5.2)+ 状态冲突处理规则(本决策 §1.5)+ 允许偏差阈值(本决策 §1.4)+ 是否需要每周人工导出(本决策 §1.10 不强制 + 提供导出能力)

### 4.3 代码事实抽检(2026-05-09 复核)

- `backend/broker/mock_broker.py:124-145` MockBroker `__init__` 已具备 _cash / _positions / _trades / _frozen_cash 字段 — 本决策 §3.2 新增 `reset_to_snapshot()` / `get_snapshot()` 方法对接
- `backend/broker/mock_broker.py:443-451` `advance_day()` T+1 推进已有 — 本决策 §1.4 阈值不依赖 T+1 字段(每日 16:00 之后由 chase_poller 与本决策的 daily_reconciliation_dispatcher 都已收尾)
- `backend/broker/models.py:75-100` `Position` / `AccountInfo` frozen Pydantic — 本决策的 `MockBrokerSnapshot`(P1-2 范围)将复用相同字段集
- `backend/broker/mock_broker.py:255-296` `_fill_order` 现已计算 commission / stamp_tax / slippage_cost — 本决策 §1.9 的"系统估算口径"指的就是这套 + min_commission=5.0
- `backend/broker/mock_broker.py:62-83` `_calc_commission` / `_calc_stamp_tax` / `_apply_slippage` 纯函数 — 本决策不修改,1 元阈值吸收估算误差
- `backend/data/trading_hours.py:7-15` SHANGHAI / 交易时段常量 — 本决策的 16:00 cutoff + 09:25 expire 都基于 SHANGHAI tzinfo
- `backend/data/database.py:36-174` `initialize()` 已建多个 collection 索引 — 本决策的 4 个新 collection 索引在 §3.1 已规划
- `backend/services/cost_guard.py:41-50` `BudgetState` frozen dataclass + `_classify` fail-closed 范式 — 本决策的 `DeviationReport` / 偏差检测算法直接借鉴此风格
- `backend/services/shadow_runner.py:66-78` 异步任务 + 锁 + admission control 范式 — 本决策的 `daily_reconciliation_dispatcher`(实施期)直接复用此范式
- `backend/services/reconciliation*.py` / `backend/integrations/feishu/reconciliation*.py` 在仓库中**不存在** — 全新增,实施期写入

### 4.4 用户选择记录(2026-05-09 决策对话)

| 问题 | 选择 |
|------|------|
| 日终对账由谁先发起、用什么节奏? | **A: 系统 16:00 自动报镜像 + 用户确认/差异** — 系统是事实源,用户只需扫一眼;减少用户文字输入成本 |
| 偏差阈值如何设定? | **A: 分级阈值:持仓股数 0% + 现金 ≤1 元 + 成本价 ≤0.01 元** — 匹配 P0-3 immutability + P0-4 fail-closed 风格 |
| 偏差超阈值时如何裁定? | **A: 创建 reconciliation_ticket,等待用户在飞书显式裁定** — 匹配 P0-4 fail-closed 状态机精神;裁定前冻结次日新买卖类 InstructionPlan |
| 公司行动第一阶段如何处理? | **A: 不支持,统一走"对账更正"语法手工调整** — 与第一阶段最简通路原则一致;留 P2-1 / P1-2 amendment 路径 |

四题全部选 Recommended,与 P0-1/P0-3/P0-4 已锁定的 fail-closed / immutability / LLM 不进路径 / 简洁优先原则连贯。

### 4.5 与 P0-1 / P0-2 / P0-3 / P0-4 的契合点对照

| 上游决策条款 | P0-5 承载方式 |
|-------------|--------------|
| P0-1 §1.1 MockBroker 是 feishu_on 时唯一账户镜像 | §1.1.1 不引入并行 UserReportedPortfolio collection;`daily_reconciliations` 仅作对照源 |
| P0-1 §1.3.1 #3 切换初始化对账模板 | §1.2.2 复用模板风格(可用现金 / 持仓 / 无持仓时填"无");格式严格对齐让用户复用肌肉记忆 |
| P0-1 §1.3.2 退出路径 A/B | 不影响本决策;退出 feishu_on 走 P0-1 路径,本决策只覆盖 feishu_on 内部周期 |
| P0-1 §1.4 切换期间冻结买卖类 InstructionPlan | §1.6 reconciliation_ticket 冻结机制完全继承此模式 |
| P0-1 §2 红线 3 不允许两条平行账本 | §2 红线 1 严禁新建 user_reported_portfolios collection |
| P0-2 §1.1 长连接 worker 3 秒 ack | dispatcher 接 §1.3 解析与 §1.5 裁定都走异步队列(P0-2 已锁) |
| P0-2 §2 红线 2 备用 webhook 不发买卖指令/对账请求/澄清 | §2 红线 13 裁定卡严禁走备用 webhook |
| P0-3 §1.5.1 position_summary by-value 字段集 | `MockBrokerSnapshot.positions[i]` 与 `position_summary.pre/post_cash` 字段集兼容(P1-2 范围统一) |
| P0-3 §1.7.1 数据流 | §1.6.2 ModeRouter 在路由前查冻结状态 — 不影响上游 InstructionPlanBuilder / RiskEngine |
| P0-4 §1.6 16:00 cutoff | §1.2.1 系统 16:00:30 之后才发对账请求,等 chase_poller 收尾 |
| P0-4 §1.6.3 与 P0-5 衔接 | §1.5 ticket 引入"对账更正"专用语法,与 P0-4 "更正"严格解耦;dispatcher 按前缀路由 |
| P0-4 §1.2.2 部分执行手续费默认 0 留待 P0-5 补对 | §1.9 由 1 元阈值天然吸收;不引入逐笔重算 |
| P0-4 §1.3.4 状态机守门函数 | §1.5.3 ReconciliationTicket 状态机直接复用此风格 |
| P0-4 §1.7 ExecutionReportApplier | §1.5.4 ReconciliationApplier 直接复用此风格(reset_to_snapshot 而非逐笔反向) |
| P0-4 §2 红线 2 LLM 严禁参与回报路径 | §2 红线 6 LLM 严禁参与对账路径 |
| P0-4 §2 红线 14 状态机守门 | §2 红线 15 同条款扩展到 ReconciliationTicket |

### 4.6 替代方案与拒绝理由

| 候选方案 | 拒绝原因 |
|---------|---------|
| 引入独立 `user_reported_portfolios` collection 与 MockBroker 平行 | P0-1 §2 红线 3 明确禁止两条平行账本;两个状态会随时间偏离,反而让对账更复杂 |
| 用户主动发对账,系统不主动催 | 决策清单 §P0-5 草稿示例倾向此模式,但与 P0-4 fail-closed 精神矛盾(系统不能假设用户会主动);长期会出现镜像漂移无人发现 |
| 系统 16:00 发空白模板让用户填 | 用户输入成本高,容易拖延导致漏对账;系统主动报镜像让用户对照零成本 |
| 偏差阈值完全 0% 严格 | MockBroker `commission_rate=0.0003 / slippage_bps=2` 必然产生 0.01-1 元尾差;0% 阈值会导致每天都触发 ticket,体验崩坏 |
| 偏差阈值宽松(现金 0.5%-1%) | 5000 元差(100 万账户 0.5%)足以扭曲 InstructionPlan.position_summary,系统会发出错误指令;明显违反 P0-4 fail-closed 精神 |
| 系统永远以用户回报为准自动重置 MockBroker | 用户飞书打错字会污染镜像;违反 P0-4 §2 红线 1"不能自动写入持仓";丢失根因分析能力 |
| 系统拒绝接受用户回报要求重报 | 若真是系统镜像错(commission 估算偏差累积 / 系统漏 ExecutionReport),用户被卡死;无明确出口 |
| 公司行动全自动处理 | 需可靠复权数据源 + 修改 MockBroker 撮合 + 修改 cost_basis 重算;实施成本远超第一阶段收益;watchlist 受控规模下手工调整成本低 |
| 仅停牌支持 | 停牌信号源 (akshare/adata) 稳定性待验,引入新数据质量依赖;停牌期间偏差由对账更正吸收已能覆盖 |
| 引入"对账更正"专用语法 vs 复用 P0-4 "更正" | "更正" 关联 instruction_id(单条成交);"对账更正" 关联 ticket_id(整个镜像);语义完全不同,共用前缀会导致 dispatcher 无法路由 |
| 自动解析券商成交单 PDF | LLM 进对账路径违反 §2 红线 6;OCR / PDF 解析准确率不足以支持账户镜像写入;留人工对照为主 |
| 不引入 fail-closed 冻结,仅创建 ticket 让用户裁定 | ticket 未裁定期间系统继续发买卖指令会基于错的 position_summary,加剧偏差;冻结是自然兜底 |
| 冻结 BUY/SELL 生成阶段(InstructionPlanBuilder) | 失去观察窗口 — 用户无法看到"冻结期间系统会怎么决策";前端 InstructionCenter 用户体验差 |
| EXPIRED ticket 自动 RESOLVED_USER_AS_TRUTH | 没用户裁定就自动覆盖镜像 = 自动重置反面;违反 §2 红线 4 |
| 每周强制人工导出对账 | 用户负担重 + 第一阶段 watchlist 受控规模偏差概率低,强制反而流失用户;改为可选 + 提供导出能力 |
| 引入 LLM 在裁定卡生成 | 文案预写死已能覆盖所有偏差场景(deviations_block 渲染规则枚举有限);LLM 引入超时 + 成本 + 错译风险,无收益 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-5 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步(本 PR 内随决策一起提交)

- [x] 写入本决策文档(`docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md`)
- [ ] 更新 `CLAUDE.md` §1.3:P0-5 状态从 ⏳ 改为 ✅,链接本文件,下一站改 P0-6
- [ ] 更新 `CLAUDE.md` §2.1:P0-5 行 决策文档列填本文件路径,备注列收紧为决策结果摘要
- [ ] 更新 `CLAUDE.md` §3.1 红线节:同步本文 §2 红线 1-16 中与现有红线不重叠的部分(尤其单一镜像 + 系统主动发起 + 三字段阈值 + ticket fail-closed + 冻结 + LLM 不进对账路径 + ReconciliationApplier 入口收口)
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_5.md` 条目
- [ ] 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_5.md`
- [ ] commit 本决策文档 + CLAUDE.md/MEMORY.md 同步更新(单 PR);**等用户授权再 commit**;不自动 push

### 5.2 依赖本决策的下游 P0/P1 决策

- **P0-6 simulation_auto 验收标准**:决定 feishu_on 切换阈值是否包含"过去 N 天对账平均偏差 < ¥X" 指标
- **P0-7 风险红线与指导强度**:决定连续 N 天对账偏差超阈值是否触发"暂停发指令" 红线;每日新指令上限是否考虑当日是否有 OPEN ticket
- **P0-8 数据与资讯可信度**:决定停牌信号源 + 停牌期间 watchlist 临时排除规则(本决策 §1.7.3 已留接口)
- **P0-10 LLM 角色边界**:本决策 §2 红线 6 已严格收紧 LLM 在对账路径的边界,P0-10 全局对照时本节直接引用
- **P1-1 新核心数据模型**:本决策 §3.1 已规划 `daily_reconciliations` / `reconciliation_tickets` / `reconciliation_messages` / `reconciliation_state_transitions` 四个新 collection 的索引;P1-1 进一步锁字段级 schema 与查询 API
- **P1-2 MockBroker 持久化与实时估值**:本决策 §1.5.4 给出 ReconciliationApplier 的 reset_to_snapshot 高层路径;P1-2 锁定 MockBroker `reset_to_snapshot()` / `get_snapshot()` 撮合细节与幂等性保证;P1-2 同时定义 `MockBrokerSnapshot` Pydantic 模型(本决策引用但未锁定字段集)
- **P1-3 飞书消息形态**:本决策对账请求 + 裁定卡均纯文本;P1-3 锁第二阶段交互卡片演进路径(若引入按钮裁定,需要新增 `card.action.trigger` 解析分支;本决策范围外的扩展)
- **P1-7 成本预算**:对账每日 1 次 + 偶发 ticket 飞书消息频次极低,本决策不影响 LLM 成本;P1-7 评估时对账渠道飞书消息条数已可忽略
- **P2-1 MiroFish 真实使用范围**:不影响本决策(MiroFish 不进对账路径)

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.6 编写 implementation 任务列表;`backend/services/reconciliation*.py` 与 P0-4 中 `execution_report*.py` / `instruction_plan_state_machine.py` 与 P0-2 中 `feishu/longconn.py` / `parser_dispatcher.py` 紧密耦合,实施期统一规划 PR 边界(可能与 P0-4 同 PR 或紧邻 PR)
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级:新对账层 + 状态机 + 冻结路由 + MockBroker 入口收口)
- [ ] 测试覆盖(§3.5 已列):
  - `tests/test_reconciliation_parser.py` 五正则 + 字段交叉校验 + 边界
  - `tests/test_reconciliation_threshold.py` 三字段阈值边界 + 集合差
  - `tests/test_reconciliation_state_machine.py` 全 ALLOWED_TICKET_TRANSITIONS + 跨态拒绝
  - `tests/test_reconciliation_applier.py` 三种 RESOLVED 应用路径
  - `tests/test_reconciliation_freeze.py` 冻结 ModeRouter / 不冻结 simulation_auto
  - `tests/test_reconciliation_renderer.py` 模板渲染
  - 覆盖率:`backend/services/reconciliation_*.py` >90% / `reconciliation_state_machine.py` >95%
- [ ] 静态检查 / lint rule(§3.6 已列):LLM 隔离 / 状态机绕过 / 模板污染 / 备用 webhook 误用 / 平行账本回潮 / 复权字段回潮
- [ ] 集成测试:从 16:00 cutoff → 系统发对账请求 → 用户回报 → 偏差检测 → ticket 创建 → 飞书发裁定卡 → 用户裁定 → ReconciliationApplier 应用 → MockBroker 重置 → 冻结解除 → 次日 InstructionPlan 正常路由 全链 e2e

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-5-amendment-{日期}-{原因}.md`。_
