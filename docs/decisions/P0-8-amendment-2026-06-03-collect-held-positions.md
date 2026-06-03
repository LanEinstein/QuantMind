# P0-8 修订 — 2026-06-03 30s 行情采集集 = union(配置 watchlist, broker 在持仓)

> **修订基准**: [P0-8 数据情报](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
> **关联**: [P0-8-amendment-2026-06-03-watchlist-tushare-sina-primary](./P0-8-amendment-2026-06-03-watchlist-tushare-sina-primary.md)（同 #63 ops session 修复实时源 eastmoney 不可达；本 amendment 修第二层「采集集不覆盖在持」）/ [P1-2.B](./P1-2-account-ledger-mtm-equity-curve.md)（intraday MTM / 权益曲线消费 `market_realtime`）
> **修订日期**: 2026-06-03（#63 ops session;真启暴露后续遗留 gap）
> **决策人**: owner（#63 三选一倾向方案 (a) 采集集=union(watchlist, broker 在持)）+ 数据诚实实测（BUY 成交未把代码加入被采集 watchlist → 在持码无 `market_realtime` 行）
> **性质**: 决策边界 + bug 修复（amendment-first，代码随后 TDD + codex-review）。**不消耗任何额外数据成本**（在持码本就需要采价；只是把它们并入既有 30s 批量快照，无新增数据源/无新增调用通道）。

## 0. 触发与意图

2026-06-03 MVP 真启暴露的两层「监控/MTM 失效」中的**第二层**（第一层=实时源 eastmoney 不可达，已由 `P0-8-amendment-2026-06-03-watchlist-tushare-sina-primary` 修复）:

- 30s 行情采集 `DataScheduler._collect_watchlist_snapshot` → `_active_watchlist_codes` **只取 `WatchlistService.list_stocks()` 的【配置】watchlist**（实测 8 码:510300/159949/000001/300750/601318/510500/600519/000858）。
- **BUY 成交【不】把代码加入该 watchlist** → 在持 5 码（605111/300433/600909/600011/605020）从不进入采集集 → `market_realtime` 无在持码行 → `MongoBackedMarketMetaProvider.get_current_price` 报『no fresh quote within 300s』（cost_price fallback 红线禁）→ `intraday_mtm_build_failed` → 权益点 `market_value=0 / positions=0`，`/api/trading/positions` 的 `unrealized_pnl` 恒 0（显示成本价）。
- **对比**:Line-2 止损监控**不受影响**（`line2_*_runner` 经 context provider **直取在持码**调 `get_watchlist_snapshot`，数据源修复后 held=5 active=5）。所以「监控失效」是两层；本 amendment 修第二层。

意图:让在持仓被纳入 30s 采集集，使 intraday MTM / 权益曲线能给在持仓标价；**保持数据层不 import broker**（注入式晚绑定回调）。

## 1. 决策

### 1.1 采集集 = union(配置 watchlist, broker 在持仓)
- `DataScheduler.__init__` 新增可选参 `held_codes_provider: Callable[[], Awaitable[list[str]]] | None = None`（晚绑定 async 回调；`None` 时行为同旧 = 仅配置 watchlist，dev/测试环境无 broker 仍可跑）。
- `_active_watchlist_codes` 在算出配置 watchlist 码后,并入 `held_codes_provider()` 返回的在持码,**去重保序**（watchlist 优先,held 追加;`watchlist=[A,B] + held=[B,C] → [A,B,C]`）。
- 在持码同样进 `market_realtime` + Redis `quote:{code}` + `watchlist_market_snapshots`,血缘/PIT 与配置码一致(同一 `snapshot_at`、同一 `get_watchlist_snapshot` 路径)。

### 1.2 数据层不 import broker（注入式）
- `backend/data/scheduler.py` **不** `import backend.broker`；只接收一个 `Callable` 回调。
- main.py `_init_data_layer` 构造一个**晚绑定闭包** `_held_position_codes`:调用时读 `application.state.broker_registry`（broker 在 `_init_trading_layer` 才挂 state,晚于 data 层构造,故 `getattr` + `None` 守卫),`reg.get_broker("default").get_positions()` 取在持,返回 `volume>0` 的码。

