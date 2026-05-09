# P0-7 — 风险红线与指导强度(仓位三连阈值 + 日内熔断 + universe 白名单 + LLM 不可改 RiskConfig)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-7 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §3.2 / §5.2 / §8 / §11 / §15 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-7 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.6 多 Agent 辩论 + §2 红线 8 LLM 不决定股数/价格/风控边界)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.5 风控耦合 + §2 红线 6 risk_summary 长度)+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(尤其 §1.7 ExecutionReportApplier 链入 MockBroker)+ `docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md`(尤其 §1.6 OPEN/EXPIRED ticket 冻结买卖类路由)+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(尤其 §1.2 指令完整率 ≥95% + §1.3 最大回撤 ≤8% / PnL≥0 / 沪深 300 超额 ≥0)|
| 派生 amendment | `docs/decisions/P0-3-amendment-2026-05-09-extend-risk-checks-from-7-to-14.md`(实施期产出;扩展 P0-3 §1.5 + §2 红线 6 的 7-check 到 14-check)|
| 替代       | `config/risk.yaml` 当前阈值组(`max_single_stock_pct=0.20` 等)被本决策的"保守组合"替换 |

## 决策摘要

QuantMind 第一阶段风险红线采用 **保守仓位三连阈值 + 中性日内熔断 + 中性 universe 白名单 + 全锁 RiskConfig + 自进化提议通道** 架构:

1. **仓位三连阈值**(保守组合,对应 Q1 答案):单股仓位 ≤ 15% / 总仓位 ≤ 70% / 单次指令金额 ≤ 50,000 RMB。`BrokerConfig.initial_capital=1,000,000` 下,1000k 资金可同时持 ≥ 10 只股票,单股至少需要 ≥ 4 笔指令才能达上限,强制资金分散。

2. **日内熔断**(中性组合,对应 Q2 答案):每日新指令数 ≤ 5 / 日内组合跌幅 ≤ -5% 触发熔断 / 连亏 3 笔触发熔断 / 熔断冷却 60 分钟。与 P0-6 §1.3.1 最大回撤 8% 硬门槛留 3% 余量,与 P0-6 §1.2.1 指令完整率 95% 硬门槛兼容(REJECTED 拦截率上限约 5%)。

3. **universe 白名单**(中性组合,对应 Q3 答案):允许 `sh_main`(600/601/603/605)+ `sz_main`(000/002)+ `chuangye`(300/301)+ `etf`(从行情源元数据识别)。**禁止** ST(包括 `*ST`)/ 科创板(688)/ 北交所(8x)/ 可转债(11x/12x)。**禁止涨停买入** + **禁止跌停卖出**(同向追涨杀跌)。

4. **LLM 不可改 RiskConfig**(全锁 + 提议通道,对应 Q4 答案):**runtime 任何路径不存在 RiskConfig 写入端口**,所有阈值改动必须 git diff `config/risk.yaml` + 进程重启 + amendment 文档备案。新增 `risk_parameter_proposals` collection — Agent 复盘时可写入"建议变更条目"(只读建议),由项目所有者每周人工 review,接受后才走 YAML edit + amendment + 重启流程。**LLM/Agent 永不持有 RiskConfig 写引用,frozen Pydantic schema + lint rule 双重护栏。**

5. **RiskEngine 从 7-check 扩展为 14-check**(派生 P0-3 amendment):
   - 1-7 = P0-3 已锁的 7-check(code_validity / price_reasonability / volume_validity / fund_sufficiency / position_limit / total_position_limit / trading_time)
   - 8 = `total_position_pct`(总仓位市值 ≤ 70%)
   - 9 = `single_instruction_amount`(单次指令金额 ≤ 50,000)
   - 10 = `daily_new_instruction_count`(当日新指令数 ≤ 5)
   - 11 = `universe_whitelist`(板块/标的白名单)
   - 12 = `limit_up_down_block`(涨停买入 / 跌停卖出禁令)
   - 13 = `daily_loss_halt`(日内组合跌幅 -5% 熔断)
   - 14 = `consecutive_loss_halt`(连亏 3 笔熔断)
   - `RiskCheckSummary` 长度从 7 → 14;`InstructionPlan.risk_summary` 同步;P0-3 §2 红线 6 走单独 amendment 文档调整。

6. **DailyTradingState 注入路径**:RiskEngine 仍是纯函数(继承 P0-1 §2 红线 8),check 10/12/13/14 需要的"当日累计指令数 / 当日组合 PnL / 最近 3 笔 PnL / 当前现价"统一封装在 `@dataclass(frozen=True) class DailyTradingState`,由 InstructionPlanBuilder 在调用 `validate_order` 前从 MockBroker / decision_ledger / 行情源装配传入。RiskEngine **不**依赖 MongoDB / 不依赖行情源(继承"backend/risk/ 严禁 import backend.llm/agents/mirofish"原则,`backend.data` 也不在白名单)。

7. **熔断状态持久化**:新增 `circuit_breaker_state` collection(单文档,`_id="singleton"`),记录 `is_in_halt` / `halt_reason` / `halt_until` / `triggered_at`;熔断解除前 InstructionPlanBuilder 直接拒绝出 InstructionPlan(降级 HOLD),不进 RiskEngine;熔断也是 P0-1 §1.4 切换冻结 + P0-5 §1.6 ticket 冻结之外的第三种买卖类路由冻结来源。

8. **板块识别独立纯模块**:新增 `backend/data/stock_metadata.py`(纯 Python,严禁 import backend.llm),提供 `classify_board(code, name) → Board` / `is_st(code, name) → bool` / `get_price_limit_pct(board) → float` 三个纯函数。ETF 识别由"代码段优先 + 行情源 instrument_type 验证"双轨;主板/创业板/科创板按代码前缀;ST 通过股票名称前缀 `ST`/`*ST` + 行情源 ST 列表交叉验证。

## 1. 决策具体内容

### 1.1 RiskConfig 字段集扩展

#### 1.1.1 PositionLimitsConfig(扩展 4 字段)

```python
# backend/broker/models.py(实施期修改)

class PositionLimitsConfig(BaseModel):
    """仓位上限参数。Frozen + LLM 不可改(详见 §1.6)。"""
    model_config = ConfigDict(frozen=True)

    # === P0-3 已锁字段(阈值随 P0-7 收紧)===
    max_single_stock_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    """单股市值占总资产上限。P0-7 由 0.20 → 0.15。"""

    max_sector_pct: float = Field(default=0.40, ge=0.0, le=1.0)
    """板块市值占总资产上限。第一阶段不强制实现 sector 检查
    (sector 分类需独立元数据表,留 P1);保留字段以与 risk.yaml 对齐。"""

    max_total_positions: int = Field(default=10, ge=1)
    """同时持有不同股票数量上限(并发持仓数,与 max_total_position_pct 互补)。"""

    price_deviation_limit: float = Field(default=0.05, ge=0.0, le=1.0)
    """限价偏离前收盘上限,主板默认 ±5%
    (注:第一阶段 universe 含创业板 ±20% — 该字段在 check 2 中按板块查
    `get_price_limit_pct(board)` 而非全局常量;详见 §1.4.2)。"""

    volume_lot_size: int = Field(default=100, ge=1)
    """A 股最小交易单位。"""

    # === P0-7 新增字段 ===
    max_total_position_pct: float = Field(default=0.70, ge=0.0, le=1.0)
    """总仓位市值占总资产上限(check 8 输入)。"""

    max_single_instruction_amount: float = Field(default=50_000.0, gt=0.0)
    """单次 BUY/SELL 指令金额上限,RMB
    (`limit_price * volume`,check 9 输入)。
    SELL 也受限:防止单笔大额清仓造成市场冲击。"""

    max_daily_new_instructions: int = Field(default=5, ge=1)
    """当日新生成 InstructionPlan 数量上限(check 10 输入)。
    HOLD 类不计入(HOLD 不路由,只入 ledger);BUY+SELL 合计。"""
```

