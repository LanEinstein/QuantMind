# P0-9 — 第一阶段标的范围与频率(中等双层 watchlist + 中等严格排除 + 传统定时主 + 事件补充 + 严格 long-only / ETF 套利留 P1)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-9 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §13(推荐路线图 — long-only / 高流动性 / watchlist 内)+ §6(行情与资讯)+ §12(关键差距清单)|
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-9 |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.1 always-on simulation_auto 底座 + §1.6 多 Agent 辩论)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.2 InstructionSide={BUY,SELL,HOLD} + §1.3 单 limit_price)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(尤其 §1.1.1 max_total_positions=10 + §1.3.3 max_daily_new_instructions=5 + §1.3.4 universe.allowed_boards + §1.3.6 max_single_instruction_amount=50000)+ `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md`(尤其 §1.2 全 watchlist 30s 快照 + §1.5 DataQualityState 早返 + §1.4 MiroFish 双路径)|
| 派生 amendment | `docs/decisions/P0-7-amendment-2026-05-09-watchlist-exclusion-rules.md`(实施期产出;在 P0-7 universe.allowed_boards 基础上由 InstructionPlanBuilder 早返追加 watchlist_policy.yaml.exclusion_rules 早返;不修改 RiskEngine 14-check)|
| 替代       | `config/watchlist_policy.yaml`(当前 39 行 fast/slow 双频 + 空 default_codes 整体重写) |

## 决策摘要

QuantMind 第一阶段标的范围与频率采用 **中等双层 watchlist(10 主创板个股 + 3 宽基 ETF = 13 codes)+ 中等严格排除规则(新股≤30 / 次新≤180 / 日均成交额<2亿 / 单价>500)+ 传统 AI 量化定时为主 fast/slow 双频架构 + 事件驱动 1 单 cap 滑动补充 + 严格 long-only ETF 仅二级市场买卖** 架构:

1. **watchlist 规模**:13 codes 总量 = 10 只熟悉个股(主板 7 + 创业板 3)+ 3 只宽基 ETF(沪深300 510300 / 中证500 510500 / 创业板50 159949)。13 codes 与 P0-7 `max_total_positions=10` 持仓数 cap 留 30% 余量(允许 watchlist 内有候选未持仓);与 P0-8 全 watchlist 30s 快照量自然适配(13 codes × 24 ticks/min × 480 min/day = 149,760 条/日,MongoDB 单 collection 单日量级可控)。

2. **watchlist 排除规则**(在 P0-7 universe.allowed_boards 板块白名单 + ST 永禁基础上叠加;由 InstructionPlanBuilder 早返,不进 RiskEngine 14-check):
   - **新股排除**:上市 ≤30 个交易日的 codes 不允许加入 watchlist
   - **次新排除**:上市 ≤180 个交易日的 codes 不允许加入 watchlist
   - **低流动性排除**:过去 20 交易日日均成交额 < 2 亿元的 codes 不允许加入 watchlist
   - **高单价排除**:`limit_price × 100 股 > 50,000` 的 codes 自动排除(与 P0-7 `max_single_instruction_amount=50000` 对齐;单价 > 500 元即触发 — 注意 100 股是 A 股最小交易单位;贵州茅台 600519 单价 ~1700 元、长春高新 000661 单价 ~600 元等会被这条排除)
   - **ST 永禁**(P0-7 §1.3.4 已锁;此处复述边界,不重复实现)
   - **涨停同向 BUY / 跌停同向 SELL 永禁**(P0-7 §1.3.4 已锁;此处复述边界,不重复实现)

3. **调仓频率与日 5 单 cap 分配**(沿用 `config/watchlist_policy.yaml` fast/slow 双频架构,以传统 AI 量化排程为主 + MiroFish/事件驱动为补充):
   - **slow_pipeline**:每交易日 09:00 启动一次;对全 watchlist 13 codes 跑多 Agent 辩论(技术面 + 基本面 + 资讯)产出主候选;`max_debate_rounds=2`;`pipeline_timeout_seconds=900`(p95 ≤ 15 min)
   - **fast_pipeline**:每交易日 09:00 / 11:00 / 13:00 / 15:00 四次盘中;对 watchlist_policy.yaml `overrides` 标记为 fast 的子集跑技术信号检查 + 验证 slow 候选 valid_until;`max_debate_rounds=1`;`pipeline_timeout_seconds=480`(p95 ≤ 8 min)
   - **5 单 cap 分配**:`traditional_path_default_cap=4` + `event_path_reserved_cap=1`(MiroFish 事件驱动 severity ≥ HIGH 时使用)
   - **滑动机制**:`event_path_reserved_cap` 当日未被事件用名,15:00 收盘前可被 fast_pipeline 取用(避免事件预留 cap 形同浪费);`traditional_path_default_cap` 用满后即使有事件也不再额外发指令(5 单硬 cap 不可破)
   - **MiroFish 事件触发**(P0-8 §1.4):仅当 `MiroFishSimulator` 输出包含 severity ≥ HIGH 的拐点 + 影响 watchlist 内 codes 时才用 event_path_reserved_cap 生成 InstructionPlan;事件触发的 InstructionPlan 走相同 InstructionPlanBuilder + 14-check 与传统路径无差异
   - **关键定位**:**MiroFish 是加分项,不是核心**;平台底层是传统 AI 量化交易(多 Agent 辩论 + 技术 + 基本面),MiroFish 提供隐性因果链推演作为额外 evidence 与小概率事件捕捉;**严禁** 事件驱动路径占用主路径 cap

4. **方向与衍生限制**(严格 long-only + ETF 仅二级市场买卖):
   - InstructionSide 仅 `{BUY, SELL, HOLD}`(P0-3 §1.2 已锁);**不扩** SHORT/COVER/MARGIN/REPO 等方向
   - **永禁** 融资融券 / 做空 / 期货 / 期权 / 可转债 / 转融通 / 国债逆回购 / ETF 一二级市场套利
   - **SELL 仅可对已持仓 codes**(broker 持仓表查询;无持仓 SELL 在 RiskEngine check 5 已 fail-closed,此处复述)
   - **ETF 限制**:仅二级市场买卖单(BUY/SELL),与个股流程一致;不做申赎/套利
   - **ETF 套利预留 P1**:第一阶段不实现接口,P1 范围;实施 P1 时必须先走 `P0-9-amendment-{date}-etf-arbitrage-enable.md`

## 决策具体内容

### 1. watchlist 规模与组成

#### 1.1 总量与板块分布

总 codes 数量恒定 = **13**(10 个股 + 3 ETF)。

| 板块 | 数量 | 比例 | 与 P0-7 关联 |
|------|------|------|--------------|
| 沪市主板(`sh_main`,代码 60xxxx) | 4 | 30.8% | `allowed_boards` 白名单 |
| 深市主板(`sz_main`,代码 000xxx / 001xxx) | 3 | 23.1% | `allowed_boards` 白名单 |
| 创业板(`chuangye`,代码 30xxxx) | 3 | 23.1% | `allowed_boards` 白名单 |
| 宽基 ETF(`etf`) | 3 | 23.1% | `allowed_boards` 白名单 |
| 科创板(`688xxxx`) | 0 | — | **永禁**(P0-7 §1.3.4) |
| 北交所(`8xxxxx` / `92xxxx`) | 0 | — | **永禁**(P0-7 §1.3.4) |
| 可转债(`11xxxx` / `12xxxx`) | 0 | — | **永禁**(P0-7 §1.3.4) |