### 1.3 held_codes_provider fail-open（infra 读,不 fail-closed 崩采集）
- broker 读失败（registry 未挂 / get_broker KeyError / get_positions 异常)→ **记 warning → 退回 watchlist-only**，**不**让 30s 采集整轮崩。
- 双层防御:① main.py 闭包内捕获 broker 读异常 → `log.warning("held_position_codes_read_failed")` + 返 `[]`（registry 未挂时静默返 `[]`,非错误);② scheduler `_active_watchlist_codes` 仍包一层 try/except，provider 抛异常 → `log.warning("held_codes_provider_failed")` + 退回 watchlist-only（defense-in-depth,TDD 钉死）。
- 依据 CLAUDE.md §3「fail-closed for data corruption / fail-open for infra glitches」——broker 在内存的读是 infra glitch,非数据腐坏。

### 1.4 修复采集→MTM 消费断链（codex cycle-1 P1，闭环必需）
**codex 抓出**:仅 union 采集集**不足以**让 intraday MTM 给在持仓标价 —— 采集产物根本不被 MTM provider 读取(绿测试只断言 union,没断言消费侧,正是 [[feedback_codex_findings_real]] / [[feedback-codex-review-manual-invocation]]「绿测试 ≠ 闭环可用」)。实测断链:
- `MongoBackedMarketMetaProvider.get_current_price` 两级读价:**tier-1 Redis** `quote:{code}`(`_parse_redis_quote` 要求 `price` + `timestamp` 两字段)→ **tier-2 Mongo** `market_realtime`(按 `code`+`timestamp` 取最新)。
- 但 `_cache_quotes_to_redis` 写的 blob 是 `WatchlistMarketSnapshot.model_dump()`,时间字段叫 **`snapshot_at`** 不是 `timestamp` → `_parse_redis_quote` `KeyError` → tier-1 恒 miss;且 30s 采集经 `save_watchlist_snapshot` 落 **`watchlist_market_snapshots`** 集合,**不**落 `market_realtime`(后者仅 `_collect_index_snapshot` 写指数 000300)→ tier-2 对个股恒 miss。
- ⇒ 在此 amendment 前,**任何个股(含在持)经 provider 都取不到价**(`no fresh quote within 300s`);union 把在持码收进采集,但产物仍读不到 = MTM 失败照旧。

**修复(本 amendment 一并落,**主路径 tier-1**)**:`_cache_quotes_to_redis` 在缓存 blob 上**非破坏性**镜像 `timestamp = snapshot_at`(保留原 `snapshot_at`,任何 OHLC 读者不破)→ provider 文档化契约(其 docstring + `_parse_redis_quote` docstring 均写「DataScheduler writes `{"price":..,"timestamp":..}`」)恢复 → tier-1 对每个被采集码(含在持)出新鲜价。Redis 上线 + 30s 刷新(≤60s tier-1 窗)= 正常运行下在持仓 MTM 即恢复。**新增 producer→consumer 往返测试**(`_cache_quotes_to_redis` 产物喂进真 `_parse_redis_quote` 断言出价)钉死消费侧契约,补上 codex 指出的测试盲区。

**tier-2(Mongo `market_realtime` 个股行)= 显式 follow-up,不在本 amendment**:provider 的 Mongo 兜底层对个股始终空(这是**先于本任务**的既有缺口,非本改动引入;provider 从来没从 `market_realtime` 拿到过个股价)。正常运行下 tier-1 Redis(30s 刷新)已足;Redis 冷/宕时按既有三级回退(§2.7 Redis≤60s→Mongo≤300s→degraded)降级,与全体个股同口径。**收敛此 gap(让 `market_realtime` 含个股行 或 provider Mongo 腿改读 `watchlist_market_snapshots`)列入「安全硬化窗口」follow-up**,与 `get_index_realtime` empty→fallback 不对称同窗,避免在本任务内扩到 provider 读契约/双写带来回归面。

## 2. 红线（保留 / 变更)

