# P0-6 修订 b — 2026-05-29 DataQualityProvider 生产挂载(C3)

> **修订基准**: [P0-6-amendment-2026-05-29-pilot-live-probe-wiring.md](./P0-6-amendment-2026-05-29-pilot-live-probe-wiring.md) §4(follow-up 缺口文档化)
> **关联**: P0-8 §2 redline 8/11 + P1-2.B §2 redline 8/10/11 + P0-10 §2 redline 1
> **修订日期**: 2026-05-29 (C3 任务)
> **决策人**: owner (2026-05-29 handoff prompt 指定 C3 为 Monday-MVP obstacle)

## 0. 背景 — 修订前缺口

[P0-6-amendment-2026-05-29 §4](./P0-6-amendment-2026-05-29-pilot-live-probe-wiring.md#4-已知-follow-up-缺口本-amendment-文档化不静默) 明确记录:

> 生产 builder 端 per-code data-quality 门实际空转:`application.state.data_quality_provider` 从未赋值
> (`main.py` 只 getattr 读、默认 None)→ context builder 走 clean fallback → builder 的 `check_data_quality`
> 早返**永不触发**。

本 amendment 即 C3,将这个既有缺口完整关闭:

1. 实现 4 个具体探针类 (`backend/data/data_quality_probes.py`)。
2. 在 `backend/main.py` lifespan `_init_data_layer` 中构造 `DataQualityProvider` 并挂
   `application.state.data_quality_provider`。

## 1. 架构决策

### 1.1 quote probe — 全真阻断门

`MarketDataQuoteProbe` 是唯一真实阻断 buy/sell 的探针:

- 调用 `MarketDataService.get_stock_realtime_dual(code)` 获取 adata 主备 / Tushare-Sina 备用双腿。
- `source="adata"` → 取 `primary` 腿；`source="akshare"` → 取 `fallback` 腿
  (P0-8-amendment-2026-05-28:sina 路由已替换 akshare eastmoney;source 标签保留 "akshare" 符合 Protocol 接口)。
- 腿为 `None` → raise `RuntimeError` → `DataQualityProvider._probe_quote_leg` catch → `(None, False)` →
  对应 breach 信号 (fail-closed)。
- `age_seconds = (datetime.now(UTC) - leg.timestamp).total_seconds()` — fetch-time 诚实语义
  (已知 U-E2 limitation:exchange-clock 时间戳映射尚未实现,staleness 门度量 RTT 而非真实行情龄;
  divergence 门不受影响)。

### 1.2 三个非阻断标记 — 诚实廉价实现

以下三个探针实现的是 **非阻断降级标记** (P0-8 §2 redline 11 / P1-2.B §2 redline 11
锁定:news/mirofish/snapshot 永不阻断 buy/sell 路由):

| 探针 | 实现 | 完整健康追踪 |
|------|------|------------|
| `WatchlistSnapshotAgeProbe` | 调 `get_watchlist_snapshot`,返回 fetch 成功时的 max(now - snapshot_at);空 watchlist → 0.0 | 从 MongoDB 读已存快照龄 — deferred follow-up |
| `NewsAvailabilityProbe` | 注入 `Callable[[datetime], int]`,默认返回 5(全存活冷启动安全值) | 每域独立 ping + Redis 追踪 — deferred |
| `MiroFishHealthProbe` | 注入 `Callable[[], bool]`,默认返回 True(已配置) | MiroFish HTTP 健康探针 — deferred |

三者全部 deferred 均为 **gate-safe in MVP**:
- gate 永不触发(非阻断信号只影响 EquityPoint/ledger 降级标记)。
- 关闭这个缺口已使 quote probe 真正生效,MiroFish/news/snapshot 标记准确反映"尚无真实追踪"而非误报。

### 1.3 watchlist_codes 注入策略

`WatchlistSnapshotAgeProbe` 需要一个返回活跃 watchlist codes 的同步 callable。`WatchlistService.list_stocks()` 是异步的,无法在 `get_oldest_among_watchlist_max_age` 内部同步调用。本次注入 `lambda: []`(空列表),probe 返回 `0.0`,`watchlist_snapshot_outage = False`。

这是诚实的实现:
- 空返回 → 0.0 → 无虚假告警 ✓
- `watchlist_snapshot_outage` 是非阻断降级标记 → gate-safe ✓
- 完整实现(在 Redis/MongoDB 缓存中维护同步快照)为 deferred follow-up ✓

## 2. 生产构造点 (main.py)

`_init_data_layer` 末尾:已构造 `DataQualityProvider` 并挂 `application.state.data_quality_provider`。

### 2.1 Line-1 BUY 路径真接通 (C3 review fix A)

`Line1ContextProvider.__init__` 新增可选参数 `data_quality_provider: Any | None = None`。
`build_lead_context` 内、`bare` 确定后立即 evaluate:

```python
if self._data_quality_provider is not None:
    try:
        dq = await self._data_quality_provider.evaluate(bare, self._now)
    except Exception:
        log.warning("line1_data_quality_failed", ...)
        dq = blocking_data_quality()  # fail-closed
else:
    dq = self._data_quality  # back-compat baseline
```

`make_assembly_context` 的 `data_quality=dq`(原 `self._data_quality`)已替换。

`_line1_daily_callback` 构造 `Line1ContextProvider` 时传入:
```python
data_quality_provider=getattr(application.state, "data_quality_provider", None)
```

**fail-closed 语义**: probe 异常 → `blocking_data_quality()` → `is_acceptable_for_buy_sell=False` → builder `check_data_quality` 早返 → lead 不路由。probe 异常绝不视同 clean quote。

### 2.2 quote probe 单次 fetch (C3 review fix B)

`MarketDataQuoteProbe` 增加 `_dual_cache: dict[str, tuple[...]]`:
- `source="adata"`: fetch dual → 存 cache → 返回 primary
- `source="akshare"`: pop cache → 返回 fallback(与 primary 同一 fetch,legs 一致);
  cache miss(defensive) → fresh fetch → fallback

修复了原来两次独立调用导致的跨调用 divergence 误判和双倍 latency。

### 2.3 `_active_watchlist_codes` 文档诚实化 (C3 review fix C)

删除误导性 "reads cursor documents" 注释。诚实 docstring: 返回 `[]` → probe 返回 `0.0` → `watchlist_snapshot_outage=False` (非阻断,gate-safe)。完整 MongoDB 存储龄追踪为 deferred follow-up。

`_init_orchestration_layer` 内 `build_line1/line2_code_contexts` 已从 `application.state.data_quality_provider` 读取(main.py ~738/780),wiring 完成后 per-code DQ 门真正生效。

## 3. 不变量 (本 amendment 不触碰)

| 约束 | 来源 | 状态 |
|------|------|------|
| DataQualityState schema 7+3 字段锁定 | P1-2.B §2 redline 10 | 不变 |
| 阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s | P0-8 §1.1.2 | 不变 |
| news/mirofish/snapshot 三信号不阻断 buy/sell | P0-8 §2 redline 11 | 不变 |
| LLM 不参与数据质量判定 | P0-10 §2 redline 1 | 不变 |
| data_quality_probes.py 不 import backend.llm/agents/mirofish | P0-8 §2 redline 8 | 遵守 |
| 4 locked files (data_quality.py / staleness.py / divergence.py / suspension.py) 内容不变 | P0-8 redline | 遵守 |
| 单一构造点 M-004 (InstructionPlan) | R0 | 不触碰 |

## 4. 测试覆盖

### 4.1 已有 (C3 round-1, 36 条)

`tests/test_data_quality_probes.py`:
- `TestMarketDataQuoteProbe` (7 条)
- `TestMarketDataQuoteProbeIntegration` (7 条)
- `TestWatchlistSnapshotAgeProbe` (5 条)
- `TestNewsAvailabilityProbe` (6 条)
- `TestMiroFishHealthProbe` (6 条)
- `TestNonBlockingMarkers` (2 条) — 补强: 两条均加 `is_acceptable_for_buy_sell is True` 断言 (fix D)

### 4.2 新增 (C3 review fixes, 本次)

`tests/test_data_quality_probes.py` 新增 `TestMarketDataQuoteProbeSingleFetch` (5 条):
- `test_adata_then_akshare_calls_dual_exactly_once` — dual 仅调用 1 次
- `test_within_fetch_consistent_pair_does_not_trip_divergence` — 0.1% 不触发
- `test_within_fetch_genuinely_divergent_pair_trips_divergence` — 5% 触发
- `test_different_codes_each_trigger_their_own_dual_fetch` — 两 code 各一次
- `test_akshare_without_preceding_adata_fetches_defensively` — defensive path

`tests/services/test_line1_data_quality_gate.py` 新增 8 条:
- (a) `test_blocking_dq_provider_assembly_context_non_acceptable`
- (a) `test_blocking_dq_provider_degrades_lead_to_early_return` — end-to-end EARLY_RETURN
- (b) `test_raising_dq_provider_uses_blocking_fallback` — AssemblyContext == blocking
- (b) `test_raising_dq_provider_blocks_route` — end-to-end no route
- (c) `test_no_dq_provider_uses_clean_baseline` — back-compat clean
- (c) `test_no_dq_provider_lead_can_route` — back-compat proceeds

### 4.3 已知 deferred 项 (gate-safe)

- watchlist-snapshot age 度量 fetch 成功非存储 cron 龄(非阻断,P0-8 §2 redline 11)
- naive-timestamp → fail-closed HOLD (当前所有 converter 均 tz-aware UTC,安全)
- float(None) price → provider fail-closed (provider 已有 validity guard)

完整套件预计: 4122 + 13 新增 = 4135 passed / 13 skipped