#### 1.2 ETF 选型(3 只宽基)

第一阶段固定 3 只:

| 代码 | 名称 | 跟踪标的 | 选型理由 |
|------|------|----------|----------|
| `510300` | 沪深300 ETF | 沪深300 指数 | 最大宽基 + 流动性极佳 + 与 P0-6 基准沪深300 自然对齐 |
| `510500` | 中证500 ETF | 中证500 指数 | 中盘宽基 + 与 510300 互补(覆盖中盘股波动) |
| `159949` | 创业板50 ETF | 创业板50 指数 | 创业板 beta 暴露 + 与 chuangye 个股相关性辅助分散 |

**禁** 行业 ETF / 主题 ETF / 跨境 ETF / 商品 ETF / 货币 ETF;新增 ETF 必须先走 `P0-9-amendment-{date}-etf-list-expand.md`。

#### 1.3 个股选型原则(用户预设清单 — 第一阶段不动态选股)

实施期填充 `config/watchlist_policy.yaml.default_codes` 时,用户根据以下原则手工选 10 只:

- **熟悉度**:用户已长期跟踪、对基本面/技术面有清晰认知
- **流动性**:过去 20 交易日日均成交额 ≥ 2 亿元(满足 §1.1.3 排除规则)
- **单价 ≤ 500 元**(满足 §1.1.4 高单价排除)
- **板块分布**:沪主 4 + 深主 3 + 创业板 3 = 10
- **行业分散**:不超过 3 只来自同一申万一级行业(避免行业集中风险)
- **市值范围**:不限定但建议大盘股(>500 亿市值)≥ 6 只 + 中盘股(200-500 亿)≤ 4 只

**watchlist 在 runtime 不可改**(继承 P0-7 §1.4 RiskConfig 全锁精神):
- `config/watchlist_policy.yaml.default_codes` 列表只能通过 `git diff` + `P0-9-amendment-{date}-watchlist-rotation.md` + 进程重启变更
- `backend/api/watchlist*.py` 只允许 `GET` 端点(添加/移除股票走 amendment 流程)
- 旧 `WatchlistService.add_stock` / `remove_stock` / `clear` 三个动态修改方法在新代码中标 deprecated + 仅留给 admin CLI 使用,不暴露在 FastAPI route

#### 1.4 watchlist 大小与 P0 系统衔接的数学验证

| 系统 | watchlist=13 codes 计算 | 阈值 | 判定 |
|------|--------------------------|------|------|
| P0-7 max_total_positions | 13 codes 中可同时持仓 ≤ 10 = 76.9% | ≤ 10 | ✅ 留 23.1% 余量(候选/观察) |
| P0-7 max_daily_new_instructions | 5 单 cap;13 codes 全部新建持仓最少 13 / 5 = 2.6 个交易日 | ≤ 5/日 | ✅ 单日不会用满 |
| P0-7 max_total_position_pct | 70% 总仓位上限;13 codes 中 10 持仓 × 7% = 70% | ≤ 70% | ✅ 平均仓位 7% / 单股可控 |
| P0-8 30s 快照量 | 13 codes × 120 ticks/h × 8h = 12,480 条/日(`stock_zh_a_spot_em` 单次返回全市场,过滤后存) | < 100K/日 | ✅ MongoDB 单 collection 量级可控 |
| P0-8 多源资讯 5 源 | 5 源 × ~50 条/h × 8h = 2000 条/日相关候选 → 按 watchlist 13 codes 过滤后 ~200 条/日 | < 1K/日 | ✅ MiroFish 输入域可控 |
| P0-6 数据缺失率 | 13 codes 30s 快照,缺失率 = 缺失条数 / (13×120×8) | ≤ 1% | ✅ ~125 条/日缺失阈值 |
| P0-6 指令完整率 | 5 单 cap × 45 交易日 = 225 单候选 — 指令完整率 95% = 11 单允许失败 | ≥ 95% | ✅ 与 cap 量级匹配 |

### 2. watchlist 排除规则

#### 2.1 排除规则四件套(在 P0-7 板块白名单 + ST 永禁 + 涨跌停同向永禁基础上叠加)

`config/watchlist_policy.yaml.exclusion_rules` 新增章节:

```yaml
exclusion_rules:
  # 第一阶段中等严格排除规则,P0-9 决策锁定
  ipo_min_trading_days: 30        # 新股排除:上市 ≤30 交易日不允许加入 watchlist
  sub_new_min_trading_days: 180   # 次新排除:上市 ≤180 交易日不允许加入 watchlist
  min_avg_amount_20d_yuan: 200000000  # 流动性排除:过去 20 交易日日均成交额 < 2 亿元不允许加入
  max_unit_price_yuan: 500.0      # 高单价排除:limit_price × 100 股 > 50,000 即触发(单价 > 500 元)
```

#### 2.2 排除规则的执行位置与 fail-closed 行为

**早返位置**(继承 P0-8 §1.5 DataQualityState 早返机制 — 不进 RiskEngine 14-check):

`backend/services/instruction_plan_builder.py::InstructionPlanBuilder.build()` 在装配 InstructionPlan 之前,**第三道早返**(顺序:数据质量早返 → 切换冻结早返 → ticket 冻结早返 → 熔断冻结早返 → **watchlist 排除早返** → RiskEngine 14-check):

```python
# 简化伪代码
def build(self, candidate: TradingCandidate) -> InstructionPlan:
    # ... 已有四道早返(P0-1/P0-5/P0-7/P0-8 各 1 道)...

    # P0-9 watchlist 排除早返 — 第五道
    exclusion_result = self._watchlist_exclusion_check(
        code=candidate.code,
        stock_meta=stock_meta,
        watchlist_policy=self._watchlist_policy,
    )
    if not exclusion_result.passed:
        return self._build_hold_plan(
            candidate=candidate,
            reason=f"watchlist_exclusion: {exclusion_result.reason}",
            evidence_ids=[f"WATCHLIST-{exclusion_result.rule_id}-{...}"],
        )

    # ... RiskEngine 14-check 进行 ...
```

**判定逻辑**:

| 规则 ID | 判定字段来源 | 通过条件 | 失败动作 |
|---------|------------|----------|----------|
| `WATCHLIST-IPO-30D` | `stock_meta.ipo_date`(由 `backend/data/stock_metadata.py` 提供)| `now - ipo_date ≥ 30 trading days` | 早返降级 HOLD |
| `WATCHLIST-SUBNEW-180D` | 同上 | `now - ipo_date ≥ 180 trading days` | 早返降级 HOLD |
| `WATCHLIST-LIQUIDITY-2E` | `watchlist_market_snapshots` 倒序 20 交易日 amount 字段 | `mean(amount_20d) ≥ 2e8` | 早返降级 HOLD |
| `WATCHLIST-HIGHPRICE-500` | `candidate.limit_price`(由 InstructionPlanBuilder 决策得出)| `limit_price × 100 ≤ 50,000` | 早返降级 HOLD |

