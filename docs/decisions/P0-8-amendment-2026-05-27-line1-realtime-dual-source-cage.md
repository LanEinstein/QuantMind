# P0-8 修订 — 2026-05-27 Line-1 接实时双源主备 + 五档盘口(缺口 4 取数层)

> **修订基准**: [P0-8 数据情报 — 行情主备 / 多域资讯 / MiroFish / DataQualityState](./P0-8-data-intelligence-quote-news-mirofish-dataquality.md)
> **关联**: 同日 P0-3-amendment(实时 cage 限价)/ P0-7-amendment(check#02 cage 子校验)/ R0 §3 红线 A(PIT 可复现)
> **修订日期**: 2026-05-27(U-E2 / 缺口 4 落地)
> **触发**: 缺口 4 的价格笼子限价需要**实时盘口**;P0-8 已锁的"主备 staleness ≤5s / divergence ≤0.3%"双源行情
> 此前仅用于 watchlist 30s 快照(Line-2),Line-1 BUY 选股路径**未接入**实时层(用 T-1 EOD)。
> 本 amendment 把同款双源契约接进 Line-1,并新增五档盘口(卖一)取数。经 plan mode + 3 轮 codex + owner 拍板。

## 1. 修订前(P0-8 原锁定)

- 主备行情:adata primary / akshare fallback;staleness ≤5s / divergence ≤0.3% / freshness ≥60s。
- 全 watchlist 30s 个股快照(`get_watchlist_snapshot`),供 DataQualityProvider 算 staleness/divergence/missing。
- `StockQuote` 仅 last/OHLC,**无五档盘口字段**;无单只 dual-source last 取数方法。
- Line-1 provider 用 **T-1 EOD 帧** last 作限价基(未接实时层)。

## 2. 修订后(本 amendment 锁定)

### 2.1 新增五档盘口取数(缺口 4 卖一来源)
- `backend/models/market.py` 新增 frozen `StockOrderbook{code, last, best_ask, best_bid, source, ts}`(strict + extra=forbid)。
- `MarketDataService.get_stock_orderbook(code)`:**adata `get_market_five` primary**(`s1`=卖一 / `b1`=买一;无 last)
  / **akshare `stock_bid_ask_em` fallback**(`sell_1`=卖一 / `buy_1`=买一 / `最新`=last)。
  primary 抛错 / 空帧 / **无正卖一**(薄盘/停牌)→ 视为 primary 失败,落 akshare;两腿皆失 → `DataFetchError`(调用方降级)。

### 2.2 Line-1 接**双源实时 last**(同 P0-8 主备契约,不新增阈值)
- `MarketDataService.get_stock_realtime_dual(code)` → `(adata_quote, akshare_quote)`(每腿失败置 None)。
- Line-1 provider 对 lead 取双源 last,跑 `evaluate_divergence`(阈值 **0.3% = P0-8 既有常量,不新增**)+ staleness(≤5s);
  单源 / divergence 超限 / stale → **DEGRADED 非可执行**(见 P0-3-amendment §2.3),**绝不**用 T-1 收盘价兜底发真 BUY。
- best_ask 取**主源**盘口(`get_stock_orderbook` 的 source);limit 经 `cage_bounded_buy_limit` 派生。

> 诚实边界:adata/akshare spot 解析模型 `timestamp` = 抓取时刻(非交易所逐笔时戳),故 staleness ≤5s 实际校验"抓取新鲜"而非逐笔时延 —— 给定供应商不暴露可靠逐笔时戳时的诚实限制;**divergence(双源 last 一致)是真正的双源守门**。

### 2.3 PIT 可复现(R0 §3 红线 A):实时盘口存**原始字节 + checksum + 血缘**
provider 把"双源 spot + 五档盘口"的 canonical-JSON 字节(供应商响应是进程内 DataFrame,无稳定 wire 字节 →
parsed-quote canonical JSON 即可复现记录)经 `MarketDataSnapshot.create` 存入 `SnapshotStore`(content-addressed +
sha256 自校验,append-only),`params`/`metadata` 带 `signal_id` 血缘,供 `replay <signal_id>` 重建笼子输入。
存原始字节(非 hash-only,R0 §3 红线 A 不破)。best-effort:store 故障仅 warning 不致命(PIT 是审计/复现关注点,
非可交易性门 —— 订单已按同一内存 quote 定价);无 store 注入则跳过。

## 3. 不变量(本 amendment 不触碰)

- 主备行情 adata/akshare + 阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s(沿用,不新增/不放宽)。
- 多域 5 源资讯 / MiroFish 双路径 / evidence_id 5 前缀 / DataQualityState 第四种冻结。
- 纯量化模块隔离(`screening`/`budget_policy`/`candidate_selector`/`marketdata_snapshot` 禁 import llm/agents/mirofish);
  `marketdata_snapshot` 纯模块零 backend.* 子包 import(provider 取数后传 payload 进 store,不破 K-006 隔离)。
- Tushare 全市场扫描层 SDK only;凭证池 EXPECTED_POOL_SIZE=8 不变。

## 4. 落地

- 代码:`backend/models/market.py` / `backend/data/market_data.py` / `backend/services/line1_context_provider.py`(`_derive_cage_quote` + `_persist_pit`)/ `scripts/dry_run_realdata.py`(修 `build_data_layer` 的 `load_data_sources_config()` 缺 yaml_path bug → market_data 恒 None)/ `scripts/dry_run_double_line.py`(Line-1 provider 接 market_data + snapshot_store)/ `backend/main.py`(同)。
- 测试:`tests/test_market_data.py`(盘口主备 + 缺卖一落 fallback + dual-source 双腿)/ `tests/services/test_line1_context_provider.py`(cage 派生 + 5 降级路径 + stale + PIT 持久化)。
- 任务:plan.html U-E2。
