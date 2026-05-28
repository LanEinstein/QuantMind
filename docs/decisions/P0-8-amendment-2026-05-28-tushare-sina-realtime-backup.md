# P0-8 修订 — 2026-05-28 实时 dual-source 备腿由 akshare(全市场批量) 换 Tushare `realtime_quote(src=sina)`

> **修订基准**: [P0-8 数据情报 — 行情主备 / 多域资讯 / MiroFish / DataQualityState](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
> **关联**: P0-8-amendment-2026-05-24-tushare-data-source(全市场扫描层 = Tushare Pro SDK)/ P0-8-amendment-2026-05-27-line1-realtime-dual-source-cage(Line-1 接双源实时 + 五档盘口)
> **修订日期**: 2026-05-28(U-E5 cond3 dry-run unblocker)
> **触发**: 5-28 开盘后 cond3 dry-run 全 5 候选 `line1_quote_degraded` → 0 BUY。诊断:akshare 备腿 `_fetch_stock_akshare(code)` 调 `stock_zh_a_spot_em()` 拉**全 A 股现货表 ~5500 行 / 58 页**再过滤单只 → eastmoney 反爬/限流 `RemoteDisconnected('Remote end closed connection without response')` **持续重置**(本日 3/3 直测 + 5 候选 5/5;非瞬时,无论是否走代理皆失败)。同 host/proto 单标的 curl + adata 主腿 + akshare 单标的 `stock_bid_ask_em` 均 200/OK,**证 eastmoney 仅对重量级 clist 批量端点限流**。同时实测 Tushare 包自带 `ts.realtime_quote(ts_code=..., src='sina')`(sina 源、非 `pro_api` 积分接口)单标的、含 L1 + 五档(`A1_P..A5_P` / `B1_P..B5_P` / `ASK` / `BID`)、avg 0.5-1s、不走 eastmoney、token 已在凭证池 §2.5。owner 5-28 决策:实时备腿改用 sina。

## 1. 修订前(P0-8 原锁定 + 5-27 amendment)

- 实时**主备**:adata `list_market_current(code_list=[code])` primary / **akshare `stock_zh_a_spot_em()` filter** fallback。
- `get_stock_realtime_dual(code) → (adata_quote, akshare_quote)`,Line-1 provider 跑 divergence ≤0.3% + staleness ≤5s。
- `_fetch_stock_akshare(code)` = 拉**全 A 股现货表**再 `df[df["代码"]==code]`(58 页 ~5500 行,只为一只)。

## 2. 修订后(本 amendment 锁定)

### 2.1 实时 dual-source 备腿(单标的)
- `get_stock_realtime_dual(code) → (adata_quote, tushare_sina_quote)`(primary 仍 adata 不变)。两腿现**并发** `asyncio.gather(..., return_exceptions=True)`(旧串行原因 = 怕 hammer eastmoney 批量,现 sina 单标的不存在;halves 5s staleness 预算的延迟占用)。
- 备腿改 `_fetch_stock_tushare_sina(code)` —— 调 `ts.realtime_quote(ts_code=<6位+.SH/.SZ>, src='sina')` 返单行(`PRICE` 末值 / `PRE_CLOSE` / `OPEN/HIGH/LOW` / `VOLUME/AMOUNT` / `ASK/BID` / `A1_P..A5_P` / `B1_P..B5_P` / `DATE+TIME` Asia/Shanghai)。tushare **lazy import**(akshare/adata 同款 lazy 风格;省 pytest collection + uvicorn cold start 时的 Tushare 全栈拉入)。
- 列映射 → `StockQuote`:`code`=`TS_CODE` 末 6 位、`name`=`NAME`、`price`=`PRICE` **经 `_positive_or_none` 校验**(NaN/inf/≤0 → `ValueError` 让 dual handler fail-closed 写 `dual_tushare_sina_failed` + fallback=None;停牌/盘前的诚实降级,不构造伪 NaN quote;复用已有 U-E2 codex P1 修过的同款 helper)、`open/high/low/prev_close` 同款 `_positive_or_none`(非主指标,缺即 0.0)、`change_pct`=`(PRICE-PRE_CLOSE)/PRE_CLOSE*100` 当 prev_close 正有限时;否则 0.0、`volume`/`amount` 同名、`turnover_rate`=`0.0`(sina 不带,显示性,**不入** divergence / staleness 判定)、`timestamp`=`datetime.now(tz=UTC)` **抓取时刻**(对齐 adata 主腿语义;sina DATE+TIME 是交易所时钟,引入会与主腿处于不同时间锚,跨腿 staleness 比较会误判 — 故捕获 DATE+TIME 是未来 schema 新字段事,不在本 swap 范围)。
- 日志键:
  - `dual_akshare_failed` → `dual_tushare_sina_failed`(vendor 网络/SDK 外断,下游 audit/grep 一致迁移)。
  - **新增 `dual_fallback_input_error`**(`ValueError` from `_to_tushare_ts_code` —— malformed/universe-blocked code 等程序/数据层 bug;**与 vendor 外断分清** ops 排查方向)。
- code → ts_code 映射(纯函数,fail-closed,`_to_tushare_ts_code`):**deferring to `classify_board`** 单一真相源(`backend/data/stock_metadata.py`),映射规则:`Board.SH_MAIN` → `.SH`;`Board.SZ_MAIN`/`Board.CHUANGYE` → `.SZ`;`Board.ETF` → `.SH`(`51x`/`588`) 或 `.SZ`(`159`)。`classify_board` 自动 fail-closed:`ForbiddenCodeError`(STAR 688/689、北交 4xx/8xx/92x、可转债 110/113/118/123/127/128/132、B-share 200/900)+ `UnknownCodeError`(其他/malformed),都是 `ValueError` 子类,统一被 `get_stock_realtime_dual` 的 `dual_fallback_input_error` 分支捕。原 amendment 草案的"`6` → `.SH`(沪市主板+科创)/`1` → `.SZ`(深市 ETF/可转债)"在 code-review 中被指出错误共用 prefix(科创不该放行;110xx SH 可转债被错路到 SZ),改 defer 即一并解决。

### 2.2 凭证池 / 隔离不变
- `TUSHARE_TOKEN` 已在 §2.5 凭证池(EXPECTED_POOL_SIZE=8 不变;5-24 amendment 锁定异质凭证不入 LLM 3+飞书 5 池)。**无新增凭证**。
- 调用方式仍 **Tushare 官方 Python SDK only**(`import tushare as ts` / `ts.realtime_quote(...)`;严禁 MCP/skill 进运行时数据路径,5-24 amendment 红线不变)。`ts.realtime_quote` 是 tushare 包内置的 sina 公开接口包装,不消耗 `pro_api` 积分(`pro_api.realtime_quote` 在本 token 档位返"请指定正确的接口名",实测 fail)。
- **诚实边界**:`ts.realtime_quote` 在 tushare SDK 内被 `@require_permission` 装饰,每次调用先 HTTPS POST `api.tushare.pro/dataapi/sdk-event` 验 token 再访问 sina。**新外部依赖**(akshare 备腿没这跳)+ 每次 ~100-300ms 永久开销吃 5s staleness 预算;不可绕过(SDK 协议)。code-review #9 提示;若 verify endpoint 偶发外断 → tushare 抛 PermissionError → fallback=None,全候选 quote_degraded。缓解:`TUSHARE_TOKEN` 已 fail-fast 启动期校验;实时可观测层有 `dual_tushare_sina_failed` audit + alert。
- `backend.data.market_data` 仍只在 backend.data 一层 import `tushare`(且 **lazy 在函数内**,见 §2.1 落地);`marketdata_snapshot/`(K-006 隔离)零 backend.* import 不破。

### 2.3 范围限定(只换 dual 备腿,不换其他 akshare 路径)
- `_fetch_stock_akshare(code)` 函数**保留**;`get_stock_realtime`(单源非 dual)/`_fetch_stock_list_akshare`(多码,`get_watchlist_snapshot` 多腿)继续用 akshare(不在 cond3 阻塞路径,如同样隐患可后续 follow-up amendment 收敛)。orderbook `_fetch_orderbook_akshare`(单标的 `stock_bid_ask_em`,实测 OK 0.4s)**不变**——仍作 orderbook 备腿。

## 3. 不变量(本 amendment 不触碰)

- 主备行情阈值 staleness ≤5s / divergence ≤0.3% / freshness ≥60s **沿用,不放宽/不新增**。
- adata `list_market_current` primary 不变。
- `StockQuote`/`StockOrderbook` schema 不变(仅新增 vendor row→quote 转换器;不扩字段)。
- `QuoteSource = Literal["adata","akshare","unknown"]` **不扩**(本 amendment 只动 `StockQuote` 路径,无 source 字段;orderbook `QuoteSource` 仍 adata/akshare)。
- 多域 5 源资讯 / MiroFish 双路径 / evidence_id 5 前缀 / DataQualityState 第四种冻结 不变。
- 纯量化隔离(`screening`/`budget_policy`/`candidate_selector`/`marketdata_snapshot` 禁 import llm/agents/mirofish)不变。
- LLM 严禁参与数据质量判定 / 行情主备路径 不变。
- Tushare 全市场扫描层 SDK only + 凭证池 8 不变。

## 4. 红线(违反即停)

1. 备腿严禁回退 akshare 全市场批量端点(`stock_zh_a_spot_em` 在 dual 路径上)—— `_fetch_stock_akshare` 不再被 `get_stock_realtime_dual` 调用,任何 PR/CR 重新接回 = 违规。
2. 严禁使用 `pro_api.realtime_quote` 或其他 `pro_api` 实时接口(token 档位无权限 + 5-24 amendment 实时层不归 Tushare Pro 扫描层管;本 amendment 锁定**只用** `ts.realtime_quote(..., src='sina')` 这一条 sina 公开接口包装)。
3. 严禁基于 sina 的 `ASK`/`A1_P` 在 dual 路径上覆盖 orderbook 的 `best_ask`(orderbook 主源仍是 adata `get_market_five`,akshare `stock_bid_ask_em` 单标的备;sina spot 行里有 `A1_P` 是事实但**不在本 amendment 启用** —— 改 orderbook 主备需另写 amendment)。
4. `_to_tushare_ts_code` 严禁 fallback / 猜:未知前缀 = `ValueError`,**永不**默认 `.SH`/`.SZ`。
5. divergence ≤0.3% / staleness ≤5s 阈值常量沿用 `data_sources.yaml`,本 amendment 不改 yaml(仍由 5-27 amendment 锁定)。

## 5. 落地

- 代码:
  - `backend/data/market_data.py` — 新增 `_to_tushare_ts_code`(defers to `classify_board`)/ `_fetch_stock_tushare_sina`(lazy import tushare)/ `_tushare_sina_row_to_quote`(`_positive_or_none` for PRICE / 复用 reuse helper);改 `get_stock_realtime_dual` 备腿调用 + `asyncio.gather` 并发两腿 + 日志键 `dual_tushare_sina_failed` (vendor 外断) + `dual_fallback_input_error` (program/data 层 ValueError 分清)。`_fetch_stock_akshare` / `_akshare_stock_row_to_quote` 留原位(被其他单源路径使用)。
  - `backend/services/line1_context_provider.py` — `_DIVERGENCE_THRESHOLD_PCT` 注释 + 3 处人类可见 degrade-reason 字符串 "akshare" → "tushare-sina"(audit/operator 视角与新 vendor 一致;旧消息说 "backup leg (akshare) unavailable" 会让 ops 排查错方向)。
- 测试:
  - `tests/test_market_data.py` 3 个 `TestGetStockRealtimeDual` mock 由 `_fetch_stock_akshare` 换 `_fetch_stock_tushare_sina`,fixture `_tushare_sina_df` 提供 sina 行 shape。
  - `tests/test_market_data_tushare_sina.py`(新)— `_to_tushare_ts_code` 允许集 + ForbiddenCodeError(STAR/BJ/CB/B-share)+ UnknownCodeError(malformed/unknown);`_tushare_sina_row_to_quote` 列映射 + change_pct 派生 + NaN/inf/zero PRICE 三种 fail-closed 路径 + fetch-time UTC timestamp + missing PRE_CLOSE → change_pct=0.0;`_fetch_stock_tushare_sina` lazy import via `patch("tushare.realtime_quote")` + forbidden/unknown 不触 SDK。
- 任务:plan.html U-E5(B) 前置(cond3 dry-run unblocker;纳入 U-E5 doing,不另开任务 id)。
- 不在范围(follow-up amendment 收敛):
  - `get_stock_realtime` 单源路径 + `_fetch_stock_list_akshare`(`get_watchlist_snapshot` 多码备腿) — 同样的 `stock_zh_a_spot_em` 全市场批量隐患存在,但不在 cond3 阻塞路径上;后续 amendment 把单源/多码路径都收敛到单标的 tushare-sina(可能整体下线 akshare 批量入口,history_data.py:90 同款隐患)。
  - `_akshare_stock_row_to_quote` 也不动(用于上面两路单源路径)。
  - 复用 `backend/utils/trading_hours.SHANGHAI` ZoneInfo 与 stock_metadata `_validate_code_shape` 是顺带福利,本 amendment 不动 callsite。

## 6. Code-review 修复记录(post-amendment R1)

claude `/code-review high` 跑出 10 finding(codex 撞额度回退;docs/reviews/U-E5-codex-review-summary.md 续写)。修复内容(P0/P1 全收 + 部分 cleanup):

1. NaN PRICE/OHLC 透 `float(row.get(X,0) or 0)` 进 `StockQuote` — fix: 复用 `_positive_or_none`,PRICE 缺/NaN/non-positive 抛 `ValueError`,dual handler fail-closed missing-data 而非伪 NaN 报价。
2. `_to_tushare_ts_code` prefix 自表绕过 universe(688 STAR/110 CB 错路) — fix: defer to `classify_board` 单一真相源 + `ForbiddenCodeError`/`UnknownCodeError`。
3. `ValueError` 被 `except Exception` 吞成 `dual_tushare_sina_failed`(misleading audit) — fix: `dual_fallback_input_error` 分键。
4. 模块顶 `import tushare as ts` 破坏 lazy 模式 — fix: 函数内 lazy import,测试 patch `tushare.realtime_quote`。
5. `line1_context_provider` 人类可见消息仍说 "akshare unavailable"(operator 误导) — fix: 3 处迁 "tushare-sina"。
6. `_ASIA_SHANGHAI` 重复发明 — fix: 删,timestamp 改用 `datetime.now(tz=UTC)` 同主腿语义。
7. 串行两腿 — fix: `asyncio.gather` 并发。
8. `_BARE_CODE_RE` 重复 — fix: 删(`classify_board` 内 `_validate_code_shape` 接管)。

deferred(非阻塞):#9 `@require_permission` 网络验证开销(SDK 协议,文档记;§2.2)、#10 `_positive_or_none` 复用与多 vendor adapter DRY 化(独立 cleanup PR)。