**fail-closed**:任一字段缺失(stock_meta=None / 流动性数据不足 20 交易日 / limit_price=None)→ 降级 HOLD,**不允许"缺数据时通过"的乐观回退**(继承 P0-7 §1.4 fail-closed 精神)。

#### 2.3 与 P0-7 universe check 11/13/14 的边界

**P0-7 universe check**(在 RiskEngine 14-check 内):
- check 11(board 白名单)— 板块代码识别
- check 13(ST 禁止)— ST 标识识别
- check 14(涨跌停同向禁止)— 涨跌停状态识别

**P0-9 watchlist 排除**(在 InstructionPlanBuilder 早返,**不进** RiskEngine):
- IPO ≤ 30D / SUBNEW ≤ 180D / LIQUIDITY < 2E / HIGHPRICE > 500

**关键边界**:**P0-9 排除 ⊂ P0-7 universe**(P0-9 是更严格子集)。理论上 watchlist 只装 13 个用户预设 codes,所有 codes 必然通过 P0-7 universe check;但 watchlist 排除规则在 InstructionPlanBuilder 层 **预防性早返**,捕捉运行时 stock_meta 字段变化(例如某 code 后来被特别处理变 ST、单价飙升过 500、流动性骤降)。

#### 2.4 排除规则的固化与变更流程

**runtime 不可改**:
- `config/watchlist_policy.yaml.exclusion_rules` 阈值在 runtime 不可变;`backend/api/watchlist*.py` 只允许 `GET`
- 调整任一阈值必须先走 `P0-9-amendment-{date}-exclusion-threshold-adjustment.md`

**变更流程示例**(假设未来需要把流动性阈值放宽到 1 亿元):
1. 写 `docs/decisions/P0-9-amendment-2026-XX-XX-liquidity-threshold-relax.md` 说明:为何放宽 / 影响哪些 watchlist codes / 风险评估 / 与 P0-6 数据缺失率影响
2. `git diff config/watchlist_policy.yaml`(改 `min_avg_amount_20d_yuan: 200000000` → `100000000`)
3. 进程重启
4. 同步更新 P0-9 决策文档顶部加 `> 已被 amendment-XXX 修订` 提示

### 3. 调仓频率与 5 单 cap 分配

#### 3.1 fast/slow 双频架构(沿用 watchlist_policy.yaml)

`config/watchlist_policy.yaml` 当前 fast/slow 配置在 P0-9 实施期保留并扩展:

```yaml
fast:
  cron: "0 9,11,13,15 * * mon-fri"   # 每交易日 4 次盘中
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480       # p95 ≤ 8 min
  default_codes: []                    # P0-9 实施期填充

slow:
  cron: "0 9 * * mon-fri"              # 每交易日 09:00 一次
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900        # p95 ≤ 15 min
  default_codes: []                    # P0-9 实施期填充

# P0-9 新增:5 单 cap 分配策略
cap_allocation:
  total_daily_cap: 5                   # 与 P0-7 max_daily_new_instructions=5 锁定
  traditional_path_default_cap: 4      # 传统量化路径(slow + fast)默认占 4
  event_path_reserved_cap: 1           # MiroFish 事件驱动预留 1(severity ≥ HIGH)
  reserved_cap_release_time: "15:00"   # 收盘前 event 未用,traditional 可取用

# P0-9 新增:子集 watchlist 与持仓数关系
watchlist_size: 13                     # 10 个股 + 3 ETF;runtime 不可改
max_positions_in_watchlist_pct: 0.77   # 13 codes 中持仓数 ≤ 10 = 76.9%(P0-7 max_total_positions)
```

#### 3.2 slow_pipeline 工作流(主路径 — 传统 AI 量化)

每交易日 09:00:

1. **数据准备**:从 `watchlist_market_snapshots` 读 watchlist 13 codes 的 T-1 收盘 + T 日开盘前数据;从 `news_collection` 读最近 24h 多域 5 源资讯
2. **多 Agent 辩论**(P0-1 §1.6 已锁,`max_debate_rounds=2`):
   - 技术面 Agent:技术信号检查(均线 / MACD / KDJ / 量价)
   - 基本面 Agent:财报 / 估值 / 行业景气
   - 资讯 Agent:多域 5 源资讯 + MiroFish 盘后复盘 evidence
   - 风控 Agent:持仓约束 + 集中度
   - 复审 Agent:跨视角合并
3. **候选输出**:产出 ≤ traditional_path_default_cap=4 个 BUY/SELL 候选 + N 个 HOLD 候选
4. **InstructionPlanBuilder.build()**:对每个 BUY/SELL 候选过五道早返(数据 / 切换 / ticket / 熔断 / **watchlist 排除**) + RiskEngine 14-check
5. **路由**:通过即写入 `instruction_plans` collection(P0-1 §1.5)+ 飞书发送(若 feishu_on)

**timeout**:`pipeline_timeout_seconds=900`(p95 ≤ 15 min)— 超时由 `asyncio.wait_for` 强制 kill,InstructionPlan 未发布即丢弃。

#### 3.3 fast_pipeline 工作流(副路径 — 盘中验证 + 技术信号)

每交易日 09:00 / 11:00 / 13:00 / 15:00 四次:

**职责**:
- 验证 slow_pipeline 09:00 候选的 valid_until 状态(若 valid_until 仍有效但行情变化,P0-3 §1.2.2 触发 EXPIRED)
- 跑 `overrides.fast` 子集 codes 的快速技术信号检查(均线突破 / 量能放大 / 涨停板 attack)
- 不跑多 Agent 完整辩论(`max_debate_rounds=1`)

**默认 fast 子集**:`config/watchlist_policy.yaml.overrides` 由用户实施期手动指定 4-5 只技术面状态明显的个股(如 `300750: fast` `601318: slow` 模式);未指定的 codes 默认 slow

**路由**:通过 traditional_path_default_cap 剩余配额;若 slow_pipeline 已发 4 单,fast_pipeline 不再发新;若 slow 发 2 单且 event 未用,fast 可发 ≤ 2 单

#### 3.4 event 路径触发(MiroFish 事件驱动)

**触发条件**(P0-8 §1.4):

```python
# backend/services/event_router.py 伪代码
if mirofish_output.severity >= Severity.HIGH \
   and mirofish_output.affected_codes & set(watchlist):
    if cap_allocator.event_path_remaining_cap > 0:
        candidate = build_candidate_from_mirofish(mirofish_output)
        plan = instruction_plan_builder.build(candidate)
        if plan.is_routable():
            cap_allocator.consume(path="event", count=1)
```

**滑动机制**:15:00 前 `event_path_reserved_cap` 未消费,可由 fast_pipeline 14:30+ 取用:

