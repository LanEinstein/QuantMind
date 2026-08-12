# P0-8 修订 — 2026-06-03 watchlist 30s 快照实时源 = Tushare `realtime_quote(src=sina)` 主 + adata 备

> **修订基准**: [P0-8 数据情报](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
> **关联**: [P0-8-amendment-2026-05-28-tushare-sina-realtime-backup](./P0-8-amendment-2026-05-28-tushare-sina-realtime-backup.md)（单标的 dual 备腿换 sina，**§2.3 显式预留** watchlist 多腿同隐患「可后续 follow-up amendment 收敛」——本 amendment 即该收敛）/ P0-8-amendment-2026-05-24-tushare-data-source（全市场扫描层 = Tushare Pro SDK）
> **修订日期**: 2026-06-03（深夜 #63 ops session;真启暴露后续）
> **决策人**: owner（AskUserQuestion 2026-06-03:实时主备答「Tushare-Sina 主 + adata 备」）+ 数据诚实实测（sina 可达/eastmoney 不可达/adata 早盘返空）
> **性质**: 决策边界 + bug 修复（amendment-first，代码随后 TDD + codex-review）。**不消耗 Tushare Pro 积分**（`ts.realtime_quote(src='sina')` 是 SDK 内置 sina 公开接口包装，非 `pro_api` 积分接口;数据成本不设 ceiling 红线沿用）。

## 0. 触发与意图

2026-06-02 MVP 真启暴露:`get_watchlist_snapshot`（30s 全 watchlist 个股快照,喂 Redis `quote:{code}` + Mongo `market_realtime` → MTM + Line-2 监控)**自上线起 `watchlist_snapshot_complete` = 0**（06-01 412 次 + 06-02 472 次 `watchlist_snapshot_both_failed`)。诊断:
- **主腿 adata `list_market_current`**:真启交易时段窗口**返回空帧**(primary_error="empty";盘后手动调 15/15 健康,但服务采集窗口内空)。
- **备腿 akshare `stock_zh_a_spot_em()`**:走 eastmoney 重量级 clist 批量端点,本机**彻底不可达**(直连 RST + 走代理 ProxyError;同 05-28 单标的诊断的同一 eastmoney 批量限流/封锁)。

后果:MTM `intraday_mtm_build_failed`(no fresh quote within 300s,cost_price fallback 红线禁)+ Line-2 监控全天 `degraded`(held=5 active=0)。实测 `ts.realtime_quote(src='sina')` 对在持 5 码 + 16 码批量**全成功**、返完整 OHLC+PRE_CLOSE+VOLUME+AMOUNT、不走 eastmoney、免费。05-28 amendment §2.3 已预留此 follow-up。

## 1. 决策

### 1.1 `get_watchlist_snapshot` 主备 = Tushare-Sina 主 + adata 备(akshare 移除)
- **主腿**：新增 `_fetch_stock_list_tushare_sina(codes)` —— 每码经 `_to_tushare_ts_code`(fail-closed,复用 05-28)转 ts_code,comma-join **分块(≤50/批)** 调 `ts.realtime_quote(ts_code=..., src='sina')`,拼接返回。行映射复用既有 `_tushare_sina_row_to_quote`（→ `StockQuote`,code=TS_CODE 末 6 位,`_positive_or_none` 校验 PRICE）。source tag = `tushare_sina`。
- **备腿**：adata `_fetch_stock_list_adata`（不变)；source tag = `adata`。
- **akshare `_fetch_stock_list_akshare`（eastmoney 批量)从本路径移除**（不可达;05-28 §2 红线 1「备腿严禁回退 akshare 全市场批量端点」精神延伸到 watchlist)。函数保留供其他单源/测试,但 `get_watchlist_snapshot` 不再调。
- 主腿异常**或**空帧 → fall through adata;两腿皆空 → `[]`;两腿皆 raise → `DataFetchError("Both tushare-sina and adata failed for watchlist snapshot")`。

### 1.2 per-row fail-closed（不 starve 批）
逐行映射;某行 `_tushare_sina_row_to_quote` raise（停牌/非正 PRICE → `ValueError`）→ **跳过该码**(该码本 tick 无新鲜价 → 其 MTM 诚实降级),**不**让一只停牌股拖垮整批。adata 腿同样逐行(其 mapper 不 raise)。

### 1.3 QuoteSource 扩 `tushare_sina`
`QuoteSource = Literal["adata","akshare","unknown"]` → `Literal["adata","akshare","tushare_sina","unknown"]`(加性;已验证下游无 watchlist-source 值分支:`staleness.quote_source` 仅 provenance 透传 / MTM 只读 price+timestamp / Line-2·suspension·intraday 不按 source 值分支 / `data_quality_probes:102 source=="adata"` 是**单股 dual-leg 选择器**,与 watchlist source tag 解耦)。

## 2. 红线（保留 / 变更)

**保留不变**:
- 调用仍 **Tushare 官方 SDK only**（`ts.realtime_quote(..., src='sina')`;严禁 MCP/skill 进运行时数据路径;严禁 `pro_api` 实时接口 —— 05-24/05-28 红线)。lazy import tushare 不变。
- 主备阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s 沿用、不放宽。
- `_to_tushare_ts_code` fail-closed（STAR/北交/可转债/B 股 ForbiddenCodeError;未知 UnknownCodeError;**永不**猜 .SH/.SZ）。
- **单标的 dual-source 路径不变**（`get_stock_realtime_dual` = adata 主 + tushare-sina 备,05-28 锁定;本 amendment 只动 `get_watchlist_snapshot` 多码批量快照)。
- LLM 严禁进数据路径 / 数据质量判定;数据成本不设 ceiling（sina 免费)。
- `marketdata_snapshot/`（K-006 隔离）零 backend.* import 不破;`backend.data` 仍只此一层 import tushare(且函数内 lazy)。

**变更**:
- `get_watchlist_snapshot` 主腿 adata→tushare-sina、备腿 akshare→adata。
- `QuoteSource` 加 `tushare_sina`。

## 3. 范围限定（不在本 amendment)
- `get_index_realtime` 的 empty→fallback 不对称（adata 空帧不 fall through;benchmark 指数）仍列**安全硬化窗口** follow-up,不在本 amendment（与 watchlist MTM 阻塞解耦)。
- orderbook 主备（adata `get_market_five` 主 + akshare `stock_bid_ask_em` 备)不变。
- 单源 `get_stock_realtime` / `_fetch_stock_akshare` 留原位。

## 4. 验证
- TDD:`_fetch_stock_list_tushare_sina` 批量(mock `tushare.realtime_quote`)+ 分块 + forbidden/unknown 码跳过不触 SDK;`get_watchlist_snapshot` tushare-sina 主成功 → source=tushare_sina / 主空 → adata 备 source=adata / 两腿空 → [] / 两腿 raise → DataFetchError / 停牌行 skip 不 starve。
- 全量 pytest + ruff + 官方 redline-check 全绿;codex-review 修完 P0/P1/P2 再 commit。
