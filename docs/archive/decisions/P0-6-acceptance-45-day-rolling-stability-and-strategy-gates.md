# P0-6 — simulation_auto 验收标准(45 交易日滚动窗口 + 稳定性硬门槛 + 策略硬门槛 + reconciliation 冻结暂停)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-6 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §1.2 / §3.1 / §6 / §11 / §15 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-6 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.3 切换 = 账户生命周期事件 + §1.5 路由规则 + §1.6 多 Agent 辩论是 InstructionPlan 唯一通路)+ `docs/decisions/P0-2-feishu-self-built-app-with-longconn-and-webhook-fallback.md`(尤其 §2 红线 2 备用 webhook 仅告警)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.2 instruction_id 格式 + §1.5 7-check 摘要 + §2 红线 8 parse_ok=False 强制降级 HOLD)+ `docs/decisions/P0-4-execution-report-parser-strict-regex-and-fail-closed-state-machine.md`(尤其 §1.2 五种回报正则 + §1.6 16:00 cutoff)+ `docs/decisions/P0-5-daily-reconciliation-fail-closed-tickets.md`(尤其 §1.6 fail-closed 冻结机制 + §1.4 偏差阈值 + §1.7 公司行动)|
| 替代       | — |

## 决策摘要

QuantMind `simulation_auto` 模式的"实战能力验收"采用 **45 交易日滚动窗口 + 稳定性硬门槛 + 策略硬门槛 + reconciliation 冻结暂停 + P0 系统级中断重置** 架构:

1. **验收周期 = 45 个交易日滚动窗口**(约 2 个月,9 周)。沿用 P0-4 §1.6 / P0-5 §1.2 的 Asia/Shanghai 交易日历语义,扣除节假日。任何时刻"是否允许打开 `FEISHU_INTERACTIVE_ENABLED=true`"由"最近 45 个交易日"窗口里的指标合规判断,不需要"一次性试运行"。

2. **稳定性硬门槛**(系统可信度,5 项必达):
   - 指令完整率 ≥ 95%(InstructionPlan.status 终态分布)
   - 回报解析准确率 ≥ 99%(P0-4 严格正则下 parse_ok=True 占比)
   - 数据延迟与缺失率 ≤ 1%(行情/资讯快照覆盖度)
   - LLM 超时率 ≤ 5%(超时 + 失败的 Agent step / 总 step)
   - 信号生成成功率 ≥ 95%(`FundManagerRecord.parse_ok=True` 占比;低于此说明多 Agent 辩论本身不稳)

3. **策略硬门槛**(资金安全,3 项必达):
   - 最大回撤 ≤ 8%(`(max(equity_curve) - min(equity_curve_after_max)) / max(equity_curve)`)
   - 验收期累计 PnL ≥ 0(扣除佣金 / 印花税 / 滑点之后)
   - 验收期相对沪深 300 累计基准超额收益 ≥ 0(独立于市场 beta 的最低证据)

4. **观察指标**(诊断辅助,不阻断切换 — 但需在每日验收报告中持续展示):
   - 风控拦截率(过高 = LLM 仓位计算与 RiskConfig 失配)
   - 模拟订单成交率(部分成交比例)
   - 胜率与盈亏比
   - 换手率
   - 指令到执行延迟
   - 单笔指令平均成本

5. **基准 = 沪深 300**(`000300.SH`)。第一阶段固定单一基准,自定义 watchlist 加权基准 / 多基准双轨追踪都属 P1 / amendment 范围。

6. **失败语义 = 滚动窗口 + 多门槛同时达标 + 不重启**:任何硬门槛在 45 日窗口内未同时达标 → 不允许切换 `feishu_on`,但**不**重置倒计时;系统继续跑,达标后窗口右端右移到达标日,即可切换。**没有"一次失败就重头来"的惩罚**。

7. **P0 系统级中断 = 倒计时重置**:行情连续断流 > 30min / 全部 LLM 不可用 > 1h / MockBroker 数据损坏 / 状态机非法迁移触发 → 当日及之前累计天数全部作废,从下一交易日起重新累计 45 日。

8. **reconciliation 冻结期间 = 暂停而非重置**:`reconciliation_tickets` 中存在 OPEN / EXPIRED ticket(P0-5 §1.6)→ 当日不计入"45 日有效观察窗口";已累计天数保留;ticket 解决后窗口从下一交易日继续延伸。

9. **每日验收报告**(`backend/services/acceptance_report.py`,实施期):每个交易日 16:00 之后(对齐 P0-4 §1.6 / P0-5 §1.2 cutoff)系统自动生成 `acceptance_reports` collection 一条记录:当日各硬门槛即时值 + 当日是否计入窗口 + 滚动 45 日累计窗口指标 + 距离切换 feishu_on 的差距。前端 AcceptanceCenter 提供查询(只读,不接收用户输入)。

10. **LLM 完全不参与验收路径**:`backend/services/acceptance*.py` 严禁 `import backend.llm.*`(继承 P0-4 / P0-5 LLM 隔离精神);所有指标计算走纯 Python 聚合 + MongoDB 查询。

## 1. 决策具体内容

### 1.1 验收周期与窗口语义

#### 1.1.1 滚动窗口定义

设当前 Asia/Shanghai 日期为 `today`(若 `today` 不是交易日则取上一交易日)。**有效观察窗口** = 满足以下条件的"最近 45 个交易日"集合:

```python
# backend/services/acceptance_window.py(实施期)

from datetime import date, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
WINDOW_TRADING_DAYS = 45


async def compute_acceptance_window(
    today: date,
    *,
    trading_calendar: TradingCalendar,
    p0_interrupts: list[P0InterruptRecord],
    reconciliation_freezes: list[FreezeWindow],
) -> AcceptanceWindow:
    """从 today 倒序回溯,拼出"有效观察窗口"。

    跳过规则:
    1. 非交易日(周末 + A 股节假日) — 不计入,继续向前
    2. P0 系统级中断当日及之前 — 立即截断,从中断日次日起重新累计
    3. reconciliation 冻结日(当日存在 OPEN/EXPIRED ticket)— 不计入,
       但不截断;继续向前回溯

    返回:
    - eligible_days: list[date] 长度 ≤ 45,按时间正序
    - is_complete: bool(len(eligible_days) == 45)
    - p0_reset_at: date | None(最近一次 P0 系统级中断重置点)
    - frozen_skipped: list[date](冻结跳过的日期,供前端展示)
    """
```

**核心不变量**:
- `len(eligible_days) ≤ 45`,只有等于 45 时所有硬门槛才能被求值;不足 45 时验收必然失败(`is_complete=False`)
- 已累计的 eligible_days 在窗口右端右移时只 pop 最早的、push 最新的(O(1) 滑动);P0 中断时整段清零

#### 1.1.2 交易日历来源

**第一阶段使用 `backend/data/trading_hours.py::is_trading_day` 当前实现的 weekday 判定**(audit 已知 TODO:Integrate holiday calendar)。

**P0-6 实施期需求**:在不引入新数据源依赖的前提下,补一份"A 股节假日静态表"(`config/a_share_holidays_2026.yaml`),覆盖元旦/春节/清明/劳动节/端午/中秋/国庆 + 调休信息。年初由人工更新一次。**LLM 不参与**(继承 P0-5 §2 红线 6)。