```python
def remaining_cap_for_traditional() -> int:
    if now() >= "15:00":
        return total_daily_cap - traditional_consumed - event_consumed
    if now() >= "14:30" and event_consumed == 0:
        return total_daily_cap - traditional_consumed  # event 预留可取用
    return traditional_path_default_cap - traditional_consumed
```

#### 3.5 关键定位红线 — MiroFish 是加分项不是核心

**LLM/event-driven 路径不得占用主路径 cap** —— 严禁通过把 MiroFish 输出包装为 traditional candidate 绕过 event_path_reserved_cap=1 限制。
- `backend/services/instruction_plan_builder.py` 必须显式标记 `path: Literal["traditional", "event"]`,`cap_allocator` 按 `path` 字段独立计数
- 实施期 lint rule:任何把 MiroFish severity 字段映射进 traditional candidate evidence 但 path=traditional 的代码即红线违规
- 单元测试覆盖:event_path_reserved_cap=1 用满后,即使 MiroFish 再触发 severity=CRITICAL 也不再发新 InstructionPlan(只写 evidence_collection)

### 4. 方向与衍生限制(严格 long-only)

#### 4.1 InstructionSide 边界

P0-3 §1.2 已锁 `InstructionSide ∈ {BUY, SELL, HOLD}`;P0-9 不扩展。

**未来扩展路径**(都需 amendment):
- `SHORT` / `COVER`(融券做空)— 违 audit §13 推荐 long-only,**永禁**
- `MARGIN_BUY`(融资买入)— 违 audit §13 推荐保守,**永禁**
- `REVERSE_REPO`(国债逆回购)— 涉新 InstructionSide,**永禁**(amendment 流程已无意义)
- `ETF_SUBSCRIBE` / `ETF_REDEEM`(ETF 一二级套利)— P1 范围,需 `P0-9-amendment-{date}-etf-arbitrage-enable.md`

#### 4.2 broker.allowed_instruments 边界

`backend/broker/mock_broker.py` 在 P0-9 实施期新增白名单校验:

```python
ALLOWED_INSTRUMENTS = frozenset({
    # 个股(P0-7 universe.allowed_boards 子集)
    "sh_main",      # 沪主板
    "sz_main",      # 深主板
    "chuangye",     # 创业板
    # ETF(宽基)
    "etf",          # 仅二级市场买卖单(BUY/SELL),不做申赎
})

def submit_order(self, plan: InstructionPlan) -> SimulationOrder:
    instrument = classify_instrument(plan.code)
    if instrument not in ALLOWED_INSTRUMENTS:
        raise ForbiddenInstrumentError(f"{plan.code} ({instrument}) not in allowed_instruments")
    # ... 继续撮合
```

#### 4.3 SELL 仅可对已持仓的强制约束

**位置**:RiskEngine check 5(`_check_sell_only_for_holdings`)— 已存在(P0-7 14-check 内);P0-9 不重复实现,只复述边界。

**约束逻辑**:
- SELL InstructionPlan.code 必须在 `MockBroker.positions` 中存在 + position.volume ≥ plan.volume
- 不允许 "T+0 卖空"(中国 A 股 T+1 也禁止 — 当日买入次日才可卖出)— RiskEngine check 5 已含此规则
- 不允许 "卖空 ETF"(`MockBroker.short_position` 永远空字典)

#### 4.4 ETF 套利 P1 预留接口设计

第一阶段 **不实现** 但预留:
- `backend/broker/etf_arbitrage_stub.py` — 空模块,只含 `class ETFArbitrageStub: NotImplementedError`
- `config/broker.yaml.etf_arbitrage_enabled: false` — 永锁 false,启用必须先走 amendment
- `backend/api/etf_arbitrage*.py` — 不创建 router

P1 启用 ETF 套利时:
1. 写 `docs/decisions/P0-9-amendment-{date}-etf-arbitrage-enable.md` 说明 IOPV 跟踪 / 申赎清单 / 一二级价差套利逻辑 / 风控扩展
2. 扩 InstructionSide 添加 `ETF_SUBSCRIBE` / `ETF_REDEEM`(P0-3 amendment)
3. 扩 RiskEngine 添加 ETF 套利专用 check(可能扩到 16-check 或 18-check;P0-7 amendment)
4. 实施期改 `etf_arbitrage_stub.py` → `etf_arbitrage.py` 真实实现

### 5. watchlist_policy.yaml 完整目标结构

P0-9 实施期 `config/watchlist_policy.yaml` 整体重写为:

```yaml
# config/watchlist_policy.yaml — P0-9 锁定结构
#
# Two cron jobs share one process:
# - slow_pipeline: 09:00 daily, full watchlist multi-Agent debate
# - fast_pipeline: 09/11/13/15 four times daily, fast technical check
#
# Daily 5-instruction cap split: traditional_path=4 + event_path_reserved=1
# Event reserved cap can be released to traditional after 14:30 if unused.
#
# All thresholds runtime-immutable. Changes require P0-9-amendment-{date}-*.md.

policy_version: 2
last_updated: 2026-05-09
locked_decision: P0-9

# === 双频排程 ===
fast:
  cron: "0 9,11,13,15 * * mon-fri"
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480
  default_codes: []

slow:
  cron: "0 9 * * mon-fri"
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900
  default_codes: []

# === watchlist 总量约束(P0-9 §1.1)===
watchlist:
  total_codes: 13
  composition:
    sh_main: 4    # 沪市主板个股 4 只
    sz_main: 3    # 深市主板个股 3 只
    chuangye: 3   # 创业板个股 3 只
    etf: 3        # 宽基 ETF 3 只(510300 + 510500 + 159949)
  default_codes: []      # 实施期用户填 13 个 codes
  default_category: slow  # 未在 overrides 的默认 slow

# === 必备 ETF 名单(P0-9 §1.2)===
required_etfs:
  - code: "510300"
    name: "沪深300 ETF"
    tracking: "沪深300指数"
  - code: "510500"
    name: "中证500 ETF"
    tracking: "中证500指数"
  - code: "159949"
    name: "创业板50 ETF"
    tracking: "创业板50指数"

# === 排除规则(P0-9 §2)===
exclusion_rules:
  ipo_min_trading_days: 30
  sub_new_min_trading_days: 180
  min_avg_amount_20d_yuan: 200000000
  max_unit_price_yuan: 500.0

# === 5 单 cap 分配(P0-9 §3.1)===
cap_allocation:
  total_daily_cap: 5
  traditional_path_default_cap: 4
  event_path_reserved_cap: 1
  reserved_cap_release_time: "14:30"

# === 方向限制(P0-9 §4)===
direction_policy:
  long_only: true
  forbidden_sides:
    - SHORT
    - COVER
    - MARGIN_BUY
    - REVERSE_REPO
    - ETF_SUBSCRIBE
    - ETF_REDEEM
  etf_arbitrage_enabled: false  # 永锁;P1 启用走 amendment

# === fast/slow 子集覆盖(P0-9 §3.3)===
overrides: {}    # 实施期填(例如 "300750": fast)

# === 关键关系(只读校验式;runtime 校验入口需引用)===
constraints:
  watchlist_size_must_equal: 13
  watchlist_etf_count_must_equal: 3
  total_daily_cap_must_equal_p0_7: 5
  long_only_must_be_true: true
```