#### 1.1.2 CircuitBreakerConfig(扩展 2 字段)

```python
class CircuitBreakerConfig(BaseModel):
    """熔断参数。Frozen + LLM 不可改。"""
    model_config = ConfigDict(frozen=True)

    # === 已有字段(阈值锁定为 P0-7 中性组合)===
    daily_loss_limit_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    """日内组合跌幅熔断阈值(check 13 输入)。
    跌幅基准 = 当日开盘 NAV(MockBroker 09:30 mark-to-market)。"""

    consecutive_loss_count: int = Field(default=3, ge=1)
    """最近 N 笔已 FILLED 交易连亏触发熔断(check 14 输入)。
    『亏』= net_amount < 成本(已扣手续费/印花税/滑点)。"""

    cooldown_minutes: int = Field(default=60, ge=1)
    """熔断冷却分钟数。"""

    # === P0-7 新增字段 ===
    halt_priority_order: tuple[str, ...] = Field(
        default=("daily_loss", "consecutive_loss")
    )
    """同时触发多种熔断时,记录主因的优先级
    (按列表顺序;前面优先;daily_loss 优先于 consecutive_loss)。"""

    apply_to_sell_orders: bool = Field(default=False)
    """熔断期间是否冻结 SELL 类指令。
    第一阶段 False — 熔断期间允许 SELL 减仓退出,但禁 BUY 加仓
    (避免熔断变成『锁仓困死』风险陷阱)。"""
```

#### 1.1.3 UniverseConfig(全新 section)

```python
class UniverseConfig(BaseModel):
    """标的白名单与价格边界规则。Frozen + LLM 不可改。"""
    model_config = ConfigDict(frozen=True)

    allowed_boards: tuple[str, ...] = Field(
        default=("sh_main", "sz_main", "chuangye", "etf")
    )
    """允许交易的板块。Board 标识符固定为:
    sh_main / sz_main / chuangye / kchuang / beijiao / etf / convertible_bond / unknown
    第一阶段不允许 kchuang / beijiao / convertible_bond;
    扩到科创板需 amendment 同步调整 price_deviation_limit per board。"""

    forbidden_st: bool = Field(default=True)
    """禁交 ST / *ST / S*ST 股票(check 11 输入)。"""

    forbid_buy_at_limit_up: bool = Field(default=True)
    """禁止已涨停时 BUY(check 12 输入)。
    判定 = current_price >= prev_close * (1 + price_limit_pct(board))
    且 current_price 是当前实时报价(InstructionPlanBuilder 注入)。"""

    forbid_sell_at_limit_down: bool = Field(default=True)
    """禁止已跌停时 SELL(check 12 输入)。"""

    price_limit_pct_by_board: dict[str, float] = Field(
        default_factory=lambda: {
            "sh_main": 0.10,
            "sz_main": 0.10,
            "chuangye": 0.20,
            "etf": 0.10,
        }
    )
    """各板块的涨跌停幅度(用于 check 12 与 check 2 的边界计算)。
    若 P0-7 amendment 引入科创板需追加 'kchuang': 0.20。"""
```

#### 1.1.4 RiskConfig 主模型

```python
class RiskConfig(BaseModel):
    """完整风控配置。Frozen + LLM 不可改;runtime 不存在 setter/writer 路径。"""
    model_config = ConfigDict(frozen=True)

    position_limits: PositionLimitsConfig
    stop_loss: StopLossConfig
    circuit_breaker: CircuitBreakerConfig
    universe: UniverseConfig  # P0-7 新增
```

#### 1.1.5 risk.yaml 锁定阈值

```yaml
# config/risk.yaml(实施期替换)

position_limits:
  max_single_stock_pct: 0.15           # P0-7 由 0.20 → 0.15
  max_sector_pct: 0.40                 # 第一阶段不强制实现
  max_total_positions: 10
  price_deviation_limit: 0.05          # 主板默认值;查表覆盖见 §1.4.2
  volume_lot_size: 100
  max_total_position_pct: 0.70         # P0-7 新增
  max_single_instruction_amount: 50000 # P0-7 新增
  max_daily_new_instructions: 5        # P0-7 新增

stop_loss:
  single_stock_pct: 0.08
  portfolio_daily_pct: 0.05
  trailing_stop_pct: 0.10

circuit_breaker:
  daily_loss_limit_pct: 0.05
  consecutive_loss_count: 3
  cooldown_minutes: 60
  halt_priority_order: ["daily_loss", "consecutive_loss"]  # P0-7 新增
  apply_to_sell_orders: false                              # P0-7 新增

universe:                                                  # P0-7 新增 section
  allowed_boards: ["sh_main", "sz_main", "chuangye", "etf"]
  forbidden_st: true
  forbid_buy_at_limit_up: true
  forbid_sell_at_limit_down: true
  price_limit_pct_by_board:
    sh_main: 0.10
    sz_main: 0.10
    chuangye: 0.20
    etf: 0.10
```

### 1.2 DailyTradingState 与 RiskEngine 签名扩展

#### 1.2.1 DailyTradingState(纯数据类)

```python
# backend/risk/daily_state.py(实施期新增,纯 Python,无 IO)

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class DailyTradingState:
    """RiskEngine 14-check 中 check 10/12/13/14 需要的当日动态状态。

    由 InstructionPlanBuilder 在调用 validate_order 前从 MockBroker /
    decision_ledger / 行情源装配,RiskEngine 不发起任何 IO。
    继承 P0-1 §2 红线 8(backend/risk/ 严禁 import backend.llm/agents/mirofish);
    backend.data 也不在依赖白名单 — 故由调用方装配,纯传入。
    """

    today_new_instruction_count: int
    """当日已 dispatch 的 BUY+SELL InstructionPlan 数量
    (HOLD 不计;DRAFT/REJECTED 也不计 — 已派发才占用配额)。"""

    today_portfolio_pnl_pct: float
    """当日组合相对开盘 NAV 的累计盈亏比例
    ((当前 NAV - 当日开盘 NAV) / 当日开盘 NAV);负数表示亏损。"""

    last_3_trade_pnls: tuple[float, ...]
    """最近 3 笔已 FILLED 交易的 PnL 序列(从 MockBroker.trades 取);
    长度可 < 3(初始期);check 14 当 len < 3 时直接 PASS。"""

    current_price: Optional[float]
    """指令股票的当前实时报价(用于 check 12 涨跌停判断);
    None 时 check 12 fail-closed REJECTED(『行情缺失,无法判断涨跌停』)。"""

    is_in_halt_cooldown: bool
    """当前是否处于熔断冷却期(circuit_breaker_state.is_in_halt
    且 now < halt_until)。"""

    halt_until: Optional[datetime]
    """熔断解除时刻;is_in_halt_cooldown=False 时 None。"""
```

#### 1.2.2 RiskEngine 14-check 签名