```python
# backend/data/trading_hours.py(实施期增强)

from pathlib import Path
import yaml

_HOLIDAYS_PATH = Path(__file__).parent.parent.parent / "config" / "a_share_holidays_2026.yaml"


def _load_holidays() -> frozenset[date]:
    """读取静态节假日表;失败返回空 frozenset(降级到 weekday-only)。"""
    if not _HOLIDAYS_PATH.exists():
        log.warning("a_share_holidays_yaml_missing", path=str(_HOLIDAYS_PATH))
        return frozenset()
    raw = yaml.safe_load(_HOLIDAYS_PATH.read_text(encoding="utf-8"))
    return frozenset(date.fromisoformat(d) for d in raw.get("holidays", []))


_HOLIDAYS: frozenset[date] = _load_holidays()


def is_trading_day(d: date | None = None) -> bool:
    """判定是否为 A 股交易日:周一到周五 且 不在节假日表中。"""
    if d is None:
        d = datetime.now(tz=SHANGHAI).date()
    return d.weekday() < 5 and d not in _HOLIDAYS
```

**降级策略**:`a_share_holidays_2026.yaml` 缺失或损坏时回退 weekday-only,验收报告显式标注"使用降级日历";不阻断验收流程。

#### 1.1.3 为什么 45 个交易日

| 候选 | 优劣 |
|------|------|
| 5 日 | 仅验证流程闭环,不足以验证策略 — 拒绝 |
| 10 日(2 周) | 单板块切换可能未走完 — 拒绝 |
| 20 日(1 月) | 单月度因子轮动 + 1 次月底情绪切换 — 偏紧 |
| **45 日(约 2 月,9 周)** | **2 个月度因子轮动 + 2 个月底情绪切换 + ≥2 次月度对账周期 + 季度财报披露窗口部分覆盖 — 用户敲定** |
| 90 日 | 季度完整覆盖,但第一阶段成本过高(45 → 90 翻倍等待);P1 / amendment 路径再考虑 |

### 1.2 稳定性硬门槛(5 项)

#### 1.2.1 指标定义与计算

| 指标 | 阈值 | 计算公式 | 数据源 | 备注 |
|------|------|----------|--------|------|
| 指令完整率 | ≥ 95% | `count(instruction_plans where status ∈ {FILLED, REJECTED, EXPIRED}) / count(all instruction_plans in window)` | `instruction_plans` collection | 终态分布:HOLD 也算 InstructionPlan 但 status 直接停在 VALIDATED(P0-3 §1.6.4),分母仅含 BUY/SELL;`AMBIGUOUS` 与 `DRAFT` 算未完整 |
| 回报解析准确率 | ≥ 99% | `count(execution_reports where parse_ok=True) / count(all execution_reports)` | `execution_reports` collection | feishu_off 模式无 ExecutionReport,该指标按"信号生成路径模拟回报"计算(详见 §1.2.3) |
| 数据延迟与缺失率 | ≤ 1% | `1 - (covered_minutes / expected_minutes)` 滑动 45 日聚合 | `market_snapshots` + `news_articles` collection | 期望覆盖 = (交易时段分钟数 + 资讯爬虫调度分钟数);实际覆盖 = 实际记录到的分钟数 |
| LLM 超时率 | ≤ 5% | `count(agent_step_records where status='failed' OR error matches 'timeout|TimeoutError') / count(all agent_step_records)` | `analysis_records.steps` 子文档 + `analysis_records.debates[].bull/bear` | 9-Agent 任一 step 失败计入 |
| 信号生成成功率 | ≥ 95% | `count(analysis_records where decision.parse_ok=True) / count(analysis_records where status='completed')` | `analysis_records` collection | `FundManagerRecord.parse_ok=True` 表示 LLM 输出可被结构化解析(P0-3 §2 红线 8 parse_ok=False 强制降级 HOLD) |

#### 1.2.2 计算窗口

所有 5 项稳定性指标按"45 日有效观察窗口"内的累积计算,不分日均与窗口均:

- 例:窗口含 45 日,共 200 条 InstructionPlan;FILLED+REJECTED+EXPIRED=192 条 → 192/200 = 96% ≥ 95% PASS
- 例:窗口共 5400 条 ExecutionReport;parse_ok=True 5350 条 → 5350/5400 = 99.07% ≥ 99% PASS

#### 1.2.3 feishu_off 模式下"回报解析准确率"的代理指标

`FEISHU_INTERACTIVE_ENABLED=false` 时不存在用户飞书回报,但仍需检查"系统能不能解析自己生成的指令"。代理:

- 模拟生成 ExecutionReport 文本(由 SimulationExecutor 在 MockBroker 撮合成功后产生),走与真实回报**完全相同**的 `execution_report_parser.py`(P0-4 §1.2),记 parse_ok 与字段交叉校验结果
- 这要求 SimulationExecutor 必须先按 P0-4 §1.2 模板渲染回报文本,再交给 parser → MockBroker 应用;不允许"绕过 parser 直接更新"
- 该机制使 `feishu_off → feishu_on` 切换前的 99% 解析率有意义 — 如果系统连自己生成的标准模板都解析不出 99%,真实用户的非标准回报必然更差

> **设计原理**:第一阶段 `feishu_off` 是 always-on 的能力考场(P0-1 §1.1),验收的本质是检测整条 InstructionPlan → ExecutionReport → MockBroker 的稳定性;不通过完全相同的 parser 走一遍,验收数据无法泛化到 `feishu_on` 场景。

#### 1.2.4 阈值依据

| 阈值 | 设计依据 |
|------|---------|
| 指令完整率 ≥ 95% | P0-3 §1.6 状态机锁定 5 个终态(VALIDATED/DISPATCHED/FILLED/EXPIRED/REJECTED);95% 即每 100 条最多 5 条卡在 DISPATCHED + AMBIGUOUS 中。这 5% 容错给数据源 / LLM 偶发失败留间隔 |
| 回报解析准确率 ≥ 99% | P0-4 严格正则 only,99% 即每 100 条最多 1 条 AMBIGUOUS。第一阶段模板严格预写死,1% 容错只为正则边界 case;真实用户在用熟悉模板后准确率会接近 100% |
| 数据缺失率 ≤ 1% | 行情免费源(adata/akshare/baostock)历史可用度普遍 99%+;资讯爬虫稳定性同水平。1% 容错足够吸收偶发网络抖动,不容许"长期掉数据" |
| LLM 超时率 ≤ 5% | 当前 phase5b_exit_check.py 已有 cost / 延迟门槛(fast p95 ≤ 8 min / slow p95 ≤ 15 min),5% 超时率与之兼容;DeepSeek/Qwen/Kimi 三家典型超时率 ≤ 2%,5% 含 Kimi thinking 高延迟容忍 |
| 信号生成成功率 ≥ 95% | 与回报解析对应;信号生成本身 LLM 输出 JSON 失败率约 2-5%(audit §6 已揭示)。95% 容错与 P0-3 §2 红线 8 一致 |