### 6. watchlist 与 5 单 cap 数学一致性表

| 场景 | watchlist 13 codes 行为 | 5 单 cap 消费 | 是否合规 |
|------|--------------------------|----------------|----------|
| 09:00 slow 产出 4 BUY 候选(全过 14-check)+ 0 SELL | traditional 全发 4 单 | t=4, e=0 | ✅ |
| 09:00 slow 产出 4 候选 + 11:00 fast 产出 1 候选 | traditional 5 单(slow 4 + fast 1) | t=5, e=0 | ✅ 但 event 当日不可发 |
| 09:00 slow 产出 4 + 13:00 MiroFish 事件 severity=HIGH | traditional 4 + event 1 | t=4, e=1 | ✅ 满 cap |
| 09:00 slow 产出 5 候选(超 traditional cap)| 仅取前 4 通过 | t=4, e=0 | ✅ 第 5 候选拒绝 |
| 09:00 slow 产出 3 + 14:35 event 仍未触发 + 14:35 fast 产出 1 | event 释放给 traditional;t=4 | t=4, e=0 | ✅ 滑动机制 |
| 09:00 slow 产出 3 + 13:00 MiroFish severity=HIGH + 14:35 fast 1 | t=3, e=1, fast 14:35 +1 → t=4, e=1 | t=4, e=1 | ✅ |
| 全日无 event + slow 4 + fast 14:35 1 | t=5(slow 4 + fast 1 from 14:30 release)| t=5, e=0 | ✅ |
| 全日 MiroFish 4 次 severity=HIGH | event 第 1 次发,后 3 次仅写 evidence | t=0, e=1 | ✅ event_path 硬 cap=1 |

### 7. 第一阶段 watchlist 选股建议(用户参考 — 不是决策硬约束)

实施期用户选 10 只熟悉个股时,以下作为参考(非锁定):

| 板块 | 候选示例 | 选型理由 | 注意事项 |
|------|----------|----------|----------|
| 沪主板 | 浦发银行 600000 / 中国平安 601318 / 招商银行 600036 / 长江电力 600900 / 中国神华 601088 / 工商银行 601398 / 兴业银行 601166 | 大盘流动性 + 白马股 | 单价均 < 100 元;过 §2 排除 |
| 沪主板(技术消费) | 海康威视 002415 不属沪主板 / 万科A 000002 不属沪主板 / 双汇发展 000895 不属沪主板 | — | 注意:002 / 000 是深主板 |
| 深主板 | 万科A 000002 / 五粮液 000858 / 美的集团 000333 / 平安银行 000001 / 双汇发展 000895 / 海康威视 002415 / 比亚迪 002594 | 大盘流动性 + 白马股 | 单价均 < 500 元;过 §2 排除 |
| 创业板 | 宁德时代 300750(单价 ~250 元 ✅)/ 迈瑞医疗 300760 / 阳光电源 300274 / 汇川技术 300124 / 智飞生物 300122 / 爱尔眼科 300015 | 创业板龙头 + 流动性佳 | 个别单价 > 500 元的不可入(如长春高新 000661 ~600 元 — 注意属深主板;如歌尔股份 002241 单价 ~25 元 ✅)|

**禁选示例**(用户应避免):
- 贵州茅台 600519(单价 ~1700 元 — 触 §2 高单价排除)
- 长春高新 000661(单价 ~600 元 — 触 §2 高单价排除)
- 任何 ST / *ST 股(P0-7 永禁)
- 任何 688xxx 科创板(P0-7 永禁)
- 任何 8/92xxxx 北交所(P0-7 永禁)
- 任何 11/12xxxx 可转债(P0-7 永禁)
- 任何近 30 交易日内 IPO 新股(§2 永禁)
- 任何过去 20 交易日日均成交额 < 2 亿的低流动性股(§2 永禁)

## 红线 / 边界(立即生效硬约束)

P0-9 在 P0-1 / P0-3 / P0-7 / P0-8 既有红线基础上叠加,共 **17 条** P0-9 专属红线:

1. **watchlist 总 codes 数恒定 = 13**:`config/watchlist_policy.yaml.watchlist.total_codes` 必须为 13(10 个股 + 3 ETF);改动必须先走 `P0-9-amendment-{date}-watchlist-size.md`;实施期 lint rule 校验 `len(default_codes) == 13`

2. **watchlist 板块组成恒定 = sh_main 4 / sz_main 3 / chuangye 3 / etf 3**:任一板块数量改动必须先走 amendment;`backend/data/watchlist.py::validate_composition()` 启动时强校验

3. **3 只必备 ETF 锁定 = 510300 + 510500 + 159949**:任何添加/替换 ETF 必须先走 `P0-9-amendment-{date}-etf-list-expand.md`;严禁第一阶段加行业 ETF / 主题 ETF / 跨境 ETF / 商品 ETF / 货币 ETF

4. **科创板(688) / 北交所(8x/92x) / 可转债(11x/12x) 在 watchlist 中永禁**(继承 P0-7 §1.3.4;此处复述边界);任何代码尝试在 watchlist 中加入这三类 codes 即红线违规

5. **watchlist 在 runtime 不可改**(继承 P0-7 §1.4 RiskConfig 全锁精神):`config/watchlist_policy.yaml.watchlist.default_codes` 列表只能通过 git diff + amendment + 进程重启变更;`backend/api/watchlist*.py` 只允许 `GET` 端点;旧 `WatchlistService.add_stock` / `remove_stock` / `clear` 方法标 deprecated 不暴露在 FastAPI route

6. **watchlist 排除规则四件套阈值锁定**:`ipo_min_trading_days=30` / `sub_new_min_trading_days=180` / `min_avg_amount_20d_yuan=200000000` / `max_unit_price_yuan=500.0`;调整任一阈值必须先走 `P0-9-amendment-{date}-exclusion-threshold-adjustment.md`

7. **watchlist 排除规则在 InstructionPlanBuilder 早返**:**不进** RiskEngine 14-check;`backend/risk/` 严禁实现 IPO/SUBNEW/LIQUIDITY/HIGHPRICE 排除逻辑;实施期 lint rule 阻止 RiskEngine `_check_*` 方法引用 `ipo_date` / `avg_amount_20d` / `limit_price * 100`

8. **watchlist 排除规则数据缺失即 fail-closed 降级 HOLD**:`stock_meta.ipo_date=None` / 流动性数据不足 20 交易日 / `limit_price=None` 即降级 HOLD;不允许"缺数据时通过"的乐观回退(继承 P0-7 §2 红线 13 fail-closed 精神)

9. **5 单 cap 分配锁定**:`total_daily_cap=5`(继承 P0-7)/ `traditional_path_default_cap=4` / `event_path_reserved_cap=1`(P0-9 锁);调整 cap 分配必须先走 amendment;`event_path_reserved_cap > 1` 永禁(继承 §3.5 MiroFish 不夺主精神)