```python
# backend/risk/engine.py(实施期重写)

class RiskEngine:
    """硬编码风控引擎,扩展为 14-check。所有 check 纯 Python + 无 IO。"""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def validate_order(
        self,
        order: Order,
        account: AccountInfo,
        positions: tuple[Position, ...],
        prev_close: float | None = None,
        now: dt.datetime | None = None,
        # === P0-7 新增参数 ===
        daily_state: DailyTradingState | None = None,
        stock_meta: "StockMetadata" | None = None,  # 见 §1.4.1
    ) -> ValidationResult:
        """运行 14-check 链。第一个失败短路返回。

        当 daily_state / stock_meta 为 None 时(向后兼容),check 8-14 PASS;
        实施期 InstructionPlanBuilder 必须传入两者,绕过即视为红线违规
        (lint rule 守门)。
        """
        checks = [
            # 1-7 = P0-3 已锁原 7-check
            self._check_code_validity,
            self._check_price_reasonability,
            self._check_volume_validity,
            self._check_fund_sufficiency,
            self._check_position_limit,
            self._check_total_position_limit,
            self._check_trading_time,
            # 8-14 = P0-7 新增
            self._check_total_position_pct,
            self._check_single_instruction_amount,
            self._check_daily_new_instruction_count,
            self._check_universe_whitelist,
            self._check_limit_up_down_block,
            self._check_daily_loss_halt,
            self._check_consecutive_loss_halt,
        ]
        for check in checks:
            result = check(order, account, positions, prev_close, now,
                           daily_state, stock_meta)
            if not result.passed:
                log.warning("order_rejected", rule=result.rule_name,
                            code=order.code, message=result.message)
                return result
        log.info("order_validated", code=order.code, direction=order.direction)
        return ValidationResult(passed=True)
```

### 1.3 7 个新 check 的实现细节

#### 1.3.1 check 8: total_position_pct(总仓位市值上限)

```python
def _check_total_position_pct(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 8: 总仓位市值占比 ≤ max_total_position_pct(0.70)。"""
    if order.direction == OrderDirection.SELL:
        return ValidationResult(passed=True)  # 减仓只可能让总仓位下降
    if account.total_assets <= 0:
        return ValidationResult(passed=False, rule_name="total_position_pct",
                                message="Cannot trade with zero total assets")

    # 用 order.price 重估 — 与 check 5 保持一致 exposure 算法
    existing_value = sum(p.volume * order.price for p in positions
                         if p.code == order.code)
    other_value = sum(p.volume * p.market_value / max(p.volume, 1)
                      for p in positions if p.code != order.code)
    new_value = existing_value + (order.volume * order.price)
    total_after = new_value + other_value
    ratio = total_after / account.total_assets
    limit = self._config.position_limits.max_total_position_pct

    if ratio > limit:
        return ValidationResult(
            passed=False, rule_name="total_position_pct",
            message=(f"Total position would be {ratio:.1%} "
                     f"of portfolio (limit: {limit:.0%})"),
        )
    return ValidationResult(passed=True)
```

#### 1.3.2 check 9: single_instruction_amount(单次指令金额上限)

```python
def _check_single_instruction_amount(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 9: 单次指令金额 ≤ max_single_instruction_amount(50,000)。"""
    amount = order.price * order.volume
    limit = self._config.position_limits.max_single_instruction_amount
    if amount > limit:
        return ValidationResult(
            passed=False, rule_name="single_instruction_amount",
            message=(f"Instruction amount {amount:.2f} exceeds "
                     f"limit {limit:.2f}"),
        )
    return ValidationResult(passed=True)
```

#### 1.3.3 check 10: daily_new_instruction_count(每日指令数)

```python
def _check_daily_new_instruction_count(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 10: 当日新指令数 ≤ max_daily_new_instructions(5)。

    新指令 = BUY+SELL 已 dispatch 的当日 InstructionPlan;HOLD 不计。
    本 check 由 InstructionPlanBuilder 在每个 BUY/SELL 候选指令派发前调用,
    调用时 today_new_instruction_count 应反映"已确认派发"的指令数,
    本指令尚未计入(否则恒会 reject 第 5 + 1 单)。
    """
    if daily_state is None:
        return ValidationResult(passed=True)
    limit = self._config.position_limits.max_daily_new_instructions
    if daily_state.today_new_instruction_count >= limit:
        return ValidationResult(
            passed=False, rule_name="daily_new_instruction_count",
            message=(f"Daily new instructions {daily_state.today_new_instruction_count}"
                     f" already at limit {limit}"),
        )
    return ValidationResult(passed=True)
```

#### 1.3.4 check 11: universe_whitelist(板块白名单)

```python
def _check_universe_whitelist(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 11: 标的板块在白名单 + 非 ST。"""
    if stock_meta is None:
        return ValidationResult(
            passed=False, rule_name="universe_whitelist",
            message=f"stock_meta unavailable for {order.code}",
        )

    universe = self._config.universe
    if stock_meta.board not in universe.allowed_boards:
        return ValidationResult(
            passed=False, rule_name="universe_whitelist",
            message=(f"Board '{stock_meta.board}' not in allowed_boards "
                     f"{universe.allowed_boards}"),
        )

    if universe.forbidden_st and stock_meta.is_st:
        return ValidationResult(
            passed=False, rule_name="universe_whitelist",
            message=f"ST stock {order.code} ({stock_meta.name}) forbidden",
        )

    return ValidationResult(passed=True)
```

#### 1.3.5 check 12: limit_up_down_block(涨停买入 / 跌停卖出禁令)

```python
def _check_limit_up_down_block(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 12: 已涨停时禁 BUY,已跌停时禁 SELL。"""
    universe = self._config.universe
    if not (universe.forbid_buy_at_limit_up or universe.forbid_sell_at_limit_down):
        return ValidationResult(passed=True)

    if daily_state is None or daily_state.current_price is None:
        return ValidationResult(
            passed=False, rule_name="limit_up_down_block",
            message="current_price unavailable; cannot evaluate limit-up/down",
        )

    if prev_close is None or prev_close <= 0:
        # 新股次新等无 prev_close — 第一阶段保守拒绝
        return ValidationResult(
            passed=False, rule_name="limit_up_down_block",
            message="prev_close unavailable; cannot evaluate limit-up/down",
        )

    if stock_meta is None:
        return ValidationResult(
            passed=False, rule_name="limit_up_down_block",
            message="stock_meta unavailable; cannot get board limit_pct",
        )

    limit_pct = universe.price_limit_pct_by_board.get(stock_meta.board, 0.10)
    upper_limit = prev_close * (1.0 + limit_pct)
    lower_limit = prev_close * (1.0 - limit_pct)

    if (order.direction == OrderDirection.BUY
            and universe.forbid_buy_at_limit_up
            and daily_state.current_price >= upper_limit - 0.001):
        return ValidationResult(
            passed=False, rule_name="limit_up_down_block",
            message=(f"BUY at limit-up forbidden: "
                     f"current {daily_state.current_price} >= upper {upper_limit:.2f}"),
        )

    if (order.direction == OrderDirection.SELL
            and universe.forbid_sell_at_limit_down
            and daily_state.current_price <= lower_limit + 0.001):
        return ValidationResult(
            passed=False, rule_name="limit_up_down_block",
            message=(f"SELL at limit-down forbidden: "
                     f"current {daily_state.current_price} <= lower {lower_limit:.2f}"),
        )

    return ValidationResult(passed=True)
```

#### 1.3.6 check 13: daily_loss_halt(日内组合跌幅熔断)