### 1.3 策略硬门槛(3 项)

#### 1.3.1 最大回撤

```python
# backend/services/equity_curve.py(实施期)

@dataclass(frozen=True)
class EquityPoint:
    trade_date: str  # YYYY-MM-DD
    total_equity: float  # cash + sum(position.volume * mark_price)


def compute_max_drawdown(equity_curve: tuple[EquityPoint, ...]) -> float:
    """计算 equity_curve 上的最大回撤(0~1 区间)。

    定义: max_i (max_{j≤i}(eq_j) - eq_i) / max_{j≤i}(eq_j)
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0].total_equity
    max_dd = 0.0
    for point in equity_curve:
        peak = max(peak, point.total_equity)
        if peak > 0:
            dd = (peak - point.total_equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd
```

**阈值: ≤ 8%**(0.08)

**`equity_curve` 数据源**:
- `feishu_off`:每个交易日 15:00 收盘后由 `mockbroker_snapshots` collection 自动 mark-to-market 一次,收盘价用 `market_data.last_close`;落地 `equity_points` collection
- `feishu_on`:同上,但仓位以 MockBroker(用户回报驱动)为准;现金 / 持仓量在 reconciliation_ticket 应用后会跳变,`equity_curve` 写入"裁定后等价值",前端展示时显式标注"裁定调整"

**`mark_price` 缺失**:
- 停牌当日仍按"上一可得收盘价"作为 mark_price 写入(继承 P0-5 §1.7.3 停牌处理)
- `market_data.last_close` 不可得 → 该 EquityPoint 标记 `mark_quality='degraded'`,**计入回撤计算但触发数据缺失率告警**

#### 1.3.2 验收期累计 PnL ≥ 0

```python
def compute_window_pnl(
    equity_curve: tuple[EquityPoint, ...],
    window: AcceptanceWindow,
) -> float:
    """累计 PnL = 窗口末日 equity - 窗口首日 equity。

    扣除佣金 / 印花税 / 滑点 — MockBroker 撮合时已自动扣除,
    equity 是"扣除成本之后"的净值。
    """
    eligible = [p for p in equity_curve if p.trade_date in window.eligible_dates_set]
    if len(eligible) < 2:
        return 0.0
    return eligible[-1].total_equity - eligible[0].total_equity
```

**阈值: ≥ 0**(累计净 PnL 不为负)

**重要约束**:窗口首日基线是 `feishu_off` 模式启动时的 MockBroker 初始化值(默认虚拟初始资金,可在前端配置)。如果验收期内发生 `feishu_off → feishu_on → feishu_off` 切换(P0-1 §1.3),归档与重置使 equity_curve 不连续 → 该次切换重置整个窗口(等价于 P0 系统级中断)。

#### 1.3.3 验收期相对沪深 300 累计基准超额收益 ≥ 0

```python
def compute_window_excess_return(
    equity_curve: tuple[EquityPoint, ...],
    benchmark_close: dict[str, float],  # trade_date -> close
    window: AcceptanceWindow,
) -> float:
    """相对沪深 300 的累计超额收益:
        portfolio_return = (eq_last - eq_first) / eq_first
        benchmark_return = (bm_last - bm_first) / bm_first
        excess_return = portfolio_return - benchmark_return
    """
    eligible = [p for p in equity_curve if p.trade_date in window.eligible_dates_set]
    if len(eligible) < 2:
        return 0.0
    eq_first = eligible[0].total_equity
    eq_last = eligible[-1].total_equity
    if eq_first <= 0:
        return 0.0
    portfolio_return = (eq_last - eq_first) / eq_first

    bm_first = benchmark_close.get(eligible[0].trade_date)
    bm_last = benchmark_close.get(eligible[-1].trade_date)
    if bm_first is None or bm_last is None or bm_first <= 0:
        return 0.0  # 基准数据缺失 → 计为 0(保守);并发数据缺失告警

    benchmark_return = (bm_last - bm_first) / bm_first
    return portfolio_return - benchmark_return
```

**阈值: ≥ 0**

**基准数据源**:沪深 300 收盘价由 `backend/data/market_data.py::get_index_close('000300.SH')` 提供,该接口已存在于 adata / akshare 主备路径。基准数据缺失视为 `excess_return=0`(保守判定:不让基准缺失阻断验收,但也不让其便宜 PASS — 0 仍 ≥ 0 是"边界 PASS",前端验收报告显式提示"含基准数据缺失日")。

**两条硬门槛(PnL ≥ 0 + 超额收益 ≥ 0)的关系**:
- 必须**同时**满足。任一未满足即 FAIL。
- 极端 case 1:沪深 300 跌 5%,组合也跌 4% → PnL=-4%(FAIL)+ 超额=+1%(PASS) → 整体 FAIL。**这是有意为之** — 用户选择"验收期累计 PnL ≥ 基准超额 ≥ 0",字面含义是"两者都满足",熊市中即便跑赢基准也不允许切真钱(避免熊市中"系统少亏"的伪稳健)。
- 极端 case 2:沪深 300 涨 10%,组合涨 8% → PnL=+8%(PASS)+ 超额=-2%(FAIL) → 整体 FAIL。**这也是有意为之** — 系统未证明能比"buy-and-hold 沪深 300 ETF"更好,不值得切真钱。

### 1.4 观察指标(不阻断切换,持续展示)

```python
# backend/services/acceptance_observation.py(实施期)

@dataclass(frozen=True)
class ObservationMetrics:
    risk_rejection_rate: float  # 风控拦截率(0~1)
    simulated_fill_rate: float  # 模拟订单完全成交率(0~1)
    win_rate: float            # 胜率(单笔 PnL>0 占比)
    payoff_ratio: float        # 盈亏比(平均盈利 / |平均亏损|)
    turnover_rate: float       # 换手率(总成交额 / 平均资金)
    instruction_to_execution_latency_p95_seconds: float
    avg_instruction_cost_rmb: float
```

| 指标 | 计算公式 | 用途 |
|------|---------|------|
| 风控拦截率 | `count(REJECTED instruction_plans where rejection_reason matches 'risk_*') / count(BUY+SELL instruction_plans)` | 持续 > 30% 提示 LLM 仓位计算与 RiskConfig 失配,需调 prompt |
| 模拟订单完全成交率 | `count(orders where filled_volume == volume) / count(all orders)` | 反映限价合理性 |
| 胜率 | `count(closed positions where realized_pnl > 0) / count(all closed positions)` | 短期窗口 45 日胜率波动大,作诊断 |
| 盈亏比 | `mean(realized_pnl > 0) / abs(mean(realized_pnl < 0))` | 长期 > 1.5 才算策略有正期望;短期不要求 |
| 换手率 | `sum(turnover) / mean(equity)` | 过高(> 5x)成本侵蚀严重 |
| 指令到执行延迟 p95 | `p95(execution_report.received_at - instruction_plan.dispatched_at)` | feishu_off 下应 < 1s;feishu_on 反映用户响应速度 |
| 单笔指令平均成本 | `total_llm_cost / count(instruction_plans)` | 与 phase5b_exit_check.py 衔接 |