10. **MiroFish 事件路径不得占用 traditional 主路径 cap**:`backend/services/instruction_plan_builder.py` 必须显式标记 `path: Literal["traditional", "event"]`;`cap_allocator` 按 path 字段独立计数;实施期 lint rule 阻止把 MiroFish severity 映射到 path=traditional 的 candidate

11. **event_path_reserved_cap=1 用满后即使 severity=CRITICAL 也不再发新 InstructionPlan**:仅写 `evidence_collection`;实施期单元测试强覆盖

12. **fast/slow 双频架构锁定**:`fast.cron="0 9,11,13,15 * * mon-fri"` / `slow.cron="0 9 * * mon-fri"`;改 cron 必须先走 amendment;`pipeline_timeout_seconds` 阈值(fast 480 / slow 900)同样锁定

13. **InstructionSide 永锁 {BUY, SELL, HOLD}**(继承 P0-3 §2 红线 2;此处复述边界);任何尝试加 SHORT / COVER / MARGIN_BUY / REVERSE_REPO / ETF_SUBSCRIBE / ETF_REDEEM 即红线违规;P1 加 ETF 套利必须先走 `P0-9-amendment-{date}-etf-arbitrage-enable.md`(届时还要扩 P0-3 InstructionSide / P0-7 RiskEngine check)

14. **`broker.allowed_instruments` 永锁 {sh_main, sz_main, chuangye, etf}**:`backend/broker/mock_broker.py::ALLOWED_INSTRUMENTS` 用 `frozenset` 不可变;严禁 runtime 修改;新增板块/工具必须先走 amendment

15. **SELL 仅可对已持仓 codes**(继承 RiskEngine check 5;此处复述边界);MockBroker 永远不维护 short_position 字段(`MockBroker.short_position={}` 永远空字典)

16. **ETF 套利 P1 预留接口永锁 disabled**:`config/broker.yaml.etf_arbitrage_enabled=false`;`backend/broker/etf_arbitrage_stub.py` 仅含 `class ETFArbitrageStub: NotImplementedError`;`backend/api/etf_arbitrage*.py` 不创建 router;启用必须走 amendment(届时改 stub → 真实实现 + 扩 InstructionSide + 扩 RiskEngine check)

17. **`config/watchlist_policy.yaml` `WatchlistPolicy` 配置类是 frozen Pydantic v2 模型**(继承 P0-3 / P0-4 / P0-5 / P0-6 / P0-7 / P0-8 immutability 原则):`model_config = ConfigDict(frozen=True)`;就地 mutation 红线违规

## 影响范围(实施期改动清单)

### 1. 配置层(YAML)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `config/watchlist_policy.yaml` | 整体重写为 §5 完整目标结构;增 `watchlist` / `required_etfs` / `exclusion_rules` / `cap_allocation` / `direction_policy` / `constraints` 章节 | P0 |
| `config/broker.yaml` | 新增 `etf_arbitrage_enabled: false` 永锁字段 | P0 |
| `config/risk.yaml` | 不改(P0-7 已锁;仅做 watchlist 排除规则不放在 risk.yaml 而在 watchlist_policy.yaml 的边界注释) | P0 |
| `config/data_sources.yaml` | 不改(P0-8 已锁;watchlist 大小变化由 P0-9 在 watchlist_policy.yaml 主导,data_sources.yaml 只读 watchlist_policy.yaml 推断 30s 快照范围) | P0 |

### 2. 数据模型与服务层(Python)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `backend/data/watchlist.py` | 重写 `WatchlistService`:新增 `validate_composition` 启动时校验;`add_stock` / `remove_stock` / `clear` 标 deprecated;新增 `get_watchlist_with_metadata`(返回 `(code, board, ipo_date, avg_amount_20d, current_price)` 元组列表)| P0 |
| `backend/data/watchlist_policy.py`(新建)| frozen Pydantic v2 `WatchlistPolicy` schema 模型;`validate_constraints()` 校验 §5 `constraints` 章节 | P0 |
| `backend/data/stock_metadata.py` | 扩 `StockMetadata` 加 `ipo_date: date` / `avg_amount_20d: float` 字段(继承 P0-7 §1.5 / P0-8 字段);新增 `is_ipo_within_days(n)` / `is_subnew_within_days(n)` / `is_low_liquidity(threshold_yuan)` 三个判定函数 | P0 |
| `backend/services/instruction_plan_builder.py` | 加第五道早返 `_watchlist_exclusion_check`;新增 `path: Literal["traditional", "event"]` 字段;新增 `cap_allocator` 引用 | P0 |
| `backend/services/cap_allocator.py`(新建)| 5 单 cap 分配状态机;`event_path_reserved_cap` 滑动释放逻辑(14:30 + event_consumed=0 → 释放);独立计数 traditional / event paths | P0 |
| `backend/services/event_router.py`(新建)| MiroFish severity ≥ HIGH 路由器;`affected_codes & watchlist != ∅` 检查 | P0 |
| `backend/broker/mock_broker.py` | 新增 `ALLOWED_INSTRUMENTS` frozenset 校验;`submit_order` 启动时调 `classify_instrument(plan.code)` | P0 |
| `backend/broker/etf_arbitrage_stub.py`(新建) | 空 stub 类,`NotImplementedError` | P0 |
| `backend/risk/engine.py` | **不改 14-check**;只在 docstring 注明 P0-9 排除规则在 InstructionPlanBuilder 早返,不在 RiskEngine | — |

### 3. API 层(FastAPI)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `backend/api/watchlist*.py` | 仅 `GET /api/watchlist` 端点(返回 13 codes + metadata);删除 `POST` / `DELETE` / `PUT` 端点(若存在);旧 `add_stock` / `remove_stock` 路由标 410 Gone | P0 |
| `backend/api/etf_arbitrage*.py` | **不创建** | — |

### 4. 调度层

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `backend/data/scheduler.py` | 读 `watchlist_policy.yaml` `fast` / `slow` cron 注册任务;`fast_pipeline` / `slow_pipeline` 调用 InstructionPlanBuilder | P0 |
| `backend/agents/pipelines/fast_pipeline.py` | `max_debate_rounds=1`;`pipeline_timeout_seconds=480` | P0 |
| `backend/agents/pipelines/slow_pipeline.py` | `max_debate_rounds=2`;`pipeline_timeout_seconds=900` | P0 |
| `backend/scheduler/cap_reset.py`(新建)| 每交易日 00:00 重置 `cap_allocator` 计数器;15:30 收盘后写入 `decision_ledger` | P0 |

### 5. 前端(Vue)

| 文件 | 改动 | 优先级 |
|------|------|--------|
| `frontend/src/views/WatchlistView.vue` | 显示 13 codes(只读);标记每 code 的 board / ETF / IPO date / 流动性;若有排除规则触发,标黄 + 显示原因 | P1 |
| `frontend/src/views/InstructionCenterView.vue` | 显示当日 cap 消费 traditional/event 计数;实时更新 | P1 |
| `frontend/src/views/MiroFishEventView.vue` (新建)| 事件触发时间线 + severity + 是否消费 event_path_cap | P1 |

### 6. 测试