```python
def _check_daily_loss_halt(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 13: 日内组合跌幅 ≤ -daily_loss_limit_pct(-5%)。

    BUY 类指令:跌幅触发即拒绝(熔断期间禁 BUY 加仓)。
    SELL 类指令:apply_to_sell_orders=false(默认)允许 SELL 减仓退出
    (避免锁仓陷阱)。
    """
    cb = self._config.circuit_breaker
    if daily_state is None:
        return ValidationResult(passed=True)

    if order.direction == OrderDirection.SELL and not cb.apply_to_sell_orders:
        return ValidationResult(passed=True)

    threshold = -cb.daily_loss_limit_pct
    if daily_state.today_portfolio_pnl_pct < threshold:
        return ValidationResult(
            passed=False, rule_name="daily_loss_halt",
            message=(f"Daily loss {daily_state.today_portfolio_pnl_pct:.2%} "
                     f"breached halt threshold {threshold:.0%}"),
        )

    if daily_state.is_in_halt_cooldown:
        until = daily_state.halt_until.isoformat() if daily_state.halt_until else "unknown"
        return ValidationResult(
            passed=False, rule_name="daily_loss_halt",
            message=f"In halt cooldown until {until}",
        )

    return ValidationResult(passed=True)
```

#### 1.3.7 check 14: consecutive_loss_halt(连亏熔断)

```python
def _check_consecutive_loss_halt(
    self, order, account, positions, prev_close, now, daily_state, stock_meta,
) -> ValidationResult:
    """Check 14: 最近 N 笔交易连亏 → 熔断
    (默认 N=consecutive_loss_count=3)。

    BUY/SELL 同样适用(连亏与方向无关,与 daily_loss_halt 不同)。
    新启动期 last_3_trade_pnls 长度 < N 时 PASS。
    """
    cb = self._config.circuit_breaker
    if daily_state is None:
        return ValidationResult(passed=True)
    n = cb.consecutive_loss_count
    pnls = daily_state.last_3_trade_pnls
    if len(pnls) < n:
        return ValidationResult(passed=True)
    recent = pnls[-n:]
    if all(pnl < 0 for pnl in recent):
        return ValidationResult(
            passed=False, rule_name="consecutive_loss_halt",
            message=(f"Last {n} trades all losing: "
                     f"{[f'{p:.2f}' for p in recent]}"),
        )
    return ValidationResult(passed=True)
```

### 1.4 universe 识别独立模块

#### 1.4.1 stock_metadata.py(纯模块)

```python
# backend/data/stock_metadata.py(实施期新增)

from dataclasses import dataclass
from enum import StrEnum


class Board(StrEnum):
    SH_MAIN = "sh_main"          # 600/601/603/605
    SZ_MAIN = "sz_main"          # 000/002
    CHUANGYE = "chuangye"        # 300/301
    KCHUANG = "kchuang"          # 688
    BEIJIAO = "beijiao"          # 83/87/88/92
    ETF = "etf"                  # 50/51/52/56/58/15/16/18(需双轨验证)
    CONVERTIBLE_BOND = "convertible_bond"  # 11x/12x
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StockMetadata:
    code: str
    name: str
    board: Board
    is_st: bool
    instrument_type: str  # "stock" | "etf" | "bond" | "unknown",来自行情源


def classify_board(code: str, name: str = "",
                   instrument_type: str = "stock") -> Board:
    """从 6 位代码 + 行情源元数据识别板块。

    优先级:
    1. instrument_type='etf' → Board.ETF(代码段优先 + 元数据双轨)
    2. instrument_type='bond' → Board.CONVERTIBLE_BOND
    3. 代码前缀识别股票板块
    """
    if instrument_type == "etf":
        return Board.ETF
    if instrument_type == "bond":
        return Board.CONVERTIBLE_BOND
    if code.startswith(("600", "601", "603", "605")):
        return Board.SH_MAIN
    if code.startswith(("000", "002")):
        return Board.SZ_MAIN
    if code.startswith(("300", "301")):
        return Board.CHUANGYE
    if code.startswith("688"):
        return Board.KCHUANG
    if code.startswith(("83", "87", "88", "92")):
        return Board.BEIJIAO
    if code.startswith(("110", "113", "118", "123", "127", "128")):
        return Board.CONVERTIBLE_BOND
    if code.startswith(("50", "51", "52", "56", "58", "15", "16", "18")):
        return Board.ETF
    return Board.UNKNOWN


def is_st(name: str) -> bool:
    """ST/*ST/S*ST 通过股票名称前缀识别(简单可靠)。"""
    if not name:
        return False
    upper = name.upper().strip()
    return (upper.startswith("ST")
            or upper.startswith("*ST")
            or upper.startswith("S*ST")
            or upper.startswith("S ST"))


def get_price_limit_pct(board: Board, config: UniverseConfig) -> float:
    """从配置查板块涨跌停幅度;未知板块默认 10%。"""
    return config.price_limit_pct_by_board.get(str(board), 0.10)
```

#### 1.4.2 price_reasonability(check 2)按板块查表

P0-3 §2 红线 6 中 check 2 当前用全局 `price_deviation_limit=0.05`。第一阶段 universe 含创业板(±20%),保留 5% 全局会让所有创业板限价被无理拒绝。修正:

```python
def _check_price_reasonability(self, order, ..., stock_meta):
    """Check 2: 限价偏离 prev_close 在板块涨跌幅内(主板 ±10% / 创业板 ±20%)。"""
    if order.order_type == OrderType.MARKET:
        return ValidationResult(passed=True)
    if prev_close is None or prev_close <= 0:
        return ValidationResult(passed=True)

    if stock_meta is not None:
        limit = self._config.universe.price_limit_pct_by_board.get(
            stock_meta.board, self._config.position_limits.price_deviation_limit
        )
    else:
        limit = self._config.position_limits.price_deviation_limit

    deviation = abs(order.price - prev_close) / prev_close
    if deviation > limit:
        return ValidationResult(
            passed=False, rule_name="price_reasonability",
            message=(f"Price {order.price} deviates {deviation:.1%} from "
                     f"prev_close {prev_close} (board limit: ±{limit:.0%})"),
        )
    return ValidationResult(passed=True)
```

`PositionLimitsConfig.price_deviation_limit=0.05` 仅在 stock_meta=None 时作 fallback;实施期 InstructionPlanBuilder 必须传 stock_meta,fallback 不应被命中。

### 1.5 InstructionPlanBuilder 装配 daily_state + stock_meta

```python
# backend/services/instruction_plan_builder.py(实施期新增)

async def build_instruction_plan(
    fund_manager_record: FundManagerRecord,
    *,
    mock_broker: MockBroker,
    instruction_plan_repo: InstructionPlanRepo,
    circuit_breaker_repo: CircuitBreakerRepo,
    quote_provider: QuoteProvider,        # 行情源(adata/akshare)
    stock_meta_provider: StockMetaProvider,
    risk_engine: RiskEngine,
    config: RiskConfig,
    now: datetime,
) -> InstructionPlan:
    """从 FundManagerRecord 构建 InstructionPlan。

    数据流(P0-7 锁定):
    1. 早返:熔断状态查询 → 已熔断且(BUY 或 apply_to_sell_orders)→ 直接降级 HOLD
    2. 装配 stock_meta(board / is_st / current_price / prev_close)
    3. 装配 DailyTradingState
       - today_new_instruction_count = repo.count_today_dispatched()
       - today_portfolio_pnl_pct = (current_nav - day_open_nav) / day_open_nav
       - last_3_trade_pnls = mock_broker.last_n_trade_pnls(n=3)
       - is_in_halt_cooldown / halt_until 来自 circuit_breaker_repo
    4. 派生 Order(P0-3 derive_order_from_plan)
    5. 调用 risk_engine.validate_order(order, account, positions, prev_close,
                                       now, daily_state, stock_meta)
    6. 14 条 RiskCheckSummary 全部填入 InstructionPlan.risk_summary
       (失败 fail-fast 短路时,后续 check 标 passed=None / message='not_evaluated';
        长度仍恒为 14)
    7. 任一 check failed → InstructionPlan.status = REJECTED
       全部 PASS → status = VALIDATED → ModeRouter
    """
```