**观察指标不阻断切换**,但每日验收报告必须展示;前端 AcceptanceCenter 用红/黄/绿三色提示极端值。

### 1.5 P0 系统级中断的定义与重置语义

#### 1.5.1 P0 中断定义(任一即触发)

| 类别 | 定义 | 检测来源 |
|------|------|---------|
| 行情连续断流 | adata + akshare + baostock 三源连续 30 分钟全部失败(交易时段内) | `market_data` 健康检查 |
| LLM 全部不可用 | DeepSeek + Qwen + Kimi 三家连续 1 小时全部失败 | `llm_router` 失败计数器 |
| MockBroker 数据损坏 | `mock_broker.snapshot()` 抛异常 / 镜像内现金为负 / 持仓量为负 | MockBroker 自检 |
| 状态机非法迁移 | `instruction_plan_state_machine.transition` 抛 `InvalidTransitionError`(P0-3 §1.6.4)/ `reconciliation_state_machine.transition_ticket` 抛 `InvalidTicketTransitionError`(P0-5 §1.5.3) | 状态机守门函数 |
| 长连接长时不可用 | `lark-oapi` 长连接断开 ≥ 4 小时(只在 `feishu_on` 下触发) | P0-2 §1 长连接 worker |

#### 1.5.2 P0 中断的重置语义

```python
# backend/services/acceptance_p0.py(实施期)

@dataclass(frozen=True)
class P0InterruptRecord:
    occurred_at: datetime
    category: str  # market_outage / llm_outage / mockbroker_corruption / state_invalid / longconn_outage
    detail: str
    detected_by: str


async def reset_window_on_p0(
    interrupt: P0InterruptRecord,
) -> None:
    """P0 中断触发 → 把当前 acceptance window 整体作废。

    1. 写入 acceptance_p0_interrupts collection 留痕
    2. 不删 equity_curve / instruction_plans 历史 — 仅作废窗口的"起点"
    3. 下一个交易日 compute_acceptance_window() 时 p0_reset_at 为该次中断日期,
       窗口起点从中断日次日开始重新累计
    """
```

**已发出 InstructionPlan 不撤销**:P0 中断只重置"验收倒计时",不影响已落库的 InstructionPlan 状态机 / equity_curve / MockBroker 历史。验收报告会显式标注"该 P0 之后窗口起点 = 2026-XX-XX"。

#### 1.5.3 P0 中断的恢复

P0 中断恢复后(行情恢复 / LLM 恢复 / MockBroker 重启完成 / 状态机修复),系统继续跑 simulation_auto;验收倒计时从下一个交易日重新累计。**用户无需手动操作恢复**(全自动);但每次 P0 中断会主动发飞书告警(走主通道 + 备用 webhook 并发,继承 P0-2 §1.2 双通道分工)。

### 1.6 reconciliation 冻结的暂停语义

#### 1.6.1 暂停而非重置

P0-5 §1.6 的 `reconciliation_tickets` OPEN/EXPIRED 期间:

- ModeRouter 阶段拒绝路由买卖类 InstructionPlan(P0-5 §1.6.2 已锁)
- simulation_auto 与 9-Agent 辩论持续运行,InstructionPlan 仍生成入库(status=VALIDATED)
- **MockBroker 无新成交,equity_curve 不演化** ← 这是验收的关键约束

P0-6 选择**暂停**:

- 冻结期间(任一日存在 OPEN/EXPIRED ticket)→ 当日 NOT 计入 45 日有效观察窗口
- 但已累计天数不清零 → ticket 解决后下一个交易日继续延伸窗口

#### 1.6.2 为什么暂停而非重置

| 选项 | 影响 |
|------|------|
| 重置(冻结 = P0 同等)| 公司行动 / 用户漏报 → 验收期被反复重置 → 永远到不了 45 日。第一阶段 watchlist 体量下,公司行动月发生 0-2 次,即"每月可能重置 0-2 次",90% 概率验收期被反复推后 |
| **暂停**(选择)| 冻结日不计 + 不清零;ticket 解决后继续 → 公司行动只占用 1-2 个等效交易日,验收期成本可预期 |
| 计入(等同未发生)| equity_curve 不演化 → PnL / 最大回撤指标失真 → 不可接受 |

#### 1.6.3 冻结日处理实现

```python
# backend/services/acceptance_freeze_filter.py(实施期)

async def is_window_eligible(
    trade_date: date,
    *,
    p0_interrupts: list[P0InterruptRecord],
    reconciliation_freezes: list[FreezeWindow],
) -> tuple[bool, str | None]:
    """判定单日是否计入 45 日窗口。

    返回 (eligible, skip_reason):
    - (True, None):正常计入
    - (False, 'p0_interrupt'):此前发生 P0 中断,窗口起点之前的日子直接截断
    - (False, 'reconciliation_freeze'):冻结日,跳过
    - (False, 'non_trading_day'):非交易日(节假日/周末),跳过
    """
```

**冻结起止判定**:`FreezeWindow` 由 `reconciliation_tickets` collection 派生 — `start_date = ticket.created_at.date()`;`end_date = ticket.resolved_at.date() if status ∈ RESOLVED_* else None`(EXPIRED 仍 freeze 直至 RESOLVED_*)。同一交易日只要存在任一 freeze window 覆盖,该日跳过。

### 1.7 每日验收报告

#### 1.7.1 触发时机

每个交易日 16:00:30 Asia/Shanghai(对齐 P0-4 §1.6 / P0-5 §1.2.1 cutoff + 30 秒缓冲)由 `backend/services/acceptance_dispatcher.py` 异步任务生成 `acceptance_reports` collection 一条记录。

#### 1.7.2 数据模型

```python
# backend/services/acceptance_report.py(实施期)

from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field


class StabilityGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str  # instruction_completeness / report_parse_accuracy / data_missing / llm_timeout / signal_success
    threshold: float
    actual: float
    passed: bool
    sample_size: int


class StrategyGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str  # max_drawdown / cumulative_pnl / benchmark_excess_return
    threshold: float | None  # None for cumulative_pnl(只要求 >=0)
    actual: float
    passed: bool


class ObservationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    risk_rejection_rate: float
    simulated_fill_rate: float
    win_rate: float
    payoff_ratio: float
    turnover_rate: float
    instruction_to_execution_latency_p95_seconds: float
    avg_instruction_cost_rmb: float


class AcceptanceReport(BaseModel):
    """单日验收报告;每个交易日 16:00:30 生成。"""
    model_config = ConfigDict(frozen=True)

    report_date: date
    """报告对应的交易日。"""

    generated_at: datetime
    window_eligible_dates: tuple[str, ...]
    """有效观察窗口内交易日(按时间正序),长度 ≤ 45。"""

    is_window_complete: bool
    """len(window_eligible_dates) == 45 时为 True。"""

    p0_reset_at: date | None
    frozen_skipped_dates: tuple[str, ...]

    stability_gates: tuple[StabilityGateResult, ...]
    strategy_gates: tuple[StrategyGateResult, ...]
    observation: ObservationSnapshot

    can_switch_to_feishu_on: bool
    """所有 stability_gates + strategy_gates passed=True AND is_window_complete=True。"""

    blockers: tuple[str, ...]
    """如果 can_switch_to_feishu_on=False,列出失败原因(用户可读)。"""
```