| 路径 | 新增 / 改动 | 优先级 |
|------|--------------|--------|
| `tests/data/test_watchlist_policy.py`(新建) | 校验 frozen Pydantic + constraints 校验函数 + 13 codes 总量约束 | P0 |
| `tests/services/test_instruction_plan_builder_watchlist_exclusion.py`(新建)| 五道早返中第五道(IPO/SUBNEW/LIQUIDITY/HIGHPRICE)+ fail-closed | P0 |
| `tests/services/test_cap_allocator.py`(新建) | 5 单 cap 分配 + 滑动释放 + event_path 硬 cap=1 | P0 |
| `tests/services/test_event_router.py`(新建)| MiroFish 事件 severity ≥ HIGH 路由 + watchlist 内过滤 + event_consumed=1 后再触发不发新单 | P0 |
| `tests/broker/test_mock_broker_allowed_instruments.py`(新建)| ALLOWED_INSTRUMENTS frozenset 校验 + 拒绝 688/92x/11x | P0 |
| `tests/integration/test_watchlist_full_flow.py`(新建)| 13 codes watchlist + slow 09:00 跑全 watchlist + cap 4 + event severity=HIGH 触发 → cap 5 满 + 后续 event 仅写 evidence | P0 |

### 7. 文档

| 路径 | 改动 | 优先级 |
|------|------|--------|
| `CLAUDE.md` | §1.3 P0-9 进度行 + §2.1 P0-9 ✅ + §3.1 第 19 块红线(P0-9 17 条) | P0 |
| `docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md` | 本文件 | ✅ |
| `docs/decisions/P0-7-amendment-2026-05-09-watchlist-exclusion-rules.md`(实施期产出) | 在 P0-7 universe.allowed_boards 基础上由 InstructionPlanBuilder 早返追加 watchlist exclusion;不修改 RiskEngine 14-check | P0 |
| `docs/quantmind_owner_decision_points_2026-05-07.md` | §P0-9 表格状态 ⏳ → ✅ + 链接本文件 | P0 |
| `MEMORY.md` | 加 P0-9 索引项 | P0 |
| `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_9.md`(新建)| P0-9 锁定要点 | P0 |

## 决策依据

### 1. audit 引用

- `docs/quantmind_project_audit_2026-05-07.md` §13 推荐:**只做 watchlist + long-only + 高流动性 A 股或 ETF + 不做高频 + 每日少量指令** — 决定 P0-9 取严格中等保守组合
- `docs/quantmind_project_audit_2026-05-07.md` §6.2 揭示:30s 快照只采三大指数 — 决定 watchlist 大小不能太大(P0-8 30s 全 watchlist 快照负担)
- `docs/quantmind_project_audit_2026-05-07.md` §12 关键差距清单:数据质量 / 资讯 / 复盘均要求 watchlist 内标的 — 决定 watchlist 边界硬性
- `docs/quantmind_project_audit_2026-05-07.md` §10 旧前端 suggest/confirm/auto 不再适用 — 决定 watchlist 工作流走新前端 InstructionCenter

### 2. 用户决策记录

**Q1 watchlist 规模与组成(2026-05-09 第一轮)**:
- 用户选 "中等 + 双层(推荐)" = 10 只熟悉个股(主板 7 + 创业板 3) + 3 只宽基 ETF(沪深300 510300 / 中证500 510500 / 创业板50 159949)= 13 codes
- 拒绝:保守 + 纯主板(9 codes 太少 + 创业板 P0-7 已许可应启用)/ 较大 + 行业 ETF 池(20 codes 与 30s 快照 + 5 单 cap 冲突)/ 动态选股(违 P0-7 RiskConfig 全锁精神)

**Q2 排除规则严格度**:
- 用户选 "推荐(中等严格)" = 新股 ≤30 / 次新 ≤180 / 流动性 < 2 亿 / 单价 > 500
- 拒绝:极严格(180 天新股 + 5 亿流动性 + 200 元单价 — 可能 watchlist 选不出股)/ 较宽松(仅 30 天 + 1000 元 — 不足以排除高风险标的)/ 仅 P0-7 既定(无新股次新流动性排除 — 风险过大)

**Q3 调仓频率与日 5 单 cap(2026-05-09 第二轮 — 第一轮被拒绝重新选项)**:
- 用户第一轮拒绝事件触发主导框架,澄清 "MiroFish 是加分项不是核心,平台底层是传统 AI 量化交易,不能抛弃成熟传统量化逻辑全部追求资讯分析"
- 第二轮选 "传统定时主 + 事件补充(推荐)" = 沿用 fast/slow 双频 + traditional_path_cap=4 + event_path_reserved=1 + 滑动机制
- 拒绝:传统定时独霸(MiroFish 价值被压缩)/ 周度传统 + 日内事件(slow_pipeline 改周度违 always-on 精神)/ fast/slow 不共享 cap(违 P0-7 5 单硬 cap)

**Q4 方向与衍生限制**:
- 用户选 "long-only + ETF 套利留 P1"
- 拒绝:严格 long-only 但不留 ETF 套利接口(P1 未来扩需 amendment 改太多)/ + 国债逆回购(扩 InstructionSide 与 P0-3 极简精神冲突)/ 不强制 long-only(违 audit §13 推荐)

### 3. 与既有决策的衔接

- **P0-1**(simulation_auto always-on 底座):watchlist 13 codes always-on,即使 feishu_off 模式 slow_pipeline 也每日 09:00 跑(MockBroker 留痕)
- **P0-3**(InstructionPlan 严格 schema):InstructionSide ∈ {BUY, SELL, HOLD},P0-9 不扩展;`evidence_ids` 加 `WATCHLIST-{rule_id}-...` 前缀(若需扩 P0-8 五前缀约定,走 amendment)
- **P0-6**(45 交易日验收):指令完整率 95% / 数据缺失率 1% — watchlist 13 codes 数学验证(§1.4)显示与 P0-6 阈值兼容
- **P0-7**(风险红线 + 14-check):universe.allowed_boards 决定 watchlist 板块边界;P0-9 排除规则在 InstructionPlanBuilder 早返,不挤 14-check;5 单 cap 由 P0-7 锁定
- **P0-8**(数据与资讯 + MiroFish 双路径):watchlist 13 codes 决定 30s 快照量;MiroFish 事件触发使用 event_path_reserved_cap=1 滑动机制
- **派生 P0-7 amendment**:实施期产出 `P0-7-amendment-2026-05-09-watchlist-exclusion-rules.md` 说明 watchlist 排除规则在 InstructionPlanBuilder 而非 RiskEngine 的边界

### 4. 代码事实抽检

- `config/watchlist_policy.yaml` 当前 39 行 fast/slow 双频架构 + `default_codes=[]` 空列表 — 需 P0-9 实施期填充
- `backend/data/watchlist.py` 仅 60 行 MongoDB CRUD wrapper — 需扩 `validate_composition` + 重写 add/remove/clear
- `backend/services/instruction_plan_builder.py`(实施期新建)— P0-1 / P0-7 / P0-8 已规划早返链;P0-9 加第五道
- `backend/broker/mock_broker.py`(P0-5 已实现)— 需加 `ALLOWED_INSTRUMENTS` frozenset