#### 1.5.1 fail-fast 与 risk_summary 长度 14 的兼容

P0-3 §1.5 要求 `risk_summary` 长度恒为 7,且每条都是真实评估结果。本决策扩到 14 后,fail-fast 短路会让后续 check 未执行 — 但 `RiskCheckSummary` 仍需占位。约定:

- 已执行的 check:`passed=True/False`,`actual` 填值,`message` 填评估结论
- 未执行的 check(短路后):`passed=None`(`bool | None`),`actual=None`,`message="short_circuited_by_<failed_rule>"`

`RiskCheckSummary.passed` 类型要从 `bool` 放宽到 `bool | None`(P0-3 amendment 同步更新)。

### 1.6 LLM 边界:全锁 + RiskParameterProposal

#### 1.6.1 RiskConfig runtime 不可改

继承 P0-1 §2 红线 8:**runtime 任何路径不存在 RiskConfig 写入端口**。具体:

- `backend/api/risk/*.py` 仅暴露 `GET` 端点(读取当前阈值供前端展示);**禁止 POST/PUT/PATCH** 任何 RiskConfig 字段
- `RiskConfig` / `PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig` 全是 `model_config = ConfigDict(frozen=True)` 的 Pydantic v2 模型
- LLM/Agent 永不持有 RiskConfig 写引用;`backend/agents/` / `backend/llm/` / `backend/mirofish/` 严禁 `from backend.risk import` 或 `from backend.broker.models import RiskConfig` 后修改字段
- 阈值修改流程 = git diff `config/risk.yaml` + amendment 文档(P0-3-amendment / P0-7-amendment 命名)+ 进程重启

#### 1.6.2 RiskParameterProposal collection(自进化提议通道)

允许 simulation_auto 复盘 Agent 写入"建议变更条目"作为只读历史,但不写入 RiskConfig:

```python
# backend/services/risk_parameter_proposal.py(实施期新增)

from pydantic import BaseModel, ConfigDict, Field


class RiskParameterProposal(BaseModel):
    """Agent 复盘时提出的 RiskConfig 建议变更条目(只读 ledger)。

    Agent 写入此 collection 不影响 runtime;由项目所有者每周人工 review。
    接受后走 git edit risk.yaml + amendment 文档 + 进程重启。
    """
    model_config = ConfigDict(frozen=True)

    proposal_id: str = Field(pattern=r"^RPP-\d{8}-\d{3}$")
    """格式: RPP-{YYYYMMDD}-{seq}。"""

    proposed_at: datetime
    proposing_agent: str  # e.g. "fund_manager" / "risk_reviewer"

    # === 建议变更内容 ===
    parameter_path: str
    """字段路径,e.g. "position_limits.max_single_stock_pct"。"""

    current_value: float | int | bool | str | tuple[str, ...]
    proposed_value: float | int | bool | str | tuple[str, ...]

    rationale: str = Field(max_length=1000)
    """Agent 给出的理由(LLM 自由文本,但不影响 runtime)。"""

    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    """支撑该提议的复盘记录 / equity_curve 片段 / 拦截统计 ID。"""

    # === 人工 review 状态 ===
    review_status: str = Field(default="pending")
    """pending / accepted / rejected / superseded。"""

    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    decision_doc: str | None = None
    """接受后产出的 amendment 文档相对路径,e.g.
    "docs/decisions/P0-7-amendment-2026-06-01-loosen-single-stock-pct.md"。"""
```

#### 1.6.3 提议生命周期与不可变性

- Agent 写入后 review_status='pending';前端 RiskProposalCenter 提供 review 视图(只读 + 三选一裁定)
- `accepted` 后必须由人工同步产出 amendment 文档,decision_doc 字段填路径,YAML 修改 + 重启;无 amendment 不算正式生效
- `rejected` 不删除,仅作历史留痕
- `superseded`:多个相关 proposal 中保留一条 accepted、其余标 superseded

#### 1.6.4 lint 规则(实施期 ci-lint 强制)

```python
# scripts/lint_risk_immutability.py(实施期新增)

# 1. backend/api/risk/*.py 不可包含 POST/PUT/PATCH 装饰器
# 2. backend/agents/ 与 backend/llm/ 不可 import RiskConfig
# 3. backend/mirofish/ 不可 import RiskConfig
# 4. RiskConfig / PositionLimitsConfig / CircuitBreakerConfig / UniverseConfig 必须 frozen=True
# 5. WINDOW_TRADING_DAYS / max_single_stock_pct 等关键常量
#    在 backend/risk/ 之外不可被 setattr / __setattr__
```

### 1.7 数据流总览

```
FundManagerRecord(LLM 多 Agent 辩论收尾;parse_ok=True)
        │
        ▼
InstructionPlanBuilder
  ├── 早返:CircuitBreakerState.is_in_halt + (BUY 或 apply_to_sell)→ 降级 HOLD
  ├── 装配 stock_meta(stock_metadata.classify_board + is_st + price_limit_pct)
  ├── 装配 DailyTradingState
  │     ├── today_new_instruction_count(repo query)
  │     ├── today_portfolio_pnl_pct(MockBroker open_nav vs current_nav)
  │     ├── last_3_trade_pnls(MockBroker.trades)
  │     ├── current_price(quote_provider)
  │     └── is_in_halt_cooldown / halt_until(CircuitBreakerRepo)
  ├── 派生 Order(P0-3 derive_order_from_plan)
  └── RiskEngine.validate_order(14-check)
       ├── 1-7 = P0-3 已锁
       ├── 8 = total_position_pct
       ├── 9 = single_instruction_amount
       ├── 10 = daily_new_instruction_count
       ├── 11 = universe_whitelist
       ├── 12 = limit_up_down_block
       ├── 13 = daily_loss_halt
       └── 14 = consecutive_loss_halt
        │
        ▼
14 条 RiskCheckSummary(passed=True/False/None) 写入 InstructionPlan.risk_summary
        │
        ├── 全 PASS → InstructionPlan.status=VALIDATED → ModeRouter
        │           feishu_off → SimulationExecutor → MockBroker
        │           feishu_on → FeishuMessenger → 用户 → ExecutionReportParser → MockBroker
        └── 任一 FAIL → status=REJECTED
                       ├── check 13/14 失败 → 同步写入 CircuitBreakerState.is_in_halt=true
                       └── halt_until = now + cooldown_minutes
```

熔断触发后,InstructionPlanBuilder 在下一次构建前的"早返"阶段直接拒绝出 BUY 类 InstructionPlan(降级 HOLD,写 ledger);冷却结束后才恢复。

### 1.8 与 P0-6 验收指标的衔接

| P0-6 指标 | P0-7 阈值的影响 |
|----------|----------------|
| 指令完整率 ≥ 95% | check 8-14 拒绝率上限约 5%;选保守仓位组(单股 15%)+ 严格 universe 后,实际 REJECTED 率应 ≤ 3%(主因来自单笔金额 / 涨跌停),留 2% 余量给 check 1-7 |
| 最大回撤 ≤ 8% | daily_loss_halt -5% + consecutive_loss_halt 提供前置保护;留 3% 缓冲应对盘后跌停 + 跨日 gap |
| PnL ≥ 0 | 单股 15% + 总仓 70% 强制资金分散;单笔 5 万天然限制止损损失上限 |
| 沪深 300 超额 ≥ 0 | universe 限主板 + 创业板 + ETF,保留 alpha 空间(创业板高 beta + ETF 工具) |
| 风控拦截率(观察) | 实施期需在 acceptance_observation 中按 14 条 rule_name 分别统计,不阻断切换但作诊断 |