#### 1.7.3 索引

- `(report_date)` 唯一(防同日重复)
- `(can_switch_to_feishu_on, generated_at)` 复合,供前端查"最近一次 PASS 报告"

#### 1.7.4 前端 AcceptanceCenter

只读视图(不接受用户输入,继承 P0-5 §1.10 ReconciliationCenter 设计原则):

- 顶部 KPI 卡:`is_window_complete` / `can_switch_to_feishu_on` / `len(window_eligible_dates)` / `p0_reset_at`
- 中部图表:8 项硬门槛随时间(过去 60 个交易日)的折线图;每条线标 threshold 线
- 底部表格:每日 AcceptanceReport 的所有字段;支持按日期查询
- 切换按钮:`can_switch_to_feishu_on=True` 才点亮,点击触发 P0-1 §1.3.1 的"feishu_off → feishu_on"切换流程;不点亮时鼠标悬停显示 blockers

#### 1.7.5 LLM 严禁参与验收报告生成

继承 P0-4 / P0-5 LLM 隔离精神:`backend/services/acceptance*.py` 严禁 `import backend.llm.*`。所有指标计算走纯 Python 聚合 + MongoDB 查询;`blockers` 列表用预写死的中文模板:

```python
BLOCKER_TEMPLATES = {
    "instruction_completeness_below": "指令完整率 {actual:.1%} < 阈值 {threshold:.0%}(45 日窗口共 {sample_size} 条 InstructionPlan)",
    "report_parse_accuracy_below": "回报解析准确率 {actual:.1%} < 阈值 {threshold:.0%}(45 日窗口共 {sample_size} 条 ExecutionReport)",
    "data_missing_above": "数据缺失率 {actual:.1%} > 阈值 {threshold:.0%}",
    "llm_timeout_above": "LLM 超时率 {actual:.1%} > 阈值 {threshold:.0%}",
    "signal_success_below": "信号生成成功率 {actual:.1%} < 阈值 {threshold:.0%}",
    "max_drawdown_above": "最大回撤 {actual:.1%} > 阈值 {threshold:.0%}",
    "cumulative_pnl_negative": "验收期累计 PnL {actual:.2f} 元 < 0",
    "benchmark_excess_negative": "相对沪深 300 累计超额收益 {actual:.2%} < 0",
    "window_incomplete": "有效观察窗口仅 {actual} 个交易日,需累计满 45 日",
}
```

### 1.8 与 P0-1 / P0-5 切换流程的衔接

#### 1.8.1 P0-1 §1.3.1 切换触发的硬前置

P0-1 §1.3.1 描述了 `feishu_off → feishu_on` 切换的 6 步流程(归档 → 重置 → 首条飞书 → 解析与确认 → 初始化 → 状态切换)。**P0-6 在前置条件层增加一个红线**:

> 切换 API `POST /api/run-mode/transition` 必须在执行第 1 步"归档"之前调用 `acceptance_report.can_switch_to_feishu_on()` 校验:
> - True:进入第 1 步
> - False:返回 HTTP 403 + JSON 体含 `{"error": "acceptance_blocked", "blockers": [...]}` + 前端提示用户先去 AcceptanceCenter 查看 blockers
>
> 这是路由层硬约束,**不允许**通过 env var 或 CLI 绕过(继承 P0-1 §1.4 切换需要后台显式触发)。

#### 1.8.2 切换成功后的窗口语义

切换成功(`feishu_off → feishu_on`)后:

- MockBroker 被重置到用户初始化对账值(P0-1 §1.3.1 #5)
- equity_curve 不连续 → 验收窗口从切换日次日重新累计 45 日(等同于 P0 系统级中断重置)
- 这意味着 `feishu_on` 期间会重新经历 45 个交易日的验收(指标重新满足)
- 但这不会反向锁住 `feishu_on → feishu_off`:反向切换无验收硬前置(用户随时可以退出真实交易,继承 P0-1 §1.3.2 路径 A/B)

#### 1.8.3 实施期模块依赖图

```
backend/services/
├── acceptance_window.py        ← compute_acceptance_window
├── acceptance_freeze_filter.py ← is_window_eligible(单日跳过判定)
├── acceptance_p0.py            ← P0 中断定义 + reset_window_on_p0
├── acceptance_metrics_stability.py   ← 5 项稳定性硬门槛计算
├── acceptance_metrics_strategy.py    ← 3 项策略硬门槛计算
├── acceptance_observation.py   ← 7 项观察指标
├── acceptance_report.py        ← AcceptanceReport 数据模型 + 持久化
├── acceptance_dispatcher.py    ← 16:00:30 调度器
└── equity_curve.py             ← MockBroker 每日 mark-to-market 写 EquityPoint

backend/api/
├── acceptance.py               ← GET /api/acceptance/today / /history / /window-detail
└── run_mode.py(P0-1 已规划)  ← POST /api/run-mode/transition 增加 acceptance 前置校验

frontend/src/views/
└── AcceptanceCenter.vue        ← 只读验收看板
```

### 1.9 与现有 phase5b_exit_check 的关系

`backend/services/phase5b_exit_check.py` 是旧方向 Phase 5B 出口检查,门槛包括 cost / latency / shadow consistency。该模块在新方向(P0-1 整体重写)实施期会被退役 — 但 P0-6 阶段不删除,理由:

- phase5b_exit_check.py 的"延迟 p95 / cost 上限"思路与本决策"LLM 超时率"互补,实施期可借鉴 `compute_p95` 等纯函数
- shadow_compare 的 ACTION_MATCH / CONFIDENCE_DELTA 阈值范式可作为"观察指标"的扩展(P1 / amendment 路径)

### 1.10 验收期与 Phase 5B 的语义不混淆

- Phase 5B 是旧方向的"评估期 → 干运行 → 实盘"升级路径,已暂停推进
- P0-6 验收期是新方向的"`feishu_off` 模式能否切到 `feishu_on`"的硬前置
- 两个概念**不可互换**;实施期文档中应严格使用"acceptance window"或"验收期",**禁用** "phase5b_eval" / "phase6_dryrun" 等旧术语(继承 P0-1 §1.7 旧授权语义废止)

## 2. 红线 / 边界(立即生效)

P0-6 落地后这些立即成为代码硬约束:

1. **验收期固定 45 个交易日滚动窗口**:任何尝试在新代码中读取 `WINDOW_TRADING_DAYS` 之外的常量(如 30 / 20 / 60 等)即红线违规;调整必须先走 `P0-6-amendment-{date}-window-length.md`
2. **稳定性硬门槛 5 项 + 策略硬门槛 3 项**:8 项门槛阈值锁定(95% / 99% / 1% / 5% / 95% / 8% / 0 / 0);调整任一阈值必须先走 amendment;实施期 lint rule 阻止阈值常量被修改
3. **LLM 严禁参与验收路径**:`backend/services/acceptance*.py` / `backend/services/equity_curve.py` 严禁 `import backend.llm.*`(继承 P0-4 §2 红线 2 / P0-5 §2 红线 6)
4. **A 股节假日表静态 YAML**:第一阶段不引入 akshare/adata/baostock 节假日 API(否则 1.2 实施期成本爆炸);只读 `config/a_share_holidays_*.yaml`,人工年度更新
5. **基准固定沪深 300**:`benchmark_close = market_data.get_index_close('000300.SH')`;切换基准必须先走 `P0-6-amendment-{date}-benchmark-change.md`
6. **`equity_curve` 不可绕过 mark-to-market**:每个交易日 15:00 之后必须有一条 `EquityPoint` 写入;`equity_points` collection 缺日红线违规;数据降级时仍写入但标 `mark_quality='degraded'`
7. **回报解析准确率 99% 在 feishu_off 模式下也要求**:SimulationExecutor 必须按 P0-4 §1.2 模板渲染 ExecutionReport 文本,经过相同 parser 解析后才能更新 MockBroker;严禁绕过 parser 直接更新(详见 §1.2.3)
8. **P0 中断的 5 类定义锁定**:行情连续断流 30min / LLM 全停 1h / MockBroker 损坏 / 状态机非法迁移 / 长连接 4h;新增类别必须先走 amendment
9. **reconciliation 冻结只暂停不重置**:任何代码尝试在冻结日触发 `reset_window_on_p0()` 或同语义函数即红线违规
10. **切换 API `POST /api/run-mode/transition` 必须前置 `acceptance_report.can_switch_to_feishu_on()` 校验**:返回 False 时立即 HTTP 403 + 预写死 blockers 文案;严禁 env var / CLI 绕过(继承 P0-1 §1.4)
11. **AcceptanceReport 是 frozen Pydantic v2 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 / P0-5 §2 红线 16 immutability 原则)
12. **`acceptance_reports` collection 每日仅 1 条**:`(report_date)` 唯一索引;同日重新生成必须 upsert(防"试探" + 防数据污染)
13. **观察指标不阻断切换 = 不参与 `can_switch_to_feishu_on` 计算**:任何把观察指标写入 `can_switch_to_feishu_on` 判断式的代码即红线违规;前端展示用三色提示
14. **验收期内禁止"绕过指标修改 MockBroker"**:用户不能通过前端直接编辑 MockBroker 现金 / 持仓(继承 P0-5 §2 红线 12 ReconciliationApplier 入口收口)— 否则 equity_curve 失真,PnL / 最大回撤指标无意义
15. **基准数据缺失保守判定 = 0**:`benchmark_excess_return=0`(等价边界 PASS);严禁在基准缺失时跳过校验或假阳性 PASS;数据缺失会同步产生告警