### 5. 红线动机

| 红线编号 | 动机 |
|----------|------|
| 1 / 2 | watchlist 总量与板块组成稳定性 — runtime 改动会破 P0-8 30s 快照负载假设与 P0-7 持仓数 cap 假设 |
| 3 | ETF 选型稳定性 — 行业 ETF 引入会扩 universe 风险敞口 + 改变 30s 快照负载 |
| 4 | 继承 P0-7 universe 永禁;此处复述以防实施期混淆 |
| 5 | 继承 P0-7 RiskConfig 全锁精神 — runtime 改 watchlist 会绕过决策门禁 |
| 6 | 排除规则阈值的稳定性 — 改阈值需要看回测影响,不能 hot-reload |
| 7 / 8 | 排除规则归 InstructionPlanBuilder 早返而非 RiskEngine — 避免再次扩 14-check + 与 P0-8 早返机制并列 |
| 9 / 10 / 11 | event_path_reserved_cap=1 是 MiroFish 不夺主的根本约束 — 任何绕过即 MiroFish 实质核心化(违用户澄清)|
| 12 | fast/slow cron 时点对应 A 股交易时段(09:30-11:30 / 13:00-15:00)— 改 cron 影响调度负载与延迟 |
| 13 / 14 | InstructionSide 与 ALLOWED_INSTRUMENTS 是 P0-3 / P0-7 永禁衍生品的实施层 |
| 15 | T+1 卖空与 ETF 卖空在 A 股法规与 MockBroker 设计层面均不允许 |
| 16 | ETF 套利留 P1 预留接口设计 — 启用是大改而非小改 |
| 17 | frozen Pydantic v2 immutability 原则;继承 P0-3 / P0-4 / P0-5 / P0-6 / P0-7 / P0-8 |

## 后续动作 / Checklist

### 决策落定 — 当前 commit 范围

- [x] 写 `docs/decisions/P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md`(本文件)
- [ ] 同步更新 `CLAUDE.md` §1.3 进度行 + §2.1 表格 + §3.1 加第 19 块"watchlist 与频率红线"(17 条)
- [ ] 同步更新 `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-9 表格状态
- [ ] 同步更新 `MEMORY.md` 加 P0-9 索引项
- [ ] 创建 `~/.claude/projects/-home-ps-papers-QuantMind/memory/project_run_mode_p0_9.md` 自记忆文件
- [ ] 等用户授权后 commit;不自动 push
- [ ] commit 后写 P0-10 handoff prompt(P0 最后一站 — LLM 角色边界)

### 实施期(决策对齐期完成后启动)

实施期不在 P0-9 commit 范围,但本节列出 checklist 作为 implementation 阶段输入:

#### 配置(P0)

- [ ] 重写 `config/watchlist_policy.yaml` 为 §5 完整目标结构
- [ ] 用户填充 `default_codes` 13 个 codes(根据 §7 选股建议)
- [ ] `config/broker.yaml` 加 `etf_arbitrage_enabled: false`
- [ ] 实施期产出 `docs/decisions/P0-7-amendment-2026-05-09-watchlist-exclusion-rules.md`

#### 数据模型与服务层(P0)

- [ ] 新建 `backend/data/watchlist_policy.py`(frozen Pydantic v2 + constraints 校验)
- [ ] 重写 `backend/data/watchlist.py`(`validate_composition` + deprecated 旧方法)
- [ ] 扩 `backend/data/stock_metadata.py`(`ipo_date` + `avg_amount_20d` + 三个 is_* 函数)
- [ ] 改 `backend/services/instruction_plan_builder.py`(第五道早返 + path 字段)
- [ ] 新建 `backend/services/cap_allocator.py`(5 单 cap 分配状态机 + 滑动释放)
- [ ] 新建 `backend/services/event_router.py`(MiroFish severity ≥ HIGH 路由)
- [ ] 改 `backend/broker/mock_broker.py`(ALLOWED_INSTRUMENTS frozenset)
- [ ] 新建 `backend/broker/etf_arbitrage_stub.py`(空 stub)

#### API 层(P0)

- [ ] 改 `backend/api/watchlist*.py`(仅 GET;旧 POST/DELETE 标 410 Gone)

#### 调度层(P0)

- [ ] 改 `backend/data/scheduler.py`(读 watchlist_policy.yaml + 注册 fast/slow)
- [ ] 改/新建 `backend/agents/pipelines/fast_pipeline.py` / `slow_pipeline.py`
- [ ] 新建 `backend/scheduler/cap_reset.py`(00:00 重置 + 15:30 ledger)

#### 前端(P1)

- [ ] 改 `frontend/src/views/WatchlistView.vue`(只读 13 codes + 排除规则展示)
- [ ] 改 `frontend/src/views/InstructionCenterView.vue`(当日 cap 消费实时)
- [ ] 新建 `frontend/src/views/MiroFishEventView.vue`(事件时间线)

#### 测试(P0)

- [ ] `tests/data/test_watchlist_policy.py`
- [ ] `tests/services/test_instruction_plan_builder_watchlist_exclusion.py`
- [ ] `tests/services/test_cap_allocator.py`
- [ ] `tests/services/test_event_router.py`
- [ ] `tests/broker/test_mock_broker_allowed_instruments.py`
- [ ] `tests/integration/test_watchlist_full_flow.py`

#### 红线静态检查(实施期 lint rule)

```bash
# P0-9 红线 1:watchlist 总数恒 13
grep -rn "total_codes" config/watchlist_policy.yaml | grep -v "13"

# P0-9 红线 4:watchlist 永禁科创板/北交所/可转债
grep -rn "688\|92.....\|11....\|12....\|^8.....$" config/watchlist_policy.yaml

# P0-9 红线 5:watchlist runtime 不可改
grep -rn "@router.post\|@router.put\|@router.delete" backend/api/watchlist*.py

# P0-9 红线 7:排除规则不在 RiskEngine
grep -rn "ipo_date\|avg_amount_20d\|max_unit_price" backend/risk/

# P0-9 红线 9 / 10:event_path 硬 cap=1
grep -rn "event_path_reserved_cap" backend/services/cap_allocator.py | grep -v "= 1"

# P0-9 红线 13:InstructionSide 永锁
grep -rn "SHORT\|COVER\|MARGIN_BUY\|REVERSE_REPO\|ETF_SUBSCRIBE\|ETF_REDEEM" backend/data/instruction_plan.py

# P0-9 红线 14:ALLOWED_INSTRUMENTS frozenset
grep -rn "ALLOWED_INSTRUMENTS" backend/broker/mock_broker.py | grep "frozenset"

# P0-9 红线 16:ETF 套利永锁 disabled
grep -rn "etf_arbitrage_enabled" config/broker.yaml | grep "true"
```

---

**P0-9 决策锁定时间**:2026-05-09
**P0-9 决策实施期**:全部 P0 决策(P0-1 ~ P0-10)锁定后统一启动
**下一站**:P0-10 LLM 角色边界(P0 决策清单最后一站)