## 2. 红线(立即生效)

1. **`RiskConfig` 任何字段在 runtime 不可改**:`backend/api/risk/*.py` 只允许 `GET` 端点;`backend/agents/` / `backend/llm/` / `backend/mirofish/` 严禁 `from backend.risk import` 或 `from backend.broker.models import RiskConfig`(继承 P0-1 §2 红线 8)。`RiskConfig` / `PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig` 必须 `model_config = ConfigDict(frozen=True)`;违规即红线。

2. **阈值修改流程 = 不可绕过的三步**:`git diff config/risk.yaml` + 同期产出 `P0-7-amendment-{date}-{原因}.md` + 进程重启。任何 runtime hot-reload / setattr / __setattr__ / monkey-patch 绕过即红线违规。

3. **保守仓位三连阈值锁定**:`max_single_stock_pct=0.15` / `max_total_position_pct=0.70` / `max_single_instruction_amount=50000`;调整任一阈值必须先走 `P0-7-amendment-{date}-{原因}.md`;实施期 lint rule 阻止三常量被覆写。

4. **中性日内熔断阈值锁定**:`max_daily_new_instructions=5` / `daily_loss_limit_pct=0.05` / `consecutive_loss_count=3` / `cooldown_minutes=60`;调整必须先走 amendment;阈值锁定与 P0-6 指令完整率 95% / 最大回撤 8% 配合。

5. **中性 universe 白名单锁定**:`allowed_boards=("sh_main","sz_main","chuangye","etf")`;新增/删除板块必须先走 amendment;**禁止 ST / 科创板(688) / 北交所(8x/92x) / 可转债(11x/12x)**;`forbidden_st=true` / `forbid_buy_at_limit_up=true` / `forbid_sell_at_limit_down=true` 全部锁定。

6. **熔断期间冻结 BUY 类 InstructionPlan**:`CircuitBreakerState.is_in_halt=true` 期间 InstructionPlanBuilder 在早返阶段直接降级 BUY 候选为 HOLD;SELL 类按 `apply_to_sell_orders=false` 默认放行(避免锁仓陷阱);改为 `apply_to_sell_orders=true` 必须先走 amendment。

7. **熔断与 P0-1 切换冻结 / P0-5 ticket 冻结独立并行**:三种买卖类路由冻结来源(切换中 / OPEN-EXPIRED ticket / 熔断冷却)在 ModeRouter 与 InstructionPlanBuilder 中独立判定,任一为真即冻结;不允许任意一个绕过其他冻结。

8. **RiskEngine 14-check 完整性**:`RiskEngine.validate_order` 必须依次执行 14 条 check,任何"短路时只填 7 条 RiskCheckSummary"或"实施期跳过 check 8-14"即红线违规;`InstructionPlan.risk_summary` 长度恒为 14(passed 字段类型放宽 `bool | None`,通过 P0-3 amendment 同步更新)。

9. **`backend/risk/` 严禁 import `backend.data`**:check 11/12 需要的 `stock_meta` / check 10/12/13/14 需要的 `daily_state` 必须由 InstructionPlanBuilder 装配后传入;RiskEngine 不发起任何 IO(继承 P0-1 §2 红线 8 RiskEngine 纯函数原则)。

10. **`backend/risk/` 严禁 import `backend.llm` / `backend.agents` / `backend.mirofish`**(继承 P0-1 §2 红线 8 不变);新增 `backend.broker.models` 等被动依赖必须保持单向(broker → risk OK,risk → broker 禁止)。

11. **LLM 严禁产出 RiskConfig 字段值或 RiskCheckSummary 结果**:LLM 可以解释为什么某仓位"看起来风险高",但**绝不**直接产出"建议设置 max_single_stock_pct=X" 进入 RiskConfig;Agent 复盘时仅可写入 `risk_parameter_proposals` collection(只读 ledger),不写入 RiskConfig。

12. **`risk_parameter_proposals` collection 是只读 ledger**:`accepted` 后必须由人工同步产出 amendment 文档,decision_doc 字段填路径,YAML 修改 + 重启才正式生效;系统不通过 proposal `accepted` 状态自动改 RiskConfig,实施期 lint rule 守门。

13. **`stock_meta` 与 `daily_state` 缺失即 fail-closed**:check 11(stock_meta=None)/ check 12(stock_meta=None 或 current_price=None 或 prev_close=None)直接 REJECTED;不允许"缺数据时通过"的乐观回退。

14. **熔断状态持久化必经 `circuit_breaker_repo`**:严禁直接 mutation `CircuitBreakerState` 或在内存中临时存熔断状态;`circuit_breaker_state` collection 是单文档(`_id="singleton"`),所有读写经 repo 接口(继承 P0-5 §2 红线 12 ReconciliationApplier 收口精神)。

15. **板块/ST 识别经 `backend/data/stock_metadata.py` 纯函数**:严禁在 `backend/risk/` 内重复实现板块/ST 识别逻辑;严禁绕过 `classify_board` 直接 hard-code 代码前缀于 RiskEngine 内部(单一真相源原则)。

16. **`PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig` / `RiskParameterProposal` 是 frozen Pydantic v2 模型**;就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 / P0-5 §2 红线 16 / P0-6 §2 红线 14 immutability 原则)。

17. **第一阶段排除项目**:`max_sector_pct` 字段保留但 RiskEngine 不实现 sector check(留 P1);市价单(`OrderType.MARKET`)绕过 check 2 已存在但仍在 P0-3 第一阶段排除范围;科创板 / 北交所 / 可转债 / ST 加入 universe 必须先走 amendment;`apply_to_sell_orders=true`(SELL 也熔断)必须先走 amendment。

## 3. 影响范围(实施期统一执行)

### 3.1 新模块

- `backend/risk/daily_state.py` — `DailyTradingState` dataclass
- `backend/data/stock_metadata.py` — `Board` enum + `classify_board` / `is_st` / `get_price_limit_pct` 纯函数
- `backend/services/circuit_breaker_repo.py` — `CircuitBreakerState` 持久化 repo + `circuit_breaker_state` collection
- `backend/services/risk_parameter_proposal.py` — `RiskParameterProposal` 模型 + repo + `risk_parameter_proposals` collection
- `backend/services/instruction_plan_builder.py` — 装配 daily_state + stock_meta + 调用 RiskEngine + 14-check 结果映射(P0-7 主要落地点)
- `scripts/lint_risk_immutability.py` — ci-lint 守门 RiskConfig immutability + import 隔离 + frozen=True

### 3.2 修改模块

- `backend/risk/engine.py` — `validate_order` 签名扩展 `daily_state` + `stock_meta`;新增 `_check_total_position_pct` / `_check_single_instruction_amount` / `_check_daily_new_instruction_count` / `_check_universe_whitelist` / `_check_limit_up_down_block` / `_check_daily_loss_halt` / `_check_consecutive_loss_halt`;`_check_price_reasonability` 改为按 stock_meta.board 查表(fallback 全局 `price_deviation_limit`)
- `backend/broker/models.py` — `PositionLimitsConfig` 加 3 字段 / `CircuitBreakerConfig` 加 2 字段 / 新增 `UniverseConfig` / `RiskConfig` 加 universe section / `ValidationResult` 不变
- `config/risk.yaml` — 阈值替换为 P0-7 锁定值 + 新增 universe section
- `backend/services/instruction_plan.py`(P0-3 实施期落地)— `RiskCheckSummary.passed` 类型从 `bool` → `bool | None`;risk_summary 长度从 7 → 14
- `backend/api/risk/*.py` — 移除任何 POST/PUT/PATCH 端点(若已存在);保留 GET 视图 + 新增 `GET /api/risk/proposals` 只读列表
- `backend/services/mode_router.py`(P0-1 实施期落地)— 路由前查询 `circuit_breaker_repo.is_in_halt()` 与现有切换冻结 / ticket 冻结合并(三冻结来源)