## 3. 影响范围(留给 implementation 阶段)

后续实施任务清单(不在 P0-6 决策内,等所有 P0 锁定后由新执行计划编排):

### 3.1 新增项(代码级)

- `backend/services/acceptance_window.py`:
  - `AcceptanceWindow` frozen dataclass(eligible_dates / is_complete / p0_reset_at / frozen_skipped)
  - `compute_acceptance_window(today, *, trading_calendar, p0_interrupts, reconciliation_freezes) -> AcceptanceWindow`
  - `WINDOW_TRADING_DAYS = 45` 常量
- `backend/services/acceptance_freeze_filter.py`:
  - `FreezeWindow` frozen dataclass
  - `is_window_eligible(trade_date, ...) -> tuple[bool, str | None]`
  - `derive_freeze_windows_from_tickets(tickets) -> tuple[FreezeWindow, ...]`
- `backend/services/acceptance_p0.py`:
  - `P0InterruptRecord` frozen Pydantic 模型
  - `P0InterruptCategory` StrEnum(`MARKET_OUTAGE` / `LLM_OUTAGE` / `MOCKBROKER_CORRUPTION` / `STATE_INVALID` / `LONGCONN_OUTAGE`)
  - `reset_window_on_p0(interrupt) -> None`
  - `detect_market_outage` / `detect_llm_outage` / `detect_mockbroker_corruption` / `detect_state_invalid` / `detect_longconn_outage` 各自的检测函数(被调度器周期性调用)
- `backend/services/acceptance_metrics_stability.py`:
  - 5 个纯函数:`compute_instruction_completeness` / `compute_report_parse_accuracy` / `compute_data_missing_rate` / `compute_llm_timeout_rate` / `compute_signal_success_rate`
  - 各自接受 `window: AcceptanceWindow` 与必要的 collection 查询接口
  - 阈值常量集中:`STABILITY_THRESHOLDS = {...}` frozen dict
- `backend/services/acceptance_metrics_strategy.py`:
  - `compute_max_drawdown` / `compute_window_pnl` / `compute_window_excess_return`
  - 阈值常量:`MAX_DRAWDOWN_THRESHOLD = 0.08` / `BENCHMARK_INDEX_CODE = "000300.SH"`
- `backend/services/acceptance_observation.py`:
  - `ObservationMetrics` frozen Pydantic 模型
  - `compute_observation_metrics(window, ...) -> ObservationMetrics`
- `backend/services/acceptance_report.py`:
  - `StabilityGateResult` / `StrategyGateResult` / `ObservationSnapshot` / `AcceptanceReport` frozen Pydantic 模型
  - `compose_acceptance_report(report_date, ...) -> AcceptanceReport`
  - `BLOCKER_TEMPLATES` 预写死 dict
  - `can_switch_to_feishu_on(report) -> tuple[bool, list[str]]`
- `backend/services/acceptance_dispatcher.py`:
  - 16:00:30 异步任务,调用 compose + 持久化
  - 与 P0-5 reconciliation_dispatcher 共用调度框架
- `backend/services/equity_curve.py`:
  - `EquityPoint` frozen Pydantic 模型
  - `mark_to_market_daily(mock_broker, market_data, trade_date)` 持久化 EquityPoint
  - `MarkQuality` StrEnum(`OK` / `DEGRADED`)
- `backend/api/acceptance.py`:
  - `GET /api/acceptance/today` 返回当日 AcceptanceReport
  - `GET /api/acceptance/history?from=&to=` 列表
  - `GET /api/acceptance/window-detail` 返回当前窗口的 eligible/skipped 详细日表
- `backend/api/run_mode.py`(P0-1 已规划)修改:
  - `POST /api/run-mode/transition` 进入第 1 步前增加 `acceptance.can_switch_to_feishu_on()` 校验
  - 失败返回 HTTP 403 + JSON `{"error": "acceptance_blocked", "blockers": [...]}`
- `frontend/src/views/AcceptanceCenter.vue`:
  - 顶部 KPI 卡 + 中部折线图 + 底部 AcceptanceReport 表格 + 切换按钮(条件点亮)
- 新 MongoDB collection `acceptance_reports`:
  - 索引 `(report_date)` 唯一
  - 索引 `(can_switch_to_feishu_on, generated_at)` 复合
  - 字段 schema = AcceptanceReport.model_dump()
- 新 MongoDB collection `equity_points`:
  - 索引 `(trade_date)` 唯一
  - 字段:`trade_date / total_equity / cash / positions_value / mark_quality / generated_at`
- 新 MongoDB collection `acceptance_p0_interrupts`:
  - 索引 `(occurred_at)` 单字段
  - 字段:`occurred_at / category / detail / detected_by`
