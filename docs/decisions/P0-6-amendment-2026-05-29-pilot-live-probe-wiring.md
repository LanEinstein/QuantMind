# P0-6 修订 — 2026-05-29 PILOT gate 三条 live-probe 真实接通(cond9 / cond10a / cond10b)

> **修订基准**: [P0-6 验收 — simulation_auto 45 交易日滚动 + 5 稳定性 + 3 策略硬门槛](./P0-6-acceptance-rolling-window-stability-strategy-gates.md)
> **关联**: P0-6-amendment-2026-05-25(PILOT 11 条最小集 + `can_switch_to_feishu_on("pilot")`)/ P0-8(DataQualityState 主备阈值)/ P1-7-amendment-2026-05-26(日 ¥100 hard 真·预留)/ P0-10(LLM 单调用 30s + 0 重试)
> **修订日期**: 2026-05-29(U-D6 — 真 BUY e2e 首次真启 backend 暴露 gate 不可过)
> **决策人**: owner(2026-05-29 本 session 两问:① 方向=今天实现 3 探针目标周一真 BUY;② cond9 语义=infra 可达口径)

## 0. 触发(SSoT 与代码的实质性背离)

2026-05-29 周五,owner 已在 `~/.bashrc` 配齐 4 个 go-live env,目标当日真 BUY e2e。首次以 `FEISHU_INTERACTIVE_ENABLED=true` 真启 backend 时,PILOT acceptance gate 的 **async live-probe 复检阶段 fail-closed `SystemExit`**,3 条未达成:`cond9:data_quality_blocking_breach` + `cond10a:llm_timeout_rate_above_ceiling` + `cond10b:cost_guard_hard_reserve_inactive`。

逐条读源码定位,**全部不是行情/临时性失败,而是探针从未真实接通**:

| 条件 | 修订前代码事实 | 性质 |
|---|---|---|
| cond9 data_quality | `main.py::_data_quality_clear()` → 写死 `return False`(注释 "Fail-closed until U-D3") | 占位桩,从未接通 |
| cond10a llm_timeout | `main.py::_llm_timeout_ok()` → 写死 `return False`(注释 "Fail-closed until U-D4") | 占位桩,从未接通;全项目无 live timeout 遥测(连 `main.py` acceptance 装配 `llm_timeout_calls=0` 也是写死) |
| cond10b cost_guard | `main.py::_cost_guard_hard_reserve_active()` 读 `state.daily.status`,但 `get_daily_budget_state()` 返回的 `DailyBudgetState` 直接有 `.status`、无 `.daily` → `AttributeError` → `_safe_await` fail-closed | 一行 bug |

**SSoT 背离**:`docs/plan.html` 里 **U-D3 / U-D4 标 `status:"done"`**,但这两个任务实际交付的是 dry-run harness(U-D3 `c00c6a0`)+ 真冒烟脚本/applier 幂等(U-D4 `0b6c2bc`),**压根没接通这两个 live-probe**;探针注释里的 "until U-D3/U-D4" 是挂错引用的 carry-forward。之前 cond3/cond4 的 dry-run / 真发 smoke 都走独立脚本,从未真正引导过完整 interactive backend,所以这三条 live-probe 直到本日首次真启才暴露不可过。本 amendment 即 U-D6,把它们真实接通;U-D3/U-D4 的 SSoT note 同步加更正说明(它们的脚本交付本身有效,只是 live-probe 接通不在其 scope)。

**红线不破**:gate 永不可绕过(`FEISHU_INTERACTIVE_ENABLED` 只选 tier,不改 verdict)。本 amendment 是**把 fail-closed 桩替换为真实判定**,不是放宽门槛。

## 1. cond9 — data-quality 启动期就绪 = infra 可达口径(owner 2026-05-29 拍板)

### 1.1 语义
gate 在 backend 启动时跑一次。**per-code 交易时新鲜度由 builder 早返 + `DataQualityState` 把关(见 §4 follow-up 缺口)**,gate 不重复门控新鲜度。cond9 只证明**数据层可达**:
- 探 3 只**强制 ETF**(`510300` / `510500` / `159949`,P0-9 §2 红线锁定必备宽基 ETF,恒在 universe)。
- 对每只调 `MarketDataService.get_stock_realtime_dual(code) → (primary, fallback)`。某只**两腿都 None** = `quote_unavailable`(供应商全宕)→ cond9 **未达成**。
- staleness / divergence / freshness / 停牌 等**时间点伪阶**(盘前实时报价必然 stale)**不阻断启动** —— 这正是 owner 选 infra 口径的原因:可盘前启动,不被 09:30 前没有 fresh 报价卡死。
- `application.state.market_data` 为 None(数据层未构造)→ fail-closed `return False`。
- 探针本身抛异常 → `_safe_await` 兜底 fail-closed(契约不变)。

### 1.2 为何用精简可达探针而非完整 `DataQualityProvider`
全项目**没有任何地方构造过真 `DataQualityProvider`**:其 4 个信号探针(`PrimaryBackupQuoteProbe` / `WatchlistSnapshotAgeProbe` / `NewsAvailabilityProbe` / `MiroFishHealthProbe`)只有 Protocol 定义、无具体实现;dry-run 的 `build_data_layer` 也恒返 `dq_provider=None` 走 clean fallback。建完整 7 信号 provider 栈是多 session 工程。owner 选定的 "infra 可达口径" 在语义上**只需** `quote_unavailable` 判定,故实现一个聚焦的 `canary_quotes_reachable(market_data, codes)` 纯异步 helper(复用既有 `get_stock_realtime_dual`),与所选语义精确对齐,不建全栈。