### 3.3 新 collection

- `circuit_breaker_state` — 单文档(`_id="singleton"`),记录当前熔断状态:`is_in_halt` / `halt_reason`(`daily_loss` / `consecutive_loss`)/ `triggered_at` / `halt_until` / `triggering_instruction_id`
- `risk_parameter_proposals` — Agent 写入的提议条目;索引 `(proposal_id)` 唯一 + `(review_status, proposed_at)`

### 3.4 新 API

- `GET /api/risk/config` — 返回当前 RiskConfig 完整 dump(只读)
- `GET /api/risk/proposals` — 列表 / 按 review_status 过滤
- `GET /api/risk/circuit-breaker` — 当前熔断状态(`is_in_halt` / `halt_reason` / `halt_until`)
- **不**新增 `POST /api/risk/*`:阈值修改严格走 git + amendment + 重启

### 3.5 新前端视图

- `frontend/src/views/RiskProposalCenter.vue` — Agent 提议列表 + 三选一裁定(accepted / rejected / superseded)+ amendment 路径填写
- `frontend/src/views/RiskConfigPanel.vue` 升级为只读 — 显示当前 14-check 阈值 + 当前熔断状态;修改提示"git edit + amendment + 重启"

### 3.6 派生 amendment

`docs/decisions/P0-3-amendment-2026-05-09-extend-risk-checks-from-7-to-14.md`(实施期同步产出):

- §1.5 / §2 红线 6 中 "risk_summary 必须包含恰好 7 条" → "恰好 14 条"
- `RiskCheckSummary.passed` 类型 `bool` → `bool | None`(短路时占位)
- `_check_*` 函数列表更新为 14 条,rule_name 集合扩展

### 3.7 测试覆盖

- `backend/risk/engine.py` 测试覆盖 ≥ 95%(沿用 risk 模块基线)— 14 条 check 每条最少 3 用例(PASS / FAIL / None 边界)
- `backend/data/stock_metadata.py` ≥ 95% — 8 个 Board enum 各最少 1 用例 + ST 命名 4 形态
- `backend/services/instruction_plan_builder.py` ≥ 90%(略高,因 14-check 装配复杂)
- `backend/services/circuit_breaker_repo.py` ≥ 90%
- `backend/services/risk_parameter_proposal.py` ≥ 80%
- 新增端到端测试 `tests/e2e/test_risk_redlines_p0_7.py`:覆盖"熔断触发 → BUY 全降级 HOLD" / "ST 股 reject" / "涨停 BUY reject" / "单股 15% 边界" / "单笔 5 万边界" / "每日 5 单边界" / "连亏 3 笔触发熔断"

### 3.8 静态扫描 lint rule

- 阻止 `backend/api/risk/` 出现 `@router.post` / `@router.put` / `@router.patch`
- 阻止 `backend/agents/` / `backend/llm/` / `backend/mirofish/` import `RiskConfig` / `PositionLimitsConfig` / `CircuitBreakerConfig` / `UniverseConfig`
- 阻止 RiskConfig 子模型 `frozen=True` 被改为 `frozen=False`
- 阻止 `backend/risk/` 出现 `import backend.data` / `import backend.llm` / `import backend.agents` / `import backend.mirofish`
- 阻止 `max_single_stock_pct` / `max_total_position_pct` / `max_single_instruction_amount` 等 P0-7 锁定常量被赋值改写

## 4. 决策依据

### 4.1 为什么仓位三连选「保守组合」

- **P0-6 §1.3.2 PnL ≥ 0 是硬门槛**,但同时 §1.3.1 最大回撤 ≤ 8% 也是硬门槛 — 后者比前者更难达标(单笔重仓股闪崩可一日吃掉 5%+),保守组合优先保护回撤上限
- **1000k 资金保守组合下,1000k × 15% = 150k / 50k 单笔 = 3 笔可达单股上限**,即 `simulation_auto` 收集到的 ≥ 3 个高确信度信号才能让一只股票被 fully-loaded;过度保守的"15%"反向强制资金分散
- 用户选择 Q1 答案 = 保守:"`PnL≥0 是 P0-6 硬门槛,过严会导致资金分散难以达标,过松会让单点失败放大`" — 本决策接受用户的"过松会让单点失败放大"判断,优先保护回撤
- 与 P0-6 §1.3.4 沪深 300 超额 ≥ 0 兼容:保守组合通过分散降低 beta,在沪深 300 大跌时避免被同步拖垮

### 4.2 为什么日内熔断选「中性组合」

- **每日 5 单**与 watchlist 典型规模(20-30 只标的,单股最多 BUY+SELL 各 1 单 = 40 候选)的 12.5% 命中率匹配,既不会让 LLM 噪声候选挤占配额,也不会因配额过低让真实高确信度信号被截断
- **-5% 日内熔断**与 `stop_loss.portfolio_daily_pct=0.05` 已有阈值对齐;保留 P0-6 最大回撤 8% 上限的 3% 缓冲应对盘后跌停 + 跨日 gap
- **连亏 3 次熔断**与 `circuit_breaker.consecutive_loss_count=3` 已有字段对齐;3 是经验上的"系统性误差"信号 — 单只股票连亏 1-2 次可能是市场噪声,连亏 3 次表明候选生成系统性偏差,需冷却 review
- **冷却 60 分钟**避免冷却结束 + 收盘集合竞价 = 立即重新触发;60 分钟覆盖盘中半个交易时段(09:30-15:00 共 4 小时)

### 4.3 为什么 universe 选「中性组合」

- **主板 + 创业板 + ETF** 覆盖 A 股最主流的 3500+ 只标的(主板约 3000 / 创业板约 1300 / ETF 约 1000 现存品种),对 watchlist 选股自由度足够
- **排除科创板**:涨跌幅 ±20% 与主板 ±10% 不一致,需 `_check_price_reasonability` 按板块查表(已实现于 §1.4.2),但实施期需要更多元数据稳定性;第一阶段不引入额外复杂度
- **排除北交所**:流动性显著低于主板/创业板,日均成交量级差 10-100 倍,飞书人工执行场景下用户可能因流动性不足无法在 valid_until 前成交,会拉低 P0-6 指令完整率
- **排除可转债**:T+0 + 涨跌幅 ±20%(老券)/ ±10%(新券,2022-08-01 后)/ 实物交割转股权 — 与股票交易语义差异过大,RiskEngine 14-check 不适配
- **排除 ST**:风险特征显著高于普通股票(±5% 涨跌幅 + 退市预警);P0-7 第一阶段保守化优先
- **禁涨停买入 + 禁跌停卖出**:防止"追涨杀跌"心智偏差,即使 LLM 给出涨停后买入候选 RiskEngine 也直接拦截

### 4.4 为什么 LLM 边界选「建议提议」