- 新 `config/a_share_holidays_2026.yaml`:
  - 字段:`year: 2026 / holidays: [YYYY-MM-DD, ...]` 静态列表
  - 由人工年度更新

### 3.2 修改项

- `backend/data/trading_hours.py`:
  - `is_trading_day` 增加节假日表读取(§1.1.2)
  - 失败降级 weekday-only 并产生 warning 日志
- `backend/broker/mock_broker.py`:
  - 新增 `async def daily_mark_to_market(market_data, trade_date) -> EquityPoint`(被 acceptance_dispatcher 在 15:00 之后调用)
  - 现有 `async def get_snapshot()` 不变(P0-5 已规划)
- `backend/data/market_data.py`:
  - 确认 `get_index_close('000300.SH')` 接口稳定可用(adata / akshare 主备已存在,但需添加返回值缓存避免重复请求)
- `backend/api/run_mode.py`(P0-1 §3.2 已规划):
  - 切换前置增加 acceptance 校验
- `backend/data/database.py`:
  - 新增 `save_equity_point` / `query_equity_points_in_window` / `save_acceptance_report` / `query_latest_acceptance_report` / `query_acceptance_reports_history` / `save_p0_interrupt` / `query_p0_interrupts_after`

### 3.3 配置项

- `config/acceptance.yaml`(新):
  - `window_trading_days: 45`
  - `stability_gates: {instruction_completeness: 0.95, report_parse_accuracy: 0.99, data_missing_rate: 0.01, llm_timeout_rate: 0.05, signal_success_rate: 0.95}`
  - `strategy_gates: {max_drawdown: 0.08, cumulative_pnl: 0.0, benchmark_excess_return: 0.0}`
  - `benchmark_index_code: "000300.SH"`
  - `daily_report_send_offset_seconds: 30`(16:00 + 30 秒)
  - `p0_thresholds: {market_outage_minutes: 30, llm_outage_hours: 1, longconn_outage_hours: 4}`
- `config/a_share_holidays_2026.yaml`(新):静态节假日列表
- `.env`:无新增(验收路径不需要新凭证)

### 3.4 文档同步(本决策落地立即执行,见 §5.1)