**保留不变**:
- 数据源仍 Tushare-Sina 主 + adata 备（`watchlist-tushare-sina-primary` 不动);主备阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s 沿用、不放宽。
- **PIT 数据可复现**:在持码快照走同一 `get_watchlist_snapshot` → 同存 `market_realtime` 原始行 + checksum + 同一 `snapshot_at`，血缘一致（R0 §3 PIT 红线不破)。
- **数据成本不设 ceiling**（在持码本就要采价;无新增数据源/调用通道,sina 免费腿不变）。
- **LLM 严禁进数据路径 / 数据质量判定**（本改动零 LLM)。
- **`backend.data` 仍不 import `backend.broker`**（只收注入的 `Callable` 回调;import 隔离不破)。`marketdata_snapshot/`（K-006 隔离)不涉及、零 backend.* import 不破。
- `instruction_plan_builder` 单一构造点、RiskEngine 纯函数、127.0.0.1、永禁真实下单、飞书人工 gate 全不涉及、不变。

**变更**:
- `DataScheduler` 采集集从「仅配置 watchlist」→「union(配置 watchlist, broker 在持仓)」（经注入 `held_codes_provider`)。
- `_cache_quotes_to_redis` 缓存 blob 非破坏性新增 `timestamp`（镜像 `snapshot_at`)→ 恢复 provider 文档化的 `quote:{code}` = `{"price","timestamp"}` 契约（§1.4;唯一消费者 = `MongoBackedMarketMetaProvider`,无其他读者）。

## 3. 范围限定（不在本 amendment)
- **不**改 BUY 成交把代码加入 watchlist（方案 b;写路径更重 + SELL 清仓后要移除,生命周期复杂——本 amendment 不选)。
- **不**让 MTM provider 绕过采集直 fetch（方案 c;违背「采集是单一行情入口」+ 重复 fetch——本 amendment 不选)。
- **不**改 `get_watchlist_snapshot` 主备腿（数据源仍 sina 主 + adata 备)。
- **不**改 watchlist 配置内容（8 配置码不动;只是采集时额外并入在持)。

## 4. 验证
- TDD（`tests/test_scheduler.py` + `tests/test_held_position_codes.py`）:
  - `_active_watchlist_codes` 返回 union 且去重保序（watchlist=[A,B] + held=[B,C] → [A,B,C]）/ provider 抛异常 → 退回 watchlist-only + 记 warning（不崩）/ provider=None → 行为同旧（仅 watchlist）/ provider 返回非 str 码跳过 / watchlist 未挂但 provider 有 → 仍采在持。
  - `_read_held_position_codes`:出 `volume>0` 在持码 / volume=0 排除（已平仓不采）/ registry 未挂 → `[]` / get_broker / get_positions 异常 → fail-open `[]`。
  - **§1.4 消费侧契约(codex P1)**:`_cache_quotes_to_redis` 产物 blob 含 `timestamp`(==`snapshot_at`)+ `price`,且喂进真 `_parse_redis_quote` 往返出价(producer→consumer round-trip)。
- 全量 pytest（`FEISHU_INTERACTIVE_ENABLED=false`)+ ruff + 官方 `scripts/redline-check.sh` 全绿;codex-review 修完 P0/P1/P2 再 commit。
- **真启验证（交易时段 09:30–15:00,owner 重启后)** —— ⚠️ **修正原 task 验证口径**(task 把采集集误当写 `market_realtime`,实写 `watchlist_market_snapshots` + Redis):
  - **Redis tier-1(主路径,本 amendment 修复点)**:`redis-cli GET quote:605111` → JSON 含 `"price"` + `"timestamp"`(可被 provider 解析);age<60s。
  - **采集落库**:`db.watchlist_market_snapshots.find_one({"code":"605111"}, sort=[("snapshot_at",-1)])` 有 5 在持码新鲜行(**不是** `market_realtime` —— 后者仅指数 000300)。
  - **MTM 闭环(权威成功判据)**:`logs/quantmind.jsonl` 不再 `intraday_mtm_build_failed`;`curl /api/portfolio/equity-points/latest` → `market_value>0` 且 `positions=5`;`curl /api/trading/positions` → `unrealized_pnl` 带入实时价(不再恒 0)。
  - Line-2 仍 `held=5 active=5`(不回退)。
  - 盘前(<09:30)Tushare-sina PRICE=0 属正常;采集只在 `is_trading_hours` 跑,故验证须在交易时段。
</content>
</invoke>