- **runtime 全锁是底线**,P0-1 §2 红线 8 已明确禁 LLM 改风控参数;P0-7 在此基础上把"runtime 不可改"扩展到 RiskConfig 的所有字段(包括新增的 universe / circuit_breaker 字段)
- **建议提议通道**比"软参数可调"更安全:RiskConfig 字段不分软硬(避免边界扩散红线漏洞),所有改动统一走 amendment 流程
- **自进化机制(P2-2)的入口**:Agent 复盘时识别"风控拦截率持续 >5%"或"指令完整率 <95%"等问题时,可写入 RiskParameterProposal 作为系统性改进建议,但不影响 runtime
- **每周 review 节奏**:与 P0-6 §1.1.3 的 9 周验收期同步;每周一次 review 不会拖慢迭代,也不会让风控配置漂移

### 4.5 为什么扩到 14-check 而非保持 7-check

- 用户决策清单 P0-7 明确列出了"单股仓位 / 总仓位 / 单次金额 / 每日指令数 / 亏损暂停线 / 标的板块限制 / LLM 不可改的硬参数集合"7 类红线 — 单股仓位与并发持仓数已被原 check 5/6 覆盖,**剩余 5 类无现成 check**
- 可选方案:把新 check 放在 InstructionPlanBuilder 的 PRE-RiskEngine gate 阶段(避免改 RiskEngine 接口)。**否决**:这会让"硬编码风控引擎"的语义被切成两半,与 P0-1 §2 红线 8"风控未贯穿到订单链路"的 audit 教训直接冲突
- 选择扩 RiskEngine 到 14-check:统一硬编码风控入口 + 统一 ValidationResult 报错语义 + 统一 risk_summary 写入(派生 P0-3 amendment 长度调整)

### 4.6 为什么 SELL 默认不熔断(`apply_to_sell_orders=false`)

- 熔断本质是"防止短期内系统性偏差被放大",对应 BUY 加仓动作("继续加注亏损方向")— 这是研究中典型的『anti-Martingale』风控
- SELL 是"减仓退出"动作,熔断期间允许 SELL 让用户在系统性偏差被识别后及时减仓,避免锁仓困死
- 同时熔断 SELL 会让"日内大跌 + 连亏触发熔断 + 用户希望止损"三重叠加时无法行动,反向放大风险
- 用户在 audit 文档 P0-7 §"是否允许追涨"边界要求明确暂停线行为 — 本决策阐释为"暂停 BUY 加仓但不暂停 SELL 减仓",符合 audit 精神

### 4.7 为什么 stock_meta 缺失即 fail-closed(check 11/12)

- 继承 P0-4 §1.2 "严格正则不通过 = AMBIGUOUS,绝不更新 MockBroker" 的 fail-closed 精神
- check 12 涨跌停判断必须用 prev_close + current_price + board limit_pct 三者齐备,缺一即无法做精确判断
- 第一阶段只接受"系统能精确判定"的指令派出;模糊期允许通过会让 LLM 噪声指令绕过涨跌停红线

### 4.8 为什么 risk_parameter_proposals 不允许自动改 RiskConfig

- 自进化机制(P2-2)讨论尚未锁定;现阶段允许"自动接受 → 自动改 RiskConfig"等于隐式锁定 P2-2 走向,违背决策对齐期"先锁定再实施"原则
- amendment 文档是不可绕过的 fail-closed 守门:任何 RiskConfig 改动必须有书面理由 + 项目所有者签字 — proposal 自动接受会绕过这层守门

## 5. 后续动作

实施期开工前(全部 P0 锁定后)的 checklist:

### 5.1 模型与配置(高优先级)

- [ ] `backend/broker/models.py` 中扩展 `PositionLimitsConfig` / `CircuitBreakerConfig`、新增 `UniverseConfig` 子模型,顶层 `RiskConfig` 引入 `universe`
- [ ] `config/risk.yaml` 替换为 P0-7 锁定阈值组(单股 15% / 总仓 70% / 单次 5 万 / 每日 5 单 / 日亏 -5% / 连亏 3 / universe 中性 / 涨跌停禁令 true)
- [ ] 新增 `backend/risk/daily_state.py`(`DailyTradingState` dataclass)
- [ ] 新增 `backend/data/stock_metadata.py`(`Board` enum + `classify_board` / `is_st` / `get_price_limit_pct`)

### 5.2 RiskEngine 14-check(中优先级)

- [ ] `backend/risk/engine.py` 中 `validate_order` 签名扩展 `daily_state` + `stock_meta`(向后兼容 None)
- [ ] 新增 7 个 check 函数(`_check_total_position_pct` 等),按 §1.3 实现
- [ ] `_check_price_reasonability` 改按 stock_meta.board 查 `price_limit_pct_by_board`(fallback `price_deviation_limit`)
- [ ] 单测覆盖每条 check ≥ 3 用例(PASS / FAIL / None 边界)

### 5.3 InstructionPlanBuilder 装配(中优先级)

- [ ] 新增 `backend/services/instruction_plan_builder.py`(P0-3/P0-7 共同落地点)
- [ ] 装配 daily_state(从 MockBroker / repo / quote_provider)+ stock_meta(从 metadata_provider)
- [ ] 调用 RiskEngine.validate_order(14-check) + 把 14 条 RiskCheckSummary 写入 InstructionPlan.risk_summary
- [ ] fail-fast 短路时 `passed=None`/`message='short_circuited_by_<failed_rule>'`

### 5.4 熔断状态 + 自进化提议(中优先级)

- [ ] 新增 `backend/services/circuit_breaker_repo.py` + `circuit_breaker_state` collection 单文档读写
- [ ] InstructionPlanBuilder 早返查询 `is_in_halt` + (BUY 或 apply_to_sell)→ 降级 HOLD
- [ ] check 13/14 失败时同步写入 `CircuitBreakerState.is_in_halt=true` + `halt_until=now + cooldown_minutes`
- [ ] 新增 `backend/services/risk_parameter_proposal.py` + `risk_parameter_proposals` collection
- [ ] 新增 `GET /api/risk/proposals` 列表 + `GET /api/risk/circuit-breaker` 状态查询

### 5.5 派生 P0-3 amendment(高优先级,与本决策同期产出)

- [ ] 新建 `docs/decisions/P0-3-amendment-2026-05-09-extend-risk-checks-from-7-to-14.md`
- [ ] amendment 内容:`risk_summary` 长度 7 → 14;`RiskCheckSummary.passed` 类型 `bool` → `bool | None`;`_check_*` 函数列表更新
- [ ] 在 P0-3 决策文档顶部加 `> 已被 amendment-2026-05-09-extend-risk-checks 修订` 提示

### 5.6 lint 与 frontend(低优先级)

- [ ] `scripts/lint_risk_immutability.py` 新建 + ci 集成
- [ ] `frontend/src/views/RiskConfigPanel.vue` 升级为只读视图(展示 14-check 阈值 + 熔断状态)
- [ ] `frontend/src/views/RiskProposalCenter.vue` 新建(proposal 列表 + 三选一裁定 + amendment 路径填写)

### 5.7 文档同步(本次锁定时即可完成)

- [x] 写入 `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(本文)
- [ ] 同步 `CLAUDE.md` §1.3 进度 + §2.1 P0-7 行 + §3.1 风险红线段落 + §3.4 操作速查
- [ ] 同步 `MEMORY.md` 索引(项目记忆 + 自记忆文件)
- [ ] 写下 P0-8 handoff prompt(继 commit 后)

---

> 本决策一旦定稿不就地修改;阈值松紧调整必须新建 `P0-7-amendment-{date}-{原因}.md` 并在本文顶部加 `> 已被 amendment-XXX 修订` 提示。