### 1.3 落地
- 新模块 `backend/services/pilot_data_probe.py`:`async def canary_quotes_reachable(market_data, codes) -> bool`(任一 code 两腿全 None → False;market_data None → False;每只码探测异常视为该码不可达 fail-closed)。`MANDATORY_ETF_CANARIES = ("510300","510500","159949")` 模块常量,注释引 P0-9 §2。
- `main.py::_data_quality_clear()` 改为:`market_data = getattr(application.state,"market_data",None)`;`return await canary_quotes_reachable(market_data, MANDATORY_ETF_CANARIES)`。

## 2. cond10a — live LLM timeout-rate 计数器(非 45 日 acceptance 口径)

### 2.1 语义
gate MET = 当日 LLM 超时率 ≤ 5%(`llm_timeout_rate ≤ 0.05`,沿用 P0-6 §2 稳定性门槛数值)。**必须用 live 当日计数器**,严禁从 45 日 acceptance 报告派生(窗口未满时报告是 INSUFFICIENT_DATA 无指标会误阻 PILOT;零分母报告又可能按 FULL 遥测误放行 —— Codex U-D2 P2 已警示)。
- 分子 `llm:timeouts:{utc_date}` / 分母 `llm:calls:{utc_date}`(Redis,UTC date 基,与既有 `llm:usage:{date}` 同 `_utc_date_str()` 单一真相源)。
- `rate = timeouts / max(calls, 1)`;**冷启动 0 调用 → 0/1 = 0 ≤ 0.05 → MET**(无超时证据即放行,符合启动期就绪语义)。
- Redis 为 None / 读失败 → `_safe_await` fail-closed。

### 2.2 落地(不改 30s / 0 重试契约)
- `backend/llm/fallback.py` 新增:`track_llm_call(redis, *, date=None)`(incr `llm:calls:{date}` + 1 天 TTL 兜底)、`track_llm_timeout(redis)`(incr `llm:timeouts:{date}`)、`read_llm_timeout_rate(redis) → tuple[int,int]`(返 `(timeouts, calls)`,缺键 0)。键 TTL 取 `llm:usage` 同款(防 Redis 无界增长)。
- `backend/llm/router.py::_call_provider`:**每次 provider 调用** `track_llm_call`(primary + fallback 各计一次,口径=provider 调用尝试数);捕 `openai.APITimeoutError` 时 `track_llm_timeout` 后**原样 re-raise**(不吞、不改 fallback 流转、不改 timeout/retry)。计数 best-effort:计数器写失败只 warning 不阻 LLM 主路径(fail-open infra glitch;与预算守门的 fail-closed 区分 —— 计数器不是安全门,gate 读不到只会 fail-closed 更保守)。
- `main.py::_llm_timeout_ok()`:读 `application.state.redis`;`t,c = await read_llm_timeout_rate(redis)`;`return (t / max(c,1)) <= 0.05`。

## 3. cond10b — 修 `DailyBudgetState.status` 访问 bug

`main.py::_cost_guard_hard_reserve_active()`:`state.daily.status` → `state.status`。`get_daily_budget_state(redis)` 直接返回 daily 维度的 `DailyBudgetState`(字段 `status ∈ {"ok","soft_breach","hard_breach"}`),无 `.daily` 嵌套。语义不变:hard reserve "active"(健康)= `state.status != "hard_breach"`。

## 4. 已知 follow-up 缺口(本 amendment 文档化,不静默)

**生产 builder 端 per-code data-quality 门实际空转**:`InstructionPlanContext.data_quality: DataQualityState` 必填,由 `build_line1/line2_code_contexts(data_quality_provider=...)` 产出;但 `application.state.data_quality_provider` **从未赋值**(`main.py` 只 getattr 读、默认 None)→ context builder 走 clean fallback → builder 的 `check_data_quality` 早返**永不触发**。即:交易时刻 §1.1 所说"per-code 新鲜度由 builder 把关"在生产**尚未真正生效**。

- 这是**既有缺口**,与本日 gate-completion 独立,修它 = 建 §1.2 的完整 `DataQualityProvider` + 4 具体探针(多 session 工程)。
- 本 amendment **不**承诺修它;cond9 的 infra 可达口径**不依赖**它(只验供应商可达)。
- 建议新开任务(候选 U-D7 / Phase 后续):实现 4 具体探针 + 构造 provider 挂 `app.state` + builder 真实门控。**在它落地前,真 BUY 的数据质量防御依赖**:RiskEngine 14-check(独立并行,纯函数)+ MockBroker at-fill 涨跌停 recheck + 实时 cage 限价(U-E2)+ 飞书人工 owner 终审。owner 知悉此分层后决定是否在真 BUY 前优先补 provider。

## 5. 不变量(本 amendment 不触碰)

- gate 永不可绕过;`can_switch_to_feishu_on` / `PilotReadinessProbe.evaluate` 11 条结构、`_safe_await` fail-closed 契约、6 manifest ledger、其余 live-probe(cond1/2/8)全不变。
- LLM 单调用 30s + 0 重试(P0-10)、日 ¥100 hard 真·预留(P1-7-amendment)、cost_guard / SoftDegradeManager 隔离(不 import backend.{llm,agents,mirofish,data})全不变 —— 本 amendment 只在 router 加 best-effort 计数 + 读 cost_guard 既有 `DailyBudgetState`,不新增预算逻辑。
- 主备阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s 不放宽(cond9 不门控它们,但 builder/RiskEngine 路径仍按原阈值)。
- LLM 严禁参与验收 / 数据质量判定路径:本 amendment 的 cond9/cond10a 探针是**确定性纯逻辑**(报价腿 None 计数 / Redis 整数计数),零 LLM 参与。