- `CLAUDE.md` §1.3 进度行(P0-6 ✅,下一站 P0-7)
- `CLAUDE.md` §2.1 P0-6 行(状态 + 决策文档列 + 备注)
- `CLAUDE.md` §3.1 红线节(同步本文 §2 红线 1-15 中与现有红线不重叠的部分)
- `MEMORY.md` 索引新增 `project_run_mode_p0_6.md`
- 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_6.md`

### 3.5 测试覆盖(实施期任务)

- `tests/test_acceptance_window.py`:
  - 满 45 日窗口 happy path
  - 含 P0 中断重置后窗口
  - 含 reconciliation 冻结跳过(暂停语义)
  - 节假日表正常加载 / 缺失降级到 weekday-only
  - 边界:刚开始运行 < 45 日 / 中断后第 N 日(N < 45)
- `tests/test_acceptance_metrics_stability.py`:
  - 5 项稳定性指标各自 happy / boundary / 全 fail / 数据稀疏
  - 阈值边界(94.99% FAIL / 95.00% PASS / 95.01% PASS)
  - feishu_off 模式下"回报解析准确率"代理路径
- `tests/test_acceptance_metrics_strategy.py`:
  - max_drawdown happy / 边界 7.99% / 8.00% / 8.01%
  - cumulative_pnl 正/零/负
  - benchmark_excess_return 正/零/负
  - 基准数据缺失 → 0
- `tests/test_acceptance_freeze_filter.py`:
  - 单日存在 OPEN ticket → skip
  - 单日存在 EXPIRED ticket → skip
  - 单日存在 RESOLVED_* ticket(其他日 OPEN)→ 该日仍 skip
  - 多 ticket 重叠 → skip
- `tests/test_acceptance_p0.py`:
  - 5 类 P0 中断各自检测 happy
  - reset_window_on_p0 写入 collection
  - 重置后下个交易日窗口起点 = 中断日次日
- `tests/test_acceptance_report.py`:
  - 8 项硬门槛全 PASS → can_switch_to_feishu_on=True / blockers=[]
  - 任一硬门槛 FAIL → can_switch_to_feishu_on=False / blockers 中含对应预写死文案
  - 窗口未满(< 45 日)→ False / blockers 含 window_incomplete
- `tests/test_acceptance_api.py`:
  - GET /api/acceptance/today / history / window-detail
  - POST /api/run-mode/transition 在 can_switch=False 时返回 403 + blockers
  - POST /api/run-mode/transition 在 can_switch=True 时正常进入 P0-1 §1.3.1 流程
- `tests/test_equity_curve.py`:
  - mark_to_market_daily 写入 EquityPoint(含 mark_quality=DEGRADED 路径)
  - compute_max_drawdown 单调递减 / 单调递增 / V 形 / 复杂折线
- `tests/test_acceptance_isolation_redline.py`:
  - 静态扫描 `backend/services/acceptance*.py` 不含 `from backend.llm` / `import backend.llm`
- `tests/test_acceptance_e2e.py`:
  - 端到端:模拟 60 日运行 + 中间 1 次 P0 + 1 次 reconciliation 冻结 + 验收报告每日生成 + 第 50 日 PASS
- 覆盖率目标:`backend/services/acceptance*.py` ≥ 90%(策略性内核;略低于 risk 95% 但高于普通 70%)

### 3.6 与 P0-1 / P0-5 实施期的并行度

| 任务 | 依赖 | 并行 |
|------|------|------|
| 节假日 YAML | 无 | 可独立先做 |
| equity_curve / mark-to-market | MockBroker(P1-2 范围)| 与 P0-5 reconciliation_applier 并行(共享 MockBroker.reset_to_snapshot 入口)|
| acceptance_metrics_stability | InstructionPlan(P0-3)+ ExecutionReport(P0-4)| P0-3 / P0-4 实施期完成后 |
| acceptance_metrics_strategy | equity_curve | equity_curve 完成后 |
| acceptance_dispatcher | reconciliation_dispatcher(P0-5)| 与 P0-5 dispatcher 共享调度器框架 |
| acceptance API + 前端 | 全部 backend 完成 | 最后阶段 |
| run_mode 切换前置 | acceptance + P0-1 run_mode | P0-1 切换 API 落地后增量加 |

## 4. 决策依据

### 4.1 audit 引用

- audit §1.2 关键缺口:"信号生成成功率 / 操作指令完整率 / 风控拦截率 / 数据延迟与缺失率 / 飞书模式下回报解析准确率" — 本决策把这些缺口翻译成可量化的硬门槛
- audit §3.1 已揭示"测试通过 ≠ 闭环可用",1139 测试全绿但 RiskEngine 不接订单 — 本决策"指令完整率 + 回报解析准确率"是测试套件无法替代的"端到端可用性"指标
- audit §6 揭示当前信号生成 LLM 输出 JSON 失败率约 2-5% — 本决策 95% 阈值与之兼容并预留改进空间
- audit §11 推荐路线图:"先稳定 simulation_auto,再切 feishu_interactive" — 本决策硬前置正是这条路线的代码化

### 4.2 现有代码事实抽检

- `backend/agents/records.py:92-111` 的 `FundManagerRecord.parse_ok` 字段 — 信号生成成功率指标的直接数据源
- `backend/services/signal_evaluator.py:35-97` 已实现 hit_rate(horizon=5 日)— 胜率指标的现成范式
- `backend/services/cost_guard.py:140-158` fail-closed 范式(NaN/Inf 视为 hard_breach)— 数据降级保守判定的设计灵感
- `backend/services/phase5b_exit_check.py:43-48` 现有阈值范式(cost / latency / consistency)— 验证"硬门槛 + 滚动窗口"模式在本项目已有先例
- `backend/services/shadow_compare.py:27-28` `ACTION_MATCH_THRESHOLD=0.85` / `CONFIDENCE_DELTA_THRESHOLD=0.15` — 决策一致性的成熟阈值范式
- `backend/data/trading_hours.py:42-53` `is_trading_day` 当前 weekday-only(TODO 节假日日历)— 本决策实施期 §1.1.2 必须解决

### 4.3 用户选择记录(2026-05-09 决策对话两轮)

#### 第一轮(确定大方向)

| 问题 | 选择 |
|------|------|
| 验收期连续运行天数硬门槛? | **45 个交易日跟踪**(用户敲定,严于推荐 20 日) |
| P0-6 的 15 项指标如何分级? | **稳定性硬门槛 + 策略观察**(但用户在 Q3 中实际把 PnL+benchmark 也提为硬门槛,文档以最终一致版为准) |
| 切换 feishu_on 最低 PnL 要求? | **PnL ≥ 基准超额收益 ≥ 0**(双重硬门槛) |
| P0 故障定义 + 重置语义 + 冻结期? | **P0=系统级中断;reconciliation 冻结不计入连续天数(暂停而非重置)** |

#### 第二轮(锁定具体阈值)

| 问题 | 选择 |
|------|------|
| 基准指数选择? | **沪深 300** |
| 稳定性硬门槛具体阈值套组? | **指令完整率 ≥ 95% / 回报解析准确率 ≥ 99% / 数据缺失率 ≤ 1% / LLM 超时率 ≤ 5% / 信号生成成功率 ≥ 95%**(用户选最严套组,严于"保守型推荐"的 ≤ 2% 数据缺失率) |
| 验收期最大回撤上限? | **≤ 8%** |
| 验收期付机制 + 失败语义? | **滚动窗口 + 多门槛同时达标 + 不重启**(达标后窗口右移即可切换) |

### 4.4 与 P0-1 ~ P0-5 已锁红线的兼容性自检

| 现有红线 | 本决策的兼容性 |
|----------|---------------|
| P0-1 §2 红线 4(模式切换不允许仅改 env var)| §1.8.1 切换前置 acceptance 校验进一步加强:连"显式后台触发"也要先满足 8 项硬门槛 |
| P0-1 §2 红线 5(InstructionPlan 必须由多 Agent 辩论生成)| §1.2.1 信号生成成功率指标恰好覆盖此红线;debate_round_count ≥ 1 在 P0-3 已强制 |
| P0-2 §2 红线 2(备用 webhook 仅发系统告警)| §1.5.3 P0 中断告警走主通道 + 备用 webhock 并发 — 兼容 |
| P0-3 §2 红线 8(parse_ok=False 强制降级 HOLD)| §1.2.1 信号生成成功率正是 parse_ok=True 占比 — 直接复用 |
| P0-4 §2 红线 2(LLM 严禁参与回报路径)| §1.10 LLM 严禁参与验收路径 — 模式一致 |
| P0-5 §2 红线 5(OPEN/EXPIRED ticket 期间冻结买卖类路由)| §1.6 验收窗口同步暂停;两者机制配合 |
| P0-5 §2 红线 12(MockBroker 覆盖必须经过 ReconciliationApplier)| §1.3.2 equity_curve 数据源对齐 — feishu_on 下裁定调整在曲线上显示 |

## 5. 后续动作 (checklist)

> 本决策本身定稿不触发实施工作。以下条目仅记录"P0-6 锁定后下一步要做什么",真实落地排期等所有 P0 全部锁定后由新执行计划统一编排。

### 5.1 立刻完成的状态同步(本 commit 同步进行)

- [ ] 更新 `CLAUDE.md` §1.3:P0-6 状态从 ⏳ 改为 ✅,链接本文件
- [ ] 更新 `CLAUDE.md` §2.1:P0-6 行 决策文档列填本文件路径
- [ ] 更新 `CLAUDE.md` §3.1 红线节:把本文件 §2 红线 1-15 中与现有红线不重叠的部分加进去
- [ ] 更新 `MEMORY.md` 索引:新增 `project_run_mode_p0_6.md` 条目
- [ ] 新建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_6.md`
- [ ] commit 本决策文档 + CLAUDE.md / MEMORY.md / 自记忆文件 同步更新(单 commit)
- [ ] 不要立即实施代码 — 等剩余 P0 决策(P0-7 风险红线 / P0-8 数据可信度 / P0-9 标的范围 / P0-10 LLM 边界)也锁定后再统一进入实施期

### 5.2 依赖本决策的下游 P0 决策

- **P0-7 风险红线与指导强度**: 本决策的"风控拦截率"是观察指标;P0-7 决定的硬限制(单股仓位 / 总仓位 / 单次金额 / 每日指令数 / 亏损暂停线)会反过来影响"指令完整率"的分母与"风控拦截率"的高低
- **P0-8 数据与资讯可信度**: 本决策的"数据延迟与缺失率 ≤ 1%"依赖 P0-8 锁定的具体源(adata 主 / akshare 备 / baostock 历史)与异常停发规则;停牌检测属 P0-8 范围
- **P0-9 第一阶段标的范围与频率**: 本决策的"换手率"观察指标依赖 P0-9 锁定的 watchlist 范围与每日最大新指令数;watchlist 太小 → 换手率失真;太大 → 信号生成成功率分母膨胀
- **P0-10 LLM 角色边界**: 本决策的"LLM 超时率 ≤ 5%"依赖 P0-10 锁定的"哪些场景允许 Kimi thinking";thinking 启用越多超时率越高

### 5.3 实施期(所有 P0 锁定后)

- [ ] 按 §3.1-§3.5 编写 implementation 任务列表
- [ ] 该 PR 走 codex review 5 轮 hard gate(major 级,涉及切换 API 红线 + 验收路径全新 collection)
- [ ] 测试覆盖:`backend/services/acceptance*.py` ≥ 90%
- [ ] 静态检查:lint rule 阻止 `backend/services/acceptance*.py` import `backend.llm.*`
- [ ] 静态检查:lint rule 阻止 `WINDOW_TRADING_DAYS` 常量被覆写或在其他模块重新定义
- [ ] e2e 测试:模拟 60 日运行 + 中间 1 次 P0 + 1 次 reconciliation 冻结,验证窗口跳过逻辑

---

_本文件定稿,不再就地修改。如需调整,新建 `P0-6-amendment-{日期}-{原因}.md`。_
